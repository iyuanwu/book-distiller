"""Chapter-scoped knowledge. References express provenance, not verified fidelity."""
from datetime import datetime, timezone
from enum import StrEnum
from typing import Annotated, Literal, Self
from uuid import UUID
from pydantic import Field, AwareDatetime, model_validator
from book_distiller.core.models.normalized import DocumentModel
from book_distiller.core.models.ai_tasks import Hash, EngineMetadata

CHUNKER_VERSION = 'analysis-chunker-v1'
Score = Annotated[float, Field(ge=0, le=1, strict=True, allow_inf_nan=False)]
ShortText = Annotated[str, Field(min_length=1, max_length=1200, pattern=r'\S')]
Term = Annotated[str, Field(min_length=1, max_length=80, pattern=r'\S')]
ChapterID = Annotated[str, Field(pattern=r'^ch_\d{4,}$')]


class ChunkBudget(DocumentModel):
    max_chars: int = Field(default=10000, ge=1000, le=15000)
    estimated_max_tokens: int = Field(default=3500, ge=334, le=6000)
    max_blocks: int = Field(default=30, ge=2, le=100)
    overlap_blocks: int = Field(default=1, ge=0, le=2)


class AnalysisChunk(DocumentModel):
    chunk_id: str
    chapter_id: ChapterID
    section_ids: list[str]
    primary_blocks: list[str] = Field(min_length=1)
    context_blocks: list[str] = Field(default_factory=list)
    first_order: int
    last_order: int
    chars: int
    estimated_tokens: int
    oversized: bool = False
    warnings: list[str] = Field(default_factory=list)
    chunker_version: Literal['analysis-chunker-v1'] = CHUNKER_VERSION


class ChunkManifest(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    chunker_version: Literal['analysis-chunker-v1'] = CHUNKER_VERSION
    chapter_id: ChapterID
    normalized_document_hash: Hash
    budget: ChunkBudget
    chunks: list[AnalysisChunk]


class ClaimType(StrEnum):
    FACT='fact'
    CLAIM='claim'
    PRINCIPLE='principle'
    DEFINITION='definition'
    METHOD='method'
    RULE='rule'
    ARGUMENT='argument'
    EXAMPLE='example'
    OBSERVATION='observation'
    RECOMMENDATION='recommendation'
    INTERPRETATION='interpretation'


class AtomType(StrEnum):
    CONCEPT='concept'
    PRINCIPLE='principle'
    METHOD='method'
    ARGUMENT='argument'
    EXAMPLE='example'
    RULE='rule'
    FRAMEWORK='framework'
    OBSERVATION='observation'


class BlockEvidence(DocumentModel):
    block_id: str = Field(pattern=r'^blk_\d{6,}$')


class ClaimDraft(DocumentModel):
    statement: ShortText
    claim_type: ClaimType
    concept_terms: list[Term] = Field(default_factory=list, max_length=12)
    importance: Score
    confidence: Score
    evidence: list[BlockEvidence] = Field(min_length=1, max_length=12)
    source_type: Literal['source'] = 'source'

    @model_validator(mode='after')
    def unique(self) -> Self:
        if len({e.block_id for e in self.evidence}) != len(self.evidence):
            raise ValueError('Duplicate evidence blocks')
        if len(set(self.concept_terms)) != len(self.concept_terms):
            raise ValueError('Duplicate concept terms')
        return self


class AtomDraft(DocumentModel):
    title: Annotated[str, Field(min_length=1, max_length=160, pattern=r'\S')]
    summary: ShortText
    atom_type: AtomType
    claim_ids: list[str] = Field(min_length=1, max_length=64)
    concept_terms: list[Term] = Field(default_factory=list, max_length=12)
    # Public reasons/examples in the source Claims, never model-private reasoning.
    reasoning: list[ShortText] = Field(default_factory=list, max_length=8)
    examples: list[ShortText] = Field(default_factory=list, max_length=8)
    importance: Score
    confidence: Score
    source_type: Literal['source'] = 'source'

    @model_validator(mode='after')
    def unique(self) -> Self:
        if len(set(self.claim_ids)) != len(self.claim_ids):
            raise ValueError('Duplicate claim IDs')
        if len(set(self.concept_terms)) != len(self.concept_terms):
            raise ValueError('Duplicate concept terms')
        return self


class KnowledgeBinding(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    task_id: UUID
    book_id: UUID
    edition_id: UUID
    generation_id: UUID
    chapter_id: ChapterID
    context_hash: Hash
    source_sha256: Hash
    normalized_schema_version: str
    normalized_document_hash: Hash
    classification_hash: Hash
    workflow_version: str
    prompt_version: str
    context_package_version: Literal['1.1'] = '1.1'
    chunker_version: Literal['analysis-chunker-v1'] = CHUNKER_VERSION
    source_parse_quality: Literal['pass','review_recommended']
    generated_by: EngineMetadata
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ClaimResult(KnowledgeBinding):
    chunk_id: str
    claims: list[ClaimDraft] = Field(max_length=64)


class AtomResult(KnowledgeBinding):
    claims_hash: Hash
    atoms: list[AtomDraft] = Field(max_length=128)


class AtomicClaim(ClaimDraft, KnowledgeBinding):
    claim_id: str
    chunk_id: str


class KnowledgeAtom(AtomDraft, KnowledgeBinding):
    atom_id: str
    claims_hash: Hash


class ChapterGeneration(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    generation_id: UUID
    book_id: UUID
    edition_id: UUID
    chapter_id: ChapterID
    normalized_document_hash: Hash
    classification_hash: Hash
    chunks_hash: Hash
    # Bind source code assets used by both workflows, including selected overlays.
    resources: dict[str, Hash]
    claim_tasks: dict[str, UUID] = Field(default_factory=dict)
    atom_task: UUID | None = None
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
