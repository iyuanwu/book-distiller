"""SQLite schema and parameterized repositories; no SQL belongs in the CLI."""
from contextlib import contextmanager
from collections.abc import Iterator
from datetime import datetime, timezone
from pathlib import Path
import sqlite3
from uuid import UUID
from book_distiller.core.errors import StorageError
from book_distiller.core.enums import StageStatus
from book_distiller.core.models import RunMetadata, TaskRecord
from book_distiller.core.models.library import Manifest

SCHEMA_VERSION = 1
SCHEMA = (
    "CREATE TABLE schema_meta (version INTEGER NOT NULL)",
    """CREATE TABLE books (
        book_id TEXT PRIMARY KEY, canonical_title TEXT NOT NULL,
        slug TEXT NOT NULL UNIQUE, metadata_status TEXT NOT NULL CHECK(metadata_status = 'provisional'),
        status TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE editions (
        edition_id TEXT PRIMARY KEY, book_id TEXT NOT NULL REFERENCES books(book_id),
        display_title TEXT NOT NULL, language TEXT, publisher TEXT, isbn TEXT,
        source_sha256 TEXT NOT NULL UNIQUE CHECK(length(source_sha256) = 64),
        source_extension TEXT NOT NULL, source_original_filename TEXT NOT NULL,
        source_original_path TEXT NOT NULL, source_stored_path TEXT NOT NULL,
        source_file_size INTEGER NOT NULL CHECK(source_file_size >= 0),
        copy_mode TEXT NOT NULL CHECK(copy_mode IN ('copy', 'reference')),
        manifest_version TEXT NOT NULL, created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    "CREATE INDEX editions_book_id ON editions(book_id)",
    """CREATE TABLE runs (
        run_id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES editions(edition_id),
        mode TEXT NOT NULL CHECK(mode IN ('fast', 'standard', 'deep')), status TEXT NOT NULL,
        prompt_version TEXT, schema_version TEXT, pipeline_version TEXT, skill_version TEXT,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
    """CREATE TABLE tasks (
        task_id TEXT PRIMARY KEY, edition_id TEXT NOT NULL REFERENCES editions(edition_id),
        task_type TEXT NOT NULL, status TEXT NOT NULL,
        created_at TEXT NOT NULL, updated_at TEXT NOT NULL)""",
)
SELECT_MANIFEST = """SELECT b.book_id, b.canonical_title, b.slug, b.metadata_status,
    b.status, e.edition_id, e.display_title, e.language, e.publisher, e.isbn,
    e.source_sha256, e.source_extension, e.source_original_filename,
    e.source_original_path, e.source_stored_path, e.source_file_size,
    e.copy_mode, e.manifest_version, e.created_at, e.updated_at
    FROM books b JOIN editions e ON e.book_id = b.book_id"""


class Database:
    """Manage the global index, with explicit version checks and transactions."""

    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self) -> Iterator[sqlite3.Connection]:
        """Open a short-lived connection with enforced foreign keys."""
        connection = sqlite3.connect(self.path, timeout=30)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            yield connection
        finally:
            connection.close()

    def initialize(self) -> None:
        """Create schema only for an empty database; refuse unknown versions."""
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            tables = {row[0] for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            if not tables:
                for statement in SCHEMA:
                    connection.execute(statement)
                connection.execute("INSERT INTO schema_meta(version) VALUES (?)", (SCHEMA_VERSION,))
            elif "schema_meta" not in tables:
                raise StorageError("Unversioned database; refusing to modify it.")
            else:
                versions = [row[0] for row in connection.execute("SELECT version FROM schema_meta")]
                if versions != [SCHEMA_VERSION]:
                    raise StorageError(f"Unsupported database schema version: {versions}; expected {SCHEMA_VERSION}.")
                if not {"books", "editions", "runs", "tasks"}.issubset(tables):
                    raise StorageError("Database schema is incomplete; refusing to modify it.")

    @contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Commit all index records together, rolling back on failure."""
        with self.connect() as connection, connection:
            connection.execute("BEGIN IMMEDIATE")
            yield connection

    def check(self) -> None:
        """Check schema, SQLite integrity and foreign keys."""
        self.initialize()
        with self.connect() as connection:
            if connection.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                raise StorageError("SQLite integrity check failed")
            if connection.execute("PRAGMA foreign_key_check").fetchone() is not None:
                raise StorageError("SQLite foreign key check failed")
            # Prepare queries so a versioned but malformed table is not reported OK.
            connection.execute(SELECT_MANIFEST + " LIMIT 0")
            connection.execute("SELECT run_id, edition_id, mode, status, prompt_version, schema_version, pipeline_version, skill_version, created_at, updated_at FROM runs LIMIT 0")
            connection.execute("SELECT task_id, edition_id, task_type, status, created_at, updated_at FROM tasks LIMIT 0")

    @staticmethod
    def _manifest(row: sqlite3.Row) -> Manifest:
        return Manifest.model_validate({
            "manifest_version": row["manifest_version"],
            "book": {"book_id": row["book_id"], "title": row["canonical_title"], "slug": row["slug"], "metadata_status": row["metadata_status"], "status": row["status"]},
            "edition": {"edition_id": row["edition_id"], "book_id": row["book_id"], "display_title": row["display_title"], "language": row["language"], "publisher": row["publisher"], "isbn": row["isbn"]},
            "source": {"original_filename": row["source_original_filename"], "original_path": row["source_original_path"], "stored_path": row["source_stored_path"], "file_size": row["source_file_size"], "sha256": row["source_sha256"], "extension": row["source_extension"], "copy_mode": row["copy_mode"]},
            "created_at": row["created_at"], "updated_at": row["updated_at"],
        })

    def list_books(self) -> list[Manifest]:
        """Return indexed editions in deterministic order."""
        with self.connect() as connection:
            return [self._manifest(row) for row in connection.execute(SELECT_MANIFEST + " ORDER BY b.created_at, b.book_id")]

    def lookup_hash(self, sha256: str) -> Manifest | None:
        """Find the existing edition for exact source bytes."""
        with self.connect() as connection:
            row = connection.execute(SELECT_MANIFEST + " WHERE e.source_sha256 = ?", (sha256,)).fetchone()
            return self._manifest(row) if row else None

    def lookup_book(self, selector: str) -> list[Manifest]:
        """Select by stable book UUID or exact slug, never fuzzy title guesses."""
        with self.connect() as connection:
            return [self._manifest(row) for row in connection.execute(SELECT_MANIFEST + " WHERE b.book_id = ? OR b.slug = ?", (selector, selector))]

    def insert_manifest(self, connection: sqlite3.Connection, manifest: Manifest) -> None:
        """Insert a Book and Edition within the caller's transaction."""
        b, e, s = manifest.book, manifest.edition, manifest.source
        times = (manifest.created_at.isoformat(), manifest.updated_at.isoformat())
        connection.execute("INSERT INTO books VALUES (?, ?, ?, ?, ?, ?, ?)", (str(b.book_id), b.title, b.slug, b.metadata_status, b.status.value, *times))
        connection.execute("INSERT INTO editions VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (
            str(e.edition_id), str(b.book_id), e.display_title, e.language, e.publisher, e.isbn,
            s.sha256, s.extension, s.original_filename, str(s.original_path), s.stored_path,
            s.file_size, s.copy_mode, manifest.manifest_version, *times))

    def insert_task(self, connection: sqlite3.Connection, task: TaskRecord) -> None:
        """Persist task metadata without executing or scheduling it."""
        connection.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)", (str(task.task_id), str(task.edition_id), task.task_type, task.status.value, task.created_at.isoformat(), task.updated_at.isoformat()))

    def update_task_status(self, task_id: UUID, status: StageStatus) -> None:
        """Update an existing task record; no queue behavior is implied."""
        with self.transaction() as connection:
            cursor = connection.execute("UPDATE tasks SET status = ?, updated_at = ? WHERE task_id = ?", (status.value, datetime.now(timezone.utc).isoformat(), str(task_id)))
            if cursor.rowcount != 1:
                raise StorageError(f"Task not found: {task_id}")

    def insert_run(self, connection: sqlite3.Connection, run: RunMetadata) -> None:
        """Persist supplied run metadata; ingest never creates an AI run."""
        row = connection.execute("SELECT book_id FROM editions WHERE edition_id = ?", (str(run.edition_id),)).fetchone()
        if not row or row[0] != str(run.book_id):
            raise StorageError("Run book and edition do not match")
        connection.execute("INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)", (str(run.run_id), str(run.edition_id), run.mode.value, run.status.value, run.prompt_version, run.schema_version, run.pipeline_version, run.skill_version, run.created_at.isoformat(), run.updated_at.isoformat()))
