from __future__ import annotations

import re
from pathlib import Path

from app import llm_client
from app.formats import get_profile
from app.pipeline._common import WarnCB, strip_llm_chatter, warn_if_cut
from app.pipeline.directives import build_directives
from app.storage import read_text
from app.store import Book

_BANNED_AI_NAMES = {
    "kael", "elara", "thorne", "jax", "vance", "lyra", "elena",
    "kaelen", "alaric", "seraphina", "vesper", "nyx", "aether", "silas",
}

_SCRUB_NAME_POOLS: dict[str, list[str]] = {
    "silas": ["Caleb", "Frank", "Walter", "Howard", "Samuel"],
    "elara": ["Hannah", "Clara", "Ruth", "Miriam", "Grace"],
    "thorne": ["Mercer", "Briggs", "Kowalski", "Reid", "Caldwell"],
    "vance": ["Hollis", "Porter", "Gorman", "Brennan", "Shaw"],
    "kael": ["Owen", "Peter", "Grant", "Paul", "Simon"],
    "lyra": ["Nora", "Martha", "Alice", "Helen", "Jean"],
    "kaelen": ["Rowan", "Daniel", "Thomas", "Warren", "Cole"],
    "alaric": ["Martin", "Arthur", "Edward", "Philip", "George"],
    "seraphina": ["Beatrice", "Lydia", "Edith", "Harriet", "Nadia"],
    "vesper": ["Vera", "Louise", "Esther", "Agnes", "Maud"],
    "nyx": ["Nina", "Pearl", "Faye", "Wren", "Maeve"],
    "aether": ["Ash", "Flint", "Harbor", "Glass", "Slate"],
    "jax": ["Jack", "Roy", "Dale", "Ray", "Lou"],
    "elena": ["Claire", "Audrey", "Marian", "Laura", "Rachel"],
}


def _find_banned_names(text: str, allowed_src: str) -> list[str]:
    allowed_low = (allowed_src or "").lower()
    hits: list[str] = []
    for name in sorted(_BANNED_AI_NAMES):
        if re.search(rf"\b{name}\b", allowed_low):
            continue
        if re.search(rf"\b{name}\b", text, re.IGNORECASE):
            hits.append(name.title())
    return hits


def _pick_unused_replacement(pool: list[str], text: str, used: set[str]) -> str:
    for cand in pool:
        if cand.lower() in used:
            continue
        if not re.search(rf"\b{re.escape(cand)}\b", text, re.IGNORECASE):
            return cand
    for cand in pool:
        if cand.lower() not in used:
            return cand
    return pool[0]


def scrub_banned_names(text: str, allowed_src: str) -> str:
    """Deterministically replace any unrequested cliché AI names cleanly without colliding with existing names."""
    out = text or ""
    allowed_low = (allowed_src or "").lower()
    used: set[str] = set()
    for name, pool in _SCRUB_NAME_POOLS.items():
        if re.search(rf"\b{name}\b", allowed_low):
            continue
        if not re.search(rf"\b{name}\b", out, re.IGNORECASE):
            continue
        repl = _pick_unused_replacement(pool, out, used)
        used.add(repl.lower())
        out = re.sub(
            rf"\b{name}\b",
            lambda m, r=repl: r.upper() if m.group(0).isupper() else (r.lower() if m.group(0).islower() else r),
            out,
            flags=re.IGNORECASE,
        )
    return out


def _strip_appended_prose_chapters(text: str) -> str:
    """Truncate accidental story chapters / narrative prose appended onto a cast sheet."""
    m = re.search(
        r"\n+(?:---\s*\n+)?#{1,4}\s*(?:Chapter|Ch\.?|Scene|Part)\s+\d+\b",
        text or "",
        re.IGNORECASE,
    )
    if m:
        return (text or "")[: m.start()].rstrip()
    return (text or "").strip()


_SEED_BAD_FIRST = {
    "present", "strict", "complete", "when", "while", "after", "before", "during", "under",
    "until", "because", "american", "british", "harbor", "state", "town", "fire", "general",
    "coastal", "marine", "veteran", "retired", "young", "older", "little", "sleepy", "curious",
    "picture", "book", "chapter", "part", "spread", "october", "november", "december", "january",
    "february", "march", "april", "may", "june", "july", "august", "september", "monday",
    "tuesday", "wednesday", "thursday", "friday", "saturday", "sunday", "mr", "mrs", "ms",
    "dr", "deputy", "sheriff", "inspector", "captain", "new",
}
_SEED_BAD_SECOND = {
    "bay", "harbor", "harbour", "point", "island", "street", "road", "lane", "avenue", "dock",
    "pier", "wharf", "river", "marsh", "creek", "house", "hall", "store", "shop", "studio",
    "optics", "marine", "services", "english", "maine", "boston", "portland", "mystery",
    "commissioner", "marshal", "inspector", "deputy", "sheriff", "county", "city", "town",
    "village", "company", "corporation", "corp", "inc", "llc", "bank", "hotel", "diner",
    "church", "school", "station", "port", "cove", "beach", "hill", "valley", "forest",
    "park", "bridge", "tower", "lighthouse", "press", "times", "journal", "news", "police",
    "department", "commission", "association", "cooperative", "boatyard", "marina", "yard",
    "pov", "tense", "person", "first", "second", "third", "fourth", "fifth", "one", "two",
    "three", "four", "five", "glass", "sea", "boot", "boots", "hat", "boat", "puddle",
    "england", "standalone",
}
_SEED_BAD_PAIRS = {
    "rockport maine", "boothbay maine", "boothbay harbor", "american english", "british english",
    "new england", "state police", "fire marshal", "harbor commissioner", "town hall",
    "general store", "coastal optics", "penobscot bay", "first person", "third limited",
    "past tense", "present tense", "complete standalone", "coast guard", "pier five",
    "lind sea", "sea glass", "blackwood marine", "boothbay boatyard",
}


def _missing_seed_character_names(seed: str, cast_md: str) -> list[str]:
    """Return full character names present in Seed that were omitted or renamed in the cast sheet."""
    seen: set[str] = set()
    missing: list[str] = []
    cast_low = re.sub(r"[*_`]+", "", cast_md or "")
    for first, last in re.findall(r"\b([A-Z][a-z]{1,20})\s+([A-Z][a-z]{1,20})\b", seed or ""):
        fl = f"{first} {last}".lower()
        if fl in seen or fl in _SEED_BAD_PAIRS:
            continue
        if first.lower() in _SEED_BAD_FIRST or last.lower() in _SEED_BAD_SECOND:
            continue
        seen.add(fl)
        pat = rf"\b{re.escape(first)}\s+{re.escape(last)}\b"
        if not re.search(pat, cast_low, re.IGNORECASE):
            missing.append(f"{first} {last}")
    return missing


async def generate(
    book: Book,
    bdir: Path,
    *,
    ref_text: str = "",
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> str:
    prof = get_profile(book.book_format)
    directives = build_directives(book)
    seed = read_text(bdir / "seed.txt").strip() or book.idea
    world = read_text(bdir / "world.md").strip()

    seed_name_rule = (
        "**HARD SEED NAME, AGE, ROLE & GENDER LOCK:** Preserve EVERY character name, EXACT numerical age, single fixed workplace/role, explicit gender/pronouns (`he/him` or `she/her` in the table for EVERY named character including any law enforcement officer), and exact cause of death mentioned in the Seed 100% verbatim. "
        "NEVER rename, swap, or drop any character named in the Seed, NEVER alter character ages, and NEVER invent unrequested estranged-parent/grandfather mysteries or duplicate heirlooms not in the Seed.\n"
        "**FORMAT LOCK:** Output ONLY the Cast table and concise analytical character profiles. "
        "NEVER write story prose, narrative scenes, `## Chapter N` headings, or sample dialogue lines. Stop immediately after the last character profile."
    )

    if prof.key.value in {"picture_book", "early_reader"}:
        cast_guide = (
            "Design 2–5 memorable, visual, child-friendly characters (400–800 words total):\n"
            f"{seed_name_rule}\n"
            "## Cast Overview (Table: Name | Role | Visual Silhouette | Signature Habit)\n"
            "For each character write:\n"
            "- **Big Want & Small Fear:** concrete child-scale desire and hesitation\n"
            "- **Visual & Physical Quirks:** posture, outfit/prop, expressive gesture for illustrations\n"
            "- **Speech Rhythm:** simple, distinctive cadence (NO sample story dialogue lines that could be copied verbatim)\n"
            "- **Relationship Dynamic:** how they nudge or help the others"
        )
        max_tok = 2200
    else:
        cast_guide = (
            "Design an ensemble of distinct characters with fresh, culturally grounded names "
            "(NEVER use stock AI names like Kael, Elara, Thorne, Jax, Vance, Lyra, Elena unless the user's seed explicitly asks for them).\n"
            f"{seed_name_rule}\n"
            "Every character MUST have a completely unique first name and unique surname — NEVER reuse a first name or surname across two characters (unless explicit immediate family).\n"
            "CRITICAL: Describe HOW each character speaks analytically (sentence length, register, evasion habit, verbal blind spot) — "
            "do NOT write ready-made story dialogue lines that a drafter might copy verbatim.\n\n"
            "Structure:\n"
            "## Cast at a Glance (Markdown table: Name | Age/Role | Want | Blind Spot | Speech Cadence)\n"
            "For each key character include:\n"
            "- **External Goal vs. Inner Fear/Flaw** (keep honest/loyal allies from Seed genuinely principled and reliable — never invent shady dodging, bribe solicitation, or dark motives onto honest allies)\n"
            "- **Competence & Friction:** what they handle well vs. where pride/habit trips them\n"
            "- **Voice Contrast Rules:** sentence length, vocabulary domain, how they hedge or deflect (for deceptive characters only)\n"
            "- **Permitted Baseline Props:** list ONLY objects they naturally own or carry on Day 1 in Seed; NEVER assign future investigation clues, tools, receipts, checks, or late-chapter props to any cast sheet\n"
            "- **Relational Debts & Fractures:** specific interpersonal friction grounded strictly in Seed"
        )
        max_tok = 6500 if prof.craft_profile == "novel" else 3000

    system = "\n\n---\n\n".join(
        [
            directives,
            "You are a character architect creating living, contradictory characters whose voices and agendas clash naturally.",
            cast_guide,
        ]
    )

    user_parts = [f"Project title: {book.title}", f"Seed:\n{seed}"]
    if world:
        user_parts.append(f"World:\n{world[:8000]}")
    if ref_text.strip():
        user_parts.append(f"Reference material:\n{ref_text.strip()[:12000]}")

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(user_parts),
        task="foundation",
        max_tokens=max_tok,
        creativity="medium",
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, "Characters")
    out = _strip_appended_prose_chapters(strip_llm_chatter(res.text))

    allowed_src = f"{book.title}\n{seed}\n{ref_text}"
    bad = _find_banned_names(out, allowed_src)
    if bad:
        if on_warning:
            on_warning(f"Cast contained cliché AI name(s) {', '.join(bad)}; replacing with grounded names.")
        fix_res = await llm_client.complete(
            system="You are revising a character sheet. Replace ONLY the specified cliché AI names with ordinary, culturally authentic names fitting the setting, updating every reference consistently. Output ONLY the clean Markdown document without conversational preamble or story prose.",
            user=f"Replace these cliché names everywhere in the cast sheet: {', '.join(bad)}\n\nCAST SHEET:\n{out}",
            task="foundation",
            max_tokens=max_tok,
            creativity="low",
        )
        cleaned = _strip_appended_prose_chapters(strip_llm_chatter(fix_res.text))
        base = cleaned if cleaned else out
        if _find_banned_names(base, allowed_src):
            base = scrub_banned_names(base, allowed_src)
        out = base

    missing_names = _missing_seed_character_names(seed, out)
    if missing_names:
        if on_warning:
            on_warning(f"Cast renamed or dropped seed character(s) {', '.join(missing_names)}; restoring exact Seed names.")
        repair_res = await llm_client.complete(
            system=(
                "You are repairing a character cast sheet. The Seed names exact characters that were renamed or omitted. "
                "Restore EVERY missing Seed character name 100% verbatim throughout the table and character notes, replacing any invented substitute name consistently. "
                "Output ONLY the cast sheet Markdown — zero preamble and zero story chapters."
            ),
            user=f"SEED:\n{seed}\n\nMISSING EXACT NAMES TO RESTORE: {', '.join(missing_names)}\n\nCURRENT CAST SHEET:\n{out}",
            task="foundation",
            max_tokens=max_tok,
            creativity="low",
        )
        repaired = _strip_appended_prose_chapters(strip_llm_chatter(repair_res.text))
        if repaired and not _missing_seed_character_names(seed, repaired):
            out = scrub_banned_names(repaired, allowed_src)

    return out
