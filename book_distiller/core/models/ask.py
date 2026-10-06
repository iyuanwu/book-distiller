"""BookAnswer 1.0: derived answers, never canonical knowledge."""
from datetime import datetime, timezone
from typing import Literal, Self
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator
from book_distiller.core.models.normalized import DocumentModel
from book_distiller.core.models.ai_tasks import Hash, EngineMetadata

AskIntent = Literal['fact', 'concept', 'relationship', 'comparison', 'chapter', 'why', 'source_location', 'application', 'meta']


class AnswerSegment(DocumentModel):
    layer: Literal['source', 'ai', 'user']
    text: str = Field(min_length=1, max_length=6000, pattern=r'\S')
    knowledge_refs: list[str] = Field(default_factory=list, max_length=64)
    citation_refs: list[str] = Field(default_factory=list, max_length=64)
    human_note_refs: list[UUID] = Field(default_factory=list, max_length=24)

    @model_validator(mode='after')
    def provenance(self) -> Self:
        if self.layer == 'source' and (not self.knowledge_refs or not self.citation_refs):
            raise ValueError('Source statements require Knowledge and Citation refs')
        if self.layer == 'user' and (not self.human_note_refs or self.citation_refs):
            raise ValueError('User statements require Note refs, not Source citations')
        if self.layer != 'user' and self.human_note_refs:
            raise ValueError('User Notes cannot be presented as Source or AI evidence')
        return self


class BookAnswer(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    answer_id: UUID
    book_id: UUID
    edition_id: UUID
    question: str = Field(min_length=1, max_length=4000, pattern=r'\S')
    answer: str = Field(min_length=1, max_length=16000, pattern=r'\S')
    answer_type: AskIntent
    segments: list[AnswerSegment] = Field(min_length=1, max_length=16)
    knowledge_refs: list[str] = Field(default_factory=list, max_length=64)
    citation_refs: list[str] = Field(default_factory=list, max_length=64)
    human_note_refs: list[UUID] = Field(default_factory=list, max_length=24)
    quality_warnings: list[str] = Field(default_factory=list, max_length=64)
    confidence: Literal['high', 'medium', 'low']
    insufficient_evidence: bool
    out_of_scope: bool = False
    book_generation_id: UUID
    verification_generation_id: UUID
    human_state_hash: Hash
    question_hash: Hash
    classification_hash: Hash
    applicable_rule_hash: Hash
    task_id: UUID
    context_hash: Hash
    source_sha256: Hash
    normalized_schema_version: str
    normalized_document_hash: Hash
    workflow_version: str
    prompt_version: str
    context_package_version: Literal['1.4'] = '1.4'
    source_parse_quality: Literal['pass', 'review_recommended']
    generated_by: EngineMetadata
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))

    @model_validator(mode='after')
    def consistency(self) -> Self:
        if self.answer != '\n\n'.join(s.text for s in self.segments):
            raise ValueError('Answer must be the exact ordered projection of segments')
        for field in ('knowledge_refs', 'citation_refs', 'human_note_refs'):
            refs = getattr(self, field)
            if len(refs) != len(set(refs)) or set(refs) != {r for s in self.segments for r in getattr(s, field)}:
                raise ValueError(f'{field} must be the unique union of segment refs')
        if self.out_of_scope and not self.insufficient_evidence:
            raise ValueError('Out-of-scope requires insufficient evidence')
        if (self.insufficient_evidence or not self.citation_refs) and self.confidence == 'high':
            raise ValueError('High confidence requires sufficient cited evidence')
        return self
