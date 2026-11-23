from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app import llm_client
from app.enums import POV
from app.formats import get_profile
from app.pipeline._common import (
    WarnCB,
    extract_plot_spine,
    foreshadowing_for_chapter,
    is_mystery_genre,
    outline_block,
    parse_llm_json,
    pov_name,
    warn_if_cut,
)
from app.pipeline.characters_gen import scrub_banned_names
from app.pipeline.context_builder import filter_sections_before
from app.pipeline.directives import build_directives, per_unit_word_target
from app.storage import atomic_write_json, read_json, read_text
from app.store import Book, scene_path


async def generate_scenes(
    book: Book,
    bdir: Path,
    n: int,
    *,
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> dict[str, Any]:
    prof = get_profile(book.book_format)
    directives = build_directives(book)
    outline_md = read_text(bdir / "outline.md")
    spine_txt = extract_plot_spine(outline_md)
    cur_outline = outline_block(outline_md, n)
    foreshadow = foreshadowing_for_chapter(outline_md, n)
    canon_prior = filter_sections_before(read_text(bdir / "canon.md"), n)
    summary_prior = filter_sections_before(read_text(bdir / "summary.md"), n)
    chars = read_text(bdir / "characters.md")
    seed_txt = read_text(bdir / "seed.txt").strip() or (book.idea or "")
    total_units = book.chapter_count or len(book.chapters) or prof.default_units
    seed_low = f"{book.idea or ''}\n{seed_txt}".lower()
    is_final_standalone = (
        total_units >= 2
        and n == total_units
        and not any(w in seed_low for w in ("cliffhanger", "sequel", "book 1", "series opener"))
    )
    per_unit = per_unit_word_target(book) or prof.min_unit_words * 3
    if book.pov == POV.first_person:
        active_pov = pov_name(outline_block(outline_md, 1), book.pov) or pov_name(cur_outline, book.pov) or book.pov.value
    else:
        active_pov = pov_name(cur_outline, book.pov) or book.pov.value

    if prof.key.value == "picture_book":
        desired = 1
        card_count = "1 spread card"
        illus_rule = 'Include a vivid, specific `"illustration_prompt"` describing composition, characters, lighting, and emotion for the illustrator.'
    else:
        desired = 1 if per_unit <= 2500 else (2 if per_unit <= 4000 else 3)
        card_count = f"EXACTLY {desired} sequential scene card{'s' if desired > 1 else ''} whose word_budget sums to ~{per_unit}"
        illus_rule = (
            'Include `"illustration_prompt"` if helpful for illustrated books, else `""`.'
            if prof.illustration_support
            else 'Leave `"illustration_prompt": ""`.'
        )

    mystery_mode = is_mystery_genre(book, seed_txt)
    extra_locks: list[str] = [
        "STRICT 1-ROLE & EXACT GENDER LOCK: Every named character in Cast Reference has ONE fixed job/workplace and ONE exact gender/pronoun. NEVER move a character out of their Seed workplace or flip any character's gender pronouns. Use a brief unnamed bystander/clerk if an incidental scene needs one.",
        "FACT, TIMELINE, QUANTITY & HANDOFF FIDELITY: Preserve exact dates, remaining-day subtraction math, material/fastener quantities, active subject roles (NEVER invert who inspects vs builds, who owns vs buys), collateral vs sale terms, and exact giver->recipient direction from Seed, Spine, and Outline. NEVER invent unrequested heirlooms, crimes, or subplots not in Seed/Outline.",
        "PLOT BULLETS ONLY — ZERO QUOTED DIALOGUE OR LITERARY PROSE: Keep `start_state`, `goal`, `turn`, `outcome`, and `hard_stop` terse (10–25 words each); NEVER write quoted spoken dialogue (`\"...\"`, `'...'`, `‘...’`) or first-person `'I ...'` literary sentences inside scene JSON.",
        "STRICT FORWARD CHRONOLOGY (NO REPLAYS): Advance continuously forward from any prior beat without re-entering completed beats, contradicting established deliveries, or jumping backward in time.",
    ]
    if book.pov == POV.first_person:
        extra_locks.append(
            f"FIRST-PERSON NOTE STYLE: Write `start_state`, `goal`, `turn`, `outcome`, and `hard_stop` as terse neutral plot notes (e.g., 'Confirm delivery date and return to truck') — NEVER third-person literary sentences about '{active_pov}'."
        )
    if is_final_standalone:
        if mystery_mode:
            extra_locks.append(
                "FINAL STANDALONE CHAPTER STAGING: Stage the complete on-page confrontation, confession, and joint resolution/arrest of EVERY culprit named in Plot Logic Spine together, plus the quiet closing handoff with ZERO sequel teasers."
            )
        else:
            extra_locks.append(
                "FINAL STANDALONE CHAPTER STAGING: Stage ONLY the Seed and Outline's exact climactic payoff and warm/honest resolution on-page (NEVER invent crimes, legal charges, or unrequested subplots!) with ZERO sequel teasers."
            )

    system = "\n\n".join(
        [
            directives,
            f"Break {prof.unit_label} {n} into {card_count} so the drafter hits word budget cleanly without racing into future chapters.\n"
            "Each scene MUST advance to a new beat, location, or confrontation — NEVER repeat the same shop visit, interrogation, or threat twice in one chapter.\n"
            "Use ONLY established character names from Cast Reference; give any incidental newcomer a unique non-cliché name.\n"
            + "\n".join(extra_locks)
            + f"\n{illus_rule}\n"
            "Return ONLY valid JSON matching:\n"
            '{"chapter": 1, "scenes": [{"title": "...", "word_budget": 600, "location": "...", "time": "...", '
            '"pov": "...", "cast": ["..."], "start_state": "...", "goal": "...", "turn": "...", "outcome": "...", '
            '"hard_stop": "...", "illustration_prompt": ""}]}',
        ]
    )

    ch = book.get_chapter(n)
    user_parts = [
        f"Book: {book.title} | {prof.unit_label} {n}: {ch.title if ch else ''}",
        f"Active POV: {active_pov} | Target total words: ~{per_unit}",
    ]
    if spine_txt:
        user_parts.append(f"Plot Logic & Resolution Spine (CANONICAL LOCKS):\n{spine_txt[:2200]}")
    if seed_txt:
        user_parts.append(f"Premise Seed:\n{seed_txt[:6000]}")
    if cur_outline:
        user_parts.append(f"Outline Entry:\n{cur_outline}")
    if foreshadow:
        user_parts.append("Foreshadowing:\n" + "\n".join(foreshadow))
    if canon_prior:
        user_parts.append(f"Canon Facts (< {prof.unit_label} {n}):\n{canon_prior[:2500]}")
    if summary_prior:
        user_parts.append(f"Prior Story Summary:\n{summary_prior[:3000]}")
    if chars:
        user_parts.append(f"Cast Reference:\n{chars[:3000]}")

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(user_parts),
        task="scenes",
        max_tokens=2200,
        creativity="low",
        json_mode=True,
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, f"Scene cards ({prof.unit_label} {n})")

    try:
        parsed = parse_llm_json(res.text)
        if not isinstance(parsed, dict) or not isinstance(parsed.get("scenes"), list) or not parsed["scenes"]:
            raise ValueError("Missing scenes list")
        payload = {"chapter": n, "scenes": parsed["scenes"]}
    except Exception as exc:
        if on_warning:
            on_warning(f"Scene card JSON parse fallback used for {prof.unit_label} {n}: {exc}")
        payload = {
            "chapter": n,
            "scenes": [
                {
                    "title": ch.title if ch else f"{prof.unit_label} {n}",
                    "word_budget": per_unit,
                    "location": "As outlined",
                    "time": "Continuous",
                    "pov": active_pov,
                    "cast": [],
                    "start_state": "Follow chapter outline opening beat.",
                    "goal": cur_outline[:400] or "Execute chapter dramatic turn.",
                    "turn": "Concrete complication shifts character position.",
                    "outcome": "Stop cleanly at chapter ending beat.",
                    "hard_stop": "Do not enter next chapter beats.",
                    "illustration_prompt": "",
                }
            ],
        }

    # Normalize scene count & strip any quoted literary prose inside plot fields so exemplar_copy cannot misfire
    plot_keys = ("start_state", "goal", "turn", "outcome", "hard_stop")
    scenes_list = [s for s in (payload.get("scenes") or []) if isinstance(s, dict)]
    if prof.key.value != "picture_book" and len(scenes_list) > desired:
        scenes_list = scenes_list[:desired]
        raw_sum = sum(max(200, int(s.get("word_budget") or 0)) for s in scenes_list) or 1
        for s in scenes_list:
            s["word_budget"] = max(450, int(round(per_unit * max(200, int(s.get("word_budget") or 0)) / raw_sum)))
    if scenes_list and prof.key.value != "picture_book":
        _quoted_re = re.compile(r'["“”‘’][^"“”‘’]{8,}["“”‘’]|(?:^|\s)\'[^\']{12,}\'')
        has_bloat = any(
            len(str(sc.get(k) or "").split()) > 48 or bool(_quoted_re.search(str(sc.get(k) or "")))
            for sc in scenes_list
            for k in plot_keys
        )
        if has_bloat:
            try:
                cond_res = await llm_client.complete(
                    system=(
                        "Rewrite each scene card's `start_state`, `goal`, `turn`, `outcome`, and `hard_stop` as concise neutral 10–25 word plot bullets. "
                        "NEVER include quoted spoken dialogue or first-person story prose. Preserve exact plot facts, names, locations, and word_budget values. Return ONLY valid JSON matching the same schema."
                    ),
                    user=json.dumps({"chapter": n, "scenes": scenes_list}, ensure_ascii=False),
                    task="scenes",
                    max_tokens=1800,
                    creativity="low",
                    json_mode=True,
                )
                cond_parsed = parse_llm_json(cond_res.text)
                if isinstance(cond_parsed, dict) and isinstance(cond_parsed.get("scenes"), list) and cond_parsed["scenes"]:
                    scenes_list = [s for s in cond_parsed["scenes"] if isinstance(s, dict)][:desired]
            except Exception:
                pass
            # Deterministic fallback: scrub any surviving quoted dialogue snippets inside plot fields
            for sc in scenes_list:
                for k in plot_keys:
                    val = str(sc.get(k) or "")
                    if val:
                        val = re.sub(r'["“][^"”]{8,}["”]', "", val)
                        val = re.sub(r"‘[^’]{8,}’", "", val)
                        val = re.sub(r"\s{2,}", " ", val).strip(" .;,—-")
                        sc[k] = val or "Advance chapter beat cleanly."
        payload["scenes"] = scenes_list

    allowed_src = f"{book.title}\n{book.idea or ''}\n{read_text(bdir / 'seed.txt')}"
    for sc_item in payload.get("scenes") or []:
        if isinstance(sc_item, dict):
            for k, v in list(sc_item.items()):
                if isinstance(v, str) and v:
                    sc_item[k] = scrub_banned_names(v, allowed_src)
                elif isinstance(v, list):
                    sc_item[k] = [scrub_banned_names(x, allowed_src) if isinstance(x, str) else x for x in v]

    atomic_write_json(scene_path(bdir, n), payload)
    return payload


def load_scenes(bdir: Path, n: int) -> dict[str, Any]:
    data = read_json(scene_path(bdir, n))
    if isinstance(data, dict) and isinstance(data.get("scenes"), list):
        return data
    return {"chapter": n, "scenes": []}


def save_scenes(bdir: Path, n: int, payload: dict[str, Any]) -> dict[str, Any]:
    scenes = payload.get("scenes") if isinstance(payload, dict) else []
    clean = {"chapter": n, "scenes": scenes if isinstance(scenes, list) else []}
    atomic_write_json(scene_path(bdir, n), clean)
    return clean
