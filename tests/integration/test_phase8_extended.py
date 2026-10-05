"""Phase 8 acceptance fixtures. Semantic answers are explicit protocol doubles."""
import json
from pathlib import Path
from uuid import uuid4
import pytest
from test_chapter_tasks import chapter_book, all_claims, submit, atom_result, claim_result, binding
from test_book_tasks import ready, finish, response
from test_verification_tasks import verification_book, complete, result as verification_result
from test_reader_integration import rendered
from test_phase8 import first, action
from book_distiller.human.service import HumanService
from book_distiller.human.resolver import EffectiveKnowledgeResolver
from book_distiller.pipeline.runs import RunService
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.pipeline.canonical import load_canonical
from book_distiller.storage.filesystem import hash_source


def drive(runs, slug, run, tasks, customize=None):
    for _ in range(100):
        for task in tasks:
            kind=task.context.task_type
            value=(claim_result(task) if kind=='extract_claims' else atom_result(task) if kind=='build_chapter_atoms'
                   else verification_result(task) if kind.startswith(('verify_','review_')) else response(task))
            if customize:value=customize(task,value)
            submit(runs.ai,task,value)
        run,tasks=runs.resume(slug,str(run.run_id))
        if not tasks:return run
    raise AssertionError('Run failed to converge')


def test_unlock_rerun_preserves_history_and_rejects_old_generation(chapter_book):
    ai,_,i=chapter_book;human=HumanService(ai);slug=i.manifest.book.slug
    original=first(chapter_book)
    edited=human.apply(slug,action(original,'edit',{'statement':'User corrected statement.'}))
    verified=human.apply(slug,action(edited,'verify'))
    locked=human.apply(slug,action(verified,'lock'))
    unlocked=human.apply(slug,action(locked,'unlock'))
    assert not unlocked['locked']
    history=(i.library_path/'human/overrides.jsonl').read_bytes()
    old_path=(i.library_path/'knowledge/chapters/ch_0001/claims.jsonl').resolve()
    old_hash=hash_source(old_path)[0]
    runs=RunService(ai);run,tasks=runs.create(slug,'claims','ch_0001','atoms')
    drive(runs,slug,run,tasks)
    values=list(EffectiveKnowledgeResolver(i.library_path).inventory().values())
    assert not any(v['locked'] for v in values)
    assert all(v['value'].get('statement')!='User corrected statement.' for v in values)
    assert (i.library_path/'human/overrides.jsonl').read_bytes().startswith(history)
    assert hash_source(old_path)[0]==old_hash
    with pytest.raises(ProtocolError,match='STALE_HUMAN_EDIT'):
        human.apply(slug,action(original,'edit',{'statement':'Outdated write.'}))


def test_event_survives_projection_failure_and_historical_base(chapter_book,monkeypatch):
    ai,_,i=chapter_book;original=first(chapter_book);human=HumanService(ai)
    before=(i.library_path/'knowledge/chapters/ch_0001/claims.jsonl').read_bytes()
    def fail(*args):raise OSError('projection disk failure')
    with monkeypatch.context() as m:
        m.setattr(human,'rebuild',fail)
        with pytest.raises(OSError,match='projection'):
            human.apply(i.manifest.book.slug,action(original,'edit',{'statement':'B: corrected.'}))
    resolver=EffectiveKnowledgeResolver(i.library_path)
    assert resolver.show(original['target_ref'])['value']['statement']=='B: corrected.'
    assert resolver.effective(original['target_ref'],original['base'],historical=True)['value']['statement']==original['value']['statement']
    human.rebuild(i.library_path)
    assert json.loads((i.library_path/'human/state.json').read_text())['event_count']==1
    assert (i.library_path/'knowledge/chapters/ch_0001/claims.jsonl').read_bytes()==before


def test_atom_lock_rebase_and_candidate_dependency_conflict(chapter_book):
    ai,_,i=chapter_book;first(chapter_book);human=HumanService(ai);slug=i.manifest.book.slug
    obj=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']=='knowledge_atom')
    human.apply(slug,action(obj,'lock'))
    runs=RunService(ai);run,tasks=runs.create(slug,'atoms','ch_0001','atoms')
    drive(runs,slug,run,tasks,lambda t,v:v|{'atoms':[]} if t.context.task_type=='build_chapter_atoms' else v)
    current=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']=='knowledge_atom')
    assert current['locked'] and current['rebased_from']==obj['target_ref']
    assert current['value']['summary']==obj['value']['summary']
    # A changed Claim beneath the locked Atom must conflict, even with the same ID.
    claim=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']=='atomic_claim')
    human.apply(slug,action(claim,'edit',{'statement':'Changed required dependency.'}))
    with pytest.raises(ProtocolError,match='LOCK_DEPENDENCY_CONFLICT'):
        runs.create(slug,'atoms','ch_0001','atoms')
    assert runs.load(i.manifest).status.value=='needs_review'


def test_core_idea_lock_injected_before_model_context(ready):
    books,i=ready;finish(books,i);ai=books.ai;slug=i.manifest.book.slug
    obj=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']=='core_idea')
    HumanService(ai).apply(slug,action(obj,'lock'))
    runs=RunService(ai);run,tasks=runs.create(slug,'book',through='book')
    seen=[]
    def custom(t,v):
        if t.context.task_type=='build_core_ideas':v['core_ideas']=[]
        if t.context.task_type=='build_mental_models':
            seen.extend(t.context.payload['core_ideas'])
        return v
    drive(runs,slug,run,tasks,custom)
    assert seen[0]['statement']==obj['value']['statement']
    after=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']=='core_idea')
    assert after['locked'] and after['rebased_from']==obj['target_ref']
    assert after['target_ref']['generation_id']!=obj['target_ref']['generation_id']
    events=[json.loads(line)['event'] for line in (runs.root(i.library_path,run.run_id)/'events.jsonl').read_text().splitlines()]
    assert 'HUMAN_OVERRIDE_REBASED' in events


@pytest.mark.parametrize('kind,patch,expected',[
    ('atomic_claim',{'statement':'Updated Claim.'}, {'atoms:ch_0001','book','verification','render'}),
    ('knowledge_atom',{'summary':'Updated Atom.'},{'book','verification','render'}),
    ('core_idea',{'statement':'Updated Idea.'},{'book','verification','render'}),
    ('concept',{'canonical_name':'Updated Concept'},{'book','verification','render'}),
])
def test_human_edit_scope_and_reader_warning(rendered,kind,patch,expected):
    renderer,i,verify=rendered;renderer.render(i.manifest.book.slug)
    obj=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']==kind)
    HumanService(verify.ai).apply(i.manifest.book.slug,action(obj,'edit',patch))
    from book_distiller.pipeline.staleness import read
    assert set(read(i.library_path))==expected
    assert 'NEEDS RE-VERIFICATION' in (i.library_path/'reader-status.js').read_text()
    with pytest.raises(Exception,match='STALE|stale'):renderer.render(i.manifest.book.slug)


def test_metadata_only_reader_notes_and_quality_independent(rendered):
    renderer,i,verify=rendered;slug=i.manifest.book.slug;renderer.render(slug)
    before=RunService(verify.ai).snapshot(i.library_path)
    quality=(i.library_path/'verification/current/quality_report.json').read_bytes()
    obj=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']=='core_idea')
    human=HumanService(verify.ai);human.apply(slug,action(obj,'verify'));human.apply(slug,action(obj,'lock'))
    human.note(slug,{'target_ref':obj['target_ref'],'text':'这个观点值得回看第3章。'})
    assert before==RunService(verify.ai).snapshot(i.library_path)
    assert renderer.describe(slug,i.library_path)['Reader']=='stale'
    manifest,_=verify.ai._resolve(slug);document=load_canonical(i.library_path,manifest,verify.ai.library.database)
    assert verify.describe(i.library_path,document)['Verification']=='needs_review'  # Fixture duplicates are unchanged.
    renderer.render(slug)
    assert quality==(i.library_path/'verification/current/quality_report.json').read_bytes()
    data=json.loads((i.library_path/'output/data/knowledge.json').read_text())['objects'][obj['target_ref']['object_id']]
    assert data['human']['human_verified'] and data['human']['locked']
    assert data['user_notes'][0]['source_layer']=='user'


def test_classification_history_and_reference_whitelist(chapter_book):
    ai,_,i=chapter_book;slug=i.manifest.book.slug
    original=(i.library_path/'analysis/classification.json').read_bytes();old=json.loads(original)
    task=ai.prepare('classify',slug)
    submit(ai,task,binding(task)|dict(primary_type='technical',secondary_types=[],tags=[],confidence=.9,evidence=[{'block_id':task.context.blocks[0].block_id,'supports':['technical'],'note':'Storage procedure.'}],rationale_summary='Updated classification fixture.'))
    assert (i.library_path/f"analysis/classification-generations/{old['task_id']}.json").read_bytes()==original
    obj=next(iter(EffectiveKnowledgeResolver(i.library_path).inventory().values()))
    from book_distiller.core.errors import ValidationError
    with pytest.raises(ValidationError):HumanService(ai).apply(slug,action(obj,'edit',{'evidence':[{'block_id':'blk_999999'}]}))


@pytest.fixture
def high_book(isolated_storage,tmp_path):
    from book_distiller.core.ingest import IngestService
    from book_distiller.core.parse import ParseService
    from book_distiller.parsers.plaintext import PlainTextAdapter
    from book_distiller.parsers.base import ParserItem
    from book_distiller.core.models.normalized import BlockType
    from book_distiller.pipeline.ai_tasks import AITaskService
    from book_distiller.pipeline.chapter_tasks import ChapterTasks
    from book_distiller.pipeline.book_tasks import BookTasks
    class TwoChapters(PlainTextAdapter):
        def parse(self,source,raw_directory,source_sha256):
            parsed=super().parse(source,raw_directory,source_sha256)
            parsed.items=[item for n in (1,2) for item in (
                ParserItem(BlockType.HEADING,f'Chapter {n}',f'heading/{n}',heading_level=1),
                ParserItem(BlockType.PARAGRAPH,'Prepare and validate atomic writes before publication. Retain prior results on failure.',f'paragraph/{n}'))]
            return parsed
    source=tmp_path/'two-chapters.txt'
    source.write_text('\n\n'.join(f'Chapter {n}\nPrepare and validate atomic writes before publication. Retain prior results on failure.' for n in (1,2)))
    i=IngestService(isolated_storage).ingest(source);slug=i.manifest.book.slug
    ParseService(isolated_storage,adapter_factory=lambda _:TwoChapters()).parse(slug)
    ai=AITaskService(isolated_storage,Path(__file__).parents[2]);t=ai.prepare('classify',slug)
    submit(ai,t,binding(t)|dict(primary_type='technical',secondary_types=[],tags=[],confidence=.9,evidence=[{'block_id':t.context.blocks[0].block_id,'supports':['technical'],'note':'Storage procedure.'}],rationale_summary='Original two-chapter protocol fixture.'))
    ch=ChapterTasks(ai)
    for chapter in ('ch_0001','ch_0002'):
        _,tasks=ch.prepare_claims(slug,chapter)
        for task in tasks:submit(ai,task,claim_result(task))
        task=ch.prepare_atoms(slug,chapter);submit(ai,task,atom_result(task))
    books=BookTasks(ai);task=books.prepare(slug)
    while not isinstance(task,Path):
        submit(ai,task,high_response(task));task=books.prepare(slug)
    return books,i


def high_response(task):
    value=response(task);kind=task.context.task_type
    if kind=='build_core_ideas':
        base=value['core_ideas'][0]
        value['core_ideas']=[base,base|{'core_idea_id':'idea_002','title':'Retain prior state','statement':'Retain the previous publication if preparation fails.'}]
    elif kind=='build_mental_models':
        value['mental_models']=[dict(model_id='model_001',name='Validate then publish',description='A repeatable publication procedure.',
            mechanism='Prepare, validate and then replace; retain prior state on preparation failure.',core_idea_ids=['idea_001','idea_002'],
            atom_ids=[],concept_ids=['concept_atomic_writes'],when_to_use=['Publishing local results.'],limitations=['Separate transactions remain separate.'],
            promotion_reason='Supported by both fixture chapters.',confidence=.9)]
    elif kind=='build_meta_principles':
        value['meta_principles']=[dict(principle_id='principle_001',title='Guard publication boundaries',statement='Publish complete validated work while preserving prior state on preparation failure.',
            core_idea_ids=['idea_001','idea_002'],model_ids=['model_001'],concept_ids=['concept_atomic_writes'],supporting_chapters=['ch_0001','ch_0002'],
            promotion_reason='Two source ideas and chapters support this scoped principle.',confidence=.9)]
    return value


@pytest.mark.parametrize('kind,field', [('mental_model','mental_models'),('meta_principle','meta_principles')])
def test_higher_lock_rebase_and_recursive_conflict(high_book,kind,field):
    books,i=high_book;slug=i.manifest.book.slug;ai=books.ai;human=HumanService(ai)
    obj=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']==kind)
    human.apply(slug,action(obj,'lock'))
    saved=(i.library_path/'knowledge/book').resolve()
    hashes={p.name:hash_source(p)[0] for p in saved.iterdir()}
    runs=RunService(ai);run,tasks=runs.create(slug,'book',through='book')
    seen=[]
    def custom(t,_):
        v=high_response(t)
        if field in v:v[field]=[]
        if kind=='mental_model' and t.context.task_type=='build_meta_principles':seen.extend(t.context.payload['mental_models'])
        return v
    drive(runs,slug,run,tasks,custom)
    current=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']==kind)
    assert current['locked'] and current['rebased_from']==obj['target_ref']
    assert current['value']==obj['value']
    if kind=='mental_model':assert seen[0]['model_id']==obj['value']['model_id']
    assert hashes=={p.name:hash_source(p)[0] for p in saved.iterdir()}
    # The recursive closure reaches Claims through Ideas/Atoms, not just direct refs.
    claim=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']=='atomic_claim')
    human.apply(slug,action(claim,'edit',{'statement':'Changed transitive evidence premise.'}))
    with pytest.raises(ProtocolError,match='LOCK_DEPENDENCY_CONFLICT'):runs.create(slug,'book',through='book')
    assert runs.load(i.manifest).status.value=='needs_review'


@pytest.mark.parametrize('kind,patch,expected',[
    ('mental_model',{'description':'Updated model description.'},{'book','verification','render'}),
    ('meta_principle',{'statement':'Updated principle statement.'},{'verification','render'})])
def test_higher_semantic_invalidation(high_book,kind,patch,expected):
    books,i=high_book;obj=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']==kind)
    HumanService(books.ai).apply(i.manifest.book.slug,action(obj,'edit',patch))
    from book_distiller.pipeline.staleness import read
    assert set(read(i.library_path))==expected


def test_chapter_sigkill_after_pointer_before_sqlite_commit(chapter_book):
    import subprocess,sys
    ai,_,i=chapter_book;runs=RunService(ai);slug=i.manifest.book.slug
    run,tasks=runs.create(slug,'claims','ch_0001','atoms')
    for t in tasks:submit(ai,t,claim_result(t))
    run,tasks=runs.resume(slug,str(run.run_id));task=tasks[0]
    assert task.context.task_type=='build_chapter_atoms'
    result_path=task.directory/'result.json';result_path.write_text(json.dumps(atom_result(task)))
    script='''import sys,os,signal
from pathlib import Path
from uuid import UUID
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.storage import knowledge
original=knowledge.publish
def killed(*args,**kwargs):
    original(*args,**kwargs)
    os.kill(os.getpid(),signal.SIGKILL)
knowledge.publish=killed
AITaskService(Path(sys.argv[1]),Path(sys.argv[2])).submit(UUID(sys.argv[3]),Path(sys.argv[4]))
'''
    proc=subprocess.run([sys.executable,'-c',script,str(ai.library.files.root),str(ai.project),str(task.task_id),str(result_path)])
    assert proc.returncode==-9
    published=(i.library_path/'knowledge/chapters/ch_0001').resolve()
    run,pending=runs.resume(slug,str(run.run_id))
    assert run.status.value=='completed' and not pending
    assert (i.library_path/'knowledge/chapters/ch_0001').resolve()==published
    assert len(list((i.library_path/'knowledge/.generations').iterdir()))==1
    assert ai.library.database.task_status(task.task_id)=='completed'


@pytest.mark.parametrize('stage', ['book','verification'])
def test_post_publish_sigkill_recovery(verification_book,stage):
    import subprocess,sys
    verify,i=verification_book;ai=verify.ai;runs=RunService(ai);slug=i.manifest.book.slug
    run,tasks=runs.create(slug,stage,through=stage)
    while True:
        task=tasks[0]
        final=(stage=='book' and task.context.task_type=='build_meta_principles')
        if stage=='verification':
            state=json.loads((i.library_path/'runtime/verification-generations'/task.context.scope['generation_id']/'state.json').read_text())
            final=state['cursor']==len(state['plan'])-1
        submit(ai,task,response(task) if stage=='book' else verification_result(task))
        if final:break
        run,tasks=runs.resume(slug,str(run.run_id))
    script='''import sys,os,signal
from pathlib import Path
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.pipeline.runs import RunService
from book_distiller.pipeline.book_tasks import BookTasks
from book_distiller.pipeline.verification_tasks import VerificationTasks
cls=BookTasks if sys.argv[5]=='book' else VerificationTasks
original=cls.save
def killed(self,directory,state):
    if state['completed']:os.kill(os.getpid(),signal.SIGKILL)
    return original(self,directory,state)
cls.save=killed
RunService(AITaskService(Path(sys.argv[1]),Path(sys.argv[2]))).resume(sys.argv[3],sys.argv[4])
'''
    proc=subprocess.run([sys.executable,'-c',script,str(ai.library.files.root),str(ai.project),slug,str(run.run_id),stage])
    assert proc.returncode==-9
    pointer=i.library_path/('knowledge/book' if stage=='book' else 'verification/current')
    published=pointer.resolve();siblings=list(published.parent.iterdir())
    run,pending=runs.resume(slug,str(run.run_id))
    assert run.status.value=='completed' and not pending
    assert pointer.resolve()==published and list(published.parent.iterdir())==siblings


def test_parse_recovery_retains_abandoned_and_reuses_published(chapter_book):
    from book_distiller.core.parse import ParseService
    from book_distiller.core.models import TaskRecord
    from book_distiller.core.enums import StageStatus
    ai,_,i=chapter_book;service=ParseService(ai.library.files.root);db=ai.library.database
    old=(i.library_path/'parsed').resolve()
    task=TaskRecord(edition_id=i.manifest.edition.edition_id,task_type='parse')
    with db.transaction() as c:db.insert_task(c,task)
    db.update_task_status(task.task_id,StageStatus.RUNNING)
    staging=i.library_path/f'.parse-staging-{task.task_id}';staging.mkdir();(staging/'partial').write_text('preserve')
    service.recover(i.manifest.book.slug)
    assert (i.library_path/f'parse_failures/{task.task_id}/{staging.name}/partial').read_text()=='preserve'
    assert (i.library_path/'parsed').resolve()==old
    service.parse(i.manifest.book.slug,force=True)
    latest=db.latest_parse_task(i.manifest.edition.edition_id);published=(i.library_path/'parsed').resolve()
    from uuid import UUID
    db.update_task_status(UUID(latest['task_id']),StageStatus.RUNNING)
    service.recover(i.manifest.book.slug)
    assert service.parse(i.manifest.book.slug).already_parsed
    assert (i.library_path/'parsed').resolve()==published


def test_rule_scope_and_new_context_only(chapter_book):
    ai,ch,i=chapter_book;human=HumanService(ai);slug=i.manifest.book.slug
    run,tasks=RunService(ai).create(slug,'claims','ch_0001','claims');old=tasks[0]
    old_context=(old.directory/'context.json').read_bytes()
    for scope,instruction,kind in [('global','Prefer concise claims.',None),('type','Preserve technical terminology.','technical'),('book','Do not promote examples to Core Ideas.',None)]:
        human.rule(slug,dict(scope=scope,book_type=kind,applicable_workflows=['extract_claims'],instruction=instruction))
    assert (old.directory/'context.json').read_bytes()==old_context
    ai._validate_package(ai.library.database.lookup_task(old.task_id),old.directory,i.library_path)
    _,tasks=ch.prepare_claims(slug,'ch_0001',force=True)
    rules=tasks[0].context.human_guidance['rules']
    assert [r['scope'] for r in rules]==['global','type','book']
    assert rules[-1]['instruction']=='Do not promote examples to Core Ideas.'
    assert 'System invariants > Book > Type > Global' in tasks[0].context.human_guidance['precedence']


def test_lock_missing_block_in_new_normalized_generation(chapter_book):
    from book_distiller.parsers.plaintext import PlainTextAdapter
    from book_distiller.core.parse import ParseService
    ai,_,i=chapter_book;obj=first(chapter_book);slug=i.manifest.book.slug
    # Bind to the last block so a structurally smaller reparse removes that ID.
    obj=next(v for v in reversed(list(EffectiveKnowledgeResolver(i.library_path).inventory().values()))
             if v['target_ref']['object_type']=='atomic_claim')
    human=HumanService(ai);human.apply(slug,action(obj,'lock'))
    history=(i.library_path/'human/overrides.jsonl').read_bytes();old=(i.library_path/'parsed').resolve()
    class Shorter(PlainTextAdapter):
        def parse(self,*args):
            result=super().parse(*args);result.items=list(result.items)[:1];return result
    ParseService(ai.library.files.root,adapter_factory=lambda _:Shorter()).parse(slug,force=True)
    assert (i.library_path/'parsed').resolve()!=old and old.exists()
    runs=RunService(ai)
    with pytest.raises(ProtocolError,match='LOCK_DEPENDENCY_CONFLICT'):runs.create(slug,'classification',through='atoms')
    assert runs.load(i.manifest).status.value=='needs_review'
    assert (i.library_path/'human/overrides.jsonl').read_bytes()==history
    assert EffectiveKnowledgeResolver(i.library_path).show(obj['target_ref'])['locked']


def test_resume_and_rerun_thousand_metadata_performance():
    import time,tracemalloc
    from book_distiller.pipeline.dependencies import ResumePlanner,RerunPlanner
    tasks=[{'task_id':f'task-{i:04d}','status':'completed' if i<800 else 'pending','stale':i<100} for i in range(1000)]
    chapters=[f'ch_{i:04d}' for i in range(1,501)]
    generations={f'{s}:{ch}':ch for s in ('claims','atoms') for ch in chapters}
    tracemalloc.start();start=time.perf_counter()
    resume=ResumePlanner().plan(tasks)
    rerun=RerunPlanner().plan('claims',chapters,'ch_0001',generations=generations)
    elapsed=time.perf_counter()-start;_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    report={'tasks':1000,'plan_ms':round(elapsed*1000,3),'peak_bytes':peak,
        'resume_reuse_candidates':len(resume['reuse_candidates']),'resume_invalidated':len(resume['invalidated']),
        'resume_pending':len(resume['pending']),'rerun_reused_generations':len(rerun.reused_generations),
        'rerun_invalidated_generations':len(rerun.invalidated_generations),'rerun_steps':len(rerun.steps)}
    print('\nPHASE8_PLANNER',json.dumps(report))
    assert report['resume_reuse_candidates']==700 and report['resume_invalidated']==100 and report['resume_pending']==200
    assert report['rerun_reused_generations']==998 and report['rerun_invalidated_generations']==2
    assert elapsed<5 and peak<16*1024*1024


def test_reader_sigkill_during_emit_preserves_old(rendered):
    import subprocess,sys
    renderer,i,_=rendered;slug=i.manifest.book.slug
    path,_,_=renderer.render(slug);old=path.resolve();before=hash_source(old)[0]
    script='''import sys,os,signal
from pathlib import Path
import book_distiller.renderers.service as module
def killed(view,destination,*args):
    destination.mkdir(parents=True)
    (destination/'partial.txt').write_text('interrupted candidate')
    os.kill(os.getpid(),signal.SIGKILL)
module.emit=killed
module.RenderService(Path(sys.argv[1]),Path(sys.argv[2])).render(sys.argv[3],True)
'''
    proc=subprocess.run([sys.executable,'-c',script,str(renderer.ai.library.files.root),str(renderer.project),slug])
    assert proc.returncode==-9 and path.resolve()==old and hash_source(old)[0]==before
    renderer.render(slug,True)
    assert path.resolve()!=old and old.exists()


def test_run_invalidates_tampered_checkpoint_without_overwriting(chapter_book):
    ai,_,i=chapter_book;runs=RunService(ai);slug=i.manifest.book.slug
    run,tasks=runs.create(slug,'claims','ch_0001','claims')
    old=tasks[0];submit(ai,old,claim_result(old))
    schema=old.directory/'output.schema.json';schema.write_text('{}')
    run,new=runs.resume(slug,str(run.run_id))
    assert new and all(t.task_id!=old.task_id for t in new)
    assert schema.read_text()=='{}'  # preserve the failed checkpoint for diagnosis
    events=[json.loads(line) for line in (runs.root(i.library_path,run.run_id)/'events.jsonl').read_text().splitlines()]
    assert any(e['event']=='TASK_INVALIDATED' and e['details']['task_id']==str(old.task_id) for e in events)


def test_schema1_migration_preserves_business_rows(chapter_book):
    from book_distiller.core.models import RunMetadata
    ai,_,i=chapter_book;db=ai.library.database
    with db.transaction() as c:
        run=RunMetadata(book_id=i.manifest.book.book_id,edition_id=i.manifest.edition.edition_id)
        db.insert_run(c,run)
        c.execute('DROP TABLE run_tasks');c.execute('DROP INDEX runs_edition_status')
        c.execute('ALTER TABLE runs DROP COLUMN execution_json');c.execute('UPDATE schema_meta SET version=1')
        snapshots={table:[tuple(row) for row in c.execute('SELECT * FROM '+table)] for table in ('books','editions','tasks','runs')}
    db.initialize()
    with db.connect() as c:
        for table,rows in snapshots.items():
            actual=[tuple(row) for row in c.execute('SELECT * FROM '+table)]
            if table=='runs':actual=[row[:-1] for row in actual]
            assert actual==rows


def test_legacy_context_without_human_guidance_is_valid(chapter_book):
    from book_distiller.pipeline.context_package import finalize_budget,render_context
    ai,_,i=chapter_book;t=ai.prepare('classify',i.manifest.book.slug)
    c=t.context;c.human_guidance={};finalize_budget(c)
    payload=c.model_dump(mode='json');payload.pop('human_guidance')
    (t.directory/'context.json').write_text(json.dumps(payload))
    (t.directory/'context.md').write_text(render_context(c))
    request=json.loads((t.directory/'request.json').read_text());request['context_hash']=c.context_hash
    (t.directory/'request.json').write_text(json.dumps(request))
    ai._validate_package(ai.library.database.lookup_task(t.task_id),t.directory,i.library_path)


def test_classification_lock_retains_evidence_and_lineage(chapter_book):
    ai,_,i=chapter_book;slug=i.manifest.book.slug;resolver=EffectiveKnowledgeResolver(i.library_path)
    obj=next(v for v in resolver.inventory().values() if v['target_ref']['object_type']=='classification')
    HumanService(ai).apply(slug,action(obj,'lock'))
    t=ai.prepare('classify',slug)
    value=binding(t)|{key:obj['value'][key] for key in ('primary_type','secondary_types','tags','confidence','evidence','rationale_summary')}
    value['rationale_summary']='New AI rationale should not replace the locked classification.'
    submit(ai,t,value)
    after=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']=='classification')
    assert after['value']['rationale_summary']==obj['value']['rationale_summary']
    assert after['value']['evidence']==obj['value']['evidence']
    assert after['locked'] and after['rebased_from']==obj['target_ref']


@pytest.mark.parametrize('stage', ['parse','classification','verification'])
def test_cascade_ledger_at_requested_stop(rendered,stage):
    from book_distiller.pipeline.staleness import read
    renderer,i,verify=rendered;slug=i.manifest.book.slug;renderer.render(slug)
    runs=RunService(verify.ai);before=runs.snapshot(i.library_path)
    run,tasks=runs.create(slug,stage,through=stage)
    stale=set(read(i.library_path))
    if stage=='parse':
        assert run.status.value=='completed'
        assert stale=={'classification','claims:ch_0001','atoms:ch_0001','book','verification','render'}
        assert runs.snapshot(i.library_path)['parse']!=before['parse']
    elif stage=='classification':
        assert tasks and stale=={'classification','claims:ch_0001','atoms:ch_0001','book','verification','render'}
    else:
        assert tasks and stale=={'verification','render'}
    assert runs.snapshot(i.library_path)['book']==before['book']
    assert runs.snapshot(i.library_path)['render']==before['render']


def test_render_only_run_preserves_knowledge_verification_hashes(rendered):
    renderer,i,verify=rendered;slug=i.manifest.book.slug;renderer.render(slug)
    before={str(p):hash_source(p)[0] for root in ('knowledge','verification') for p in (i.library_path/root).rglob('*') if p.is_file()}
    old=(i.library_path/'output').resolve();runs=RunService(verify.ai)
    run,tasks=runs.create(slug,'render')
    assert run.status.value=='completed' and not tasks and (i.library_path/'output').resolve()!=old
    assert before=={path:hash_source(Path(path))[0] for path in before}


def test_human_atom_cannot_change_references_or_identity(chapter_book):
    from book_distiller.core.errors import ValidationError
    ai,_,i=chapter_book;first(chapter_book);human=HumanService(ai)
    obj=next(v for v in EffectiveKnowledgeResolver(i.library_path).inventory().values() if v['target_ref']['object_type']=='knowledge_atom')
    for patch in ({'claim_ids':['missing']},{'chapter_id':'ch_9999'},{'generation_id':str(uuid4())},{'source_sha256':'0'*64}):
        with pytest.raises(ValidationError):human.apply(i.manifest.book.slug,action(obj,'edit',patch))


def test_locked_dry_run_does_not_read_source_body(chapter_book,monkeypatch):
    ai,_,i=chapter_book;obj=first(chapter_book)
    HumanService(ai).apply(i.manifest.book.slug,action(obj,'lock'))
    def forbidden(*args,**kwargs):raise AssertionError('Planner read source body')
    monkeypatch.setattr(HumanService,'anchors',forbidden)
    plan=RunService(ai).plan(i.manifest.book.slug,'claims','ch_0001')
    assert plan.protected_objects==[obj['target_ref']]
