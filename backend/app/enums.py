from __future__ import annotations

from enum import Enum


class BookFormat(str, Enum):
    picture_book = "picture_book"
    early_reader = "early_reader"
    chapter_book = "chapter_book"
    short_story = "short_story"
    novella = "novella"
    novel = "novel"


class Audience(str, Enum):
    picture_book_3_7 = "picture_book_3_7"
    early_reader_5_8 = "early_reader_5_8"
    middle_grade_8_12 = "middle_grade_8_12"
    young_adult = "young_adult"
    adult = "adult"


class POV(str, Enum):
    first_person = "first_person"
    third_limited = "third_limited"
    third_omniscient = "third_omniscient"
    second_person = "second_person"


class Tense(str, Enum):
    past = "past"
    present = "present"


class WritingStyle(str, Enum):
    storybook_warm = "storybook_warm"
    whimsical_fable = "whimsical_fable"
    middle_grade_adventure = "middle_grade_adventure"
    literary_contemporary = "literary_contemporary"
    cozy_mystery = "cozy_mystery"
    psychological_thriller = "psychological_thriller"
    historical_immersive = "historical_immersive"
    romantic_banter = "romantic_banter"
    domestic_suspense = "domestic_suspense"
    gothic_atmospheric = "gothic_atmospheric"
    high_epic = "high_epic"
    cyberpunk_noir = "cyberpunk_noir"
    anthropological_sf = "anthropological_sf"
    hard_sf_clean = "hard_sf_clean"
    satirical_wit = "satirical_wit"
    visceral_grit = "visceral_grit"
    poetic_lyrical = "poetic_lyrical"
    pulp_kinetic = "pulp_kinetic"
    custom = "custom"


class BookStatus(str, Enum):
    idea = "idea"
    planning = "planning"
    drafting = "drafting"
    polishing = "polishing"
    ready = "ready"


class ChapterStatus(str, Enum):
    pending = "pending"
    drafted = "drafted"
    revising = "revising"
    polished = "polished"
    accepted = "accepted"


class JobStatus(str, Enum):
    queued = "queued"
    running = "running"
    paused = "paused"
    done = "done"
    error = "error"
    cancelled = "cancelled"


class ProviderName(str, Enum):
    anthropic = "anthropic"
    openai = "openai"
    gemini = "gemini"
    openrouter = "openrouter"
    lmstudio = "lmstudio"
    ollama = "ollama"
