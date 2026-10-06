"""Portable snapshot integration with deterministic protocol fixtures, not model smoke."""
import json
from pathlib import Path
import pytest
from test_chapter_tasks import chapter_book
from test_book_tasks import ready
from test_verification_tasks import verification_book, complete
from test_reader_integration import rendered
from book_distiller.bundle.service import BundleService
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.ask.service import AskService
from book_distiller.renderers.service import RenderService
from book_distiller.core.ingest import IngestService
from book_distiller.core.errors import BookDistillerError

PROJECT = Path(__file__).parents[2]

@pytest.fixture
def exported(rendered, tmp_path, isolated_storage):
    renderer, ingest, _ = rendered
    renderer.render(ingest.manifest.book.slug)
    service = BundleService(isolated_storage, PROJECT)
    archive = tmp_path/'snapshot.bookbundle.zip'
    bundle = service.export(ingest.manifest.book.slug, archive)
    return service, bundle, archive, ingest


def test_round_trip_zero_tasks_no_source_ask_ready(exported, tmp_path):
    service, bundle, archive, ingest = exported
    assert service.inspect(archive) == bundle
    target = BundleService(tmp_path/'imported', PROJECT)
    result, root, already = target.import_bundle(archive)
    assert not already and result == bundle
    assert target.library.status(str(bundle.book_id))
    with target.library.database.connect() as connection:
        assert connection.execute('select count(*) from books').fetchone()[0] == 1
        for name in ('tasks','runs'):
            assert connection.execute('select count(*) from '+name).fetchone()[0] == 0
    assert not (root/'source').exists()
    assert not (root/'ask').exists()
    assert RenderService(target.home,PROJECT).describe(str(bundle.book_id),root)['Reader']=='completed'
    ask=AskService(AITaskService(target.home,PROJECT))
    assert ask.describe(str(bundle.book_id))['Ask'].startswith('ready')
    task=ask.prepare(str(bundle.book_id),'What does the book say about atomic writes?')
    assert task.context.task_type=='ask_book'
    assert target.import_bundle(archive)[2]


def test_export_determinism(exported,tmp_path):
    service,bundle,archive,ingest=exported
    other=service.export(ingest.manifest.book.slug,tmp_path/'second.bookbundle.zip')
    assert other.bundle_content_hash == bundle.bundle_content_hash
    assert other.files == bundle.files


def test_source_included_round_trip(rendered,tmp_path,isolated_storage):
    renderer,ingest,_=rendered
    renderer.render(ingest.manifest.book.slug)
    archive=tmp_path/'source.bookbundle.zip'
    bundle=BundleService(isolated_storage,PROJECT).export(ingest.manifest.book.slug,archive,True)
    target=BundleService(tmp_path/'target',PROJECT)
    _,root,_=target.import_bundle(archive)
    assert (root/ingest.manifest.source.stored_path).is_file()
    source=json.loads((root/'output/data/book.json').read_text())['source']
    assert source['available'] and source['uri'].startswith('../source/')


def rewrite_archive(archive, destination, change):
    import zipfile
    from book_distiller.bundle.archive import content_hash
    import hashlib
    with zipfile.ZipFile(archive) as z:
        payload={n:z.read(n) for n in z.namelist()}
    manifest=json.loads(payload.pop('bundle_manifest.json'))
    change(manifest,payload)
    for entry in manifest['files']:
        if entry['relative_path'] in payload:
            data=payload[entry['relative_path']]
            entry.update(sha256=hashlib.sha256(data).hexdigest(),size=len(data))
    manifest['bundle_content_hash']=content_hash(manifest)
    with zipfile.ZipFile(destination,'w',compression=zipfile.ZIP_DEFLATED) as z:
        z.writestr('bundle_manifest.json',json.dumps(manifest))
        for name,data in payload.items():z.writestr(name,data)
    return destination


@pytest.mark.parametrize('kind',['pointer','undeclared-runtime','unknown-version','citation','human-field','classification-identity','generation','source-missing'])
def test_semantic_rejection_before_target_mutation(exported,tmp_path,kind):
    service,bundle,archive,ingest=exported
    def change(m,p):
        if kind=='pointer':m['pointers']['parsed']='../../outside'
        elif kind=='unknown-version':m['bundle_version']='2.0'
        elif kind=='source-missing':m['source_included']=True;m['source_mode']='included'
        elif kind=='generation':m['book_generation_id']=str(__import__('uuid').uuid4())
        elif kind=='undeclared-runtime':
            p['runtime/tasks/payload']='secret'.encode()
            m['files'].append(dict(relative_path='runtime/tasks/payload',sha256='0'*64,size=6,category='metadata'))
        elif kind=='classification-identity':
            v=json.loads(p['analysis/classification.json']);v['book_id']=str(__import__('uuid').uuid4());p['analysis/classification.json']=json.dumps(v).encode()
        elif kind=='citation':
            name=m['pointers']['verification/current']+'/citations.jsonl'
            lines=p[name].decode().splitlines();v=json.loads(lines[0]);v['text_hash']='0'*64;lines[0]=json.dumps(v);p[name]=('\n'.join(lines)+'\n').encode()
            vmname=m['pointers']['verification/current']+'/manifest.json';vm=json.loads(p[vmname]);vm['artifact_hashes']['citations.jsonl']=__import__('hashlib').sha256(p[name]).hexdigest();p[vmname]=json.dumps(vm).encode()
        else:
            from book_distiller.human.models import HumanAction,TargetRef
            ch,gid=next(iter(m['chapter_generation_ids'].items()))
            action=HumanAction(action='edit',target_ref=TargetRef(object_type='atomic_claim',generation_id=gid,object_id='missing',chapter_id=ch),base_object_hash='0'*64,patch={'book_id':str(__import__('uuid').uuid4())})
            p['human/overrides.jsonl']=(action.model_dump_json()+'\n').encode()
            m['files'].append(dict(relative_path='human/overrides.jsonl',sha256='0'*64,size=0,category='human'))
    bad=rewrite_archive(archive,tmp_path/'bad.bookbundle.zip',change)
    target=BundleService(tmp_path/'target',PROJECT)
    with pytest.raises(BookDistillerError):target.import_bundle(bad)
    assert not target.home.exists()


@pytest.mark.parametrize('when',['copy','publish','after_publish','validation','commit'])
def test_import_failure_rolls_back(exported,tmp_path,monkeypatch,when):
    from contextlib import contextmanager
    service,bundle,archive,_=exported
    target=BundleService(tmp_path/'target',PROJECT)
    target.library.database.initialize()
    def fail(*a,**k):raise OSError('injected '+when)
    if when=='copy':
        import shutil
        original=shutil.copyfile
        def copy(src,dst,*a,**k):
            if str(dst).startswith(str(target.home)):fail()
            return original(src,dst,*a,**k)
        monkeypatch.setattr(shutil,'copyfile',copy)
    elif when=='publish':monkeypatch.setattr(target.library.files,'publish',fail)
    elif when=='after_publish':
        original=target.library.files.publish
        def publish(*a,**k):
            original(*a,**k)
            fail()
        monkeypatch.setattr(target.library.files,'publish',publish)
    elif when=='validation':
        original=target.validate_book
        def validate(root,*a):
            if root.is_relative_to(target.home):fail()
            return original(root,*a)
        monkeypatch.setattr(target,'validate_book',validate)
    else:
        original=target.library.database.transaction
        @contextmanager
        def transaction():
            with original() as conn:
                yield conn
                fail()
        monkeypatch.setattr(target.library.database,'transaction',transaction)
    with pytest.raises((BookDistillerError,OSError),match='injected'):target.import_bundle(archive)
    assert target.library.database.list_books()==[]
    assert not list((target.home/'library').iterdir())


def test_same_snapshot_original_is_noop(exported):
    service,bundle,archive,ingest=exported
    assert service.import_bundle(archive)[2]


def test_human_rules_notes_lock_survive_and_missing_external_rules(rendered,tmp_path,isolated_storage):
    from book_distiller.human.models import HumanAction,HumanRule,UserNote
    from book_distiller.human.service import HumanService,guidance
    from book_distiller.human.resolver import EffectiveKnowledgeResolver
    from book_distiller.core.parse import ParseService
    renderer,ingest,_=rendered
    human=HumanService(renderer.ai)
    obj=next(v for v in EffectiveKnowledgeResolver(ingest.library_path).inventory().values() if v['target_ref']['object_type']=='atomic_claim')
    for action in ('verify','lock','unlock','lock'):
        human.apply(ingest.manifest.book.slug,HumanAction(action=action,target_ref=obj['target_ref'],base_object_hash=obj['base_object_hash']))
    human.note(ingest.manifest.book.slug,UserNote(target_ref=obj['target_ref'],text='Bundle User Note'))
    external=human.rule(ingest.manifest.book.slug,HumanRule(scope='global',applicable_workflows=['ask_book'],instruction='Use short paragraphs.'))
    human.rule(ingest.manifest.book.slug,HumanRule(scope='type',book_type='technical',applicable_workflows=['ask_book'],instruction='Explain technical scope.'))
    human.rule(ingest.manifest.book.slug,HumanRule(scope='book',applicable_workflows=['ask_book'],instruction='Show citations.'))
    renderer.render(ingest.manifest.book.slug)
    archive=tmp_path/'human.bookbundle.zip'
    bundle=BundleService(isolated_storage,PROJECT).export(ingest.manifest.book.slug,archive)
    target=BundleService(tmp_path/'target',PROJECT)
    _,root,_=target.import_bundle(archive)
    effective=EffectiveKnowledgeResolver(root).show(obj['target_ref'])
    assert effective['human_verified'] and effective['locked']
    assert EffectiveKnowledgeResolver(root).notes()[0]['text']=='Bundle User Note'
    task=AskService(AITaskService(target.home,PROJECT)).prepare(str(bundle.book_id),'What are atomic writes?')
    g=task.context.human_guidance
    assert len(g['rules'])==1 and g['rules'][0]['scope']=='book'
    assert len(g['external_rule_dependencies'])==2
    assert all(d['availability']=='missing_or_changed' for d in g['external_rule_dependencies'])
    assert g['warnings'] and 'Bundle User Note' in (root/'output/assets/book-data.js').read_text()
    with pytest.raises(BookDistillerError,match='Source|source'):ParseService(target.home).parse(str(bundle.book_id))


def test_human_changed_collision_preserves_existing(exported,tmp_path):
    from book_distiller.human.models import UserNote
    from book_distiller.human.service import HumanService
    from book_distiller.human.resolver import EffectiveKnowledgeResolver
    _,bundle,archive,_=exported
    target=BundleService(tmp_path/'target',PROJECT)
    _,root,_=target.import_bundle(archive)
    obj=next(iter(EffectiveKnowledgeResolver(root).inventory().values()))
    HumanService(AITaskService(target.home,PROJECT)).note(str(bundle.book_id),UserNote(target_ref=obj['target_ref'],text='Changed after import'))
    with pytest.raises(BookDistillerError):target.import_bundle(archive)
    assert EffectiveKnowledgeResolver(root).notes()[0]['text']=='Changed after import'


def test_generation_collision_and_different_source_identity(exported,tmp_path):
    from book_distiller.renderers.service import RenderService
    _,bundle,archive,_=exported
    target=BundleService(tmp_path/'target',PROJECT)
    _,root,_=target.import_bundle(archive)
    RenderService(target.home,PROJECT).render(str(bundle.book_id),True)
    generation=json.loads((root/'output/render_manifest.json').read_text())['render_generation_id']
    with pytest.raises(BookDistillerError,match='COLLISION'):target.import_bundle(archive)
    assert json.loads((root/'output/render_manifest.json').read_text())['render_generation_id']==generation


def test_source_hash_collision_different_identity(rendered,tmp_path,isolated_storage):
    renderer,ingest,_=rendered
    renderer.render(ingest.manifest.book.slug)
    archive=tmp_path/'source-conflict.bookbundle.zip'
    service=BundleService(isolated_storage,PROJECT);bundle=service.export(ingest.manifest.book.slug,archive)
    target=BundleService(tmp_path/'target',PROJECT)
    existing=target.library.ingest(ingest.library_path/ingest.manifest.source.stored_path,title='Other identity')
    with pytest.raises(BookDistillerError,match='COLLISION'):target.import_bundle(archive)
    assert target.library.database.list_books()==[existing.manifest]


def test_export_failure_no_final_and_stale_reader_rejected(exported,tmp_path,monkeypatch):
    service,bundle,archive,ingest=exported
    output=tmp_path/'failed.bookbundle.zip'
    def fail(*a,**k):raise OSError('self-validation failed')
    with monkeypatch.context() as context:
        context.setattr(service,'inspect',fail)
        with pytest.raises(OSError,match='self-validation'):service.export(ingest.manifest.book.slug,output)
    assert not output.exists() and not list(tmp_path.glob('.failed.bookbundle.zip.*'))
    # Corrupt the derived generation without modifying canonical Knowledge.
    (ingest.library_path/'output/data/book.json').write_text('{}')
    with pytest.raises(BookDistillerError,match='Reader stale'):service.export(ingest.manifest.book.slug,output)


def test_inspect_does_not_touch_library(exported,tmp_path):
    _,bundle,archive,_=exported
    service=BundleService(tmp_path/'unused-home',PROJECT)
    assert service.inspect(archive)==bundle
    assert not service.home.exists()


def test_imported_artifacts_can_start_new_work_without_fake_history(exported,tmp_path):
    from book_distiller.pipeline.verification_tasks import VerificationTasks
    from book_distiller.pipeline.chapter_tasks import ChapterTasks
    _,bundle,archive,_=exported
    target=BundleService(tmp_path/'target',PROJECT)
    _,root,_=target.import_bundle(archive)
    ai=AITaskService(target.home,PROJECT)
    task=VerificationTasks(ai).prepare(str(bundle.book_id),True)
    assert task.context.task_type.startswith('verify_')
    classification=ai.prepare('classify',str(bundle.book_id))
    assert classification.context.task_type=='classify_book'
    generation,tasks=ChapterTasks(ai).prepare_claims(str(bundle.book_id),'ch_0001',force=True)
    assert tasks and generation.generation_id!=bundle.chapter_generation_ids['ch_0001']
    with target.library.database.connect() as c:
        assert c.execute("select count(*) from tasks where status='completed'").fetchone()[0]==0
        assert c.execute('select count(*) from runs').fetchone()[0]==0


def test_source_included_parse_cache_is_artifact_not_executed_task(rendered,tmp_path,isolated_storage):
    from book_distiller.core.parse import ParseService
    renderer,ingest,_=rendered
    renderer.render(ingest.manifest.book.slug)
    archive=tmp_path/'parse-cache.bookbundle.zip'
    bundle=BundleService(isolated_storage,PROJECT).export(ingest.manifest.book.slug,archive,True)
    target=BundleService(tmp_path/'target',PROJECT);target.import_bundle(archive)
    assert ParseService(target.home).parse(str(bundle.book_id)).already_parsed
    with target.library.database.connect() as c:assert c.execute('select count(*) from tasks').fetchone()[0]==0
