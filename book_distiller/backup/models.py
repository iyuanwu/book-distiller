"""Closed, versioned recovery manifest and relational export."""
from datetime import datetime, timezone
from typing import Literal
from uuid import UUID
from pydantic import AwareDatetime, Field, model_validator
from book_distiller.core.models import CommonModel, RunMetadata, TaskRecord
from book_distiller.core.models.ai_tasks import Hash
from book_distiller.bundle.models import BundleFile, ExternalRule


class BackupFile(BundleFile):
    category: Literal['canonical', 'verification', 'human', 'derived', 'source', 'metadata', 'runtime', 'ask']


class BookRow(CommonModel):
    book_id: UUID
    canonical_title: str
    slug: str
    metadata_status: Literal['provisional']
    status: Literal['pending','processing','paused','completed','failed','needs_review']
    created_at: AwareDatetime
    updated_at: AwareDatetime


class EditionRow(CommonModel):
    edition_id: UUID
    book_id: UUID
    display_title: str
    language: str | None
    publisher: str | None
    isbn: str | None
    source_sha256: Hash
    source_extension: str
    source_original_filename: str
    source_original_path: str
    source_stored_path: str
    source_file_size: int = Field(ge=0)
    copy_mode: Literal['copy','reference']
    manifest_version: Literal['1.0']
    created_at: AwareDatetime
    updated_at: AwareDatetime


class RunRow(CommonModel):
    run_id: UUID
    edition_id: UUID
    mode: Literal['fast','standard','deep']
    status: Literal['pending','processing','paused','completed','failed','needs_review']
    prompt_version: str | None
    schema_version: str | None
    pipeline_version: str | None
    skill_version: str | None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    execution_json: str | None


class RunTaskRow(CommonModel):
    run_id: UUID
    task_id: UUID
    stage: Literal['parse','classification','claims','atoms','book','verification','render']
    chapter_id: str | None


class DBExport(CommonModel):
    export_version: Literal['1.0'] = '1.0'
    sqlite_schema_version: Literal[2] = 2
    books: list[BookRow] = Field(min_length=1, max_length=1)
    editions: list[EditionRow] = Field(min_length=1, max_length=1)
    runs: list[RunRow]
    tasks: list[TaskRecord]
    run_tasks: list[RunTaskRow]

    @model_validator(mode='after')
    def references(self):
        book, edition = self.books[0], self.editions[0]
        if edition.book_id != book.book_id:
            raise ValueError('DB Book/Edition reference')
        runs, tasks = {r.run_id:r for r in self.runs}, {t.task_id:t for t in self.tasks}
        if len(runs)!=len(self.runs) or len(tasks)!=len(self.tasks):
            raise ValueError('Duplicate Run/Task')
        relationships=set()
        for row in [*self.runs,*self.tasks]:
            if row.edition_id != edition.edition_id:
                raise ValueError('Cross-book DB reference')
        for link in self.run_tasks:
            key=(link.run_id,link.task_id)
            if link.run_id not in runs or link.task_id not in tasks or key in relationships:
                raise ValueError('Invalid Run/Task reference')
            relationships.add(key)
        for row in self.runs:
            if row.execution_json is None:
                continue
            run=RunMetadata.model_validate_json(row.execution_json)
            if (run.run_id,run.edition_id,run.book_id,run.status.value)!=(row.run_id,edition.edition_id,book.book_id,row.status):
                raise ValueError('Run execution identity/status')
            for parent in (run.parent_run_id,run.resume_of_run_id):
                if parent and (parent not in runs or parent==run.run_id):
                    raise ValueError('Run parent reference')
            for checkpoint in run.checkpoints.values():
                if isinstance(checkpoint,dict):
                    for task_id in checkpoint.get('tasks',[])+checkpoint.get('completed_tasks',[]):
                        if UUID(task_id) not in tasks or (run.run_id,UUID(task_id)) not in relationships:
                            raise ValueError('Checkpoint Task reference')
        return self


class BookBackupManifest(CommonModel):
    backup_version: Literal['1.0'] = '1.0'
    created_at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    created_by_version: str
    book_id: UUID
    edition_id: UUID
    title: str
    source_sha256: Hash
    source_mode: Literal['copied','external']
    source_included: bool
    sqlite_schema_version: Literal[2] = 2
    normalized_generation_id: UUID | None
    generation_inventory: dict[str,list[str]]
    human_inventory: dict[str,int]
    human_state_hash: Hash
    run_inventory: dict[str,str]
    task_inventory: dict[str,str]
    run_task_inventory: list[RunTaskRow]
    checkpoint_inventory: list[str]
    external_rule_dependencies: list[ExternalRule] = Field(default_factory=list)
    ask_history_included: Literal[True] = True
    pointers: dict[str,str]
    files: list[BackupFile]
    backup_content_hash: Hash
