"""Deterministic UTF-8 paragraph reader; no third-party parser or inferred pages."""
from datetime import datetime, timezone
from pathlib import Path
from collections.abc import Iterator
from book_distiller.parsers.base import ParserItem, ParserResult, ParserSpan
from book_distiller.core.models.normalized import BlockType, ParserMetadata
from book_distiller.core.errors import PipelineError


class PlainTextAdapter:
    name = "plaintext"
    version = "1.0"

    def parse(self, source: Path, raw_directory: Path, source_sha256: str) -> ParserResult:
        """Copy UTF-8 raw text and yield paragraphs without fabricated page numbers."""
        started = datetime.now(timezone.utc)
        destination = raw_directory / "plaintext.txt"
        try:
            with source.open("r", encoding="utf-8-sig", newline="") as incoming, destination.open("w", encoding="utf-8", newline="") as outgoing:
                while chunk := incoming.read(1024 * 1024):
                    outgoing.write(chunk)
        except UnicodeError as exc:
            raise PipelineError("TXT must be UTF-8; convert its encoding before ingesting a new source.") from exc
        metadata = ParserMetadata(parser=self.name, parser_version=self.version, source_sha256=source_sha256,
            input_format="txt", started_at=started, completed_at=datetime.now(timezone.utc), status="success")
        return ParserResult(self.iter_paragraphs(destination), metadata)

    @staticmethod
    def iter_paragraphs(path: Path) -> Iterator[ParserItem]:
        """Use source line intervals as locators; retain nonblank line breaks."""
        lines: list[str] = []
        start = 1
        end = 0
        with path.open(encoding="utf-8") as stream:
            for number, line in enumerate(stream, 1):
                if line.strip():
                    if not lines:
                        start = number
                    lines.append(line.rstrip("\r\n"))
                    end = number
                elif lines:
                    locator = f"lines:{start}-{end}"
                    yield ParserItem(BlockType.PARAGRAPH, "\n".join(lines), locator, spans=(ParserSpan(locator=locator),))
                    lines = []
            if lines:
                locator = f"lines:{start}-{end}"
                yield ParserItem(BlockType.PARAGRAPH, "\n".join(lines), locator, spans=(ParserSpan(locator=locator),))
