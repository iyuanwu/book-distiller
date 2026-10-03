"""Retry-safe atomic protocol file writes; abandoned unique temps are never read."""
import json
import os
from pathlib import Path
import tempfile
from book_distiller.core.models.normalized import DocumentModel


def write_json(path: Path, value: DocumentModel | dict) -> None:
    """Validate serialization, fsync a unique temp, then replace the target atomically."""
    content = value.model_dump_json(indent=2) if isinstance(value, DocumentModel) else json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False)
    write_bytes(path, (content + "\n").encode("utf-8"))


def write_bytes(path: Path, content: bytes) -> None:
    """Publish or restore bytes without following a pre-existing temporary path."""
    descriptor, temporary_name = tempfile.mkstemp(prefix=f".{path.name}-", suffix=".tmp", dir=path.parent)
    temporary = Path(temporary_name)
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
