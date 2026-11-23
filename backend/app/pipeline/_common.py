from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Callable

from app.enums import POV, BookFormat
from app.formats import get_profile
from app.storage import read_text
from app.store import Book, chapter_path

PROMPTS_DIR = Path(__file__).resolve().parent / "prompts"

# Matches markdown headings or bold chapter headings:
#   ### Ch 1: Title
#   ## Chapter 2 — Title
#   ### Spread 3: Title
#   ### Part 1
#   **Chapter 4: Title**
UNIT_HEADING_RE = re.compile(
    r"^\s*(?:#{1,4}\s*|\*\*)(?:Ch(?:apter)?|Spread|Part|Scene)\.?\s*(\d+)\s*(?:[:\-—–.]\s*(.*?))?(?:\*\*)?\s*$",
    re.MULTILINE | re.IGNORECASE,
)

# Standalone bold/plain heading fallback ONLY inside outline parsing if markdown headings absent
_FALLBACK_HEADING_RE = re.compile(
    r"^\s*(?:Chapter|Spread|Part)\s+(\d+)\s*[:\-—–.]\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)

WarnCB = Callable[[str], None] | None


def load_prompt(name: str) -> str:
    return read_text(PROMPTS_DIR / name).strip()


def craft_prompt_for_book(book: Book) -> str:
    core = load_prompt("craft_core.md")
    prof = get_profile(book.book_format)
    spec = load_prompt(f"craft_{prof.craft_profile}.md")
    return f"{core}\n\n{spec}".strip()


def voice_or_default(bdir: Path) -> str:
    v = read_text(bdir / "voice.md").strip()
    if v:
        return v
    return load_prompt("voice_default.md")


def approx_tokens(text: str) -> int:
    return max(1, int(len(text) / 3.7))


def warn_if_cut(stop_reason: str, on_warning: WarnCB, label: str = "Output") -> bool:
    if stop_reason == "length":
        if on_warning:
            on_warning(f"{label} reached max token limit and may be truncated.")
        return True
    return False


def parse_outline_units(outline_md: str) -> list[tuple[int, str]]:
    matches = list(UNIT_HEADING_RE.finditer(outline_md or ""))
    if not matches:
        matches = list(_FALLBACK_HEADING_RE.finditer(outline_md or ""))
    seen: set[int] = set()
    out: list[tuple[int, str]] = []
    for m in matches:
        num = int(m.group(1))
        if num in seen or num <= 0:
            continue
        seen.add(num)
        raw_title = (m.group(2) or "").strip().rstrip("*").strip()
        title = raw_title or f"Chapter {num}"
        out.append((num, title))
    out.sort(key=lambda x: x[0])
    return out


def outline_block(outline_md: str, n: int) -> str:
    matches = list(UNIT_HEADING_RE.finditer(outline_md or ""))
    if not matches:
        matches = list(_FALLBACK_HEADING_RE.finditer(outline_md or ""))
    for i, m in enumerate(matches):
        if int(m.group(1)) != n:
            continue
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(outline_md)
        block = outline_md[start:end].strip()
        # Strip trailing foreshadowing ledger / notes table if this is the final unit
        for marker in ("\n## Foreshadowing", "\n### Foreshadowing", "\n## Ledger", "\n---"):
            idx = block.find(marker)
            if idx != -1 and i == len(matches) - 1:
                block = block[:idx].strip()
        return block
    return ""


def extract_plot_spine(outline_md: str) -> str:
    """Return the outline preamble / Plot Logic & Resolution Spine before the first unit heading."""
    if not (outline_md or "").strip():
        return ""
    first_unit_m = re.search(
        r"^\s*(?:#{1,4}\s*|\*\*)(?:Ch(?:apter)?|Spread|Part)\.?\s*\d+\b",
        outline_md,
        re.MULTILINE | re.IGNORECASE,
    )
    return outline_md[: first_unit_m.start()].strip() if first_unit_m else ""


def later_units_fence(outline_md: str, n: int, unit_label: str = "Ch") -> list[str]:
    matches = list(UNIT_HEADING_RE.finditer(outline_md or ""))
    lines: list[str] = []
    for i, m in enumerate(matches):
        num = int(m.group(1))
        if num <= n:
            continue
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(outline_md)
        body = outline_md[start:end].strip()
        first_bullet = ""
        for bl in body.splitlines():
            s = bl.strip().lstrip("-*• ").strip()
            if s and not s.startswith("#") and not s.startswith("|"):
                first_bullet = s[:140]
                break
        title = (m.group(2) or f"{unit_label} {num}").strip()
        lines.append(f"- {unit_label} {num}: {title}" + (f" — {first_bullet}" if first_bullet else ""))
    return lines


def next_unit_preview(outline_md: str, n: int) -> str:
    blk = outline_block(outline_md, n + 1)
    if not blk:
        return ""
    return "\n".join(blk.splitlines()[:6])


def foreshadowing_for_chapter(outline_md: str, n: int) -> list[str]:
    """Parse ALL columns (Planted, Reinforced, Paid Off) and multi-chapter cells from Foreshadowing Ledger."""
    out: list[str] = []
    in_ledger = False
    for line in (outline_md or "").splitlines():
        s = line.strip()
        low = s.lower()
        if s.startswith("#") and "foreshadow" in low:
            in_ledger = True
            continue
        if in_ledger and s.startswith("#") and "foreshadow" not in low:
            break
        if "|" not in s:
            continue
        if re.match(r"^\|\s*[-:]+\s*\|", s):
            continue
        cells = [c.strip() for c in s.strip("|").split("|")]
        if len(cells) < 2:
            continue
        header_low = cells[0].lower()
        if header_low in {"item", "thread", "clue", "plant", "seed", "foreshadowing"}:
            continue
        item = cells[0]
        if not item:
            continue
        plant_nums = {int(x) for x in re.findall(r"\b(\d+)\b", cells[1])} if len(cells) >= 2 else set()
        reinf_nums = {int(x) for x in re.findall(r"\b(\d+)\b", cells[2])} if len(cells) >= 4 else set()
        payoff_nums = {int(x) for x in re.findall(r"\b(\d+)\b", cells[-1])} if len(cells) >= 3 else set()

        roles: list[str] = []
        if n in plant_nums:
            roles.append("PLANT subtly")
        if n in reinf_nums and n not in plant_nums:
            roles.append("REINFORCE quietly")
        if n in payoff_nums:
            roles.append("PAY OFF")
        if roles:
            out.append(f"- {' / '.join(roles)}: {item}")
    return out


_POV_LINE_RE = re.compile(
    r"(?:POV(?:\s+Character)?|Viewpoint)\s*[:\-—]\s*([^\n|;]+)",
    re.IGNORECASE,
)


def pov_name(outline_blk: str, book_pov: POV | None = None) -> str | None:
    if book_pov == POV.third_omniscient:
        return None
    m = _POV_LINE_RE.search(outline_blk or "")
    if not m:
        return None
    raw = m.group(1).strip().strip("*_ ")
    # Extract parenthesized character name if formatted like "Third-person limited (Mara Vance)"
    paren = re.search(r"\(([A-Z][a-zA-Z' \-]+)\)", raw)
    if paren:
        return paren.group(1).strip()
    # Clean descriptive prefixes like "Close third — Mara"
    for sep in ("—", "–", "-"):
        if sep in raw:
            right = raw.split(sep)[-1].strip()
            if right and right[0].isupper() and "limited" not in right.lower() and "person" not in right.lower():
                raw = right
                break
    low = raw.lower()
    if any(x in low for x in ("first-person", "third-person", "omniscient", "second-person")):
        return None
    # Strip trailing qualifiers like "(close)"
    raw = re.sub(r"\s*\([^)]*\)\s*$", "", raw).strip()
    return raw[:60] if raw else None


def upsert_markdown_section(doc: str, header: str, body: str) -> str:
    """Insert or replace a `## ...` section without corrupting sibling sections."""
    lines = (doc or "").splitlines()
    target = header.strip()
    start_idx: int | None = None
    end_idx: int = len(lines)
    for idx, ln in enumerate(lines):
        if ln.strip() == target:
            start_idx = idx
            for j in range(idx + 1, len(lines)):
                if lines[j].startswith("## "):
                    end_idx = j
                    break
            break
    block_lines = [target, body.strip()]
    if start_idx is None:
        base = "\n".join(lines).rstrip()
        new_sec = "\n".join(block_lines).strip()
        return f"{base}\n\n{new_sec}\n" if base else f"{new_sec}\n"
    new_lines = lines[:start_idx] + block_lines + ([""] if end_idx < len(lines) else []) + lines[end_idx:]
    return "\n".join(new_lines).strip() + "\n"


def earlier_chapter_texts(bdir: Path, n: int) -> list[tuple[int, str]]:
    out: list[tuple[int, str]] = []
    for k in range(1, n):
        t = read_text(chapter_path(bdir, k)).strip()
        if t:
            out.append((k, t))
    return out


def parse_llm_json(raw: str) -> dict | list:
    """Recover JSON from fenced blocks, preamble/epilogue chatter, or trailing commas."""
    txt = (raw or "").strip()
    # Strip markdown fences
    fence = re.search(r"```(?:json)?\s*([\s\S]*?)```", txt, re.IGNORECASE)
    if fence:
        txt = fence.group(1).strip()

    # Locate outermost { ... } or [ ... ]
    first_obj = txt.find("{")
    first_arr = txt.find("[")
    if first_obj == -1 and first_arr == -1:
        raise json.JSONDecodeError("No JSON object or array found", txt, 0)
    if first_arr != -1 and (first_obj == -1 or first_arr < first_obj):
        start = first_arr
        end = txt.rfind("]")
    else:
        start = first_obj
        end = txt.rfind("}")
    if end <= start:
        raise json.JSONDecodeError("Unclosed JSON container", txt, start)
    cand = txt[start : end + 1]
    try:
        return json.loads(cand)
    except json.JSONDecodeError:
        # Remove trailing commas before } or ]
        repaired = re.sub(r",\s*([}\]])", r"\1", cand)
        return json.loads(repaired)


_CHATTER_EXPLICIT_RE = re.compile(
    r"^(?:Here(?:'s| is)|Below is)[^\n]{0,500}\n+(?=\S)",
    re.IGNORECASE,
)
_CHATTER_ACK_RE = re.compile(
    r"^(?:Okay|Sure|Certainly|Great|Excellent|Of course|Alright)\b([^\n]{0,500})\n+(?=\S)",
    re.IGNORECASE,
)
_CHATTER_META_CUE_RE = re.compile(
    r"(?:here(?:'s| is)|below is|draft|chapter|scene|outline|cast|character|world|voice|guide|sheet|summary|revised|revision|version|breakdown|groundwork|framework|prose|let(?:'s| me)|i(?:'ve| have| will|'ll)|as requested|:\s*$)",
    re.IGNORECASE,
)
_CHATTER_POST_PARA_RE = re.compile(
    r"(?:\n\n+|\n+---\s*\n+)[^\n]*(?:Let me know if|Would you like|Feel free to|Hope this helps|I can also|Do you want me to|please let me know|adjustments or further refinements)[^\n]*\s*$",
    re.IGNORECASE,
)
_WORD_COUNT_TAG_RE = re.compile(
    r"(?im)^\s*[\(\[]\s*(?:word\s*count\s*:\s*~?[\d,]+|~?[\d,]+\s+words?)\s*[\)\]]\s*$"
)
_PROMPT_FENCE_LEAK_RE = re.compile(
    r"(?im)(?:^|\n)\s*##\s+(?:LATER\s+(?:CHAPTERS|UNITS|SCENES|SPREADS|PARTS)|ACTIVE ASSIGNMENT|POV LOCK|PREVIOUS\s+\w+\s+OPENING|IMAGERY ALREADY USED|END OF PREVIOUS|NEXT\s+\w+\s+PREVIEW|THIS\s+\w+'S OUTLINE|PLOT\s+SPINE\s+ANCHOR|STRUCTURED CONTINUITY|EDITORIAL REVISION BRIEF|REVISION CONSTRAINTS|FINAL\s+\w+\s+CLOSURE|CHAPTER\s+END\s+NOTE|AUTHOR'?S\s+NOTE|REVISION\s+NOTES)\b[\s\S]*$"
)
_INSTRUCTION_LEAK_LINE_RE = re.compile(
    r"(?im)(?:^|(?<=\n)|(?<=[.!?]\s))[^\n]*?\b(?:Output ONLY the revised prose|keeping final word count within|NEVER copy editorial commentary|Preserve every character's exact gender|Do NOT return the draft unchanged|Instead of describing it as|Expand and deepen (?:the scenes in )?this (?:draft|scene))\b[^\n]*(?:\n|$)"
)


def is_mystery_genre(book: Book | None, seed: str = "") -> bool:
    """Return True if book genre or seed indicates crime/mystery fiction rather than drama/literary/etc."""
    genre_txt = (book.genre if book else "") or ""
    seed_txt = seed or (book.idea if book else "") or ""
    combined = f"{genre_txt}\n{seed_txt}"
    if re.search(r"\b(?:zero|no|without)\s+(?:[a-z]+,\s*)*(?:crime|murder|police|mystery)\b", seed_txt, re.IGNORECASE):
        return False
    if re.search(r"\b(?:mystery|detective|whodunit|crime|thriller|suspense|noir|sleuth|procedural)\b", genre_txt, re.IGNORECASE):
        return True
    return bool(
        re.search(
            r"\b(?:mystery|murder|homicide|drowned|detective|culprit|suspect|sabotage|autopsy|sheriff|police investigation|whodunit|arrests?|foul play|investigates?)\b",
            combined,
            re.IGNORECASE,
        )
    )


def is_american_english(book: Book | None, bdir: Path | None = None) -> bool:
    """Return True if book directives or locale context specify American English rather than British English."""
    if not book:
        return False
    seed_txt = read_text(bdir / "seed.txt") if bdir else ""
    world_txt = read_text(bdir / "world.md")[:2000] if bdir else ""
    src = f"{book.idea or ''}\n{book.custom_style or ''}\n{seed_txt}\n{world_txt}"
    has_explicit_us = bool(re.search(r"\bAmerican English\b", src, re.IGNORECASE))
    has_uk_marker = bool(
        re.search(
            r"\b(?:British English|UK English|Commonwealth English|England|Scotland|Wales|London|Yorkshire|Cornwall|Sussex|Surrey|Oxford|Cambridge|Edinburgh|Dublin|Ireland|Australia)\b",
            src,
            re.IGNORECASE,
        )
    )
    if has_uk_marker and not has_explicit_us:
        return False
    if has_explicit_us or re.search(
        r"\b(?:United States|USA|U\.S\.|Maine|Massachusetts|Oregon|California|New York|Texas|New England|Gloucester|Astoria|Pacific Northwest|backyard|porch|sidewalk|sneakers|pickup truck)\b",
        src,
        re.IGNORECASE,
    ):
        return True
    return False


def strip_llm_chatter(text: str) -> str:
    """Remove conversational chatbot preambles, sign-offs, echoed prompt fences, instruction leaks, and word-count tags."""
    out = (text or "").strip()
    for _ in range(3):
        m_exp = _CHATTER_EXPLICIT_RE.match(out)
        if m_exp:
            out = out[m_exp.end() :].strip()
            continue
        m_ack = _CHATTER_ACK_RE.match(out)
        if m_ack and _CHATTER_META_CUE_RE.search(m_ack.group(0)):
            out = out[m_ack.end() :].strip()
            continue
        break
    for _ in range(2):
        nxt = _CHATTER_POST_PARA_RE.sub("", out).strip()
        if nxt == out:
            break
        out = nxt
    out = _PROMPT_FENCE_LEAK_RE.sub("", out)
    out = _INSTRUCTION_LEAK_LINE_RE.sub("", out)
    out = _WORD_COUNT_TAG_RE.sub("", out)
    out = re.sub(r"\n{3,}", "\n\n", out).strip()
    return out
