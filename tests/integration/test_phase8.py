"""Phase 8 protocol/recovery tests. Model responses here are test doubles."""
import json
from pathlib import Path
import pytest
from test_chapter_tasks import chapter_book,all_claims,submit,atom_result,claim_result
from test_book_tasks import response
from book_distiller.pipeline.runs import RunService
from book_distiller.pipeline.dependencies import RerunPlanner,downstream
from book_distiller.human.resolver import EffectiveKnowledgeResolver
from book_distiller.human.service import HumanService,guidance
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.core.errors import ValidationError


def first(chapter_book):
    ai,ch,ingest=chapter_book
    _,task=all_claims(chapter_book);submit(ai,task,atom_result(task))
    return next(v for v in EffectiveKnowledgeResolver(ingest.library_path).inventory().values() if v['target_ref']['object_type']=='atomic_claim')


def action(obj,kind,patch=None):
    return {'action':kind,'target_ref':obj['target_ref'],'base_object_hash':obj['base_object_hash'],'patch':patch or {}}


def test_plan_scope_determinism():
    p=RerunPlanner();generations={f'{s}:ch_{i:04d}':str(i) for s in ('claims','atoms') for i in (1,2,3)}
    a=p.plan('claims',['ch_0001','ch_0002','ch_0003'],'ch_0002',generations=generations)
    assert a==p.plan('claims',['ch_0003','ch_0001','ch_0002'],'ch_0002',generations=generations)
    assert len(a.steps)==5 and len(a.reused_generations)==4
    assert downstream('parse')==['classification','claims','atoms','book','verification','render']
    assert downstream('classification')==['claims','atoms','book','verification','render']
    assert downstream('render')==[]


def test_human_apply_schema_concurrency_history(chapter_book):
    ai,_,ingest=chapter_book;obj=first(chapter_book);service=HumanService(ai)
    old=(ingest.library_path/'knowledge/chapters/ch_0001/claims.jsonl').read_bytes()
    with pytest.raises(ValidationError):service.apply(ingest.manifest.book.slug,action(obj,'edit',{'importance':9.5}))
    with pytest.raises(ValidationError):service.apply(ingest.manifest.book.slug,action(obj,'edit',{'claim_id':'bad'}))
    changed=service.apply(ingest.manifest.book.slug,action(obj,'edit',{'statement':'Human correction of the source statement.'}))
    assert changed['human_modified'] and not changed['human_verified']
    assert changed['base']['statement']==obj['value']['statement']
    with pytest.raises(ProtocolError,match='STALE_HUMAN_EDIT'):service.apply(ingest.manifest.book.slug,action(obj,'edit',{'statement':'Outdated edit'}))
    verified=service.apply(ingest.manifest.book.slug,action(changed,'verify'))
    locked=service.apply(ingest.manifest.book.slug,action(verified,'lock'))
    assert locked['locked'] and locked['human_verified']
    unlocked=service.apply(ingest.manifest.book.slug,action(locked,'unlock'))
    assert not unlocked['locked']
    assert len(EffectiveKnowledgeResolver(ingest.library_path).events)==4
    assert (ingest.library_path/'knowledge/chapters/ch_0001/claims.jsonl').read_bytes()==old


def test_run_resume_and_idempotency(chapter_book):
    ai,_,ingest=chapter_book;service=RunService(ai)
    run,tasks=service.create(ingest.manifest.book.slug,'claims','ch_0001','atoms')
    assert tasks
    initial=[str(t.task_id) for t in tasks]
    submit(ai,tasks[0],claim_result(tasks[0]))
    run,pending=service.resume(ingest.manifest.book.slug,str(run.run_id))
    for task in pending:
        submit(ai,task,claim_result(task) if task.context.task_type=='extract_claims' else atom_result(task))
    for _ in range(5):
        run,pending=service.resume(ingest.manifest.book.slug,str(run.run_id))
        if not pending:break
        for task in pending:submit(ai,task,claim_result(task) if task.context.task_type=='extract_claims' else atom_result(task))
    assert run.status.value=='completed'
    assert service.resume(ingest.manifest.book.slug,str(run.run_id))[0].run_id==run.run_id
    with pytest.raises(ProtocolError,match='No resumable run'):service.resume(ingest.manifest.book.slug)
    assert len(list((ingest.library_path/'knowledge/.generations').iterdir()))==1


def test_human_lock_survives_claim_rerun(chapter_book):
    ai,_,ingest=chapter_book;obj=first(chapter_book);human=HumanService(ai);slug=ingest.manifest.book.slug
    obj=human.apply(slug,action(obj,'edit',{'statement':'Prepare and validate the temporary file before replacement.'}))
    obj=human.apply(slug,action(obj,'verify'));obj=human.apply(slug,action(obj,'lock'))
    old=(ingest.library_path/'knowledge/chapters/ch_0001').resolve()
    runs=RunService(ai);run,tasks=runs.create(slug,'claims','ch_0001','atoms')
    for _ in range(8):
        for task in tasks:submit(ai,task,claim_result(task) if task.context.task_type=='extract_claims' else atom_result(task))
        run,tasks=runs.resume(slug,str(run.run_id))
        if not tasks:break
    assert run.status.value=='completed'
    current=(ingest.library_path/'knowledge/chapters/ch_0001').resolve()
    assert current!=old and old.exists()
    values=EffectiveKnowledgeResolver(ingest.library_path).inventory().values()
    found=next(v for v in values if v['target_ref']['object_id']==obj['target_ref']['object_id'])
    assert found['value']['statement']==obj['value']['statement'] and found['locked'] and found['human_verified']
    assert found['rebased_from']


def test_notes_rules_and_rebuild(chapter_book):
    ai,_,ingest=chapter_book;obj=first(chapter_book);human=HumanService(ai);slug=ingest.manifest.book.slug
    human.note(slug,{'target_ref':obj['target_ref'],'text':'Read this again.'})
    for scope in ('book','global','type'):
        human.rule(slug,{'scope':scope,'book_type':'technical' if scope=='type' else None,'applicable_workflows':['build_core_ideas'],'instruction':scope})
    rules=guidance(ai.library.files.root,ingest.library_path,'build_core_ideas',['technical'])
    assert [r['scope'] for r in rules['rules']]==['global','type','book']
    human.apply(slug,action(obj,'verify'))
    (ingest.library_path/'human/state.json').write_text('broken')
    human.rebuild(ingest.library_path)
    assert json.loads((ingest.library_path/'human/state.json').read_text())['event_count']==1
    assert len(EffectiveKnowledgeResolver(ingest.library_path).notes())==1


def test_concurrent_resume_and_dry_run(chapter_book):
    ai,_,ingest=chapter_book;service=RunService(ai);slug=ingest.manifest.book.slug
    before={str(p):p.read_bytes() for p in ingest.library_path.rglob('*') if p.is_file()}
    service.plan(slug,'claims','ch_0001')
    assert before=={str(p):p.read_bytes() for p in ingest.library_path.rglob('*') if p.is_file()}
    run,_=service.create(slug,'claims','ch_0001','atoms')
    with service.lease(ingest.library_path,run.run_id):
        with pytest.raises(ProtocolError,match='RUN_BUSY'):service.resume(slug,str(run.run_id))


def test_atom_only_reuses_claims(chapter_book):
    ai,_,ingest=chapter_book;obj=first(chapter_book)
    human=HumanService(ai);slug=ingest.manifest.book.slug
    changed=human.apply(slug,action(obj,'edit',{'statement':'Prepare a complete temporary file before replacement.'}))
    runs=RunService(ai);run,tasks=runs.create(slug,'atoms','ch_0001','atoms')
    assert len(tasks)==1 and tasks[0].context.task_type=='build_chapter_atoms'
    assert any(c.statement==changed['value']['statement'] for c in tasks[0].context.claims)
    submit(ai,tasks[0],atom_result(tasks[0]))
    run,tasks=runs.resume(slug,str(run.run_id))
    assert run.status.value=='completed'


def test_source_change_invalidates_resume(chapter_book):
    ai,_,ingest=chapter_book;runs=RunService(ai);slug=ingest.manifest.book.slug
    run,tasks=runs.create(slug,'claims','ch_0001','atoms')
    submit(ai,tasks[0],claim_result(tasks[0]))
    from book_distiller.core.parse import ParseService
    ParseService(ai.library.files.root).parse(slug,force=True)
    run,tasks=runs.resume(slug,str(run.run_id))
    assert tasks[0].context.task_type=='classify_book'
    events=(ingest.library_path/f'runtime/runs/{run.run_id}/events.jsonl').read_text()
    assert 'TASK_INVALIDATED' in events


def test_lock_dependency_conflict(chapter_book):
    ai,_,ingest=chapter_book;obj=first(chapter_book);human=HumanService(ai);slug=ingest.manifest.book.slug
    human.apply(slug,action(obj,'lock'))
    # Simulate a lost source block at the protection boundary without publishing it.
    from book_distiller.human.locks import protected
    path=ingest.library_path/'parsed/normalized/blocks.jsonl';original=path.read_text()
    path.write_text('\n'.join(original.splitlines()[1:])+'\n')
    with pytest.raises(ProtocolError,match='LOCK_DEPENDENCY_CONFLICT'):protected(ai,ingest.library_path,'atomic_claim','ch_0001')
    path.write_text(original)


def test_rule_in_context_and_metadata_hash(chapter_book):
    ai,ch,ingest=chapter_book;obj=first(chapter_book);human=HumanService(ai);slug=ingest.manifest.book.slug
    before=EffectiveKnowledgeResolver(ingest.library_path).semantic_hash()
    human.apply(slug,action(obj,'verify'))
    assert EffectiveKnowledgeResolver(ingest.library_path).semantic_hash()==before
    human.rule(slug,{'scope':'book','applicable_workflows':['extract_claims'],'instruction':'Preserve original qualifications.'})
    _,tasks=ch.prepare_claims(slug,'ch_0001',force=True)
    assert tasks[0].context.human_guidance['rules'][0]['instruction']=='Preserve original qualifications.'
    assert 'Human Guidance' in (tasks[0].directory/'context.md').read_text()


def test_migration_retains_phase7_records(chapter_book):
    ai,_,ingest=chapter_book;db=ai.library.database
    with db.transaction() as c:
        count=c.execute('SELECT count(*) FROM tasks').fetchone()[0]
        c.execute('DROP TABLE run_tasks');c.execute('DROP INDEX runs_edition_status')
        c.execute('ALTER TABLE runs DROP COLUMN execution_json');c.execute('UPDATE schema_meta SET version=1')
    db.initialize();db.check()
    with db.connect() as c:
        assert c.execute('SELECT count(*) FROM tasks').fetchone()[0]==count
        assert c.execute('SELECT version FROM schema_meta').fetchone()[0]==2


def test_interrupted_task_and_torn_journal(chapter_book):
    ai,_,ingest=chapter_book;runs=RunService(ai);slug=ingest.manifest.book.slug
    run,tasks=runs.create(slug,'claims','ch_0001','atoms')
    from book_distiller.core.enums import StageStatus
    ai.library.database.update_task_status(tasks[0].task_id,StageStatus.RUNNING)
    _,again=runs.resume(slug,str(run.run_id))
    assert again[0].task_id==tasks[0].task_id
    from book_distiller.storage.journal import append_event,read_events
    path=ingest.library_path/'human/test.jsonl';append_event(path,{'ok':1})
    with path.open('ab') as stream:stream.write(b'{"partial":')
    append_event(path,{'ok':2})
    assert read_events(path)==[{'ok':1},{'ok':2}]


@pytest.mark.parametrize('stage,chapter,count',[('parse',None,11),('classification',None,10),('claims','ch_0002',1),('atoms','ch_0002',1),('book',None,1),('verification',None,1),('render',None,1)])
def test_stage_scopes(stage,chapter,count):
    through=stage if stage not in ('parse','classification') else 'render'
    plan=RerunPlanner().plan(stage,['ch_0001','ch_0002','ch_0003'],chapter,through)
    assert len(plan.steps)==count


def test_thousand_task_metadata_plan():
    chapters=[f'ch_{i:04d}' for i in range(1,501)]
    generations={f'{s}:{ch}':ch for s in ['claims','atoms'] for ch in chapters}
    plan=RerunPlanner().plan('claims',chapters,'ch_0020',generations=generations)
    assert len(plan.reused_generations)==998 and len(plan.steps)==5


def test_unknown_future_schema_unchanged(chapter_book):
    ai,_,_=chapter_book;db=ai.library.database
    with db.transaction() as c:c.execute('UPDATE schema_meta SET version=99')
    from book_distiller.core.errors import StorageError
    with pytest.raises(StorageError):db.initialize()
    with db.connect() as c:assert c.execute('SELECT version FROM schema_meta').fetchone()[0]==99
