from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app import llm_client
from app.pipeline._common import (
    WarnCB,
    craft_prompt_for_book,
    extract_plot_spine,
    foreshadowing_for_chapter,
    is_mystery_genre,
    load_prompt,
    outline_block,
    parse_llm_json,
    voice_or_default,
    warn_if_cut,
)
from app.pipeline.context_builder import filter_sections_before, format_scene_cards
from app.pipeline.directives import build_directives
from app.storage import read_text
from app.store import Book, chapter_path


_EDITOR_LIST_KEYS = (
    "weakest_sentences",
    "ai_patterns",
    "continuity_violations",
    "missed_beats",
    "over_explained_subtext",
    "flat_scenes",
    "what_to_keep",
    "concrete_revisions",
)


def _salvage_editor_json(raw_text: str) -> dict[str, Any] | None:
    """Best-effort deterministic salvage when LLM JSON hits unescaped quotes or mid-array truncation."""
    if not (raw_text or "").strip():
        return None
    txt = raw_text.strip()
    txt = re.sub(r"^```(?:json)?\s*|\s*```$", "", txt, flags=re.IGNORECASE).strip()
    start = txt.find("{")
    if start != -1:
        txt = txt[start:]
    # Strip trailing commas before closing brackets
    repaired = re.sub(r",\s*([\]}])", r"\1", txt)
    for attempt in (repaired, repaired + '"}', repaired + '"]}'):
        try:
            obj = json.loads(attempt)
            if isinstance(obj, dict):
                out = {k: [str(x).strip() for x in (obj.get(k) or []) if str(x).strip()] if isinstance(obj.get(k), list) else [] for k in _EDITOR_LIST_KEYS}
                out["verdict"] = str(obj.get("verdict") or "").strip() or "Tighten prose, dialogue, and canon consistency."
                out["parse_failed"] = False
                return out
        except Exception:
            pass

    # Field-by-field tolerant extraction
    out: dict[str, Any] = {k: [] for k in _EDITOR_LIST_KEYS}
    verdict = ""
    vm = re.search(
        r'"verdict"\s*:\s*"(.*?)"(?=\s*,\s*"(?:' + "|".join(_EDITOR_LIST_KEYS) + r')"|\s*})',
        txt,
        re.DOTALL,
    )
    if vm:
        verdict = re.sub(r"\s+", " ", vm.group(1)).replace('\\"', '"').strip()

    recovered_lists = 0
    for idx, key in enumerate(_EDITOR_LIST_KEYS):
        next_keys = "|".join(_EDITOR_LIST_KEYS[idx + 1 :])
        pat = (
            rf'"{key}"\s*:\s*\[(.*?)(?:\](?=\s*,\s*"(?:{next_keys}|verdict)"|\s*}}|$)|\Z)'
            if next_keys
            else rf'"{key}"\s*:\s*\[(.*?)(?:\]\s*}}?|\Z)'
        )
        km = re.search(pat, txt, re.DOTALL)
        if not km:
            continue
        body = km.group(1).strip()
        items: list[str] = []
        # Match lines or quoted array elements robustly
        for raw_m in re.finditer(r'"((?:\\.|[^"\n\\])*)"(?=\s*(?:,|\Z|\]))', body):
            val = raw_m.group(1).replace('\\"', '"').strip()
            if val:
                items.append(val)
        if not items and body:
            for seg in re.split(r'",\s*\n\s*"|"\s*,\s*"', body):
                clean = seg.strip().strip('"[], ').replace('\\"', '"')
                if len(clean) >= 6:
                    items.append(clean)
        if items:
            out[key] = items[:4]
            recovered_lists += 1

    if verdict or recovered_lists >= 2:
        out["verdict"] = verdict or "Revise flagged continuity, dialogue, and AI-style issues cleanly."
        out["parse_failed"] = False
        return out
    return None


_STRIP_VOICE_SEC_RE = re.compile(
    r"^###\s+(?:Unrelated Style Specimen|AI-Tic Anti-Examples[^\n]*)\s*$[\s\S]*?(?=^###\s+|\Z)",
    re.MULTILINE | re.IGNORECASE,
)


def _strip_voice_specimens(voice_md: str) -> str:
    """Remove dummy style specimens before feeding Voice Guide rules to editor_pass."""
    if not (voice_md or "").strip():
        return ""
    cleaned = _STRIP_VOICE_SEC_RE.sub("", voice_md)
    cleaned = re.sub(r"\n{3,}", "\n\n", cleaned).strip()
    return cleaned


def _scan_block(scan_report: dict[str, Any] | None) -> str:
    if not scan_report:
        return ""
    findings = scan_report.get("findings") or []
    errors = [f for f in findings if f.get("severity") == "error"]
    warns = [f for f in findings if f.get("severity") == "warning"][:8]
    lines: list[str] = []
    for f in errors[:10]:
        q = (f.get("quote") or "").strip()
        line_no = f.get("line", 1)
        if q:
            lines.append(f"- [ERROR L{line_no} | {f['rule']}] `{q}` -> {f['message']}")
        else:
            lines.append(f"- [ERROR L{line_no} | {f['rule']}] {f['message']}")
    for f in warns:
        q = (f.get("quote") or "").strip()
        line_no = f.get("line", 1)
        lines.append(f"- [WARN L{line_no} | {f['rule']}]" + (f" `{q}`" if q else "") + f" -> {f['message']}")
    return "\n".join(lines)


def render_brief(
    analysis: dict[str, Any],
    *,
    scan_report: dict[str, Any] | None = None,
    voice_rules: str = "",
    user_notes: str = "",
) -> str:
    problems: list[str] = []
    sb = _scan_block(scan_report)
    if sb:
        problems.append("### Mechanical AI-Checker Hits\n" + sb)

    editorial_issues: list[str] = []
    for key, label in (
        ("ai_patterns", "AI patterns"),
        ("weakest_sentences", "Weak line"),
        ("continuity_violations", "Continuity"),
        ("missed_beats", "Missed beat"),
        ("flat_scenes", "Flat scene"),
        ("over_explained_subtext", "Over-explained"),
    ):
        for item in (analysis.get(key) or [])[:3]:
            editorial_issues.append(f"- **{label}:** {item}")
    if editorial_issues:
        problems.append("### Editorial Findings\n" + "\n".join(editorial_issues))

    keeps = [f"- {x}" for x in (analysis.get("what_to_keep") or [])[:5]]
    changes = [f"- {x}" for x in (analysis.get("concrete_revisions") or [])[:6]]
    if user_notes.strip():
        changes.insert(0, f"- **Author note:** {user_notes.strip()}")

    sections = [
        "## PROBLEM\n" + ("\n\n".join(problems) if problems else "- No critical mechanical errors."),
        "## WHAT TO KEEP\n" + ("\n".join(keeps) if keeps else "- Keep the strongest concrete sensory moments and dialogue."),
        "## WHAT TO CHANGE\n" + ("\n".join(changes) if changes else "- Tighten loose prose and sharpen character subtext."),
        "## VOICE RULES\n" + (voice_rules.strip()[:1400] if voice_rules.strip() else "- Match the active POV, tense, and register strictly."),
        "## TARGET\n" + (str(analysis.get("verdict") or "Elevate prose cleanliness, sensory specificity, and pacing while honoring canon.")),
    ]
    return "\n\n".join(sections)


async def run(
    book: Book,
    bdir: Path,
    n: int,
    chapter_text: str,
    *,
    scan_report: dict[str, Any] | None = None,
    user_notes: str = "",
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> tuple[dict[str, Any], str]:
    """Run voice-aware, audience-aware developmental + line editorial pass."""
    directives = build_directives(book)
    craft = craft_prompt_for_book(book)
    anti_slop = load_prompt("anti_slop.md")
    voice = voice_or_default(bdir)
    chars = read_text(bdir / "characters.md").strip()
    outline_md = read_text(bdir / "outline.md")
    spine_txt = extract_plot_spine(outline_md)
    cur_outline = outline_block(outline_md, n)
    foreshadow = foreshadowing_for_chapter(outline_md, n)
    scenes = format_scene_cards(bdir, n, "Unit")
    canon_prior = filter_sections_before(read_text(bdir / "canon.md"), n)
    summary_prior = filter_sections_before(read_text(bdir / "summary.md"), n)
    prev_tail = read_text(chapter_path(bdir, n - 1))[-1200:].strip() if n > 1 else ""

    total_units = book.chapter_count or len(book.chapters) or 0
    seed_raw_full = read_text(bdir / "seed.txt").strip() or (book.idea or "")
    seed_low = seed_raw_full.lower()
    mystery_mode = is_mystery_genre(book, seed_raw_full)
    closure_rule = ""
    if (
        total_units >= 2
        and n == total_units
        and not any(w in seed_low for w in ("cliffhanger", "sequel", "book 1", "series opener"))
    ):
        if mystery_mode:
            closure_rule = (
                "Since this is the FINAL unit of a standalone book, flag under missed_beats/concrete_revisions any invented "
                "sequel cliffhanger, new shadowy villain, or off-page arrest/resolution.\n"
            )
        else:
            closure_rule = (
                "Since this is the FINAL unit of a standalone non-mystery book, flag under continuity_violations/missed_beats any invented "
                "crimes, police, arrests, sequel cliffhangers, or deviation from the Seed's promised ending.\n"
            )

    check_line_3 = (
        "3. Premature future-clue appearances (`future_clue_leak` items scheduled for later chapters), exact day-gap subtraction math (e.g., Tue Oct 8 -> Fri Oct 11 is 3 days, Thu Oct 10 -> Fri Oct 11 is 1 day), workplace fidelity, and causal order of discoveries.\n"
        "4. Seed subplot & ending fidelity: flag dropped subplots, invented relatives/heirlooms, collateral-vs-sale confusion, repeated announcements of facts settled in earlier chapters, or closing sequel hooks (`another layer`, `only a chapter`, `other secrets waited`).\n"
    )
    system = "\n\n---\n\n".join(
        [
            directives,
            "You are a ruthless, constructive senior acquiring editor reviewing one unit strictly against this book's "
            "audience, voice guide, character speech contrasts, canon, and outline.\n"
            "Do NOT score 1–10. Give sharp, surgical fixes (max 3 items per list, max 25 words per item).\n"
            "In concrete_revisions, write imperative craft directions (e.g., 'Dramatize the argument in spoken dialogue instead of summarizing it'). "
            "NEVER write meta-labels like '(showcasing his analytical nature)', detached passive-voice mandates, or verbatim replacement prose.\n"
            "STRICT CHECKS — flag ANY violation in continuity_violations / concrete_revisions:\n"
            "1. Character gender pronouns & honorifics (`Ms.` vs `Mr.`, `she/her` vs `he/him`) — flag any misgendering immediately.\n"
            "2. Unquoted dialogue attribution, subject confusion (boat vs narrator), and dropped/flipped prop handoffs.\n"
            f"{check_line_3}"
            "5. Low spoken dialogue (< 22% of chapter), passive/clinical phrasing, Unicode em-dashes ('—') or dash clusters (> 1/150w), or stock AI tells (`rain intensified`, `unreadable`, `the weight of`, `felt`, `seemed`, `silence stretched`, `avoiding eye contact`).\n"
            f"{closure_rule}"
            "CRITICAL JSON SAFETY: NEVER put double quotes inside JSON string values — paraphrase dialogue or use SINGLE quotes ('...') only!\n"
            "Return ONLY valid JSON with exact keys:\n"
            '{"verdict": "...", "weakest_sentences": ["..."], "ai_patterns": ["..."], '
            '"continuity_violations": ["..."], "missed_beats": ["..."], "over_explained_subtext": ["..."], '
            '"flat_scenes": ["..."], "what_to_keep": ["..."], "concrete_revisions": ["..."]}',
            craft,
            anti_slop,
            f"## VOICE GUIDE\n{_strip_voice_specimens(voice)[:3500]}",
        ]
    )

    seed_txt = seed_raw_full[:6000]
    parts = [f"Book: {book.title} | Unit {n}"]
    if seed_txt:
        parts.append(f"GROUND TRUTH SEED (MUST NOT CONTRADICT):\n{seed_txt}")
    if spine_txt:
        parts.append(f"PLOT LOGIC & RESOLUTION SPINE:\n{spine_txt[:2000]}")
    if chars:
        parts.append(f"CHARACTERS:\n{chars[:3000]}")
    if canon_prior:
        parts.append(f"CANON (< Unit {n}):\n{canon_prior[:3000]}")
    if summary_prior:
        parts.append(f"STORY SO FAR (< Unit {n}):\n{summary_prior[:3000]}")
    if cur_outline:
        parts.append(f"OUTLINE FOR UNIT {n}:\n{cur_outline}")
    if scenes:
        parts.append(scenes)
    if foreshadow:
        parts.append("FORESHADOWING:\n" + "\n".join(foreshadow))
    if prev_tail:
        parts.append(f"PREVIOUS UNIT TAIL:\n{prev_tail}")
    if scan_report and scan_report.get("findings"):
        parts.append("AUTOMATED AI CHECKER FINDINGS:\n" + _scan_block(scan_report))
    parts.append(f"DRAFT TEXT TO REVIEW:\n{chapter_text}")

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(parts),
        task="editor",
        max_tokens=2400,
        creativity="low",
        json_mode=True,
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, f"Editor pass (Unit {n})")

    try:
        parsed = parse_llm_json(res.text)
        if not isinstance(parsed, dict):
            raise ValueError("Editor JSON root is not an object")
        parsed["parse_failed"] = False
    except Exception:
        # One fast JSON-repair attempt, then deterministic salvage parser before failing
        try:
            rep_res = await llm_client.complete(
                system=(
                    "Fix the malformed JSON into valid JSON keeping the exact schema: "
                    '{"verdict": "...", "weakest_sentences": [], "ai_patterns": [], "continuity_violations": [], '
                    '"missed_beats": [], "over_explained_subtext": [], "flat_scenes": [], "what_to_keep": [], "concrete_revisions": []}. '
                    "Replace any unescaped double quotes inside string values with single quotes ('...'). Return ONLY valid JSON."
                ),
                user=res.text[:9000],
                task="editor",
                max_tokens=2000,
                creativity="low",
                json_mode=True,
            )
            parsed = parse_llm_json(rep_res.text)
            if not isinstance(parsed, dict):
                raise ValueError("Repaired Editor JSON root is not an object")
            parsed["parse_failed"] = False
        except Exception as exc:
            salvaged = _salvage_editor_json(res.text)
            if salvaged is not None:
                parsed = salvaged
            else:
                if on_warning:
                    on_warning(f"Editor JSON parse failed on Unit {n}: {exc}. Returning mechanical findings + voice rules only.")
                parsed = {
                    "parse_failed": True,
                    "verdict": f"Editor pass JSON failed ({exc}); resolve all mechanical checker findings and voice rules.",
                    "weakest_sentences": [],
                    "ai_patterns": [],
                    "continuity_violations": [],
                    "missed_beats": [],
                    "over_explained_subtext": [],
                    "flat_scenes": [],
                    "what_to_keep": [],
                    "concrete_revisions": [],
                }

    brief = render_brief(parsed, scan_report=scan_report, voice_rules=voice, user_notes=user_notes)
    return parsed, brief
