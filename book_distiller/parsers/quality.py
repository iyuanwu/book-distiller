"""Deterministic parser quality only, not evidence or knowledge quality."""
from typing import Literal
from pydantic import Field
from book_distiller.core.models.normalized import DocumentModel, NormalizedBlock, ParserMetadata, BlockType


class QualityThresholds(DocumentModel):
    min_source_map_coverage: float = Field(default=0.95, ge=0, le=1)
    max_empty_page_ratio: float = Field(default=0.30, ge=0, le=1)
    min_total_characters: int = Field(default=1, ge=0)


class QualityIssue(DocumentModel):
    code: str
    severity: Literal["warning", "critical"]
    message: str
    metric: float | int | None
    threshold: float | int | None


class QualityReport(DocumentModel):
    status: Literal["pass", "review_recommended", "failed"]
    total_pages: int | None
    pages_with_content: int | None
    empty_page_ratio: float | None
    total_blocks: int
    text_blocks: int
    total_characters: int
    heading_count: int
    blocks_with_source_span: int
    total_source_relevant_blocks: int
    source_map_coverage: float
    parse_warning_count: int
    parse_error_count: int
    issues: list[QualityIssue]
    thresholds: QualityThresholds


class QualityAccumulator:
    """Constant-memory counters, apart from the set of physical page numbers."""
    def __init__(self, total_pages: int | None):
        self.total_pages = total_pages
        self.pages: set[int] = set()
        self.blocks = self.text_blocks = self.characters = self.headings = self.mapped = 0

    def add(self, block: NormalizedBlock) -> None:
        self.blocks += 1
        self.text_blocks += bool(block.text.strip())
        self.characters += len(block.text)
        self.headings += block.type == BlockType.HEADING
        valid = [s for s in block.source_spans if (s.source_page_number is not None and 1 <= s.source_page_number <= self.total_pages if self.total_pages is not None else s.parser_locator is not None)]
        self.mapped += bool(valid)
        if block.text.strip() or block.type in (BlockType.FIGURE, BlockType.TABLE):
            self.pages.update(s.source_page_number for s in valid if s.source_page_number is not None and 1 <= s.source_page_number <= (self.total_pages or 0))

    def finish(self, metadata: ParserMetadata, thresholds: QualityThresholds) -> QualityReport:
        """No opaque score: each status follows a named threshold or parser outcome."""
        coverage = self.mapped / self.blocks if self.blocks else 0.0
        empty = 1 - len(self.pages) / self.total_pages if self.total_pages else None
        issues: list[QualityIssue] = []
        def issue(code: str, severity: str, message: str, metric: float | int | None, threshold: float | int | None) -> None:
            issues.append(QualityIssue(code=code, severity=severity, message=message, metric=metric, threshold=threshold))
        if not self.text_blocks or self.characters < thresholds.min_total_characters:
            issue("NO_USABLE_TEXT", "critical", "No usable text was extracted; inspect the source and parser output.", self.characters, thresholds.min_total_characters)
        if metadata.status == "failed":
            issue("PARSER_FAILED", "critical", "The parser reported failure.", None, None)
        if coverage < thresholds.min_source_map_coverage:
            issue("LOW_SOURCE_MAP_COVERAGE", "warning", "Some blocks lack reliable source locators.", coverage, thresholds.min_source_map_coverage)
        if empty is not None and empty > thresholds.max_empty_page_ratio:
            issue("EMPTY_PAGES", "warning", "Many physical pages have no extracted content.", empty, thresholds.max_empty_page_ratio)
        if metadata.warnings or metadata.errors or metadata.status == "partial_success":
            issue("PARSER_DIAGNOSTICS", "warning", "Review parser warnings/errors and partial conversion output.", len(metadata.warnings)+len(metadata.errors), 0)
        status = "failed" if any(i.severity == "critical" for i in issues) else "review_recommended" if issues else "pass"
        return QualityReport(status=status, total_pages=self.total_pages, pages_with_content=len(self.pages) if self.total_pages is not None else None,
            empty_page_ratio=empty, total_blocks=self.blocks, text_blocks=self.text_blocks, total_characters=self.characters,
            heading_count=self.headings, blocks_with_source_span=self.mapped, total_source_relevant_blocks=self.blocks,
            source_map_coverage=coverage, parse_warning_count=len(metadata.warnings), parse_error_count=len(metadata.errors), issues=issues, thresholds=thresholds)
