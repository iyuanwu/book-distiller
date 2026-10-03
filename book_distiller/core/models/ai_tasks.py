"""File-based AI protocol models; no model client or hidden reasoning fields."""
from datetime import datetime, timezone
from typing import Annotated, Literal, Self
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator
from book_distiller.core.enums import BookType
from book_distiller.core.models.normalized import DocumentModel, BlockType

Hash = Annotated[str, Field(pattern=r"^[0-9a-f]{64}$")]
Tag = Annotated[str, Field(pattern=r"^[a-z0-9]+(?:-[a-z0-9]+)*$", max_length=60)]
PROTOCOL_VERSION = "1.0"
CONTEXT_VERSION = "1.0"
BUILDER_VERSION = "1.0"


class ContextBudget(DocumentModel):
    max_chars: int = Field(default=30000, ge=4000, le=200000)
    estimated_max_tokens: int = Field(default=10000, ge=1000, le=100000)
    chars_per_token: float = Field(default=3.0, ge=1, le=8, allow_inf_nan=False)
    max_blocks: int = Field(default=40, ge=1, le=200)
    max_outline_items: int = Field(default=80, ge=0, le=500)


class SelectionSpec(DocumentModel):
    policy: Literal["classification-v1", "explicit-v1"] = "classification-v1"
    metadata: bool = True
    outline: bool = True
    block_ids: list[str] = Field(default_factory=list)
    chapter_ids: list[str] = Field(default_factory=list)
    section_ids: list[str] = Field(default_factory=list)
    block_range: tuple[int, int] | None = None

    @model_validator(mode="after")
    def valid_range(self) -> Self:
        if self.block_range and not 1 <= self.block_range[0] <= self.block_range[1]:
            raise ValueError("block_range is an inclusive 1-based order interval")
        if self.policy == "classification-v1" and (self.block_ids or self.chapter_ids or self.section_ids or self.block_range):
            raise ValueError("Use explicit-v1 for explicit block selectors")
        return self


class ContextBlock(DocumentModel):
    block_id: str
    type: BlockType
    chapter_id: str
    section_id: str | None
    order: int
    text: str
    source_pages: list[int]
    source_locators: list[str]
    original_chars: int
    excerpt_truncated: bool = False


class OutlineItem(DocumentModel):
    chapter_id: str
    section_id: str | None = None
    parent_section_id: str | None = None
    title: str
    synthetic: bool = False


class DocumentFingerprint(DocumentModel):
    parse_task_id: UUID
    schema_version: str
    normalizer_version: str
    book_sha256: Hash
    blocks_sha256: Hash
    quality_sha256: Hash
    document_hash: Hash


class SelectionRecord(DocumentModel):
    spec: SelectionSpec
    included_chapters: list[str]
    included_blocks: list[str]
    omitted_blocks: int
    truncated: bool
    omitted_outline_items: int


class BudgetRecord(DocumentModel):
    limits: ContextBudget
    selected_chars: int = 0
    estimated_tokens: int = 0
    truncated: bool = False
    total_available_blocks: int
    selected_blocks: int
    # The larger of compact context.json and its deterministic context.md rendering.
    accounting: Literal["max(canonical_json_chars,markdown_chars)"] = "max(canonical_json_chars,markdown_chars)"


class ContextPackage(DocumentModel):
    package_version: Literal["1.0"] = CONTEXT_VERSION
    task_id: UUID
    task_type: str
    book_id: UUID
    edition_id: UUID
    title: str | None
    language: str | None
    source_sha256: Hash
    source_parse_quality: Literal["pass", "review_recommended"]
    normalized_document: DocumentFingerprint
    selection: SelectionRecord
    outline: list[OutlineItem]
    blocks: list[ContextBlock]
    constraints: list[str]
    budget: BudgetRecord
    builder_version: Literal["1.0"] = BUILDER_VERSION
    workflow_version: str
    prompt_version: str
    context_hash: Hash


class AIRequest(DocumentModel):
    protocol_version: Literal["1.0"] = PROTOCOL_VERSION
    task_id: UUID
    task_type: Literal["classify_book", "extract_claims", "build_chapter_atoms"] = "classify_book"
    book_id: UUID
    edition_id: UUID
    source_sha256: Hash
    normalized_document: DocumentFingerprint
    workflow_version: str
    prompt_version: str
    context_package_version: Literal["1.0", "1.1"] = CONTEXT_VERSION
    context_hash: Hash
    workflow_sha256: Hash
    prompt_sha256: Hash
    output_schema_sha256: Hash
    created_at: AwareDatetime


class ClassificationEvidence(DocumentModel):
    block_id: str = Field(pattern=r"^blk_\d{6,}$")
    supports: list[BookType] = Field(min_length=1, max_length=4)
    note: str = Field(min_length=1, max_length=300)


class EngineMetadata(DocumentModel):
    engine: Literal["codex"] = "codex"


class BookClassification(DocumentModel):
    """Canonical classification; AI supplies judgment, Core validates all bindings."""
    schema_version: Literal["1.0"] = "1.0"
    task_id: UUID
    book_id: UUID
    edition_id: UUID
    context_hash: Hash
    source_sha256: Hash
    normalized_schema_version: str
    normalized_document_hash: Hash
    workflow_version: str
    prompt_version: str
    context_package_version: Literal["1.0"] = CONTEXT_VERSION
    source_parse_quality: Literal["pass", "review_recommended"]
    primary_type: BookType
    secondary_types: list[BookType] = Field(default_factory=list, max_length=3)
    tags: list[Tag] = Field(default_factory=list, max_length=12)
    confidence: float = Field(ge=0, le=1, strict=True, allow_inf_nan=False)
    evidence: list[ClassificationEvidence] = Field(min_length=1, max_length=20)
    rationale_summary: str = Field(min_length=1, max_length=1000)
    generated_by: EngineMetadata
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    human_verified: Literal[False] = False
    locked: Literal[False] = False

    @model_validator(mode="after")
    def validate_classification(self) -> Self:
        if self.primary_type in self.secondary_types or len(set(self.secondary_types)) != len(self.secondary_types):
            raise ValueError("secondary_types must be unique and exclude primary_type")
        if len(set(self.tags)) != len(self.tags):
            raise ValueError("tags must be unique")
        if not self.rationale_summary.strip():
            raise ValueError("rationale_summary must not be blank")
        declared = {self.primary_type, *self.secondary_types}
        evidence_ids: set[str] = set()
        for evidence in self.evidence:
            if evidence.block_id in evidence_ids or not evidence.note.strip():
                raise ValueError("Evidence block IDs must be unique and notes nonblank")
            evidence_ids.add(evidence.block_id)
            if not set(evidence.supports).issubset(declared) or len(set(evidence.supports)) != len(evidence.supports):
                raise ValueError("Evidence supports must be unique declared book types")
        if not any(self.primary_type in evidence.supports for evidence in self.evidence):
            raise ValueError("At least one evidence entry must support primary_type")
        return self
