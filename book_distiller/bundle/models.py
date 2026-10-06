"""Versioned, closed-world portable snapshot manifest."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator
from book_distiller.core.models import CommonModel
from book_distiller.core.models.ai_tasks import Hash


class BundleFile(CommonModel):
    relative_path: str
    sha256: Hash
    size: int = Field(ge=0)
    category: Literal['canonical', 'verification', 'human', 'derived', 'source', 'metadata']


class ExternalRule(CommonModel):
    rule_id: UUID
    scope: Literal['global', 'type']
    book_type: str | None = None
    sha256: Hash
    applicable_workflows: list[str]


class BookBundleManifest(CommonModel):
    bundle_version: Literal['1.0'] = '1.0'
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by_version: str
    book_id: UUID
    edition_id: UUID
    title: str
    source_sha256: Hash
    source_mode: Literal['normalized_only', 'included']
    source_included: bool
    contains_source_derived_text: Literal[True] = True
    normalized_generation_id: UUID
    classification_id: UUID
    classification_hash: Hash
    chapter_generation_ids: dict[str, UUID]
    book_generation_id: UUID
    verification_generation_id: UUID
    reader_generation_id: UUID
    human_state_hash: Hash
    book_rule_hash: Hash
    external_rule_dependencies: list[ExternalRule] = Field(default_factory=list)
    quality_gate: Literal['pass', 'needs_review']
    ask_ready: Literal[True] = True
    versions: dict[str, str]
    pointers: dict[str, str]
    files: list[BundleFile]
    bundle_content_hash: Hash

    @model_validator(mode='after')
    def source_consistent(self):
        if self.source_included != (self.source_mode == 'included'):
            raise ValueError('Source mode/included mismatch')
        return self
