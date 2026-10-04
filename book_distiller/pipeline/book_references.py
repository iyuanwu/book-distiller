"""Generation-bound source references; never resolve through current Chapter links."""
import json
from book_distiller.core.errors import StorageError
from book_distiller.pipeline.ai_tasks import safe_child
from book_distiller.storage.filesystem import hash_source


def atom_ref(state,atom_id):
    chapter=state['atom_chapters'][atom_id]
    return {'chapter_id':chapter,'chapter_generation_id':state['dependencies']['chapters'][chapter]['generation_id'],'atom_id':atom_id}


def claim_ref(state,claim_id):
    chapters={state['atom_chapters'][a] for a,claims in state['atom_claims'].items() if claim_id in claims}
    if len(chapters)!=1: raise StorageError('Claim reference has no unique Chapter binding')
    chapter=next(iter(chapters))
    return {'chapter_id':chapter,'chapter_generation_id':state['dependencies']['chapters'][chapter]['generation_id'],'claim_id':claim_id}


def bind_objects(state,key):
    values=[]
    for obj in state[key]:
        row=dict(obj)
        for ids,refs in [('atom_ids','atom_refs'),('source_atom_ids','source_atom_refs')]:
            if ids in row: row[refs]=[atom_ref(state,a) for a in row[ids]]
        if 'claim_ids' in row: row['claim_refs']=[claim_ref(state,c) for c in row['claim_ids']]
        values.append(row)
    return values


def resolve_reference(directory,book_model,reference):
    """Resolve using the Book's immutable dependency path, even when it is stale."""
    chapter=reference['chapter_id']
    dep=book_model['dependencies']['chapters'].get(chapter)
    if dep is None or dep['generation_id']!=reference['chapter_generation_id']:
        raise StorageError('Reference Chapter generation mismatch')
    path=safe_child(directory,dep['path'])
    if hash_source(path/'chapter.json')[0]!=dep['manifest_hash']:
        raise StorageError('Referenced Chapter manifest changed')
    manifest=json.loads((path/'chapter.json').read_text())
    if (manifest['generation']['generation_id']!=dep['generation_id'] or
        manifest['generation']['book_id']!=book_model['book_id'] or
        manifest['generation']['edition_id']!=book_model['edition_id']):
        raise StorageError('Referenced generation identity mismatch')
    kind='atom' if 'atom_id' in reference else 'claim'
    name='atoms.json' if kind=='atom' else 'claims.jsonl'
    if (path/name).is_symlink() or hash_source(path/name)[0]!=manifest['artifact_hashes'][name]:
        raise StorageError('Referenced Chapter artifact changed')
    with (path/name).open() as stream:
        rows=json.load(stream)['atoms'] if kind=='atom' else (json.loads(line) for line in stream)
        for row in rows:
            if row[kind+'_id']==reference[kind+'_id']: return row
    raise StorageError('Referenced object missing from pinned generation')
