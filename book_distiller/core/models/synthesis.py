"""Book-scoped knowledge contracts; structural provenance, not verified truth."""
from datetime import datetime, timezone
from typing import Annotated, Literal, Self
from uuid import UUID
from pydantic import Field, AwareDatetime, model_validator
from book_distiller.core.models.normalized import DocumentModel
from book_distiller.core.models.ai_tasks import Hash, EngineMetadata
from book_distiller.core.models.knowledge import Score, ShortText, Term, AtomDraft

BOOK_WORKFLOWS = {'normalize_concepts','build_core_ideas','build_mental_models','build_meta_principles','synthesis_reduce','reduce_chapter_atoms'}
ConceptID = Annotated[str, Field(pattern=r'^concept_[a-z0-9]+(?:[_-][a-z0-9]+)*$')]


class ConceptRelation(DocumentModel):
    target_concept_id: ConceptID
    relation: Literal['same_as','related_to']
    reason: ShortText


class CanonicalConcept(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    concept_id: ConceptID
    canonical_name: Term
    zh_name: Term | None = None
    aliases: list[Term] = Field(default_factory=list,max_length=24)
    source_terms: list[Term] = Field(min_length=1,max_length=64)
    atom_ids: list[str] = Field(default_factory=list)
    claim_ids: list[str] = Field(default_factory=list)
    relations: list[ConceptRelation] = Field(default_factory=list,max_length=12)
    confidence: Score
    human_verified: Literal[False] = False
    locked: Literal[False] = False


class ConceptRegistry(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    concepts: list[CanonicalConcept] = Field(max_length=256)


class AtomDecision(DocumentModel):
    decision: Literal['merge_atoms','related_to','potential_tension','contradicts','supports','depends_on']
    source_atom_ids: list[str] = Field(min_length=2,max_length=10000)
    canonical_concept_ids: list[ConceptID] = Field(default_factory=list,max_length=24)
    reason: ShortText
    confidence: Score


class CoreIdea(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    core_idea_id: str = Field(pattern=r'^idea_\d{3,}$')
    title: Term
    statement: ShortText
    atom_ids: list[str] = Field(min_length=1,max_length=10000)
    concept_ids: list[ConceptID] = Field(default_factory=list,max_length=24)
    supporting_chapters: list[str] = Field(default_factory=list)
    importance: Score
    confidence: Score
    promotion_reason: ShortText
    source_type: Literal['source'] = 'source'


class MentalModel(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    model_id: str = Field(pattern=r'^model_\d{3,}$')
    name: Term
    zh_name: Term | None = None
    description: ShortText
    core_idea_ids: list[str] = Field(default_factory=list,max_length=64)
    atom_ids: list[str] = Field(default_factory=list,max_length=10000)
    concept_ids: list[ConceptID] = Field(default_factory=list,max_length=24)
    mechanism: ShortText
    when_to_use: list[ShortText] = Field(default_factory=list,max_length=8)
    limitations: list[ShortText] = Field(default_factory=list,max_length=8)
    promotion_reason: ShortText
    confidence: Score
    source_type: Literal['source'] = 'source'

    @model_validator(mode='after')
    def support(self) -> Self:
        if not self.core_idea_ids and not self.atom_ids:
            raise ValueError('Model requires lower-level support')
        return self


class MetaPrinciple(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    principle_id: str = Field(pattern=r'^principle_\d{3,}$')
    title: Term
    statement: ShortText
    core_idea_ids: list[str] = Field(default_factory=list,max_length=64)
    model_ids: list[str] = Field(default_factory=list,max_length=32)
    concept_ids: list[ConceptID] = Field(default_factory=list,max_length=24)
    supporting_chapters: list[str] = Field(min_length=2)
    promotion_reason: ShortText
    confidence: Score
    source_type: Literal['source'] = 'source'

    @model_validator(mode='after')
    def support(self) -> Self:
        if len(set(self.core_idea_ids))<2 and len(set(self.model_ids))<2:
            raise ValueError('Principle needs at least two distinct Ideas or two Models')
        return self


class IntermediateGroup(DocumentModel):
    title: Term
    summary: Annotated[str,Field(min_length=1,max_length=600,pattern=r'\S')]
    source_ids: list[str] = Field(min_length=1,max_length=24)
    # Reasons remain public source qualifications, never private reasoning.
    caveats: list[Term] = Field(default_factory=list,max_length=6)


class SynthesisBinding(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    task_id: UUID
    book_id: UUID
    edition_id: UUID
    generation_id: UUID
    context_hash: Hash
    dependency_hash: Hash
    input_hash: Hash
    source_sha256: Hash
    normalized_schema_version: str
    normalized_document_hash: Hash
    classification_hash: Hash
    workflow_version: str
    prompt_version: str
    context_package_version: Literal['1.2'] = '1.2'
    source_parse_quality: Literal['pass','review_recommended']
    generated_by: EngineMetadata
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class ReduceResult(SynthesisBinding):
    groups: list[IntermediateGroup] = Field(min_length=1,max_length=12)


class ConceptsResult(SynthesisBinding):
    concepts: list[CanonicalConcept] = Field(max_length=256)


class IdeasResult(SynthesisBinding):
    core_ideas: list[CoreIdea] = Field(max_length=64)
    decisions: list[AtomDecision] = Field(default_factory=list,max_length=128)


class ModelsResult(SynthesisBinding):
    mental_models: list[MentalModel] = Field(max_length=32)


class PrinciplesResult(SynthesisBinding):
    meta_principles: list[MetaPrinciple] = Field(max_length=16)


class ReducedAtomsResult(SynthesisBinding):
    # Final chapter materialization expands group references to canonical Claim IDs.
    atoms: list[AtomDraft] = Field(max_length=128)


RESULT_MODELS = {'synthesis_reduce':ReduceResult,'normalize_concepts':ConceptsResult,
    'build_core_ideas':IdeasResult,'build_mental_models':ModelsResult,
    'build_meta_principles':PrinciplesResult,'reduce_chapter_atoms':ReducedAtomsResult}


class AtomReference(DocumentModel):
    chapter_id: str
    chapter_generation_id: UUID
    atom_id: str


class ClaimReference(DocumentModel):
    chapter_id: str
    chapter_generation_id: UUID
    claim_id: str


class PublishedConcept(CanonicalConcept):
    atom_refs: list[AtomReference]
    claim_refs: list[ClaimReference]


class PublishedCoreIdea(CoreIdea):
    atom_refs: list[AtomReference]


class PublishedMentalModel(MentalModel):
    atom_refs: list[AtomReference]


class PublishedAtomDecision(AtomDecision):
    source_atom_refs: list[AtomReference]


class BookMemory(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    builder_version: Literal['book-memory-v1.0'] = 'book-memory-v1.0'
    identity: dict
    classification: dict
    outline: list[dict]
    concepts: list[dict]
    core_ideas: list[dict]
    mental_models: list[dict]
    meta_principles: list[dict]
    unresolved_conflicts: list[dict]
    max_chars: int = Field(gt=0)
    selected_chars: int = Field(ge=0)
    omitted: dict[str,int]
    book_memory_hash: Hash


class PublishedConceptRegistry(DocumentModel):
    schema_version: Literal['1.0'] = '1.0'
    generation_id: UUID
    concepts: list[PublishedConcept] = Field(max_length=256)
