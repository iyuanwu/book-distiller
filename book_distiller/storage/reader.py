"""Immutable reader generations with one atomic public output pointer."""
import json
import os
from pathlib import Path
from book_distiller.core.errors import StorageError
from book_distiller.storage.filesystem import hash_source
from book_distiller.storage.knowledge import switch


REQUIRED = {'index.html', 'assets/app.js', 'assets/reader.css', 'assets/book-data.js'} | {
    'data/'+name+'.json' for name in ('book', 'progressive', 'concepts', 'knowledge', 'evidence', 'quality', 'search', 'cards', 'mindmap')
} | {'markdown/'+name+'.md' for name in ('L0', 'L1', 'L2', 'L3', 'knowledge-model', 'quality-report')}


def current(directory):
    pointer = directory/'output'
    if not pointer.is_symlink():
        if pointer.exists():
            raise StorageError('Reader output must be a managed link')
        return None
    link = Path(os.readlink(pointer))
    if len(link.parts) != 2 or link.parts[0] != '.reader-generations' or link.parts[1] in ('.', '..'):
        raise StorageError('Unsafe reader pointer')
    path = directory/link
    if path.parent.is_symlink() or path.is_symlink() or not path.is_dir():
        raise StorageError('Missing or unsafe reader generation')
    return path


def intact(path):
    try:
        if (path/'render_manifest.json').is_symlink() or any(p.is_symlink() for p in path.rglob('*')):
            return False
        manifest = json.loads((path/'render_manifest.json').read_text())
        files = {str(p.relative_to(path)) for p in path.rglob('*') if p.is_file() and p.name != 'render_manifest.json'}
        return REQUIRED <= files and files == set(manifest['artifact_hashes']) and all(
            not (path/n).is_symlink() and hash_source(path/n)[0] == h for n, h in manifest['artifact_hashes'].items())
    except (OSError, ValueError, KeyError, TypeError):
        return False


def publish(directory, destination):
    current(directory)
    switch(directory/'output', '.reader-generations/'+destination.name)
