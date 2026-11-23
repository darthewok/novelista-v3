from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app import llm_client
from app.pipeline import ai_checker
from app.pipeline._common import (
    _INSTRUCTION_LEAK_LINE_RE,
    _PROMPT_FENCE_LEAK_RE,
    _WORD_COUNT_TAG_RE,
    is_american_english,
    voice_or_default,
)
from app.pipeline.directives import build_directives
from app.store import Book

AUTO_FIX_RULES = {
    "tier1_slop",
    "not_just_but",
    "meta_leak",
    "script_leak",
    "artefact",
    "exemplar_copy",
    "copied_text",
    "pov_break",
    "sequel_bait",
    "gender_mismatch",
    "unbalanced_quotes",
    "future_clue_leak",
    "em_dash_forbidden",
}

OPTIONAL_STYLE_RULES = {
    "negation_contrast",
    "modern_fiction_cliche",
    "telling_patterns",
    "staccato_fragments",
    "filter_words",
    "vague_something",
    "adverb_dialogue_tags",
    "emotion_naming",
    "em_dash_overuse",
    "tier2_repeat",
    "unbalanced_quotes",
    "regional_spelling",
    "adjacent_echo",
}

_UK_TO_US_MAP: dict[str, str] = {
    "towards": "toward",
    "amongst": "among",
    "whilst": "while",
    "grey": "gray",
    "colour": "color",
    "colours": "colors",
    "favour": "favor",
    "favours": "favors",
    "honour": "honor",
    "honours": "honors",
    "harbour": "harbor",
    "harbours": "harbors",
    "realise": "realize",
    "realised": "realized",
    "realising": "realizing",
    "recognise": "recognize",
    "recognised": "recognized",
    "cancelled": "canceled",
    "travelling": "traveling",
    "catalogued": "cataloged",
    "acknowledgement": "acknowledgment",
    "downwards": "downward",
    "outwards": "outward",
}
_UK_WORD_RE = re.compile(r"\b(" + "|".join(_UK_TO_US_MAP.keys()) + r")\b", re.IGNORECASE)


def _match_case(src: str, repl: str) -> str:
    if src.isupper():
        return repl.upper()
    if src[:1].isupper():
        return repl[0].upper() + repl[1:]
    return repl


_PURE_RULE_LINE_RE = re.compile(r"^(?:[-*_]\s*){3,}$")


def replace_em_dashes_with_hyphens(text: str) -> str:
    """Replace all Unicode em-dashes, en-dashes, and double-hyphens with clean ASCII '-' minuses."""
    if not text or ("—" not in text and "–" not in text and "--" not in text):
        return text

    out_lines: list[str] = []
    for ln in text.splitlines(keepends=True):
        eol = "\r\n" if ln.endswith("\r\n") else ("\n" if ln.endswith("\n") else "")
        body = ln[: len(ln) - len(eol)] if eol else ln
        s = body.strip()
        if s in {"---", "***", "* * *", "___"} or _PURE_RULE_LINE_RE.match(s):
            out_lines.append(ln)
            continue

        cleaned = body
        # 1. Malformed `word?—lowercase` ('Discomfort?—flashed', 'Resentment? — before') -> ' - '
        cleaned = re.sub(
            r"(?<=[A-Za-z])\s*\?\s*(?:—|–|(?<![<!-])--(?!-|>))\s*(?=[a-z])",
            " - ",
            cleaned,
        )
        # 2. Malformed sentence-end + dash before uppercase/quote ('Wait?—He paused.') -> ' '
        cleaned = re.sub(
            r"(?<=[.?!])\s*(?:—|–|(?<![<!-])--(?!-|>))\s*(?=[A-Z\"'\u201c\u2018])",
            " ",
            cleaned,
        )
        # 3. Numeric / percent ranges ('1920–1924', '92%—115%') -> '-'
        cleaned = re.sub(r"(?<=\d)\s*[—–]\s*(?=\d)", "-", cleaned)
        cleaned = re.sub(r"(?<=\d%)\s*[—–]\s*(?=\d)", "-", cleaned)
        # 4. Interrupted speech before closing quote, '!'/'?' at quote/end, closing bracket, or EOL
        cleaned = re.sub(
            r"(?<=\S)\s*(?:—|–|(?<![<!-])--(?!-|>))(?=(?:[\"'\u201d\u2019](?=\s|[.,!?;:\)\]]|$)|[!?](?=[\"'\u201d\u2019]|\s|$)|[\)\]]|\s*$))",
            "-",
            cleaned,
        )
        # 5. Resumed speech right after opening quote or bracket ('"—and then' -> '"-and then')
        cleaned = re.sub(
            r"(?:(?<=^)|(?<=[\s(\[]))([\"'\u201c\u2018(\[])\s*(?:—|–|(?<![<!-])--(?!-|>))\s*(?=\S)",
            r"\1-",
            cleaned,
        )
        # 6. Remaining dashes -> ' - '
        cleaned = re.sub(r"\s*(?:—|–|(?<![<!-])--(?!-|>))\s*", " - ", cleaned)
        cleaned = re.sub(r"^ - ", "- ", cleaned)
        cleaned = re.sub(r"(?<=\S)  +(?=\S)", " ", cleaned)
        out_lines.append(cleaned + eol)

    return "".join(out_lines)


def normalize_prose_typography(text: str, *, american_english: bool = False) -> str:
    """Normalize quotes, word-count footers, leaked prompt fences, dashes into minuses, stray asterisks, UK/US spelling, and ellipses cleanly."""
    if not text:
        return text
    out = text.replace("“", '"').replace("”", '"').replace("‘", "'").replace("’", "'")
    # Strip echoed prompt fences, leaked instructions, and hallucinated LLM word-count lines anywhere in the doc
    out = _PROMPT_FENCE_LEAK_RE.sub("", out)
    out = _INSTRUCTION_LEAK_LINE_RE.sub("", out)
    out = _WORD_COUNT_TAG_RE.sub("", out)
    # Replace em-dashes/en-dashes/double-hyphens with clean '-' minuses while preserving markdown rules
    lines = out.splitlines(keepends=True)
    norm_lines: list[str] = []
    for ln in lines:
        s = ln.strip()
        if s in {"---", "***", "* * *", "___"} or _PURE_RULE_LINE_RE.match(s):
            norm_lines.append(ln)
        elif ln.lstrip().startswith("#"):
            norm_lines.append(replace_em_dashes_with_hyphens(ln))
        else:
            cleaned_ln = replace_em_dashes_with_hyphens(ln.replace("…", "..."))
            cleaned_ln = re.sub(r"([.!?])\.\.\.", r"\1", cleaned_ln)
            cleaned_ln = re.sub(r"(?<=[a-z])\.\.\.(?=[a-z])", "... ", cleaned_ln)
            cleaned_ln = re.sub(r"(?<=\b[A-Z]\.)(?=[A-Z][a-z])", " ", cleaned_ln)
            cleaned_ln = re.sub(r"(?<=[a-z]{2}[.!?])(?=[A-Z][a-z])", " ", cleaned_ln)
            cleaned_ln = re.sub(
                r'\?"\s+(He|She|They)\s+(asked|said|replied|whispered|demanded|muttered)\b',
                lambda m: f'?" {m.group(1).lower()} {m.group(2)}',
                cleaned_ln,
            )
            cleaned_ln = re.sub(r"(?<!\.)\.\.(?!\.)", ".", cleaned_ln)
            cleaned_ln = re.sub(r"(?<=[\w\"'])\.,\s+", ", ", cleaned_ln)
            cleaned_ln = re.sub(r",,+", ",", cleaned_ln)
            cleaned_ln = re.sub(r"(?<=\S)  +(?=\S)", " ", cleaned_ln)
            if american_english:
                cleaned_ln = _UK_WORD_RE.sub(lambda m: _match_case(m.group(0), _UK_TO_US_MAP[m.group(0).lower()]), cleaned_ln)
            cleaned_ln = re.sub(
                r"\bcaymen\s+islands\b",
                lambda m: "Cayman Islands" if m.group(0)[:1].isupper() else "cayman islands",
                cleaned_ln,
                flags=re.IGNORECASE,
            )
            norm_lines.append(cleaned_ln)
    out = "".join(norm_lines)
    # Remove stray unmatched single `*` glued to sentence-ending punctuation inside prose paragraphs
    paras = re.split(r"(\n\s*\n)", out)
    cleaned_paras: list[str] = []
    for part in paras:
        ps = part.strip()
        if (
            ps
            and not ps.startswith("#")
            and ps not in {"---", "***", "* * *", "___"}
            and not re.match(r"^(?:[-*_]\s*){3,}$", ps)
            and part.count("*") % 2 == 1
        ):
            part = re.sub(r"(?<=[.!?\"'])\*(?!\*)", "", part)
        cleaned_paras.append(part)
    out = "".join(cleaned_paras)
    if len(out.split()) >= 80:
        out = re.sub(r"(?:\n\s*(?:---|\*\*\*|\*\s*\*\s*\*|___)\s*)+\Z", "", out)
    out = re.sub(r"\n{3,}", "\n\n", out)
    return out.rstrip() + ("\n" if text.endswith("\n") else "")


def _para_3grams(txt: str) -> set[tuple[str, str, str]]:
    toks = [w.lower() for w in re.findall(r"\w+", txt)]
    if len(toks) < 3:
        return set()
    return {(toks[i], toks[i + 1], toks[i + 2]) for i in range(len(toks) - 2)}


def dedupe_paragraphs_mechanically(text: str) -> tuple[str, int]:
    """Remove exact and >= 68% Jaccard near-duplicate non-heading paragraphs beyond first occurrence."""
    blocks = re.split(r"(\n\s*\n)", text)
    seen: set[str] = set()
    kept_shingles: list[set[tuple[str, str, str]]] = []
    out: list[str] = []
    removed = 0
    i = 0
    while i < len(blocks):
        blk = blocks[i]
        sep = blocks[i + 1] if i + 1 < len(blocks) else ""
        i += 2
        stripped = blk.strip()
        if not stripped:
            continue
        if stripped.startswith("#") or len(stripped.split()) < 10:
            out.append(blk + sep)
            continue
        norm = re.sub(r"\s+", " ", stripped.lower())
        if norm in seen:
            removed += 1
            continue
        sh = _para_3grams(stripped)
        near_dup = False
        if len(stripped.split()) >= 12 and len(sh) >= 8:
            for prev_sh in kept_shingles:
                union_len = len(sh | prev_sh)
                if union_len and (len(sh & prev_sh) / union_len) >= 0.68:
                    near_dup = True
                    break
        if near_dup:
            removed += 1
            continue
        seen.add(norm)
        if len(sh) >= 8:
            kept_shingles.append(sh)
        out.append(blk + sep)
    result = "".join(out).strip() + ("\n" if text.endswith("\n") else "")
    result, adj_removed = dedupe_adjacent_sentences(result)
    return result, removed + adj_removed


_ADJ_STOPWORDS = {
    "the", "and", "with", "for", "that", "this", "from", "was", "were", "had", "has", "have",
    "into", "onto", "before", "after", "over", "under", "beneath", "beside", "near", "here",
    "there", "when", "while", "where", "what", "which", "who", "whom", "her", "his", "their",
    "our", "your", "she", "he", "they", "them", "him", "you", "not", "but", "yet", "just",
}


def _content_words(sentence: str) -> set[str]:
    return {
        w.lower()
        for w in re.findall(r"[A-Za-z]+", sentence)
        if len(w) >= 3 and w.lower() not in _ADJ_STOPWORDS
    }


def is_adjacent_paraphrase(prev_sent: str, cur_sent: str) -> bool:
    """Return True if two consecutive non-dialogue sentences repeat essentially the same beat."""
    if not prev_sent or not cur_sent:
        return False
    if '"' in prev_sent or '"' in cur_sent or "“" in prev_sent or "“" in cur_sent:
        return False
    prev_w = len(prev_sent.split())
    cur_w = len(cur_sent.split())
    if prev_w < 5 or cur_w < 5:
        return False
    a = _content_words(prev_sent)
    b = _content_words(cur_sent)
    if not a or not b:
        return False
    shared = len(a & b)
    jacc = shared / len(a | b)
    overlap = shared / min(len(a), len(b))
    if shared >= 3 and (jacc >= 0.60 or overlap >= 0.75):
        return True
    if prev_w >= 6 and cur_w >= 6 and shared >= 4:
        return jacc >= 0.44 or overlap >= 0.65
    return False


def dedupe_adjacent_sentences(text: str) -> tuple[str, int]:
    """Remove adjacent paraphrased duplicate narration sentences within each paragraph."""
    if not text:
        return text, 0
    blocks = re.split(r"(\n\s*\n)", text)
    out: list[str] = []
    removed = 0
    i = 0
    while i < len(blocks):
        blk = blocks[i]
        sep = blocks[i + 1] if i + 1 < len(blocks) else ""
        i += 2
        st = blk.strip()
        if not st or st.startswith("#") or set(st) <= {"*", "-", " "} or len(st.split()) < 10:
            out.append(blk + sep)
            continue
        spans = ai_checker.split_sentences_with_spans(blk)
        if len(spans) < 2:
            out.append(blk + sep)
            continue
        from collections import Counter

        sent_norm_counts = Counter(re.sub(r"\s+", " ", s.text.strip().lower()) for s in spans)
        drop_ranges: list[tuple[int, int]] = []
        last_kept = spans[0]
        for sp in spans[1:]:
            norm_cur = re.sub(r"\s+", " ", sp.text.strip().lower())
            if sent_norm_counts[norm_cur] >= 4:
                last_kept = sp
                continue
            if not sp.in_dialogue and not last_kept.in_dialogue and is_adjacent_paraphrase(last_kept.text, sp.text):
                drop_ranges.append((sp.start, sp.end))
                removed += 1
            else:
                last_kept = sp
        if not drop_ranges:
            out.append(blk + sep)
            continue
        cleaned = blk
        for s0, s1 in reversed(drop_ranges):
            cleaned = (cleaned[:s0].rstrip() + " " + cleaned[s1:].lstrip()).strip()
        cleaned = re.sub(r"(?<=\S)  +(?=\S)", " ", cleaned)
        out.append(cleaned + sep)
    result = "".join(out).strip() + ("\n" if text.endswith("\n") else "")
    return result, removed


def _trim_word_boundary(s: str, limit: int, *, from_end: bool) -> str:
    if len(s) <= limit:
        return s.strip()
    if from_end:
        chunk = s[-limit:]
        ws = chunk.find(" ")
        return chunk[ws + 1 :].strip() if 0 <= ws < limit // 2 else chunk.strip()
    chunk = s[:limit]
    ws = chunk.rfind(" ")
    return chunk[:ws].strip() if ws > limit // 2 else chunk.strip()


def _word_ngrams(s: str, n: int = 5) -> set[tuple[str, ...]]:
    toks = [w.lower() for w in re.findall(r"[A-Za-z0-9'’-]+", s)]
    if len(toks) < n:
        return set()
    return {tuple(toks[i : i + n]) for i in range(len(toks) - n + 1)}


def _leaks_surrounding_context(orig: str, rewritten: str, before: str, after: str) -> bool:
    rew_grams = _word_ngrams(rewritten, 5)
    if not rew_grams:
        return False
    ctx_grams = (_word_ngrams(before[-300:], 5) | _word_ngrams(after[:300], 5)) - _word_ngrams(orig, 5)
    return bool(rew_grams & ctx_grams)


def _extract_cast_pronouns(bdir: Path | None, span_text: str = "") -> str:
    if not bdir:
        return ""
    _, summary = ai_checker.extract_surname_genders(bdir)
    if not summary:
        return ""
    dead = ai_checker.extract_deceased_surnames(bdir)
    span_low = (span_text or "").lower()
    kept: list[str] = []
    for entry in summary:
        toks = [w.lower() for w in re.findall(r"[A-Za-z]{3,}", entry.split("(")[0])]
        if any(t in dead for t in toks) and not any(t in span_low for t in toks):
            continue
        kept.append(entry)
    return "; ".join(kept)[:350] if kept else ""


def _extract_known_names(bdir: Path | None) -> set[str]:
    if not bdir:
        return set()
    from app.storage import read_text

    names: set[str] = set(ai_checker.extract_deceased_surnames(bdir))
    src = "\n".join(read_text(bdir / fn) for fn in ("characters.md", "canon.md", "seed.txt"))
    for m in re.finditer(r"\b([A-Z][a-z]{2,18})\s+([A-Z][a-z]{2,20})\s*\(([^)\n]{2,60})\)", src):
        names.add(m.group(1).lower())
        names.add(m.group(2).lower())
    for m in re.finditer(r"^\|\s*([A-Z][a-z]{2,18})\s+([A-Z][a-z]{2,20})(?:\s*\([^)]+\))?\s*\|", src, re.MULTILINE):
        names.add(m.group(1).lower())
        names.add(m.group(2).lower())
    return names


def _valid_span_rewrite(
    orig: str,
    rewritten: str,
    before: str,
    after: str,
    *,
    allow_quote_repair: bool = False,
    rules: set[str] | None = None,
    known_names: set[str] | None = None,
) -> bool:
    if not rewritten or not rewritten.strip():
        return False
    orig_st = orig.lstrip()
    rew_st = rewritten.lstrip()
    if orig_st[:1].isupper() and rew_st[:1].islower():
        return False
    if len(rewritten) > max(180, int(len(orig) * 1.9)):
        return False
    if len(rewritten.split()) > max(20, int(len(orig.split()) * 1.45)):
        return False
    if len(ai_checker.split_sentences_with_spans(rewritten)) > max(1, len(ai_checker.split_sentences_with_spans(orig))):
        return False
    if rewritten.rstrip().endswith(("...", "…")) and not orig.rstrip().endswith(("...", "…")):
        return False
    if re.search(r"[a-z]{2}[.!?][A-Za-z]", rew_st) or re.search(
        r'\?"\s+(?:He|She|They)\s+(?:asked|said|replied|whispered|demanded|muttered)\b', rew_st
    ):
        return False
    if orig.count("*") % 2 == 0 and rewritten.count("*") % 2 != 0:
        return False
    if re.search(r"\bsomething\b", rew_st, re.IGNORECASE) and not re.search(r"\bsomething\b", orig_st, re.IGNORECASE):
        if len(rew_st.split()) <= 14:
            return False
    orig_fem = bool(re.search(r"\b(?:she|her|hers)\b", orig_st, re.IGNORECASE))
    orig_masc = bool(re.search(r"\b(?:he|him|his)\b", orig_st, re.IGNORECASE))
    rew_fem = bool(re.search(r"\b(?:she|her|hers)\b", rew_st, re.IGNORECASE))
    rew_masc = bool(re.search(r"\b(?:he|him|his)\b", rew_st, re.IGNORECASE))
    if (orig_fem and not orig_masc and rew_masc and not rew_fem) or (
        orig_masc and not orig_fem and rew_fem and not rew_masc
    ):
        return False
    orig_cap_toks = {w.lower() for w in re.findall(r"\b[A-Z][a-z]{2,20}\b", orig_st)}
    if known_names:
        rew_cap_toks = {w.lower() for w in re.findall(r"\b[A-Z][a-z]{2,20}\b", rew_st)}
        if (rew_cap_toks - orig_cap_toks) & known_names:
            return False
    orig_hon = {(m.group(1).lower(), m.group(2).lower()) for m in re.finditer(r"\b(Mr\.|Ms\.|Mrs\.|Dr\.)\s+([A-Z][a-z]{2,20})\b", orig_st)}
    for hm in re.finditer(r"\b(Mr\.|Ms\.|Mrs\.|Dr\.)\s+([A-Z][a-z]{2,20})\b", rew_st):
        if (hm.group(1).lower(), hm.group(2).lower()) not in orig_hon:
            return False
    if rules and (rules & {"exemplar_copy", "copied_text"}):
        orig_7 = _word_ngrams(orig_st, 7)
        if orig_7 and (orig_7 & _word_ngrams(rew_st, 7)):
            return False
    orig_q = orig.count('"')
    rew_q = rewritten.count('"')
    if allow_quote_repair:
        if rew_q % 2 != 0:
            return False
    elif (orig_q >= 2 and rew_q != orig_q) or (orig_q == 0 and rew_q > 0) or (orig_q == 1 and rew_q == 0):
        return False
    if _leaks_surrounding_context(orig, rewritten, before, after):
        return False
    prev_spans = ai_checker.split_sentences_with_spans(before[-350:]) if before.strip() else []
    if prev_spans and is_adjacent_paraphrase(prev_spans[-1].text, rewritten):
        return False
    next_spans = ai_checker.split_sentences_with_spans(after[:350]) if after.strip() else []
    if next_spans and is_adjacent_paraphrase(rewritten, next_spans[0].text):
        return False
    return True


async def _rewrite_span(
    *,
    book: Book | None,
    bdir: Path | None,
    span_text: str,
    issues: list[str],
    context_before: str,
    context_after: str,
) -> str:
    directives = build_directives(book) if book else ""
    voice = voice_or_default(bdir) if bdir else ""
    system_parts = [
        "Rewrite ONLY the given TARGET sentence/passage in isolation to eliminate every listed AI writing tic while preserving exact story facts, characters, pronouns, physical objects (never substitute unrelated objects like boat/wrench), POV, tense, and voice.",
        "EXCEPTION FOR `[future_clue_leak]`: If an issue cites `[future_clue_leak]`, you MUST delete or replace ONLY the flagged future clue phrase/object with a neutral Day-1 physical detail (never keep that future clue word/phrase).",
        "Keep strictly the SAME sentence count and approximate word count as TARGET. NEVER introduce any character name, deceased victim name, or Mr./Ms. title that is not already present inside TARGET.",
        "NEVER copy or paraphrase PRECEDING SENTENCE. Return ONLY the rewritten sentence(s) — no wrapper quotes around the whole output, no explanations, no ellipsis cutoffs, no commentary.",
    ]
    if directives:
        system_parts.insert(0, directives)
    if voice:
        system_parts.append(f"## VOICE GUIDE\n{voice[:2500]}")

    user_sections: list[str] = []
    cast_pron = _extract_cast_pronouns(bdir, span_text)
    if cast_pron:
        user_sections.append(f"CAST PRONOUNS (STRICT — NEVER FLIP GENDER/HONORIFIC):\n{cast_pron}")
    if (context_before or "").strip():
        prev_spans = ai_checker.split_sentences_with_spans(context_before[-350:])
        if prev_spans:
            user_sections.append(
                "PRECEDING SENTENCE (READ-ONLY FOR PRONOUN/SUBJECT CLARITY ONLY — NEVER COPY OR PARAPHRASE IT):\n"
                + _trim_word_boundary(prev_spans[-1].text, 200, from_end=True)
            )
    user_sections.append("ISSUES TO REMOVE:\n" + "\n".join(f"- {iss}" for iss in issues))
    user_sections.append(f"TARGET TO REWRITE ({len(span_text.split())} words):\n{span_text}")
    user = "\n\n".join(user_sections)
    res = await llm_client.complete(
        system="\n\n".join(system_parts),
        user=user,
        task="revise",
        max_tokens=max(70, int(len(span_text.split()) * 2.4) + 35),
        creativity="low",
    )
    cand = res.text.strip()
    # Strip accidental wrapper quotes if original didn't start/end with them
    if cand.startswith('"') and cand.endswith('"') and not (span_text.startswith('"') and span_text.endswith('"')):
        cand = cand[1:-1].strip()
    return cand


async def fix(
    text: str,
    report: dict[str, Any],
    *,
    book: Book | None = None,
    bdir: Path | None = None,
    chapter_num: int | None = None,
    include_warnings: bool = False,
    max_rewrites: int = 24,
) -> tuple[str, dict[str, Any]]:
    """Span-safe bottom-up mechanical + surgical LLM fixer (never truncates at 160 chars)."""
    us_english = is_american_english(book, bdir)
    cur, deduped = dedupe_paragraphs_mechanically(text)

    # Deterministic fast-path for em-dashes and unambiguous character gender/honorific mismatches before LLM rewrites
    mech_fixed = 0
    dash_clean = replace_em_dashes_with_hyphens(cur)
    if dash_clean != cur:
        cur = dash_clean
        mech_fixed += 1
    if bdir:
        surname_genders, _ = ai_checker.extract_surname_genders(bdir)
        _, _, seed_hon_pref = ai_checker.extract_character_genders(bdir)
        if surname_genders or seed_hon_pref:
            def _fix_hon(m: re.Match[str]) -> str:
                nonlocal mech_fixed
                hon = m.group(1)
                first_mid = (m.group(2) + " ") if m.group(2) else ""
                last = m.group(3)
                exp = surname_genders.get(last.lower())
                pref_hon = seed_hon_pref.get(last.lower())
                if exp == "female" and hon.lower() == "mr.":
                    mech_fixed += 1
                    return f"{pref_hon or 'Ms.'} {first_mid}{last}"
                if exp == "male" and hon.lower() in {"ms.", "mrs."}:
                    mech_fixed += 1
                    return f"Mr. {first_mid}{last}"
                if pref_hon and hon.lower() != pref_hon.lower():
                    mech_fixed += 1
                    return f"{pref_hon} {first_mid}{last}"
                return m.group(0)
            cur = re.sub(r"\b(Mr\.|Mrs\.|Ms\.)\s+(?:([A-Z][a-z]{1,18})\s+)?([A-Z][a-z]{2,20})\b", _fix_hon, cur)

    # Re-scan after deduplication or deterministic repair so offsets match `cur` strictly!
    if deduped > 0 or mech_fixed > 0:
        report = ai_checker.scan(cur, book=book, bdir=bdir, chapter_num=chapter_num)

    allowed_rules = set(AUTO_FIX_RULES)
    if include_warnings:
        allowed_rules |= OPTIONAL_STYLE_RULES

    findings = [
        f
        for f in (report.get("findings") or [])
        if f.get("rule") in allowed_rules and int(f.get("end_char", 0)) > int(f.get("start_char", 0))
    ]

    # Group findings by exact (start_char, end_char) span
    by_span: dict[tuple[int, int], list[dict[str, Any]]] = {}
    for f in findings:
        s0 = max(0, min(int(f["start_char"]), len(cur)))
        s1 = max(s0, min(int(f["end_char"]), len(cur)))
        if s1 <= s0:
            continue
        by_span.setdefault((s0, s1), []).append(f)

    # Merge any overlapping spans
    sorted_spans = sorted(by_span.items(), key=lambda kv: (kv[0][0], -kv[0][1]))
    merged_groups: list[tuple[int, int, list[dict[str, Any]]]] = []
    for (s0, s1), flist in sorted_spans:
        if merged_groups and s0 < merged_groups[-1][1]:
            ps0, ps1, pfl = merged_groups[-1]
            merged_groups[-1] = (ps0, max(ps1, s1), pfl + flist)
        else:
            merged_groups.append((s0, s1, flist))

    # Prioritize hard errors and mechanical quote/spelling/anachronism fixes over style polish
    _mech_rules = {"unbalanced_quotes", "regional_spelling", "anachronism", "mixed_quotes", "gender_mismatch"}

    def _group_prio(grp: tuple[int, int, list[dict[str, Any]]]) -> tuple[int, int]:
        s0, _, fl = grp
        if any(f.get("severity") == "error" for f in fl):
            return (0, s0)
        if any(f.get("rule") in _mech_rules for f in fl):
            return (1, s0)
        return (2, s0)

    merged_groups = sorted(merged_groups, key=_group_prio)[:max_rewrites]

    # Compute replacements first, then apply strictly in descending offset order!
    edits: list[tuple[int, int, str]] = []
    skipped: list[str] = []
    cache: dict[str, str] = {}
    known_names = _extract_known_names(bdir)
    _hard_check_rules = {"token_meltdown", "meta_leak", "tier1_slop", "exemplar_copy", "copied_text", "sequel_bait", "pov_break", "gender_mismatch", "unbalanced_quotes", "future_clue_leak"}

    for s0, s1, flist in merged_groups:
        orig_span = cur[s0:s1]
        if not orig_span.strip():
            continue
        # Skip short non-error warning fragments (avoids mangling 4-word lines like "It wasn't a question.")
        if all(f.get("severity") != "error" for f in flist) and len(orig_span.split()) < 6:
            continue
        grp_rules = {f["rule"] for f in flist}
        allow_q_rep = "unbalanced_quotes" in grp_rules
        issues = [f"[{f['rule']}] {f.get('message', '')} (matched: {f.get('quote', '')})" for f in flist]
        ckey = f"{orig_span}||{'|'.join(sorted(issues))}"
        if ckey in cache:
            rewritten = cache[ckey]
        else:
            rewritten = await _rewrite_span(
                book=book,
                bdir=bdir,
                span_text=orig_span,
                issues=issues,
                context_before=cur[:s0],
                context_after=cur[s1:],
            )
            cache[ckey] = rewritten

        valid1 = _valid_span_rewrite(
            orig_span, rewritten, cur[:s0], cur[s1:], allow_quote_repair=allow_q_rep, rules=grp_rules, known_names=known_names
        )
        check = ai_checker.scan(rewritten, book=book, bdir=bdir, chapter_num=chapter_num) if valid1 else {"findings": []}
        bad_new = {f["rule"] for f in check["findings"] if f["rule"] in _hard_check_rules and (f["rule"] in grp_rules or f["rule"] in {"token_meltdown", "meta_leak", "tier1_slop", "gender_mismatch", "unbalanced_quotes"})}
        if not valid1 or bad_new:
            retry_note = (
                f"Completely rephrase with fresh wording (no matching 7-word phrase), keep exact character pronouns, introduce zero new proper names, and eliminate: {', '.join(sorted(bad_new or grp_rules))}"
            )
            rewritten2 = await _rewrite_span(
                book=book,
                bdir=bdir,
                span_text=orig_span,
                issues=issues + [retry_note],
                context_before=cur[:s0],
                context_after=cur[s1:],
            )
            valid2 = _valid_span_rewrite(
                orig_span, rewritten2, cur[:s0], cur[s1:], allow_quote_repair=allow_q_rep, rules=grp_rules, known_names=known_names
            )
            check2 = ai_checker.scan(rewritten2, book=book, bdir=bdir, chapter_num=chapter_num) if valid2 else {"findings": [{"rule": "invalid"}]}
            bad_new2 = {f["rule"] for f in check2["findings"] if f["rule"] in _hard_check_rules and (f["rule"] in grp_rules or f["rule"] in {"token_meltdown", "meta_leak", "tier1_slop", "gender_mismatch", "unbalanced_quotes"})}
            if not valid2 or bad_new2:
                skipped.append(orig_span[:80])
                continue
            rewritten = rewritten2

        edits.append((s0, s1, rewritten))

    out = cur
    for s0, s1, repl in sorted(edits, key=lambda x: x[0], reverse=True):
        out = out[:s0] + repl + out[s1:]
    out = re.sub(r"(?:\n\s*(?:---|\*\*\*|\*\s*\*\s*\*|___)\s*)+\Z", "", out)
    out = normalize_prose_typography(out, american_english=us_english)
    out, adj_extra = dedupe_adjacent_sentences(out)

    return out, {
        "deduped_paragraphs": deduped + mech_fixed + adj_extra,
        "rewritten_spans": len(edits),
        "skipped_spans": len(skipped),
    }


async def fix_one(
    text: str,
    *,
    start_char: int,
    end_char: int,
    message: str,
    rule: str,
    quote: str = "",
    book: Book | None = None,
    bdir: Path | None = None,
) -> tuple[str, bool]:
    """Surgical single-finding rewrite triggered directly from the AI Checker UI."""
    s0 = max(0, min(start_char, len(text)))
    s1 = max(s0, min(end_char, len(text)))
    if s1 <= s0:
        return text, False
    orig = text[s0:s1]
    if rule == "em_dash_forbidden":
        fixed_span = replace_em_dashes_with_hyphens(orig)
        if fixed_span != orig:
            out = normalize_prose_typography(
                text[:s0] + fixed_span + text[s1:],
                american_english=is_american_english(book, bdir),
            )
            return out, True
    rewritten = await _rewrite_span(
        book=book,
        bdir=bdir,
        span_text=orig,
        issues=[f"[{rule}] {message} (matched: {quote})"],
        context_before=text[:s0],
        context_after=text[s1:],
    )
    if (
        not _valid_span_rewrite(
            orig,
            rewritten,
            text[:s0],
            text[s1:],
            allow_quote_repair=(rule == "unbalanced_quotes"),
        )
        or rewritten.strip() == orig.strip()
    ):
        return text, False
    out = normalize_prose_typography(
        text[:s0] + rewritten + text[s1:],
        american_english=is_american_english(book, bdir),
    )
    return out, True
