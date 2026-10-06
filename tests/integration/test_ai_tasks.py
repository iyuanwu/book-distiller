"""Protocol tests: no model client, network, or private book inputs."""
from pathlib import Path
import json
from uuid import UUID, uuid4
import pytest
from typer.testing import CliRunner
from book_distiller.cli.main import app
from book_distiller.core.ingest import IngestService
from book_distiller.core.parse import ParseService
from book_distiller.core.errors import StorageError
from book_distiller.core.models.ai_tasks import BookClassification
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.pipeline.result_validation import ProtocolError

PROJECT=Path(__file__).parents[2]


@pytest.fixture
def ai_book(isolated_storage):
    ingest=IngestService(isolated_storage).ingest(PROJECT/'tests/fixtures/sample.txt')
    ParseService(isolated_storage).parse(ingest.manifest.book.slug)
    return AITaskService(isolated_storage,PROJECT),ingest


def valid_result(prepared):
    c=prepared.context
    return {'schema_version':'1.0','task_id':str(prepared.task_id),'book_id':str(c.book_id),'edition_id':str(c.edition_id),
        'context_hash':c.context_hash,'source_sha256':c.source_sha256,'normalized_schema_version':c.normalized_document.schema_version,
        'normalized_document_hash':c.normalized_document.document_hash,'workflow_version':c.workflow_version,
        'prompt_version':c.prompt_version,'context_package_version':c.package_version,'source_parse_quality':c.source_parse_quality,
        'primary_type':'other','secondary_types':[],'tags':['reading'],'confidence':0.6,
        'evidence':[{'block_id':c.blocks[0].block_id,'supports':['other'],'note':'An original reading exercise.'}],
        'rationale_summary':'A short fixture provides insufficient material for a more specific book type.', 'generated_by':{'engine':'codex'}}


def write_result(prepared, value):
    path=prepared.directory/'result.json'
    path.write_text(json.dumps(value),encoding='utf-8')
    return path


def test_prepare_submit_and_runtime_independence(ai_book):
    service,ingest=ai_book
    prepared=service.prepare('classify',ingest.manifest.book.slug)
    for name in ['request.json','context.json','context.md','output.schema.json','workflow.md','prompt.md']:
        assert (prepared.directory/name).is_file()
    assert service.library.database.task_status(prepared.task_id)=='pending'
    assert json.loads((prepared.directory/'output.schema.json').read_text())==BookClassification.model_json_schema()
    assert not (ingest.library_path/'analysis/classification.json').exists()
    result=valid_result(prepared)
    canonical=service.submit(prepared.task_id,write_result(prepared,result))
    assert canonical==ingest.library_path/'analysis/classification.json'
    assert service.library.database.task_status(prepared.task_id)=='completed'
    saved=BookClassification.model_validate_json(canonical.read_text())
    assert saved.primary_type=='other'
    assert service.submit(prepared.task_id,prepared.directory/'result.json')==canonical
    assert json.loads((prepared.directory/'validation.json').read_text())['status']=='applied'
    import shutil
    shutil.rmtree(prepared.directory)
    assert service.describe(ingest.library_path)['Classification']=='completed'
    with service.library.database.connect() as connection:
        assert connection.execute('SELECT count(*) FROM runs').fetchone()[0]==0
        assert connection.execute('SELECT version FROM schema_meta').fetchone()[0]==2


@pytest.mark.parametrize('change,code',[
    ({'primary_type':'alien'},'RESULT_SCHEMA_INVALID'),
    ({'secondary_types':['business','business']},'RESULT_SCHEMA_INVALID'),
    ({'secondary_types':['other']},'RESULT_SCHEMA_INVALID'),
    ({'tags':['reading','reading']},'RESULT_SCHEMA_INVALID'),
    ({'tags':['Not Kebab']},'RESULT_SCHEMA_INVALID'),
    ({'confidence':1.1},'RESULT_SCHEMA_INVALID'),
    ({'confidence':-0.1},'RESULT_SCHEMA_INVALID'),
    ({'confidence':'0.8'},'RESULT_SCHEMA_INVALID'),
    ({'task_id':str(uuid4())},'RESULT_TASK_MISMATCH'),
    ({'context_hash':'0'*64},'STALE_CONTEXT'),
    ({'source_sha256':'0'*64},'STALE_CONTEXT'),
    ({'normalized_document_hash':'0'*64},'STALE_CONTEXT'),
    ({'evidence':[{'block_id':'blk_999999','supports':['other'],'note':'not present'}]},'RESULT_EVIDENCE_INVALID'),
    ({'private_chain_of_thought':'not permitted'},'RESULT_SCHEMA_INVALID'),
])
def test_invalid_results_rejected_and_retry(ai_book,change,code):
    service,ingest=ai_book
    prepared=service.prepare('classify',ingest.manifest.book.slug)
    result=valid_result(prepared)
    with pytest.raises(ProtocolError,match=code):
        service.submit(prepared.task_id,write_result(prepared,result|change))
    assert service.library.database.task_status(prepared.task_id)=='pending'
    assert not (ingest.library_path/'analysis/classification.json').exists()
    assert json.loads((prepared.directory/'validation.json').read_text())['code']==code
    assert service.submit(prepared.task_id,write_result(prepared,result)).is_file()


def test_stale_after_force_parse(ai_book,isolated_storage):
    service,ingest=ai_book
    prepared=service.prepare('classify',ingest.manifest.book.slug)
    ParseService(isolated_storage).parse(ingest.manifest.book.slug,force=True)
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'):
        service.submit(prepared.task_id,write_result(prepared,valid_result(prepared)))
    assert not (ingest.library_path/'analysis/classification.json').exists()


@pytest.mark.parametrize('file',['context.md','context.json','output.schema.json','workflow.md'])
def test_runtime_tampering_rejected(ai_book,file):
    service,ingest=ai_book
    prepared=service.prepare('classify',ingest.manifest.book.slug)
    (prepared.directory/file).write_text('{}',encoding='utf-8')
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'):
        service.submit(prepared.task_id,write_result(prepared,valid_result(prepared)))


def test_no_raw_or_source_read_for_workflow(ai_book,monkeypatch):
    service,ingest=ai_book
    original_open=Path.open
    def guarded(path,*args,**kwargs):
        if 'raw' in path.parts or 'source' in path.parts:
            pytest.fail(f'AI workflow opened non-Canonical content: {path}')
        return original_open(path,*args,**kwargs)
    monkeypatch.setattr(Path,'open',guarded)
    prepared=service.prepare('classify',ingest.manifest.book.slug)
    service.submit(prepared.task_id,write_result(prepared,valid_result(prepared)))


def test_atomic_apply_failure_restores_prior(ai_book,monkeypatch):
    service,ingest=ai_book
    first=service.prepare('classify',ingest.manifest.book.slug)
    canonical=service.submit(first.task_id,write_result(first,valid_result(first)))
    original=canonical.read_bytes()
    next_task=service.prepare('classify',ingest.manifest.book.slug)
    def fail(*args): raise OSError('Injected commit failure')
    monkeypatch.setattr(service,'_complete',fail)
    with pytest.raises(StorageError,match='Injected commit failure'):
        service.submit(next_task.task_id,write_result(next_task,valid_result(next_task)))
    assert canonical.read_bytes()==original
    assert service.library.database.task_status(next_task.task_id)=='pending'


def test_cli_and_status(ai_book):
    service,ingest=ai_book
    runner=CliRunner()
    result=runner.invoke(app,['workflow','prepare','classify',ingest.manifest.book.slug])
    assert result.exit_code==0,result.output
    task_id=UUID(result.output.split('Task ID: ')[1].splitlines()[0])
    context_path=ingest.library_path/'runtime/tasks'/str(task_id)/'context.json'
    from book_distiller.core.models.ai_tasks import ContextPackage
    from book_distiller.pipeline.ai_tasks import PreparedTask
    prepared=PreparedTask(task_id,context_path.parent,ContextPackage.model_validate_json(context_path.read_text()))
    output=runner.invoke(app,['workflow','submit',str(task_id),'--result',str(write_result(prepared,valid_result(prepared)))])
    assert output.exit_code==0,output.output
    output=runner.invoke(app,['status',ingest.manifest.book.slug])
    assert output.exit_code==0 and 'Classification' in output.output and 'other' in output.output


def test_unparsed_rejected(isolated_storage):
    ingest=IngestService(isolated_storage).ingest(PROJECT/'tests/fixtures/sample.txt')
    with pytest.raises(StorageError,match='NOT_PARSED'):
        AITaskService(isolated_storage,PROJECT).prepare('classify',ingest.manifest.book.slug)


@pytest.mark.parametrize('content',[b'{', b'\xff', b' '*131073])
def test_malformed_result_audited_and_retryable(ai_book,content):
    service,ingest=ai_book
    prepared=service.prepare('classify',ingest.manifest.book.slug)
    path=prepared.directory/'result.json'
    path.write_bytes(content)
    with pytest.raises(ProtocolError,match='RESULT_SCHEMA_INVALID'):
        service.submit(prepared.task_id,path)
    assert service.library.database.task_status(prepared.task_id)=='pending'
    assert json.loads((prepared.directory/'validation.json').read_text())['status']=='rejected'
    service.submit(prepared.task_id,write_result(prepared,valid_result(prepared)))


def test_completed_task_never_replaces_newer_classification(ai_book):
    service,ingest=ai_book
    first=service.prepare('classify',ingest.manifest.book.slug)
    service.submit(first.task_id,write_result(first,valid_result(first)))
    second=service.prepare('classify',ingest.manifest.book.slug)
    canonical=service.submit(second.task_id,write_result(second,valid_result(second)))
    latest=canonical.read_bytes()
    service.submit(first.task_id,first.directory/'result.json')
    assert canonical.read_bytes()==latest
    with pytest.raises(ProtocolError,match='TASK_ALREADY_COMPLETED'):
        service.submit(first.task_id,write_result(first,valid_result(first)|{'confidence':0.7}))
    assert canonical.read_bytes()==latest


def test_interrupted_apply_can_be_resubmitted(ai_book):
    service,ingest=ai_book
    prepared=service.prepare('classify',ingest.manifest.book.slug)
    result=valid_result(prepared)
    # Simulate process termination after atomic canonical replace, before SQLite commit.
    canonical=ingest.library_path/'analysis/classification.json'
    canonical.parent.mkdir()
    canonical.write_text(BookClassification.model_validate(result).model_dump_json())
    assert service.describe(ingest.library_path)['Classification'].startswith('pending apply')
    service.submit(prepared.task_id,write_result(prepared,result))
    assert service.describe(ingest.library_path)['Classification']=='completed'


def test_failed_first_apply_leaves_no_canonical_and_can_retry(ai_book,monkeypatch):
    service,ingest=ai_book
    prepared=service.prepare('classify',ingest.manifest.book.slug)
    complete=service._complete
    def fail(*args): raise OSError('apply interrupted')
    monkeypatch.setattr(service,'_complete',fail)
    with pytest.raises(StorageError,match='apply interrupted'):
        service.submit(prepared.task_id,write_result(prepared,valid_result(prepared)))
    assert not (ingest.library_path/'analysis/classification.json').exists()
    monkeypatch.setattr(service,'_complete',complete)
    service.submit(prepared.task_id,prepared.directory/'result.json')


def test_protocol_files_and_skill_static():
    assert json.loads((PROJECT/'schemas/types/classification.schema.json').read_text())==BookClassification.model_json_schema()
    skill=(PROJECT/'.codex/skills/book-distiller/SKILL.md').read_text()
    assert 'Current implementation phase: Phase 9C' in skill
    assert './book bundle export' in skill and './book bundle inspect' in skill and './book bundle import' in skill
    assert './book ask prepare' in skill and './book ask submit' in skill
    for route in ['workflow prepare classify','workflow submit','output.schema.json','STALE_CONTEXT','classification.json']:
        assert route in skill
    assert 'analyze book' in skill and './book verify <book>' in skill
    assert './book resume <book>' in skill and './book rerun <book>' in skill
    assert 'human apply' in skill and 'STALE_HUMAN_EDIT' in skill
