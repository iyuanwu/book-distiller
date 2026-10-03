"""Shared string-valued lifecycle definitions; no runner is implemented."""
from enum import StrEnum


class BookStatus(StrEnum):
    PENDING = "pending"
    PROCESSING = "processing"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    NEEDS_REVIEW = "needs_review"


class StageStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"
    NEEDS_REVIEW = "needs_review"


class ErrorSeverity(StrEnum):
    CRITICAL = "critical"
    RECOVERABLE = "recoverable"
    WARNING = "warning"


class DistillationMode(StrEnum):
    FAST = "fast"
    STANDARD = "standard"
    DEEP = "deep"
