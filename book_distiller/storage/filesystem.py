"""Streaming source storage and atomic manifest publication."""
from contextlib import contextmanager
import fcntl
import hashlib
import os
from pathlib import Path
import re
import shutil
import tempfile
import unicodedata
from collections.abc import Iterator
from pydantic import ValidationError as ModelValidationError
from book_distiller.core.errors import StorageError, ValidationError
from book_distiller.core.models.library import Manifest, SUPPORTED_EXTENSIONS

CHUNK_SIZE = 1024 * 1024


def validate_source(path: Path) -> Path:
    """Accept supported regular files only; never parse their contents."""
    path = path.expanduser()
    if path.suffix.lower() not in SUPPORTED_EXTENSIONS:
        raise ValidationError(f"Unsupported source format: {path.suffix or '(none)'}")
    if not path.is_file():
        raise ValidationError(f"Source file does not exist or is not a regular file: {path}")
    return path.resolve(strict=True)


def hash_source(path: Path) -> tuple[str, int]:
    """Hash in bounded chunks and reject changes observed during the read."""
    digest = hashlib.sha256()
    size = 0
    with path.open("rb") as stream:
        before = os.fstat(stream.fileno())
        while chunk := stream.read(CHUNK_SIZE):
            digest.update(chunk)
            size += len(chunk)
        after = os.fstat(stream.fileno())
    current = path.stat()
    signature = lambda stat: (stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns, stat.st_ctime_ns)
    if signature(before) != signature(after) or signature(after) != signature(current) or size != after.st_size:
        raise StorageError("Source changed during ingest; retry with a stable file.")
    return digest.hexdigest(), size


def make_slug(title: str) -> str:
    """Keep readable Unicode letters/digits without path separators."""
    normalized = unicodedata.normalize("NFKC", title).casefold()
    return re.sub(r"[^\w]+", "-", normalized, flags=re.UNICODE).replace("_", "-").strip("-")[:80].rstrip("-") or "book"


class FileStorage:
    """Own the Library directory, staging files, and ingest coordination."""

    def __init__(self, root: Path):
        self.root = root.resolve()
        self.library = self.root / "library"

    @contextmanager
    def locked(self) -> Iterator[None]:
        """Serialize cooperating readers/writers across CLI processes."""
        self.root.mkdir(parents=True, exist_ok=True)
        data = self.root / "data"
        data.mkdir(exist_ok=True)
        if data.is_symlink() or self.library.is_symlink():
            raise StorageError("Data and Library directories must not be symlinks.")
        with (data / ".ingest.lock").open("a+b") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            try:
                self.library.mkdir(exist_ok=True)
                yield
            finally:
                fcntl.flock(lock.fileno(), fcntl.LOCK_UN)

    def available_slug(self, title: str, occupied: set[str]) -> str:
        """Avoid both indexed slugs and existing unindexed filesystem entries."""
        base = make_slug(title)
        slug, suffix = base, 2
        while slug in occupied or (self.library / slug).exists() or (self.library / slug).is_symlink():
            slug = f"{base}-{suffix}"
            suffix += 1
        return slug

    def stage(self) -> Path:
        """Create a unique private staging directory on the Library volume."""
        return Path(tempfile.mkdtemp(prefix=".ingest-", dir=self.library))

    def copy_source(self, source: Path, destination: Path, expected_hash: str) -> None:
        """Stream a new copy and verify it matches the source hash."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with source.open("rb") as incoming, destination.open("xb") as outgoing:
            shutil.copyfileobj(incoming, outgoing, length=CHUNK_SIZE)
            outgoing.flush()
            os.fsync(outgoing.fileno())
        if hash_source(destination)[0] != expected_hash:
            raise StorageError("Source changed while copying; ingest rolled back.")

    def write_manifest(self, directory: Path, manifest: Manifest) -> None:
        """Validate and atomically replace JSON within the private staging area."""
        validated = Manifest.model_validate_json(manifest.model_dump_json())
        temporary = directory / "manifest.json.tmp"
        with temporary.open("x", encoding="utf-8") as stream:
            stream.write(validated.model_dump_json(indent=2) + "\n")
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(directory / "manifest.json")

    def read_manifest(self, directory: Path) -> Manifest:
        """Read a supported manifest, reporting corrupt/missing inventories."""
        try:
            return Manifest.model_validate_json((directory / "manifest.json").read_text(encoding="utf-8"))
        except (OSError, ValueError, ModelValidationError) as exc:
            raise StorageError(f"Invalid or missing manifest in {directory}: {exc}") from exc

    def publish(self, stage: Path, slug: str) -> Path:
        """Publish a prepared directory; called under the Library lock."""
        destination = self.library / slug
        if destination.exists() or destination.is_symlink():
            raise StorageError(f"Library destination already exists: {destination}")
        stage.rename(destination)
        return destination

    def discard(self, directory: Path) -> None:
        """Remove only a directory created by the current failed ingest."""
        shutil.rmtree(directory)

    def check_inventory(self, slugs: set[str]) -> None:
        """Report interrupted writes; never silently delete unindexed user files."""
        for path in self.library.iterdir():
            if path.name.startswith(".ingest-") or (path.is_dir() and path.name not in slugs and (path / "manifest.json").exists()):
                raise StorageError(f"Unindexed or interrupted ingest at {path}. Preserve it and reconcile the manifest with the database before retrying.")
