"""Stream neutral parser items into canonical blocks and a compact book index."""
from collections.abc import Iterator
from datetime import datetime, timezone
import json
from pathlib import Path
import unicodedata
from book_distiller.core.models.library import Manifest
from book_distiller.core.models.normalized import (
    BlockType, NormalizedBlock, NormalizedBook, NormalizationMetadata, SourceSpan, SpecialContent,
)
from book_distiller.normalize.chapters import ChapterBuilder
from book_distiller.parsers.base import ParserResult
from book_distiller.parsers.quality import QualityAccumulator, QualityReport, QualityThresholds


def read_blocks(path: Path) -> Iterator[NormalizedBlock]:
    """Read validated blocks one JSONL line at a time."""
    with path.open(encoding="utf-8") as stream:
        for line in stream:
            yield NormalizedBlock.model_validate_json(line)


def normalize(result: ParserResult, manifest: Manifest, directory: Path,
              thresholds: QualityThresholds | None = None) -> tuple[NormalizedBook, QualityReport]:
    """Normalize structure without rewriting, summarizing, or deduplicating text."""
    spool = directory / ".items.jsonl"
    top_level: int | None = None
    # A bounded-memory first pass determines the global highest effective heading.
    with spool.open("w", encoding="utf-8") as stream:
        for item in result.items:
            if item.type == BlockType.HEADING and item.heading_level is not None:
                top_level = min(top_level, item.heading_level) if top_level is not None else item.heading_level
            stream.write(json.dumps({"type": item.type.value, "text": item.text, "locator": item.locator,
                "heading_level": item.heading_level, "caption": item.caption, "raw_label": item.raw_label,
                "spans": [{"page_number": s.page_number, "bbox": s.bbox.model_dump() if s.bbox else None,
                    "locator": s.locator, "raw_char_range": s.raw_char_range} for s in item.spans]}, ensure_ascii=False) + "\n")
    hierarchy = ChapterBuilder(top_level)
    quality = QualityAccumulator(result.total_pages)
    special: dict[str, list[SpecialContent]] = {"figures": [], "tables": [], "formulas": [], "code_blocks": []}
    kinds = {"figure": ("figures", "fig"), "table": ("tables", "tbl"), "formula": ("formulas", "frm"), "code": ("code_blocks", "code")}
    count = 0
    with spool.open(encoding="utf-8") as incoming, (directory / "blocks.jsonl").open("w", encoding="utf-8") as outgoing:
        for count, line in enumerate(incoming, 1):
            item = json.loads(line)
            block_id = f"blk_{count:06d}"
            text = unicodedata.normalize("NFC", item["text"].replace("\r\n", "\n").replace("\r", "\n"))
            level = item["heading_level"] if item["type"] == "heading" else None
            chapter_id, section_id = hierarchy.locate(text, level, block_id)
            spans: list[SourceSpan] = []
            raw_spans = item["spans"] or ([{"page_number": None, "bbox": None, "locator": item["locator"], "raw_char_range": None}] if item["locator"] else [])
            for raw in raw_spans:
                page = raw["page_number"]
                start = end = None
                char_range = raw["raw_char_range"]
                if text == item["text"] and char_range and 0 <= char_range[0] <= char_range[1] <= len(text):
                    start, end = char_range
                elif len(raw_spans) == 1:
                    start, end = 0, len(text)
                spans.append(SourceSpan(block_id=block_id, source_page_index=page-1 if page is not None else None,
                    source_page_number=page, char_start=start, char_end=end, bbox=raw["bbox"],
                    parser=result.metadata.parser, parser_locator=raw["locator"] or item["locator"]))
            block = NormalizedBlock(block_id=block_id, type=item["type"], text=text, chapter_id=chapter_id,
                section_id=section_id, order=count, source_spans=spans,
                metadata={"heading_level": level, "raw_label": item["raw_label"],
                          "raw_char_ranges": [s["raw_char_range"] for s in raw_spans]})
            outgoing.write(block.model_dump_json() + "\n")
            quality.add(block)
            if item["type"] in kinds:
                collection, prefix = kinds[item["type"]]
                special[collection].append(SpecialContent(content_id=f"{prefix}_{len(special[collection])+1:06d}", block_id=block_id, caption=item["caption"]))
    spool.unlink()
    book = NormalizedBook(book_id=manifest.book.book_id, edition_id=manifest.edition.edition_id,
        source_sha256=manifest.source.sha256, title=manifest.edition.display_title or manifest.book.title,
        language=manifest.edition.language, chapters=hierarchy.chapters, block_count=count, **special,
        parser_metadata=result.metadata, normalization_metadata=NormalizationMetadata(source_raw_parser=result.metadata.parser,
            raw_artifact_version=result.raw_artifact_version, normalized_at=datetime.now(timezone.utc)))
    return book, quality.finish(result.metadata, thresholds or QualityThresholds())
