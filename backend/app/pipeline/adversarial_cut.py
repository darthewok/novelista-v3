from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app import llm_client
from app.pipeline._common import WarnCB, foreshadowing_for_chapter, load_prompt, parse_llm_json, voice_or_default, warn_if_cut
from app.pipeline.directives import build_directives
from app.storage import read_text
from app.store import Book


def _norm_curly(s: str) -> str:
    return (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def _locate_quote(text: str, quote: str) -> tuple[int, int] | None:
    q = (quote or "").strip()
    if len(q) < 4:
        return None
    # 1. Exact
    idx = text.find(q)
    if idx != -1 and text.find(q, idx + 1) == -1:
        return idx, idx + len(q)
    # 2. Curly-quote normalized
    nt = _norm_curly(text)
    nq = _norm_curly(q)
    idx = nt.find(nq)
    if idx != -1 and nt.find(nq, idx + 1) == -1:
        return idx, idx + len(nq)
    # 3. Whitespace normalized regex
    words = [re.escape(w) for w in nq.split()]
    if len(words) >= 3:
        pat = re.compile(r"\s+".join(words))
        hits = list(pat.finditer(nt))
        if len(hits) == 1:
            return hits[0].start(), hits[0].end()
    return None


def apply_cuts(text: str, cuts: list[dict[str, Any]], *, max_percent: float = 20.0) -> tuple[str, dict[str, Any]]:
    total_words = max(1, len(text.split()))
    max_words_removed = int(total_words * (max_percent / 100.0))

    spans: list[tuple[int, int, str, str]] = []
    skipped = 0
    for item in cuts:
        quote = str(item.get("quote") or "").strip()
        if "\n\n" in quote:
            skipped += 1
            continue
        loc = _locate_quote(text, quote)
        if not loc:
            skipped += 1
            continue
        spans.append((loc[0], loc[1], str(item.get("type") or "FAT"), quote))

    # Resolve overlaps & word cap
    spans.sort(key=lambda x: x[0])
    chosen: list[tuple[int, int, str, str]] = []
    removed_words = 0
    for s0, s1, ctype, q in spans:
        if chosen and s0 < chosen[-1][1]:
            skipped += 1
            continue
        wc = len(text[s0:s1].split())
        if removed_words + wc > max_words_removed:
            skipped += 1
            continue
        chosen.append((s0, s1, ctype, q))
        removed_words += wc

    out = text
    for s0, s1, _, _ in sorted(chosen, key=lambda x: x[0], reverse=True):
        before = out[:s0]
        after = out[s1:]
        # Clean leading sentence capitalization if we deleted the start of a sentence
        prev_stripped = before.rstrip()
        at_sentence_start = not prev_stripped or prev_stripped[-1] in ".!?\n"
        if at_sentence_start and after:
            a_strip = after.lstrip(" ,;:")
            if a_strip and a_strip[0].islower():
                a_strip = a_strip[0].upper() + a_strip[1:]
            after = a_strip
        # Tidy double spaces
        out = (before.rstrip(" ") + " " + after.lstrip(" ")) if (before.endswith(" ") and after.startswith(" ")) else (before + after)

    out = re.sub(r"[ \t]{2,}", " ", out)
    out = re.sub(r" +([,.!?;:])", r"\1", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip() + ("\n" if text.endswith("\n") else "")

    return out, {
        "applied_cuts": len(chosen),
        "skipped_cuts": skipped,
        "words_removed": removed_words,
        "percent_removed": round(100.0 * removed_words / total_words, 2),
    }


async def run(
    book: Book,
    bdir: Path,
    n: int,
    chapter_text: str,
    *,
    percent: int = 10,
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> tuple[str, dict[str, Any]]:
    directives = build_directives(book)
    anti_slop = load_prompt("anti_slop.md")
    voice = voice_or_default(bdir)
    foreshadow = foreshadowing_for_chapter(read_text(bdir / "outline.md"), n)

    system = "\n\n---\n\n".join(
        [
            directives,
            f"You are an adversarial copy/line editor trimming ~{percent}% deadweight prose.\n"
            "Target ONLY: FAT (bloated throat-clearing), REDUNDANT (restates what dialogue/action just showed), "
            "OVER-EXPLAIN (explaining an emotional beat after landing it), GENERIC (filler phrases), TELL (unearned emotion summaries).\n"
            "PROTECT: Character voice quirks, sensory grounding, and every foreshadowing clue listed below.\n"
            "Return ONLY valid JSON: `{\"cuts\": [{\"quote\": \"exact verbatim substring\", \"type\": \"FAT|REDUNDANT|OVER-EXPLAIN|GENERIC|TELL\", \"reason\": \"...\"}]}`",
            anti_slop,
            f"## VOICE GUIDE\n{voice[:2000]}",
        ]
    )

    user_parts = []
    if foreshadow:
        user_parts.append("PROTECT THESE FORESHADOWING BEATS (DO NOT CUT):\n" + "\n".join(foreshadow))
    user_parts.append(f"CHAPTER TEXT:\n{chapter_text}")

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(user_parts),
        task="editor",
        max_tokens=4000,
        creativity="low",
        json_mode=True,
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, f"Tighten (Unit {n})")

    try:
        parsed = parse_llm_json(res.text)
        cuts = parsed.get("cuts") if isinstance(parsed, dict) else []
        if not isinstance(cuts, list):
            cuts = []
    except Exception as exc:
        if on_warning:
            on_warning(f"Tighten JSON parse failed: {exc}")
        return chapter_text, {"applied_cuts": 0, "skipped_cuts": 0, "words_removed": 0, "percent_removed": 0.0}

    return apply_cuts(chapter_text, cuts, max_percent=float(percent * 2))
