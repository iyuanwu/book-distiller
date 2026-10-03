"""Local library orchestration only: no content parsing or reasoning."""
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from pydantic import ValidationError as ModelValidationError
from book_distiller.core.errors import BookDistillerError, StorageError, ValidationError
from book_distiller.core.models import BookIdentity, EditionIdentity, TaskRecord
from book_distiller.core.models.library import Manifest, SourceFile
from book_distiller.storage.database import Database
from book_distiller.storage.filesystem import FileStorage, hash_source, validate_source


@dataclass(frozen=True)
class IngestResult:
    manifest: Manifest
    library_path: Path
    already_ingested: bool


class IngestService:
    """Coordinate idempotent filesystem and SQLite operations under one lock."""

    def __init__(self, root: Path):
        self.files = FileStorage(root)
        self.database = Database(root / "data" / "book_distiller.sqlite3")

    def _initialize(self) -> None:
        self.database.initialize()
        self.files.check_inventory({m.book.slug for m in self.database.list_books()})

    def _verify(self, manifest: Manifest, *, full_hash: bool = False) -> None:
        directory = self.files.library / manifest.book.slug
        if directory.is_symlink():
            raise StorageError(f"Library book directory must not be a symlink: {directory}")
        if self.files.read_manifest(directory) != manifest:
            raise StorageError(f"Manifest/index mismatch at {directory}; reconcile before proceeding.")
        source = manifest.source
        path = directory / source.stored_path if source.copy_mode == "copy" else Path(source.stored_path)
        if source.copy_mode == "copy" and not path.resolve().is_relative_to(directory.resolve()):
            raise StorageError(f"Copied source escapes its Library directory: {path}")
        if not path.is_file():
            raise StorageError(f"Source unavailable: {path}. A referenced original may have moved or been deleted.")
        if path.stat().st_size != source.file_size or (full_hash and hash_source(path)[0] != source.sha256):
            raise StorageError(f"Source integrity mismatch: {path}")

    def ingest(self, source: Path, *, copy: bool = True, title: str | None = None) -> IngestResult:
        """Prepare, transact, publish and roll back this ingest on ordinary errors."""
        prepared: Path | None = None
        published: Path | None = None
        committed = False
        try:
            original_filename = source.name
            extension = source.suffix.lower()
            source = validate_source(source)
            with self.files.locked():
                self._initialize()
                digest, size = hash_source(source)
                existing = self.database.lookup_hash(digest)
                if existing:
                    self._verify(existing, full_hash=True)
                    return IngestResult(existing, self.files.library / existing.book.slug, True)
                display_title = title.strip() if title is not None else Path(original_filename).stem.strip()
                if not display_title:
                    raise ValidationError("Book title must not be empty")
                slug = self.files.available_slug(display_title, {m.book.slug for m in self.database.list_books()})
                book = BookIdentity(title=display_title, slug=slug)
                edition = EditionIdentity(book_id=book.book_id, display_title=display_title)
                now = datetime.now(timezone.utc)
                manifest = Manifest(book=book, edition=edition, source=SourceFile(
                    original_filename=original_filename, original_path=source,
                    stored_path=f"source/original{extension}" if copy else str(source),
                    file_size=size, sha256=digest, extension=extension, copy_mode="copy" if copy else "reference",
                ), created_at=now, updated_at=now)
                prepared = self.files.stage()
                try:
                    if copy:
                        self.files.copy_source(source, prepared / manifest.source.stored_path, digest)
                    elif hash_source(source) != (digest, size):
                        raise StorageError("Referenced source changed during ingest; retry with a stable file.")
                    self.files.write_manifest(prepared, manifest)
                    with self.database.transaction() as connection:
                        self.database.insert_manifest(connection, manifest)
                        self.database.insert_task(connection, TaskRecord(edition_id=edition.edition_id, task_type="ingest", status="completed", created_at=now, updated_at=now))
                        published = self.files.publish(prepared, slug)
                    committed = True
                finally:
                    if not committed:
                        cleanup = published or prepared
                        if cleanup is not None and cleanup.exists():
                            self.files.discard(cleanup)
                return IngestResult(manifest, published, False)
        except BookDistillerError:
            raise
        except (OSError, sqlite3.Error, ModelValidationError) as exc:
            raise StorageError(f"Ingest failed: {exc}") from exc

    def status(self, selector: str | None = None) -> list[Manifest]:
        """Read inventory and detect missing sources/manifests without parsing."""
        try:
            with self.files.locked():
                self._initialize()
                records = self.database.lookup_book(selector) if selector else self.database.list_books()
                if selector and not records:
                    raise ValidationError(f"Book not found: {selector}. Use an exact book ID or slug.")
                if selector and len({m.book.book_id for m in records}) > 1:
                    raise ValidationError("Ambiguous book selector: " + ", ".join(str(m.book.book_id) + " (" + m.book.slug + ")" for m in records))
                for manifest in records:
                    self._verify(manifest)
                return records
        except BookDistillerError:
            raise
        except (OSError, sqlite3.Error, ModelValidationError) as exc:
            raise StorageError(f"Library status failed: {exc}") from exc

    def check(self) -> None:
        """Initialize/check the global index and detect interrupted imports."""
        try:
            with self.files.locked():
                self.database.check()
                self.files.check_inventory({m.book.slug for m in self.database.list_books()})
        except (OSError, sqlite3.Error, ModelValidationError) as exc:
            raise StorageError(f"Database check failed: {exc}") from exc
