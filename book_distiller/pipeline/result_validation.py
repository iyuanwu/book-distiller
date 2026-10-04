"""Strict protocol validation; validates evidence IDs, never evidence semantics."""
from pydantic import ValidationError as PydanticValidationError
from book_distiller.core.errors import ValidationError
from book_distiller.core.models.ai_tasks import AIRequest, BookClassification, ContextPackage
from book_distiller.core.models.synthesis import RESULT_MODELS


class ProtocolError(ValidationError):
    """Machine-readable protocol rejection with a user-facing explanation."""
    def __init__(self, code: str, message: str):
        self.code = code
        super().__init__(f"{code}: {message}")


def validate_result(text: str, request: AIRequest, context: ContextPackage) -> BookClassification:
    """Reject invalid schemas, mismatched identity/provenance and unseen evidence."""
    try:
        if request.task_type in RESULT_MODELS:
            model=RESULT_MODELS[request.task_type]
        elif request.task_type == 'classify_book':
            model = BookClassification
        else:
            from book_distiller.core.models.knowledge import ClaimResult, AtomResult
            model = ClaimResult if request.task_type=='extract_claims' else AtomResult
        result = model.model_validate_json(text)
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
    if request.task_type in RESULT_MODELS:
        from book_distiller.pipeline.synthesis_validation import validate_synthesis
        validate_synthesis(result,context)
        return result
    if request.task_type != 'classify_book':
        from book_distiller.pipeline.knowledge_validation import validate_knowledge
        validate_knowledge(result,context)
        return result
    available = {block.block_id for block in context.blocks}
    if any(e.block_id not in available for e in result.evidence):
        raise ProtocolError("RESULT_EVIDENCE_INVALID", "Evidence must refer only to selected Context Package blocks.")
    return result
