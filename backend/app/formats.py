from __future__ import annotations

from dataclasses import dataclass

from app.enums import Audience, BookFormat


@dataclass(frozen=True)
class FormatProfile:
    key: BookFormat
    label: str
    unit_label: str             # "Spread" | "Part" | "Chapter"
    unit_label_plural: str
    default_units: int
    min_units: int
    max_units: int
    default_words: int
    min_words: int
    max_words: int
    min_unit_words: int         # Minimum words per chapter/spread for scanner
    max_unit_words: int
    default_audience: Audience
    optional_phases: tuple[str, ...]
    illustration_support: bool
    craft_profile: str          # "children" | "short" | "novel"
    default_trim: str           # KDP PDF trim size


FORMAT_PROFILES: dict[BookFormat, FormatProfile] = {
    BookFormat.picture_book: FormatProfile(
        key=BookFormat.picture_book,
        label="Children's Picture Book",
        unit_label="Spread",
        unit_label_plural="Spreads",
        default_units=12,
        min_units=8,
        max_units=20,
        default_words=550,
        min_words=150,
        max_words=1500,
        min_unit_words=15,
        max_unit_words=180,
        default_audience=Audience.picture_book_3_7,
        optional_phases=("world", "canon"),
        illustration_support=True,
        craft_profile="children",
        default_trim="8.5x8.5",
    ),
    BookFormat.early_reader: FormatProfile(
        key=BookFormat.early_reader,
        label="Early Reader",
        unit_label="Chapter",
        unit_label_plural="Chapters",
        default_units=6,
        min_units=3,
        max_units=12,
        default_words=2400,
        min_words=600,
        max_words=6000,
        min_unit_words=120,
        max_unit_words=900,
        default_audience=Audience.early_reader_5_8,
        optional_phases=("canon",),
        illustration_support=True,
        craft_profile="children",
        default_trim="5.5x8.5",
    ),
    BookFormat.chapter_book: FormatProfile(
        key=BookFormat.chapter_book,
        label="Chapter Book / Middle Grade",
        unit_label="Chapter",
        unit_label_plural="Chapters",
        default_units=10,
        min_units=5,
        max_units=30,
        default_words=18000,
        min_words=5000,
        max_words=45000,
        min_unit_words=350,
        max_unit_words=4000,
        default_audience=Audience.middle_grade_8_12,
        optional_phases=(),
        illustration_support=False,
        craft_profile="children",
        default_trim="5.5x8.5",
    ),
    BookFormat.short_story: FormatProfile(
        key=BookFormat.short_story,
        label="Short Story",
        unit_label="Part",
        unit_label_plural="Parts",
        default_units=3,
        min_units=1,
        max_units=6,
        default_words=4500,
        min_words=800,
        max_words=10000,
        min_unit_words=250,
        max_unit_words=5000,
        default_audience=Audience.adult,
        optional_phases=("world", "canon"),
        illustration_support=False,
        craft_profile="short",
        default_trim="5x8",
    ),
    BookFormat.novella: FormatProfile(
        key=BookFormat.novella,
        label="Novella",
        unit_label="Chapter",
        unit_label_plural="Chapters",
        default_units=7,
        min_units=4,
        max_units=18,
        default_words=18000,
        min_words=8000,
        max_words=45000,
        min_unit_words=450,
        max_unit_words=5500,
        default_audience=Audience.adult,
        optional_phases=(),
        illustration_support=False,
        craft_profile="novel",
        default_trim="5.5x8.5",
    ),
    BookFormat.novel: FormatProfile(
        key=BookFormat.novel,
        label="Full Novel",
        unit_label="Chapter",
        unit_label_plural="Chapters",
        default_units=20,
        min_units=6,
        max_units=120,
        default_words=65000,
        min_words=35000,
        max_words=250000,
        min_unit_words=500,
        max_unit_words=8000,
        default_audience=Audience.adult,
        optional_phases=(),
        illustration_support=False,
        craft_profile="novel",
        default_trim="6x9",
    ),
}


# Flesch-Kincaid grade-level ranges (inclusive soft target band) per audience.
AUDIENCE_GRADE_BANDS: dict[Audience, tuple[float, float]] = {
    Audience.picture_book_3_7: (0.0, 3.8),
    Audience.early_reader_5_8: (1.0, 5.0),
    Audience.middle_grade_8_12: (3.0, 7.5),
    Audience.young_adult: (4.5, 10.5),
    Audience.adult: (4.5, 14.0),
}


def get_profile(fmt: BookFormat | str) -> FormatProfile:
    if isinstance(fmt, str):
        fmt = BookFormat(fmt)
    return FORMAT_PROFILES[fmt]
