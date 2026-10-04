"""One immutable evaluation generation; Knowledge Model is never rewritten."""
import json,os
from pathlib import Path
from book_distiller.core.errors import StorageError
from book_distiller.storage.knowledge import Publication,switch
from book_distiller.storage.filesystem import hash_source

FILES={'citations.jsonl','claim_assessments.jsonl','atom_assessments.jsonl','idea_assessments.json','model_assessments.json','principle_assessments.json','coverage_review.json','review_issues.json','quality_report.json','quality_report.md','provenance.json'}

def current(directory):
    pointer=directory/'verification/current'
    if not pointer.is_symlink():
        if pointer.exists():raise StorageError('Verification pointer must be a managed link')
        return None
    link=Path(os.readlink(pointer))
    if len(link.parts)!=2 or link.parts[0]!='generations':raise StorageError('Unsafe Verification pointer')
    path=pointer.parent/link
    if pointer.parent.is_symlink() or path.parent.is_symlink() or path.is_symlink() or not path.is_dir():raise StorageError('Missing verification generation')
    if (path/'manifest.json').is_symlink():raise StorageError('Unsafe verification manifest')
    data=json.loads((path/'manifest.json').read_text())
    if set(data['artifact_hashes'])!=FILES:raise StorageError('Incomplete verification generation')
    for name,digest in data['artifact_hashes'].items():
        if (path/name).is_symlink() or hash_source(path/name)[0]!=digest:raise StorageError('Verification generation corrupt')
    return path

def publish(directory,destination):
    current(directory);pointer=directory/'verification/current';old=os.readlink(pointer) if pointer.is_symlink() else None
    switch(pointer,f'generations/{destination.name}')
    return Publication(pointer,old)
