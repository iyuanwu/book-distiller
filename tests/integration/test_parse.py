from pathlib import Path
from datetime import datetime, timezone
import shutil
import pytest
from typer.testing import CliRunner
from book_distiller.core.ingest import IngestService
from book_distiller.core.parse import ParseService
from book_distiller.core.errors import PipelineError, StorageError
from book_distiller.core.models.normalized import ParserMetadata, BlockType
from book_distiller.parsers.base import ParserResult, ParserItem, ParserSpan
from book_distiller.parsers.plaintext import PlainTextAdapter
from book_distiller.normalize.book import read_blocks
from book_distiller.cli.main import app


@pytest.fixture
def text_source(tmp_path):
    target = tmp_path / "sample.txt"
    shutil.copyfile(Path(__file__).parents[1] / "fixtures/sample.txt", target)
    return target


def test_text_parse_idempotent_and_force(isolated_storage, text_source):
    manifest = IngestService(isolated_storage).ingest(text_source).manifest
    service = ParseService(isolated_storage)
    first = service.parse(manifest.book.slug)
    target = first.parsed_path.resolve()
    assert first.quality.status == "pass"
    assert first.book.chapters[0].synthetic
    assert not first.already_parsed
    assert service.parse(str(manifest.book.book_id)).already_parsed
    assert first.parsed_path.resolve() == target
    second = service.parse(manifest.book.slug, force=True)
    assert second.parsed_path.resolve() != target
    assert target.exists()
    assert second.book.normalization_metadata.normalizer_version == "1.0"
    assert all(span.source_page_number is None for b in read_blocks(second.parsed_path / "normalized/blocks.jsonl") for span in b.source_spans)
    assert IngestService(isolated_storage).status() == [manifest]


@pytest.mark.parametrize("copy,action", [(True,"modify"),(True,"delete"),(False,"modify"),(False,"delete")])
def test_parse_integrity(isolated_storage, text_source, copy, action):
    result = IngestService(isolated_storage).ingest(text_source, copy=copy)
    service = ParseService(isolated_storage)
    service.parse(result.manifest.book.slug)
    source = result.library_path / result.manifest.source.stored_path if copy else text_source
    if action == "modify":
        source.write_bytes(b"x" * source.stat().st_size)
    else:
        source.unlink()
    with pytest.raises(PipelineError, match="changed since ingest" if action == "modify" else "unavailable"):
        service.parse(result.manifest.book.slug)


class BrokenAdapter(PlainTextAdapter):
    def parse(self, *args):
        raise RuntimeError("Injected parser failure")


def test_force_failure_preserves_old(isolated_storage, text_source):
    manifest = IngestService(isolated_storage).ingest(text_source).manifest
    first = ParseService(isolated_storage).parse(manifest.book.slug)
    previous = first.parsed_path.resolve()
    before = (previous / "normalized/book.json").read_bytes()
    broken = ParseService(isolated_storage, adapter_factory=lambda _: BrokenAdapter())
    with pytest.raises(PipelineError, match="Injected"):
        broken.parse(manifest.book.slug, force=True)
    assert first.parsed_path.resolve() == previous
    assert (previous / "normalized/book.json").read_bytes() == before
    assert broken.library.database.latest_parse_task(manifest.edition.edition_id)["status"] == "failed"
    assert not list(previous.parents[1].glob(".parse-staging-*"))
    assert ParseService(isolated_storage).parse(manifest.book.slug).already_parsed


def test_failure_retry_without_prior_results(isolated_storage, text_source):
    result = IngestService(isolated_storage).ingest(text_source)
    broken = ParseService(isolated_storage, adapter_factory=lambda _: BrokenAdapter())
    with pytest.raises(PipelineError):
        broken.parse(result.manifest.book.slug)
    assert not (result.library_path / "parsed").exists()
    assert list((result.library_path / "parse_failures").glob("*.json"))
    assert ParseService(isolated_storage).parse(result.manifest.book.slug).quality.status == "pass"


def test_parse_cli_and_status(isolated_storage, text_source):
    manifest = IngestService(isolated_storage).ingest(text_source).manifest
    runner = CliRunner()
    first = runner.invoke(app, ["parse", manifest.book.slug])
    assert first.exit_code == 0, first.output
    assert "successfully" in first.output
    assert "Already parsed." in runner.invoke(app, ["parse", str(manifest.book.book_id)]).output
    detail = runner.invoke(app, ["status", manifest.book.slug])
    assert detail.exit_code == 0
    assert "plaintext" in detail.output and "Normalized schema" in detail.output


def test_unknown_book_never_scanned(isolated_storage):
    (isolated_storage / "library/unknown").mkdir()
    with pytest.raises(Exception, match="indexed Edition"):
        ParseService(isolated_storage).parse("unknown")


def test_corrupted_artifact_not_cache_hit(isolated_storage, text_source):
    manifest = IngestService(isolated_storage).ingest(text_source).manifest
    service = ParseService(isolated_storage)
    result = service.parse(manifest.book.slug)
    (result.parsed_path / "normalized/blocks.jsonl").write_text("garbage")
    with pytest.raises(StorageError, match="integrity mismatch"):
        service.parse(manifest.book.slug)
    assert not service.parse(manifest.book.slug, force=True).already_parsed


def test_failed_quality_keeps_task_failed(isolated_storage, text_source):
    class EmptyAdapter(PlainTextAdapter):
        def parse(self, source, raw, source_sha256):
            result = super().parse(source, raw, source_sha256)
            result.items = []
            return result
    manifest = IngestService(isolated_storage).ingest(text_source).manifest
    service = ParseService(isolated_storage, adapter_factory=lambda _: EmptyAdapter())
    with pytest.raises(PipelineError, match="NO_USABLE_TEXT"):
        service.parse(manifest.book.slug)
    assert service.library.database.latest_parse_task(manifest.edition.edition_id)["status"] == "failed"


def test_warning_quality_completes(isolated_storage, text_source):
    class WarningAdapter(PlainTextAdapter):
        def parse(self, source, raw, source_sha256):
            result = super().parse(source, raw, source_sha256)
            result.metadata.warnings.append("test warning")
            return result
    manifest = IngestService(isolated_storage).ingest(text_source).manifest
    service = ParseService(isolated_storage, adapter_factory=lambda _: WarningAdapter())
    assert service.parse(manifest.book.slug).quality.status == "review_recommended"
    assert service.library.database.latest_parse_task(manifest.edition.edition_id)["status"] == "completed"


def test_completion_failure_restores_pointer(isolated_storage, text_source, monkeypatch):
    from book_distiller.core.enums import StageStatus
    manifest = IngestService(isolated_storage).ingest(text_source).manifest
    service = ParseService(isolated_storage)
    previous = service.parse(manifest.book.slug).parsed_path.resolve()
    original = service.library.database.update_task_status
    def fail(task, status):
        if status == StageStatus.COMPLETED:
            raise OSError("Cannot commit completion")
        original(task, status)
    monkeypatch.setattr(service.library.database, "update_task_status", fail)
    with pytest.raises(PipelineError, match="Cannot commit"):
        service.parse(manifest.book.slug, force=True)
    assert (previous.parents[1] / "parsed").resolve() == previous


def test_parser_version_invalidates_cache(isolated_storage, text_source):
    manifest = IngestService(isolated_storage).ingest(text_source).manifest
    first = ParseService(isolated_storage).parse(manifest.book.slug)
    old = first.parsed_path.resolve()
    class UpdatedAdapter(PlainTextAdapter):
        version = "1.1"
    service = ParseService(isolated_storage, adapter_factory=lambda _: UpdatedAdapter())
    result = service.parse(manifest.book.slug)
    assert not result.already_parsed
    assert result.parsed_path.resolve() != old
    assert service.parse(manifest.book.slug).already_parsed


def test_normalizer_failure_preserves_old(isolated_storage, text_source, monkeypatch):
    import book_distiller.core.parse as module
    manifest = IngestService(isolated_storage).ingest(text_source).manifest
    previous = ParseService(isolated_storage).parse(manifest.book.slug).parsed_path.resolve()
    def fail(*args):
        raise ValueError("Injected normalization failure")
    monkeypatch.setattr(module, "normalize", fail)
    service = ParseService(isolated_storage)
    with pytest.raises(PipelineError, match="normalization failure"):
        service.parse(manifest.book.slug, force=True)
    assert (previous.parents[1] / "parsed").resolve() == previous
    assert service.library.database.latest_parse_task(manifest.edition.edition_id)["status"] == "failed"


def test_source_changes_during_conversion(isolated_storage, text_source):
    class MutatingAdapter(PlainTextAdapter):
        def parse(self, source, raw, source_sha256):
            result = super().parse(source, raw, source_sha256)
            source.write_text("Changed source", encoding="utf-8")
            return result
    ingest = IngestService(isolated_storage).ingest(text_source, copy=False)
    service = ParseService(isolated_storage, adapter_factory=lambda _: MutatingAdapter())
    with pytest.raises(PipelineError, match="changed during parse"):
        service.parse(ingest.manifest.book.slug)
    assert not (ingest.library_path / "parsed").exists()


def test_killed_parse_not_reported_completed(isolated_storage, text_source):
    import subprocess, sys
    manifest = IngestService(isolated_storage).ingest(text_source).manifest
    code = '''
import os, sys
from pathlib import Path
from book_distiller.core.parse import ParseService
from book_distiller.storage.parsed import ParsedStorage
original = ParsedStorage.publish

def crash(self, stage, task_id):
    original(self, stage, task_id)
    os._exit(74)
ParsedStorage.publish = crash
ParseService(Path(sys.argv[1])).parse(sys.argv[2])
'''
    result = subprocess.run([sys.executable, "-c", code, str(isolated_storage), manifest.book.slug])
    assert result.returncode == 74
    service = ParseService(isolated_storage)
    with pytest.raises(StorageError, match="Interrupted parse task"):
        service.parse(manifest.book.slug)
    description = service.describe(isolated_storage / "library" / manifest.book.slug, manifest.edition.edition_id)
    assert description["Parse status"] == "needs_review"


def test_plaintext_utf8_and_paragraph_boundaries(tmp_path):
    path = tmp_path / "input.txt"
    path.write_text("First paragraph.\r\nContinued.\r\n\r\nSecond paragraph.\n", encoding="utf-8")
    paragraphs = list(PlainTextAdapter.iter_paragraphs(path))
    assert len(paragraphs) == 2
    assert paragraphs[0].locator == "lines:1-2"
    assert paragraphs[1].locator == "lines:4-4"
    raw = tmp_path / "raw"; raw.mkdir()
    path.write_bytes(b"\xff\xfe")
    with pytest.raises(PipelineError, match="UTF-8"):
        PlainTextAdapter().parse(path, raw, "0"*64)
