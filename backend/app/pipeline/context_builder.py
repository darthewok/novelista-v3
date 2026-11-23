from __future__ import annotations

import re
from pathlib import Path

from app.enums import POV
from app.formats import get_profile
from app.pipeline._common import (
    approx_tokens,
    extract_plot_spine,
    foreshadowing_for_chapter,
    later_units_fence,
    load_prompt,
    next_unit_preview,
    outline_block,
    pov_name,
    craft_prompt_for_book,
    voice_or_default,
)
from app.pipeline.ai_checker import future_foreshadowing_items
from app.pipeline.directives import build_directives, per_unit_word_target
from app.settings import active_provider_config
from app.storage import read_json, read_text
from app.store import Book, chapter_path, scene_path

_CH_SEC_RE = re.compile(r"^##\s+(?:Ch(?:apter)?|Spread|Part)\.?\s*(\d+)\b", re.IGNORECASE)


def filter_sections_before(markdown_doc: str, n: int) -> str:
    """Keep preamble/top-level non-chapter sections + ONLY `## Ch K` sections with K < n.

    Prevents future-chapter continuity leaks when revising or redrafting chapter `n`.
    """
    if not (markdown_doc or "").strip():
        return ""
    lines = markdown_doc.splitlines()
    out: list[str] = []
    skip_section = False
    for ln in lines:
        if ln.startswith("## "):
            m = _CH_SEC_RE.match(ln.strip())
            if m:
                ch_num = int(m.group(1))
                skip_section = ch_num >= n
            else:
                skip_section = False
        if not skip_section:
            out.append(ln)
    return "\n".join(out).strip()


def compact_summary_window(summary_md: str, n: int, full_recent: int = 3) -> str:
    """Keep full prose summaries for the most recent `full_recent` chapters `< n`,
    and compress older chapters to just their `- State:` / heading lines."""
    filtered = filter_sections_before(summary_md, n)
    if not filtered:
        return ""
    sections = re.split(r"(?=^##\s+)", filtered, flags=re.MULTILINE)
    kept: list[str] = []
    cutoff = max(1, n - full_recent)
    for sec in sections:
        s = sec.strip()
        if not s:
            continue
        m = _CH_SEC_RE.match(s.splitlines()[0].strip())
        if not m:
            kept.append(s)
            continue
        ch_num = int(m.group(1))
        if ch_num >= cutoff:
            kept.append(s)
        else:
            # Older chapter: keep heading + state line only to keep prompt lean
            lines = [ln for ln in s.splitlines() if ln.startswith("## ") or ln.lstrip().startswith("- State:")]
            kept.append("\n".join(lines[:3]))
    return "\n\n".join(kept).strip()


def format_scene_cards(bdir: Path, n: int, unit_label: str) -> str:
    data = read_json(scene_path(bdir, n))
    if not isinstance(data, dict):
        return ""
    cards = data.get("scenes") or []
    if not cards:
        return ""
    lines = [f"### {unit_label.upper()} {n} SCENE PLAN"]
    for idx, sc in enumerate(cards, 1):
        lines.append(
            f"{idx}. **{sc.get('title', f'Scene {idx}')}** (~{sc.get('word_budget', '?')} words)"
            f" | Location: {sc.get('location', '?')} | Time: {sc.get('time', '?')}"
            f" | Cast: {', '.join(sc.get('cast') or [])}"
        )
        if sc.get("start_state"):
            lines.append(f"   - Start: {sc['start_state']}")
        if sc.get("goal"):
            lines.append(f"   - Goal / Friction: {sc['goal']}")
        if sc.get("turn"):
            lines.append(f"   - Turn: {sc['turn']}")
        if sc.get("outcome"):
            lines.append(f"   - Outcome: {sc['outcome']}")
        if sc.get("illustration_prompt"):
            lines.append(f"   - Illustration Concept (visual context only; don't over-narrate): {sc['illustration_prompt']}")
        if sc.get("hard_stop"):
            lines.append(f"   - Hard Stop Beat: {sc['hard_stop']}")
    return "\n".join(lines)


def retired_motifs(bdir: Path, n: int) -> list[str]:
    """Identify overused 4-word similes/images in earlier chapters so we advise fresh imagery
    abstractly without injecting huge verbatim copyable passages."""
    if n <= 1:
        return []
    counts: dict[str, int] = {}
    for k in range(1, n):
        txt = read_text(chapter_path(bdir, k))
        for m in re.finditer(r"\b(?:like|as)\s+(?:a|an|the)\s+([a-z]+(?:\s+[a-z]+){1,4})", txt, re.IGNORECASE):
            phrase = re.sub(r"\s+", " ", m.group(0).lower().strip())
            counts[phrase] = counts.get(phrase, 0) + 1
    return [p for p, c in sorted(counts.items(), key=lambda kv: -kv[1]) if c >= 2][:12]


def build_static_system(book: Book, bdir: Path, *, mode: str = "draft") -> str:
    """Build a 100% chapter-invariant system prompt (optimal for Anthropic prompt caching)."""
    prof = get_profile(book.book_format)
    directives = build_directives(book)
    craft = craft_prompt_for_book(book)
    anti_slop = load_prompt("anti_slop.md")
    voice = voice_or_default(bdir)

    if prof.craft_profile == "children":
        scene_rule = (
            "- **Page-turn / scene focus:** Keep every beat visual, lively, and emotionally honest; "
            "never drop into adult cynicism or horror tropes."
        )
    else:
        scene_rule = (
            "- **70%+ in-scene & 20–35% dramatized dialogue:** Play critical turns moment by moment through physical action, sensory detail, "
            "and character-distinct spoken dialogue exchanges whenever two or more characters share a scene. Never summarize conversations or pad with "
            "repeated weather updates ('rain intensified') or repeated facial labels ('unreadable')."
        )

    role_line = (
        f"You are drafting one {prof.unit_label.lower()} of **{book.title}**."
        if mode == "draft"
        else f"You are revising an existing {prof.unit_label.lower()} of **{book.title}** to elevate prose quality while preserving continuity."
    )

    rules = "\n".join(
        [
            role_line,
            "",
            "### STRICT PROSE & CONTINUITY RULES",
            scene_rule,
            "- **Open inside motion:** Begin directly inside the scene's opening beat — no summary preamble or throat-clearing.",
            "- **Active fiction voice & clean syntax:** Use vivid active verbs and natural spoken contractions; keep em-dashes sparse (<= 1 per 150 words) and avoid 'felt', 'seemed', 'unreadable', 'tangible', 'palpable', 'avoiding eye contact', or 'It wasn't X; it was Y'.",
            "- **Continue forward, never replay:** The previous tail (if provided) is *already published* context; start from the next beat, never echo or paraphrase the ending you were given.",
            "- **Never plagiarize the Voice Guide or Character Sheet:** Treat style samples and speech notes as tone guides only; invent completely new phrasing and fresh dialogue.",
            "- **Scope boundary:** Stop cleanly at the end of this unit's final beat. Never resolve or summarize beats listed under LATER UNITS.",
            "- **Clean output:** Return pure story prose only (start with `## {unit_label} N: Title` or clean scene prose). Never output beat notes, meta-commentary, or `*Plant N*` tags.",
            "- **No chapter-ending clichés:** End on a grounded concrete image, line of dialogue, or altered choice — never closing eyes, walking out a door, or an abstract moral.",
        ]
    ).replace("{unit_label}", prof.unit_label)

    return "\n\n---\n\n".join(
        [
            directives,
            rules,
            craft,
            anti_slop,
            f"## VOICE GUIDE\n{voice}",
        ]
    )


def _filter_early_spine(spine_txt: str, future_items: list[tuple[str, int]]) -> str:
    """Keep baseline cast/crime/workplace locks for early chapters while stripping late-story spoilers."""
    if not (spine_txt or "").strip():
        return ""
    spoiler_pats: list[re.Pattern[str]] = []
    for item_lbl, _ in future_items:
        toks = [t for t in re.findall(r"[A-Za-z0-9'-]+", item_lbl.lower()) if len(t) >= 4 and t not in {"with", "from", "that", "this"}]
        if len(toks) >= 2:
            spoiler_pats.append(re.compile(r"\b" + r"\s+".join(re.escape(w) for w in toks[:2]) + r"\b", re.IGNORECASE))
    kept: list[str] = []
    for ln in spine_txt.splitlines():
        s = ln.strip()
        if re.match(r"^(?:3\.|4\.|-\s*\*\*(?:Causal Clue|Named Culprit))", s, re.IGNORECASE):
            continue
        if any(pat.search(s) for pat in spoiler_pats):
            continue
        kept.append(ln)
    return "\n".join(kept).strip()


def extract_opening_word(text: str) -> str:
    """Return the first prose narration word (skipping markdown headings and scene dividers)."""
    for ln in (text or "").splitlines():
        s = ln.strip()
        if not s or s.startswith("#") or re.match(r"^(?:\*\s*){3,}$|^-{3,}$", s):
            continue
        fm = re.match(r'^["""\'\s]*([A-Za-z][A-Za-z\'-]*)', s)
        if fm:
            return fm.group(1)
        break
    return ""


def build_chapter_context(
    book: Book,
    bdir: Path,
    n: int,
    *,
    include_draft_fences: bool = True,
    include_scene_cards: bool = True,
    scene_mode: bool = False,
    max_prev_chars: int = 1600,
) -> tuple[str, dict]:
    """Assemble token-budgeted, strictly `< n`-filtered context for chapter `n`."""
    prof = get_profile(book.book_format)
    ch = book.get_chapter(n)
    title = ch.title if ch else f"{prof.unit_label} {n}"
    per_unit = per_unit_word_target(book)

    outline_md = read_text(bdir / "outline.md")
    cur_outline = outline_block(outline_md, n)
    nxt_outline = next_unit_preview(outline_md, n)
    foreshadow = foreshadowing_for_chapter(outline_md, n)
    later_fence = later_units_fence(outline_md, n, prof.unit_label) if include_draft_fences else []
    scenes_txt = format_scene_cards(bdir, n, prof.unit_label) if include_scene_cards else ""

    # Strict < n filtering on summary & canon!
    _, cfg = active_provider_config()
    ctx_budget_tokens = max(6000, int(cfg.num_ctx * 0.45))

    summary_txt = compact_summary_window(read_text(bdir / "summary.md"), n, full_recent=4)
    canon_txt = filter_sections_before(read_text(bdir / "canon.md"), n)
    world_txt = read_text(bdir / "world.md").strip()
    chars_txt = read_text(bdir / "characters.md").strip()

    # Check continuity.json for structured state < n
    cont_data = read_json(bdir / "continuity.json") or {}
    cont_lines: list[str] = []
    if isinstance(cont_data, dict):
        for k in sorted((int(x) for x in cont_data.keys() if str(x).isdigit() and int(x) < n)):
            entry = cont_data[str(k)]
            if isinstance(entry, dict) and entry.get("state"):
                cont_lines.append(f"- After {prof.unit_label} {k}: {entry['state']}")

    prev_tail = ""
    if n > 1:
        raw_prev = read_text(chapter_path(bdir, n - 1)).strip()
        if raw_prev:
            prev_tail = raw_prev[-max_prev_chars:]
            # Snap to paragraph boundary if possible
            nl = prev_tail.find("\n\n")
            if 0 < nl < 400:
                prev_tail = prev_tail[nl + 2 :].strip()

    total_units = book.chapter_count or len(book.chapters) or prof.default_units
    spine_txt = extract_plot_spine(outline_md)
    future_items = future_foreshadowing_items(outline_md, n) if include_draft_fences else []

    prev_openers: list[str] = []
    if n > 1:
        for k in range(1, n):
            ptxt = read_text(chapter_path(bdir, k))
            for ln in ptxt.splitlines():
                s = ln.strip()
                if not s or s.startswith("#") or re.match(r"^(?:\*\s*){3,}$|^-{3,}$", s):
                    continue
                fm = re.match(r'^["""\'\s]*([A-Za-z][A-Za-z\'-]*)', s)
                if fm:
                    prev_openers.append(f"{prof.unit_label} {k}: '{fm.group(1)}'")
                break

    parts: list[str] = [f"# PROJECT: {book.title}"]

    if spine_txt:
        if n >= max(2, total_units - 1):
            parts.append(f"## PLOT LOGIC & RESOLUTION SPINE (MUST PAY OFF ON-PAGE)\n{spine_txt[:2200]}")
        else:
            filtered_spine = _filter_early_spine(spine_txt, future_items) or spine_txt[:900]
            parts.append(f"## PLOT SPINE ANCHOR (CORE TIES & RULES — DO NOT CONTRADICT)\n{filtered_spine[:2200]}")

    if world_txt and "world" not in prof.optional_phases:
        parts.append(f"## WORLD REFERENCE\n{world_txt[:5000]}")
    elif world_txt:
        parts.append(f"## SETTING SNAPSHOT\n{world_txt[:2200]}")

    if chars_txt:
        parts.append(f"## CHARACTERS & VOICE MATRIX\n{chars_txt[:6000]}")

    if canon_txt:
        parts.append(f"## CANON FACTS (UP TO {prof.unit_label.upper()} {n - 1})\n{canon_txt[:5000]}")

    if cont_lines:
        parts.append("## STRUCTURED CONTINUITY LEDGER\n" + "\n".join(cont_lines[-5:]))

    if summary_txt:
        parts.append(f"## STORY SO FAR (DO NOT RE-TELL THESE EVENTS)\n{summary_txt[:5500]}")

    if cur_outline and not scene_mode:
        parts.append(f"## THIS {prof.unit_label.upper()}'S OUTLINE\n{cur_outline}")

    if scenes_txt:
        parts.append(scenes_txt)

    if foreshadow:
        parts.append(f"## FORESHADOWING FOR {prof.unit_label.upper()} {n}\n" + "\n".join(foreshadow))

    if nxt_outline:
        parts.append(f"## NEXT {prof.unit_label.upper()} PREVIEW (HANDOFF TARGET ONLY — DO NOT WRITE)\n{nxt_outline}")

    if prev_tail:
        parts.append(
            f"## END OF PREVIOUS {prof.unit_label.upper()} (ALREADY WRITTEN — CONTINUE FORWARD, DO NOT REPEAT)\n{prev_tail}"
        )

    retired = retired_motifs(bdir, n)
    if retired:
        parts.append(
            "## IMAGERY ALREADY USED EARLIER (FIND FRESH COMPARISONS INSTEAD)\n" + ", ".join(retired)
        )

    if later_fence:
        parts.append(
            f"## LATER {prof.unit_label_plural.upper()} — OFF-LIMITS SCOPE FENCE (DO NOT SPOIL OR RESOLVE)\n"
            + "\n".join(later_fence)
        )

    if future_items:
        parts.append(
            f"## FUTURE CLUES — STRICTLY FORBIDDEN IN {prof.unit_label.upper()} {n} (DO NOT APPEAR, DROP, OR DISCOVER YET)\n"
            + "\n".join(
                f"- **{item}** (reserved for {prof.unit_label} {ch_no} — NEVER mention, plant, or place on-page here!)"
                for item, ch_no in future_items
            )
        )

    if prev_openers:
        parts.append(
            f"## PREVIOUS {prof.unit_label.upper()} OPENING WORDS (DO NOT REUSE)\n"
            + f"Earlier {prof.unit_label_plural.lower()} began with: {', '.join(prev_openers)}.\n"
            + f"Start {prof.unit_label} {n}'s FIRST narration word cleanly with a FRESH word and action (never start with 'The' if already used)."
        )

    # Dynamic POV lock lives in user message so system prompt remains 100% cacheable
    if book.pov == POV.first_person:
        active_pov = (
            pov_name(outline_block(outline_md, 1), book.pov)
            or pov_name(cur_outline, book.pov)
            or (ch.pov if ch and ch.pov else None)
        )
        if active_pov:
            parts.append(
                f"## POV LOCK\nYou are writing as **{active_pov}** in strict first-person ('I'). NEVER narrate as any other character or switch 'I' mid-book."
            )
    else:
        active_pov = pov_name(cur_outline, book.pov) or (ch.pov if ch and ch.pov else None)
        if active_pov and book.pov != POV.third_omniscient:
            parts.append(
                f"## POV LOCK\nAnchor strictly in **{active_pov}**'s perceptions, thoughts, and immediate sensory knowledge. Never head-hop into other characters' unspoken feelings."
            )

    band_clause = ""
    if per_unit:
        lo = max(prof.min_unit_words, int(per_unit * 0.85))
        hi = int(per_unit * 1.15)
        band_clause = f"Target length: **{lo:,}–{hi:,} words** (aim near {per_unit:,} words)."

    opener_clause = ""
    if prev_openers:
        opener_clause = f" HARD OPENING RULE: Do NOT start sentence 1 with: {', '.join(prev_openers)}."

    seed_all_low = f"{book.idea or ''}\n{read_text(bdir / 'seed.txt')}".lower()
    if (
        total_units >= 2
        and n == total_units
        and not any(w in seed_all_low for w in ("cliffhanger", "sequel", "book 1", "series opener"))
    ):
        parts.append(
            f"## FINAL {prof.unit_label.upper()} CLOSURE LOCK\n"
            "This is the FINAL unit of a standalone work. Resolve the central premise conflict/mystery completely ON-PAGE "
            "with concrete proof, confrontation/outcome, and emotional closure. Do NOT invent any new villain, shell company, "
            "hidden conspiracy, or sequel cliffhanger."
        )

    if scene_mode:
        parts.append(
            f"## ACTIVE ASSIGNMENT\nWrite **{prof.unit_label} {n}: {title}** scene-by-scene strictly matching the single-scene target length below.{opener_clause}"
        )
    else:
        beat_guide = (
            " Unfold 3 substantial on-page beats (arrival & grounding -> extended back-and-forth spoken exchange with pushback & clue inspection -> rising complication & grounded closing image) to hit target length cleanly."
            if prof.craft_profile == "novel"
            else ""
        )
        parts.append(
            f"## ACTIVE ASSIGNMENT\nWrite **{prof.unit_label} {n}: {title}**. {band_clause}{opener_clause}{beat_guide}"
        )

    joined = "\n\n".join(parts)
    # Soft budget trim: if exceeding budget, trim world/chars slightly while preserving outline, scenes, prev_tail, assignment
    if approx_tokens(joined) > ctx_budget_tokens and world_txt:
        parts = [p[:3000] if p.startswith("## WORLD") or p.startswith("## CHARACTERS") else p for p in parts]
        joined = "\n\n".join(parts)

    meta = {
        "title": title,
        "per_unit_target": per_unit,
        "pov_character": active_pov or "",
        "unit_label": prof.unit_label,
        "outline_block": cur_outline,
        "foreshadowing": foreshadow,
    }
    return joined, meta
