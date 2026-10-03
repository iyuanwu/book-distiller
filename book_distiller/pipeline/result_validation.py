"""Strict protocol validation; validates evidence IDs, never evidence semantics."""
from pydantic import ValidationError as PydanticValidationError
from book_distiller.core.errors import ValidationError
from book_distiller.core.models.ai_tasks import AIRequest, BookClassification, ContextPackage


class ProtocolError(ValidationError):
    """Machine-readable protocol rejection with a user-facing explanation."""
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def validate_result(text: str, request: AIRequest, context: ContextPackage) -> BookClassification:
    """Reject invalid schemas, mismatched identity/provenance and unseen evidence."""
    try:
        result = BookClassification.model_validate_json(text)
    except (PydanticValidationError, ValueError) as exc:
        raise ProtocolError("RESULT_SCHEMA_INVALID", str(exc)) from exc
    if result.task_id != request.task_id or result.book_id != request.book_id or result.edition_id != request.edition_id:
        raise ProtocolError("RESULT_TASK_MISMATCH", "Result identity does not match this task.")
    expected = {"context_hash": request.context_hash, "source_sha256": request.source_sha256,
        "normalized_schema_version": request.normalized_document.schema_version,
        "normalized_document_hash": request.normalized_document.document_hash,
        "workflow_version": request.workflow_version, "prompt_version": request.prompt_version,
        "context_package_version": request.context_package_version,
        "source_parse_quality": context.source_parse_quality}
    for field, value in expected.items():
        if getattr(result, field) != value:
            raise ProtocolError("STALE_CONTEXT", f"Result {field} does not match its request; prepare a new task.")
    available = {block.block_id for block in context.blocks}
    if any(e.block_id not in available for e in result.evidence):
        raise ProtocolError("RESULT_EVIDENCE_INVALID", "Evidence must refer only to selected Context Package blocks.")
    return result
