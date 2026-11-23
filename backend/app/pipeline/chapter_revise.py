from __future__ import annotations

import difflib
import re
from pathlib import Path

from app import llm_client
from app.enums import POV
from app.pipeline import ai_checker
from app.pipeline._common import (
    WarnCB,
    earlier_chapter_texts,
    is_american_english,
    strip_llm_chatter,
    voice_or_default,
    warn_if_cut,
)
from app.pipeline.chapter_draft import _ensure_fresh_opener, last_sentences_brief, strip_scene_boundary_replay
from app.pipeline.characters_gen import scrub_banned_names
from app.pipeline.context_builder import build_chapter_context, build_static_system, extract_opening_word
from app.pipeline.directives import build_directives, clamp_max_tokens
from app.pipeline.slop_fix import dedupe_adjacent_sentences, normalize_prose_typography
from app.storage import read_text
from app.store import Book

_SCENE_SEP_RE = re.compile(r"\n\s*\*\s*\*\s*\*\s*\n")
_REPEAT_HEADING_RE = re.compile(
    r"^(?:#{1,4}\s*|\*\*)(?:Ch(?:apter)?|Spread|Part)\s+\d+[^\n]*(?:\*\*)?\n+",
    re.IGNORECASE,
)


async def _revise_scene_by_scene(
    book: Book,
    bdir: Path,
    n: int,
    *,
    system: str,
    user_ctx: str,
    unit_label: str,
    scenes_raw: list[str],
    notes: str,
    brief: str,
    allowed_src: str,
    stream_cb: llm_client.StreamCB | None,
    on_warning: WarnCB,
) -> str:
    revised_parts: list[str] = []
    total = len(scenes_raw)
    us_english = is_american_english(book, bdir)
    for idx, scene_txt in enumerate(scenes_raw, 1):
        sc_clean = scene_txt.strip()
        sc_words = len(sc_clean.split())
        sc_max_tok = max(1800, min(6500, int(sc_words * 2.5) + 400))

        sections: list[str] = [user_ctx]
        if brief.strip():
            sections.append(f"## EDITORIAL REVISION BRIEF (APPLY RELEVANT POINTS FOR SCENE {idx})\n{brief.strip()}")
        if notes.strip():
            sections.append(f"## AUTHOR NOTES (MUST APPLY)\n{notes.strip()}")
        if revised_parts:
            brief_tail = last_sentences_brief(revised_parts[-1], max_chars=220)
            sections.append(f"## EARLIER REVISED SCENE END (ALREADY ON PAGE — DO NOT REPLAY OR PARAPHRASE)\n...{brief_tail}")
            if stream_cb:
                await stream_cb("\n\n* * *\n\n")
        sections.append(
            f"## REVISION CONSTRAINTS (OUTPUT STORY PROSE ONLY)\n"
            f"- Revise ONLY Scene {idx} prose, keeping final word count within 92%–115% of {sc_words:,} words.\n"
            "- Preserve all dialogue exchanges, physical subjects (boat vs narrator), exact remaining day-counts & quantities from Canon, and exact character genders/names/family ties.\n"
            "- Keep every spoken dialogue line wrapped in double quotation marks and keep narrative voice warm, active, and natural (NEVER inject clinical passive-voice bureaucratese).\n"
            "- Cut AI prose tics actively: cap em-dashes at <= 1 per 150 words; replace 'felt'/'seemed'/'palpable'/'silence stretched'/'rain intensified', 'It wasn't X; it was Y', and '-ly' dialogue adverbs (e.g. 'said softly') with concrete action and clean periods.\n"
            "- Never add end-notes, '## Chapter End Note', meta-explanations ('Instead of describing...'), word counts, or constraint text."
        )
        sections.append(f"## SCENE {idx} OF {total} TO REVISE ({sc_words:,} words)\n{sc_clean}")
        sc_prompt = "\n\n".join(sections)

        res = await llm_client.complete(
            system=system,
            user=sc_prompt,
            task="revise",
            max_tokens=sc_max_tok,
            creativity="medium",
            cache_prefix=True,
            stream_cb=stream_cb,
        )
        warn_if_cut(res.stop_reason, on_warning, f"{unit_label} {n} scene {idx} revision")
        cand = strip_llm_chatter(res.text)
        if idx >= 2 and cand:
            cand = _REPEAT_HEADING_RE.sub("", cand).strip()
        cand = normalize_prose_typography(scrub_banned_names(cand, allowed_src), american_english=us_english)
        if revised_parts and cand:
            cand = strip_scene_boundary_replay("\n\n".join(revised_parts), cand) or cand

        cand_words = len(cand.split())
        if sc_words >= 180 and cand_words < int(sc_words * 0.90):
            res2 = await llm_client.complete(
                system=system,
                user=sc_prompt + f"\n\nIMPORTANT: Your previous revision compressed Scene {idx} to {cand_words} words. Keep EVERY beat and dialogue exchange intact at ~{sc_words:,} words.",
                task="revise",
                max_tokens=sc_max_tok,
                creativity="medium",
                cache_prefix=True,
            )
            cand2 = strip_llm_chatter(res2.text)
            if idx >= 2 and cand2:
                cand2 = _REPEAT_HEADING_RE.sub("", cand2).strip()
            cand2 = normalize_prose_typography(scrub_banned_names(cand2, allowed_src), american_english=us_english)
            if revised_parts and cand2:
                cand2 = strip_scene_boundary_replay("\n\n".join(revised_parts), cand2) or cand2
            if len(cand2.split()) > cand_words:
                cand = cand2
                cand_words = len(cand.split())

        if sc_words >= 180 and cand_words < int(sc_words * 0.86):
            if on_warning:
                on_warning(f"Scene {idx} of {unit_label} {n} shrank excessively ({cand_words}w vs {sc_words}w); keeping original scene for surgical fixes.")
            revised_parts.append(sc_clean)
        else:
            revised_parts.append(cand or sc_clean)

    out_joined = normalize_prose_typography("\n\n* * *\n\n".join(revised_parts), american_english=us_english)
    out_joined, _ = dedupe_adjacent_sentences(out_joined)
    return out_joined


async def generate(
    book: Book,
    bdir: Path,
    n: int,
    *,
    current_text: str,
    notes: str = "",
    brief: str = "",
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> tuple[str, bool]:
    """Revise chapter `n` scene-by-scene for multi-scene chapters or whole-unit otherwise, with length preservation."""
    cur_words = len(current_text.split())
    notes_low = notes or ""
    wants_expand = bool(
        re.search(
            r"\b(?:expand|below the minimum|word count[^.\n]{0,30}below|descriptive density|add scene|lengthen|increase word)\b",
            notes_low,
            re.IGNORECASE,
        )
    )
    wants_cut = (not wants_expand) and bool(
        re.search(
            r"\b(?:shorten|condense|trim\s+(?:length|word|down)|cut\s+\d+%|reduce\s+word\s+count)\b",
            notes_low,
            re.IGNORECASE,
        )
    )

    allowed_src = f"{book.title}\n{book.idea or ''}\n{read_text(bdir / 'seed.txt')}"
    us_english = is_american_english(book, bdir)
    scenes_raw = _SCENE_SEP_RE.split(current_text.strip())
    is_scene_rev = not wants_cut and cur_words >= 2300 and len(scenes_raw) >= 2
    system = build_static_system(book, bdir, mode="revise")
    user_ctx, meta = build_chapter_context(book, bdir, n, include_draft_fences=True, scene_mode=is_scene_rev)
    expand_target = max(int(cur_words * 1.35), int(meta.get("per_unit_target") or 0)) if wants_expand else meta.get("per_unit_target")
    max_tok = clamp_max_tokens(expand_target if wants_expand else meta.get("per_unit_target"))
    if is_scene_rev:
        revised = await _revise_scene_by_scene(
            book,
            bdir,
            n,
            system=system,
            user_ctx=user_ctx,
            unit_label=meta["unit_label"],
            scenes_raw=scenes_raw,
            notes=notes,
            brief=brief,
            allowed_src=allowed_src,
            stream_cb=stream_cb,
            on_warning=on_warning,
        )
        if n > 1:
            forbidden_openers = {
                w.lower()
                for _, pt in earlier_chapter_texts(bdir, n)
                if (w := extract_opening_word(pt))
            }
            if forbidden_openers:
                revised = normalize_prose_typography(
                    scrub_banned_names(
                        await _ensure_fresh_opener(
                            revised,
                            forbidden_openers,
                            directives=build_directives(book),
                            is_first_person=(book.pov == POV.first_person),
                        ),
                        allowed_src,
                    ),
                    american_english=us_english,
                )
                revised, _ = dedupe_adjacent_sentences(revised)
        sim = difflib.SequenceMatcher(None, current_text[:8000].strip(), revised[:8000]).ratio()
        no_op = sim > 0.985
        if no_op and on_warning:
            on_warning("Revision produced no measurable text change; snapshot skipped.")
        return revised, no_op

    edit_sections: list[str] = [user_ctx]
    if brief.strip():
        edit_sections.append(f"## EDITORIAL REVISION BRIEF (ADDRESS EVERY POINT)\n{brief.strip()}")
    if notes.strip():
        edit_sections.append(f"## AUTHOR NOTES (MUST APPLY)\n{notes.strip()}")
    cur_scan = ai_checker.scan(current_text, book=book, bdir=bdir, chapter_num=n)
    scan_m = cur_scan.get("metrics") or {}
    tic_items: list[str] = []
    for f_item in (cur_scan.get("findings") or [])[:12]:
        rule_n = str(f_item.get("rule") or "")
        q_txt = str(f_item.get("quote") or "").strip()[:80]
        msg_txt = str(f_item.get("message") or "").strip()[:110]
        if rule_n == "future_clue_leak" and q_txt:
            tic_items.append(f"- [REMOVE EARLY CLUE] Remove or replace '{q_txt}' ({msg_txt})")
        elif q_txt:
            tic_items.append(f"- [{rule_n} L{f_item.get('line', '?')}] '{q_txt}': {msg_txt}")
        elif msg_txt:
            tic_items.append(f"- [{rule_n}] {msg_txt}")
    if tic_items or float(scan_m.get("em_dash_per_150", 0.0)) > 1.0 or float(scan_m.get("felt_per_1000", 0.0)) > 1.5:
        metric_summary = (
            f"Current scan metrics: dash-breaks={scan_m.get('em_dash_per_150', 0)}/150w (target <= 1.0; Unicode em-dashes '—' forbidden, use '-' or crisp punctuation), "
            f"'felt'={scan_m.get('felt_per_1000', 0)}/1k (target <= 1.5), "
            f"dialogue={scan_m.get('dialogue_pct', 0)}% (target 22–35%)."
        )
        edit_sections.append(
            "## DETECTED AI-TICS & MECHANICS TO CLEAN IN THIS PASS\n"
            + metric_summary
            + "\n"
            + "\n".join(tic_items)
        )
    if wants_cut:
        length_rule = ""
    elif wants_expand:
        length_rule = (
            f"\n- CRITICAL: Expand thin scenes and dialogue exchanges so final word count reaches ~{expand_target:,} words "
            f"(between {max(cur_words, int(expand_target * 0.92)):,} and {int(expand_target * 1.15):,} words) — NEVER trim!"
        )
    else:
        length_rule = f"\n- CRITICAL: Keep final word count within 95%–115% of {cur_words:,} words — NEVER summarize, telescope, or trim dialogue/scenes down!"
    edit_sections.append(
        "## REVISION CONSTRAINTS (OUTPUT STORY PROSE ONLY)\n"
        "- Revise the prose preserving what works while cleanly executing every requested change."
        f"{length_rule}\n"
        "- Preserve every character's exact gender, name, and established family ties from Canon, exact physical subjects (boat vs narrator), and exact calendar day-counts/quantities.\n"
        "- Keep voice active, sensory, and natural with spoken contractions (NEVER inject detached passive-voice bureaucratese), and keep spoken dialogue wrapped in double quotation marks (target 22–35% dialogue).\n"
        "- Cut AI prose tics actively: NEVER use Unicode em-dashes ('—'); replace every em-dash with plain '-' (or ' - ' / crisp periods or commas), capping dash breaks at <= 1 per 150 words; replace 'felt'/'seemed'/'palpable'/'silence stretched'/'rain intensified', 'It wasn't X; it was Y', and '-ly' dialogue adverbs with concrete action and clean periods.\n"
        "- NEVER add end-notes ('## Chapter End Note'), meta-explanations ('Instead of describing...'), editorial commentary, or constraint text. Do NOT return the draft unchanged."
    )
    edit_sections.append(f"## CURRENT DRAFT TO REVISE ({cur_words:,} words)\n{current_text.strip()}")
    user_prompt = "\n\n".join(edit_sections)

    res = await llm_client.complete(
        system=system,
        user=user_prompt,
        task="revise",
        max_tokens=max_tok,
        creativity="medium",
        cache_prefix=True,
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, f"{meta['unit_label']} {n} revision")
    revised = normalize_prose_typography(
        scrub_banned_names(strip_llm_chatter(res.text), allowed_src),
        american_english=us_english,
    )
    revised, _ = dedupe_adjacent_sentences(revised)

    # Length-collapse guard: prevent polish/revise from gutting chapter scenes into outlines!
    rev_words = len(revised.split())
    if not wants_cut and cur_words >= 300 and rev_words < int(cur_words * 0.94):
        if on_warning:
            on_warning(f"Revision compressed unit {n} from {cur_words}w to {rev_words}w; retrying with strict scene-length preservation.")
        res_len = await llm_client.complete(
            system=system,
            user=user_prompt + f"\n\nIMPORTANT: Your previous attempt compressed the chapter to {rev_words} words. Keep EVERY scene and dialogue exchange intact at ~{cur_words:,} words.",
            task="revise",
            max_tokens=max_tok,
            creativity="medium",
            cache_prefix=True,
        )
        cand_len = normalize_prose_typography(
            scrub_banned_names(strip_llm_chatter(res_len.text), allowed_src),
            american_english=us_english,
        )
        if len(cand_len.split()) >= int(cur_words * 0.94):
            revised = cand_len
            rev_words = len(revised.split())
        elif len(cand_len.split()) > rev_words:
            revised = cand_len
            rev_words = len(revised.split())

    # If revision STILL shrank below 90% of the chapter without request, fall back safely to original for line-level slop_fix!
    if not wants_cut and cur_words >= 300 and rev_words < int(cur_words * 0.90):
        if on_warning:
            on_warning(f"Revision still shrank unit {n} excessively ({rev_words}w vs {cur_words}w); keeping original draft cleanly.")
        return current_text, True

    # Check for silent no-op revision when changes were asked
    had_instructions = bool(notes.strip() or brief.strip())
    sim = difflib.SequenceMatcher(None, current_text[:8000].strip(), revised[:8000]).ratio()
    if had_instructions and sim > 0.96:
        if on_warning:
            on_warning(f"Revision returned virtually unchanged text ({int(sim * 100)}% match); retrying once with explicit edit enforcement.")
        res2 = await llm_client.complete(
            system=system,
            user=user_prompt + "\n\nIMPORTANT: Your previous revision left the text identical. Rewrite the flagged/weak passages substantially.",
            task="revise",
            max_tokens=max_tok,
            creativity="medium",
            cache_prefix=True,
            stream_cb=stream_cb,
        )
        cand = normalize_prose_typography(
            scrub_banned_names(strip_llm_chatter(res2.text), allowed_src),
            american_english=us_english,
        )
        sim2 = difflib.SequenceMatcher(None, current_text[:8000].strip(), cand[:8000]).ratio()
        if sim2 < sim and len(cand) > 100 and (wants_cut or len(cand.split()) >= int(cur_words * 0.90)):
            revised = cand
            sim = sim2
    revised, _ = dedupe_adjacent_sentences(revised)
    if n > 1 and not wants_cut:
        forbidden_openers = {
            w.lower()
            for _, pt in earlier_chapter_texts(bdir, n)
            if (w := extract_opening_word(pt))
        }
        if forbidden_openers:
            revised = normalize_prose_typography(
                scrub_banned_names(
                    await _ensure_fresh_opener(
                        revised,
                        forbidden_openers,
                        directives=build_directives(book),
                        is_first_person=(book.pov == POV.first_person),
                    ),
                    allowed_src,
                ),
                american_english=us_english,
            )
            revised, _ = dedupe_adjacent_sentences(revised)
            sim = difflib.SequenceMatcher(None, current_text[:8000].strip(), revised[:8000]).ratio()
    else:
        sim = difflib.SequenceMatcher(None, current_text[:8000].strip(), revised[:8000]).ratio()
    no_op = sim > 0.985
    if no_op and on_warning:
        on_warning("Revision produced no measurable text change; snapshot skipped.")
    return revised, no_op


_ACTION_INSTRUCTIONS: dict[str, str] = {
    "rewrite": "Rewrite the selected passage for cleaner flow, sharper concrete detail, and zero AI clichés while preserving exact plot facts.",
    "show_dont_tell": "Dramatize any emotion labels, filter verbs, or explanations through immediate physical action, sensory detail, and subtext.",
    "punch_dialogue": "Sharpen the dialogue so each speaker sounds distinct, cuts exposition, and carries subtext, pauses, or interruptions.",
    "expand_sensory": "Deepen the grounding sensory texture (sight, sound, smell, weight, physical friction) without stalling momentum or adding purple prose.",
    "tighten": "Cut 20-30% of wordiness, redundancies, and throat-clearing while making every remaining verb and noun hit harder.",
    "custom": "Follow the author's custom instruction precisely.",
}


async def rewrite_selection(
    book: Book,
    bdir: Path,
    *,
    full_text: str,
    start_char: int,
    end_char: int,
    action: str = "rewrite",
    instruction: str = "",
) -> str:
    """Selection-level co-writer: rewrites ONLY the selected span with surrounding 1k context."""
    s = max(0, min(start_char, len(full_text)))
    e = max(s, min(end_char, len(full_text)))
    selected = full_text[s:e].strip()
    if not selected:
        return ""

    before = full_text[max(0, s - 900) : s]
    after = full_text[e : min(len(full_text), e + 900)]
    directives = build_directives(book)
    voice = voice_or_default(bdir)
    goal = _ACTION_INSTRUCTIONS.get(action, _ACTION_INSTRUCTIONS["rewrite"])
    if instruction.strip():
        goal = f"{goal}\nAuthor instruction: {instruction.strip()}"

    system = "\n\n".join(
        [
            directives,
            "You are a surgical line editor rewriting ONLY the highlighted passage to splice seamlessly between BEFORE and AFTER context.\n"
            "Return ONLY the replacement prose (no quotes, no explanation, no surrounding context repeated).",
            f"## VOICE GUIDE\n{voice}",
        ]
    )
    user = (
        f"TASK: {goal}\n\n"
        f"--- BEFORE CONTEXT (DO NOT REPEAT) ---\n{before}\n\n"
        f"--- SELECTED PASSAGE TO REWRITE ---\n{selected}\n\n"
        f"--- AFTER CONTEXT (DO NOT REPEAT) ---\n{after}"
    )
    res = await llm_client.complete(
        system=system,
        user=user,
        task="revise",
        max_tokens=max(400, min(4000, len(selected) // 2 + 500)),
        creativity="medium",
    )
    return normalize_prose_typography(strip_llm_chatter(res.text))
