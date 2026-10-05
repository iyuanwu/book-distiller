import hashlib
import json
from pathlib import Path
import sqlite3
from uuid import uuid4
import pytest
from pydantic import ValidationError as PydanticValidationError
from book_distiller.core.errors import StorageError, ValidationError
from book_distiller.core.ingest import IngestService
from book_distiller.core.models import RunMetadata
from book_distiller.core.models.library import Manifest
from book_distiller.storage.database import Database
from book_distiller.storage.filesystem import FileStorage, hash_source, make_slug, validate_source


def test_streaming_hash(sample_source, monkeypatch):
    import book_distiller.storage.filesystem as module
    monkeypatch.setattr(module, "CHUNK_SIZE", 7)
    assert hash_source(sample_source) == (hashlib.sha256(sample_source.read_bytes()).hexdigest(), sample_source.stat().st_size)


@pytest.mark.parametrize("title,expected", [("Poor Charlie's Almanack", "poor-charlie-s-almanack"), ("穷查理宝典", "穷查理宝典"), ("../", "book"), ("A / B", "a-b"), ("ＡＢＣ", "abc")])
def test_slug(title, expected):
    assert make_slug(title) == expected


def test_slug_conflict(isolated_storage):
    storage = FileStorage(isolated_storage)
    (storage.library / "book-2").mkdir()
    assert storage.available_slug("Book", {"book"}) == "book-3"


@pytest.mark.parametrize("extension", [".pdf", ".epub", ".txt", ".md", ".markdown", ".docx", ".PDF"])
def test_extensions(extension, sample_source):
    target = sample_source.with_suffix(extension)
    if target != sample_source:
        target.write_bytes(sample_source.read_bytes())
    assert validate_source(target) == target.resolve()


def test_invalid_sources(tmp_path):
    with pytest.raises(ValidationError, match="Unsupported"):
        validate_source(tmp_path / "test.zip")
    with pytest.raises(ValidationError, match="does not exist"):
        validate_source(tmp_path / "missing.md")
    folder = tmp_path / "folder.md"
    folder.mkdir()
    with pytest.raises(ValidationError):
        validate_source(folder)


def test_database_initialize(isolated_storage):
    database = Database(isolated_storage / "data/index.sqlite3")
    database.initialize()
    database.initialize()
    database.check()
    with database.connect() as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 2
        tables = {r[0] for r in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        assert tables == {"schema_meta", "books", "editions", "runs", "tasks", "run_tasks"}
        assert connection.execute("PRAGMA foreign_keys").fetchone()[0] == 1


def test_unknown_version_untouched(isolated_storage):
    database = Database(isolated_storage / "data/index.sqlite3")
    database.initialize()
    with database.transaction() as connection:
        connection.execute("UPDATE schema_meta SET version = 99")
    before = database.path.read_bytes()
    with pytest.raises(StorageError, match="Unsupported database schema"):
        database.initialize()
    assert database.path.read_bytes() == before


def test_unversioned_database_untouched(isolated_storage):
    path = isolated_storage / "unknown.sqlite3"
    with sqlite3.connect(path) as connection:
        connection.execute("CREATE TABLE unrelated (id TEXT)")
    before = path.read_bytes()
    with pytest.raises(StorageError, match="Unversioned"):
        Database(path).initialize()
    assert path.read_bytes() == before


def test_manifest_validation(isolated_storage, sample_source):
    result = IngestService(isolated_storage).ingest(sample_source)
    raw = json.loads((result.library_path / "manifest.json").read_text())
    assert Manifest.model_validate(raw) == result.manifest
    assert raw["source"]["original_filename"] == sample_source.name
    assert raw["source"]["stored_path"] == "source/original.md"
    assert raw["book"]["metadata_status"] == "provisional"
    for change in ({"manifest_version": "2.0"}, {"created_at": "2026-01-01T00:00:00"}):
        with pytest.raises(PydanticValidationError):
            Manifest.model_validate(raw | change)
    for path in ("../escape.md", "/tmp/escape.md"):
        with pytest.raises(PydanticValidationError):
            Manifest.model_validate(raw | {"source": raw["source"] | {"stored_path": path}})
    with pytest.raises(PydanticValidationError):
        Manifest.model_validate(raw | {"edition": raw["edition"] | {"book_id": str(uuid4())}})


def test_database_lookup_and_records(isolated_storage, sample_source):
    service = IngestService(isolated_storage)
    manifest = service.ingest(sample_source).manifest
    assert service.database.lookup_hash(manifest.source.sha256) == manifest
    assert service.database.lookup_hash("0" * 64) is None
    assert service.database.lookup_book("' OR 1=1 --") == []
    with service.database.connect() as connection:
        assert connection.execute("SELECT count(*) FROM runs").fetchone()[0] == 0
        task_id = connection.execute("SELECT task_id FROM tasks").fetchone()[0]
    from uuid import UUID
    from book_distiller.core.enums import StageStatus
    service.database.update_task_status(UUID(task_id), StageStatus.COMPLETED)
    run = RunMetadata(book_id=manifest.book.book_id, edition_id=manifest.edition.edition_id)
    with service.database.transaction() as connection:
        service.database.insert_run(connection, run)
    with service.database.connect() as connection:
        assert connection.execute("SELECT edition_id FROM runs").fetchone()[0] == str(manifest.edition.edition_id)
    with pytest.raises(sqlite3.IntegrityError), service.database.transaction() as connection:
        connection.execute("INSERT INTO tasks VALUES (?, ?, ?, ?, ?, ?)", (str(uuid4()), str(uuid4()), "ingest", "pending", "x", "x"))
