"""Structural protocol tests; judgments are explicit test doubles, not real smoke."""
import json
from pathlib import Path
import pytest
from test_chapter_tasks import chapter_book,all_claims,submit,atom_result,prepare,claim_result
from book_distiller.pipeline.book_tasks import BookTasks
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.pipeline.canonical import load_canonical
from book_distiller.storage import book_knowledge


def binding(task):
    c=task.context
    return dict(task_id=str(c.task_id),book_id=str(c.book_id),edition_id=str(c.edition_id),context_hash=c.context_hash,
        source_sha256=c.source_sha256,normalized_schema_version=c.normalized_document.schema_version,
        normalized_document_hash=c.normalized_document.document_hash,workflow_version=c.workflow_version,
        prompt_version=c.prompt_version,context_package_version=c.package_version,source_parse_quality=c.source_parse_quality,
        generated_by={'engine':'codex'},generation_id=c.scope['generation_id'],dependency_hash=c.scope['dependency_hash'],
        input_hash=c.input_hash,classification_hash=c.scope['dependencies']['classification_hash'])


def response(task):
    c=task.context;items=c.payload['items'];ids=[i['id'] for i in items]
    if c.task_type=='normalize_concepts':
        value={'concepts':[dict(concept_id='concept_atomic_writes',canonical_name='atomic writes',source_terms=['atomic writes'],atom_ids=ids,confidence=.9)]}
    elif c.task_type=='build_core_ideas':
        value={'core_ideas':[dict(core_idea_id='idea_001',title='Reliable publication',statement='Publish complete validated results.',atom_ids=ids,
            concept_ids=['concept_atomic_writes'],supporting_chapters=sorted({ch for i in items for ch in i['chapters']}),importance=.8,confidence=.9,promotion_reason='Repeated source principle.')]}
    elif c.task_type=='build_mental_models': value={'mental_models':[]}
    elif c.task_type=='build_meta_principles': value={'meta_principles':[]}
    elif c.task_type=='synthesis_reduce':
        value={'groups':[dict(title='Publication',summary='Validate complete results before publishing; preserve prior state on failure.',source_ids=ids)]}
    else:
        value={'atoms':[dict(title='Publication',summary='Publish complete results.',atom_type='principle',claim_ids=ids,concept_terms=['atomic writes'],importance=.8,confidence=.9)]}
    return binding(task)|value


@pytest.fixture
def ready(chapter_book):
    ai,chapters,ingest=chapter_book
    _,task=all_claims(chapter_book);submit(ai,task,atom_result(task))
    return BookTasks(ai),ingest


def finish(service,ingest,force=False):
    task=service.prepare(ingest.manifest.book.slug,force)
    for _ in range(100):
        if isinstance(task,Path): return task
        submit(service.ai,task,response(task))
        task=service.prepare(ingest.manifest.book.slug)
    raise AssertionError('Too many stages')


def test_book_pipeline_retry_and_memory(ready):
    service,ingest=ready;task=service.prepare(ingest.manifest.book.slug)
    assert task.context.package_version=='1.2'
    assert task.context.blocks==[]
    bad=response(task);bad['concepts'][0]['atom_ids']=['missing']
    with pytest.raises(ProtocolError,match='INVALID_ATOM_REFERENCE'): submit(service.ai,task,bad)
    assert service.prepare(ingest.manifest.book.slug).task_id==task.task_id
    path=finish(service,ingest);target=path.resolve()
    assert service.prepare(ingest.manifest.book.slug).resolve()==target
    model=json.loads((path/'book_model.json').read_text())
    assert model['metrics']['core_idea_count']==1 and model['metrics']['meta_principle_count']==0
    memory=json.loads((path/'book_memory.json').read_text())
    assert memory['selected_chars']<=12000
    doc=load_canonical(ingest.library_path,ingest.manifest,service.library.database)
    assert service.describe(ingest.library_path,doc)['Book synthesis']=='completed'
    next_task=service.prepare(ingest.manifest.book.slug,True)
    assert next_task.context.payload['existing_concepts'][0]['concept_id']=='concept_atomic_writes'


def test_invalid_idea_reference_and_no_partial_publish(ready):
    service,ingest=ready;task=service.prepare(ingest.manifest.book.slug)
    submit(service.ai,task,response(task));task=service.prepare(ingest.manifest.book.slug)
    bad=response(task);bad['core_ideas'][0]['atom_ids']=['bad']
    with pytest.raises(ProtocolError,match='INVALID_ATOM_REFERENCE'): submit(service.ai,task,bad)
    assert book_knowledge.current(ingest.library_path) is None
    finish(service,ingest)


def test_atomic_book_publish_rollback(ready,monkeypatch):
    service,ingest=ready;old=finish(service,ingest).resolve()
    original=book_knowledge.publish
    monkeypatch.setattr(book_knowledge,'publish',lambda *a: (_ for _ in ()).throw(OSError('injected switch failure')))
    with pytest.raises(Exception,match='injected'): finish(service,ingest,True)
    assert book_knowledge.current(ingest.library_path)==old
    monkeypatch.setattr(book_knowledge,'publish',original)
    assert finish(service,ingest).resolve()!=old


def test_chapter_change_invalidates_book(ready,chapter_book):
    service,ingest=ready;finish(service,ingest)
    task=service.prepare(ingest.manifest.book.slug,True)
    ai,chapters,_=chapter_book
    _,new=all_claims(chapter_book,True);submit(ai,new,atom_result(new))
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'): submit(ai,task,response(task))
    doc=load_canonical(ingest.library_path,ingest.manifest,ai.library.database)
    assert service.describe(ingest.library_path,doc)['Book synthesis']=='stale'


def test_chapter_reduce_and_chain(chapter_book,monkeypatch):
    ai,chapters,ingest=chapter_book
    _,tasks=prepare(chapter_book)
    for t in tasks: submit(ai,t,claim_result(t))
    import book_distiller.pipeline.book_tasks as module
    from book_distiller.pipeline.reduction import batches
    monkeypatch.setattr(module,'batches',lambda items:batches(items,max_items=3))
    service=BookTasks(ai);task=service.prepare_chapter(ingest.manifest.book.slug,'ch_0001')
    reduced=0
    while not isinstance(task,Path):
        reduced+=task.context.task_type=='synthesis_reduce'
        submit(ai,task,response(task))
        task=service.prepare_chapter(ingest.manifest.book.slug,'ch_0001')
    assert reduced>=2
    atoms=json.loads((task/'atoms.json').read_text())['atoms']
    claims=[json.loads(l) for l in (task/'claims.jsonl').read_text().splitlines()]
    assert set(atoms[0]['claim_ids'])=={c['claim_id'] for c in claims}
    assert atoms[0]['context_package_version']=='1.2'

@pytest.mark.parametrize('stage,field,badref',[
    ('build_mental_models','core_idea_ids','idea_other_book'),
    ('build_meta_principles','core_idea_ids','idea_other_book'),
])
def test_invalid_promotions_retain_old_book(ready,stage,field,badref):
    service,ingest=ready;old=finish(service,ingest).resolve()
    task=service.prepare(ingest.manifest.book.slug,True)
    while task.context.task_type!=stage:
        submit(service.ai,task,response(task));task=service.prepare(ingest.manifest.book.slug)
    if stage=='build_mental_models':
        obj=dict(model_id='model_001',name='X',description='X',mechanism='X',core_idea_ids=[badref],promotion_reason='X',confidence=.9)
        payload={'mental_models':[obj]}
    else:
        obj=dict(principle_id='principle_001',title='X',statement='X',core_idea_ids=['idea_001',badref],supporting_chapters=['ch_0001','ch_0002'],promotion_reason='X',confidence=.9)
        payload={'meta_principles':[obj]}
    with pytest.raises(ProtocolError,match='INVALID_IDEA_REFERENCE'): submit(service.ai,task,binding(task)|payload)
    assert book_knowledge.current(ingest.library_path)==old
    submit(service.ai,task,response(task));finish(service,ingest)


@pytest.mark.parametrize('change',['classification','normalized'])
def test_stale_source_and_classification(ready,change,isolated_storage):
    from test_chapter_tasks import binding as old_binding
    from book_distiller.core.parse import ParseService
    service,ingest=ready;finish(service,ingest);task=service.prepare(ingest.manifest.book.slug,True)
    if change=='classification':
        classify=service.ai.prepare('classify',ingest.manifest.book.slug)
        submit(service.ai,classify,old_binding(classify)|dict(primary_type='business',secondary_types=[],tags=[],confidence=.8,
            evidence=[{'block_id':classify.context.blocks[0].block_id,'supports':['business'],'note':'Test changed classification.'}],rationale_summary='Test change.'))
    else: ParseService(isolated_storage).parse(ingest.manifest.book.slug,force=True)
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'): submit(service.ai,task,response(task))
    doc=load_canonical(ingest.library_path,ingest.manifest,service.library.database)
    assert service.describe(ingest.library_path,doc)['Book synthesis']=='stale'


def test_alias_conflict_and_stable_existing_ids(ready):
    service,ingest=ready;finish(service,ingest);task=service.prepare(ingest.manifest.book.slug,True)
    bad=response(task);bad['concepts'][0]['concept_id']='concept_renamed'
    with pytest.raises(ProtocolError,match='CONCEPT_ALIAS_CONFLICT'): submit(service.ai,task,bad)
    finish(service,ingest)


def test_optional_book_memory_context(ready):
    from book_distiller.pipeline.synthesis_context import build_synthesis_context
    from book_distiller.pipeline.workflows import load_workflow
    service,ingest=ready;path=finish(service,ingest)
    memory=json.loads((path/'book_memory.json').read_text())
    task=service.prepare(ingest.manifest.book.slug,True);c=task.context
    doc=load_canonical(ingest.library_path,ingest.manifest,service.library.database)
    workflow=load_workflow(service.ai.project,c.task_type,['technical'])
    added=build_synthesis_context(doc,c.task_id,workflow,c.scope,c.payload,c.classification,include_book_memory=True,book_memory=memory)
    assert added.payload['book_memory']['book_memory_hash']==memory['book_memory_hash']
    assert added.context_hash!=c.context_hash and added.input_hash!=c.input_hash


def test_old_bound_reference_resolves_old_generation(ready,chapter_book):
    from book_distiller.pipeline.book_references import resolve_reference
    service,ingest=ready;old=finish(service,ingest).resolve()
    model=json.loads((old/'book_model.json').read_text())
    idea=json.loads((old/'core_ideas.json').read_text())['core_ideas'][0]
    ref=idea['atom_refs'][0];before=resolve_reference(ingest.library_path,model,ref)
    ai,chapters,_=chapter_book
    _,task=all_claims(chapter_book,True)
    result=atom_result(task);result['atoms'][0]['summary']='A changed statement in a new generation.'
    submit(ai,task,result)
    after=resolve_reference(ingest.library_path,model,ref)
    assert before==after and str(after['generation_id'])==ref['chapter_generation_id']
    current=json.loads((ingest.library_path/'knowledge/chapters/ch_0001/atoms.json').read_text())['atoms'][0]
    assert current['atom_id']==after['atom_id'] and current['generation_id']!=after['generation_id']
    with pytest.raises(Exception,match='generation mismatch'):
        resolve_reference(ingest.library_path,model,ref|{'chapter_generation_id':current['generation_id']})


def test_metadata_failure_after_switch_restores_previous(ready,monkeypatch):
    service,ingest=ready;old=finish(service,ingest).resolve()
    original=service.save
    def fail_completed(directory,state):
        if state['completed']: raise OSError('metadata commit failed')
        return original(directory,state)
    monkeypatch.setattr(service,'save',fail_completed)
    with pytest.raises(Exception,match='metadata commit failed'): finish(service,ingest,True)
    assert book_knowledge.current(ingest.library_path)==old
    monkeypatch.setattr(service,'save',original)
    assert finish(service,ingest).resolve()!=old


def test_1200_atom_full_protocol_reduce(ready,tmp_path):
    """Synthetic Canonical data stress; AI outputs here are test doubles."""
    import tracemalloc
    from book_distiller.pipeline.files import write_json
    from book_distiller.storage.filesystem import hash_source
    from book_distiller.pipeline.book_references import resolve_reference
    service,ingest=ready;chapter=ingest.library_path/'knowledge/chapters/ch_0001'
    source=json.loads((chapter/'atoms.json').read_text())['atoms'][0]
    atoms=[source|{'atom_id':f'atom_ch_0001_{i:04d}','summary':'Prepare complete writes. '*25} for i in range(1200)]
    write_json(chapter/'atoms.json',{'schema_version':'1.0','atoms':atoms})
    info=json.loads((chapter/'chapter.json').read_text());info['artifact_hashes']['atoms.json']=hash_source(chapter/'atoms.json')[0]
    info['metrics']['atoms']=1200;write_json(chapter/'chapter.json',info)
    del atoms
    tracemalloc.start();task=service.prepare(ingest.manifest.book.slug)
    batch_count=0;max_context=0;tasks=0
    while not isinstance(task,Path):
        max_context=max(max_context,task.context.budget.selected_chars)
        assert task.context.budget.selected_chars<=60000
        batch_count+=task.context.task_type=='synthesis_reduce';tasks+=1
        submit(service.ai,task,response(task));task=service.prepare(ingest.manifest.book.slug)
        assert tasks<150
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    idea=json.loads((task/'core_ideas.json').read_text())['core_ideas'][0]
    refs=idea['atom_refs'];actual=[r['atom_id'] for r in refs];expected={f'atom_ch_0001_{i:04d}' for i in range(1200)}
    model=json.loads((task/'book_model.json').read_text())
    assert resolve_reference(ingest.library_path,model,refs[0])['atom_id']==refs[0]['atom_id']
    report={'input_refs':1200,'output_refs':len(refs),'missing_refs':len(expected-set(actual)),
        'duplicated_refs':len(actual)-len(set(actual)),'batch_count':batch_count,'max_context_chars':max_context,'peak_memory_bytes':peak}
    print('\nREDUCE_STRESS',json.dumps(report))
    assert report['missing_refs']==report['duplicated_refs']==0
    assert peak<128*1024*1024 and batch_count>50


def test_invalid_model_refs_and_zero_principles(ready):
    service,ingest=ready;task=service.prepare(ingest.manifest.book.slug)
    while task.context.task_type!='build_meta_principles':
        submit(service.ai,task,response(task));task=service.prepare(ingest.manifest.book.slug)
    invalid=binding(task)|{'meta_principles':[dict(principle_id='principle_001',title='Unsupported',statement='Unsupported',
        model_ids=['model_missing','model_other_book'],supporting_chapters=['ch_0001','ch_0002'],promotion_reason='Invalid test.',confidence=.8)]}
    with pytest.raises(ProtocolError,match='INVALID_MODEL_REFERENCE'): submit(service.ai,task,invalid)
    submit(service.ai,task,response(task));path=finish(service,ingest)
    assert json.loads((path/'meta_principles.json').read_text())['meta_principles']==[]


def test_aliases_and_relation_types_do_not_delete_atoms(ready):
    from book_distiller.pipeline.files import write_json
    from book_distiller.storage.filesystem import hash_source
    service,ingest=ready;chapter=ingest.library_path/'knowledge/chapters/ch_0001'
    atom=json.loads((chapter/'atoms.json').read_text())['atoms'][0]
    write_json(chapter/'atoms.json',{'schema_version':'1.0','atoms':[atom,atom|{'atom_id':'atom_ch_0001_002'}]})
    info=json.loads((chapter/'chapter.json').read_text());info['artifact_hashes']['atoms.json']=hash_source(chapter/'atoms.json')[0];write_json(chapter/'chapter.json',info)
    before=(chapter/'atoms.json').read_bytes();task=service.prepare(ingest.manifest.book.slug)
    value=response(task);value['concepts'][0].update(zh_name='原子写入',aliases=['atomic publication'])
    submit(service.ai,task,value);task=service.prepare(ingest.manifest.book.slug)
    value=response(task);value['decisions']=[dict(decision=kind,source_atom_ids=[atom['atom_id'],'atom_ch_0001_002'],canonical_concept_ids=['concept_atomic_writes'],reason='Protocol fixture checks independent relationship records.',confidence=.9) for kind in ['merge_atoms','related_to','potential_tension','contradicts']]
    submit(service.ai,task,value);path=finish(service,ingest)
    assert (chapter/'atoms.json').read_bytes()==before
    assert len(json.loads((path/'relationships.json').read_text())['decisions'])==4
    task=service.prepare(ingest.manifest.book.slug,True)
    assert task.context.payload['existing_concepts'][0]['zh_name']=='原子写入'
    with pytest.raises(ProtocolError,match='CONCEPT_REGISTRY_LOSS'): submit(service.ai,task,response(task))


def test_long_chapter_claims_use_bounded_reduce(chapter_book,monkeypatch):
    from book_distiller.pipeline.files import write_json
    from book_distiller.storage.filesystem import hash_source
    from book_distiller.pipeline.knowledge_context import build_knowledge_context
    from book_distiller.pipeline.workflows import load_workflow
    from book_distiller.core.models.knowledge import AtomicClaim
    from uuid import uuid4
    ai,chapters,ingest=chapter_book
    generation,tasks=prepare(chapter_book)
    for t in tasks: submit(ai,t,claim_result(t))
    pending=chapters.pending_path(ingest.library_path,generation)
    template=json.loads((pending/'claims.jsonl').read_text().splitlines()[0])
    # Generated source fixture, not model-generated claims: larger than the old single-task budget.
    claims=[template|{'claim_id':f'claim_ch_0001_stress_{i:04d}','statement':'Validate the complete candidate before publication. '*20} for i in range(250)]
    (pending/'claims.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in claims))
    receipt=json.loads((pending/'claims-receipt.json').read_text());receipt.update(claims_hash=hash_source(pending/'claims.jsonl')[0],claim_count=250);write_json(pending/'claims-receipt.json',receipt)
    doc=load_canonical(ingest.library_path,ingest.manifest,ai.library.database)
    classification,_=chapters.classification(ingest.library_path,doc)
    from book_distiller.core.errors import ValidationError
    with pytest.raises(ValidationError,match='CHAPTER_CONTEXT_TOO_LARGE'):
        build_knowledge_context(doc,uuid4(),load_workflow(ai.project,'build_chapter_atoms',['technical']),generation,classification,
            claims=[AtomicClaim.model_validate(c) for c in claims],claims_hash=receipt['claims_hash'])
    service=BookTasks(ai);task=service.prepare_chapter(ingest.manifest.book.slug,'ch_0001');count=0
    while not isinstance(task,Path):
        assert task.context.budget.selected_chars<=60000
        count+=1;submit(ai,task,response(task));task=service.prepare_chapter(ingest.manifest.book.slug,'ch_0001')
        assert count<100
    atoms=json.loads((task/'atoms.json').read_text())['atoms']
    assert len(atoms[0]['claim_ids'])==250
    assert hash_source(task/'claims.jsonl')[0]==receipt['claims_hash']
