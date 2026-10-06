"""Recovery acceptance: deterministic protocol fixtures, no model calls."""
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import zipfile
import pytest
from test_chapter_tasks import chapter_book, claim_result, atom_result, submit
from test_book_tasks import ready
from test_verification_tasks import verification_book, complete
from test_reader_integration import rendered
from test_ask_protocol import answer
from test_phase8 import action, first
from book_distiller.backup.service import BackupService
from book_distiller.backup.database import export_rows
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.pipeline.runs import RunService
from book_distiller.core.errors import BookDistillerError
from book_distiller.core.models.knowledge import ChunkBudget
from book_distiller.pipeline.chapter_tasks import ChapterTasks
from book_distiller.human.service import HumanService
from book_distiller.human.resolver import EffectiveKnowledgeResolver
from book_distiller.renderers.service import RenderService
from book_distiller.ask.service import AskService
from book_distiller.storage.filesystem import hash_source

PROJECT=Path(__file__).parents[2]

@pytest.fixture
def partial(chapter_book,monkeypatch):
    ai,ch,ingest=chapter_book
    original=ChapterTasks.prepare_claims
    def small(self,*args,**kwargs):
        kwargs.setdefault('budget',ChunkBudget(max_chars=2000,estimated_max_tokens=700,max_blocks=4))
        return original(self,*args,**kwargs)
    monkeypatch.setattr(ChapterTasks,'prepare_claims',small)
    runs=RunService(ai);run,tasks=runs.create(ingest.manifest.book.slug,'claims','ch_0001','claims')
    assert len(tasks)==4
    for task in tasks[:2]:submit(ai,task,claim_result(task))
    run,pending=runs.resume(ingest.manifest.book.slug,str(run.run_id))
    assert len(pending)==2
    return ai,ingest,run,tasks

@pytest.fixture
def partial_backup(partial,tmp_path):
    ai,i,run,tasks=partial
    service=BackupService(ai.library.files.root,PROJECT);archive=tmp_path/'partial.bookbackup.zip'
    value=service.export(i.manifest.book.slug,archive)
    return service,value,archive,partial

@pytest.fixture
def full_backup(rendered,tmp_path,isolated_storage):
    renderer,i,_=rendered;renderer.render(i.manifest.book.slug)
    service=BackupService(isolated_storage,PROJECT);archive=tmp_path/'full.bookbackup.zip'
    value=service.export(i.manifest.book.slug,archive)
    return service,value,archive,i


def test_partial_restore_resume_reuses_completed(partial_backup,tmp_path):
    service,value,archive,(old,i,run,tasks)=partial_backup
    before={str(t.task_id):hash_source(t.directory/'accepted.json')[0] for t in tasks[:2]}
    target=BackupService(tmp_path/'restored',PROJECT);_,root,noop=target.restore(archive)
    assert not noop and target.restore(archive)[2]
    ai=AITaskService(target.home,PROJECT);runs=RunService(ai)
    recovered,pending=runs.resume(str(value.book_id),str(run.run_id))
    assert {t.task_id for t in pending}=={t.task_id for t in tasks[2:]}
    assert all(ai.library.database.lookup_task(t.task_id)['status']=='completed' for t in tasks[:2])
    for task in pending:submit(ai,task,claim_result(task))
    finished,again=runs.resume(str(value.book_id),str(run.run_id))
    assert finished.status.value=='completed' and not again
    assert runs.resume(str(value.book_id),str(run.run_id))[1]==[]
    assert before=={str(t.task_id):hash_source(root/'runtime/tasks'/str(t.task_id)/'accepted.json')[0] for t in tasks[:2]}
    events=(root/f'runtime/runs/{run.run_id}/events.jsonl').read_text()
    assert 'TASK_REUSED' in events
    with ai.library.database.connect() as c:
        assert c.execute("select count(*) from tasks where task_type='extract_claims'").fetchone()[0]==4


def test_processing_becomes_recoverable(partial,tmp_path):
    ai,i,run,tasks=partial
    from book_distiller.core.enums import StageStatus,BookStatus
    run.status=BookStatus.PROCESSING;RunService(ai).save(i.library_path,run)
    ai.library.database.update_task_status(tasks[2].task_id,StageStatus.RUNNING)
    archive=tmp_path/'active.bookbackup.zip';BackupService(ai.library.files.root,PROJECT).export(i.manifest.book.slug,archive)
    target=BackupService(tmp_path/'target',PROJECT);v,root,_=target.restore(archive)
    restored=AITaskService(target.home,PROJECT);runs=RunService(restored)
    assert runs.load(i.manifest,str(run.run_id)).status.value=='paused'
    assert restored.library.database.lookup_task(tasks[2].task_id)['status']=='pending'
    assert not list(root.glob('runtime/runs/*/execution.lock'))
    with runs.lease(root,run.run_id):
        with pytest.raises(BookDistillerError,match='RUN_BUSY'):runs.resume(str(v.book_id),str(run.run_id))
    assert len(runs.resume(str(v.book_id),str(run.run_id))[1])==2


def test_stale_checkpoint_not_reused(partial_backup,tmp_path):
    _,v,archive,(old,i,run,tasks)=partial_backup
    target=BackupService(tmp_path/'target',PROJECT);_,root,_=target.restore(archive)
    (root/'runtime/tasks'/str(tasks[0].task_id)/'context.md').write_text('changed context')
    ai=AITaskService(target.home,PROJECT)
    _,pending=RunService(ai).resume(str(v.book_id),str(run.run_id))
    assert not {t.task_id for t in pending}&{t.task_id for t in tasks}
    assert 'TASK_INVALIDATED' in (root/f'runtime/runs/{run.run_id}/events.jsonl').read_text()


def test_full_restore_reader_ask_history(full_backup,tmp_path):
    service,v,archive,i=full_backup
    target=BackupService(tmp_path/'target',PROJECT);_,root,_=target.restore(archive)
    assert RenderService(target.home,PROJECT).describe(str(v.book_id),root)['Reader']=='completed'
    ai=AITaskService(target.home,PROJECT);ask=AskService(ai)
    assert ask.describe(str(v.book_id))['Ask'].startswith('ready')
    task=ask.prepare(str(v.book_id),'Explain atomic writes');submit(ai,task,answer(task))
    assert (root/'ask/answers'/f'{task.task_id}.json').is_file()
    assert '<!doctype html>' in (root/'output/index.html').read_text()
    second=tmp_path/'ask.bookbackup.zip';after=target.export(str(v.book_id),second)
    assert any(f.relative_path==f'ask/answers/{task.task_id}.json' for f in after.files)


def test_determinism_inspect_read_only(partial_backup,tmp_path):
    service,v,archive,(_,i,*_)=partial_backup
    before=hash_source(service.library.database.path)[0]
    assert service.inspect(archive)==v
    assert hash_source(service.library.database.path)[0]==before
    other=service.export(i.manifest.book.slug,tmp_path/'again.bookbackup.zip')
    assert other.backup_content_hash==v.backup_content_hash and other.files==v.files
    with zipfile.ZipFile(archive) as z:
        assert not any('execution.lock' in n or n.endswith('.sqlite3') for n in z.namelist())


def test_human_history_and_rules(chapter_book,tmp_path):
    ai,_,i=chapter_book;obj=first(chapter_book);human=HumanService(ai);slug=i.manifest.book.slug
    obj=human.apply(slug,action(obj,'edit',{'statement':'Preserve the complete previous file.'}))
    for kind in ('verify','lock','unlock','lock'):obj=human.apply(slug,action(obj,kind))
    human.note(slug,{'target_ref':obj['target_ref'],'text':'Review this evidence.'})
    for scope in ('global','type','book'):
        human.rule(slug,{'scope':scope,'book_type':'technical' if scope=='type' else None,'instruction':scope,'applicable_workflows':['extract_claims']})
    service=BackupService(ai.library.files.root,PROJECT);archive=tmp_path/'human.bookbackup.zip';v=service.export(slug,archive)
    target=BackupService(tmp_path/'target',PROJECT);_,root,_=target.restore(archive)
    assert len(v.external_rule_dependencies)==2
    for name in ('overrides.jsonl','notes.jsonl','rules.jsonl','state.json'):assert (root/'human'/name).read_bytes()==(i.library_path/'human'/name).read_bytes()
    resolved=EffectiveKnowledgeResolver(root)
    assert resolved.display_hash()==v.human_state_hash and any(x['locked'] for x in resolved.inventory().values())
    HumanService(AITaskService(target.home,PROJECT)).rebuild(root)
    assert EffectiveKnowledgeResolver(root).display_hash()==v.human_state_hash


@pytest.mark.parametrize('change',['human','run','new_task','pointer'])
def test_conflict_rejected(partial_backup,tmp_path,change):
    _,v,archive,(_,i,run,tasks)=partial_backup
    target=BackupService(tmp_path/'target',PROJECT);_,root,_=target.restore(archive)
    if change=='human':
        (root/'human').mkdir(exist_ok=True);(root/'human/notes.jsonl').write_text('{}\n')
    elif change=='run':
        from book_distiller.core.enums import BookStatus
        run.status=BookStatus.FAILED;RunService(AITaskService(target.home,PROJECT)).save(root,run)
    elif change=='new_task':
        from book_distiller.core.models import TaskRecord
        with target.library.database.transaction() as c:target.library.database.insert_task(c,TaskRecord(edition_id=v.edition_id,task_type='parse'))
    else:
        (root/'parsed').unlink()
    with pytest.raises(BookDistillerError,match='COLLISION'):target.restore(archive)


@pytest.mark.parametrize('failure',['stage','copy','publish','db','post'])
def test_restore_rollback(partial_backup,tmp_path,monkeypatch,failure):
    _,v,archive,_=partial_backup;target=BackupService(tmp_path/'target',PROJECT)
    def fail(*a,**kw):raise OSError('injected')
    if failure=='stage':monkeypatch.setattr(target.library.files,'stage',fail)
    elif failure=='copy':
        import shutil
        original_copy=shutil.copyfile
        def copy_fail(source,dest,*a,**kw):
            if Path(dest).is_relative_to(target.home/'library'):fail()
            return original_copy(source,dest,*a,**kw)
        monkeypatch.setattr(shutil,'copyfile',copy_fail)
    elif failure=='post':monkeypatch.setattr(target,'post_validate',fail)
    elif failure=='publish':
        original=target.library.files.publish
        def published(*a,**kw):original(*a,**kw);fail()
        monkeypatch.setattr(target.library.files,'publish',published)
    else:
        from contextlib import contextmanager
        original=target.library.database.transaction
        @contextmanager
        def db_fail():
            with original() as connection:
                yield connection
                fail()
        monkeypatch.setattr(target.library.database,'transaction',db_fail)
    with pytest.raises(OSError,match='injected'):target.restore(archive)
    assert not list(target.library.files.library.iterdir())
    with target.library.database.connect() as c:
        for table in ('books','editions','runs','tasks','run_tasks'):assert c.execute('select count(*) from '+table).fetchone()[0]==0


@pytest.mark.parametrize('failure',['write','validate','publish'])
def test_backup_atomic(partial_backup,tmp_path,monkeypatch,failure):
    service,v,_,(_,i,*_)=partial_backup;destination=tmp_path/'failed.bookbackup.zip'
    def fail(*a,**kw):raise OSError('injected')
    if failure=='write':monkeypatch.setattr(zipfile.ZipFile,'write',fail)
    elif failure=='validate':monkeypatch.setattr(service,'inspect',fail)
    else:monkeypatch.setattr(os,'link',fail)
    with pytest.raises(OSError,match='injected'):service.export(i.manifest.book.slug,destination)
    assert not destination.exists() and not list(tmp_path.glob('.failed*'))


def test_sigkill_published_orphan_recovery(partial_backup,tmp_path):
    _,v,archive,(_,i,*_)=partial_backup;home=tmp_path/'killed'
    code='''import os,signal,sys
from pathlib import Path
from book_distiller.backup.service import BackupService
s=BackupService(sys.argv[1],Path.cwd())
original=s.library.files.publish
def kill(*a,**kw):
 result=original(*a,**kw)
 os.kill(os.getpid(),signal.SIGKILL)
s.library.files.publish=kill
s.restore(sys.argv[2])
'''
    result=subprocess.run([sys.executable,'-c',code,str(home),str(archive)],cwd=PROJECT,capture_output=True)
    assert result.returncode==-signal.SIGKILL,result.stderr.decode()
    target=BackupService(home,PROJECT)
    assert (home/'library'/i.manifest.book.slug).exists()
    with target.library.database.connect() as c:assert c.execute('select count(*) from books').fetchone()[0]==0
    assert not target.restore(archive)[2]
    assert target.restore(archive)[2]
    assert len(list((home/'data/restore-recovery').iterdir()))==1


def test_unknown_orphan_preserved(partial_backup,tmp_path):
    _,v,archive,(_,i,*_)=partial_backup;target=BackupService(tmp_path/'target',PROJECT)
    root=target.home/'library'/i.manifest.book.slug;root.mkdir(parents=True);(root/'private.txt').write_text('keep')
    with pytest.raises(BookDistillerError,match='ORPHAN_UNOWNED'):target.restore(archive)
    assert (root/'private.txt').read_text()=='keep'

@pytest.mark.parametrize('included',[False,True])
def test_external_source_policy(full_backup,tmp_path,included):
    service,v,_,i=full_backup;root=i.library_path
    source=tmp_path/'external.txt';source.write_bytes((root/i.manifest.source.stored_path).read_bytes())
    manifest=i.manifest.model_copy(deep=True);manifest.source.copy_mode='reference';manifest.source.original_path=source;manifest.source.stored_path=str(source)
    (root/'manifest.json').write_text(manifest.model_dump_json(indent=2))
    with service.library.database.transaction() as c:
        c.execute("UPDATE editions SET copy_mode='reference',source_original_path=?,source_stored_path=?",(str(source),str(source)))
    RenderService(service.home,PROJECT).render(manifest.book.slug,force=True)
    archive=tmp_path/f'external-{included}.bookbackup.zip';value=service.export(manifest.book.slug,archive,included)
    assert value.source_mode=='external' and value.source_included==included
    target=BackupService(tmp_path/'external-restore',PROJECT);_,restored,_=target.restore(archive)
    from book_distiller.bundle.receipt import source_display
    m=target.library.status(str(value.book_id))[0]
    assert source_display(restored,m)['available']==included
    assert AskService(AITaskService(target.home,PROJECT)).describe(str(value.book_id))['Ask'].startswith('ready')
    assert RenderService(target.home,PROJECT).describe(str(value.book_id),restored)['Reader']=='completed'
    if not included:
        from book_distiller.core.parse import ParseService
        with pytest.raises(BookDistillerError):ParseService(target.home).parse(str(value.book_id),force=True)


def rewrite(archive,destination,mutate):
    from book_distiller.bundle.archive import content_hash
    import hashlib
    with zipfile.ZipFile(archive) as z:payload={n:z.read(n) for n in z.namelist()}
    value=json.loads(payload.pop('backup_manifest.json'));mutate(value,payload)
    for entry in value['files']:
        data=payload[entry['relative_path']];entry.update(sha256=hashlib.sha256(data).hexdigest(),size=len(data))
    value['backup_content_hash']=content_hash(value,'backup_content_hash')
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('backup_manifest.json',json.dumps(value))
        for n,data in payload.items():z.writestr(n,data)
    return destination


@pytest.mark.parametrize('bad',['db_schema','manifest_schema','cross_book','run_task','checkpoint','runtime_path','resource','result','pointer','undeclared','normalized_identity','extra_source'])
def test_malformed_rejected_before_target_write(partial_backup,tmp_path,bad):
    _,v,archive,(_,i,run,tasks)=partial_backup
    def mutate(m,p):
        db=json.loads(p['metadata/db-export.json'])
        if bad=='db_schema':db['sqlite_schema_version']=3
        elif bad=='manifest_schema':m['backup_version']='2.0'
        elif bad=='cross_book':db['tasks'][0]['edition_id']='00000000-0000-0000-0000-000000000001'
        elif bad=='run_task':db['run_tasks'][0]['task_id']='00000000-0000-0000-0000-000000000001'
        elif bad=='checkpoint':
            execution=json.loads(db['runs'][0]['execution_json']);execution['checkpoints']['0']['tasks'].append('00000000-0000-0000-0000-000000000001');db['runs'][0]['execution_json']=json.dumps(execution)
        elif bad=='runtime_path':
            name=next(n for n in p if n.startswith('runtime/generations/') and n.endswith('generation.json'))
            value=json.loads(p[name]);value['claim_tasks']['bad']='00000000-0000-0000-0000-000000000001';p[name]=json.dumps(value).encode()
        elif bad=='resource':p[f'runtime/tasks/{tasks[0].task_id}/prompt.md']=b'changed'
        elif bad=='result':
            name=f'runtime/tasks/{tasks[0].task_id}/accepted.json';r=json.loads(p[name]);r['context_hash']='0'*64;p[name]=json.dumps(r).encode()
        elif bad=='pointer':m['pointers']['parsed']='../../escape'
        elif bad=='normalized_identity':
            name=m['pointers']['parsed']+'/normalized/book.json';data=json.loads(p[name]);data['book_id']='00000000-0000-0000-0000-000000000001';p[name]=json.dumps(data).encode()
            import hashlib
            receipt_name=m['pointers']['parsed']+'/completion.json';receipt=json.loads(p[receipt_name]);receipt['artifact_hashes']['normalized/book.json']=hashlib.sha256(p[name]).hexdigest();p[receipt_name]=json.dumps(receipt).encode()
        elif bad=='extra_source':
            p['source/original.pdf']=b'extra source';m['files'].append({'relative_path':'source/original.pdf','category':'source','sha256':'0'*64,'size':12})
        else:p['unknown.txt']=b'undeclared'
        p['metadata/db-export.json']=json.dumps(db).encode()
    corrupted=rewrite(archive,tmp_path/'bad.bookbackup.zip',mutate)
    target=BackupService(tmp_path/'untouched',PROJECT)
    with pytest.raises(BookDistillerError):target.restore(corrupted)
    assert not target.home.exists()


def test_active_run_backup_refused(partial,tmp_path):
    ai,i,run,tasks=partial
    with RunService(ai).lease(i.library_path,run.run_id):
        with pytest.raises(BookDistillerError,match='BACKUP_RUN_BUSY'):BackupService(ai.library.files.root,PROJECT).export(i.manifest.book.slug,tmp_path/'busy.bookbackup.zip')


def test_preserves_historical_chapter_generations(chapter_book,tmp_path):
    ai,ch,i=chapter_book;obj=first(chapter_book)
    original=(i.library_path/'knowledge/chapters/ch_0001').resolve()
    hashes={p.name:hash_source(p)[0] for p in original.iterdir()}
    runs=RunService(ai);run,tasks=runs.create(i.manifest.book.slug,'claims','ch_0001','atoms')
    while tasks:
        for task in tasks:submit(ai,task,claim_result(task) if task.context.task_type=='extract_claims' else atom_result(task))
        run,tasks=runs.resume(i.manifest.book.slug,str(run.run_id))
    archive=tmp_path/'history.bookbackup.zip';v=BackupService(ai.library.files.root,PROJECT).export(i.manifest.book.slug,archive)
    target=BackupService(tmp_path/'target',PROJECT);_,root,_=target.restore(archive)
    assert len(v.generation_inventory['chapter'])==2
    restored=root/'knowledge/.generations'/original.name
    assert hashes=={p.name:hash_source(p)[0] for p in restored.iterdir()}
    assert (root/'knowledge/chapters/ch_0001').resolve().name!=original.name


def test_copied_source_reparse(full_backup,tmp_path):
    _,v,archive,i=full_backup
    target=BackupService(tmp_path/'target',PROJECT);_,root,_=target.restore(archive)
    manifest=target.library.status(str(v.book_id))[0]
    assert manifest.source.copy_mode=='copy'
    assert hash_source(root/manifest.source.stored_path)[0]==manifest.source.sha256
    from book_distiller.core.parse import ParseService
    ParseService(target.home).parse(str(v.book_id),force=True)
    assert (root/'parsed/normalized/book.json').exists()


@pytest.mark.parametrize('bad',['traversal','absolute','symlink','special','duplicate','checksum','corruption','limit'])
def test_backup_profile_safezip(partial_backup,tmp_path,bad):
    _,v,archive,_=partial_backup
    destination=tmp_path/'unsafe.bookbackup.zip'
    import stat
    from book_distiller.bundle.archive import BundleLimits
    with zipfile.ZipFile(archive) as z:payload={n:z.read(n) for n in z.namelist()}
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED) as z:
        for name,data in payload.items():
            if bad=='checksum' and name=='manifest.json':data+=b' '
            z.writestr(name,data)
        if bad=='traversal':z.writestr('../escape',b'bad')
        elif bad=='absolute':z.writestr('/tmp/escape',b'bad')
        elif bad in ('symlink','special'):
            info=zipfile.ZipInfo('unsafe');info.create_system=3
            info.external_attr=((stat.S_IFLNK if bad=='symlink' else stat.S_IFIFO)|0o600)<<16
            z.writestr(info,b'/tmp/escape')
        elif bad=='duplicate':
            with pytest.warns(UserWarning):z.writestr('manifest.json',payload['manifest.json'])
    if bad=='corruption':destination.write_bytes(destination.read_bytes()[:100])
    target=BackupService(tmp_path/'target',PROJECT,BundleLimits(max_files=1) if bad=='limit' else BundleLimits())
    with pytest.raises(BookDistillerError):target.restore(destination)
    assert not target.home.exists()


@pytest.mark.parametrize('bad',['book_identity','verification_reference','live_script','live_false_pass','stale_script'])
def test_generation_internal_refs(full_backup,tmp_path,bad):
    _,v,archive,i=full_backup
    def mutate(m,p):
        if bad=='live_script':
            p['reader-status.js']+=b'alert(1);';return
        if bad=='stale_script':
            import hashlib
            output=m['pointers']['output'];p[output+'/index.html']+=b'<script>alert(1)</script>'
            rm=json.loads(p[output+'/render_manifest.json']);rm['artifact_hashes']['index.html']=hashlib.sha256(p[output+'/index.html']).hexdigest();p[output+'/render_manifest.json']=json.dumps(rm).encode()
            p['reader-status.js']=b'window.BOOK_DISTILLER_LIVE_STATUS = {"status": "stale", "reason": "Human semantic edit"};\n'
            p['runtime/stale.json']=b'{"verification":{"reason":"Human semantic edit"}}'
            if not any(f['relative_path']=='runtime/stale.json' for f in m['files']):
                m['files'].append({'relative_path':'runtime/stale.json','sha256':'0'*64,'size':0,'category':'runtime'})
                m['checkpoint_inventory']=sorted([*m['checkpoint_inventory'],'runtime/stale.json'])
            return
        if bad=='live_false_pass':
            p['reader-status.js']=b'window.BOOK_DISTILLER_LIVE_STATUS = {"status": "current", "reason": "Current"};\n'
            p['runtime/stale.json']=b'{"verification":{"reason":"Human semantic edit"}}'
            if not any(f['relative_path']=='runtime/stale.json' for f in m['files']):
                m['files'].append({'relative_path':'runtime/stale.json','sha256':'0'*64,'size':0,'category':'runtime'})
                m['checkpoint_inventory']=sorted([*m['checkpoint_inventory'],'runtime/stale.json'])
            return
        if bad=='book_identity':
            name=m['pointers']['knowledge/book']+'/book_model.json';data=json.loads(p[name]);data['book_id']='00000000-0000-0000-0000-000000000001'
        else:
            name=m['pointers']['verification/current']+'/manifest.json';data=json.loads(p[name]);data['dependencies']['book_path']='knowledge/.book-generations/00000000-0000-0000-0000-000000000001'
        p[name]=json.dumps(data).encode()
    malformed=rewrite(archive,tmp_path/'bad.bookbackup.zip',mutate)
    target=BackupService(tmp_path/'target',PROJECT)
    with pytest.raises(BookDistillerError):target.restore(malformed)
    assert not target.home.exists()


def test_stale_reader_recovery_preserves_history(full_backup,tmp_path):
    service,v,archive,i=full_backup
    root=i.library_path;human=HumanService(AITaskService(service.home,PROJECT))
    obj=next(v for v in EffectiveKnowledgeResolver(root).inventory().values() if v['target_ref']['object_type']=='atomic_claim')
    human.apply(i.manifest.book.slug,action(obj,'edit',{'statement':'Human semantic correction requiring verification.'}))
    before={str(p):hash_source(p)[0] for folder in ('knowledge','verification') for p in (root/folder).rglob('*') if p.is_file()}
    archive=tmp_path/'stale.bookbackup.zip';service.export(i.manifest.book.slug,archive)
    target=BackupService(tmp_path/'target',PROJECT);value,restored,_=target.restore(archive)
    assert RenderService(target.home,PROJECT).describe(str(value.book_id),restored)['Reader']!='completed'
    assert '"status": "stale"' in (restored/'reader-status.js').read_text()
    assert before=={str(p):hash_source(p)[0] for folder in ('knowledge','verification') for p in (root/folder).rglob('*') if p.is_file()}
    assert json.loads((restored/'output/data/book.json').read_text())['source']['uri'].startswith('../source/')
