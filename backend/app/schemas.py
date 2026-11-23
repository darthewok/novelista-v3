from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field, field_validator

from app.enums import (
    Audience,
    BookFormat,
    BookStatus,
    ChapterStatus,
    JobStatus,
    POV,
    Tense,
    WritingStyle,
)


class FrontMatterSchema(BaseModel):
    subtitle: str = ""
    author_name: str = ""
    series_name: str = ""
    series_number: str = ""
    copyright_year: str = ""
    imprint: str = ""
    dedication: str = ""
    epigraph: str = ""
    acknowledgments: str = ""
    author_bio: str = ""
    also_by: str = ""
    blurb: str = ""


class BookCreate(BaseModel):
    title: str = Field(default="Untitled", max_length=200)
    book_format: BookFormat = BookFormat.novel
    audience: Audience | None = None
    chapter_count: int | None = Field(default=None, ge=1, le=200)
    target_words: int | None = Field(default=None, ge=150, le=2_000_000)
    pov: POV = POV.third_limited
    tense: Tense = Tense.past
    writing_style: WritingStyle = WritingStyle.literary_contemporary
    custom_style: str = Field(default="", max_length=600)
    genre: str = Field(default="", max_length=120)
    language: str = Field(default="English", max_length=60)
    idea: str = Field(default="", max_length=20_000)
    front_matter: FrontMatterSchema | None = None


class BookPatch(BaseModel):
    title: str | None = Field(default=None, min_length=1, max_length=200)
    book_format: BookFormat | None = None
    audience: Audience | None = None
    chapter_count: int | None = Field(default=None, ge=1, le=200)
    target_words: int | None = Field(default=None, ge=150, le=2_000_000)
    pov: POV | None = None
    tense: Tense | None = None
    writing_style: WritingStyle | None = None
    custom_style: str | None = Field(default=None, max_length=600)
    genre: str | None = Field(default=None, max_length=120)
    language: str | None = Field(default=None, min_length=1, max_length=60)
    idea: str | None = Field(default=None, max_length=20_000)

    @field_validator("title", "book_format", "audience", "pov", "tense", "writing_style", "language")
    @classmethod
    def _reject_explicit_null(cls, v: Any) -> Any:
        if v is None:
            raise ValueError("Field cannot be null")
        return v


class ChapterSummaryOut(BaseModel):
    number: int
    title: str
    status: ChapterStatus
    word_count: int
    version: int
    tells: int
    error_count: int = 0
    warning_count: int = 0
    cleanliness_grade: str = ""
    pov: str = ""
    scene_count: int = 0
    has_illustration_prompt: bool = False


class ChapterOut(ChapterSummaryOut):
    text: str


class BookOut(BaseModel):
    id: str
    title: str
    status: BookStatus
    book_format: BookFormat
    audience: Audience
    chapter_count: int | None
    target_words: int | None
    pov: POV
    tense: Tense
    writing_style: WritingStyle
    custom_style: str
    genre: str
    language: str
    idea: str
    front_matter: FrontMatterSchema
    created_at: str
    updated_at: str
    chapters: list[ChapterSummaryOut]


class TextPayload(BaseModel):
    text: str


class SeedChoose(BaseModel):
    text: str | None = None
    index: int | None = Field(default=None, ge=1, le=20)


class GenerateOpts(BaseModel):
    choice: int | None = Field(default=None, ge=1, le=10)


class DraftOpts(BaseModel):
    polish: bool | None = None
    scene_cards: bool | None = None


class ReviseNotes(BaseModel):
    notes: str = Field(default="", max_length=8000)
    polish: bool = False


class SelectionRewriteOpts(BaseModel):
    start_char: int = Field(ge=0)
    end_char: int = Field(gt=0)
    action: str = Field(default="rewrite")  # rewrite | show_dont_tell | punch_dialogue | expand_sensory | tighten | custom
    instruction: str = Field(default="", max_length=2000)


class FixFindingOpts(BaseModel):
    finding_id: str
    line: int | None = None
    start_char: int | None = None
    end_char: int | None = None


class IgnoreFindingOpts(BaseModel):
    fingerprint: str = Field(min_length=1, max_length=400)


class AuditPriorityFixItem(BaseModel):
    chapter: int = Field(ge=1)
    action: str = Field(min_length=1, max_length=2000)


class AuditFixOpts(BaseModel):
    global_issues: list[str] = Field(default_factory=list, max_length=50)
    priority_fixes: list[AuditPriorityFixItem] = Field(default_factory=list, max_length=100)
    fix_all: bool = False
    rerun_audit: bool = True


class JobOut(BaseModel):
    id: str
    book_id: str
    kind: str
    status: JobStatus
    created_at: str
    finished_at: str | None = None
    error: str | None = None
    chapter: int | None = None
    batch_id: str | None = None
