"""Immutable Book generations with one atomic pointer, preserving Chapter data."""
import json
import os
from pathlib import Path
from book_distiller.core.errors import StorageError
from book_distiller.storage.filesystem import hash_source
from book_distiller.storage.knowledge import Publication,switch

FILES={'concepts.json','core_ideas.json','mental_models.json','meta_principles.json','relationships.json','book_memory.json','provenance.json'}


def current(root):
    pointer=root/'knowledge/book'
    if not pointer.is_symlink():
        if pointer.exists(): raise StorageError('Book model pointer must be a managed symlink')
        return None
    target=Path(os.readlink(pointer))
    if len(target.parts)!=2 or target.parts[0]!='.book-generations': raise StorageError('Unsafe Book generation link')
    path=pointer.parent/target
    if pointer.parent.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.is_dir():
        raise StorageError('Missing or unsafe Book generation')
    if (path/'book_model.json').is_symlink(): raise StorageError('Unsafe Book manifest')
    model=json.loads((path/'book_model.json').read_text())
    if set(model['artifact_hashes'])!=FILES: raise StorageError('Incomplete Book generation')
    for name,digest in model['artifact_hashes'].items():
        if (path/name).is_symlink() or hash_source(path/name)[0]!=digest: raise StorageError('Book artifact integrity mismatch')
    return path


def publish(root,destination):
    current(root)
    pointer=root/'knowledge/book'
    old=os.readlink(pointer) if pointer.is_symlink() else None
    switch(pointer,f'.book-generations/{destination.name}')
    return Publication(pointer,old)
