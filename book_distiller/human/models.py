"""Generation-bound, validated human actions; system identities are never editable."""
from typing import Literal
from uuid import UUID, uuid4
from datetime import datetime, timezone
from pydantic import Field, AwareDatetime
from book_distiller.core.models import CommonModel
from book_distiller.core.models.ai_tasks import Hash


class TargetRef(CommonModel):
    object_type: Literal['classification','concept','atomic_claim','knowledge_atom','core_idea','mental_model','meta_principle','book','chapter','citation']
    generation_id: UUID
    object_id: str = Field(min_length=1, max_length=200)
    chapter_id: str | None = Field(default=None, pattern=r'^ch_\d{4,}$')


class HumanAction(CommonModel):
    schema_version: Literal['1.0'] = '1.0'
    override_id: UUID = Field(default_factory=uuid4)
    action: Literal['edit','verify','lock','unlock']
    target_ref: TargetRef
    base_object_hash: Hash
    patch: dict = Field(default_factory=dict)
    reason: str = Field(default='', max_length=2000)
    created_by: Literal['user'] = 'user'
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class UserNote(CommonModel):
    schema_version: Literal['1.0'] = '1.0'
    note_id: UUID = Field(default_factory=uuid4)
    target_ref: TargetRef
    text: str = Field(min_length=1, max_length=10000)
    tags: list[str] = Field(default_factory=list, max_length=24)
    locked: bool = False
    source_layer: Literal['user'] = 'user'
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    updated_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))


class HumanRule(CommonModel):
    rule_id: UUID = Field(default_factory=uuid4)
    scope: Literal['global','type','book']
    book_type: str | None = None
    applicable_workflows: list[str] = Field(min_length=1)
    instruction: str = Field(min_length=1, max_length=2000)
    reason: str = Field(default='', max_length=2000)
    enabled: bool = True
    priority: int = Field(default=0, ge=0, le=100)
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
