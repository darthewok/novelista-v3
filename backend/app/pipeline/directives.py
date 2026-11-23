from __future__ import annotations

from app.enums import Audience, POV, Tense, WritingStyle
from app.formats import AUDIENCE_GRADE_BANDS, get_profile
from app.store import Book

AUDIENCE_NOTES: dict[Audience, str] = {
    Audience.picture_book_3_7: (
        "Picture book for ages 3-7 (read aloud by adults). Concrete sensory words, playful rhythm, "
        "short crystal-clear sentences, gentle curiosity, emotional warmth, zero grimdark cynicism."
    ),
    Audience.early_reader_5_8: (
        "Early reader for ages 5-8. Simple direct syntax, vivid visual action, light humor, "
        "clear cause-and-effect, encouraging and age-safe stakes."
    ),
    Audience.middle_grade_8_12: (
        "Middle grade for ages 8-12. Accessible clear prose, lively dialogue, wonder and loyalty, "
        "real stakes scaled to young protagonists without gratuitous gore or adult nihilism."
    ),
    Audience.young_adult: (
        "Young adult (ages 13-18). Immediate emotional stakes, sharp character voice, momentum, "
        "and moral complexity without preachy exposition."
    ),
    Audience.adult: (
        "Adult trade fiction. Unflinching character psychology, layered subtext, varied syntax, "
        "mature emotional complexity, and natural dialogue."
    ),
}

POV_NOTES: dict[POV, str] = {
    POV.first_person: "Strict single first-person singular ('I') throughout EVERY chapter from the same protagonist — NEVER switch 'I' to another character mid-book.",
    POV.third_limited: "Close third-person limited ('he'/'she'/'they'), anchored in one viewpoint character per scene.",
    POV.third_omniscient: "Controlled third-person omniscient with an intentional, cohesive narrative observer.",
    POV.second_person: "Second-person ('you') maintaining immediacy without self-conscious gimmickry.",
}

TENSE_NOTES: dict[Tense, str] = {
    Tense.past: "Consistent simple past tense throughout all narration.",
    Tense.present: "Consistent literary present tense throughout all narration.",
}

STYLE_NOTES: dict[WritingStyle, str] = {
    WritingStyle.storybook_warm: (
        "Warm classic storybook (Beatrix Potter / Oliver Jeffers spirit): gentle rhythm, charming sensory details, "
        "musical read-aloud cadence, affectionate humor, visual white space."
    ),
    WritingStyle.whimsical_fable: (
        "Whimsical modern fable (Roald Dahl / Lemony Snicket lightness): spry phrasing, playful surprise, "
        "tactile oddities, wry delight without heavy-handed moralizing."
    ),
    WritingStyle.middle_grade_adventure: (
        "Spirited middle-grade adventure (Rick Riordan / Katherine Rundell pace): brisk action, earned loyalty, "
        "vivid world texture, clever problem-solving, humor under pressure."
    ),
    WritingStyle.literary_contemporary: (
        "Literary contemporary realism (Anne Tyler / Kazuo Ishiguro restraint): precise domestic and physical detail, "
        "subtext carried by ordinary gestures, unhurried emotional accuracy."
    ),
    WritingStyle.cozy_mystery: (
        "Cozy mystery (Richard Osman / Agatha Christie charm): intriguing puzzle clues, distinct village/community cast, "
        "dry wit, cozy sensory comfort contrasted with genuine clever deceit."
    ),
    WritingStyle.psychological_thriller: (
        "Taut psychological suspense (Tana French / Patricia Highsmith): creeping unease, social politeness masking deceit, "
        "sharp behavioral observation, earned dread without purple prose."
    ),
    WritingStyle.historical_immersive: (
        "Immersive historical fiction (Hilary Mantel / Patrick O'Brian texture): authentic material culture, trades, "
        "tools, weather, social hierarchy, period-clean idiom without anachronism."
    ),
    WritingStyle.romantic_banter: (
        "Character-driven romance (Emily Henry / Nora Ephron warmth): crisp banter, chemistry built on genuine vulnerabilities, "
        "emotional tenderness, sharp comic timing."
    ),
    WritingStyle.domestic_suspense: (
        "Domestic suburban suspense (Celeste Ng / Liane Moriarty): ordinary family routines hiding sharp fractures, "
        "revealing small lies, relatable social tension."
    ),
    WritingStyle.gothic_atmospheric: (
        "Atmospheric gothic (Shirley Jackson / Daphne du Maurier): unsettling architecture and weather mirroring obsession, "
        "restrained uncanny dread, crisp prose free of B-movie camp."
    ),
    WritingStyle.high_epic: (
        "Mythic epic fantasy (Ursula K. Le Guin / Robin Hobb weight): ancient custom, lived-in geography, costly choices, "
        "dignified cadence free of generic video-game lore."
    ),
    WritingStyle.cyberpunk_noir: (
        "Grounded cyberpunk noir (William Gibson precision): industrial decay, corporate paperwork mixed with street hardware, "
        "cool observant eye, unsentimental edge."
    ),
    WritingStyle.anthropological_sf: (
        "Anthropological science fiction (Le Guin / Octavia Butler): culture, ecology, and daily ritual shaped organically "
        "by technology or biology, empathetic moral inquiry."
    ),
    WritingStyle.hard_sf_clean: (
        "Clean hard science fiction (Andy Weir / Arthur C. Clarke clarity): plausible engineering constraints, "
        "clear spatial problem-solving, plainspoken momentum."
    ),
    WritingStyle.satirical_wit: (
        "Satirical social comedy (Terry Pratchett / Evelyn Waugh): crisp comic timing, bureaucratic absurdity, "
        "dryly observed human vanity, real heart underneath the joke."
    ),
    WritingStyle.visceral_grit: (
        "Gritty dramatic realism (George R.R. Martin / Cormac McCarthy austerity): physical consequence, mud and cold, "
        "sparse blunt dialogue, zero melodramatic filler."
    ),
    WritingStyle.poetic_lyrical: (
        "Lyrical literary prose (Ray Bradbury / Madeline Miller cadence): fresh concrete metaphors rooted in nature or craft, "
        "rhythmic clauses, never overwriting plain action."
    ),
    WritingStyle.pulp_kinetic: (
        "Kinetic noir/pulp (Elmore Leonard / Raymond Chandler punch): fast scenes, crooked idioms, sharp physical beats, "
        "minimal throat-clearing."
    ),
    WritingStyle.custom: "Custom author style (follow the user's Custom Style specification carefully).",
}


def per_unit_word_target(book: Book) -> int | None:
    if not book.target_words:
        return None
    count = book.chapter_count or len(book.chapters)
    if not count or count <= 0:
        prof = get_profile(book.book_format)
        count = prof.default_units
    return max(20, round(book.target_words / count))


def build_directives(book: Book) -> str:
    prof = get_profile(book.book_format)
    g_lo, g_hi = AUDIENCE_GRADE_BANDS.get(book.audience, (5.0, 12.0))
    per_unit = per_unit_word_target(book)
    style_desc = STYLE_NOTES.get(book.writing_style, "")
    if book.custom_style.strip():
        style_desc = (
            f"{style_desc}\n  Custom style guidance: {book.custom_style.strip()}"
            if book.writing_style != WritingStyle.custom
            else book.custom_style.strip()
        )

    lines = [
        "## CREATIVE DIRECTIVES",
        f"- **Format:** {prof.label} ({prof.unit_label_plural})",
        f"- **Language:** {book.language} (write 100% in {book.language}; never mix foreign scripts unless quoted in-world)",
        f"- **Audience:** {AUDIENCE_NOTES.get(book.audience, book.audience.value)} [Target reading level: Flesch-Kincaid Grade {g_lo:.1f}-{g_hi:.1f}]",
        f"- **Point of View:** {POV_NOTES.get(book.pov, book.pov.value)}",
        f"- **Narrative Tense:** {TENSE_NOTES.get(book.tense, book.tense.value)}",
        f"- **Prose Style:** {style_desc}",
    ]
    if book.genre.strip():
        lines.append(f"- **Genre / Shelf:** {book.genre.strip()}")
    if book.target_words:
        unit_count = book.chapter_count or len(book.chapters) or prof.default_units
        lines.append(
            f"- **Length Target:** ~{book.target_words:,} total words across {unit_count} {prof.unit_label_plural.lower()}"
            + (f" (~{per_unit:,} words per {prof.unit_label.lower()})" if per_unit else "")
        )
    lines.append(
        "- **Cultural & Spelling Consistency:** Keep regional institutions, laws, geography, and spelling conventions (UK vs. US) 100% consistent throughout."
    )
    return "\n".join(lines)


def clamp_max_tokens(per_unit_words: int | None, *, minimum: int = 3500, maximum: int = 24000) -> int:
    if not per_unit_words:
        return 8000
    # ~1.35 tokens/word + generous headroom so chapters never cut off mid-sentence
    est = int(per_unit_words * 2.3) + 800
    return max(minimum, min(maximum, est))
