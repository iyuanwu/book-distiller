"""Parser-independent canonical document schemas. No third-party parser types."""
from enum import StrEnum
from typing import Literal, Self
from uuid import UUID
from pydantic import BaseModel, ConfigDict, Field, AwareDatetime, JsonValue, model_validator

SCHEMA_VERSION = "1.0"
NORMALIZER_VERSION = "1.0"


class DocumentModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class BlockType(StrEnum):
    TITLE = "title"
    HEADING = "heading"
    PARAGRAPH = "paragraph"
    LIST_ITEM = "list_item"
    QUOTE = "quote"
    TABLE = "table"
    FORMULA = "formula"
    CODE = "code"
    FIGURE = "figure"
    CAPTION = "caption"
    FOOTNOTE = "footnote"
    OTHER = "other"


class BoundingBox(DocumentModel):
    left: float
    top: float
    right: float
    bottom: float
    coordinate_origin: Literal["top-left", "bottom-left"]


class SourceSpan(DocumentModel):
    """Physical PDF pages; char offsets are half-open in normalized block text."""
    source_page_index: int | None = Field(default=None, ge=0)
    source_page_number: int | None = Field(default=None, ge=1)
    printed_page_label: str | None = None
    block_id: str = Field(pattern=r"^blk_\d{6,}$")
    char_start: int | None = Field(default=None, ge=0)
    char_end: int | None = Field(default=None, ge=0)
    bbox: BoundingBox | None = None
    parser: str
    parser_locator: str | None = None

    @model_validator(mode="after")
    def validate_ranges(self) -> Self:
        if (self.source_page_index is None) != (self.source_page_number is None):
            raise ValueError("Physical page index and number must both be present or null")
        if self.source_page_index is not None and self.source_page_number != self.source_page_index + 1:
            raise ValueError("Page number must equal zero-based index + 1")
        if (self.char_start is None) != (self.char_end is None):
            raise ValueError("Both character offsets are required together")
        if self.char_start is not None and self.char_end < self.char_start:
            raise ValueError("Invalid character range")
        return self


class NormalizedBlock(DocumentModel):
    block_id: str = Field(pattern=r"^blk_\d{6,}$")
    type: BlockType
    text: str
    chapter_id: str
    section_id: str | None = None
    order: int = Field(ge=1)
    source_spans: list[SourceSpan] = Field(default_factory=list)
    metadata: dict[str, JsonValue] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_spans(self) -> Self:
        for span in self.source_spans:
            if span.block_id != self.block_id:
                raise ValueError("SourceSpan must refer to this block")
            if span.char_end is not None and span.char_end > len(self.text):
                raise ValueError("SourceSpan exceeds normalized block text")
        return self


class NormalizedSection(DocumentModel):
    section_id: str
    chapter_id: str
    title: str
    heading_level: int = Field(ge=1)
    parent_section_id: str | None = None
    start_block_id: str


class NormalizedChapter(DocumentModel):
    chapter_id: str
    title: str
    synthetic: bool = False
    heading_level: int | None = None
    start_block_id: str
    sections: list[NormalizedSection] = Field(default_factory=list)


class SpecialContent(DocumentModel):
    """An index entry into the unified block stream; no text is duplicated."""
    content_id: str
    block_id: str
    caption: str | None = None
    asset_path: str | None = None


class ParserMetadata(DocumentModel):
    parser: str
    parser_version: str
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    input_format: str
    started_at: AwareDatetime
    completed_at: AwareDatetime
    status: Literal["success", "partial_success", "failed"]
    warnings: list[str] = Field(default_factory=list)
    errors: list[str] = Field(default_factory=list)
    options: dict[str, JsonValue] = Field(default_factory=dict)


class NormalizationMetadata(DocumentModel):
    source_raw_parser: str
    raw_artifact_version: str
    normalizer_version: str = NORMALIZER_VERSION
    normalized_at: AwareDatetime


class NormalizedBook(DocumentModel):
    """Small document index. Read blocks from blocks_path as JSONL."""
    schema_version: Literal["1.0"] = SCHEMA_VERSION
    book_id: UUID
    edition_id: UUID
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    title: str
    language: str | None = None
    chapters: list[NormalizedChapter]
    blocks_path: Literal["blocks.jsonl"] = "blocks.jsonl"
    block_count: int = Field(ge=0)
    figures: list[SpecialContent] = Field(default_factory=list)
    tables: list[SpecialContent] = Field(default_factory=list)
    formulas: list[SpecialContent] = Field(default_factory=list)
    code_blocks: list[SpecialContent] = Field(default_factory=list)
    parser_metadata: ParserMetadata
    normalization_metadata: NormalizationMetadata

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if self.source_sha256 != self.parser_metadata.source_sha256:
            raise ValueError("Parser source hash must match normalized source")
        chapter_ids = {chapter.chapter_id for chapter in self.chapters}
        if len(chapter_ids) != len(self.chapters):
            raise ValueError("Duplicate chapter IDs")
        for chapter in self.chapters:
            previous: set[str] = set()
            for section in chapter.sections:
                if section.chapter_id != chapter.chapter_id or section.section_id in previous:
                    raise ValueError("Invalid section identity")
                if section.parent_section_id and section.parent_section_id not in previous:
                    raise ValueError("Section parent must precede its child in the same chapter")
                previous.add(section.section_id)
        return self
