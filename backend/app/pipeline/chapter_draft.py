from __future__ import annotations

import logging
import re
from pathlib import Path

from app import llm_client
from app.enums import POV
from app.formats import get_profile
from app.pipeline import ai_checker, scene_planner
from app.pipeline._common import (
    WarnCB,
    allowed_name_sources,
    earlier_chapter_texts,
    is_american_english,
    strip_llm_chatter,
    warn_if_cut,
)
from app.pipeline.characters_gen import scrub_banned_names
from app.pipeline.context_builder import build_chapter_context, build_static_system, extract_opening_word
from app.pipeline.directives import build_directives, clamp_max_tokens, per_unit_word_target
from app.pipeline.slop_fix import dedupe_adjacent_sentences, normalize_prose_typography
from app.settings import load_settings
from app.storage import read_text
from app.store import Book

log = logging.getLogger("novelista.draft")
_REPEAT_UNIT_HEADING_RE = re.compile(
    r"^(?:#{1,4}\s*|\*\*)(?:Ch(?:apter)?|Spread|Part)\s+\d+[^\n]*(?:\*\*)?\n+",
    re.IGNORECASE,
)


def last_sentences_brief(text: str, max_chars: int = 220) -> str:
    """Return only the final 1-2 sentences of the previous scene clipped to `max_chars`."""
    paras = [p.strip() for p in re.split(r"\n\s*\n", (text or "").strip()) if p.strip() and not p.strip().startswith("#")]
    if not paras:
        return ""
    last_para = paras[-1]
    sents = re.split(r"(?<=[.!?])\s+(?=[\"A-Z])", last_para)
    tail = " ".join(sents[-2:]).strip() if len(sents) >= 2 else last_para
    if len(tail) > max_chars:
        tail = tail[-max_chars:]
        sp = tail.find(" ")
        if 0 <= sp < len(tail) // 2:
            tail = tail[sp + 1 :].strip()
    return tail


def _para_3gram_shingles(txt: str) -> set[tuple[str, str, str]]:
    toks = [w.lower() for w in re.findall(r"[A-Za-z0-9'’-]+", txt)]
    if len(toks) < 3:
        return set()
    return {(toks[i], toks[i + 1], toks[i + 2]) for i in range(len(toks) - 2)}


def strip_scene_boundary_replay(prev_text: str, scene_chunk: str) -> str:
    """Drop leading or near-duplicate paragraphs in `scene_chunk` that replay `prev_text` across scene boundaries."""
    if not (prev_text or "").strip() or not (scene_chunk or "").strip():
        return (scene_chunk or "").strip()
    prev_paras = [
        p.strip()
        for p in re.split(r"\n\s*\n", prev_text)
        if p.strip() and not p.strip().startswith("#") and not set(p.strip()) <= {"*", "-", " "}
    ]
    prev_shingles = [sh for p in prev_paras if len(sh := _para_3gram_shingles(p)) >= 8]
    prev_10grams: set[tuple[str, ...]] = set()
    all_prev_toks = [w.lower() for w in re.findall(r"[A-Za-z0-9'’-]+", prev_text)]
    for k in range(0, max(0, len(all_prev_toks) - 9)):
        prev_10grams.add(tuple(all_prev_toks[k : k + 10]))

    kept: list[str] = []
    para_idx = 0
    for blk in re.split(r"\n\s*\n", scene_chunk.strip()):
        s = blk.strip()
        if not s:
            continue
        if s.startswith("#") or set(s) <= {"*", "-", " "}:
            kept.append(s)
            continue
        para_idx += 1
        toks = [w.lower() for w in re.findall(r"[A-Za-z0-9'’-]+", s)]
        sh = _para_3gram_shingles(s)
        overlap_10 = any(tuple(toks[k : k + 10]) in prev_10grams for k in range(0, max(0, len(toks) - 9)))
        max_jaccard = 0.0
        if len(sh) >= 6:
            for psh in prev_shingles:
                u_len = len(sh | psh)
                if u_len:
                    max_jaccard = max(max_jaccard, len(sh & psh) / u_len)
        if para_idx <= 4 and (max_jaccard >= 0.45 or overlap_10):
            continue
        if len(toks) >= 12 and max_jaccard >= 0.65:
            continue
        kept.append(s)
    return "\n\n".join(kept).strip()


def _format_single_scene_instruction(
    sc: dict,
    idx: int,
    total: int,
    unit_label: str,
    ch_title: str,
    *,
    is_first_person: bool = False,
) -> str:
    budget = int(sc.get("word_budget") or 600)
    lo_b = max(450, int(budget * 0.90))
    hi_b = max(550, int(budget * 1.12))
    lines = [
        f"## SCENE {idx} OF {total} FOR {unit_label.upper()}: {sc.get('title', f'Scene {idx}')}",
        f"- **Target Length for THIS Scene Only:** Write ~{lo_b:,}–{hi_b:,} words for Scene {idx} by unfolding 2–3 concrete beats (entry action -> spoken exchange with friction -> physical reveal/turn) rather than summarizing.",
        f"- **Location / Time:** {sc.get('location', 'As outlined')} | {sc.get('time', 'Continuous')}",
        f"- **Cast:** {', '.join(sc.get('cast') or [])}",
        f"- **Entry State:** {sc.get('start_state', '')}",
        f"- **Goal & Friction:** {sc.get('goal', '')}",
        f"- **Dramatic Turn:** {sc.get('turn', '')}",
        f"- **Outcome:** {sc.get('outcome', '')}",
        f"- **HARD STOP FOR SCENE {idx}:** {sc.get('hard_stop', 'Stop cleanly at the end of this scene.')}",
    ]
    if is_first_person:
        lines.append(
            "- **First-Person Lock:** Write narration strictly in first-person ('I') voice — NEVER copy third-person protagonist names or 'she/he' self-references from these notes into narration."
        )
    if idx == 1:
        lines.append(f"Begin directly with `## {unit_label} {sc.get('_ch_num', '')}: {ch_title}` (or open immediately inside the scene), and STOP at Scene 1's hard stop without jumping to Scene 2.")
    else:
        lines.append(f"Write ONLY Scene {idx} prose seamlessly continuing from the previous scene's end (do NOT repeat its ending, and do NOT skip ahead to later scenes).")
    return "\n".join(lines)


async def _draft_scene_by_scene(
    book: Book,
    bdir: Path,
    n: int,
    *,
    system: str,
    user_ctx: str,
    meta: dict,
    scenes: list[dict],
    overrides: dict | None,
    stream_cb: llm_client.StreamCB | None,
    on_warning: WarnCB,
) -> str:
    unit_label = meta["unit_label"]
    ch_title = meta["title"]
    written_parts: list[str] = []
    _corrupt_rules = {"token_meltdown", "paragraph_loop", "meta_leak"}

    for idx, sc in enumerate(scenes, 1):
        sc_copy = dict(sc)
        sc_copy["_ch_num"] = n
        budget = int(sc.get("word_budget") or 650)
        sc_max_tok = max(1800, min(6500, int(budget * 2.5) + 400))

        # Build per-scene focused prompt (isolated from full multi-scene cards)
        sc_instr = _format_single_scene_instruction(
            sc_copy,
            idx,
            len(scenes),
            unit_label,
            ch_title,
            is_first_person=(book.pov == POV.first_person),
        )
        later_note = ""
        if idx < len(scenes):
            later_lines = [
                f"- Scene {k}: {s.get('title', f'Scene {k}')} ({(s.get('goal') or s.get('outcome') or '')[:100]})"
                for k, s in enumerate(scenes[idx:], idx + 1)
            ]
            later_note = (
                "\n\n## LATER SCENES IN THIS CHAPTER (DO NOT WRITE YET — STOP AT HARD STOP)\n"
                + "\n".join(later_lines)
            )
        if written_parts:
            prev_all = "\n\n".join(written_parts)
            brief_tail = last_sentences_brief(prev_all, max_chars=220)
            sc_user = (
                f"{user_ctx}\n\n"
                f"## PREVIOUS SCENE HARD STOP (ALREADY ON PAGE — DO NOT REPLAY, PARAPHRASE, OR RE-WRITE)\n...{brief_tail}\n\n"
                f"{sc_instr}{later_note}"
            )
            if stream_cb:
                await stream_cb("\n\n* * *\n\n")
        else:
            sc_user = f"{user_ctx}\n\n{sc_instr}{later_note}"

        res = await llm_client.complete(
            system=system,
            user=sc_user,
            task="draft",
            max_tokens=sc_max_tok,
            creativity="medium",
            cache_prefix=True,
            overrides=overrides,
            stream_cb=stream_cb,
        )
        warn_if_cut(res.stop_reason, on_warning, f"{unit_label} {n} scene {idx}")
        chunk = strip_llm_chatter(res.text)
        if idx >= 2 and chunk:
            chunk = _REPEAT_UNIT_HEADING_RE.sub("", chunk).strip()
        if written_parts and chunk:
            prev_all = "\n\n".join(written_parts)
            raw_wc = len(chunk.split())
            cleaned = strip_scene_boundary_replay(prev_all, chunk)
            if raw_wc >= 80 and len(cleaned.split()) < int(raw_wc * 0.65) and overrides is None:
                if on_warning:
                    on_warning(f"{unit_label} {n} scene {idx} replayed previous scene boundary; regenerating fresh from scene location.")
                rep_prompt = (
                    f"{sc_user}\n\nCRITICAL: Your previous attempt replayed an earlier scene. "
                    f"Begin FRESH at {sc.get('location', 'the new scene location')} ({sc.get('time', 'now')}) "
                    f"starting from: {sc.get('start_state', '')} — zero repeated dialogue or events."
                )
                rep_res = await llm_client.complete(
                    system=system,
                    user=rep_prompt,
                    task="draft",
                    max_tokens=sc_max_tok,
                    creativity="medium",
                    cache_prefix=True,
                )
                rep_chunk = strip_llm_chatter(rep_res.text)
                if idx >= 2 and rep_chunk:
                    rep_chunk = _REPEAT_UNIT_HEADING_RE.sub("", rep_chunk).strip()
                rep_clean = strip_scene_boundary_replay(prev_all, rep_chunk)
                chunk = rep_clean if len(rep_clean.split()) >= len(cleaned.split()) else cleaned
            else:
                chunk = cleaned or chunk

        # Focused per-scene deepening pass if a single scene drafted rushed (< 78% of scene budget)
        sc_words = len(chunk.split())
        sc_target = max(450, budget)
        if sc_words >= 40 and sc_words < int(sc_target * 0.78):
            if on_warning:
                on_warning(f"{unit_label} {n} scene {idx} drafted at {sc_words}w (< 78% of {sc_target}w); deepening scene.")
            exp_user = (
                f"{sc_user}\n\n## CURRENT UNDERSIZED SCENE {idx} ({sc_words} WORDS — TARGET ~{sc_target} WORDS)\n{chunk}\n\n"
                f"Expand ONLY Scene {idx} from {sc_words}w to ~{sc_target}w by dramatizing summarized beats into spoken dialogue "
                f"exchanges, physical friction, and concrete sensory detail. Keep all plot facts, stop cleanly at Scene {idx}'s "
                "hard stop, and NEVER output word counts like '(Word count: ...)'."
            )
            exp_res = await llm_client.complete(
                system=system,
                user=exp_user,
                task="draft",
                max_tokens=sc_max_tok,
                creativity="medium",
                cache_prefix=True,
            )
            exp_clean = strip_llm_chatter(exp_res.text)
            if idx >= 2 and exp_clean:
                exp_clean = _REPEAT_UNIT_HEADING_RE.sub("", exp_clean).strip()
            if written_parts and exp_clean:
                exp_clean = strip_scene_boundary_replay("\n\n".join(written_parts), exp_clean)
            exp_rep = ai_checker.scan(exp_clean, book=book, bdir=bdir, chapter_num=n) if exp_clean else {"findings": []}
            if (
                exp_clean
                and len(exp_clean.split()) > sc_words * 1.08
                and not any(f["rule"] in _corrupt_rules for f in exp_rep["findings"])
            ):
                chunk = exp_clean

        # Per-scene corruption guard (isolate recovery to this scene alone so sibling scenes remain intact)
        if chunk and overrides is None:
            sc_rep = ai_checker.scan(chunk, book=book, bdir=bdir, chapter_num=n)
            sc_bad = {f["rule"] for f in sc_rep["findings"]} & _corrupt_rules
            if sc_bad:
                if on_warning:
                    on_warning(f"{unit_label} {n} scene {idx} triggered {', '.join(sorted(sc_bad))}; redrafting scene cleanly.")
                rec_res = await llm_client.complete(
                    system=system,
                    user=sc_user + "\n\nCRITICAL: Output pure scene prose only with zero loops, zero meta labels, and zero echoed outlines.",
                    task="draft",
                    max_tokens=sc_max_tok,
                    creativity="medium",
                    cache_prefix=True,
                    overrides={"repeat_penalty": 1.16, "temperature": 0.68},
                )
                rec_chunk = strip_llm_chatter(rec_res.text)
                if idx >= 2 and rec_chunk:
                    rec_chunk = _REPEAT_UNIT_HEADING_RE.sub("", rec_chunk).strip()
                if rec_chunk:
                    rec_rep = ai_checker.scan(rec_chunk, book=book, bdir=bdir, chapter_num=n)
                    if rec_rep["error_count"] <= sc_rep["error_count"]:
                        chunk = rec_chunk

        if chunk:
            written_parts.append(chunk)

    return "\n\n* * *\n\n".join(written_parts)


async def _ensure_fresh_opener(
    text: str,
    forbidden: set[str],
    *,
    directives: str = "",
    is_first_person: bool = False,
) -> str:
    """Rewrite only sentence 1 if chapter starts with a word already used by an earlier chapter."""
    if not text or not forbidden:
        return text
    cur_first = extract_opening_word(text).lower()
    if not cur_first or cur_first not in forbidden:
        return text

    lines = text.splitlines(keepends=True)
    target_idx = -1
    for i, ln in enumerate(lines):
        s = ln.strip()
        if not s or s.startswith("#") or re.match(r"^(?:\*\s*){3,}$|^-{3,}$", s):
            continue
        target_idx = i
        break
    if target_idx == -1:
        return text

    line_body = lines[target_idx]
    leading_ws_len = len(line_body) - len(line_body.lstrip())
    stripped_line = line_body.lstrip()
    m_sent = re.match(r'^([^.!?\n]+[.!?]["\']?)', stripped_line)
    orig_sent = m_sent.group(1) if m_sent else stripped_line.strip()
    if len(orig_sent.split()) < 3:
        return text

    pov_rule = (
        " CRITICAL: Preserve strict FIRST-PERSON POV ('I', 'my', 'me') — NEVER shift into third-person 'he/she/his/her' self-reference!"
        if is_first_person
        else ""
    )
    sys_msg = (
        (directives + "\n\n" if directives else "")
        + "Rewrite ONLY the given opening sentence so its FIRST word is a fresh concrete noun, verb, character name, or dialogue quote "
        "— NEVER starting with any of the forbidden first words. Preserve exact story facts, POV, tense, and meaning."
        + pov_rule
        + " Output ONLY the single rewritten sentence."
    )
    res = await llm_client.complete(
        system=sys_msg,
        user=f"FORBIDDEN FIRST WORDS: {', '.join(sorted(forbidden))}\n\nOPENING SENTENCE:\n{orig_sent}",
        task="revise",
        max_tokens=160,
        creativity="low",
    )
    cand = strip_llm_chatter(res.text).strip().splitlines()[0].strip() if res.text else ""
    new_first = extract_opening_word(cand).lower()
    if not cand or not new_first or new_first in forbidden or len(cand.split()) < 3:
        return text
    if cand[-1] not in ".!?\"'":
        cand += "."
    new_stripped = cand + stripped_line[len(orig_sent) :]
    lines[target_idx] = line_body[:leading_ws_len] + new_stripped
    return "".join(lines)


async def generate(
    book: Book,
    bdir: Path,
    n: int,
    *,
    brief: str = "",
    overrides: dict | None = None,
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> tuple[str, dict]:
    """Draft unit `n`, using Scene-by-Scene drafting when multi-scene cards exist, plus continuation & loop guards."""
    settings = load_settings()
    system = build_static_system(book, bdir, mode="draft")
    scenes = scene_planner.load_scenes(bdir, n).get("scenes") or []
    target_per_unit = per_unit_word_target(book)
    use_scene_by_scene = len(scenes) >= 2 and (target_per_unit or 0) >= 2500 and overrides is None
    user_ctx, meta = build_chapter_context(
        book,
        bdir,
        n,
        include_draft_fences=True,
        include_scene_cards=not use_scene_by_scene,
        scene_mode=use_scene_by_scene,
    )
    if brief.strip():
        user_ctx = f"{user_ctx}\n\n## SPECIFIC DRAFT NOTES\n{brief.strip()}"

    per_unit = meta.get("per_unit_target")
    max_tok = clamp_max_tokens(per_unit)

    if use_scene_by_scene:
        text = await _draft_scene_by_scene(
            book,
            bdir,
            n,
            system=system,
            user_ctx=user_ctx,
            meta=meta,
            scenes=scenes,
            overrides=overrides,
            stream_cb=stream_cb,
            on_warning=on_warning,
        )
    else:
        res = await llm_client.complete(
            system=system,
            user=user_ctx,
            task="draft",
            max_tokens=max_tok,
            creativity="medium",
            cache_prefix=True,
            overrides=overrides,
            stream_cb=stream_cb,
        )
        text = strip_llm_chatter(res.text)

        continuations = 0
        stop = res.stop_reason
        while stop == "length" and settings.quality.auto_continue_truncated and continuations < 2:
            continuations += 1
            tail = text[-1400:]
            cont_user = (
                f"{user_ctx}\n\n## ALREADY WRITTEN IN THIS {meta['unit_label'].upper()} (CONTINUE SEAMLESSLY FROM THE FINAL LINE WITHOUT REPEATING)\n"
                f"...{tail}\n\nFinish the remaining beats cleanly and stop at this {meta['unit_label'].lower()}'s ending beat."
            )
            cont = await llm_client.complete(
                system=system,
                user=cont_user,
                task="draft",
                max_tokens=max(2500, max_tok // 2),
                creativity="medium",
                cache_prefix=True,
                overrides=overrides,
                stream_cb=stream_cb,
            )
            addition = strip_llm_chatter(cont.text)
            if addition:
                text = f"{text}\n\n{addition}"
            stop = cont.stop_reason
        warn_if_cut(stop, on_warning, f"{meta['unit_label']} {n} draft")

    allowed_src = allowed_name_sources(book, bdir)
    us_english = is_american_english(book, bdir)
    text = scrub_banned_names(text, allowed_src)
    text = normalize_prose_typography(text, american_english=us_english)
    text, _ = dedupe_adjacent_sentences(text)

    # Under-length OR dialogue-starved expansion pass for single-pass drafts
    cur_words = len(text.split())
    draft_rep = ai_checker.scan(text, book=book, bdir=bdir, chapter_num=n) if overrides is None and not use_scene_by_scene else {"metrics": {}}
    dlg_pct = float((draft_rep.get("metrics") or {}).get("dialogue_pct", 0.0))
    is_novel_prof = get_profile(book.book_format).craft_profile == "novel"
    needs_depth = bool(per_unit and per_unit >= 900 and cur_words < int(per_unit * 0.88))
    needs_dialogue = bool(is_novel_prof and cur_words >= 700 and dlg_pct < 20.0)
    if (needs_depth or needs_dialogue) and overrides is None and not use_scene_by_scene:
        if on_warning:
            on_warning(
                f"{meta['unit_label']} {n} drafted at {cur_words}w ({dlg_pct}% dialogue vs target ~{per_unit or cur_words}w); deepening scenes & spoken exchanges."
            )
        target_goal_w = per_unit or max(cur_words, 1500)
        b1_w = max(280, int(target_goal_w * 0.28))
        b2_w = max(400, int(target_goal_w * 0.42))
        b3_w = max(280, target_goal_w - b1_w - b2_w)
        exp_user = (
            f"{user_ctx}\n\n## CURRENT DRAFT TO DEEPEN ({cur_words} WORDS, {dlg_pct}% DIALOGUE — TARGET ~{target_goal_w:,} WORDS & 22–35% DIALOGUE)\n{text}\n\n"
            f"Expand and deepen the scenes in this draft to ~{target_goal_w:,} words using 3 substantial beats "
            f"(Beat 1 opening grounding ~{b1_w}w -> Beat 2 spoken dialogue with pushback & character friction ~{b2_w}w -> Beat 3 turn & grounded closing image ~{b3_w}w). "
            "Dramatize summarized beats into live character-distinct spoken dialogue exchanges (targeting 22–35% dialogue) and concrete physical actions. "
            "Preserve all plot facts, do NOT repeat scenes, and add ZERO weather loops or abstract rumination."
        )
        exp_res = await llm_client.complete(
            system=system,
            user=exp_user,
            task="draft",
            max_tokens=max_tok,
            creativity="medium",
            cache_prefix=True,
        )
        exp_clean = normalize_prose_typography(
            scrub_banned_names(strip_llm_chatter(exp_res.text), allowed_src),
            american_english=us_english,
        )
        exp_clean, _ = dedupe_adjacent_sentences(exp_clean)
        exp_rep = ai_checker.scan(exp_clean, book=book, bdir=bdir, chapter_num=n)
        exp_words = len(exp_clean.split())
        exp_dlg_pct = float((exp_rep.get("metrics") or {}).get("dialogue_pct", 0.0))
        improved_depth = (
            exp_words > cur_words + 60
            or (needs_dialogue and exp_dlg_pct > dlg_pct + 4.0 and exp_words >= cur_words * 0.96)
        )
        if improved_depth and not any(
            f["rule"] in {"token_meltdown", "paragraph_loop", "meta_leak"} for f in exp_rep["findings"]
        ):
            text = exp_clean

    # Deterministic opening-word variety guard against prior chapters
    if n > 1 and overrides is None:
        forbidden_openers = {
            w.lower()
            for _, pt in earlier_chapter_texts(bdir, n)
            if (w := extract_opening_word(pt))
        }
        if forbidden_openers:
            text = normalize_prose_typography(
                scrub_banned_names(
                    await _ensure_fresh_opener(
                        text,
                        forbidden_openers,
                        directives=build_directives(book),
                        is_first_person=(book.pov == POV.first_person),
                    ),
                    allowed_src,
                ),
                american_english=us_english,
            )
            text, _ = dedupe_adjacent_sentences(text)

    report = ai_checker.scan(text, book=book, bdir=bdir, chapter_num=n)

    # Automatic meltdown / loop guard: whole-chapter redraft ONLY for single-pass drafts (multi-scene chapters isolate per scene)
    bad_rules = {f["rule"] for f in report["findings"]}
    need_redraft = (
        ("token_meltdown" in bad_rules)
        or ("paragraph_loop" in bad_rules)
        or ("meta_leak" in bad_rules)
    )
    if need_redraft and overrides is None and not use_scene_by_scene:
        if on_warning:
            on_warning(f"{meta['unit_label']} {n} triggered loop/corruption guard ({', '.join(sorted(bad_rules & {'token_meltdown','paragraph_loop','meta_leak'}))}); running one automatic recovery redraft.")
        adj_ov = (
            {"repeat_penalty": 1.08, "temperature": 0.65}
            if "token_meltdown" in bad_rules
            else {"repeat_penalty": 1.18, "temperature": 0.72}
        )
        res2 = await llm_client.complete(
            system=system,
            user=user_ctx + "\n\nCRITICAL: Write every paragraph fresh with zero repeated paragraphs or loops.",
            task="draft",
            max_tokens=max_tok,
            creativity="medium",
            cache_prefix=True,
            overrides=adj_ov,
            stream_cb=stream_cb,
        )
        cand = normalize_prose_typography(
            scrub_banned_names(strip_llm_chatter(res2.text), allowed_src),
            american_english=us_english,
        )
        cand_rep = ai_checker.scan(cand, book=book, bdir=bdir, chapter_num=n)
        if cand_rep["error_count"] <= report["error_count"] and len(cand) > 100:
            text = cand
            report = cand_rep
            warn_if_cut(res2.stop_reason, on_warning, f"{meta['unit_label']} {n} recovery draft")

    return text, report
