"""Explicit invalidation state complements content-based dependency checking."""
from book_distiller.pipeline.ai_tasks import safe_child
from book_distiller.pipeline.files import write_json
import json


def read(directory):
    path=safe_child(directory,'runtime/stale.json')
    return json.loads(path.read_text()) if path.exists() else {}


def invalidate(directory,plan,run_id):
    state=read(directory)
    for stage in [plan['requested_stage'],*plan['downstream_stale']]:
        for ch in plan['affected_chapters'] if stage in ('claims','atoms') else [None]:
            state[stage+(':'+ch if ch else '')]={'run_id':str(run_id),'reason':'Explicit scoped rerun'}
    write_json(safe_child(directory,'runtime/stale.json'),state)
    reader_status(directory,'stale','NEEDS RE-VERIFICATION: Scoped rerun')


def complete(directory,stage,chapter=None):
    state=read(directory);state.pop(stage+(':'+chapter if chapter else ''),None)
    if stage=='atoms':state.pop('claims:'+chapter,None)
    path=safe_child(directory,'runtime/stale.json')
    if path.parent.exists():write_json(path,state)


def reader_status(directory, status, reason):
    """Small live projection outside immutable exports, readable over file://."""
    from book_distiller.pipeline.files import write_bytes
    if status == 'display_stale' and any(k != 'render' for k in read(directory)):
        status, reason = 'stale', 'NEEDS RE-VERIFICATION: Human semantic edit or unfinished rerun'
    payload = json.dumps({'status':status, 'reason':reason}, ensure_ascii=True)
    write_bytes(safe_child(directory,'reader-status.js'),
                ('window.BOOK_DISTILLER_LIVE_STATUS = ' + payload + ';\n').encode())


def human_edit(directory, target):
    stages = {'classification':'claims','atomic_claim':'atoms','knowledge_atom':'book',
              'concept':'book','core_idea':'book','mental_model':'book','meta_principle':'verification'}
    stage = stages[target['object_type']]
    from book_distiller.pipeline.dependencies import downstream
    state = read(directory)
    for node in [stage, *downstream(stage)]:
        chapters = [target['chapter_id']] if node in ('claims','atoms') and target['chapter_id'] else [None]
        if node in ('claims','atoms') and not target['chapter_id']:
            book = json.loads((directory/'parsed/normalized/book.json').read_text())
            chapters = [c['chapter_id'] for c in book['chapters']]
        for chapter in chapters:
            state[node + (':' + chapter if chapter else '')] = {'reason':'Human semantic edit'}
    write_json(safe_child(directory,'runtime/stale.json'), state)
    reader_status(directory,'stale','NEEDS RE-VERIFICATION: Human semantic edit')
