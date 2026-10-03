from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4
import pytest
from pydantic import ValidationError
from book_distiller.core.ingest import IngestService
from book_distiller.core.models.normalized import SourceSpan, NormalizedBlock, BlockType, ParserMetadata, NormalizedBook
from book_distiller.parsers.base import ParserItem, ParserSpan, ParserResult
from book_distiller.normalize.book import normalize, read_blocks
from book_distiller.parsers.quality import QualityAccumulator, QualityThresholds


def metadata(**kwargs):
    now = datetime.now(timezone.utc)
    return ParserMetadata(parser="fixture", parser_version="1", source_sha256="0"*64,
        input_format="pdf", started_at=now, completed_at=now, status="success", **kwargs)


@pytest.mark.parametrize("values", [
    {"source_page_index": 0, "source_page_number": 2},
    {"source_page_index": -1, "source_page_number": 0},
    {"source_page_number": 1},
    {"char_start": 4, "char_end": 3},
    {"char_start": 1},
])
def test_source_span_rejects_invalid(values):
    with pytest.raises(ValidationError):
        SourceSpan(block_id="blk_000001", parser="fixture", **values)


def test_source_span_conventions():
    span = SourceSpan(block_id="blk_000001", parser="fixture", source_page_index=0, source_page_number=1)
    assert span.printed_page_label is None
    assert SourceSpan(block_id="blk_000001", parser="plaintext").source_page_number is None


def test_block_range_validation():
    with pytest.raises(ValidationError):
        NormalizedBlock(block_id="blk_000001", type="paragraph", text="abc", chapter_id="ch_0001", order=1,
            source_spans=[SourceSpan(block_id="blk_000001", parser="fixture", char_start=0, char_end=4)])


def test_all_block_types_expressible():
    for kind in BlockType:
        assert NormalizedBlock(block_id="blk_000001", type=kind, text="", chapter_id="ch_0001", order=1).type == kind


def test_deterministic_structure(isolated_storage, sample_source, tmp_path):
    manifest = IngestService(isolated_storage).ingest(sample_source).manifest
    items = [ParserItem(BlockType.TITLE, "Title", "#/t"),
        ParserItem(BlockType.HEADING, "Chapter A", "#/h1", heading_level=2),
        ParserItem(BlockType.HEADING, "Section A", "#/h2", heading_level=3),
        ParserItem(BlockType.PARAGRAPH, "Keep this.\r\nKeep this.", "#/p"),
        ParserItem(BlockType.HEADING, "Nested", "#/h3", heading_level=4),
        ParserItem(BlockType.HEADING, "Section B", "#/h4", heading_level=3),
        ParserItem(BlockType.HEADING, "Chapter B", "#/h5", heading_level=2)]
    m = metadata().model_copy(update={"source_sha256": manifest.source.sha256})
    blocks_by_run = []
    for run in range(2):
        out = tmp_path / str(run);out.mkdir()
        book, quality = normalize(ParserResult(iter(items), m), manifest, out)
        blocks = list(read_blocks(out / "blocks.jsonl"));blocks_by_run.append(blocks)
        assert [c.chapter_id for c in book.chapters] == ["ch_0001", "ch_0002", "ch_0003"]
        assert book.chapters[0].synthetic
        sections = book.chapters[1].sections
        assert sections[1].parent_section_id == sections[0].section_id
        assert sections[2].parent_section_id is None
        assert blocks[3].text == "Keep this.\nKeep this."
        assert blocks[4].section_id == "sec_0002_0002"
        assert quality.source_map_coverage == 1
        assert NormalizedBook.model_validate_json(book.model_dump_json()) == book
    assert blocks_by_run[0] == blocks_by_run[1]


def test_synthetic_and_special_content(isolated_storage, sample_source, tmp_path):
    manifest = IngestService(isolated_storage).ingest(sample_source).manifest
    items = [ParserItem(kind, "Retained content", f"#/{kind.value}") for kind in (BlockType.PARAGRAPH, BlockType.TABLE, BlockType.FIGURE, BlockType.CODE, BlockType.FORMULA)]
    m = metadata().model_copy(update={"source_sha256": manifest.source.sha256})
    book, _ = normalize(ParserResult(items, m), manifest, tmp_path)
    assert len(book.chapters) == 1 and book.chapters[0].synthetic
    assert book.tables[0].content_id == "tbl_000001"
    assert book.figures[0].block_id == "blk_000003"
    assert book.code_blocks[0].asset_path is None
    assert all(s.source_page_number is None for b in read_blocks(tmp_path / "blocks.jsonl") for s in b.source_spans)


def test_quality_thresholds():
    accumulator = QualityAccumulator(total_pages=4)
    for number in range(1,4):
        accumulator.add(NormalizedBlock(block_id=f"blk_{number:06d}", type="paragraph", text="A sentence.", chapter_id="ch_0001", order=number,
            source_spans=[SourceSpan(block_id=f"blk_{number:06d}", parser="fixture", source_page_index=number-1, source_page_number=number)]))
    report = accumulator.finish(metadata(), QualityThresholds())
    assert report.status == "pass" and report.empty_page_ratio == .25
    stricter = accumulator.finish(metadata(), QualityThresholds(max_empty_page_ratio=.2))
    assert stricter.status == "review_recommended"
    assert stricter.issues[0].code == "EMPTY_PAGES"
    assert stricter.issues[0].threshold == .2


def test_quality_failed_and_unmapped():
    assert QualityAccumulator(None).finish(metadata(), QualityThresholds()).status == "failed"
    accumulator = QualityAccumulator(None)
    accumulator.add(NormalizedBlock(block_id="blk_000001", type="paragraph", text="Text", chapter_id="ch_0001", order=1))
    report = accumulator.finish(metadata(warnings=["warning"]), QualityThresholds())
    assert report.status == "review_recommended"
    assert report.total_pages is None
    assert {issue.code for issue in report.issues} == {"LOW_SOURCE_MAP_COVERAGE", "PARSER_DIAGNOSTICS"}


def test_whitespace_only_is_not_usable_text():
    accumulator = QualityAccumulator(None)
    accumulator.add(NormalizedBlock(block_id="blk_000001", type="paragraph", text=" \n ", chapter_id="ch_0001", order=1))
    assert accumulator.finish(metadata(), QualityThresholds()).status == "failed"


def test_out_of_range_page_is_not_coverage():
    accumulator = QualityAccumulator(2)
    accumulator.add(NormalizedBlock(block_id="blk_000001", type="paragraph", text="Text", chapter_id="ch_0001", order=1,
        source_spans=[SourceSpan(block_id="blk_000001", parser="fixture", source_page_index=5, source_page_number=6)]))
    assert accumulator.finish(metadata(), QualityThresholds()).source_map_coverage == 0
