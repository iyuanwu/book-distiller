"""Evaluation contracts, separate from immutable Knowledge Objects."""
from datetime import datetime,timezone
from typing import Literal,Self
from uuid import UUID
from pydantic import Field,AwareDatetime,model_validator
from book_distiller.core.models.normalized import DocumentModel,SourceSpan
from book_distiller.core.models.knowledge import Score,ShortText
from book_distiller.core.models.ai_tasks import Hash,EngineMetadata

ObjectType=Literal['atomic_claim','knowledge_atom','core_idea','mental_model','meta_principle']
Strength=Literal['strong','moderate','weak','insufficient']
Verdict=Literal['supported','partially_supported','unsupported','contradicted']
IssueType=Literal['UNSUPPORTED_CLAIM','CONTRADICTED_CLAIM','OVERCLAIM','WEAK_EVIDENCE','BROKEN_CITATION','MAJOR_OMISSION','DUPLICATE_KNOWLEDGE','CONCEPT_DISTORTION','SYNTHESIS_OVERREACH','LOW_TRACEABILITY','LOW_CHAPTER_COVERAGE']

class ObjectRef(DocumentModel):
    object_type:ObjectType
    object_id:str
    book_id:UUID
    edition_id:UUID
    chapter_id:str|None=None
    chapter_generation_id:UUID|None=None
    book_generation_id:UUID|None=None

    @model_validator(mode='after')
    def bound(self)->Self:
        if self.object_type in {'atomic_claim','knowledge_atom'}:
            if not self.chapter_id or not self.chapter_generation_id: raise ValueError('Chapter object requires a generation')
        elif not self.book_generation_id: raise ValueError('Book object requires a generation')
        return self

class Citation(DocumentModel):
    schema_version:Literal['1.0']='1.0'
    citation_id:str=Field(pattern=r'^cit_[0-9a-f]{32}$')
    book_id:UUID
    edition_id:UUID
    normalized_generation_id:UUID
    block_id:str
    char_start:int=Field(ge=0)
    char_end:int=Field(ge=0)
    page_index:int|None=None
    page_number:int|None=None
    parser:str
    parser_locator:str|None=None
    source_spans:list[SourceSpan]=Field(min_length=1)
    context_block_ids:list[str]=Field(default_factory=list)
    text_hash:Hash

    @model_validator(mode='after')
    def ranges(self)->Self:
        if self.char_end<self.char_start: raise ValueError('Invalid Citation range')
        if (self.page_index is None)!=(self.page_number is None): raise ValueError('Page pair required')
        if self.page_index is not None and (self.page_index<0 or self.page_number!=self.page_index+1): raise ValueError('Invalid page pair')
        if any(s.block_id!=self.block_id for s in self.source_spans):raise ValueError('SourceSpan block mismatch')
        return self

class AssessmentDraft(DocumentModel):
    object_ref:ObjectRef
    citation_ids:list[str]=Field(default_factory=list,max_length=128)
    supplemental_citation_ids:list[str]=Field(default_factory=list,max_length=128)
    evidence_strength:Strength
    fidelity_verdict:Verdict
    unsupported_aspects:list[ShortText]=Field(default_factory=list,max_length=12)
    reason_codes:list[Literal['context_insufficient','overclaim','unsupported','contradiction','synthesis_overreach','none']]=Field(default_factory=list,max_length=6)
    reviewer_confidence:Score
    verification_summary:ShortText

    @model_validator(mode='after')
    def consistency(self)->Self:
        if self.fidelity_verdict in {'unsupported','contradicted'} and self.evidence_strength!='insufficient':
            raise ValueError('Unsupported/contradicted requires insufficient evidence')
        if self.fidelity_verdict=='partially_supported' and self.evidence_strength not in {'moderate','weak'}:
            raise ValueError('Partial support requires moderate or weak evidence')
        if self.fidelity_verdict=='supported' and (not self.citation_ids or self.evidence_strength=='insufficient'):
            raise ValueError('Supported requires citations and non-insufficient evidence')
        if len(self.citation_ids)!=len(set(self.citation_ids)) or len(self.supplemental_citation_ids)!=len(set(self.supplemental_citation_ids)):
            raise ValueError('Duplicate citations')
        if not set(self.supplemental_citation_ids)<=set(self.citation_ids):raise ValueError('Supplemental citations must be selected')
        return self

class ReviewIssueDraft(DocumentModel):
    issue_type:IssueType
    severity:Literal['critical','major','minor','warning']
    object_refs:list[ObjectRef]=Field(default_factory=list,max_length=24)
    source_refs:list[str]=Field(default_factory=list,max_length=24)
    chapter_id:str|None=None
    summary:ShortText
    suggested_action:Literal['rerun_claim_extraction','rerun_chapter_atoms','rerun_book_synthesis','inspect_source','accept_as_low_confidence']
    reviewer_confidence:Score

class VerificationBinding(DocumentModel):
    schema_version:Literal['1.0']='1.0'
    task_id:UUID
    book_id:UUID
    edition_id:UUID
    verification_generation_id:UUID
    dependency_hash:Hash
    input_hash:Hash
    context_hash:Hash
    source_sha256:Hash
    normalized_schema_version:str
    normalized_document_hash:Hash
    classification_hash:Hash
    workflow_version:str
    prompt_version:str
    context_package_version:Literal['1.3']='1.3'
    source_parse_quality:Literal['pass','review_recommended']
    generated_by:EngineMetadata
    created_at:AwareDatetime=Field(default_factory=lambda:datetime.now(timezone.utc))

class VerifyResult(VerificationBinding):
    assessment:AssessmentDraft

class CoverageResult(VerificationBinding):
    reviewed_block_ids:list[str]=Field(min_length=1,max_length=128)
    issues:list[ReviewIssueDraft]=Field(default_factory=list,max_length=24)
    review_summary:ShortText

class QualityReviewResult(VerificationBinding):
    issues:list[ReviewIssueDraft]=Field(default_factory=list,max_length=24)
    review_summary:ShortText

class EvidenceAssessment(AssessmentDraft,VerificationBinding):
    assessment_id:str
    auto_recheck_attempted:bool=False
    original_citation_ids:list[str]
    lower_objects_reviewed:int
    lower_objects_available:int
    source_selection_complete:bool=True

class ReviewIssue(ReviewIssueDraft):
    issue_id:str
    auto_recheck_attempted:bool=False
    status:Literal['open','resolved']='open'
    task_id:UUID|None=None

VERIFY_NAMES={'atomic_claim':'verify_claim','knowledge_atom':'verify_atom','core_idea':'verify_core_idea','mental_model':'verify_mental_model','meta_principle':'verify_meta_principle'}
VERIFY_RESULT_MODELS={**{name:VerifyResult for name in [*VERIFY_NAMES.values(),'repair_evidence']},'review_chapter_coverage':CoverageResult,'review_quality':QualityReviewResult}
VERIFY_WORKFLOWS=set(VERIFY_RESULT_MODELS)

class QualityMetric(DocumentModel):
    numerator:int|float=Field(ge=0,allow_inf_nan=False)
    denominator:int|float|None=Field(default=None,ge=0,allow_inf_nan=False)
    value:int|float|None=Field(default=None,allow_inf_nan=False)
    formula:str=Field(min_length=1)

class QualityReport(DocumentModel):
    schema_version:Literal['1.0']='1.0'
    status:Literal['pass','needs_review','failed']
    metrics:dict[str,QualityMetric]
    thresholds:dict
    gate_reasons:list[str]
    verdict_counts:dict[Verdict,int]
    major_verdict_counts:dict[Verdict,int]
    major_objects_reviewed:int=Field(ge=0)
    major_objects_total:int=Field(ge=0)
    issue_counts:dict[IssueType,int]
    automatic_rechecks:int=Field(ge=0)
    remaining_review_items:list[str]
    provenance:dict
