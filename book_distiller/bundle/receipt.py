"""Local import provenance. Artifact availability never manufactures a Task."""
import json
from pathlib import Path
from book_distiller.storage.filesystem import hash_source


def receipt(directory):
    path = directory / '.bundle-import.json'
    if not path.is_file() or path.is_symlink():
        return None
    value = json.loads(path.read_text(encoding='utf-8'))
    if value.get('receipt_version') != '1.0':
        raise ValueError('Unsupported bundle import receipt')
    return value


def available(directory, path):
    value = receipt(directory)
    if value is None:
        return False
    root = directory.resolve()
    resolved = path.resolve()
    if not resolved.is_relative_to(root) or not resolved.is_file():
        return False
    expected = value['files'].get(resolved.relative_to(root).as_posix())
    return expected is not None and hash_source(resolved)[0] == expected


def source_omitted(directory, manifest):
    value = receipt(directory)
    return bool(value and not value['source_included'] and value['source_sha256'] == manifest.source.sha256
                and available(directory, directory / 'manifest.json'))


def source_display(directory, manifest):
    original = Path(manifest.source.stored_path) if manifest.source.copy_mode == 'reference' else directory / manifest.source.stored_path
    exists = original.is_file()
    if receipt(directory):
        return {'path': manifest.source.stored_path, 'available': exists,
                'uri': '../' + manifest.source.stored_path if exists else None}
    return {'path': str(original), 'available': exists, 'uri': original.resolve().as_uri() if exists else None}


def external_rule_status(directory, rules, workflow=None):
    value = receipt(directory)
    if not value:
        return []
    # Compare actual available rule bodies, never treat imported hashes as instructions.
    from book_distiller.pipeline.canonical import json_hash
    present = {str(r['rule_id']):json_hash(r) for r in rules}
    return [dict(dep, availability='available' if present.get(str(dep['rule_id'])) == dep['sha256'] else 'missing_or_changed')
            for dep in value.get('external_rule_dependencies', [])
            if workflow is None or workflow in dep['applicable_workflows']]
