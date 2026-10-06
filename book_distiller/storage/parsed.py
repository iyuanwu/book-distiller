from book_distiller.bundle.receipt import available as imported_available
"""Immutable parse generations with one atomic pointer for raw and normalized."""
from pathlib import Path
import json
import os
import shutil
from typing import Literal
from uuid import UUID
from book_distiller.core.errors import StorageError
from book_distiller.core.models.normalized import DocumentModel, NormalizedBook
from book_distiller.parsers.quality import QualityReport
from book_distiller.storage.filesystem import hash_source


class ParseReceipt(DocumentModel):
    status: Literal["completed"] = "completed"
    task_id: UUID
    cache_key: dict[str, str]
    artifact_hashes: dict[str, str]


def write_json(path: Path, value: DocumentModel | dict) -> None:
    """Write a complete UTF-8 JSON file and atomically replace its destination."""
    text = value.model_dump_json(indent=2) if isinstance(value, DocumentModel) else json.dumps(value, ensure_ascii=False, indent=2)
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("x", encoding="utf-8") as stream:
        stream.write(text + "\n")
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


class ParsedStorage:
    def __init__(self, book_root: Path):
        self.root = book_root
        self.pointer = book_root / "parsed"
        self.generations = book_root / ".parsed-generations"

    def check_staging(self) -> None:
        """Refuse silent recovery of incomplete parse attempts."""
        leftovers = list(self.root.glob(".parse-staging-*")) + list(self.root.glob(".parsed-link-*"))
        if leftovers:
            raise StorageError(f"Interrupted parse artifacts at {leftovers[0]}; preserve and inspect before retrying.")

    def current(self) -> Path | None:
        """Resolve only locally owned generation links; never follow arbitrary paths."""
        if not self.pointer.is_symlink():
            if self.pointer.exists():
                raise StorageError("parsed must be a managed generation link; existing results were preserved.")
            return None
        target = Path(os.readlink(self.pointer))
        if target.is_absolute() or len(target.parts) != 2 or target.parts[0] != ".parsed-generations":
            raise StorageError("Unsafe parsed generation link")
        path = self.root / target
        if self.generations.is_symlink() or path.is_symlink() or not path.is_dir():
            raise StorageError("Missing or unsafe parse generation")
        return path

    def prepare(self, task_id: UUID) -> Path:
        stage = self.root / f".parse-staging-{task_id}"
        (stage / "raw").mkdir(parents=True)
        (stage / "normalized").mkdir()
        return stage

    def seal(self, stage: Path, task_id: UUID, cache_key: dict[str, str]) -> ParseReceipt:
        """Record byte hashes so incomplete or altered results cannot be cache hits."""
        hashes = {p.relative_to(stage).as_posix(): hash_source(p)[0] for p in stage.rglob("*") if p.is_file()}
        receipt = ParseReceipt(task_id=task_id, cache_key=cache_key, artifact_hashes=hashes)
        write_json(stage / "completion.json", receipt)
        return receipt

    def inspect(self) -> tuple[ParseReceipt, NormalizedBook, QualityReport] | None:
        """Validate the current generation and stream-check artifact hashes."""
        current = self.current()
        if current is None:
            return None
        receipt = ParseReceipt.model_validate_json((current / "completion.json").read_text(encoding="utf-8"))
        required = {"raw/parse_metadata.json", "normalized/book.json", "normalized/blocks.jsonl", "normalized/quality.json"}
        if receipt.cache_key.get("parser") == "docling":
            required |= {"raw/docling.json", "raw/docling.md"}
        else:
            required.add("raw/plaintext.txt")
        if not required.issubset(receipt.artifact_hashes):
            raise StorageError("Incomplete parsed artifact inventory")
        artifacts = receipt.artifact_hashes
        if imported_available(self.root, current / 'completion.json'):
            artifacts = {k:v for k,v in artifacts.items() if k.startswith('normalized/')}
        for name, digest in artifacts.items():
            path = current / name
            if Path(name).is_absolute() or ".." in Path(name).parts or not path.resolve().is_relative_to(current.resolve()):
                raise StorageError("Unsafe parsed artifact path")
            if hash_source(path)[0] != digest:
                raise StorageError(f"Parsed artifact integrity mismatch: {name}")
        book = NormalizedBook.model_validate_json((current / "normalized/book.json").read_text(encoding="utf-8"))
        quality = QualityReport.model_validate_json((current / "normalized/quality.json").read_text(encoding="utf-8"))
        if quality.status == "failed":
            raise StorageError("A failed parse cannot be a successful generation")
        return receipt, book, quality

    def publish(self, stage: Path, task_id: UUID) -> Path:
        """Atomically switch raw and normalized together using one relative symlink."""
        self.generations.mkdir(exist_ok=True)
        if self.generations.is_symlink():
            raise StorageError("Parse generations must not be a symlink")
        destination = self.generations / str(task_id)
        stage.rename(destination)
        temporary = self.root / f".parsed-link-{task_id}"
        try:
            temporary.symlink_to(destination.relative_to(self.root), target_is_directory=True)
            temporary.replace(self.pointer)
        except BaseException:
            if temporary.is_symlink():
                temporary.unlink()
            # Preserve the fully written but unpublished generation for diagnosis.
            raise
        return destination

    def restore(self, old: Path | None, task_id: UUID) -> None:
        """Restore the previous pointer if task completion cannot be persisted."""
        if old is None:
            self.pointer.unlink(missing_ok=True)
        else:
            temporary = self.root / f".parsed-link-rollback-{task_id}"
            temporary.symlink_to(old.relative_to(self.root), target_is_directory=True)
            temporary.replace(self.pointer)

    def discard_stage(self, stage: Path) -> None:
        """Remove only this attempt's unpublished staging tree."""
        if stage.exists():
            shutil.rmtree(stage)
