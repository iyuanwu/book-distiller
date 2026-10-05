"""Parse service: source integrity, adapters, normalization, tasks and publication."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
from typing import Callable
from uuid import UUID
from book_distiller.core.errors import BookDistillerError, PipelineError, StorageError, ValidationError
from book_distiller.core.ingest import IngestService
from book_distiller.core.enums import StageStatus
from book_distiller.core.models import TaskRecord
from book_distiller.core.models.normalized import NORMALIZER_VERSION, SCHEMA_VERSION, NormalizedBook
from book_distiller.normalize.book import normalize, read_blocks
from book_distiller.parsers.base import ParserAdapter
from book_distiller.parsers.plaintext import PlainTextAdapter
from book_distiller.parsers.quality import QualityReport, QualityThresholds
from book_distiller.storage.filesystem import hash_source
from book_distiller.storage.parsed import ParsedStorage, write_json


def adapter_for(extension: str) -> ParserAdapter:
    if extension == ".txt":
        return PlainTextAdapter()
    from book_distiller.parsers.docling import DoclingAdapter
    return DoclingAdapter()


@dataclass(frozen=True)
class ParseOutcome:
    book: NormalizedBook
    quality: QualityReport
    parsed_path: Path
    already_parsed: bool = False


class ParseService:
    def __init__(self, root: Path, adapter_factory: Callable[[str], ParserAdapter] = adapter_for,
                 thresholds: QualityThresholds | None = None):
        self.library = IngestService(root)
        self.adapter_factory = adapter_factory
        self.thresholds = thresholds or QualityThresholds()

    def parse(self, selector: str, *, force: bool = False) -> ParseOutcome:
        """Publish only complete valid results; preserve older successful generations."""
        try:
            return self._parse_locked(selector, force=force)
        except BookDistillerError:
            raise
        except Exception as exc:
            raise PipelineError(f"Parse failed: {exc}") from exc

    def _parse_locked(self, selector: str, *, force: bool) -> ParseOutcome:
        library = self.library
        with library.files.locked():
            library._initialize()
            records = library.database.lookup_book(selector)
            if len(records) != 1:
                raise ValidationError("Parse requires one indexed Edition selected by exact book ID or slug.")
            manifest = records[0]
            directory = library.files.library / manifest.book.slug
            store = ParsedStorage(directory)
            store.check_staging()
            latest = library.database.latest_parse_task(manifest.edition.edition_id)
            if latest and latest["status"] in {"pending", "running"}:
                raise StorageError(f"Interrupted parse task {latest['task_id']}; inspect before retrying. Automatic resume is not implemented.")
            source = directory / manifest.source.stored_path if manifest.source.copy_mode == "copy" else Path(manifest.source.stored_path)
            # Always check the source before cache lookup, even for Already parsed.
            if not source.is_file():
                raise PipelineError(f"Source unavailable: {source}. Restore it or ingest a new source.")
            if hash_source(source) != (manifest.source.sha256, manifest.source.file_size):
                message = "External source has changed since ingest." if manifest.source.copy_mode == "reference" else "Copied source has changed since ingest."
                raise PipelineError(message + " Ingest the changed file as a new Edition / Source.")
            library._verify(manifest, full_hash=False)
            adapter = self.adapter_factory(manifest.source.extension)
            key = {"source_sha256": manifest.source.sha256, "parser": adapter.name, "parser_version": adapter.version,
                "normalized_schema_version": SCHEMA_VERSION, "normalizer_version": NORMALIZER_VERSION,
                "parser_configuration": json.dumps(getattr(adapter, "configuration", {}), sort_keys=True),
                "quality_thresholds": self.thresholds.model_dump_json()}
            old = store.current()
            if not force and old:
                current = store.inspect()
                receipt, book, quality = current
                if library.database.task_status(receipt.task_id) != "completed":
                    raise StorageError("Published result has no completed parse task; inspect interrupted publication.")
                if book.book_id != manifest.book.book_id or book.edition_id != manifest.edition.edition_id:
                    raise StorageError("Parsed result identity does not match this Edition")
                if receipt.cache_key == key:
                    return ParseOutcome(book, quality, store.pointer, True)
            task = TaskRecord(edition_id=manifest.edition.edition_id, task_type="parse")
            with library.database.transaction() as connection:
                library.database.insert_task(connection, task)
            stage: Path | None = None
            published = False
            try:
                library.database.update_task_status(task.task_id, StageStatus.RUNNING)
                stage = store.prepare(task.task_id)
                result = adapter.parse(source, stage / "raw", manifest.source.sha256)
                write_json(stage / "raw/parse_metadata.json", result.metadata)
                book, quality = normalize(result, manifest, stage / "normalized", self.thresholds)
                write_json(stage / "normalized/book.json", book)
                write_json(stage / "normalized/quality.json", quality)
                # Re-read the serialized block stream without accumulating it in memory.
                chapter_ids = {chapter.chapter_id for chapter in book.chapters}
                section_ids = {section.section_id for chapter in book.chapters for section in chapter.sections}
                count = 0
                for count, block in enumerate(read_blocks(stage / "normalized/blocks.jsonl"), 1):
                    if block.order != count or block.block_id != f"blk_{count:06d}" or block.chapter_id not in chapter_ids or (block.section_id and block.section_id not in section_ids):
                        raise PipelineError("Canonical block identity or hierarchy validation failed")
                if count != book.block_count:
                    raise PipelineError("Canonical block count mismatch")
                if quality.status == "failed":
                    raise PipelineError("Parse quality failed: " + "; ".join(issue.code for issue in quality.issues))
                if hash_source(source) != (manifest.source.sha256, manifest.source.file_size):
                    raise PipelineError("Source changed during parse; no new result was published.")
                store.seal(stage, task.task_id, key)
                store.publish(stage, task.task_id)
                published = True
                library.database.update_task_status(task.task_id, StageStatus.COMPLETED)
                return ParseOutcome(book, quality, store.pointer)
            except Exception as exc:
                if published:
                    store.restore(old, task.task_id)
                library.database.update_task_status(task.task_id, StageStatus.FAILED)
                failures = directory / "parse_failures"
                failures.mkdir(exist_ok=True)
                write_json(failures / f"{task.task_id}.json", {"task_id": str(task.task_id), "status": "failed",
                    "parser": adapter.name, "parser_version": adapter.version, "source_sha256": manifest.source.sha256,
                    "started_at": task.created_at.isoformat(), "completed_at": datetime.now(timezone.utc).isoformat(),
                    "errors": [str(exc)], "quality": quality.model_dump(mode="json") if "quality" in locals() else None})
                raise PipelineError(f"Parse failed (task {task.task_id}): {exc}") from exc
            finally:
                if stage is not None:
                    store.discard_stage(stage)

    def recover(self, selector: str) -> None:
        """Run-owned recovery under the library lock; preserve abandoned candidates."""
        library = self.library
        with library.files.locked():
            library._initialize()
            records = library.database.lookup_book(selector)
            if len(records) != 1:
                raise ValidationError('Recovery requires one indexed Edition')
            manifest = records[0]
            directory = library.files.library / manifest.book.slug
            store = ParsedStorage(directory)
            latest = library.database.latest_parse_task(manifest.edition.edition_id)
            if not latest or latest['status'] not in {'pending', 'running'}:
                return
            current = store.inspect()
            published = current and str(current[0].task_id) == latest['task_id']
            if published:
                library.database.update_task_status(UUID(latest['task_id']), StageStatus.COMPLETED)
            else:
                library.database.update_task_status(UUID(latest['task_id']), StageStatus.FAILED)
            # An unsealed candidate cannot be resumed inside the parser. Archive it
            # and run a fresh parse; no source/normalized generation is overwritten.
            archive = directory / 'parse_failures' / latest['task_id']
            for prefix in ('.parse-staging-', '.parsed-link-'):
                artifact = directory / (prefix + latest['task_id'])
                if artifact.exists() or artifact.is_symlink():
                    archive.mkdir(parents=True, exist_ok=True)
                    artifact.rename(archive / artifact.name)

    def describe(self, book_root: Path, edition_id: UUID) -> dict[str, str]:
        """Describe the last successful generation and latest task separately."""
        store = ParsedStorage(book_root)
        try:
            store.check_staging()
            current = store.inspect()
            latest = self.library.database.latest_parse_task(edition_id)
            fields = {"Parse status": latest["status"] if latest else "not_parsed"}
            if current:
                receipt, book, quality = current
                if self.library.database.task_status(receipt.task_id) != "completed":
                    raise StorageError("Published result task is not completed")
                fields = {"Parse status": "completed", "Parser": book.parser_metadata.parser,
                    "Parser version": book.parser_metadata.parser_version, "Parse quality": quality.status,
                    "Normalized schema": book.schema_version}
            if latest:
                fields["Latest parse task"] = latest["status"]
            return fields
        except Exception as exc:
            return {"Parse status": "needs_review", "Parse diagnostic": str(exc)}
