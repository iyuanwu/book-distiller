"""Protocol test doubles; real Codex evidence judgments are a separate smoke."""
import json
from copy import deepcopy
from pathlib import Path
import pytest
from test_chapter_tasks import chapter_book,submit
from test_book_tasks import ready,finish
from book_distiller.pipeline.verification_tasks import VerificationTasks
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.pipeline.canonical import load_canonical
from book_distiller.storage import verification as storage

@pytest.fixture
def verification_book(ready):
    books,ingest=ready;finish(books,ingest)
    return VerificationTasks(books.ai),ingest

def binding(task):
    c=task.context
    return dict(task_id=str(c.task_id),book_id=str(c.book_id),edition_id=str(c.edition_id),context_hash=c.context_hash,
        source_sha256=c.source_sha256,normalized_schema_version=c.normalized_document.schema_version,normalized_document_hash=c.normalized_document.document_hash,
        workflow_version=c.workflow_version,prompt_version=c.prompt_version,context_package_version=c.package_version,source_parse_quality=c.source_parse_quality,
        generated_by={'engine':'codex'},verification_generation_id=c.scope['generation_id'],dependency_hash=c.scope['dependency_hash'],
        input_hash=c.input_hash,classification_hash=c.scope['classification_hash'])

def result(task,verdict='supported',strength='strong',codes=None):
    p=task.context.payload;kind=task.context.task_type
    if kind=='review_chapter_coverage':value={'reviewed_block_ids':p['reviewed_block_ids'],'issues':[],'review_summary':'Protocol fixture: no omission.'}
    elif kind=='review_quality':value={'issues':[],'review_summary':'Protocol fixture: no issue.'}
    else:
        citations=p['original_citation_ids']
        value={'assessment':{'object_ref':p['target']['ref'],'citation_ids':citations,'supplemental_citation_ids':[],
            'evidence_strength':strength,'fidelity_verdict':verdict,'unsupported_aspects':[], 'reason_codes':codes or [],
            'reviewer_confidence':.9,'verification_summary':'Protocol fixture source supports the object.'}}
    return binding(task)|deepcopy(value)

def complete(service,ingest,force=False):
    task=service.prepare(ingest.manifest.book.slug,force);count=0
    while not isinstance(task,Path):
        submit(service.ai,task,result(task));count+=1;assert count<200
        task=service.prepare(ingest.manifest.book.slug)
    return task

def test_complete_generation_and_knowledge_unchanged(verification_book):
    service,ingest=verification_book
    knowledge=ingest.library_path/'knowledge';before={str(p):p.read_bytes() for p in knowledge.rglob('*') if p.is_file()}
    path=complete(service,ingest)
    assert path.is_symlink();report=json.loads((path/'quality_report.json').read_text())
    assert report['status']=='needs_review'  # Existing protocol fixture repeats identical Claims.
    assert report['metrics']['exact_duplicate_rate']['value']>0.1
    assert report['metrics']['verified_claim_support_rate']['value']==1
    assert before=={str(p):p.read_bytes() for p in knowledge.rglob('*') if p.is_file()}
    assert service.prepare(ingest.manifest.book.slug).resolve()==path.resolve()

def test_invalid_citation_retry_uuid(verification_book):
    service,ingest=verification_book;t=service.prepare(ingest.manifest.book.slug)
    bad=result(t);bad['assessment']['citation_ids']=['cit_'+'0'*32]
    with pytest.raises(ProtocolError,match='BROKEN_CITATION'):submit(service.ai,t,bad)
    again=service.prepare(ingest.manifest.book.slug);assert again.task_id==t.task_id
    submit(service.ai,t,result(t));next_task=service.prepare(ingest.manifest.book.slug)
    assert next_task.task_id!=t.task_id

def test_one_local_recheck_supplemental(verification_book):
    service,ingest=verification_book;t=service.prepare(ingest.manifest.book.slug)
    submit(service.ai,t,result(t,'partially_supported','weak',['context_insufficient']))
    repair=service.prepare(ingest.manifest.book.slug);assert repair.context.task_type=='repair_evidence'
    p=repair.context.payload;assert len(p['source_excerpts'])>=len(t.context.payload['source_excerpts'])
    assert {s['chapter_id'] for s in p['source_excerpts']}=={t.context.payload['target']['ref']['chapter_id']}
    r=result(repair,'partially_supported','weak',['context_insufficient']);extra=[c['citation_id'] for c in p['citations'] if c['citation_id'] not in p['original_citation_ids']]
    r['assessment']['citation_ids']+=extra[:1];r['assessment']['supplemental_citation_ids']=extra[:1]
    submit(service.ai,repair,r);next_task=service.prepare(ingest.manifest.book.slug)
    assert next_task.context.task_type!='repair_evidence'
    path=complete(service,ingest);report=json.loads((path/'quality_report.json').read_text())
    assert report['status']=='needs_review' and report['automatic_rechecks']==1

def test_after_pointer_metadata_failure_rolls_back(verification_book,monkeypatch):
    service,ingest=verification_book;old=complete(service,ingest).resolve();original=service.save
    def fail(d,s):
        if s['completed']:raise OSError('metadata injected failure')
        original(d,s)
    monkeypatch.setattr(service,'save',fail)
    with pytest.raises(Exception,match='metadata injected'):complete(service,ingest,True)
    assert storage.current(ingest.library_path)==old
    monkeypatch.setattr(service,'save',original);assert complete(service,ingest).resolve()!=old

@pytest.mark.parametrize('change',['chapter','book','classification','normalized'])
def test_stale_dependencies(verification_book,chapter_book,change,isolated_storage):
    from test_chapter_tasks import all_claims,atom_result,binding as class_binding
    from book_distiller.core.parse import ParseService
    service,ingest=verification_book;old=complete(service,ingest).resolve()
    if change=='book':finish(service.books,ingest,True)
    elif change=='chapter':
        _,t=all_claims(chapter_book,True);submit(service.ai,t,atom_result(t))
    elif change=='classification':
        t=service.ai.prepare('classify',ingest.manifest.book.slug)
        submit(service.ai,t,class_binding(t)|dict(primary_type='technical',secondary_types=[],tags=['new-tag'],confidence=.9,
            evidence=[{'block_id':t.context.blocks[0].block_id,'supports':['technical'],'note':'Changed classification.'}],rationale_summary='Changed classification version.'))
    else:ParseService(isolated_storage).parse(ingest.manifest.book.slug,force=True)
    doc=load_canonical(ingest.library_path,ingest.manifest,service.library.database)
    assert service.describe(ingest.library_path,doc)['Verification']=='stale'
    assert storage.current(ingest.library_path)==old
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'):service.prepare(ingest.manifest.book.slug)


def test_higher_task_rejection_keeps_old_generation(verification_book):
    service,ingest=verification_book;old=complete(service,ingest).resolve();t=service.prepare(ingest.manifest.book.slug,True)
    while t.context.task_type!='verify_core_idea':
        submit(service.ai,t,result(t));t=service.prepare(ingest.manifest.book.slug)
    bad=result(t);bad['assessment']['object_ref']['object_id']='idea_foreign'
    with pytest.raises(ProtocolError,match='INVALID_OBJECT_REFERENCE'):submit(service.ai,t,bad)
    assert storage.current(ingest.library_path)==old
    assert service.prepare(ingest.manifest.book.slug).task_id==t.task_id
    submit(service.ai,t,result(t));assert complete(service,ingest).resolve()!=old


def test_broken_canonical_citation_fails_before_ai(verification_book):
    from book_distiller.pipeline.files import write_json
    from book_distiller.storage.filesystem import hash_source
    service,ingest=verification_book;chapter=ingest.library_path/'knowledge/chapters/ch_0001'
    claims=[json.loads(l) for l in (chapter/'claims.jsonl').read_text().splitlines()];claims[0]['evidence']=[{'block_id':'blk_999999'}]
    (chapter/'claims.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in claims))
    cm=json.loads((chapter/'chapter.json').read_text());cm['artifact_hashes']['claims.jsonl']=hash_source(chapter/'claims.jsonl')[0];write_json(chapter/'chapter.json',cm)
    # Make a deliberately self-consistent isolated fixture, leaving the invalid reference for the verifier.
    book=ingest.library_path/'knowledge/book';bm=json.loads((book/'book_model.json').read_text());bm['dependencies']['chapters']['ch_0001']['manifest_hash']=hash_source(chapter/'chapter.json')[0];write_json(book/'book_model.json',bm)
    with pytest.raises(ProtocolError,match='BROKEN_CITATION'):service.prepare(ingest.manifest.book.slug)
    failure=json.loads((ingest.library_path/'runtime/verification-failure.json').read_text());assert failure['status']=='failed'
    assert storage.current(ingest.library_path) is None


def test_coverage_omission_gate_and_dynamic_evidence_path(verification_book):
    from book_distiller.evidence.paths import evidence_path
    service,ingest=verification_book;t=service.prepare(ingest.manifest.book.slug);inserted=False
    while not isinstance(t,Path):
        r=result(t)
        if t.context.task_type=='review_chapter_coverage' and not inserted:
            p=t.context.payload;r['issues']=[dict(issue_type='MAJOR_OMISSION',severity='major',object_refs=[],source_refs=[p['citations'][0]['citation_id']],chapter_id=p['chapter_ids'][0],summary='Protocol fixture important omitted idea.',suggested_action='rerun_claim_extraction',reviewer_confidence=.9)];inserted=True
        submit(service.ai,t,r);t=service.prepare(ingest.manifest.book.slug)
    report=json.loads((t/'quality_report.json').read_text());assert report['status']=='needs_review' and report['metrics']['major_omission_count']['value']==1
    data=evidence_path(ingest.library_path,'idea_001');assert data['nodes']['idea_001']['citations'][0]['source_excerpt']
    assert any(k.startswith('claim_') for k in data['nodes'])

@pytest.mark.parametrize('failure',['source','report','pointer'])
def test_integrity_failure_is_not_completed(verification_book,failure):
    service,ingest=verification_book;path=complete(service,ingest)
    if failure=='source':
        source=ingest.library_path/ingest.manifest.source.stored_path;source.write_text('Changed original source')
    elif failure=='report':(path/'quality_report.json').write_text('{}')
    else:path.unlink()
    with pytest.raises(Exception):service.prepare(ingest.manifest.book.slug)
    document=load_canonical(ingest.library_path,ingest.manifest,service.library.database)
    assert service.describe(ingest.library_path,document)['Verification']=='failed'


def test_claim_foreign_identity_is_rejected(verification_book):
    from uuid import uuid4
    from book_distiller.pipeline.files import write_json
    from book_distiller.storage.filesystem import hash_source
    service,ingest=verification_book;path=ingest.library_path/'knowledge/chapters/ch_0001'
    rows=[json.loads(l) for l in (path/'claims.jsonl').read_text().splitlines()];rows[0]['book_id']=str(uuid4())
    (path/'claims.jsonl').write_text(''.join(json.dumps(r)+'\n' for r in rows))
    meta=json.loads((path/'chapter.json').read_text());meta['artifact_hashes']['claims.jsonl']=hash_source(path/'claims.jsonl')[0];write_json(path/'chapter.json',meta)
    bp=ingest.library_path/'knowledge/book';bm=json.loads((bp/'book_model.json').read_text());bm['dependencies']['chapters']['ch_0001']['manifest_hash']=hash_source(path/'chapter.json')[0];write_json(bp/'book_model.json',bm)
    with pytest.raises(ProtocolError,match='dependency binding'):service.prepare(ingest.manifest.book.slug)


def test_large_plan_bounded_context_and_resume(verification_book):
    """Synthetic load test, not semantic verification: 1,000 Claims + 500 Atoms."""
    import tracemalloc
    from book_distiller.pipeline.files import write_json
    from book_distiller.storage.filesystem import hash_source
    service,ingest=verification_book;path=ingest.library_path/'knowledge/chapters/ch_0001'
    claims=[json.loads(l) for l in (path/'claims.jsonl').read_text().splitlines()];atoms=json.loads((path/'atoms.json').read_text())
    original_claim_count=len(claims)
    for i in range(1000-len(claims)):
        row=deepcopy(claims[0]);row['claim_id']=f'claim_scale_{i:06d}';row['statement']=f'Synthetic load statement {i}.';claims.append(row)
    for i in range(500-len(atoms['atoms'])):
        row=deepcopy(atoms['atoms'][0]);row['atom_id']=f'atom_scale_{i:06d}';row['summary']=f'Synthetic load summary {i}.';row['claim_ids']=[claims[original_claim_count+i]['claim_id']];atoms['atoms'].append(row)
    (path/'claims.jsonl').write_text(''.join(json.dumps(c)+'\n' for c in claims));write_json(path/'atoms.json',atoms)
    cm=json.loads((path/'chapter.json').read_text());cm['artifact_hashes']={n:hash_source(path/n)[0] for n in cm['artifact_hashes']};write_json(path/'chapter.json',cm)
    bp=ingest.library_path/'knowledge/book';bm=json.loads((bp/'book_model.json').read_text());bm['dependencies']['chapters']['ch_0001']['manifest_hash']=hash_source(path/'chapter.json')[0];write_json(bp/'book_model.json',bm)
    tracemalloc.start();task=service.prepare(ingest.manifest.book.slug);peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
    state_path=service.root(ingest.library_path,task.context.scope['generation_id'])/'state.json';state=json.loads(state_path.read_text())
    assert len(state['entries'])>=1500 and peak<64*1024*1024
    for _ in range(5):
        assert task.context.budget.selected_chars<=48000
        assert sum(len(v['text']) for v in task.context.payload['source_excerpts'])<=16000
        submit(service.ai,task,result(task));task=service.prepare(ingest.manifest.book.slug)
    old=json.loads(state_path.read_text());resumed=VerificationTasks(service.ai).prepare(ingest.manifest.book.slug)
    assert resumed.task_id==task.task_id and old['cursor']==5
    assert json.loads(state_path.read_text())['assessments']==old['assessments']
    print(f'scale: objects={len(state["entries"])} peak_bytes={peak} checkpointed=5 pending_reused=True')


def test_evidence_path_pins_old_same_named_objects(verification_book,chapter_book):
    from test_chapter_tasks import all_claims,atom_result
    from book_distiller.evidence.paths import evidence_path
    service,ingest=verification_book;complete(service,ingest)
    before=evidence_path(ingest.library_path,'idea_001')
    atom_key=next(k for k in before['nodes'] if k.startswith('atom_'))
    _,task=all_claims(chapter_book,True);response=atom_result(task);response['atoms'][0]['summary']='Different new-generation content.'
    submit(service.ai,task,response)
    after=evidence_path(ingest.library_path,'idea_001')
    assert before==after
    new=json.loads((ingest.library_path/'knowledge/chapters/ch_0001/atoms.json').read_text())['atoms'][0]
    assert new['atom_id']==atom_key and new['generation_id']!=after['nodes'][atom_key]['object']['generation_id']


def test_invalid_coverage_refs_preserve_checkpoint(verification_book):
    service,ingest=verification_book;t=service.prepare(ingest.manifest.book.slug)
    while t.context.task_type!='review_chapter_coverage':
        submit(service.ai,t,result(t));t=service.prepare(ingest.manifest.book.slug)
    state_path=service.root(ingest.library_path,t.context.scope['generation_id'])/'state.json';before=json.loads(state_path.read_text())['assessments']
    bad=result(t);bad['reviewed_block_ids']=bad['reviewed_block_ids'][:-1]
    with pytest.raises(ProtocolError,match='COVERAGE_INCOMPLETE'):submit(service.ai,t,bad)
    bad=result(t);bad['issues']=[dict(issue_type='MAJOR_OMISSION',severity='major',source_refs=['cit_fake'],chapter_id='ch_0001',summary='Invalid source.',suggested_action='inspect_source',reviewer_confidence=.9)]
    with pytest.raises(ProtocolError,match='BROKEN_CITATION'):submit(service.ai,t,bad)
    assert service.prepare(ingest.manifest.book.slug).task_id==t.task_id
    assert json.loads(state_path.read_text())['assessments']==before


def test_cli_pending_resume_and_status(verification_book,isolated_storage):
    from typer.testing import CliRunner
    from book_distiller.cli.main import app
    service,ingest=verification_book;runner=CliRunner();env={'BOOK_DISTILLER_HOME':str(isolated_storage)};slug=ingest.manifest.book.slug
    first=runner.invoke(app,['verify',slug],env=env);assert first.exit_code==0,first.output
    assert 'Progress: 0 /' in first.output
    second=runner.invoke(app,['verify',slug],env=env);assert second.exit_code==0 and second.output==first.output
    pending=runner.invoke(app,['status',slug],env=env);assert pending.exit_code==0 and 'in_progress' in pending.output and 'pending' in pending.output
    old=complete(service,ingest).resolve()
    forced=runner.invoke(app,['verify',slug,'--force'],env=env);assert forced.exit_code==0
    assert storage.current(ingest.library_path).resolve()==old
    status=runner.invoke(app,['status',slug],env=env);assert status.exit_code==0 and 'in_progress' in status.output and 'Previous quality gate' in status.output
