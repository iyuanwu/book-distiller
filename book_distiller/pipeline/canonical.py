"""Read verified Canonical documents without opening parser raw files."""
from dataclasses import dataclass
from pathlib import Path
import hashlib
import json
from book_distiller.core.errors import StorageError
from book_distiller.core.models.library import Manifest
from book_distiller.core.models.normalized import NormalizedBook
from book_distiller.core.models.ai_tasks import DocumentFingerprint
from book_distiller.parsers.quality import QualityReport
from book_distiller.storage.database import Database
from book_distiller.storage.filesystem import hash_source
from book_distiller.storage.parsed import ParsedStorage, ParseReceipt


def canonical_json(value: dict) -> str:
    """Stable JSON for protocol hashing, without whitespace or ASCII escaping."""
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False)


def json_hash(value: dict) -> str:
    return hashlib.sha256(canonical_json(value).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class CanonicalDocument:
    book: NormalizedBook
    quality: QualityReport
    blocks_path: Path
    fingerprint: DocumentFingerprint


def load_canonical(book_root: Path, manifest: Manifest, database: Database) -> CanonicalDocument:
    """Verify only Canonical artifacts/receipt and completed parse Task."""
    storage = ParsedStorage(book_root)
    storage.check_staging()
    generation = storage.current()
    if generation is None:
        raise StorageError("NOT_PARSED: run book parse before preparing an AI task.")
    receipt = ParseReceipt.model_validate_json((generation / "completion.json").read_text(encoding="utf-8"))
    if database.task_status(receipt.task_id) != "completed":
        raise StorageError("PARSE_NOT_COMPLETED: classification requires a successful parse.")
    hashes: dict[str, str] = {}
    for filename in ("book.json", "blocks.jsonl", "quality.json"):
        path = generation / "normalized" / filename
        if not path.resolve().is_relative_to(generation.resolve()):
            raise StorageError("Unsafe normalized artifact path")
        digest = hash_source(path)[0]
        if digest != receipt.artifact_hashes.get("normalized/" + filename):
            raise StorageError(f"CANONICAL_INTEGRITY: {filename} does not match its parse receipt.")
        hashes[filename] = digest
    book = NormalizedBook.model_validate_json((generation / "normalized/book.json").read_text(encoding="utf-8"))
    quality = QualityReport.model_validate_json((generation / "normalized/quality.json").read_text(encoding="utf-8"))
    if quality.status == "failed":
        raise StorageError("PARSE_FAILED: classification is not permitted.")
    if (book.book_id, book.edition_id, book.source_sha256) != (manifest.book.book_id, manifest.edition.edition_id, manifest.source.sha256):
        raise StorageError("Canonical identity does not match the indexed Edition")
    fingerprint = DocumentFingerprint(parse_task_id=receipt.task_id, schema_version=book.schema_version,
        normalizer_version=book.normalization_metadata.normalizer_version,
        book_sha256=hashes["book.json"], blocks_sha256=hashes["blocks.jsonl"], quality_sha256=hashes["quality.json"],
        document_hash=json_hash({"parse_task_id": str(receipt.task_id), **hashes}))
    return CanonicalDocument(book, quality, generation / "normalized/blocks.jsonl", fingerprint)
