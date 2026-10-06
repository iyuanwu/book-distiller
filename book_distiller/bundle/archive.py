"""Bounded ZIP streaming. Never extractall, follow links, or trust archive paths."""
from dataclasses import dataclass
from pathlib import PurePosixPath
import hashlib
import stat
import unicodedata
import zipfile
from book_distiller.core.errors import StorageError
from book_distiller.bundle.models import BookBundleManifest
from book_distiller.pipeline.canonical import json_hash

MANIFEST = 'bundle_manifest.json'


@dataclass(frozen=True)
class BundleLimits:
    max_files: int = 50000
    max_total_bytes: int = 8 * 1024**3
    max_file_bytes: int = 2 * 1024**3
    max_compression_ratio: int = 2000
    max_manifest_bytes: int = 16 * 1024**2


def safe_name(name):
    if (not name or '\\' in name or '\x00' in name or ':' in name
        or name.startswith('/') or unicodedata.normalize('NFC', name) != name
        or any(c in name for c in '\r\n')
        or any(p in ('', '.', '..') or p.endswith((' ', '.')) for p in name.split('/'))):
        raise StorageError('BUNDLE_UNSAFE_PATH: ' + repr(name))
    if PurePosixPath(name).as_posix() != name:
        raise StorageError('BUNDLE_NONCANONICAL_PATH')
    return name


def content_hash(manifest):
    value = manifest.model_dump(mode='json') if hasattr(manifest, 'model_dump') else dict(manifest)
    for key in ('created_at', 'bundle_content_hash'):
        value.pop(key, None)
    return json_hash(value)


def regular(info):
    mode = info.external_attr >> 16
    kind = stat.S_IFMT(mode)
    if info.flag_bits & 1 or kind not in (0, stat.S_IFREG, stat.S_IFDIR):
        raise StorageError('BUNDLE_UNSAFE_ENTRY: encrypted, symlink or special file')
    if (info.is_dir() and kind == stat.S_IFREG) or (not info.is_dir() and kind == stat.S_IFDIR):
        raise StorageError('BUNDLE_ENTRY_TYPE_MISMATCH')


def validate_archive(archive, stage, limits=BundleLimits()):
    """Validate complete inventory/bytes before handing staging to semantic validation."""
    try:
        with zipfile.ZipFile(archive) as z:
            infos = z.infolist()
            if len(infos) > limits.max_files:
                raise StorageError('BUNDLE_LIMIT: file count')
            names, folded, total = {}, set(), 0
            for info in infos:
                regular(info)
                name = safe_name(info.filename[:-1] if info.is_dir() else info.filename)
                if name.casefold() in folded:
                    raise StorageError('BUNDLE_DUPLICATE_PATH')
                names[name] = info
                folded.add(name.casefold())
                total += info.file_size
                if (info.file_size > limits.max_file_bytes or total > limits.max_total_bytes
                    or info.file_size > limits.max_compression_ratio * max(1, info.compress_size)):
                    raise StorageError('BUNDLE_LIMIT: size or compression ratio')
            if MANIFEST not in names or names[MANIFEST].is_dir() or names[MANIFEST].file_size > limits.max_manifest_bytes:
                raise StorageError('BUNDLE_MANIFEST_MISSING_OR_OVERSIZED')
            with z.open(names[MANIFEST]) as stream:
                raw = stream.read(limits.max_manifest_bytes + 1)
                if len(raw) > limits.max_manifest_bytes:
                    raise StorageError('BUNDLE_LIMIT: manifest')
            manifest = BookBundleManifest.model_validate_json(raw)
            if content_hash(manifest) != manifest.bundle_content_hash:
                raise StorageError('BUNDLE_CONTENT_HASH_MISMATCH')
            declared = {f.relative_path: f for f in manifest.files}
            if len(declared) != len(manifest.files) or MANIFEST in declared:
                raise StorageError('BUNDLE_DUPLICATE_INVENTORY')
            for name in declared:
                safe_name(name)
            files = {n for n,i in names.items() if not i.is_dir()}
            if files != set(declared) | {MANIFEST}:
                raise StorageError('BUNDLE_INVENTORY_MISMATCH: undeclared or missing file')
            for n,i in names.items():
                if i.is_dir() and not any(p.startswith(n + '/') for p in declared):
                    raise StorageError('BUNDLE_UNDECLARED_DIRECTORY')
                if not i.is_dir() and any(p.startswith(n + '/') for p in names):
                    raise StorageError('BUNDLE_PATH_CONFLICT')
            actual_total = len(raw)
            for name, entry in declared.items():
                info = names[name]
                if info.file_size != entry.size:
                    raise StorageError('BUNDLE_SIZE_MISMATCH')
                destination = stage / name
                destination.parent.mkdir(parents=True, exist_ok=True)
                digest, size = hashlib.sha256(), 0
                with z.open(info) as source, destination.open('xb') as output:
                    while chunk := source.read(1024 * 1024):
                        size += len(chunk)
                        actual_total += len(chunk)
                        if size > min(entry.size, limits.max_file_bytes) or actual_total > limits.max_total_bytes:
                            raise StorageError('BUNDLE_LIMIT: streamed bytes')
                        output.write(chunk)
                        digest.update(chunk)
                if size != entry.size or digest.hexdigest() != entry.sha256:
                    raise StorageError('BUNDLE_CHECKSUM_MISMATCH: ' + name)
            return manifest
    except StorageError:
        raise
    except (OSError, ValueError, KeyError, RuntimeError, zipfile.BadZipFile, NotImplementedError) as exc:
        raise StorageError('BUNDLE_INVALID: ' + str(exc)) from exc
