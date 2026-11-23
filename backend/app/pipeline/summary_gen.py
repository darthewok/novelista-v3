from __future__ import annotations

import re
from pathlib import Path

from app import llm_client
from app.formats import get_profile
from app.pipeline import ai_checker
from app.pipeline._common import WarnCB, outline_block, parse_llm_json, pov_name, upsert_markdown_section, warn_if_cut
from app.pipeline.characters_gen import scrub_banned_names
from app.storage import atomic_write_json, atomic_write_text, read_json, read_text
from app.store import Book


_MACRON_TRANSLIT = str.maketrans({"ā": "a", "ē": "e", "ī": "i", "ō": "o", "ū": "u", "Ā": "A", "Ē": "E", "Ī": "I", "Ō": "O", "Ū": "U"})


def _restore_cast_names(text: str, ref_src: str) -> str:
    """Repair 1-char surname truncations (e.g. 'Kenji Sat' -> 'Kenji Sato') & accidental macrons using cast pairs."""
    if not (text or "").strip() or not (ref_src or "").strip():
        return text or ""
    out = text
    seen_pairs: list[tuple[str, str]] = []
    seen_low: set[tuple[str, str]] = set()
    for m in re.finditer(r"\b([A-Z][a-z]{2,18})\s+([A-Z][a-z]{3,20})\b", ref_src):
        f_name, l_name = m.group(1), m.group(2)
        if f_name in {"Clatsop", "Lincoln", "Jefferson", "Pacific", "Youngs", "Yaquina", "Halvorsen", "Lindholm", "County", "Marine", "Harbor", "Waterfront", "Cascade", "Oregon"}:
            continue
        key = (f_name.lower(), l_name.lower())
        if key not in seen_low:
            seen_low.add(key)
            seen_pairs.append((f_name, l_name))

    # First normalize accidental macrons on cast surnames (e.g., Satō -> Sato)
    for f_name, l_name in seen_pairs:
        norm_text = out.translate(_MACRON_TRANSLIT)
        if norm_text != out and re.search(rf"\b{re.escape(l_name)}\b", norm_text):
            out = re.sub(
                rf"\b[^\W\d_]{{{max(3, len(l_name) - 1)},{len(l_name) + 1}}}\b",
                lambda mm, ln=l_name: ln if mm.group(0).translate(_MACRON_TRANSLIT).lower() == ln.lower() else mm.group(0),
                out,
            )

    # Next repair missing trailing letter on cast surnames (paired with first name or unambiguous surname trunc)
    for f_name, l_name in seen_pairs:
        trunc = l_name[:-1]
        if len(trunc) < 3:
            continue
        out = re.sub(rf"\b({re.escape(f_name)}\s+){re.escape(trunc)}\b", rf"\g<1>{l_name}", out)
        # Fix truncated possessive/mention when full name was also in the same ref
        if re.search(rf"\b{re.escape(f_name)}\s+{re.escape(l_name)}\b", ref_src):
            out = re.sub(rf"\b{re.escape(trunc)}(?=(?:'s|’s)\b)", l_name, out)
    return out


async def update(
    book: Book,
    bdir: Path,
    n: int,
    chapter_text: str,
    *,
    on_warning: WarnCB = None,
) -> str:
    """Summarize unit `n` into `summary.md` AND structured `continuity.json`, rejecting corrupted loops."""
    # Guard: never summarize a corrupted/melting chapter into story memory!
    pre_check = ai_checker.scan(chapter_text, book=book, bdir=bdir, chapter_num=n)
    if any(f["rule"] == "token_meltdown" for f in pre_check["findings"]):
        if on_warning:
            on_warning(f"Skipped updating story memory for Unit {n} because text has token corruption.")
        return read_text(bdir / "summary.md")

    prof = get_profile(book.book_format)
    ch = book.get_chapter(n)
    title = ch.title if ch else f"{prof.unit_label} {n}"

    if prof.craft_profile == "children":
        state_fields = "characters_present, current_location, props_or_keepsakes, mood_shift, open_curiosity"
    else:
        state_fields = "alive, dead_or_missing, locations, physical_state_or_props, promises_or_deals, open_threads"

    chars_ref = read_text(bdir / "characters.md").strip()[:1500]
    outline_md = read_text(bdir / "outline.md")
    active_pov = pov_name(outline_block(outline_md, 1), book.pov) or pov_name(outline_block(outline_md, n), book.pov) or (ch.pov if ch else "")
    seed_src = read_text(bdir / "seed.txt").strip() or (book.idea or "")
    ref_lines: list[str] = []
    if active_pov:
        ref_lines.append(f"Active POV / Protagonist: {active_pov}")
    if seed_src:
        ref_lines.append(f"Premise Anchor: {seed_src[:400]}")
    if chars_ref:
        ref_lines.append(f"Cast Sheet:\n{chars_ref}")
    cast_anchor_blk = (
        "CAST REFERENCE (USE EXACT NAMES & RELATIONSHIPS ONLY — NEVER INVENT NAMES):\n"
        + "\n".join(ref_lines)
        + "\n\n"
        if ref_lines
        else ""
    )

    system = (
        f"Summarize {prof.unit_label} {n} accurately and concisely for downstream story continuity.\n"
        "Use ONLY exact established character names and relationships from CAST REFERENCE and TEXT — never invent a new character name if 'I' narrates without stating their first name.\n"
        "Spell EVERY character first and last name 100% verbatim in plain ASCII as written in CAST REFERENCE — never drop trailing letters (e.g., never turn 'Sato' into 'Sat') or add diacritics.\n"
        f"Return ONLY valid JSON: {{\"summary\": \"<=110 words factual prose summary\", \"state\": \"one dense factual line covering: {state_fields}\"}}"
    )
    user = f"{prof.unit_label} {n}: {title}\n\n{cast_anchor_blk}TEXT:\n{chapter_text[:14000]}"

    res = await llm_client.complete(
        system=system,
        user=user,
        task="summary",
        max_tokens=500,
        creativity="low",
        json_mode=True,
    )
    warn_if_cut(res.stop_reason, on_warning, f"Summary update ({prof.unit_label} {n})")

    try:
        parsed = parse_llm_json(res.text)
        summary_txt = str(parsed.get("summary") or "").strip() if isinstance(parsed, dict) else ""
        state_txt = str(parsed.get("state") or "").strip() if isinstance(parsed, dict) else ""
    except Exception:
        summary_txt = res.text.strip()[:500]
        state_txt = ""

    allowed_src = f"{book.title}\n{book.idea or ''}\n{seed_src}"
    name_ref = f"{seed_src}\n{chars_ref}"
    summary_txt = _restore_cast_names(scrub_banned_names(summary_txt, allowed_src), name_ref)
    state_txt = _restore_cast_names(scrub_banned_names(state_txt, allowed_src), name_ref)

    # Anti-corruption guard on summary output itself
    sum_check = ai_checker.scan(f"{summary_txt}\n{state_txt}")
    if any(f["rule"] == "token_meltdown" for f in sum_check["findings"]):
        if on_warning:
            on_warning(f"Summary output for {prof.unit_label} {n} appeared corrupted and was discarded.")
        return read_text(bdir / "summary.md")

    block = summary_txt
    if state_txt:
        block = f"{summary_txt}\n- State: {state_txt}"

    cur_summary = read_text(bdir / "summary.md")
    updated = upsert_markdown_section(cur_summary, f"## Ch {n} — {title}", block)
    atomic_write_text(bdir / "summary.md", updated)

    cont = read_json(bdir / "continuity.json")
    if not isinstance(cont, dict):
        cont = {}
    cont[str(n)] = {"title": title, "summary": summary_txt, "state": state_txt}
    atomic_write_json(bdir / "continuity.json", cont)
    return updated
