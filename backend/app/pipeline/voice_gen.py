from __future__ import annotations

import re
from pathlib import Path

from app import llm_client
from app.enums import POV
from app.formats import get_profile
from app.pipeline._common import WarnCB, is_american_english, load_prompt, strip_llm_chatter, warn_if_cut
from app.pipeline.directives import build_directives
from app.storage import read_text
from app.store import Book

_TRIAL_HEADING_RE = re.compile(
    r"^\s*(?:#{1,4}\s*|\*\*)?Trial\s+(\d+)\b[^\n]*",
    re.MULTILINE | re.IGNORECASE,
)


def parse_trials(trials_md: str) -> list[dict[str, str]]:
    """Robustly parse `### Trial N — Register` / `## Trial N` / `**Trial N**` into structured list."""
    matches = list(_TRIAL_HEADING_RE.finditer(trials_md or ""))
    out: list[dict[str, str]] = []
    for i, m in enumerate(matches):
        num = m.group(1)
        header_line = m.group(0).strip().strip("#* ").strip()
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(trials_md)
        body = (trials_md[start:end] or "").strip()
        register = re.sub(r"^Trial\s+\d+\s*[:\-—–]*\s*", "", header_line, flags=re.IGNORECASE).strip() or f"Register {num}"
        out.append({"index": int(num), "register": register, "text": body})
    return out


async def trials(
    book: Book,
    bdir: Path,
    *,
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> str:
    prof = get_profile(book.book_format)
    directives = build_directives(book)
    anti_slop = load_prompt("anti_slop.md")
    seed = read_text(bdir / "seed.txt").strip() or book.idea
    world = read_text(bdir / "world.md").strip()
    chars = read_text(bdir / "characters.md").strip()

    trial_len = "80–120 words" if prof.key.value == "picture_book" else "180–240 words"
    pov_lock = (
        "All 3 trials MUST narrate strictly in the SAME single protagonist's first-person ('I') voice from Seed — NEVER switch 'I' to another character."
        if book.pov == POV.first_person
        else f"Keep POV strictly locked to {book.pov.value} throughout all 3 trials."
    )
    us_lock = (
        "Use American English, Fahrenheit (°F), and imperial measurements (feet/inches, never Celsius/metric)."
        if is_american_english(book, bdir)
        else ""
    )

    system = "\n\n---\n\n".join(
        [
            directives,
            "Write 3 genuinely distinct, publishable narrative fiction voice trials for the opening moment ONLY (do NOT summarize later events).\n"
            f"Honor the project's Writing Style and Custom Style Notes (`{book.custom_style or book.writing_style.value}`).\n"
            "Contrast 3 human storytelling cadences (e.g., Trial 1: Grounded Tactile Realism with active verbs & natural dialogue; Trial 2: Spare Understated Rhythm; Trial 3: Warm Sensory & Relational Presence).\n"
            "STRICTLY FORBIDDEN in all trials: clinical inspection reports, legalistic passive voice, 'Surveyor's Report', anti-contraction stiffness, Celsius in US settings, or meta commentary ('I aimed for...', 'please let me know...').\n"
            f"{pov_lock} {us_lock}\n"
            f"Each trial must be {trial_len} and contrast sharply in sentence syntax, psychic distance, and sensory focus.\n"
            "Format EXACTLY with these Markdown headings and ZERO conversational intro or sign-off:\n"
            "### Trial 1 — <Descriptive Fiction Register Name>\n<prose>\n\n"
            "### Trial 2 — <Descriptive Fiction Register Name>\n<prose>\n\n"
            "### Trial 3 — <Descriptive Fiction Register Name>\n<prose>\n\n"
            "Make sure none of the three trials repeat each other's metaphors or opening structures.",
            anti_slop,
        ]
    )

    user_parts = [f"Title: {book.title}", f"Seed:\n{seed}"]
    if world:
        user_parts.append(f"World:\n{world[:5000]}")
    if chars:
        user_parts.append(f"Characters:\n{chars[:5000]}")

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(user_parts),
        task="voice",
        max_tokens=2800,
        creativity="high",
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, "Voice trials")
    return strip_llm_chatter(res.text)


async def generate(
    book: Book,
    bdir: Path,
    *,
    choice: int | None = None,
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> str:
    directives = build_directives(book)
    anti_slop = load_prompt("anti_slop.md")
    part1 = load_prompt("voice_default.md")
    seed = read_text(bdir / "seed.txt").strip() or book.idea
    world = read_text(bdir / "world.md").strip()
    chars = read_text(bdir / "characters.md").strip()
    trials_md = read_text(bdir / "voice_trials.md").strip()

    chosen_note = ""
    if choice and trials_md:
        parsed = parse_trials(trials_md)
        hit = next((t for t in parsed if t["index"] == choice), None)
        if hit:
            chosen_note = f"Selected register (#{choice} — {hit['register']}):\n{hit['text']}"

    system = "\n\n---\n\n".join(
        [
            directives,
            "Build **Part 2 — Project Voice Guide** for this specific book.\n"
            "CRITICAL ANTI-COPY RULE: Your exemplar and anti-exemplar sentences MUST depict an **UNRELATED dummy scenario** "
            "(invent a completely unrelated everyday scene—like repairing a bicycle tire or sorting dry beans in a kitchen—with NO character names, locations, or plot props from this book!). "
            "Tag that block clearly as `[UNRELATED STYLE SPECIMEN — RHYTHM DEMO ONLY]` so chapter drafters imitate the rhythm without ever pasting your sentences into the book!\n"
            "FICTION VOICE INTEGRITY: Favor concrete active verbs, warm sensory specificity, and natural spoken contractions for the narrator and working characters (unless custom style explicitly demands clinical/archaic voice). NEVER instruct 'use passive voice', 'avoid contractions', 'Surveyor's Report', or use banned AI words (`utilize`, `foster`).\n\n"
            "Required exact Markdown headings (replace angle-bracket placeholders with concrete guide content; NEVER copy prompt bullets literally):\n"
            "## Part 2 — Project Voice Calibration\n"
            "Chosen register: <short clean fiction register label>\n\n"
            "### Rhythm & Syntax Rules\n<3–5 concrete sentence-length variance, active-verb, contraction, and sensory-first bullets>\n\n"
            "### Unrelated Style Specimen\n[UNRELATED STYLE SPECIMEN — RHYTHM DEMO ONLY]\n<2 short paragraphs showing the target cadence on an unrelated topic>\n\n"
            "### AI-Tic Anti-Examples (Do -> Instead)\n<2 concrete before/after rewrites on unrelated dummy sentences>\n\n"
            "### Character Speech Contrasts\n<specific bullets showing how each Seed character differs in cadence, sentence length, diction, and habits — keep honest/loyal allies plainspoken and candid; allow natural contractions>\n\n"
            "### Vocabulary Wells\n<20–35 concrete sensory, trade, weather, or regional words drawn strictly from Seed and World Bible — zero dummy-specimen words like beans/colander/bicycle/tire>\n\n"
            "Output ONLY the Markdown sections above with ZERO conversational intro or outro.",
            anti_slop,
        ]
    )

    user_parts = [f"Title: {book.title}", f"Seed:\n{seed}"]
    if world:
        user_parts.append(f"World:\n{world[:4000]}")
    if chars:
        user_parts.append(f"Characters:\n{chars[:4500]}")
    if chosen_note:
        user_parts.append(chosen_note)

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(user_parts),
        task="voice",
        max_tokens=2400,
        creativity="medium",
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, "Voice guide")

    part2 = strip_llm_chatter(res.text)
    part2 = re.sub(
        r"(?im)^-\s*(?:concrete sentence-length|how each major character|20[–-]35 concrete sensory)\b[^\n]*\n?",
        "",
        part2,
    )
    part2 = re.sub(r"\bUtiliz(e|ing)\b", lambda m: "Us" + m.group(1), part2)
    part2 = re.sub(r"\butiliz(e|ing)\b", lambda m: "us" + m.group(1), part2)
    if part2.startswith("# "):
        # Avoid duplicate top-level headings
        part2 = "\n".join(part2.splitlines()[1:]).strip()
    full_doc = f"{part1}\n\n---\n\n{part2}\n"
    return full_doc
