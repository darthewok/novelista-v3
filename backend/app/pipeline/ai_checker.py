from __future__ import annotations

import hashlib
import re
from collections import Counter
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from app.enums import Audience, BookFormat, POV, Tense, WritingStyle
from app.formats import AUDIENCE_GRADE_BANDS, get_profile
from app.pipeline._common import earlier_chapter_texts, load_prompt, pov_name
from app.storage import read_text
from app.store import Book, read_ignored_findings


@dataclass
class Finding:
    rule: str
    severity: str       # "error" | "warning"
    line: int
    quote: str
    message: str
    start_char: int = 0
    end_char: int = 0
    full_sentence: str = ""
    in_dialogue: bool = False
    fingerprint: str = ""

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _norm_ws(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


def _norm_quotes(s: str) -> str:
    return (s or "").replace("’", "'").replace("‘", "'").replace("“", '"').replace("”", '"')


def _make_fingerprint(rule: str, match_txt: str) -> str:
    norm = _norm_ws(_norm_quotes(match_txt)).lower()[:80]
    digest = hashlib.sha1(f"{rule}:{norm}".encode("utf-8")).hexdigest()[:12]
    return f"{rule}:{digest}"


# --- Dynamic parsing of prompts/anti_slop.md ---

def _parse_anti_slop_sections() -> dict[str, list[str]]:
    raw = load_prompt("anti_slop.md")
    sections: dict[str, list[str]] = {
        "tier1_words": [],
        "tier1_phrases": [],
        "tier1_tells": [],
        "tier2": [],
        "tier3": [],
        "cliches": [],
    }
    cur_h2 = ""
    cur_h3 = ""
    for line in raw.splitlines():
        s = line.strip()
        if s.startswith("## "):
            cur_h2 = s[3:].lower()
            cur_h3 = ""
            continue
        if s.startswith("### "):
            cur_h3 = s[4:].lower()
            continue
        if not s.startswith("- "):
            continue
        item = s[2:].strip()
        # Strip trailing parenthetical notes like " (verb)" or " (never in fiction)"
        item = re.sub(r"\s*\([^)]*\)\s*$", "", item).strip()
        if not item:
            continue
        if "tier 1" in cur_h2:
            if "phrase" in cur_h3:
                sections["tier1_phrases"].append(item)
            elif "tell" in cur_h3:
                sections["tier1_tells"].append(item)
            else:
                sections["tier1_words"].append(item)
        elif "tier 2" in cur_h2:
            sections["tier2"].append(item)
        elif "tier 3" in cur_h2:
            sections["tier3"].append(item)
        elif "cliché" in cur_h2 or "cliche" in cur_h2:
            sections["cliches"].append(item)
    return sections


def _word_inflection_pattern(word: str) -> str:
    """Build exact inflection regex handling silent -e (`utilize` -> `utilizing`) and `-y` -> `-ies`."""
    w = word.lower().strip()
    if not w.isalpha():
        return re.escape(w)
    if w.endswith("e") and not w.endswith("ee") and len(w) > 3:
        stem = re.escape(w[:-1])
        return rf"(?:{re.escape(w)}(?:s|d)?|{stem}(?:ing|ed|es|ations?)?)"
    if w.endswith("y") and len(w) > 3 and w[-2] not in "aeiou":
        stem = re.escape(w[:-1])
        return rf"(?:{re.escape(w)}|{stem}(?:ies|ied|ying))"
    return rf"{re.escape(w)}(?:s|es|d|ed|ing)?"


def _phrase_to_pattern(entry: str) -> re.Pattern[str]:
    """Convert an anti-slop phrase entry (supporting `/`, `[slot]`, `...`) into a compiled regex."""
    cleaned = entry.rstrip(".")
    tokens = cleaned.split()
    parts: list[str] = []
    for tok in tokens:
        if tok.startswith("[") and tok.endswith("]"):
            inner = tok[1:-1]
            if "/" in inner:
                alts = [re.escape(a.strip()) for a in inner.split("/") if a.strip()]
                parts.append(rf"(?:{'|'.join(alts)})")
            else:
                parts.append(r"\b[\w'-]+\b")
        elif "/" in tok:
            alts = [_word_inflection_pattern(a.strip()) for a in tok.split("/") if a.strip()]
            parts.append(rf"(?:{'|'.join(alts)})")
        elif tok.endswith(","):
            parts.append(_word_inflection_pattern(tok[:-1]) + r",?")
        else:
            parts.append(_word_inflection_pattern(tok) if len(tokens) == 1 else re.escape(tok))
    pat = r"\b" + r"\s+".join(parts) + r"\b"
    return re.compile(pat, re.IGNORECASE)


def _compile_rules() -> dict[str, list[tuple[str, re.Pattern[str]]]]:
    sec = _parse_anti_slop_sections()
    compiled: dict[str, list[tuple[str, re.Pattern[str]]]] = {}
    for key in ("tier1_words", "tier1_phrases", "tier1_tells", "tier2", "tier3", "cliches"):
        entries: list[tuple[str, re.Pattern[str]]] = []
        for raw_item in sec[key]:
            # Support entries written as "A / B" with spaces around slash
            if " / " in raw_item and not raw_item.startswith("["):
                for sub in raw_item.split(" / "):
                    s = sub.strip()
                    if s:
                        entries.append((s, _phrase_to_pattern(s)))
            else:
                entries.append((raw_item, _phrase_to_pattern(raw_item)))
        compiled[key] = entries
    return compiled


_COMPILED_LISTS = _compile_rules()

# Context exclusions preventing literal physical false positives
_PHYSICAL_WEIGHT_RE = re.compile(
    r"\bthe weight of (?:the |his |her |a |an )?(?:crate|box|bag|pack|pack's|door|iron|steel|brass|stone|rock|gold|silver|coin|ring|book|lantern|oar|bucket|anchor|axe|sword|hammer|child|body|coat|boots|snow|water|rain)\b",
    re.IGNORECASE,
)
_SENSE_LITERAL_RE = re.compile(
    r"\ba sense of (?:direction|humor|humour|balance|proportion|scale|timing|smell|taste|touch|sight|hearing)\b",
    re.IGNORECASE,
)
_REALM_GENRE_STYLE = {WritingStyle.high_epic, WritingStyle.whimsical_fable}

# --- Dialogue & Sentence Indexing Helpers ---

_QUOTE_SPAN_RE = re.compile(r'["“][^"\n”]{1,1200}["”]')
_ABBREV_SAFE_RE = re.compile(r"\b(?:Mr|Mrs|Ms|Dr|Prof|St|Sr|Jr|Lt|Capt|Sgt|Rev|Col|Gen|vs|etc|e\.g|i\.e|U\.S)\.", re.IGNORECASE)


def _dialogue_ranges(text: str) -> list[tuple[int, int]]:
    return [(m.start(), m.end()) for m in _QUOTE_SPAN_RE.finditer(text)]


def _in_ranges(start: int, end: int, ranges: list[tuple[int, int]]) -> bool:
    for r0, r1 in ranges:
        if start >= r0 and end <= r1:
            return True
        if r0 > end:
            break
    return False


def _line_number_at(text: str, pos: int) -> int:
    return text.count("\n", 0, max(0, pos)) + 1


@dataclass
class SentenceSpan:
    text: str
    start: int
    end: int
    line: int
    in_dialogue: bool
    word_count: int


def split_sentences_with_spans(text: str, d_ranges: list[tuple[int, int]] | None = None) -> list[SentenceSpan]:
    d_ranges = d_ranges if d_ranges is not None else _dialogue_ranges(text)
    spans: list[SentenceSpan] = []
    # Mask abbreviations & ellipses temporarily on equal-length string for clean offsets
    masked = text
    for m in _ABBREV_SAFE_RE.finditer(text):
        masked = masked[: m.end() - 1] + "_" + masked[m.end() :]
    masked = masked.replace("...", "___").replace("…", "_")

    n = len(masked)
    i = 0
    while i < n:
        while i < n and masked[i].isspace():
            i += 1
        if i >= n:
            break
        start = i
        while i < n:
            ch = masked[i]
            if ch == "_" and (
                (i >= 2 and masked[i - 2 : i + 1] == "___" and text[i - 2 : i + 1] == "...")
                or text[i] == "…"
            ):
                if i + 1 < n and masked[i + 1] in '"”’)\'':
                    q_end = i + 1
                    while q_end + 1 < n and masked[q_end + 1] in '"”’)\'':
                        q_end += 1
                    if re.match(r"^\s+[A-Z\"]", masked[q_end + 1 :]):
                        i = q_end + 1
                        break
            if ch in ".!?":
                # Consume trailing closing quotes/parens & multiple punctuation
                while i + 1 < n and masked[i + 1] in '.!?"”’)\'':
                    i += 1
                i += 1
                break
            if ch == "\n" and i + 1 < n and masked[i + 1] == "\n":
                break
            i += 1
        end = i
        frag = text[start:end].strip()
        if not frag or frag.startswith("#") or set(frag) <= {"-", "*", " ", "_"}:
            continue
        words = re.findall(r"[A-Za-z0-9'’-]+", frag)
        spans.append(
            SentenceSpan(
                text=frag,
                start=start,
                end=end,
                line=_line_number_at(text, start),
                in_dialogue=_in_ranges(start, end, d_ranges),
                word_count=len(words),
            )
        )
    return spans


def _sentence_for_offset(spans: list[SentenceSpan], start: int, end: int, text: str) -> tuple[int, int, str]:
    for sp in spans:
        if sp.start <= start <= sp.end:
            return sp.start, sp.end, sp.text
    # Fallback: expand to line boundaries
    ls = text.rfind("\n", 0, start) + 1
    le = text.find("\n", end)
    if le == -1:
        le = len(text)
    return ls, le, text[ls:le].strip()


# --- Syllable & Flesch-Kincaid Readability ---

def _count_syllables(word: str) -> int:
    w = re.sub(r"[^a-z]", "", word.lower())
    if not w:
        return 0
    if len(w) <= 3:
        return 1
    w = re.sub(r"(?:[^laeiouy]es|ed|[^laeiouy]e)$", "", w)
    w = re.sub(r"^y", "", w)
    vowels = re.findall(r"[aeiouy]{1,2}", w)
    return max(1, len(vowels))


def flesch_kincaid_grade(words: list[str], sentence_count: int) -> float:
    if not words or sentence_count <= 0:
        return 0.0
    syllables = sum(_count_syllables(w) for w in words)
    w_count = len(words)
    grade = 0.39 * (w_count / sentence_count) + 11.8 * (syllables / w_count) - 15.59
    return round(max(0.0, grade), 2)


# --- Individual Rule Checkers ---

_NON_LATIN_SCRIPT_RE = re.compile(r"[\u4e00-\u9fff\u3040-\u30ff\u0400-\u04ff\u0600-\u06ff\uac00-\ud7af]+")
_LATEX_RE = re.compile(
    r"\$(?!\s*\d[\d,]*(?:\.\d{1,2})?(?:\b|[^A-Za-z0-9_^\\]))[^$\n]{1,60}(?:[=^_{}\\]|[A-Za-z]\s*[+\-*/]\s*[A-Za-z0-9])[^$\n]{0,40}\$"
    r"|\\(?:frac|sqrt|alpha|beta|gamma|sum|int)\b"
)
_META_LEAK_RE = re.compile(
    r"(?:\bcamera pans?\b|\bfade to black\b|\bscreen faded\b|\bbeat sheet\b|\bthe metaphor\b|"
    r"\bplant(?:ed|ing)? (?:a|the) (?:hint|seed|clue)\b|\*Plant \d+|\bThread \d+\b|"
    r"\bAs an AI\b|\bHere is (?:Chapter|Spread|Part) \d+\b|\[Insert [^\]]+\]|\bWait—I wrote myself\b|"
    r"\bOutput ONLY the revised prose\b|\bkeeping final word count within\b|"
    r"\bNEVER copy editorial commentary\b|\bPreserve every character's exact gender\b|\bDo NOT return the draft unchanged\b|"
    r"\bInstead of describing it as\b|\bExpand and deepen (?:the scenes in )?this (?:draft|scene)\b|"
    r"^\s*##\s*(?:LATER\s+(?:CHAPTERS|UNITS|SCENES|SPREADS|PARTS)|OFF-LIMITS SCOPE FENCE|CHAPTER\s+END\s+NOTE|AUTHOR'?S\s+NOTE|REVISION\s+NOTES)\b|"
    r"\(\s*Word count\s*:\s*~?[\d,]+\s*\)|\[\s*Word count\s*:\s*~?[\d,]+\s*\]|"
    r"\(\s*~?[\d,]+\s*words?\s*\)|\[\s*~?[\d,]+\s*words?\s*\])",
    re.IGNORECASE | re.MULTILINE,
)

_NOT_JUST_BUT_RE = re.compile(
    r"(?:\bnot\s+(?:just|only|merely|simply)\s+[^.!?\n]{1,95},?\s+but\b|"
    r"\b(?:wasn't|was not|weren't|were not|hadn't|had not)\s+(?:just|only|merely|simply)\s+[^.!?\n]{1,95}(?:[;—]|\s+-\s+)\s*(?:it|he|she|they|there)\b)",
    re.IGNORECASE,
)

_NEG_CONTRAST_RE_1 = re.compile(
    r"\b(?:He|She|They|It|I|[A-Z][a-z]{1,18})\s+(?:did not|didn't|was not|wasn't|were not|weren't|had not|hadn't)\s+[^.!?\n]{2,90}[.;]\s*(?:He|She|They|It|I|[A-Z][a-z]{1,18})\b"
)
_NEG_CONTRAST_RE_2 = re.compile(
    r"(?:^|[.!?]\s+)(?:Not|No)\s+[A-Za-z][^.!?]{1,42}\.\s*(?:(?:Not|No)\s+[^.!?]{1,42}\.\s*)?(?:Just|Only|Merely|It was|He was|She was|There was|A |The )\b"
)
_NEG_CONTRAST_RE_3 = re.compile(
    r"\b(?:It|That|This)\s+(?:wasn't|was not|isn't|is not)\s+[^.!?\n]{2,60}(?:[.;—]|\s+-\s+)\s*(?:it|that|this)\s+(?:was|is)\b",
    re.IGNORECASE,
)
_NEG_CONTRAST_RE_4 = re.compile(
    r"\bless (?:like|of) [^.!?\n]{2,45} and more (?:like|of)\b",
    re.IGNORECASE,
)

_TRIAD_RE = re.compile(r"\b[A-Za-z][A-Za-z'-]{1,18},\s+[A-Za-z][A-Za-z'-]{1,18}(?:\s+[A-Za-z'-]+)?,?\s+and\s+[A-Za-z][A-Za-z'-]{1,18}\b")
_THE_WAY_RE = re.compile(r"\bthe way (?:he|she|they|it|you|[A-Z][a-z]+)\s+\w+(?:ed|s)\b", re.IGNORECASE)
_TRANSITION_OPEN_RE = re.compile(
    r"^(?:However|Meanwhile|Suddenly|Furthermore|Moreover|Nevertheless|Consequently|And yet|Still,|Yet,)\b"
)
_PARTICIPLE_OPEN_RE = re.compile(r"^[A-Z][a-z]{2,}ing\b")

_TELLING_RE = re.compile(
    r"(?:"
    r"\brealiz(?:ed|es|ing) that (?:he|she|they|I) (?:was|were|had)\b|"
    r"\bknew that (?:he|she|they|I) (?:was|were|had)\b|"
    r"\bcouldn't help but\b|"
    r"\bfelt (?:(?:a|an) )?(?:surge|wave|rush|pang|swell|jolt|flicker|stab|twist|adrenaline|panic|dread|unease|guilt|relief)(?: of| surge| rise| tighten)?\b"
    r")",
    re.IGNORECASE,
)

_EMOTION_NAME_RE = re.compile(
    r"\b(?:I|he|she|they)\s+(?:felt|was|were)\s+(?:afraid|angry|sad|happy|anxious|terrified|furious|ashamed|relieved|devastated|overwhelmed|lonely|desperate)\b",
    re.IGNORECASE,
)

_FILTER_VERB_RE = re.compile(
    r"(?:\b(?:he|she|they|I)\s+(?:felt|seemed to|noticed (?:that|a|an|something)|realized that|watched as|heard the sound of|saw that)\b|"
    r"\b(?:felt like|seemed like|appeared to|settled deep)\b)",
    re.IGNORECASE,
)

_ADVERB_DIALOGUE_TAG_RE = re.compile(
    r'["”][,.!?]?\s+(?:[A-Z][a-z]+(?:\s+[A-Z][a-z]+)?|he|she|they|I)\s+(?:said|asked|replied|whispered|muttered|shouted|snapped|murmured|declared|added|answered|observed|noted|insisted|demanded|sighed|explained|continued)\s+([a-z]+ly)\b',
    re.IGNORECASE,
)
_SAFE_LY_WORDS = {"only", "really", "finally", "barely", "simply", "actually", "early", "nearly", "hardly", "mostly", "probably"}

_VAGUE_SOMETHING_RE = re.compile(
    r"\bsomething\s+(?:deeper|larger|darker|else|sinister|complex|peculiar|amiss|wrong|unspoken|more)\b",
    re.IGNORECASE,
)
_CLICHE_AI_NAME_WORDS = {
    "kael", "elara", "thorne", "lyra", "vance", "kaelen",
    "alaric", "seraphina", "silas", "vesper", "nyx", "aether",
}

_THEME_STATED_RE = re.compile(
    r"(?:\b(?:loyalty|trust|power|love|fear|mercy|truth|hope|survival)\s+(?:is|was)\s+(?:just|only|nothing but|always)\s+a\b|"
    r"\bin this (?:city|world|town|place), (?:nothing|no one|everyone)\b)",
    re.IGNORECASE,
)

_PRESENT_DRIFT_RE = re.compile(
    r"^(?:He|She|They)\s+(?:walks|turns|looks|steps|reaches|opens|hears|sees|stands|sits|waits|smiles|nods|watches|pulls|pushes)\b"
)


def _shingles(words: list[str], k: int) -> set[tuple[str, ...]]:
    if len(words) < k:
        return set()
    return {tuple(words[i : i + k]) for i in range(len(words) - k + 1)}


def _add_finding(
    findings: list[Finding],
    ignored: set[str],
    *,
    rule: str,
    severity: str,
    line: int,
    quote: str,
    message: str,
    start_char: int = 0,
    end_char: int = 0,
    full_sentence: str = "",
    in_dialogue: bool = False,
) -> None:
    fp = _make_fingerprint(rule, full_sentence or quote or message)
    if fp in ignored:
        return
    findings.append(
        Finding(
            rule=rule,
            severity=severity,
            line=line,
            quote=quote,
            message=message,
            start_char=start_char,
            end_char=end_char,
            full_sentence=full_sentence or quote,
            in_dialogue=in_dialogue,
            fingerprint=fp,
        )
    )


def extract_surname_genders(bdir: Path | None) -> tuple[dict[str, str], list[str]]:
    """Extract unambiguous surname -> ('female'|'male') map and compact cast pronoun ledger from Seed/Cast/Canon."""
    if not bdir:
        return {}, []
    combined = "\n".join(
        read_text(bdir / name)
        for name in ("seed.txt", "characters.md", "canon.md")
    )
    if not combined.strip():
        return {}, []
    cleaned = re.sub(r"\b(?:never|not)\s+(?:Mr\.|Mrs\.|Ms\.)(?:\s+[A-Z][a-z]+)?", "", combined, flags=re.IGNORECASE)
    genders_by_surname: dict[str, set[str]] = {}
    full_labels: dict[str, str] = {}

    for m in re.finditer(r"\b([A-Z][a-z]{1,18})\s+([A-Z][a-z]{2,20})\s*\(([^)\n]{3,80})\)", cleaned):
        first, last, parens = m.group(1), m.group(2), m.group(3).lower()
        g = "female" if re.search(r"\bshe/her\b|\bfemale\b", parens) else ("male" if re.search(r"\bhe/him\b|\bmale\b", parens) else "")
        if g:
            genders_by_surname.setdefault(last.lower(), set()).add(g)
            hon = "Ms." if g == "female" else "Mr."
            pr = "she/her" if g == "female" else "he/him"
            full_labels.setdefault(last.lower(), f"{first} {last} ({pr}, {hon} {last})")

    for m in re.finditer(r"^\|\s*(?:[A-Z][a-z]+\s+)?([A-Z][a-z]{2,20})\s*\|[^|\n]{1,40}\|\s*(she/her|he/him)\b", cleaned, re.MULTILINE | re.IGNORECASE):
        last, pr = m.group(1), m.group(2).lower()
        g = "female" if "she" in pr else "male"
        genders_by_surname.setdefault(last.lower(), set()).add(g)

    for m in re.finditer(r"\b(Ms\.|Mrs\.|Mr\.)\s+([A-Z][a-z]{2,20})\b", cleaned):
        hon, last = m.group(1).lower(), m.group(2)
        g = "male" if hon == "mr." else "female"
        genders_by_surname.setdefault(last.lower(), set()).add(g)

    unambiguous = {k: next(iter(v)) for k, v in genders_by_surname.items() if len(v) == 1}
    summary = [full_labels[k] for k in unambiguous if k in full_labels]
    return unambiguous, summary


def extract_character_genders(bdir: Path | None) -> tuple[dict[str, str], dict[str, str], dict[str, str]]:
    """Return `(full_name_genders, token_genders, seed_preferred_honorifics)` from Seed/Cast/Canon."""
    if not bdir:
        return {}, {}, {}
    seed_raw = read_text(bdir / "seed.txt")
    combined = "\n".join(read_text(bdir / name) for name in ("seed.txt", "characters.md", "canon.md"))
    if not combined.strip():
        return {}, {}, {}
    cleaned = re.sub(r"\b(?:never|not)\s+(?:Mr\.|Mrs\.|Ms\.)(?:\s+[A-Z][a-z]+){0,2}", "", combined, flags=re.IGNORECASE)
    cleaned_seed = re.sub(r"\b(?:never|not)\s+(?:Mr\.|Mrs\.|Ms\.)(?:\s+[A-Z][a-z]+){0,2}", "", seed_raw, flags=re.IGNORECASE)

    full_g: dict[str, set[str]] = {}
    tok_g: dict[str, set[str]] = {}
    _skip_tokens = {
        "harbor", "bay", "point", "port", "lane", "pier", "slip", "dock", "north", "south", "east", "west",
        "county", "office", "boatyard", "marine", "deputy", "sheriff", "chief", "captain", "officer", "coast",
    }

    for m in re.finditer(r"\b([A-Z][a-z]{1,18})\s+([A-Z][a-z]{2,20})\s*\(([^)\n]{3,80})\)", cleaned):
        first, last, parens = m.group(1), m.group(2), m.group(3).lower()
        g = "female" if re.search(r"\bshe/her\b|\bfemale\b", parens) else ("male" if re.search(r"\bhe/him\b|\bmale\b", parens) else "")
        if g:
            full_g.setdefault(f"{first} {last}".lower(), set()).add(g)
            if first.lower() not in _skip_tokens and len(first) >= 3:
                tok_g.setdefault(first.lower(), set()).add(g)
            if last.lower() not in _skip_tokens:
                tok_g.setdefault(last.lower(), set()).add(g)

    for m in re.finditer(r"^\|\s*(?:([A-Z][a-z]{1,18})\s+)?([A-Z][a-z]{2,20})\s*\|[^|\n]{1,40}\|\s*(she/her|he/him)\b", cleaned, re.MULTILINE | re.IGNORECASE):
        first, last, pr = (m.group(1) or ""), m.group(2), m.group(3).lower()
        g = "female" if "she" in pr else "male"
        if first:
            full_g.setdefault(f"{first} {last}".lower(), set()).add(g)
            if first.lower() not in _skip_tokens and len(first) >= 3:
                tok_g.setdefault(first.lower(), set()).add(g)
        if last.lower() not in _skip_tokens:
            tok_g.setdefault(last.lower(), set()).add(g)

    for m in re.finditer(r"\b(Ms\.|Mrs\.|Mr\.)\s+(?:[A-Z][a-z]{1,18}\s+)?([A-Z][a-z]{2,20})\b", cleaned):
        hon, last = m.group(1).lower(), m.group(2)
        g = "male" if hon == "mr." else "female"
        if last.lower() not in _skip_tokens:
            tok_g.setdefault(last.lower(), set()).add(g)

    seed_hon_sets: dict[str, set[str]] = {}
    for m in re.finditer(r"\b(Ms\.|Mrs\.|Mr\.)\s+(?:[A-Z][a-z]{1,18}\s+)?([A-Z][a-z]{2,20})\b", cleaned_seed):
        seed_hon_sets.setdefault(m.group(2).lower(), set()).add(m.group(1))

    unambig_surnames, _ = extract_surname_genders(bdir)
    return (
        {k: next(iter(v)) for k, v in full_g.items() if len(v) == 1},
        {k: next(iter(v)) for k, v in tok_g.items() if len(v) == 1},
        {k: next(iter(v)) for k, v in seed_hon_sets.items() if len(v) == 1 and k in unambig_surnames},
    )


def extract_deceased_surnames(bdir: Path | None) -> set[str]:
    """Return lowercase first/last names of explicitly deceased victims so span rewrites never resurrect them."""
    if not bdir:
        return set()
    dead: set[str] = set()
    combined = "\n".join(read_text(bdir / fn) for fn in ("seed.txt", "characters.md", "canon.md"))
    for ln in combined.splitlines():
        if not re.search(r"\b(?:deceased|drowned|victim|killed|fatality|murdered|late\s+[A-Z])\b", ln, re.IGNORECASE):
            continue
        for m in re.finditer(r"\b([A-Z][a-z]{1,18})\s+([A-Z][a-z]{2,20})\s*\([^)\n]{0,50}(?:deceased|drown|age\s*\d+|he/him|she/her)", ln, re.IGNORECASE):
            if re.search(r"(?:fatality|deceased|drowned|victim|killed|murdered|late\b)[^.\n]{0,60}" + re.escape(m.group(0)[:12]), ln, re.IGNORECASE) or "deceased" in m.group(0).lower() or re.search(rf"{re.escape(m.group(1))}\s+{re.escape(m.group(2))}[^.\n]{{0,80}}\b(?:deceased|drowned|died)\b", ln, re.IGNORECASE):
                dead.add(m.group(1).lower())
                dead.add(m.group(2).lower())
        for m in re.finditer(r"^\|\s*([A-Z][a-z]{1,18})\s+([A-Z][a-z]{2,20})\s*\(Deceased\)\s*\|", ln, re.IGNORECASE):
            dead.add(m.group(1).lower())
            dead.add(m.group(2).lower())
    return dead


_GENERIC_CLUE_WORDS = {"demeanor", "rigidity", "nervous", "presence", "anxiety", "tension", "attitude", "behavior", "loyalty"}


def future_foreshadowing_items(outline_md: str, n: int) -> list[tuple[str, int]]:
    """Return `(item_label, planted_ch)` pairs from `## Foreshadowing Ledger` whose `Planted (Ch) > n`."""
    if not (outline_md or "").strip() or n <= 0:
        return []
    m = re.search(r"^##\s+Foreshadowing Ledger\s*$([\s\S]*?)(?:^##\s+|\Z)", outline_md, re.MULTILINE | re.IGNORECASE)
    if not m:
        return []
    out: list[tuple[str, int]] = []
    for ln in m.group(1).splitlines():
        s = ln.strip()
        if not s.startswith("|") or re.match(r"^\|\s*[-:]+\s*\|", s):
            continue
        cols = [c.strip() for c in s.strip("|").split("|")]
        if len(cols) < 2:
            continue
        item = re.sub(r"[*_`]", "", cols[0]).strip()
        if not item or item.lower().startswith("item"):
            continue
        pm = re.search(r"\b(\d+)\b", cols[1])
        if not pm:
            continue
        planted_ch = int(pm.group(1))
        if planted_ch > n:
            out.append((item, planted_ch))
    return out


def scan(
    text: str,
    *,
    book: Book | None = None,
    bdir: Path | None = None,
    chapter_num: int | None = None,
) -> dict[str, Any]:
    raw = text or ""
    norm = _norm_quotes(raw)
    prof = get_profile(book.book_format) if book else get_profile(BookFormat.novel)
    ignored = read_ignored_findings(bdir, chapter_num) if (bdir and chapter_num) else set()
    d_ranges = _dialogue_ranges(raw)
    spans = split_sentences_with_spans(raw, d_ranges)
    findings: list[Finding] = []

    words = re.findall(r"[A-Za-z0-9'’-]+", raw)
    word_count = len(words)
    bytes_per_word = round(len(raw.encode("utf-8")) / max(1, word_count), 2) if word_count else 0.0

    # --- 1. Token Meltdown & Degeneration (hard error) ---
    tokens = raw.split()
    long_tokens = [t for t in tokens if len(t) > 40 and not t.startswith("http")]
    if long_tokens:
        pos = raw.find(long_tokens[0])
        _add_finding(
            findings,
            ignored,
            rule="token_meltdown",
            severity="error",
            line=_line_number_at(raw, max(0, pos)),
            quote=long_tokens[0][:120],
            message=f"Degenerated unspaced token ({len(long_tokens[0])} chars) — model sampling meltdown.",
            start_char=max(0, pos),
            end_char=max(0, pos) + len(long_tokens[0]),
        )
    if word_count >= 50 and bytes_per_word > 9.5:
        _add_finding(
            findings,
            ignored,
            rule="token_meltdown",
            severity="error",
            line=1,
            quote="",
            message=f"Extreme bytes/word ratio ({bytes_per_word}) indicates token corruption.",
        )
    # Word repetition spiral inside 10-word window
    low_words = [w.lower() for w in words]
    for idx in range(0, max(0, len(low_words) - 8)):
        win = low_words[idx : idx + 8]
        most_w, cnt = Counter(win).most_common(1)[0]
        if cnt >= 4 and len(most_w) >= 4:
            pos = raw.lower().find(most_w)
            _add_finding(
                findings,
                ignored,
                rule="token_meltdown",
                severity="error",
                line=_line_number_at(raw, max(0, pos)),
                quote=" ".join(words[idx : idx + 8]),
                message=f"Word '{most_w}' loops {cnt}x inside 8 words.",
                start_char=max(0, pos),
                end_char=max(0, pos) + len(most_w),
            )
            break

    # --- 2. Script Leak (CJK/Cyrillic/Arabic in Latin-language books) ---
    lang = (book.language if book else "English").lower()
    if any(x in lang for x in ("english", "spanish", "french", "german", "italian", "portuguese", "dutch")):
        for m in list(_NON_LATIN_SCRIPT_RE.finditer(raw))[:5]:
            s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
            _add_finding(
                findings,
                ignored,
                rule="script_leak",
                severity="error",
                line=_line_number_at(raw, m.start()),
                quote=m.group(0),
                message=f"Unexpected non-Latin script characters '{m.group(0)}' leaked into {book.language if book else 'English'} prose.",
                start_char=s0,
                end_char=s1,
                full_sentence=sent,
            )

    # --- 3. Meta Leaks & Formatting Artefacts (hard error) ---
    for m in list(_META_LEAK_RE.finditer(raw))[:5]:
        s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
        _add_finding(
            findings,
            ignored,
            rule="meta_leak",
            severity="error",
            line=_line_number_at(raw, m.start()),
            quote=m.group(0),
            message=f"Outline/LLM/screenplay meta-text leaked into prose: '{m.group(0)}'.",
            start_char=s0,
            end_char=s1,
            full_sentence=sent,
        )
    for m in list(_LATEX_RE.finditer(raw))[:3]:
        s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
        _add_finding(
            findings,
            ignored,
            rule="artefact",
            severity="error",
            line=_line_number_at(raw, m.start()),
            quote=m.group(0),
            message="LaTeX math formatting found inside story prose.",
            start_char=s0,
            end_char=s1,
            full_sentence=sent,
        )
    # Markdown bold inside prose body (ignore header lines)
    for ln_i, ln in enumerate(raw.splitlines(), 1):
        if ln.strip().startswith("#"):
            continue
        bm = re.search(r"\*\*[^*\n]{2,60}\*\*", ln)
        if bm:
            pos = raw.find(bm.group(0))
            s0, s1, sent = _sentence_for_offset(spans, max(0, pos), max(0, pos) + len(bm.group(0)), raw)
            _add_finding(
                findings,
                ignored,
                rule="artefact",
                severity="error",
                line=ln_i,
                quote=bm.group(0),
                message="Markdown bold (**...**) leaked into story prose.",
                start_char=s0,
                end_char=s1,
                full_sentence=sent,
            )
            break

    # --- 4. Word Count ---
    min_w = prof.min_unit_words
    if word_count < min_w:
        _add_finding(
            findings,
            ignored,
            rule="word_count",
            severity="error",
            line=1,
            quote="",
            message=f"Only {word_count} words (minimum for {prof.label} {prof.unit_label.lower()} is {min_w}).",
        )
    elif book and book.target_words:
        cnt = book.chapter_count or len(book.chapters) or prof.default_units
        target_u = book.target_words / max(1, cnt)
        if word_count < target_u * 0.55 or word_count > target_u * 1.65:
            _add_finding(
                findings,
                ignored,
                rule="word_count",
                severity="warning",
                line=1,
                quote="",
                message=f"Length ({word_count:,} words) is outside target band (~{int(target_u):,} words).",
            )

    # --- 5. Paragraph Loops (exact + near-duplicate Jaccard) ---
    paras = [p.strip() for p in re.split(r"\n\s*\n", raw) if p.strip() and not p.strip().startswith("#")]
    norm_paras = [_norm_ws(p).lower() for p in paras]
    unique_paras = len(set(norm_paras))
    unique_ratio = round(unique_paras / max(1, len(norm_paras)), 3) if norm_paras else 1.0

    # Exact duplicate count
    para_counts = Counter(norm_paras)
    for np_txt, cnt in para_counts.items():
        if cnt >= 2 and len(np_txt.split()) >= 15:
            pos = raw.lower().find(np_txt[:40])
            _add_finding(
                findings,
                ignored,
                rule="paragraph_loop",
                severity="error",
                line=_line_number_at(raw, max(0, pos)),
                quote=np_txt[:120],
                message=f"Paragraph repeated verbatim {cnt} times.",
                start_char=max(0, pos),
                end_char=max(0, pos) + len(np_txt),
                full_sentence=np_txt[:300],
            )
            break
    if len(norm_paras) >= 8 and unique_ratio < 0.88:
        _add_finding(
            findings,
            ignored,
            rule="paragraph_loop",
            severity="error",
            line=1,
            quote="",
            message=f"Low unique-paragraph ratio ({unique_ratio:.2f}; {unique_paras}/{len(norm_paras)} unique).",
        )
    else:
        # Near-duplicate paragraph check via 3-gram Jaccard
        para_shingles = [_shingles(re.findall(r"\w+", p), 3) for p in norm_paras]
        for i in range(len(para_shingles)):
            if len(para_shingles[i]) < 12:
                continue
            for j in range(i + 1, len(para_shingles)):
                if len(para_shingles[j]) < 12:
                    continue
                inter = len(para_shingles[i] & para_shingles[j])
                union = len(para_shingles[i] | para_shingles[j])
                if union and (inter / union) >= 0.72:
                    pos = raw.find(paras[j])
                    _add_finding(
                        findings,
                        ignored,
                        rule="paragraph_loop",
                        severity="error",
                        line=_line_number_at(raw, max(0, pos)),
                        quote=paras[j][:120],
                        message=f"Paragraph {j + 1} is a near-duplicate loop of paragraph {i + 1} ({int(100 * inter / union)}% overlap).",
                        start_char=max(0, pos) if pos != -1 else 0,
                        end_char=(pos + len(paras[j])) if pos != -1 else 0,
                        full_sentence=paras[j][:400],
                    )
                    break

    # --- 6. Copied Text (in-chapter & cross-chapter 10-word runs, skipping heading lines) ---
    header_ranges = [
        (hm.start(), hm.end())
        for hm in re.finditer(r"(?im)^[ \t]*#{1,4}\s*(?:Ch(?:apter)?|Spread|Part)\.?\s*\d+[^\n]*$", raw)
    ]
    word_matches = [
        m for m in re.finditer(r"[A-Za-z0-9'’-]+", raw) if not _in_ranges(m.start(), m.end(), header_ranges)
    ]
    w_lows = [m.group(0).lower() for m in word_matches]
    seen_10: dict[tuple[str, ...], int] = {}
    dup_hits = 0
    idx = 0
    while idx <= len(w_lows) - 10:
        gram = tuple(w_lows[idx : idx + 10])
        if gram in seen_10:
            prev_idx = seen_10[gram]
            if idx - prev_idx >= 12 and dup_hits < 4:
                s_off = word_matches[idx].start()
                e_off = word_matches[idx + 9].end()
                _add_finding(
                    findings,
                    ignored,
                    rule="copied_text",
                    severity="error",
                    line=_line_number_at(raw, s_off),
                    quote=raw[s_off:e_off],
                    message="10-word passage duplicated within this chapter.",
                    start_char=s_off,
                    end_char=e_off,
                    full_sentence=raw[s_off:e_off],
                )
                dup_hits += 1
                idx += 10
                continue
        else:
            seen_10[gram] = idx
        idx += 1

    if bdir and chapter_num and chapter_num > 1:
        prior_grams: set[tuple[str, ...]] = set()
        for _, ptxt in earlier_chapter_texts(bdir, chapter_num):
            pw = [w.lower() for w in re.findall(r"[A-Za-z0-9'’-]+", ptxt)]
            for k in range(0, max(0, len(pw) - 9)):
                prior_grams.add(tuple(pw[k : k + 10]))
        cross_hits = 0
        c_idx = 0
        while c_idx <= len(w_lows) - 10:
            if tuple(w_lows[c_idx : c_idx + 10]) in prior_grams:
                s_off = word_matches[c_idx].start()
                e_off = word_matches[c_idx + 9].end()
                s0, s1, sent = _sentence_for_offset(spans, s_off, e_off, raw)
                _add_finding(
                    findings,
                    ignored,
                    rule="copied_text",
                    severity="error",
                    line=_line_number_at(raw, s_off),
                    quote=raw[s_off:e_off],
                    message="10-word passage copied verbatim from an earlier chapter.",
                    start_char=s0,
                    end_char=s1,
                    full_sentence=sent,
                )
                cross_hits += 1
                if cross_hits >= 4:
                    break
                c_idx += 10
            else:
                c_idx += 1

    # --- 7. Exemplar / Spec-File Copy (7w vs voice specimens; 10w vs planning files) ---
    if bdir:
        voice_7grams: set[tuple[str, ...]] = set()
        voice_txt = read_text(bdir / "voice.md")
        if voice_txt:
            vw = [w.lower() for w in re.findall(r"[A-Za-z0-9'’-]+", voice_txt)]
            for k in range(0, max(0, len(vw) - 6)):
                voice_7grams.add(tuple(vw[k : k + 7]))

        plan_10grams: set[tuple[str, ...]] = set()
        plan_files = [bdir / "characters.md", bdir / "outline.md"]
        if chapter_num:
            from app.store import scene_path as _sp
            plan_files.append(_sp(bdir, chapter_num))
        for fp in plan_files:
            ftxt = read_text(fp)
            if ftxt:
                fw = [w.lower() for w in re.findall(r"[A-Za-z0-9'’-]+", ftxt)]
                for k in range(0, max(0, len(fw) - 9)):
                    plan_10grams.add(tuple(fw[k : k + 10]))

        ex_hits = 0
        idx = 0
        while idx < len(w_lows) and ex_hits < 5:
            matched_len = 0
            if idx <= len(w_lows) - 10 and tuple(w_lows[idx : idx + 10]) in plan_10grams:
                matched_len = 10
            elif idx <= len(w_lows) - 7 and tuple(w_lows[idx : idx + 7]) in voice_7grams:
                matched_len = 7
            if matched_len:
                s_off = word_matches[idx].start()
                e_off = word_matches[idx + matched_len - 1].end()
                s0, s1, sent = _sentence_for_offset(spans, s_off, e_off, raw)
                _add_finding(
                    findings,
                    ignored,
                    rule="exemplar_copy",
                    severity="error",
                    line=_line_number_at(raw, s_off),
                    quote=raw[s_off:e_off],
                    message="Copied wording verbatim from Voice Guide, Cast Sheet, Outline, or Scene Cards.",
                    start_char=s0,
                    end_char=s1,
                    full_sentence=sent,
                )
                ex_hits += 1
                idx += matched_len
            else:
                idx += 1

    # --- 8. Tier 1 Slop (Words + Phrases + Fiction Tells, with smart exclusions) ---
    for group_key in ("tier1_words", "tier1_phrases", "tier1_tells"):
        for label, pat in _COMPILED_LISTS[group_key]:
            if label == "realm" and book and book.writing_style in _REALM_GENRE_STYLE:
                continue
            for m in pat.finditer(norm):
                s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
                in_dlg = _in_ranges(m.start(), m.end(), d_ranges)
                if label.startswith("the weight of") and _PHYSICAL_WEIGHT_RE.search(norm[max(0, m.start() - 4) : m.end() + 45]):
                    continue
                if label.startswith("a sense of") and _SENSE_LITERAL_RE.search(norm[max(0, m.start() - 2) : m.end() + 30]):
                    continue
                _add_finding(
                    findings,
                    ignored,
                    rule="tier1_slop",
                    severity="error",
                    line=_line_number_at(raw, m.start()),
                    quote=raw[m.start() : m.end()],
                    message=f"Banned AI slop ({label}). Rewrite with concrete physical detail.",
                    start_char=s0,
                    end_char=s1,
                    full_sentence=sent,
                    in_dialogue=in_dlg,
                )

    # --- 9. Not just X, but Y ---
    njb_hits = list(_NOT_JUST_BUT_RE.finditer(norm))
    njb_sev = "error" if len(njb_hits) >= 2 else "warning"
    for m in njb_hits[:8]:
        s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
        _add_finding(
            findings,
            ignored,
            rule="not_just_but",
            severity=njb_sev,
            line=_line_number_at(raw, m.start()),
            quote=m.group(0),
            message="Negative-antithesis crutch ('not just/only X, but Y'). State directly instead.",
            start_char=s0,
            end_char=s1,
            full_sentence=sent,
            in_dialogue=_in_ranges(m.start(), m.end(), d_ranges),
        )

    # --- 10. Full Negation-Contrast Family ---
    neg_matches: list[re.Match[str]] = []
    for pat in (_NEG_CONTRAST_RE_1, _NEG_CONTRAST_RE_2, _NEG_CONTRAST_RE_3, _NEG_CONTRAST_RE_4):
        neg_matches.extend(pat.finditer(norm))
    if len(neg_matches) >= 1:
        for m in neg_matches[:6]:
            s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
            _add_finding(
                findings,
                ignored,
                rule="negation_contrast",
                severity="warning",
                line=_line_number_at(raw, m.start()),
                quote=_norm_ws(m.group(0))[:120],
                message="AI negation-contrast framing ('Not X. Y.' / 'It wasn't X; it was Y.'). Show the positive action instead.",
                start_char=s0,
                end_char=s1,
                full_sentence=sent,
                in_dialogue=_in_ranges(m.start(), m.end(), d_ranges),
            )

    # --- 11. Tier 2 Repeats & Tier 3 Fillers & Modern Fiction Clichés ---
    proper_tokens: set[str] = set()
    if book:
        proper_tokens |= {w.lower() for w in re.findall(r"\b[A-Z][a-z]{2,}\b", book.title or "")}
    if bdir:
        proper_tokens |= {w.lower() for w in re.findall(r"\b[A-Z][a-z]{2,}\b", read_text(bdir / "characters.md"))}

    for label, pat in _COMPILED_LISTS["tier2"]:
        hits: list[re.Match[str]] = []
        for m in pat.finditer(norm):
            surface = raw[m.start() : m.end()]
            if surface[:1].isupper():
                before_ctx = raw[max(0, m.start() - 32) : m.start()]
                after_ctx = raw[m.end() : min(len(raw), m.end() + 32)]
                is_proper = (
                    label.lower() in proper_tokens
                    or bool(re.search(r"(?:\b[A-Z][a-z]{1,18}|Mr\.|Ms\.|Mrs\.|Dr\.)\s+$", before_ctx))
                    or bool(re.match(r"^(?:'s\b|\s+[A-Z][a-z]{2,}\b)", after_ctx))
                )
                if is_proper:
                    continue
            hits.append(m)
        if len(hits) > 1:
            for m in hits[1:5]:
                s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
                _add_finding(
                    findings,
                    ignored,
                    rule="tier2_repeat",
                    severity="warning",
                    line=_line_number_at(raw, m.start()),
                    quote=m.group(0),
                    message=f"Restricted AI-favorite word '{label}' used {len(hits)}x (max 1 per chapter).",
                    start_char=s0,
                    end_char=s1,
                    full_sentence=sent,
                )

    from app.pipeline.slop_fix import is_adjacent_paraphrase

    adj_echo_hits = 0
    for s_idx in range(1, len(spans)):
        sp_prev, sp_cur = spans[s_idx - 1], spans[s_idx]
        if "\n\n" in raw[sp_prev.end : sp_cur.start]:
            continue
        if not sp_prev.in_dialogue and not sp_cur.in_dialogue and is_adjacent_paraphrase(sp_prev.text, sp_cur.text):
            adj_echo_hits += 1
            if adj_echo_hits <= 4:
                _add_finding(
                    findings,
                    ignored,
                    rule="adjacent_echo",
                    severity="warning",
                    line=sp_cur.line,
                    quote=sp_cur.text[:120],
                    message="Adjacent sentence paraphrases the preceding beat; merge or cut redundancy.",
                    start_char=sp_cur.start,
                    end_char=sp_cur.end,
                    full_sentence=sp_cur.text,
                )

    for label, pat in _COMPILED_LISTS["tier3"]:
        for m in pat.finditer(norm):
            if _in_ranges(m.start(), m.end(), d_ranges):
                continue
            s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
            _add_finding(
                findings,
                ignored,
                rule="tier3_filler",
                severity="warning",
                line=_line_number_at(raw, m.start()),
                quote=m.group(0),
                message=f"Essayistic filler phrase ('{label}') inside fiction narration.",
                start_char=s0,
                end_char=s1,
                full_sentence=sent,
            )

    seen_cliche_spans: set[tuple[int, int]] = set()
    for label, pat in _COMPILED_LISTS["cliches"]:
        for m in pat.finditer(norm):
            if label.startswith("the weight of") and _PHYSICAL_WEIGHT_RE.search(
                norm[max(0, m.start() - 4) : m.end() + 45]
            ):
                continue
            s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
            if (s0, s1) in seen_cliche_spans:
                continue
            seen_cliche_spans.add((s0, s1))
            _add_finding(
                findings,
                ignored,
                rule="modern_fiction_cliche",
                severity="warning",
                line=_line_number_at(raw, m.start()),
                quote=m.group(0),
                message=f"Overused thriller/AI body cliché ('{label}'). Replace with a fresher gesture.",
                start_char=s0,
                end_char=s1,
                full_sentence=sent,
                in_dialogue=_in_ranges(m.start(), m.end(), d_ranges),
            )

    # --- 12. Punctuation Densities & Consistency ---
    raw_em_dashes = raw.count("—")
    if raw_em_dashes > 0:
        em_spans = [sp for sp in spans if "—" in sp.text]
        for sp in em_spans[:12]:
            _add_finding(
                findings,
                ignored,
                rule="em_dash_forbidden",
                severity="warning",
                line=sp.line,
                quote=sp.text[:120],
                message="Em-dash ('—') detected (AI tell); replace with '-' (run Polish or Fix Tells to auto-clean).",
                start_char=sp.start,
                end_char=sp.end,
                full_sentence=sp.text,
                in_dialogue=sp.in_dialogue,
            )
        if not em_spans:
            first_em_line = next((idx for idx, ln in enumerate(raw.splitlines(), 1) if "—" in ln), 1)
            _add_finding(
                findings,
                ignored,
                rule="em_dash_forbidden",
                severity="warning",
                line=first_em_line,
                quote="—",
                message="Em-dash ('—') detected (AI tell); replace with '-' (run Polish or Fix Tells to auto-clean).",
            )
    spaced_dashes = len(re.findall(r"(?<=[\w\"”'’])\s+[–-]\s+(?=[*A-Za-z\"“'‘])", raw))
    em_dashes = raw_em_dashes + len(re.findall(r"(?<!-)--(?!-)", raw)) + spaced_dashes
    em_per_150 = round(em_dashes / max(1.0, word_count / 150.0), 2)
    if word_count >= 150 and em_per_150 > 1.2:
        _add_finding(
            findings,
            ignored,
            rule="em_dash_density",
            severity="warning",
            line=1,
            quote="—",
            message=f"Dash density is {em_per_150}/150w ({em_dashes} total; target <= 1.0/150w).",
        )
        allowed_dash_sents = max(2, word_count // 300)
        dash_sents = [sp for sp in spans if ("—" in sp.text or " -- " in sp.text or " – " in sp.text or " - " in sp.text)]
        for sp in dash_sents[allowed_dash_sents : allowed_dash_sents + 6]:
            _add_finding(
                findings,
                ignored,
                rule="em_dash_overuse",
                severity="warning",
                line=sp.line,
                quote=sp.text[:120],
                message="Excess appositive/interruptive dash; tighten with crisp periods or commas.",
                start_char=sp.start,
                end_char=sp.end,
                full_sentence=sp.text,
                in_dialogue=sp.in_dialogue,
            )
    if ('"' in raw and ('“' in raw or '”' in raw)) or ("'" in raw and ("‘" in raw or "’" in raw)):
        _add_finding(
            findings,
            ignored,
            rule="mixed_quotes",
            severity="warning",
            line=1,
            quote="",
            message="Mixed straight and curly quotation marks detected; save or polish to normalize.",
        )

    # Vague 'something + abstract adj' & cliché AI names
    vague_hits = list(_VAGUE_SOMETHING_RE.finditer(norm))
    if len(vague_hits) >= 2:
        for m in vague_hits[:4]:
            if _in_ranges(m.start(), m.end(), d_ranges):
                continue
            s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
            _add_finding(
                findings,
                ignored,
                rule="vague_something",
                severity="warning",
                line=_line_number_at(raw, m.start()),
                quote=m.group(0),
                message=f"Vague ominous filler ('{m.group(0)}'). Name the exact concrete object or suspicion.",
                start_char=s0,
                end_char=s1,
                full_sentence=sent,
            )

    if book:
        seed_low = f"{book.title}\n{book.idea or ''}".lower()
        for cname in sorted(_CLICHE_AI_NAME_WORDS):
            if cname in seed_low:
                continue
            cm = re.search(rf"\b{cname}\b", raw, re.IGNORECASE)
            if cm:
                _add_finding(
                    findings,
                    ignored,
                    rule="cliche_ai_name",
                    severity="warning",
                    line=_line_number_at(raw, cm.start()),
                    quote=cm.group(0),
                    message=f"Overused AI default character name '{cm.group(0)}' not requested in your seed.",
                )

    semicolons = raw.count(";")
    if word_count >= 300 and (semicolons / max(1.0, word_count / 300.0)) > 2.2:
        _add_finding(
            findings,
            ignored,
            rule="semicolon_density",
            severity="warning",
            line=1,
            quote=";",
            message=f"Heavy semicolon density ({semicolons} semicolons in {word_count} words).",
        )

    ellipses = len(re.findall(r"\.\.\.|…", raw))
    if word_count >= 300 and ellipses > max(6, word_count // 200):
        _add_finding(
            findings,
            ignored,
            rule="ellipsis_spam",
            severity="warning",
            line=1,
            quote="...",
            message=f"Frequent ellipses ({ellipses}x) can read melodramatic.",
        )

    # --- 13. Narration Stylometry (Rhythm, Staccato Fragments, Openers, Filters) ---
    narr_spans = [sp for sp in spans if not sp.in_dialogue and sp.word_count >= 1]
    sent_lens = [sp.word_count for sp in narr_spans]
    sent_cv = 0.0
    if len(sent_lens) >= 12:
        mean_l = sum(sent_lens) / len(sent_lens)
        var_l = sum((x - mean_l) ** 2 for x in sent_lens) / len(sent_lens)
        sent_cv = round((var_l**0.5) / max(0.1, mean_l), 3)
        if len(sent_lens) >= 25 and sent_cv < 0.42:
            _add_finding(
                findings,
                ignored,
                rule="sentence_rhythm",
                severity="warning",
                line=1,
                quote="",
                message=f"Metronomic narration sentence lengths (CV={sent_cv:.2f}; vary short and flowing clauses).",
            )

    # Staccato fragment runs (3+ consecutive <=4-word narration sentences)
    if prof.craft_profile != "children" and len(narr_spans) >= 5:
        run_start: int | None = None
        run_len = 0
        for idx, sp in enumerate(narr_spans):
            if sp.word_count <= 4:
                if run_start is None:
                    run_start = idx
                run_len += 1
            else:
                if run_len >= 3 and run_start is not None:
                    first_sp = narr_spans[run_start]
                    last_sp = narr_spans[idx - 1]
                    _add_finding(
                        findings,
                        ignored,
                        rule="staccato_fragments",
                        severity="warning",
                        line=first_sp.line,
                        quote=raw[first_sp.start : last_sp.end][:120],
                        message=f"Staccato fragment cluster ({run_len} ultra-short sentences in a row). Smooth into varied prose.",
                        start_char=first_sp.start,
                        end_char=last_sp.end,
                        full_sentence=raw[first_sp.start : last_sp.end],
                    )
                run_start = None
                run_len = 0
        if run_len >= 3 and run_start is not None:
            first_sp = narr_spans[run_start]
            last_sp = narr_spans[-1]
            _add_finding(
                findings,
                ignored,
                rule="staccato_fragments",
                severity="warning",
                line=first_sp.line,
                quote=raw[first_sp.start : last_sp.end][:120],
                message=f"Staccato fragment cluster ({run_len} ultra-short sentences in a row). Smooth into varied prose.",
                start_char=first_sp.start,
                end_char=last_sp.end,
                full_sentence=raw[first_sp.start : last_sp.end],
            )
        short_ratio = sum(1 for sp in narr_spans if sp.word_count <= 4) / len(narr_spans)
        if len(narr_spans) >= 20 and short_ratio > 0.24:
            _add_finding(
                findings,
                ignored,
                rule="staccato_fragments",
                severity="warning",
                line=1,
                quote="",
                message=f"Over-chopped prose: {int(short_ratio * 100)}% of narration sentences are <=4 words.",
            )

    # Triads & The way X did Y
    triad_hits = list(_TRIAD_RE.finditer(raw))
    if len(triad_hits) > 4:
        m = triad_hits[4]
        s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
        _add_finding(
            findings,
            ignored,
            rule="triads",
            severity="warning",
            line=_line_number_at(raw, m.start()),
            quote=m.group(0),
            message=f"Rule-of-three list cadence overused ({len(triad_hits)} triads in chapter).",
            start_char=s0,
            end_char=s1,
            full_sentence=sent,
        )

    the_way_hits = list(_THE_WAY_RE.finditer(norm))
    if len(the_way_hits) >= 3:
        m = the_way_hits[2]
        s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
        _add_finding(
            findings,
            ignored,
            rule="the_way",
            severity="warning",
            line=_line_number_at(raw, m.start()),
            quote=m.group(0),
            message=f"'the way X verbed' cliché used {len(the_way_hits)}x.",
            start_char=s0,
            end_char=s1,
            full_sentence=sent,
        )

    # Narration transition & participle openers
    if len(narr_spans) >= 12:
        trans_cnt = sum(1 for sp in narr_spans if _TRANSITION_OPEN_RE.match(sp.text))
        if trans_cnt / len(narr_spans) > 0.14:
            _add_finding(
                findings,
                ignored,
                rule="transition_openers",
                severity="warning",
                line=1,
                quote="",
                message=f"High transition-opener ratio ({trans_cnt}/{len(narr_spans)} sentences start with However/Meanwhile/Suddenly/etc.).",
            )
        part_cnt = sum(1 for sp in narr_spans if _PARTICIPLE_OPEN_RE.match(sp.text))
        if part_cnt / len(narr_spans) > 0.20:
            _add_finding(
                findings,
                ignored,
                rule="participle_openers",
                severity="warning",
                line=1,
                quote="",
                message=f"Too many '-ing' participle sentence openers ({part_cnt}/{len(narr_spans)}).",
            )

    # Paragraph uniformity & paragraph-opener monotony
    para_lens = [len(re.findall(r"\w+", p)) for p in paras]
    para_cv = 0.0
    if len(para_lens) >= 8:
        pm = sum(para_lens) / len(para_lens)
        pv = sum((x - pm) ** 2 for x in para_lens) / len(para_lens)
        para_cv = round((pv**0.5) / max(0.1, pm), 3)
        if para_cv < 0.28:
            _add_finding(
                findings,
                ignored,
                rule="paragraph_uniformity",
                severity="warning",
                line=1,
                quote="",
                message=f"Uniform paragraph lengths (CV={para_cv:.2f}). Mix punchy single-beat and fuller paragraphs.",
            )

    narr_paras = [p for p in paras if not p.startswith(('"', "“"))]
    if len(narr_paras) >= 8:
        first_words = [re.match(r"^[A-Za-z'’-]+", p).group(0) for p in narr_paras if re.match(r"^[A-Za-z'’-]+", p)]
        if first_words:
            top_word, top_cnt = Counter(first_words).most_common(1)[0]
            if top_cnt / len(first_words) > 0.38 and top_cnt >= 5:
                _add_finding(
                    findings,
                    ignored,
                    rule="paragraph_opener_monotony",
                    severity="warning",
                    line=1,
                    quote=top_word,
                    message=f"{top_cnt}/{len(first_words)} narration paragraphs start with '{top_word}'. Vary scene entry subjects.",
                )

    # Section breaks
    breaks = len(re.findall(r"^\s*(?:\*\s*\*\s*\*|---|___)\s*$", raw, re.MULTILINE))
    if breaks > 3:
        _add_finding(
            findings,
            ignored,
            rule="section_breaks",
            severity="warning",
            line=1,
            quote="---",
            message=f"{breaks} section breaks in one chapter (prefer smooth transitions or max 2-3).",
        )

    # --- 14. Narration-Only Telling / Filter Words / Emotion Naming / Felt ---
    felt_hits = [m for m in re.finditer(r"\bfelt\b", norm, re.IGNORECASE) if not _in_ranges(m.start(), m.end(), d_ranges)]
    felt_per_1k = round(len(felt_hits) / max(1.0, word_count / 1000.0), 2)
    if word_count >= 250 and felt_per_1k > 3.5:
        _add_finding(
            findings,
            ignored,
            rule="felt_density",
            severity="warning",
            line=1,
            quote="felt",
            message=f"Heavy 'felt' filter verb usage in narration ({felt_per_1k}/1,000 words; {len(felt_hits)}x).",
        )

    for m in _TELLING_RE.finditer(norm):
        if _in_ranges(m.start(), m.end(), d_ranges):
            continue
        s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
        _add_finding(
            findings,
            ignored,
            rule="telling_patterns",
            severity="warning",
            line=_line_number_at(raw, m.start()),
            quote=m.group(0),
            message="Telling/filter phrasing in narration. Dramatize through sensory action instead.",
            start_char=s0,
            end_char=s1,
            full_sentence=sent,
        )

    emo_hits = [m for m in _EMOTION_NAME_RE.finditer(norm) if not _in_ranges(m.start(), m.end(), d_ranges)]
    if len(emo_hits) > 2:
        for m in emo_hits[2:5]:
            s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
            _add_finding(
                findings,
                ignored,
                rule="emotion_naming",
                severity="warning",
                line=_line_number_at(raw, m.start()),
                quote=m.group(0),
                message="Direct emotion naming ('felt afraid/angry/...'). Show physical reaction or behavior.",
                start_char=s0,
                end_char=s1,
                full_sentence=sent,
            )

    filter_hits = [m for m in _FILTER_VERB_RE.finditer(norm) if not _in_ranges(m.start(), m.end(), d_ranges)]
    if word_count >= 350 and len(filter_hits) > max(4, word_count // 450):
        for m in filter_hits[:5]:
            s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
            _add_finding(
                findings,
                ignored,
                rule="filter_words",
                severity="warning",
                line=_line_number_at(raw, m.start()),
                quote=m.group(0),
                message=f"Frequent distancing filter phrases ('{m.group(0)}', {len(filter_hits)}x). Put perceptions directly on the page.",
                start_char=s0,
                end_char=s1,
                full_sentence=sent,
            )

    # --- 15. Dialogue Ratio & Adverbial Dialogue Tags ---
    dlg_words = sum(len(re.findall(r"[A-Za-z0-9'’-]+", raw[r0:r1])) for r0, r1 in d_ranges)
    dialogue_pct = round(100.0 * dlg_words / max(1, word_count), 1)
    if prof.craft_profile == "novel" and word_count >= 700 and dialogue_pct < 12.0:
        _add_finding(
            findings,
            ignored,
            rule="low_dialogue",
            severity="warning",
            line=1,
            quote="",
            message=f"Dialogue share is {dialogue_pct}% (< 12%). Dramatize key exchanges in spoken dialogue instead of narration summary.",
        )
    adverb_tag_hits = 0
    for m in _ADVERB_DIALOGUE_TAG_RE.finditer(raw):
        raw_adv = m.group(1)
        adv = raw_adv.lower()
        if adv in _SAFE_LY_WORDS or raw_adv[:1].isupper():
            continue
        adverb_tag_hits += 1
        if adverb_tag_hits >= 2:
            s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
            _add_finding(
                findings,
                ignored,
                rule="adverb_dialogue_tags",
                severity="warning",
                line=_line_number_at(raw, m.start()),
                quote=m.group(0),
                message=f"Adverbial dialogue tag ('{adv}'). Let the line or a concrete action carry the tone.",
                start_char=s0,
                end_char=s1,
                full_sentence=sent,
            )

    # --- 16. POV, Quotes, Anachronisms, Regional Spelling & Tense Consistency ---
    if book and book.pov == POV.first_person and bdir and chapter_num:
        outline_md = read_text(bdir / "outline.md")
        if outline_md:
            from app.pipeline._common import outline_block as _ob
            ch1_pov = pov_name(_ob(outline_md, 1), book.pov)
            if ch1_pov:
                pov_first = ch1_pov.split()[0]
                prot_surname = ch1_pov.split()[-1].lower()
                sg_map, _ = extract_surname_genders(bdir)
                prot_gender = sg_map.get(prot_surname, "")
                poss_pron = (
                    "her"
                    if prot_gender == "female"
                    else ("his" if prot_gender == "male" else "(?:her|his)")
                )
                drift_re = re.compile(
                    rf"(?:"
                    rf"\b(?:It was|There stood|watched)\s+{re.escape(pov_first)}\b|"
                    rf"\b(?:avoiding|toward|beside|past|behind|near|with|at)\s+{re.escape(pov_first)}(?:'s|\b)|"
                    rf"\b{re.escape(pov_first)}(?:'s)?\s+(?:eye|eyes|gaze|hand|hands|head|voice|face|fingers|shoulders|breath|stomach|mind|heart|chest|grip)\b[^.\n]{{0,50}}\b(?:she|he|her|his)\b|"
                    rf"\b{re.escape(pov_first)}\s+(?:walked|stepped|pulled|reached|turned|looked|glanced|paused|nodded|shook|left|entered|opened|closed|sat|stood|felt|knew|wondered|realized|sighed|exhaled|grabbed|held|placed|pushed|slid)\b"
                    rf")"
                )
                pov_breaks = 0
                _poss_pov_re = re.compile(
                    rf"\b{poss_pron}\s+(?:lips|eyes|gaze|hand|hands|chin|brow|cheek|shoulders|jaw|fingers|coat|boots|breath|heart)\b"
                )
                for sp_idx, sp in enumerate(narr_spans):
                    dm = drift_re.search(sp.text)
                    poss_hit = None
                    if not dm and _poss_pov_re.search(sp.text):
                        if (
                            not re.search(r"\b(?:I|He|She|he|she)\b", sp.text)
                            and not re.search(r"(?<=\s)[A-Z][a-z]{1,18}\b", sp.text)
                            and sp_idx + 1 < len(narr_spans)
                            and narr_spans[sp_idx + 1].text.lstrip().startswith("I ")
                            and (
                                sp_idx == 0
                                or not re.search(
                                    r"\b(?:He|She|he|she)\b|(?:^|\s)[A-Z][a-z]{2,}\b",
                                    narr_spans[sp_idx - 1].text,
                                )
                            )
                        ):
                            poss_hit = _poss_pov_re.search(sp.text)
                    hit = dm or poss_hit
                    if hit:
                        pov_breaks += 1
                        _add_finding(
                            findings,
                            ignored,
                            rule="pov_break",
                            severity="error",
                            line=sp.line,
                            quote=hit.group(0),
                            message=f"First-person POV break: narrator referred to '{pov_first}' in third person.",
                            start_char=sp.start,
                            end_char=sp.end,
                            full_sentence=sp.text,
                        )
                        if pov_breaks >= 4:
                            break

    if bdir:
        surname_genders, _ = extract_surname_genders(bdir)
        full_genders, tok_genders, seed_hon_pref = extract_character_genders(bdir)
        gm_hits = 0
        if surname_genders or seed_hon_pref:
            for m in re.finditer(r"\b(Mr\.|Mrs\.|Ms\.)\s+(?:([A-Z][a-z]{1,18})\s+)?([A-Z][a-z]{2,20})\b", raw):
                hon = m.group(1)
                first_mid = (m.group(2) + " ") if m.group(2) else ""
                last = m.group(3)
                expected = surname_genders.get(last.lower())
                pref_hon = seed_hon_pref.get(last.lower())
                is_gender_wrong = bool(
                    expected
                    and ((expected == "female" and hon.lower() == "mr.") or (expected == "male" and hon.lower() in {"ms.", "mrs."}))
                )
                is_pref_wrong = bool(pref_hon and hon.lower() != pref_hon.lower() and not is_gender_wrong)
                if is_gender_wrong or is_pref_wrong:
                    correct_hon = pref_hon if is_pref_wrong and pref_hon else ("Ms." if expected == "female" else "Mr.")
                    s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
                    _add_finding(
                        findings,
                        ignored,
                        rule="gender_mismatch",
                        severity="error",
                        line=_line_number_at(raw, m.start()),
                        quote=m.group(0),
                        message=f"Character gender/honorific mismatch ('{m.group(0)}' should be '{correct_hon} {first_mid}{last}').",
                        start_char=s0,
                        end_char=s1,
                        full_sentence=sent,
                        in_dialogue=_in_ranges(m.start(), m.end(), d_ranges),
                    )
                    gm_hits += 1
                    if gm_hits >= 6:
                        break

        # Safe third-person pronoun flip check inside narration sentences
        if (full_genders or tok_genders) and gm_hits < 6:
            _prep_before_name_re = re.compile(
                r"\b(?:at|to|toward|towards|from|for|with|beside|behind|against|past|near|about|over|around|between|of|asked|told|handed|gave|faced|watched|ignored)\s+(?:Ms\.|Mr\.|Mrs\.|Dr\.|Deputy|Sheriff|Captain|Officer)?\s*$",
                re.IGNORECASE,
            )

            def _narr_text_for_span(sp_obj: _SentSpan) -> str:
                chars = list(sp_obj.text)
                for r0, r1 in d_ranges:
                    lo = max(0, r0 - sp_obj.start)
                    hi = min(len(chars), r1 - sp_obj.start)
                    if hi > lo:
                        chars[lo:hi] = [" "] * (hi - lo)
                masked = "".join(chars)
                masked = _QUOTE_SPAN_RE.sub(" ", masked)
                masked = re.sub(r'["“][^"\n”]*$', " ", masked)
                return masked

            def _genders_in_narr(sp_obj: _SentSpan) -> tuple[set[str], list[str]]:
                n_txt = _narr_text_for_span(sp_obj)
                gs: set[str] = set()
                names: list[str] = []
                covered: list[tuple[int, int]] = []
                for fm in re.finditer(r"\b([A-Z][a-z]{1,18})\s+([A-Z][a-z]{2,20})\b", n_txt):
                    if _prep_before_name_re.search(n_txt[: fm.start()]):
                        continue
                    fn_low = f"{fm.group(1)} {fm.group(2)}".lower()
                    if fn_low in full_genders:
                        gs.add(full_genders[fn_low])
                        names.append(fm.group(0))
                        covered.append((fm.start(), fm.end()))
                for tm in re.finditer(r"\b([A-Z][a-z]{2,20})\b", n_txt):
                    if any(tm.start() >= c0 and tm.end() <= c1 for c0, c1 in covered):
                        continue
                    if _prep_before_name_re.search(n_txt[: tm.start()]):
                        continue
                    g = tok_genders.get(tm.group(1).lower())
                    if g:
                        gs.add(g)
                        names.append(tm.group(0))
                return gs, names

            def _same_para(a: _SentSpan, b: _SentSpan) -> bool:
                lo, hi = min(a.end, b.end), max(a.start, b.start)
                return "\n\n" not in raw[lo:hi]

            for s_idx, sp in enumerate(spans):
                if gm_hits >= 6:
                    break
                if sp.in_dialogue:
                    continue
                n_sp = _narr_text_for_span(sp)
                cur_gs, cur_names = _genders_in_narr(sp)

                prev_gs_ctx: set[str] = set()
                prev_txt_ctx = ""
                for back in (1, 2):
                    if s_idx - back >= 0 and _same_para(spans[s_idx - back], sp):
                        prev_sp = spans[s_idx - back]
                        if not prev_sp.in_dialogue:
                            bgs, _ = _genders_in_narr(prev_sp)
                            prev_gs_ctx |= bgs
                            if back == 1:
                                prev_txt_ctx = _narr_text_for_span(prev_sp)

                mismatch_quote = ""
                mismatch_msg = ""
                # Case A: Same sentence names one character/gender cleanly and contradicts it with third-person pronouns
                if len(cur_gs) == 1 and cur_names:
                    cg = next(iter(cur_gs))
                    opp = "female" if cg == "male" else "male"
                    if opp not in prev_gs_ctx:
                        subj_re = r"\b(?:she|herself)\b" if cg == "male" else r"\b(?:he|himself)\b"
                        poss_body_re = (
                            r"\bher\s+(?:head|eyes|hands?|face|voice|shoulders?|pocket|coat|jacket|radio|suv|truck|car|cruiser|notebook|badge|boots?|clipboard|notes?)\b"
                            if cg == "male"
                            else r"\bhis\s+(?:head|eyes|hands?|face|voice|shoulders?|pocket|coat|jacket|radio|suv|truck|car|cruiser|notebook|badge|boots?|clipboard|notes?)\b"
                        )
                        sm = re.search(subj_re, n_sp, re.IGNORECASE) or re.search(poss_body_re, n_sp, re.IGNORECASE)
                        if sm:
                            mismatch_quote = sm.group(0)
                            expected_pr = "he/his" if cg == "male" else "she/her"
                            mismatch_msg = f"Third-person pronoun mismatch ('{sm.group(0)}' contradicts {cg} character '{cur_names[0]}'; expected {expected_pr})."
                # Case B: Sentence starts with 'He'/'She' referencing sole opposite-gender character named in immediately preceding narration sentence
                elif not cur_gs and re.match(r"^\s*(He|She)\b", n_sp) and s_idx >= 1 and _same_para(spans[s_idx - 1], sp) and not spans[s_idx - 1].in_dialogue:
                    sp_pron = "male" if re.match(r"^\s*He\b", n_sp) else "female"
                    p1_gs, p1_names = _genders_in_narr(spans[s_idx - 1])
                    p2_gs = set()
                    if s_idx >= 2 and _same_para(spans[s_idx - 2], sp) and not spans[s_idx - 2].in_dialogue:
                        p2_gs, _ = _genders_in_narr(spans[s_idx - 2])
                    opp_p1_re = r"\b(?:she|her|hers|herself)\b" if sp_pron == "female" else r"\b(?:he|him|his|himself)\b"
                    if len(p1_gs) == 1 and p1_names and sp_pron not in p1_gs and sp_pron not in p2_gs and not re.search(opp_p1_re, prev_txt_ctx, re.IGNORECASE):
                        pm = re.match(r"^\s*(He|She)\b", n_sp)
                        if pm:
                            cg = next(iter(p1_gs))
                            mismatch_quote = pm.group(1)
                            expected_pr = "He" if cg == "male" else "She"
                            mismatch_msg = f"Third-person pronoun flip ('{pm.group(1)}' follows {cg} character '{p1_names[0]}'; expected '{expected_pr}')."

                if mismatch_quote:
                    _add_finding(
                        findings,
                        ignored,
                        rule="gender_mismatch",
                        severity="error",
                        line=sp.line,
                        quote=mismatch_quote,
                        message=mismatch_msg,
                        start_char=sp.start,
                        end_char=sp.end,
                        full_sentence=sp.text,
                    )
                    gm_hits += 1

    # Unbalanced quotes per paragraph (ignoring valid multi-para uninterrupted dialogue continuations)
    raw_paras = [p for p in re.split(r"\n\s*\n", raw) if p.strip() and not p.strip().startswith("#")]
    for p_idx, p_blk in enumerate(raw_paras):
        np_blk = _norm_quotes(p_blk)
        q_cnt = np_blk.count('"')
        if q_cnt % 2 == 1:
            next_starts_quote = p_idx + 1 < len(raw_paras) and _norm_quotes(raw_paras[p_idx + 1].lstrip()).startswith('"')
            if not next_starts_quote:
                p_pos = raw.find(p_blk)
                st = p_blk.strip()
                p_start = max(0, p_pos + p_blk.find(st)) if p_pos != -1 else 0
                p_end = p_start + len(st) if st else p_start
                _add_finding(
                    findings,
                    ignored,
                    rule="unbalanced_quotes",
                    severity="error",
                    line=_line_number_at(raw, max(0, p_start)),
                    quote=st[:120],
                    message="Unbalanced double quotation marks in paragraph; check opening/closing quote.",
                    start_char=p_start,
                    end_char=p_end,
                    full_sentence=st,
                )

    if book:
        era_src = f"{book.idea or ''}\n{read_text(bdir / 'world.md')[:1500] if bdir else ''}"
        if re.search(r"\b(?:1[6-8]\d\d|19[0-3]\d)\b", era_src):
            ana_re = re.compile(
                r"\b(?:fluorescent|coffee machine|breakroom|Formica|police cruisers?|skillset|cellphone|smartphone|computer|website|email|microwave|GPS)\b",
                re.IGNORECASE,
            )
            for m in list(ana_re.finditer(raw))[:4]:
                s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
                _add_finding(
                    findings,
                    ignored,
                    rule="anachronism",
                    severity="warning",
                    line=_line_number_at(raw, m.start()),
                    quote=m.group(0),
                    message=f"Historical anachronism ('{m.group(0)}') in pre-1940 setting.",
                    start_char=s0,
                    end_char=s1,
                    full_sentence=sent,
                )

        from app.pipeline._common import is_american_english as _is_us

        style_src = f"{book.idea or ''}\n{book.custom_style or ''}"
        if _is_us(book, bdir):
            uk_re = re.compile(r"\b(?:grey|towards|amongst|constable|favour|colour|harbour|realise|honour)\b", re.IGNORECASE)
            for m in list(uk_re.finditer(raw))[:4]:
                s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
                _add_finding(
                    findings,
                    ignored,
                    rule="regional_spelling",
                    severity="warning",
                    line=_line_number_at(raw, m.start()),
                    quote=m.group(0),
                    message=f"UK spelling/term ('{m.group(0)}') inside an American English manuscript.",
                    start_char=s0,
                    end_char=s1,
                    full_sentence=sent,
                )
        elif re.search(r"\bBritish English\b", style_src, re.IGNORECASE):
            us_re = re.compile(r"\b(?:color|favor|honor|realize|sidewalk|flashlight)\b", re.IGNORECASE)
            for m in list(us_re.finditer(raw))[:4]:
                s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
                _add_finding(
                    findings,
                    ignored,
                    rule="regional_spelling",
                    severity="warning",
                    line=_line_number_at(raw, m.start()),
                    quote=m.group(0),
                    message=f"US spelling/term ('{m.group(0)}') inside a British English manuscript.",
                    start_char=s0,
                    end_char=s1,
                    full_sentence=sent,
                )

    if book and book.pov == POV.third_limited and bdir and chapter_num:
        out_blk = ""
        outline_md = read_text(bdir / "outline.md")
        if outline_md:
            from app.pipeline._common import outline_block as _ob
            out_blk = _ob(outline_md, chapter_num)
        active_pov = pov_name(out_blk, book.pov) or (book.get_chapter(chapter_num).pov if book.get_chapter(chapter_num) else None)
        if active_pov:
            pov_first = active_pov.split()[0].lower()
            pov_hits = 0
            for sp in narr_spans:
                m = re.match(r"^([A-Z][a-z]{2,18})\s+(?:felt|knew|realized|realised|thought|wondered|hoped|feared)\b", sp.text)
                if m and m.group(1).lower() != pov_first and m.group(1).lower() not in {"he", "she", "they"}:
                    _add_finding(
                        findings,
                        ignored,
                        rule="pov_break",
                        severity="warning",
                        line=sp.line,
                        quote=m.group(0),
                        message=f"Possible head-hop out of {active_pov}'s POV into '{m.group(1)}'.",
                        start_char=sp.start,
                        end_char=sp.end,
                        full_sentence=sp.text,
                    )
                    pov_hits += 1
                    if pov_hits >= 4:
                        break

    if book and book.tense == Tense.past and prof.craft_profile != "children":
        drift_hits = [sp for sp in narr_spans if _PRESENT_DRIFT_RE.match(sp.text)]
        if len(drift_hits) >= 3:
            sp0 = drift_hits[0]
            _add_finding(
                findings,
                ignored,
                rule="tense_drift",
                severity="warning",
                line=sp0.line,
                quote=sp0.text[:100],
                message=f"Detected {len(drift_hits)} present-tense narration verbs in a past-tense manuscript.",
                start_char=sp0.start,
                end_char=sp0.end,
                full_sentence=sp0.text,
            )

    # --- 17. Sequel Bait (Final Standalone Unit), Theme Stated & Ending Repetition ---
    if book and chapter_num:
        total_u = book.chapter_count or len(book.chapters) or 0
        seed_low = f"{book.idea or ''}\n{read_text(bdir / 'seed.txt') if bdir else ''}".lower()
        is_final_standalone = (
            total_u >= 2
            and chapter_num == total_u
            and not any(w in seed_low for w in ("cliffhanger", "sequel", "book 1", "series opener"))
        )
        if is_final_standalone:
            _sequel_re = re.compile(
                r"\b(?:(?:only |just )?a pause, not an end|web extended (?:far )?beyond|(?:only|just) the beginning|"
                r"wasn't finished turning|this wasn't (?:truly |really |quite )?over|(?:it was |this is )?far from over|not over yet|more questions than answers|"
                r"work (?:here )?wasn't (?:quite )?finished|unanswered questions|unnamed individual|still unanswered|"
                r"only (?:be )?a chapter[—\-,\s]+not (?:the|an) end|chapter[—\-,\s]+not (?:the|an) end|(?:just|only) one chapter\b|"
                r"story that refused to be simple|another layer peeled back|what (?:else )?lay (?:hidden|beneath)|"
                r"other secrets waited to be (?:unearthed|uncovered|revealed|found)|secrets waited (?:in the shadows|to be)|"
                r"what other secrets were hidden|roots reaching far beyond|there will be repercussions)\b",
                re.IGNORECASE,
            )
            for m in list(_sequel_re.finditer(norm))[:4]:
                if _in_ranges(m.start(), m.end(), d_ranges):
                    continue
                s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
                _add_finding(
                    findings,
                    ignored,
                    rule="sequel_bait",
                    severity="error",
                    line=_line_number_at(raw, m.start()),
                    quote=m.group(0),
                    message="Banned sequel hook/teaser in final standalone chapter. Close cleanly on-page.",
                    start_char=s0,
                    end_char=s1,
                    full_sentence=sent,
                )

    for m in list(_THEME_STATED_RE.finditer(norm))[:3]:
        if _in_ranges(m.start(), m.end(), d_ranges):
            continue
        s0, s1, sent = _sentence_for_offset(spans, m.start(), m.end(), raw)
        _add_finding(
            findings,
            ignored,
            rule="theme_stated",
            severity="warning",
            line=_line_number_at(raw, m.start()),
            quote=m.group(0),
            message="On-the-nose stated theme/aphorism in narration.",
            start_char=s0,
            end_char=s1,
            full_sentence=sent,
        )

    if paras and bdir and chapter_num and chapter_num > 1:
        end_words = [w.lower() for w in re.findall(r"[A-Za-z0-9'’-]+", paras[-1])]
        end_6 = _shingles(end_words, 6)
        if end_6:
            for k, ptxt in earlier_chapter_texts(bdir, chapter_num):
                pp = [p.strip() for p in re.split(r"\n\s*\n", ptxt) if p.strip()]
                if not pp:
                    continue
                pw = [w.lower() for w in re.findall(r"[A-Za-z0-9'’-]+", pp[-1])]
                if end_6 & _shingles(pw, 6):
                    _add_finding(
                        findings,
                        ignored,
                        rule="chapter_ending_echo",
                        severity="warning",
                        line=_line_number_at(raw, max(0, len(raw) - len(paras[-1]))),
                        quote=paras[-1][:120],
                        message=f"Closing paragraph echoes ending of {prof.unit_label} {k}.",
                    )
                    break

    # --- 17b. Future Foreshadowing Prop / Clue Leak ---
    if bdir and chapter_num:
        outline_md_fc = read_text(bdir / "outline.md")
        f_items = future_foreshadowing_items(outline_md_fc, chapter_num)
        full_g_fc, tok_g_fc, _ = extract_character_genders(bdir)
        cast_name_toks = {t for fn in full_g_fc for t in fn.split()} | set(tok_g_fc.keys())
        day1_world_low = read_text(bdir / "world.md").lower()
        fc_hits = 0
        for item_label, planted_ch in f_items:
            if fc_hits >= 4:
                break
            low_item = item_label.lower()
            if any(gw in low_item for gw in _GENERIC_CLUE_WORDS):
                continue
            toks_item = [t for t in re.findall(r"[A-Za-z0-9'-]+", item_label.lower()) if t not in {"the", "a", "an", "of", "and", "in", "on", "to", "for", "from"}]
            cand_phrases: list[str] = []
            if len(toks_item) >= 2:
                # Try 2-word and 3-word concrete phrases from the foreshadowing item (e.g., "cashier's check", "brass feeler gauge")
                for k in (3, 2):
                    for j in range(0, len(toks_item) - k + 1):
                        sub_toks = toks_item[j : j + k]
                        if all(st in cast_name_toks for st in sub_toks):
                            continue
                        sub = r"\b" + r"\s+".join(re.escape(w) for w in sub_toks) + r"\b"
                        if day1_world_low and not any(cw in low_item for cw in ("cashier", "feeler", "cutoff")) and re.search(sub, day1_world_low, re.IGNORECASE):
                            continue
                        cand_phrases.append(sub)
            elif len(toks_item) == 1 and len(toks_item[0]) >= 6:
                if toks_item[0] not in cast_name_toks:
                    single_p = rf"\b{re.escape(toks_item[0])}\b"
                    if not (day1_world_low and not any(cw in low_item for cw in ("cashier", "feeler", "cutoff")) and re.search(single_p, day1_world_low, re.IGNORECASE)):
                        cand_phrases.append(single_p)
            # Special common possessive spelling e.g. cashier's check
            if "cashier" in low_item and "check" in low_item:
                cand_phrases.insert(0, r"\bcashier['’]?s\s+check\b")
            for cpat in cand_phrases:
                fm = re.search(cpat, norm, re.IGNORECASE)
                if fm:
                    s0, s1, sent = _sentence_for_offset(spans, fm.start(), fm.end(), raw)
                    _add_finding(
                        findings,
                        ignored,
                        rule="future_clue_leak",
                        severity="error",
                        line=_line_number_at(raw, fm.start()),
                        quote=raw[fm.start() : fm.end()],
                        message=f"Future clue '{item_label}' is scheduled for {prof.unit_label} {planted_ch}; do not introduce early.",
                        start_char=s0,
                        end_char=s1,
                        full_sentence=sent,
                        in_dialogue=_in_ranges(fm.start(), fm.end(), d_ranges),
                    )
                    fc_hits += 1
                    break

    # --- 18. Readability Grade Check ---
    fk_grade = flesch_kincaid_grade(words, max(1, len(spans)))
    min_fk_words = 40 if prof.craft_profile == "children" else 120
    if book and word_count >= min_fk_words:
        g_lo, g_hi = AUDIENCE_GRADE_BANDS.get(book.audience, (4.0, 14.0))
        if fk_grade > g_hi + 2.5 or (fk_grade < g_lo - 1.5 and g_lo > 2.0):
            _add_finding(
                findings,
                ignored,
                rule="readability_mismatch",
                severity="warning",
                line=1,
                quote="",
                message=f"Flesch-Kincaid grade is {fk_grade:.1f} vs target {g_lo:.1f}-{g_hi:.1f} for {book.audience.value}.",
            )

    # --- Summary & Cleanliness Grade ---
    error_count = sum(1 for f in findings if f.severity == "error")
    warning_count = sum(1 for f in findings if f.severity == "warning")
    tells_per_1000 = round((error_count * 2 + warning_count) / max(1.0, word_count / 1000.0), 2)

    hard_rules = {"token_meltdown", "paragraph_loop", "meta_leak", "script_leak"}
    if error_count >= 3 or any(f.rule in hard_rules for f in findings):
        grade = "F"
    elif error_count >= 1 or tells_per_1000 > 9.0:
        grade = "D"
    elif tells_per_1000 > 5.5:
        grade = "C"
    elif tells_per_1000 > 2.5:
        grade = "B"
    else:
        grade = "A"

    metrics = {
        "word_count": word_count,
        "bytes_per_word": bytes_per_word,
        "paragraph_count": len(paras),
        "paragraph_count_unique": unique_paras,
        "unique_paragraph_ratio": unique_ratio,
        "sentence_count": len(spans),
        "sentence_len_cv": sent_cv,
        "paragraph_len_cv": para_cv,
        "em_dash_count": em_dashes,
        "em_dash_per_150": em_per_150,
        "felt_per_1000": felt_per_1k,
        "dialogue_pct": dialogue_pct,
        "flesch_kincaid_grade": fk_grade,
        "tells_per_1000": tells_per_1000,
    }

    return {
        "error_count": error_count,
        "warning_count": warning_count,
        "tells_total": error_count + warning_count,
        "cleanliness_grade": grade,
        "metrics": metrics,
        "findings": [f.to_dict() for f in findings],
    }
