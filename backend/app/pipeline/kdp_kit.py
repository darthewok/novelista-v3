from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from app import llm_client
from app.enums import BookFormat
from app.formats import get_profile
from app.pipeline import ai_checker
from app.pipeline._common import WarnCB, extract_plot_spine, is_mystery_genre, outline_block, parse_llm_json, pov_name, warn_if_cut
from app.pipeline.directives import build_directives
from app.pipeline.slop_fix import replace_em_dashes_with_hyphens
from app.storage import atomic_write_json, atomic_write_text, read_json, read_text
from app.store import Book

_ADULT_FICTION_BISAC = [
    "FICTION / Mystery & Detective / Amateur Sleuth",
    "FICTION / Mystery & Detective / Private Investigators",
    "FICTION / Mystery & Detective / Police Procedural",
    "FICTION / Mystery & Detective / Traditional",
    "FICTION / Mystery & Detective / Cozy / General",
    "FICTION / Mystery & Detective / Women Sleuths",
    "FICTION / Thrillers / Crime",
    "FICTION / Thrillers / Suspense",
    "FICTION / Literary",
    "FICTION / Family Life / General",
    "FICTION / Family Life / Siblings",
    "FICTION / Contemporary Women",
    "FICTION / Coming of Age",
    "FICTION / Romance / Contemporary",
    "FICTION / Science Fiction / General",
    "FICTION / Fantasy / General",
    "FICTION / Sea Stories",
    "FICTION / Small Town & Rural",
    "FICTION / Historical / General",
    "FICTION / General",
]

_JUVENILE_FICTION_BISAC = [
    "JUVENILE FICTION / Animals / General",
    "JUVENILE FICTION / Animals / Frogs & Toads",
    "JUVENILE FICTION / Animals / Mice, Hamsters, Guinea Pigs, etc.",
    "JUVENILE FICTION / Animals / Turtles & Tortoises",
    "JUVENILE FICTION / Social Themes / Friendship",
    "JUVENILE FICTION / Bedtime & Dreams",
    "JUVENILE FICTION / Nature & the Natural World / General",
    "JUVENILE FICTION / Imagination & Play",
]

_BISAC_ALIASES: dict[str, str] = {
    "fiction / crime thrillers / amateur sleuth": "FICTION / Mystery & Detective / Amateur Sleuth",
    "fiction / mystery & suspense / amateur sleuth": "FICTION / Mystery & Detective / Amateur Sleuth",
    "fiction / contemporary fiction": "FICTION / Small Town & Rural",
    "fiction / contemporary": "FICTION / Literary",
    "fiction / maritime": "FICTION / Sea Stories",
    "fiction / nautical & maritime": "FICTION / Sea Stories",
    "fiction / mystery & detective / general": "FICTION / Mystery & Detective / Traditional",
    "juvenile fiction / animals": "JUVENILE FICTION / Animals / General",
    "juvenile fiction / friendship": "JUVENILE FICTION / Social Themes / Friendship",
    "juvenile fiction / bedtime": "JUVENILE FICTION / Bedtime & Dreams",
    "juvenile fiction / nature": "JUVENILE FICTION / Nature & the Natural World / General",
}

_BANNED_COMP_PATTERNS = (
    "fokley",
    "before we were yours",
    "winter house by elin",
    "where the crawdads sing",
    "slewfoot",
    "sweet hereafter by",
    "commonwealth by robertson",
    "name of the rose",
    "josephine tey",
    "an expert in murder",
    "maisie dobbs",
    "long bright river",
)
_VERIFIED_MYSTERY_COMPS = [
    "The Dry by Jane Harper",
    "Raven Black by Ann Cleeves",
    "Still Life by Louise Penny",
]
_VERIFIED_LITERARY_COMPS = [
    "The Shipping News by Annie Proulx",
    "Plainsong by Kent Haruf",
    "Train Dreams by Denis Johnson",
]
_VERIFIED_CHILDREN_COMPS = [
    "Corduroy by Don Freeman",
    "Blueberries for Sal by Robert McCloskey",
    "Owl Moon by Jane Yolen",
]


def _normalize_comp_titles(raw_comps: list[Any], *, is_children: bool, is_mystery: bool = True) -> list[str]:
    cleaned: list[str] = []
    removed_any = False
    _mystery_pats = ("the dry by jane harper", "raven black by ann cleeves", "still life by louise penny")
    for item in raw_comps or []:
        c = str(item or "").strip()
        if not c:
            continue
        low = c.lower()
        if any(pat in low for pat in _BANNED_COMP_PATTERNS):
            removed_any = True
            continue
        if not is_children and not is_mystery and any(mp in low for mp in _mystery_pats):
            removed_any = True
            continue
        if c not in cleaned:
            cleaned.append(c)
    if removed_any or not cleaned:
        fallback = (
            _VERIFIED_CHILDREN_COMPS
            if is_children
            else (_VERIFIED_MYSTERY_COMPS if is_mystery else _VERIFIED_LITERARY_COMPS)
        )
        for fb in fallback:
            if len(cleaned) >= 3:
                break
            if fb not in cleaned:
                cleaned.append(fb)
    return cleaned[:5]


def _normalize_bisac_categories(
    raw_cats: list[Any],
    *,
    is_children: bool = False,
    is_present_day: bool = False,
    is_mystery: bool = True,
    is_maritime: bool = False,
) -> list[str]:
    allowed_set = _JUVENILE_FICTION_BISAC if is_children else _ADULT_FICTION_BISAC
    allowed_lookup = {c.lower(): c for c in allowed_set}
    prefix = "JUVENILE FICTION /" if is_children else "FICTION /"
    out: list[str] = []
    for item in raw_cats or []:
        c = re.sub(r"\s*/\s*", " / ", str(item or "").strip())
        if not c:
            continue
        low = c.lower()
        if "nonfiction" in low:
            continue
        if is_present_day and "historical" in low:
            continue
        mapped = _BISAC_ALIASES.get(low)
        if mapped:
            c = mapped
            low = c.lower()
        if not is_children and not is_mystery and ("mystery" in low or "thriller" in low):
            continue
        if low in allowed_lookup:
            c = allowed_lookup[low]
        elif not c.upper().startswith(prefix):
            continue
        elif is_children and low not in allowed_lookup:
            # Keep only verified BISG paths for children's books
            continue
        if c not in out:
            out.append(c)
    if not out:
        if is_children:
            return [
                "JUVENILE FICTION / Animals / General",
                "JUVENILE FICTION / Social Themes / Friendship",
                "JUVENILE FICTION / Bedtime & Dreams",
            ]
        if is_mystery:
            return [
                "FICTION / Mystery & Detective / Amateur Sleuth",
                "FICTION / Small Town & Rural",
                "FICTION / General",
            ]
        return [
            "FICTION / Literary",
            "FICTION / Sea Stories" if is_maritime else "FICTION / Family Life / General",
            "FICTION / Small Town & Rural",
        ]
    return out[:3]


def load_kdp_kit(bdir: Path) -> dict[str, Any]:
    data = read_json(bdir / "kdp_kit.json")
    if isinstance(data, dict):
        return data
    return {
        "subtitle": "",
        "blurb_html": "",
        "keywords": [],
        "categories": [],
        "comp_titles": [],
        "age_range": "",
        "cover_art_brief": "",
    }


def _html_blurb_to_md(blurb_html: str) -> str:
    txt = str(blurb_html or "").strip()
    if not txt:
        return ""
    txt = re.sub(r"<(?:strong|b)\b[^>]*>(.*?)</(?:strong|b)>", r"**\1**", txt, flags=re.IGNORECASE | re.DOTALL)
    txt = re.sub(r"<(?:em|i)\b[^>]*>(.*?)</(?:em|i)>", r"*\1*", txt, flags=re.IGNORECASE | re.DOTALL)
    txt = re.sub(r"</p>\s*<p\b[^>]*>", "\n\n", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<br\s*/?>", "\n", txt, flags=re.IGNORECASE)
    txt = re.sub(r"<[^>]+>", "", txt)
    txt = re.sub(r"\n{3,}", "\n\n", txt)
    return txt.strip()


def save_kdp_kit(
    bdir: Path,
    kit: dict[str, Any],
    *,
    title: str = "",
    is_children: bool | None = None,
    is_present_day: bool = False,
    is_mystery: bool = True,
    is_maritime: bool = False,
    author_name: str = "",
    protagonist_name: str = "",
) -> dict[str, Any]:
    blurb = str(kit.get("blurb_html") or "")
    blurb = re.sub(r"<strong\b[^>]*>\s*(?:Hook|Body|Setup|Opening)\s*:\s*</strong>\s*", "", blurb, flags=re.IGNORECASE)
    blurb = re.sub(r"(<strong\b[^>]*>)\s*(?:Hook|Body|Setup|Opening)\s*:\s*", r"\1", blurb, flags=re.IGNORECASE)
    blurb = re.sub(r"(<p\b[^>]*>)\s*(?:Hook|Body|Setup|Opening)\s*:\s*", r"\1", blurb, flags=re.IGNORECASE)
    blurb = re.sub(r"^\s*(?:Hook|Body|Setup|Opening)\s*:\s*", "", blurb, flags=re.IGNORECASE)
    blurb = replace_em_dashes_with_hyphens(blurb).replace("…", "...")
    blurb = re.sub(r" {2,}", " ", blurb)
    cover_brief = str(kit.get("cover_art_brief") or "")
    if author_name.strip() and protagonist_name.strip() and author_name.strip().lower() != protagonist_name.strip().lower():
        cover_brief = re.sub(
            rf"(author(?:\s+name)?[^.\n]{{0,25}}?)(['\"]?){re.escape(protagonist_name.strip())}\2",
            rf"\1\g<2>{author_name.strip()}\g<2>",
            cover_brief,
            flags=re.IGNORECASE,
        )
    subtitle = str(kit.get("subtitle") or "")[:200]
    title_tokens = {
        w.lower()
        for w in re.findall(r"[A-Za-z0-9']+", f"{title} {subtitle}")
        if len(w) >= 3
    }
    raw_kws = [str(k).strip()[:50] for k in (kit.get("keywords") or []) if str(k).strip()]
    filtered_kws = [
        k
        for k in raw_kws
        if (kw_toks := [w.lower() for w in re.findall(r"[A-Za-z0-9']+", k)])
        and not all(w in title_tokens for w in kw_toks)
    ]
    final_kws = (filtered_kws if filtered_kws else raw_kws)[:7]
    raw_cats = [str(c).strip() for c in (kit.get("categories") or []) if str(c).strip()]
    inferred_child = is_children if is_children is not None else any("juvenile" in c.lower() for c in raw_cats)
    norm_cats = _normalize_bisac_categories(
        raw_cats,
        is_children=inferred_child,
        is_present_day=is_present_day,
        is_mystery=is_mystery,
        is_maritime=is_maritime,
    )
    clean = {
        "subtitle": subtitle,
        "blurb_html": blurb,
        "keywords": final_kws,
        "categories": norm_cats,
        "comp_titles": _normalize_comp_titles(kit.get("comp_titles") or [], is_children=inferred_child, is_mystery=is_mystery),
        "age_range": str(kit.get("age_range") or ""),
        "cover_art_brief": cover_brief,
    }
    atomic_write_json(bdir / "kdp_kit.json", clean)
    md = "\n".join(
        [
            "# Amazon KDP Publishing Kit",
            *([f"**Author:** {author_name.strip()}"] if author_name.strip() else []),
            f"**Subtitle:** {clean['subtitle']}",
            f"**Age / Grade Range:** {clean['age_range']}",
            "",
            "## Back-Cover / Product Blurb",
            _html_blurb_to_md(clean["blurb_html"]),
            "",
            "## 7 Backend Keywords (<= 50 chars each)",
            "\n".join(f"- {k}" for k in clean["keywords"]),
            "",
            "## BISAC / Amazon Categories",
            "\n".join(f"- {c}" for c in clean["categories"]),
            "",
            "## Comp Titles",
            "\n".join(f"- {c}" for c in clean["comp_titles"]),
            "",
            "## Cover Art Direction Brief",
            clean["cover_art_brief"],
        ]
    )
    atomic_write_text(bdir / "kdp_kit.md", md)
    return clean


async def generate(
    book: Book,
    bdir: Path,
    *,
    stream_cb: llm_client.StreamCB | None = None,
    on_warning: WarnCB = None,
) -> dict[str, Any]:
    prof = get_profile(book.book_format)
    directives = build_directives(book)
    seed = read_text(bdir / "seed.txt").strip() or book.idea
    canon = read_text(bdir / "canon.md").strip()
    summary = read_text(bdir / "summary.md").strip()
    chars = read_text(bdir / "characters.md").strip()
    outline_md = read_text(bdir / "outline.md")
    spine_txt = extract_plot_spine(outline_md)
    protagonist_name = pov_name(outline_block(outline_md, 1), book.pov) or ""
    author_name = (book.front_matter.author_name or "").strip() or "Author"
    is_children = prof.craft_profile == "children"
    is_mystery = is_mystery_genre(book, seed)
    is_maritime = bool(re.search(r"\b(?:boat\w*|maritime|harbor|harbour|sea|ocean|coast\w*|sail\w*|ship\w*|hull|tugboat)\b", f"{book.genre or ''}\n{seed}", re.I))
    is_present_day = bool(
        re.search(r"\b(?:present-day|present day|modern|contemporary|202\d)\b", f"{seed}\n{canon}", re.I)
    ) and not bool(re.search(r"\b1[6-9]\d\d\b", seed))
    bisac_root = "JUVENILE FICTION / ..." if is_children else "FICTION / ..."
    allowed_bisac = _JUVENILE_FICTION_BISAC if is_children else _ADULT_FICTION_BISAC

    comp_hint = (
        "Only cite verified published picture-book classics matching ages 3–7 (e.g., Don Freeman, Robert McCloskey, Jane Yolen, Laura Numeroff)."
        if is_children
        else (
            (
                "Only cite verified concise/atmospheric mystery titles matching novella or classic whodunit pacing (e.g., Ann Cleeves, Louise Penny, Georges Simenon, Agatha Christie, Jane Harper) — NEVER invent fake titles or cite 450-page blockbuster thrillers."
                if book.book_format == BookFormat.novella
                else "Only cite verified published mystery/suspense novels whose real author and subgenre match this book (e.g., Ann Cleeves, Louise Penny, Jane Harper, William Kent Krueger, Tana French) — NEVER invent fake titles."
            )
            if is_mystery
            else "Only cite verified literary/contemporary fiction novellas or coastal/small-town family dramas matching this book's tone (e.g., Annie Proulx, Kent Haruf, Elizabeth Strout, Denis Johnson, Claire Keegan, Richard Russo) — NEVER cite murder mysteries, thrillers, or invented titles."
        )
    )
    genre_low = (book.genre or "").lower()
    thriller_rule = "" if "thriller" in genre_low else (" Avoid calling a pure whodunit mystery a 'thriller' in keywords." if is_mystery else " NEVER use 'mystery', 'thriller', or 'whodunit' for non-mystery fiction; never use nonfiction hobby phrases like 'hand tool woodworking' or 'small town economics' — write 24–48 char fiction reader search phrases (e.g., 'coastal family drama novella', 'oregon working harbor literary fiction').")
    cat_rule = (
        "For a professional investigator/claims-adjuster whodunit, pick accurate detective/traditional categories — do NOT pick `FICTION / Literary` or `Amateur Sleuth` unless the protagonist is truly an amateur."
        if is_mystery
        else "Match categories strictly to the book's real literary/drama/family genre (`FICTION / Literary`, `FICTION / Sea Stories`, `FICTION / Small Town & Rural`, `FICTION / Family Life / General`) — NEVER assign Mystery, Detective, or Thriller categories to non-mystery fiction."
    )
    scope_rule = (
        " Reflect focused novella scope accurately in the blurb and NEVER invent 'decades-old' claims unless explicitly in Seed/Canon."
        if book.book_format == BookFormat.novella
        else ""
    )

    system = "\n\n".join(
        [
            directives,
            "You are an experienced Amazon KDP book-launch strategist and copywriter.\n"
            "Write zero AI buzzwords or blurb clichés ('delve', 'tapestry', 'rollercoaster ride', 'unforgettable journey', 'better left undisturbed', 'a web of', 'unravel a conspiracy', 'isn't just X, it's Y', 'more than just', 'everything she holds dear', 'hangs in the balance', 'hangs precariously'). "
            "Write a punchy, human back-cover blurb (NEVER output literal template words like 'Hook:' or 'Body:' inside the blurb!). "
            "Reflect ONLY real stakes and on-page facts from Summary, Canon, and Seed (NEVER invent crimes, police, conspiracies, or life threats unless on-page)."
            + scope_rule
            + "\nPreserve protagonist trades, surnames, exact timeline facts, and plot facts accurately from Plot Logic Spine, Canon, and Seed.\n"
            f"Each of the 7 KDP backend keywords MUST be a specific 24–48 character fiction buyer search phrase (avoid vague 2-word filler or nonfiction how-to phrases) and MUST NOT repeat any significant word already in the book title or subtitle"
            + (". NEVER use the word 'novel' for a novella, short story, or picture book." if book.book_format.value != "novel" else ".")
            + thriller_rule
            + ("\nNEVER use 'historical' in keywords or categories for a present-day story." if is_present_day else "")
            + f"\n{comp_hint}\n{cat_rule}\n"
            "In `cover_art_brief`, put ONLY the Book Author Name (NEVER the protagonist's name!) in the author-name zone, describe ONLY the exact physical prop & surface from Seed/Canon (never invent 'rusted' or 'wooden planks'), specify one unmistakable foreground focal object, high-contrast thumbnail hierarchy, and clean negative-space title/author placement.\n"
            f"Choose 3 BISAC categories strictly from this curated official BISG list:\n- " + "\n- ".join(allowed_bisac) + "\n"
            "Return ONLY valid JSON:\n"
            '{"subtitle": "...", "blurb_html": "<p><strong>Opening dilemma line...</strong></p><p>Two concise narrative paragraphs...</p>", '
            f'"keywords": ["kw phrase 1", ... 7 items], "categories": ["{bisac_root}", ... 3 items], '
            '"comp_titles": ["Book A by Author A", ...], "age_range": "...", "cover_art_brief": "..."}',
        ]
    )

    user_parts = [f"Title: {book.title} ({prof.label})", f"Author Name (for cover brief — DO NOT use protagonist name): {author_name}"]
    if book.front_matter.subtitle.strip():
        user_parts.append(f"Existing Subtitle (preserve): {book.front_matter.subtitle.strip()}")
    user_parts.append(f"Seed:\n{seed[:6000]}")
    if spine_txt:
        user_parts.append(f"Plot Logic & Resolution Spine:\n{spine_txt[:2000]}")
    if canon:
        user_parts.append(f"Canon:\n{canon[:2500]}")
    user_parts.extend([f"Characters:\n{chars[:2500]}", f"Summary:\n{summary[:4000]}"])

    res = await llm_client.complete(
        system=system,
        user="\n\n".join(user_parts),
        task="kdp",
        max_tokens=2200,
        creativity="medium",
        json_mode=True,
        stream_cb=stream_cb,
    )
    warn_if_cut(res.stop_reason, on_warning, "KDP Publishing Kit")

    try:
        parsed = parse_llm_json(res.text)
        if not isinstance(parsed, dict):
            raise ValueError("KDP kit root is not dict")
    except Exception as exc:
        if on_warning:
            on_warning(f"KDP kit JSON parse fallback: {exc}")
        parsed = {
            "subtitle": book.front_matter.subtitle or f"A {prof.label}",
            "blurb_html": f"<p>{seed[:450]}</p>",
            "keywords": [book.genre or prof.label.lower()],
            "categories": ["Fiction / General"],
            "comp_titles": [],
            "age_range": book.audience.value,
            "cover_art_brief": res.text.strip()[:1000],
        }
    else:
        # Anti-slop & keyword-overlap polish on KDP kit copy
        plain_blurb = re.sub(r"<[^>]+>", " ", str(parsed.get("blurb_html") or ""))
        blurb_scan = ai_checker.scan(plain_blurb)
        slop_hits = [
            str(f.get("quote") or "")
            for f in blurb_scan.get("findings") or []
            if f.get("rule") in {"tier1_slop", "not_just_but", "modern_fiction_cliche", "negation_contrast"} and f.get("quote")
        ]
        buzz_hits = re.findall(
            r"\b(?:delve[sd]?|delving|tapestry|rollercoaster|unforgettable|testament|realm|beacon|a web of|unravel a conspiracy|powerful forces|silence anyone|unraveling the circumstances|unfolds|tragedy unfolds|before another tragedy|(?:better )?left undisturbed|roots reaching far beyond|far beyond|more than just|everything (?:she|he|they) holds? dear|hangs(?: precariously)? in the balance)\b|isn['’]t just\b[^.]{1,75}[.,;—–-]\s*it['’]s",
            plain_blurb,
            re.IGNORECASE,
        )
        bad_terms = sorted({t.lower() for t in [*slop_hits, *buzz_hits] if t.strip()})
        title_toks = {
            w.lower()
            for w in re.findall(r"[A-Za-z0-9']+", f"{book.title} {parsed.get('subtitle') or ''}")
            if len(w) >= 4
        }
        seed_has_missing = bool(re.search(r"\b(?:disappear\w*|missing)\b", seed, re.IGNORECASE))
        _geo_halluc_re = re.compile(r"\b(?:bayou|swamp|desert|outback)\b", re.IGNORECASE)
        _seen_2word_stems: set[tuple[str, str]] = set()
        overlap_kws: list[str] = []
        for raw_kw in parsed.get("keywords") or []:
            kw_s = str(raw_kw).strip()
            kw_low = kw_s.lower()
            toks_kw = [w.lower() for w in re.findall(r"[A-Za-z0-9']+", kw_s)]
            stem2 = (toks_kw[0], toks_kw[1]) if len(toks_kw) >= 2 else None
            dup_stem = bool(stem2 and stem2 in _seen_2word_stems)
            if stem2:
                _seen_2word_stems.add(stem2)
            bad_kw = (
                len(kw_s) < 20
                or any(w in title_toks for w in toks_kw)
                or (is_present_day and "historical" in kw_low)
                or (book.book_format.value != "novel" and re.search(r"\bnovel\b", kw_s, re.IGNORECASE))
                or bool(_geo_halluc_re.search(kw_s))
                or (not seed_has_missing and "disappearance" in kw_low)
                or ("thriller" not in genre_low and bool(re.search(r"\bthriller\b", kw_low)))
                or dup_stem
            )
            if bad_kw:
                overlap_kws.append(kw_s)
        bad_cats = [
            str(c)
            for c in (parsed.get("categories") or [])
            if (is_present_day and "historical" in str(c).lower())
            or "nonfiction" in str(c).lower()
        ]
        if bad_terms or overlap_kws or bad_cats:
            issues: list[str] = []
            if bad_terms:
                issues.append(f"Remove banned AI buzzwords/slop from blurb_html: {', '.join(bad_terms)}")
            if overlap_kws:
                issues.append(
                    f"Replace invalid, repetitive, <20-char, off-location, or title-overlapping keywords ({', '.join(sorted(title_toks))}): {', '.join(overlap_kws)}"
                )
            if bad_cats:
                issues.append(
                    f"Replace invalid/mismatching BISAC categories ({', '.join(bad_cats)}) with exact paths from the curated BISG list."
                )
            try:
                fix_res = await llm_client.complete(
                    system=(
                        "Revise this Amazon KDP JSON object to fix ONLY the listed blurb/keyword/category defects. "
                        "Keep exact <p> HTML tags, curated official BISG paths, real comp titles, and exact Canon character names. Return ONLY valid JSON."
                    ),
                    user="FIXES REQUIRED:\n- " + "\n- ".join(issues) + "\n\nCURRENT KDP JSON:\n" + json.dumps(parsed, ensure_ascii=False),
                    task="kdp",
                    max_tokens=1600,
                    creativity="low",
                    json_mode=True,
                )
                rep_parsed = parse_llm_json(fix_res.text)
                if isinstance(rep_parsed, dict) and rep_parsed.get("blurb_html"):
                    parsed = rep_parsed
            except Exception:
                pass
        # Guaranteed keyword length, hallucination & unique-stem guard
        seen_stems_final: set[tuple[str, str]] = set()

        def _kw_valid(kw_str: str, *, track_stem: bool = True) -> bool:
            s_kw = kw_str.strip()
            low_kw = s_kw.lower()
            if len(s_kw) < 20 or len(s_kw) > 50:
                return False
            if is_present_day and "historical" in low_kw:
                return False
            if book.book_format.value != "novel" and re.search(r"\bnovel\b", s_kw, re.IGNORECASE):
                return False
            if _geo_halluc_re.search(s_kw) or (not seed_has_missing and "disappearance" in low_kw):
                return False
            toks = [w.lower() for w in re.findall(r"[A-Za-z0-9']+", s_kw)]
            if any(w in title_toks for w in toks):
                return False
            if len(toks) >= 2:
                st = (toks[0], toks[1])
                if st in seen_stems_final:
                    return False
                if track_stem:
                    seen_stems_final.add(st)
            return True

        raw_kws_after = [str(kw).strip() for kw in (parsed.get("keywords") or []) if str(kw).strip()]
        has_genre = bool(genre_low.strip())
        needs_kw_expand = len(raw_kws_after) < 7 or any(
            len(sk) < 20
            or bool(_geo_halluc_re.search(sk))
            or (not seed_has_missing and "disappearance" in sk.lower())
            or (
                has_genre
                and (
                    any(w in title_toks for w in re.findall(r"[A-Za-z0-9']+", sk.lower()))
                    or (book.book_format.value != "novel" and bool(re.search(r"\bnovel\b", sk, re.IGNORECASE)))
                    or ("thriller" not in genre_low and bool(re.search(r"\bthriller\b", sk, re.IGNORECASE)))
                )
            )
            for sk in raw_kws_after
        )
        if not needs_kw_expand:
            st_check: set[tuple[str, str]] = set()
            for sk in raw_kws_after:
                tk = [w.lower() for w in re.findall(r"[A-Za-z0-9']+", sk)]
                if len(tk) >= 2:
                    pair = (tk[0], tk[1])
                    if pair in st_check:
                        needs_kw_expand = True
                        break
                    st_check.add(pair)

        if needs_kw_expand:
            existing_valid: list[str] = []
            for kw in raw_kws_after:
                sk = kw[:50]
                if (
                    ("thriller" in genre_low or "thriller" not in sk.lower())
                    and _kw_valid(sk)
                    and sk.lower() not in {x.lower() for x in existing_valid}
                ):
                    existing_valid.append(sk)
            merged = list(existing_valid)
            seen_low = {x.lower() for x in merged}
            for _attempt in range(2):
                if len(merged) >= 7:
                    break
                need_n = 7 - len(merged)
                try:
                    kw_res = await llm_client.complete(
                        system=(
                            f'Return ONLY a valid JSON object matching {{"keywords": ["phrase 1", ... {need_n} items]}} '
                            f"containing {need_n} NEW unique-stem Amazon KDP fiction buyer search phrases (24–48 characters EACH, zero generic 2-word filler, zero repeated starting words). "
                            f"Avoid repeating words from title/subtitle ({', '.join(sorted(title_toks))})"
                            + (" and NEVER use the word 'novel'." if book.book_format.value != "novel" else ".")
                            + (" NEVER use 'historical'." if is_present_day else "")
                            + (" NEVER use 'thriller' or off-setting locales." if "thriller" not in genre_low else "")
                        ),
                        user=(
                            f"BOOK: {book.title} ({prof.label})\n"
                            f"ALREADY ACCEPTED KEYWORDS (DO NOT REPEAT OR SHARE 2-WORD STARTS): {merged}\n"
                            f"CURRENT WEAK/DROPPED KEYWORDS: {raw_kws_after}\n"
                            f"SEED SUMMARY: {seed[:500]}"
                        ),
                        task="kdp",
                        max_tokens=500,
                        creativity="low",
                        json_mode=True,
                    )
                    kw_arr = parse_llm_json(kw_res.text)
                    if isinstance(kw_arr, dict):
                        kw_arr = kw_arr.get("keywords")
                    if isinstance(kw_arr, list):
                        for item in kw_arr:
                            cand_kw = str(item).strip()[:50]
                            if (
                                ("thriller" in genre_low or "thriller" not in cand_kw.lower())
                                and _kw_valid(cand_kw)
                                and cand_kw.lower() not in seen_low
                            ):
                                merged.append(cand_kw)
                                seen_low.add(cand_kw.lower())
                            if len(merged) >= 7:
                                break
                except Exception:
                    break
            if merged:
                parsed["keywords"] = merged[:7]
    return save_kdp_kit(
        bdir,
        parsed,
        title=book.title,
        is_children=is_children,
        is_present_day=is_present_day,
        is_mystery=is_mystery,
        is_maritime=is_maritime,
        author_name=author_name,
        protagonist_name=protagonist_name,
    )
