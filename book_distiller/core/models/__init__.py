"""Minimal shared records; these do not implement workflow behavior."""
from datetime import datetime, timezone
from uuid import UUID, uuid4
from typing import Literal
from pydantic import AwareDatetime, BaseModel, ConfigDict, Field
from book_distiller.core.enums import BookStatus, DistillationMode, ErrorSeverity, StageStatus


class CommonModel(BaseModel):
    """Reject unknown fields and whitespace-only required text."""
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)


class BookIdentity(CommonModel):
    """Identity of a work, independent of edition."""
    book_id: UUID = Field(default_factory=uuid4)
    title: str = Field(min_length=1)
    authors: list[str] = Field(default_factory=list)
    slug: str | None = None
    metadata_status: Literal["provisional"] = "provisional"
    status: BookStatus = BookStatus.PENDING


class EditionIdentity(CommonModel):
    """Identity of an edition associated with a work."""
    edition_id: UUID = Field(default_factory=uuid4)
    book_id: UUID
    label: str | None = None
    language: str | None = None
    display_title: str | None = None
    publisher: str | None = None
    isbn: str | None = None


class RunMetadata(CommonModel):
    """User-level execution, retaining the original metadata contract."""
    run_id: UUID = Field(default_factory=uuid4)
    book_id: UUID
    edition_id: UUID
    operation: Literal['initial_pipeline','rerun','verification','render'] = 'initial_pipeline'
    requested_scope: dict = Field(default_factory=dict)
    resolved_plan: dict = Field(default_factory=dict)
    pipeline_graph_version: str = 'pipeline-graph-v1'
    source_sha256: str | None = None
    normalized_generation_id: str | None = None
    started_at: AwareDatetime | None = None
    completed_at: AwareDatetime | None = None
    parent_run_id: UUID | None = None
    resume_of_run_id: UUID | None = None
    failure_summary: str | None = None
    cursor: int = 0
    checkpoints: dict = Field(default_factory=dict)
    mode: DistillationMode = DistillationMode.STANDARD
    status: BookStatus = BookStatus.PENDING
    prompt_version: str | None = None
    schema_version: str | None = None
    pipeline_version: str | None = None
    skill_version: str | None = None
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class StageRecord(CommonModel):
    """Minimal stage state record."""
    run_id: UUID
    name: str = Field(min_length=1)
    status: StageStatus = StageStatus.PENDING


class ErrorRecord(CommonModel):
    """Structured error description with optional run association."""
    severity: ErrorSeverity
    message: str = Field(min_length=1)
    run_id: UUID | None = None
    stage: str | None = None


class TaskRecord(CommonModel):
    """Persistable task metadata; no queue scheduler is implemented."""
    task_id: UUID = Field(default_factory=uuid4)
    edition_id: UUID
    task_type: str = Field(min_length=1)
    status: StageStatus = StageStatus.PENDING
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
