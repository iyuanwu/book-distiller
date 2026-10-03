from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import shutil
import sqlite3
import subprocess
import pytest
from typer.testing import CliRunner
from book_distiller.cli.main import app
from book_distiller.core.errors import StorageError
from book_distiller.core.ingest import IngestService

runner = CliRunner()


def counts(service):
    with service.database.connect() as connection:
        return tuple(connection.execute(f"SELECT count(*) FROM {table}").fetchone()[0] for table in ("books", "editions", "runs", "tasks"))


def test_copy_and_duplicate(isolated_storage, sample_source):
    service = IngestService(isolated_storage)
    first = service.ingest(sample_source)
    copied = first.library_path / "source/original.md"
    assert copied.read_bytes() == sample_source.read_bytes()
    before = copied.stat().st_mtime_ns
    second = service.ingest(sample_source, copy=False, title="Ignored for duplicate")
    assert second.already_ingested
    assert second.manifest == first.manifest
    assert copied.stat().st_mtime_ns == before
    assert counts(service) == (1, 1, 0, 1)
    assert len(list((isolated_storage / "library").iterdir())) == 1


def test_reference(isolated_storage, sample_source):
    service = IngestService(isolated_storage)
    result = service.ingest(sample_source, copy=False)
    assert not (result.library_path / "source").exists()
    assert result.manifest.source.stored_path == str(sample_source.resolve())
    assert result.manifest.source.copy_mode == "reference"
    assert service.database.lookup_hash(result.manifest.source.sha256) == result.manifest
    sample_source.unlink()
    with pytest.raises(StorageError, match="Source unavailable"):
        service.status()


def test_same_title_different_bytes(isolated_storage, sample_source, tmp_path):
    service = IngestService(isolated_storage)
    first = service.ingest(sample_source, title="Book")
    second_source = tmp_path / "second.txt"
    shutil.copyfile(Path(__file__).parents[1] / "fixtures/sample.txt", second_source)
    second = service.ingest(second_source, title="Book")
    assert first.manifest.book.slug == "book"
    assert second.manifest.book.slug == "book-2"
    assert first.manifest.book.book_id != second.manifest.book.book_id


@pytest.mark.parametrize("failure", ["copy", "manifest", "insert", "publish", "commit"])
def test_rollback(isolated_storage, sample_source, monkeypatch, failure):
    service = IngestService(isolated_storage)
    def fail(*args, **kwargs):
        raise OSError("injected failure")
    if failure == "copy":
        monkeypatch.setattr(service.files, "copy_source", fail)
    elif failure == "manifest":
        monkeypatch.setattr(service.files, "write_manifest", fail)
    elif failure == "insert":
        monkeypatch.setattr(service.database, "insert_task", fail)
    elif failure == "publish":
        monkeypatch.setattr(service.files, "publish", fail)
    else:
        original = service.database.transaction
        @contextmanager
        def fail_commit():
            with original() as connection:
                yield connection
                raise sqlite3.OperationalError("injected commit failure")
        monkeypatch.setattr(service.database, "transaction", fail_commit)
    with pytest.raises(StorageError, match="injected"):
        service.ingest(sample_source)
    assert counts(service) == (0, 0, 0, 0)
    assert list(service.files.library.iterdir()) == []
    assert sample_source.exists()


def test_concurrent_duplicate(isolated_storage, sample_source):
    with ThreadPoolExecutor(max_workers=2) as executor:
        results = list(executor.map(lambda _: IngestService(isolated_storage).ingest(sample_source), range(2)))
    assert sorted(result.already_ingested for result in results) == [False, True]
    assert counts(IngestService(isolated_storage)) == (1, 1, 0, 1)


def test_interrupted_staging_reported(isolated_storage):
    leftover = isolated_storage / "library/.ingest-interrupted"
    leftover.mkdir()
    with pytest.raises(StorageError, match="interrupted"):
        IngestService(isolated_storage).status()
    assert leftover.exists()


def test_orphan_manifest_reported(isolated_storage, sample_source):
    service = IngestService(isolated_storage)
    result = service.ingest(sample_source)
    with service.database.transaction() as connection:
        connection.execute("DELETE FROM tasks")
        connection.execute("DELETE FROM editions")
        connection.execute("DELETE FROM books")
    with pytest.raises(StorageError, match="Unindexed"):
        service.status()
    assert (result.library_path / "manifest.json").exists()


def test_corrupt_copy_duplicate_rejected(isolated_storage, sample_source):
    service = IngestService(isolated_storage)
    result = service.ingest(sample_source)
    target = result.library_path / "source/original.md"
    target.write_bytes(b"x" * target.stat().st_size)
    with pytest.raises(StorageError, match="integrity mismatch"):
        service.ingest(sample_source)


def test_cli_ingest_status(isolated_storage, sample_source):
    result = runner.invoke(app, ["ingest", str(sample_source)])
    assert result.exit_code == 0, result.output
    assert "successfully" in result.output
    assert runner.invoke(app, ["ingest", str(sample_source)]).output.startswith("Already ingested.")
    records = IngestService(isolated_storage).status()
    for selector in (records[0].book.slug, str(records[0].book.book_id)):
        result = runner.invoke(app, ["status", selector])
        assert result.exit_code == 0, result.output
        assert "SHA256" in result.output
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "pending" in result.output


def test_cli_reference(sample_source):
    result = runner.invoke(app, ["ingest", str(sample_source), "--no-copy"])
    assert result.exit_code == 0, result.output
    assert "reference" in result.output
    assert "移动或删除" in result.output


@pytest.mark.parametrize("args,message", [(["ingest", "missing.md"], "does not exist"), (["ingest", "file.zip"], "Unsupported source format"), (["status", "absent"], "Book not found")])
def test_cli_errors(args, message):
    result = runner.invoke(app, args)
    assert result.exit_code == 1
    assert message in result.output
    assert "Traceback" not in result.output


def test_empty_status():
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 0
    assert "empty" in result.output


def test_launcher_relative_source(tmp_path, sample_source):
    launcher = Path(__file__).resolve().parents[2] / "book"
    result = subprocess.run([str(launcher), "ingest", sample_source.name], cwd=sample_source.parent, capture_output=True, text=True)
    assert result.returncode == 0, result.stderr


def test_killed_process_is_detected(isolated_storage, sample_source):
    import sys
    code = '''
import os
import sys
from pathlib import Path
from book_distiller.core.ingest import IngestService
service = IngestService(Path(sys.argv[1]))
original = service.files.publish

def crash(stage, slug):
    original(stage, slug)
    os._exit(73)

service.files.publish = crash
service.ingest(Path(sys.argv[2]))
'''
    result = subprocess.run([sys.executable, "-c", code, str(isolated_storage), str(sample_source)])
    assert result.returncode == 73
    service = IngestService(isolated_storage)
    assert counts(service) == (0, 0, 0, 0)
    with pytest.raises(StorageError, match="Unindexed"):
        service.status()


def test_failure_preserves_existing_book(isolated_storage, sample_source, tmp_path, monkeypatch):
    service = IngestService(isolated_storage)
    first = service.ingest(sample_source)
    original = (first.library_path / "manifest.json").read_bytes()
    another = tmp_path / "another.txt"
    shutil.copyfile(Path(__file__).parents[1] / "fixtures/sample.txt", another)
    def fail(*args):
        raise OSError("database unavailable")
    monkeypatch.setattr(service.database, "insert_manifest", fail)
    with pytest.raises(StorageError):
        service.ingest(another)
    assert (first.library_path / "manifest.json").read_bytes() == original
    assert counts(service) == (1, 1, 0, 1)
    assert service.status() == [first.manifest]


def test_reference_manifest_rejects_relative_path(isolated_storage, sample_source):
    from pydantic import ValidationError
    from book_distiller.core.models.library import Manifest
    result = IngestService(isolated_storage).ingest(sample_source, copy=False)
    raw = result.manifest.model_dump(mode="json")
    raw["source"]["stored_path"] = "relative.md"
    with pytest.raises(ValidationError):
        Manifest.model_validate(raw)


def test_corrupt_manifest_reports_error(isolated_storage, sample_source):
    service = IngestService(isolated_storage)
    result = service.ingest(sample_source)
    (result.library_path / "manifest.json").write_text("{broken", encoding="utf-8")
    result = runner.invoke(app, ["status"])
    assert result.exit_code == 1
    assert "Invalid or missing manifest" in result.output


def test_cli_doctor_unknown_schema(isolated_storage):
    service = IngestService(isolated_storage)
    service.status()
    with service.database.transaction() as connection:
        connection.execute("UPDATE schema_meta SET version = 999")
    result = runner.invoke(app, ["doctor"])
    assert result.exit_code == 1
    assert "FAIL" in result.output
    with service.database.connect() as connection:
        assert connection.execute("SELECT version FROM schema_meta").fetchone()[0] == 999
