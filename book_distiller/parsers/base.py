"""Small neutral adapter contract; Docling objects stop at the adapter boundary."""
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from book_distiller.core.models.normalized import BlockType, BoundingBox, ParserMetadata


@dataclass(frozen=True)
class ParserSpan:
    page_number: int | None = None
    bbox: BoundingBox | None = None
    locator: str | None = None
    # Raw character ranges are provenance metadata, not normalized offsets.
    raw_char_range: tuple[int, int] | None = None


@dataclass(frozen=True)
class ParserItem:
    type: BlockType
    text: str
    locator: str | None
    heading_level: int | None = None
    spans: tuple[ParserSpan, ...] = ()
    caption: str | None = None
    raw_label: str | None = None


@dataclass
class ParserResult:
    items: Iterable[ParserItem]
    metadata: ParserMetadata
    total_pages: int | None = None
    raw_artifact_version: str = "1.0"


class ParserAdapter(Protocol):
    name: str
    version: str

    def parse(self, source: Path, raw_directory: Path, source_sha256: str) -> ParserResult:
        """Save raw artifacts and return neutral items in reading order."""
        ...
