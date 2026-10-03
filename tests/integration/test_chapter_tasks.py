"""Isolated deterministic protocol tests. AI judgments here are test doubles."""
import json
from pathlib import Path
from uuid import uuid4
import pytest
from book_distiller.core.ingest import IngestService
from book_distiller.core.parse import ParseService
from book_distiller.core.models.knowledge import ChunkBudget
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.pipeline.chapter_tasks import ChapterTasks
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.core.errors import StorageError
from book_distiller.storage import knowledge as storage

PROJECT=Path(__file__).parents[2]


def binding(task):
    c=task.context
    result=dict(task_id=str(c.task_id),book_id=str(c.book_id),edition_id=str(c.edition_id),context_hash=c.context_hash,
        source_sha256=c.source_sha256,normalized_schema_version=c.normalized_document.schema_version,
        normalized_document_hash=c.normalized_document.document_hash,workflow_version=c.workflow_version,
        prompt_version=c.prompt_version,context_package_version=c.package_version,source_parse_quality=c.source_parse_quality,
        generated_by={'engine':'codex'})
    if c.task_type!='classify_book':
        result.update(generation_id=str(c.generation.generation_id),chapter_id=c.generation.chapter_id,
            classification_hash=c.generation.classification_hash,chunker_version='analysis-chunker-v1')
    return result


def submit(ai,task,result):
    path=task.directory/'result.json'
    path.write_text(json.dumps(result),encoding='utf-8')
    return ai.submit(task.task_id,path)


def claim_result(task,claims=None):
    if claims is None:
        claims=[dict(statement='A complete local write preserves the previous result on preparation failure.',claim_type='principle',
            concept_terms=['atomic writes'],importance=.8,confidence=.9,evidence=[{'block_id':task.context.chunk.primary_blocks[0]}])]
    return binding(task)|{'chunk_id':task.context.chunk.chunk_id,'claims':claims}


def atom_result(task,atoms=None):
    if atoms is None:
        atoms=[dict(title='Reliable publication',summary='Publish complete local writes and preserve prior data on preparation failure.',atom_type='principle',
            claim_ids=[c.claim_id for c in task.context.claims],concept_terms=['atomic writes'],reasoning=[],examples=[],importance=.8,confidence=.9)]
    return binding(task)|{'claims_hash':task.context.claims_hash,'atoms':atoms}


@pytest.fixture
def chapter_book(isolated_storage,tmp_path):
    source=tmp_path/'chapter.txt'
    source.write_text('\n\n'.join(f'Paragraph {i}. '+('Prepare and validate a complete temporary file before replacing the previous result. '*4) for i in range(12)))
    ingest=IngestService(isolated_storage).ingest(source)
    ParseService(isolated_storage).parse(ingest.manifest.book.slug)
    ai=AITaskService(isolated_storage,PROJECT)
    task=ai.prepare('classify',ingest.manifest.book.slug)
    submit(ai,task,binding(task)|dict(primary_type='technical',secondary_types=[],tags=['file-storage'],confidence=.9,
        evidence=[{'block_id':task.context.blocks[0].block_id,'supports':['technical'],'note':'Original storage exercise.'}],rationale_summary='Software storage exercise.'))
    return ai,ChapterTasks(ai),ingest


def prepare(chapter_book,force=False):
    ai,chapters,ingest=chapter_book
    return chapters.prepare_claims(ingest.manifest.book.slug,'ch_0001',force=force,
        budget=ChunkBudget(max_chars=1000,estimated_max_tokens=334,max_blocks=4))


def all_claims(chapter_book,force=False):
    ai,chapters,ingest=chapter_book
    generation,tasks=prepare(chapter_book,force)
    for task in tasks: submit(ai,task,claim_result(task))
    return generation,chapters.prepare_atoms(ingest.manifest.book.slug,'ch_0001')


def test_multichunk_publication_and_runtime_independence(chapter_book):
    ai,chapters,ingest=chapter_book
    generation,tasks=prepare(chapter_book)
    assert len(tasks)>=3
    assert len({t.context.context_hash for t in tasks})==len(tasks)
    submit(ai,tasks[0],claim_result(tasks[0]))
    _,remaining=prepare(chapter_book)
    assert {t.task_id for t in remaining}=={t.task_id for t in tasks[1:]}
    assert storage.current(ingest.library_path,'ch_0001') is None
    with pytest.raises(ProtocolError,match='CLAIMS_INCOMPLETE'):
        chapters.prepare_atoms(ingest.manifest.book.slug,'ch_0001')
    for task in remaining: submit(ai,task,claim_result(task))
    atom=chapters.prepare_atoms(ingest.manifest.book.slug,'ch_0001')
    assert atom.context.blocks==[] and len(atom.context.claims)==len(tasks)
    path=submit(ai,atom,atom_result(atom))
    assert path.is_symlink()
    info=json.loads((path/'chapter.json').read_text())
    assert info['metrics']['claims']==len(tasks) and info['metrics']['atoms']==1
    assert info['metrics']['claims_with_evidence']==len(tasks)
    assert info['metrics']['assigned_claims']==len(tasks)
    assert ai.library.database.task_status(atom.task_id)=='completed'
    # Retry does not rebuild accepted tasks or publish a new generation.
    target=path.resolve()
    submit(ai,atom,atom_result(atom))
    assert path.resolve()==target
    import shutil
    shutil.rmtree(ingest.library_path/'runtime')
    from book_distiller.pipeline.canonical import load_canonical
    doc=load_canonical(ingest.library_path,ingest.manifest,ai.library.database)
    assert chapters.describe(ingest.library_path,doc)['Atoms']=='1'


@pytest.mark.parametrize('change',[
    {'evidence':[]},{'evidence':[{'block_id':'blk_999999'}]},
    {'confidence':1.1},{'importance':-1.0},{'claim_type':'mental_model'},
    {'source_type':'external'},{'evidence':[{'block_id':'blk_000001'},{'block_id':'blk_000001'}]},
])
def test_claim_invalid_and_retry(chapter_book,change):
    ai,_,ingest=chapter_book
    _,tasks=prepare(chapter_book);task=tasks[0]
    good=claim_result(task);bad=good|{'claims':[good['claims'][0]|change]}
    with pytest.raises(ProtocolError): submit(ai,task,bad)
    assert ai.library.database.task_status(task.task_id)=='pending'
    assert storage.current(ingest.library_path,'ch_0001') is None
    submit(ai,task,good)


def test_context_only_evidence_rejected(chapter_book):
    ai,_,_=chapter_book
    _,tasks=prepare(chapter_book);task=tasks[1]
    assert task.context.chunk.context_blocks
    result=claim_result(task)
    result['claims'][0]['evidence']=[{'block_id':task.context.chunk.context_blocks[0]}]
    with pytest.raises(ProtocolError,match='RESULT_EVIDENCE_INVALID'): submit(ai,task,result)


@pytest.mark.parametrize('change',[
    {'claim_ids':[]},{'claim_ids':['claim_ch_9999_0001_001']},
    {'importance':2.0},{'confidence':-1.0},{'atom_type':'core_idea'},
])
def test_atom_invalid_and_retry(chapter_book,change):
    ai,_,_=chapter_book
    _,task=all_claims(chapter_book)
    good=atom_result(task);bad=good|{'atoms':[good['atoms'][0]|change]}
    with pytest.raises(ProtocolError): submit(ai,task,bad)
    assert ai.library.database.task_status(task.task_id)=='pending'
    submit(ai,task,good)


def test_duplicate_atom_claim_ids(chapter_book):
    ai,_,_=chapter_book
    _,task=all_claims(chapter_book)
    result=atom_result(task)
    result['atoms'][0]['claim_ids']*=2
    with pytest.raises(ProtocolError,match='RESULT_SCHEMA_INVALID'): submit(ai,task,result)


def test_zero_claims_zero_atoms(chapter_book):
    ai,chapters,ingest=chapter_book
    _,tasks=prepare(chapter_book)
    for t in tasks: submit(ai,t,claim_result(t,[]))
    atom=chapters.prepare_atoms(ingest.manifest.book.slug,'ch_0001')
    path=submit(ai,atom,atom_result(atom,[]))
    info=json.loads((path/'chapter.json').read_text())
    assert info['metrics']['claims']==info['metrics']['atoms']==0
    assert 'NO_CLAIMS_EXTRACTED' in info['warnings']


@pytest.mark.parametrize('stage',['claims','atoms'])
def test_stale_parse(chapter_book,isolated_storage,stage):
    ai,_,ingest=chapter_book
    if stage=='claims':
        _,tasks=prepare(chapter_book);task=tasks[0];result=claim_result(task)
    else:
        _,task=all_claims(chapter_book);result=atom_result(task)
    ParseService(isolated_storage).parse(ingest.manifest.book.slug,force=True)
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'): submit(ai,task,result)


def test_atomic_publish_failure_preserves_old_generation(chapter_book,monkeypatch):
    ai,_,ingest=chapter_book
    _,old=all_claims(chapter_book)
    current=submit(ai,old,atom_result(old));target=current.resolve()
    before={p.name:p.read_bytes() for p in target.iterdir()}
    _,new=all_claims(chapter_book,force=True)
    publish=storage.publish
    def fail(*args): raise OSError('injected publish failure')
    monkeypatch.setattr(storage,'publish',fail)
    with pytest.raises(StorageError,match='injected publish failure'): submit(ai,new,atom_result(new))
    assert current.resolve()==target
    assert {p.name:p.read_bytes() for p in target.iterdir()}==before
    assert ai.library.database.task_status(new.task_id)=='pending'
    monkeypatch.setattr(storage,'publish',publish)
    submit(ai,new,atom_result(new))
    assert current.resolve()!=target


def test_classification_required_and_unknown_chapter(chapter_book):
    ai,chapters,ingest=chapter_book
    with pytest.raises(ProtocolError,match='CHAPTER_NOT_FOUND'):
        chapters.prepare_claims(ingest.manifest.book.slug,'../outside')
    (ingest.library_path/'analysis/classification.json').unlink()
    with pytest.raises(ProtocolError,match='CLASSIFICATION_REQUIRED'): prepare(chapter_book)


def test_changed_classification_is_stale(chapter_book):
    ai,_,ingest=chapter_book
    _,tasks=prepare(chapter_book)
    path=ingest.library_path/'analysis/classification.json'
    value=json.loads(path.read_text());value['confidence']=0.7;path.write_text(json.dumps(value))
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'): submit(ai,tasks[0],claim_result(tasks[0]))


def test_changed_canonical_claims_are_stale(chapter_book):
    ai,chapters,ingest=chapter_book
    generation,task=all_claims(chapter_book)
    path=chapters.pending_path(ingest.library_path,generation)/'claims.jsonl'
    path.write_text(path.read_text().replace('A complete','An altered'))
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'): submit(ai,task,atom_result(task))


def test_commit_failure_after_pointer_switch_rolls_back(chapter_book,monkeypatch):
    from contextlib import contextmanager
    ai,_,_=chapter_book
    _,first=all_claims(chapter_book)
    pointer=submit(ai,first,atom_result(first));old=pointer.resolve()
    _,second=all_claims(chapter_book,force=True)
    transaction=ai.library.database.transaction
    @contextmanager
    def fail_commit():
        with transaction() as connection:
            yield connection
            raise OSError('injected commit failure after switch')
    monkeypatch.setattr(ai.library.database,'transaction',fail_commit)
    with pytest.raises(StorageError,match='injected commit failure'): submit(ai,second,atom_result(second))
    assert pointer.resolve()==old
    assert ai.library.database.task_status(second.task_id)=='pending'


def test_cli_chapter_prepare(chapter_book):
    from typer.testing import CliRunner
    from book_distiller.cli.main import app
    _,_,ingest=chapter_book
    result=CliRunner().invoke(app,['analyze','claims',ingest.manifest.book.slug,'--chapter','ch_0001'])
    assert result.exit_code==0,result.output
    assert 'Generation:' in result.output and 'Task directory:' in result.output
    result=CliRunner().invoke(app,['analyze','atoms',ingest.manifest.book.slug,'--chapter','ch_0001'])
    assert result.exit_code==1 and 'CLAIMS_INCOMPLETE' in result.output


def test_overlays_and_schema_snapshots():
    from book_distiller.pipeline.workflows import load_workflow
    from book_distiller.core.models.knowledge import AtomicClaim,KnowledgeAtom,ClaimResult,AtomResult
    workflow=load_workflow(PROJECT,'extract_claims',['investment','philosophy','business'])
    assert [p.parent.name for p in workflow.overlay_paths]==['investment','philosophy','business']
    assert workflow.prompt_text().count('Primary type emphasis')==1
    assert workflow.prompt_text().count('Secondary type hint')==2
    technical=load_workflow(PROJECT,'extract_claims',['technical','investment'])
    assert 'Primary type emphasis' not in technical.prompt_text()
    assert 'Secondary type hint' in technical.prompt_text()
    for filename,model in [('atomic-claim',AtomicClaim),('knowledge-atom',KnowledgeAtom),('extract-claims-result',ClaimResult),('chapter-atoms-result',AtomResult)]:
        assert json.loads((PROJECT/f'schemas/types/{filename}.schema.json').read_text())==model.model_json_schema()


def test_atom_budget_limit_keeps_every_claim_and_prior_generation(chapter_book):
    from book_distiller.core.errors import ValidationError
    ai,chapters,ingest=chapter_book
    _,old=all_claims(chapter_book)
    pointer=submit(ai,old,atom_result(old));previous=pointer.resolve()
    generation,tasks=prepare(chapter_book,force=True)
    count=0
    for task in tasks:
        draft=claim_result(task)['claims'][0]
        claims=[draft|{'statement':f'Case {i}: '+('original source condition '*45)} for i in range(10)]
        submit(ai,task,claim_result(task,claims));count+=len(claims)
    candidate=chapters.pending_path(ingest.library_path,generation)/'claims.jsonl'
    before=candidate.read_bytes()
    assert len(before.splitlines())==count
    with pytest.raises(ValidationError,match='CHAPTER_CONTEXT_TOO_LARGE'):
        chapters.prepare_atoms(ingest.manifest.book.slug,'ch_0001')
    assert candidate.read_bytes()==before
    assert pointer.resolve()==previous
    assert chapters.load_generation(ingest.library_path,generation.generation_id).atom_task is None


def test_claim_rejects_existing_block_from_other_chunk(chapter_book):
    ai,_,_=chapter_book
    _,tasks=prepare(chapter_book)
    result=claim_result(tasks[0])
    result['claims'][0]['evidence']=[{'block_id':tasks[-1].context.chunk.primary_blocks[-1]}]
    with pytest.raises(ProtocolError,match='RESULT_EVIDENCE_INVALID'):
        submit(ai,tasks[0],result)


def test_claim_rejects_foreign_chapter_even_when_in_context(chapter_book):
    from book_distiller.pipeline.knowledge_validation import validate_knowledge
    from book_distiller.core.models.knowledge import ClaimResult
    ai,_,_=chapter_book
    _,tasks=prepare(chapter_book);task=tasks[0]
    result=ClaimResult.model_validate(claim_result(task))
    context=task.context.model_copy(deep=True)
    context.blocks[0].chapter_id='ch_9999'
    with pytest.raises(ProtocolError,match='RESULT_EVIDENCE_INVALID'):
        validate_knowledge(result,context)


def test_no_raw_or_source_read_for_knowledge(chapter_book,monkeypatch):
    ai,_,_=chapter_book
    original_open=Path.open
    def guarded(path,*args,**kwargs):
        if 'raw' in path.parts or 'source' in path.parts:
            pytest.fail(f'Knowledge workflow opened non-Canonical content: {path}')
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    _,task=all_claims(chapter_book)
    submit(ai,task,atom_result(task))
