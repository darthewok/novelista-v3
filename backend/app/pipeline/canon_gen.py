from __future__ import annotations

import re
from pathlib import Path

from app import llm_client
from app.pipeline._common import WarnCB, strip_llm_chatter, upsert_markdown_section, warn_if_cut
from app.pipeline.directives import build_directives
from app.storage import atomic_write_text, read_text
from app.store import Book

_CH_SEC_START_RE = re.compile(r"^##\s+(?:Ch(?:apter)?|Spread|Part)\.?\s*\d+\b", re.IGNORECASE)


def _norm_fact(line: str) -> str:
    return re.sub(r"[^a-z0-9 ]+", "", line.lower()).strip()


def _bullet_3grams(line: str) -> set[tuple[str, str, str]]:
    toks = [w.lower() for w in re.findall(r"[a-z0-9]+", line)]
    if len(toks) < 3:
        return set()
    return {(toks[i], toks[i + 1], toks[i + 2]) for i in range(len(toks) - 2)}


_CANON_STOPWORDS = {"the", "and", "for", "with", "was", "were", "that", "this", "from", "into", "her", "his", "she", "had", "has"}


def _bullet_content_words(line: str) -> set[str]:
    return {w.lower() for w in re.findall(r"[a-z0-9]+", line) if len(w) >= 3 and w.lower() not in _CANON_STOPWORDS}


def _split_base_and_chapters(canon_md: str) -> tuple[str, str]:
    """Preserve existing `## Ch N` sections when regenerating base canon."""
    lines = (canon_md or "").splitlines()
    for i, ln in enumerate(lines):
        if _CH_SEC_START_RE.match(ln.strip()):
            return "\n".join(lines[:i]).strip(), "\n".join(lines[i:]).strip()
    return canon_md.strip(), ""


def _strip_chapter_section(canon_md: str, header: str) -> str:
    """Remove existing `## Ch N` block BEFORE computing dedup set so revisions never wipe facts."""
    lines = (canon_md or "").splitlines()
    target = header.strip()
    out: list[str] = []
    skip = False
    for ln in lines:
        s = ln.strip()
        if s.startswith("## "):
            skip = s == target
        if not skip:
            out.append(ln)
    return "\n".join(out).strip()


async def generate(
    book: Book,
    bdir: Path,
    *,
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> str:
    directives = build_directives(book)
    seed = read_text(bdir / "seed.txt").strip() or (book.idea or "")
    world = read_text(bdir / "world.md").strip()
    chars = read_text(bdir / "characters.md").strip()
    existing_canon = read_text(bdir / "canon.md").strip()
    _, chapter_sections = _split_base_and_chapters(existing_canon)

    system = "\n\n".join(
        [
            directives,
            "Compile a crisp, authoritative Canon Ledger of DAY-1 PUBLICLY KNOWN STARTING BASELINE facts strictly grounded in Premise Seed and Cast Reference.\n"
            "Copy exact Day-1 calendar date, cast names, exact numerical ages, explicit he/him or she/her pronouns & honorifics (Ms. vs Mr.) for EVERY character, ownership/family roles (e.g. living retired parents vs deceased relatives strictly per Seed — NEVER invent deceased grandparents), material quantities, and fixed workplaces directly from Seed.\n"
            "DAY-1 BASELINE ONLY — STRICT ANTI-SPOILER LOCK: `## Timeline & Ages` and `## Hard Constraints` MUST list ONLY Day-1 publicly known starting facts! NEVER disclose Ch 2+ obstacles, future deliveries, future inspections, buyout/credit outcomes, confessions, or endings inside base `canon.md`.\n"
            "GROUNDED BASELINE PROPS ONLY: Copy EVERY Day-1 prop explicitly listed for a character in Seed or Cast Reference into `Characters & Possessions` (put `None` only if neither mentions any tool/prop). NEVER invent unmentioned heirlooms (no unrequested grandfather squares, watches, or rings).\n"
            "Output ONLY the 4 exact markdown sections (no meta-commentary or `## Timeline Contradictions` section):\n"
            "## World Rules\n## Characters & Possessions\n## Timeline & Ages\n## Hard Constraints",
        ]
    )

    parts = [f"Title: {book.title}"]
    if seed:
        parts.append(f"Premise Seed (AUTHORITATIVE GROUND TRUTH — NEVER CONTRADICT DATES, AGES, QUANTITIES, WORKPLACES, PRONOUNS, FAMILY FACTS, OR KEY OBJECTS):\n{seed[:6000]}")
    if world:
        parts.append(f"World:\n{world[:6000]}")
    if chars:
        parts.append(f"Characters:\n{chars[:6000]}")

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(parts),
        task="canon",
        max_tokens=3000,
        creativity="low",
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, "Canon ledger")
    base = strip_llm_chatter(res.text)
    if chapter_sections:
        return f"{base}\n\n{chapter_sections}\n"
    return base + "\n"


async def append_from_chapter(
    book: Book,
    bdir: Path,
    n: int,
    chapter_text: str,
    *,
    on_warning: WarnCB = None,
) -> str:
    canon = read_text(bdir / "canon.md").strip()
    header = f"## Ch {n}"

    # CRITICAL FIX: Strip `## Ch {n}` BEFORE computing `seen` so revising Ch n never drops its own facts!
    canon_without_n = _strip_chapter_section(canon, header)
    prior_bullets = [ln.lstrip("-* ").strip() for ln in canon_without_n.splitlines() if ln.strip()]
    seen = {_norm_fact(b) for b in prior_bullets}
    seen_shingles = [
        (_bullet_3grams(b), _bullet_content_words(b))
        for b in prior_bullets
        if len(b.split()) >= 6 and len(_bullet_3grams(b)) >= 4
    ]

    system = (
        "Extract ONLY new hard, verifiable continuity facts explicitly established inside CHAPTER {n} TEXT below "
        "(injuries, collateral pledges vs. sales—NEVER conflate collateral with selling—deliveries/quantities, promises, revealed facts, exact numbers/dates, location changes).\n"
        "NEVER invent unstated crimes, sales, or future events that do not appear in CHAPTER {n} TEXT, NEVER wrap bullets in backticks (`...`), and NEVER output meta-commentary.\n"
        "Output 0 to 10 plain Markdown `- ` bullet points. If nothing new became canon in this chapter, output `NONE`."
    ).replace("{n}", str(n))
    user = f"CHAPTER {n} TEXT:\n{chapter_text[:12000]}"

    res = await llm_client.complete(
        system=system,
        user=user,
        task="canon",
        max_tokens=700,
        creativity="low",
    )
    warn_if_cut(res.stop_reason, on_warning, f"Canon update (Ch {n})")

    raw_lines = [ln.strip() for ln in res.text.splitlines() if ln.strip().startswith(("-", "*"))]
    kept: list[str] = []
    for ln in raw_lines:
        clean = "- " + ln.lstrip("-* ").strip()
        body = clean[2:].strip()
        nf = _norm_fact(clean)
        if not nf or nf == "none" or nf in seen:
            continue
        sh = _bullet_3grams(body)
        cw = _bullet_content_words(body)
        if len(body.split()) >= 6 and len(sh) >= 4:
            is_near_dup = False
            for psh, pcw in seen_shingles:
                u = len(sh | psh)
                if u and (len(sh & psh) / u) >= 0.55:
                    is_near_dup = True
                    break
                if pcw and cw:
                    shared_cw = len(cw & pcw)
                    if shared_cw >= 5 and (shared_cw / min(len(cw), len(pcw))) >= 0.80:
                        is_near_dup = True
                        break
            if is_near_dup:
                continue
            seen_shingles.append((sh, cw))
        seen.add(nf)
        kept.append(clean)

    if not kept:
        # Leave existing canon unchanged (or update cleanly if chapter section existed and now has no unique facts)
        if canon_without_n != canon:
            atomic_write_text(bdir / "canon.md", canon_without_n + ("\n" if canon_without_n else ""))
        return canon_without_n

    updated = upsert_markdown_section(canon_without_n, header, "\n".join(kept[:12]))
    atomic_write_text(bdir / "canon.md", updated)
    return updated
