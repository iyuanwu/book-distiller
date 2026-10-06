"""Protocol test doubles, separate from the real Codex Ask acceptance smoke."""
from pathlib import Path
from copy import deepcopy
import json
import pytest
from test_chapter_tasks import chapter_book, submit
from test_book_tasks import ready
from test_verification_tasks import verification_book, complete
from book_distiller.ask.service import AskService
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.pipeline.files import write_json
from book_distiller.human.service import HumanService
from book_distiller.human.models import HumanAction, HumanRule, UserNote
from book_distiller.human.resolver import EffectiveKnowledgeResolver


@pytest.fixture
def ask_book(verification_book):
    verifier, ingest = verification_book
    complete(verifier,ingest)
    return AskService(verifier.ai), ingest


def answer(task, insufficient=False):
    c = task.context; p=c.payload
    claims=[o for o in p['objects'] if o['ref']['object_type']=='atomic_claim']
    obj = claims[0] if claims else None
    assessments={a['object_ref']['object_id']:a for a in p['assessments']}
    allowed={v['citation_id'] for v in p['citations']}
    refs=[] if insufficient or not obj else [obj['ref']['object_id']]
    citations=[] if not refs else [v for v in assessments[refs[0]]['citation_ids'] if v in allowed][:1]
    insufficient = insufficient or not citations
    segment={'layer':'ai' if insufficient else 'source','text':'Insufficient book evidence.' if insufficient else 'Protocol fixture supported statement.',
             'knowledge_refs':refs,'citation_refs':citations,'human_note_refs':[]}
    data=dict(task_id=str(c.task_id),book_id=str(c.book_id),edition_id=str(c.edition_id),context_hash=c.context_hash,
        source_sha256=c.source_sha256,normalized_schema_version=c.normalized_document.schema_version,
        normalized_document_hash=c.normalized_document.document_hash,workflow_version=c.workflow_version,
        prompt_version=c.prompt_version,context_package_version=c.package_version,source_parse_quality=c.source_parse_quality,generated_by={'engine':'codex'})
    data.update({k:c.scope[k] for k in ('book_generation_id','verification_generation_id','human_state_hash','question_hash','classification_hash','applicable_rule_hash')})
    return data|dict(answer_id=str(task.task_id),question=c.scope['question'],answer=segment['text'],answer_type=p['intent'],segments=[segment],
                     knowledge_refs=refs,citation_refs=citations,human_note_refs=[],quality_warnings=p['quality_warnings'],confidence='low' if insufficient else 'medium',insufficient_evidence=insufficient)


def prepare(service,ingest,question='Explain atomic writes'): return service.prepare(ingest.manifest.book.slug,question)


def test_prepare_submit_retry_readonly(ask_book):
    service,ingest=ask_book;root=ingest.library_path
    before={str(p):p.read_bytes() for folder in ('knowledge','verification') for p in (root/folder).rglob('*') if p.is_file()}
    task=prepare(service,ingest); assert task.context.package_version=='1.4'
    assert task.context.payload['objects'] and task.context.payload['citations']
    assert task.context.budget.selected_chars<=40000
    result=answer(task);path=submit(service.ai,task,result)
    assert path.parent==root/'ask/answers' and path.exists()
    assert submit(service.ai,task,result)==path
    assert len((root/'ask/history.jsonl').read_text().splitlines())==1
    assert before=={str(p):p.read_bytes() for folder in ('knowledge','verification') for p in (root/folder).rglob('*') if p.is_file()}


@pytest.mark.parametrize('bad',['schema','citation','object','warning','high','source_user'])
def test_rejection_retry(ask_book,bad):
    service,ingest=ask_book;t=prepare(service,ingest);value=answer(t)
    if bad=='schema':value['hidden_reasoning']='forbidden'
    elif bad=='citation':value['citation_refs']=value['segments'][0]['citation_refs']=['cit_'+'0'*32]
    elif bad=='object':value['knowledge_refs']=value['segments'][0]['knowledge_refs']=['unknown']
    elif bad=='warning':value['quality_warnings']=[]
    elif bad=='high':value['confidence']='high';value['insufficient_evidence']=True
    else:value['segments'][0]['human_note_refs']=['00000000-0000-0000-0000-000000000001'];value['human_note_refs']=value['segments'][0]['human_note_refs']
    with pytest.raises(ProtocolError):submit(service.ai,t,value)
    assert service.ai.library.database.lookup_task(t.task_id)['status']=='pending'
    assert not (ingest.library_path/'ask/answers'/f'{t.task_id}.json').exists()
    submit(service.ai,t,answer(t))


@pytest.mark.parametrize('change',['human','rule','context'])
def test_stale_task(ask_book,change):
    service,ingest=ask_book;t=prepare(service,ingest);h=HumanService(service.ai)
    if change=='human':
        item=next(v for v in EffectiveKnowledgeResolver(ingest.library_path).inventory().values() if v['target_ref']['object_type']=='atomic_claim')
        h.apply(ingest.manifest.book.slug,HumanAction(action='verify',target_ref=item['target_ref'],base_object_hash=item['base_object_hash']))
    elif change=='rule':h.rule(ingest.manifest.book.slug,HumanRule(scope='book',applicable_workflows=['ask_book'],instruction='Be concise.'))
    else:(t.directory/'context.md').write_text('tampered')
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'):submit(service.ai,t,answer(t))


def test_stale_semantic_edit_blocks(ask_book):
    service,ingest=ask_book
    item=next(v for v in EffectiveKnowledgeResolver(ingest.library_path).inventory().values() if v['target_ref']['object_type']=='atomic_claim')
    HumanService(service.ai).apply(ingest.manifest.book.slug,HumanAction(action='edit',target_ref=item['target_ref'],base_object_hash=item['base_object_hash'],patch={'statement':'Changed local statement.'}))
    with pytest.raises(ProtocolError,match='STALE'):prepare(service,ingest)
    assert service.describe(ingest.manifest.book.slug)['Ask']=='blocked'


def test_unknown_and_outside_questions(ask_book):
    service,ingest=ask_book
    for question in ['Does the book recommend AES-256 as default publication format?','今天苹果股票多少钱？']:
        t=prepare(service,ingest,question);value=answer(t,True)
        value['out_of_scope']='苹果' in question
        path=submit(service.ai,t,value)
        assert json.loads(path.read_text())['insufficient_evidence']


def test_publish_failure_and_history_repair(ask_book,monkeypatch):
    service,ingest=ask_book;t=prepare(service,ingest)
    original=service.ai._complete
    def fail(*args):raise OSError('injected commit failure')
    monkeypatch.setattr(service.ai,'_complete',fail)
    with pytest.raises(Exception,match='injected'):submit(service.ai,t,answer(t))
    assert not (ingest.library_path/'ask/answers'/f'{t.task_id}.json').exists()
    assert service.ai.library.database.lookup_task(t.task_id)['status']=='pending'
    monkeypatch.setattr(service.ai,'_complete',original)
    submit(service.ai,t,answer(t))
    (ingest.library_path/'ask/history.jsonl').unlink()
    submit(service.ai,t,answer(t))
    assert len((ingest.library_path/'ask/history.jsonl').read_text().splitlines())==1


@pytest.mark.parametrize('change',['book','verification'])
def test_generation_change_rejects(ask_book,change):
    from test_book_tasks import finish
    from book_distiller.pipeline.book_tasks import BookTasks
    from book_distiller.pipeline.verification_tasks import VerificationTasks
    service,ingest=ask_book;t=prepare(service,ingest)
    if change=='book':finish(BookTasks(service.ai),ingest,True)
    else:complete(VerificationTasks(service.ai),ingest,True)
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'):submit(service.ai,t,answer(t))


@pytest.mark.parametrize('corrupt',['text_hash','source_span','failed_gate'])
def test_corrupt_evidence_and_failed_gate(ask_book,corrupt):
    from book_distiller.storage.verification import current
    from book_distiller.storage.filesystem import hash_source
    service,ingest=ask_book;t=prepare(service,ingest);root=current(ingest.library_path)
    if corrupt=='failed_gate':
        p=root/'quality_report.json';v=json.loads(p.read_text());v['status']='failed';write_json(p,v)
    else:
        p=root/'citations.jsonl';rows=[json.loads(l) for l in p.read_text().splitlines()]
        selected={c['citation_id'] for c in t.context.payload['citations']}
        for row in rows:
            if row['citation_id'] in selected:
                if corrupt=='text_hash':row['text_hash']='0'*64
                else:row['source_spans'][0]['parser_locator']='fabricated'
                break
        p.write_text(''.join(json.dumps(r)+'\n' for r in rows))
    manifest=json.loads((root/'manifest.json').read_text());manifest['artifact_hashes'][p.name]=hash_source(p)[0];write_json(root/'manifest.json',manifest)
    assert service.describe(ingest.manifest.book.slug)['Ask']=='blocked' if corrupt=='failed_gate' else True
    with pytest.raises(ProtocolError):prepare(service,ingest)
    with pytest.raises(ProtocolError,match='STALE_CONTEXT'):submit(service.ai,t,answer(t))


def test_citation_exists_but_outside_context(ask_book):
    from book_distiller.storage.verification import current
    service,ingest=ask_book;t=prepare(service,ingest,'unfindableXYZ')
    assert not t.context.payload['citations']
    citation=json.loads((current(ingest.library_path)/'citations.jsonl').read_text().splitlines()[0])['citation_id']
    value=answer(t,True);value['citation_refs']=value['segments'][0]['citation_refs']=[citation]
    with pytest.raises(ProtocolError,match='BROKEN_CITATION'):submit(service.ai,t,value)


def test_note_rules_and_cli(ask_book):
    from typer.testing import CliRunner
    from book_distiller.cli.main import app
    service,ingest=ask_book;h=HumanService(service.ai);slug=ingest.manifest.book.slug
    item=next(v for v in EffectiveKnowledgeResolver(ingest.library_path).inventory().values() if v['target_ref']['object_type']=='core_idea')
    note=h.note(slug,UserNote(target_ref=item['target_ref'],text='这个观点值得回看第3章。'))
    for scope in ('global','type','book'):
        h.rule(slug,HumanRule(scope=scope,book_type='technical' if scope=='type' else None,applicable_workflows=['ask_book'],instruction=scope+' concise'))
    t=prepare(service,ingest,'我给 idea_001 写过什么备注？');p=t.context.payload
    assert note in p['user_notes']
    assert [r['scope'] for r in t.context.human_guidance['rules']]==['global','type','book']
    value=answer(t,True);segment=dict(layer='user',text='User Note: 这个观点值得回看第3章。这是用户笔记，不是作者观点。',knowledge_refs=[],citation_refs=[],human_note_refs=[note['note_id']])
    value.update(answer=segment['text'],segments=[segment],knowledge_refs=[],citation_refs=[],human_note_refs=[note['note_id']],insufficient_evidence=False)
    path=submit(service.ai,t,value)
    runner=CliRunner();env={'BOOK_DISTILLER_HOME':str(service.ai.library.files.root)}
    result=runner.invoke(app,['ask','show',slug,str(t.task_id)],env=env)
    assert result.exit_code==0 and 'User Note' in result.output, result.output
    status=runner.invoke(app,['status',slug],env=env)
    assert status.exit_code==0 and 'Ask' in status.output and 'ready' in status.output
    prepared=runner.invoke(app,['ask','prepare',slug,'Explain atomic writes'],env=env)
    assert prepared.exit_code==0 and 'Task:' in prepared.output
    path.unlink()
    with pytest.raises(ProtocolError,match='ANSWER_MISSING'):service.finish(ingest.library_path,t.task_id)


def test_metadata_does_not_raise_confidence(ask_book,monkeypatch):
    service,ingest=ask_book;original=service.inventory
    def partial(*args):
        objects,assessments=original(*args)
        for o in objects.values():o['human']={'human_modified':False,'human_verified':True,'locked':True}
        for a in assessments.values():a.update(fidelity_verdict='partially_supported',evidence_strength='weak')
        return objects,assessments
    monkeypatch.setattr(AskService,'inventory',staticmethod(partial))
    t=prepare(service,ingest)
    assert any('partially_supported' in w for w in t.context.payload['quality_warnings'])
    v=answer(t);v['confidence']='high'
    with pytest.raises(ProtocolError,match='CONFIDENCE_EXCEEDS_EVIDENCE'):submit(service.ai,t,v)
    v['confidence']='low';submit(service.ai,t,v)


def test_application_is_ai(ask_book):
    service,ingest=ask_book;t=prepare(service,ingest,'How can I apply atomic writes to my company?');v=answer(t)
    assert t.context.payload['intent']=='application'
    with pytest.raises(ProtocolError,match='APPLICATION_LAYER'):submit(service.ai,t,v)
    v['segments'][0]['layer']='ai';submit(service.ai,t,v)


def test_history_publication_failure_recovery(ask_book,monkeypatch):
    import book_distiller.ask.service as module
    service,ingest=ask_book;t=prepare(service,ingest);original=module.append_event
    def fail(*args):raise OSError('history metadata failure')
    monkeypatch.setattr(module,'append_event',fail)
    with pytest.raises(Exception,match='history metadata'):submit(service.ai,t,answer(t))
    path=ingest.library_path/'ask/answers'/f'{t.task_id}.json';before=path.read_bytes()
    assert service.ai.library.database.lookup_task(t.task_id)['status']=='completed'
    monkeypatch.setattr(module,'append_event',original)
    assert service.finish(ingest.library_path,t.task_id)==path
    assert path.read_bytes()==before
    assert len((ingest.library_path/'ask/history.jsonl').read_text().splitlines())==1


from test_phase8_extended import high_book


def test_effective_override_reverification_and_full_expansion(high_book):
    from book_distiller.pipeline.verification_tasks import VerificationTasks
    from book_distiller.ask.retrieval import AskRetriever
    from book_distiller.pipeline.canonical import load_canonical
    from book_distiller.storage.verification import current
    books,ingest=high_book;v=VerificationTasks(books.ai);complete(v,ingest);service=AskService(books.ai)
    resolver=EffectiveKnowledgeResolver(ingest.library_path)
    item=next(o for o in resolver.inventory().values() if o['target_ref']['object_type']=='meta_principle')
    original=item['base']['statement']
    HumanService(books.ai).apply(ingest.manifest.book.slug,HumanAction(action='edit',target_ref=item['target_ref'],base_object_hash=item['base_object_hash'],patch={'statement':'Human effective publication boundary.'}))
    with pytest.raises(ProtocolError,match='STALE'):prepare(service,ingest)
    complete(v,ingest,True)
    assert service.describe(ingest.manifest.book.slug)['Ask'].startswith('ready')
    t=prepare(service,ingest,'Explain principle_001 publication')
    found=next(o for o in t.context.payload['objects'] if o['ref']['object_id']=='principle_001')
    assert found['value']['statement']=='Human effective publication boundary.' and found['human']['human_modified']
    assert EffectiveKnowledgeResolver(ingest.library_path).show(item['target_ref'])['base']['statement']==original
    doc=load_canonical(ingest.library_path,ingest.manifest,books.ai.library.database)
    objects,assessments=service.inventory(EffectiveKnowledgeResolver(ingest.library_path),doc,current(ingest.library_path))
    retriever=AskRetriever(objects,assessments)
    for key in ('concept_atomic_writes','idea_001','model_001','principle_001'):
        chain=retriever.expand(key)
        assert key in chain and any(objects[k]['ref']['object_type']=='atomic_claim' for k in chain)
        assert any(assessments.get(k,{}).get('citation_ids') for k in chain)
    assert any(objects[k]['ref']['object_type']=='mental_model' for k in retriever.expand('principle_001'))


def test_large_deterministic_budget(ask_book,monkeypatch):
    import tracemalloc,time
    from uuid import uuid4
    from book_distiller.pipeline.canonical import load_canonical
    from book_distiller.pipeline.workflows import load_workflow
    from book_distiller.storage.verification import current
    from book_distiller.ask.retrieval import AskRetriever
    service,ingest=ask_book;root=ingest.library_path
    doc=load_canonical(root,ingest.manifest,service.ai.library.database)
    base,assessments=service.inventory(EffectiveKnowledgeResolver(root),doc,current(root))
    claim=next(o for o in base.values() if o['ref']['object_type']=='atomic_claim')
    assess=assessments[claim['ref']['object_id']]
    tracemalloc.start();objects={};evaluations={}
    for i in range(5000):
        key=f'claim_scale_{i:05d}';o=deepcopy(claim);o['ref']['object_id']=key;o['value']['statement']=f'Publication candidate metadata {i}';objects[key]=o
        a=deepcopy(assess);a['object_ref']['object_id']=key;evaluations[key]=a
    for i in range(1000):
        key=f'atom_scale_{i:05d}';o=deepcopy(claim);o['ref'].update(object_id=key,object_type='knowledge_atom');o['value']={'title':f'Publication stage {i}'}
        o['lower_ids']=[f'claim_scale_{j:05d}' for j in range(i*5,i*5+5)];objects[key]=o
        a=deepcopy(assess);a['object_ref'].update(object_id=key,object_type='knowledge_atom');evaluations[key]=a
    for i in range(200):
        key=f'concept_scale_{i:05d}';o=deepcopy(claim);o['ref'].update(object_id=key,object_type='concept');o['value']={'canonical_name':f'Publication pattern {i}','aliases':[f'发布模式{i}']}
        o['lower_ids']=[f'atom_scale_{i:05d}'];objects[key]=o
    retriever=AskRetriever(objects,evaluations);start=time.perf_counter();first=retriever.retrieve('publication');elapsed=time.perf_counter()-start
    assert first==retriever.retrieve('publication')
    monkeypatch.setattr(AskService,'inventory',staticmethod(lambda *args:(objects,evaluations)))
    task_id=uuid4();workflow=load_workflow(service.ai.project,'ask_book')
    context=service.build(root,doc,task_id,workflow,'publication');again=service.build(root,doc,task_id,workflow,'publication')
    peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
    assert context==again and context.context_hash==again.context_hash
    assert context.budget.selected_chars<=40000 and context.budget.estimated_tokens<=context.budget.limits.estimated_max_tokens
    selected={o['ref']['object_id'] for o in context.payload['objects']}
    assert 0<len(selected)<=48 and len(first.ranked_ids)==6200
    assert all(set(o['lower_ids'])<=selected for o in context.payload['objects'])
    assert context.payload['retrieval']['omitted_candidates']>6000
    metrics={'objects':len(objects),'retrieval_seconds':elapsed,'candidate_count':len(first.ranked_ids),'selected_count':len(selected),
        'context_chars':context.budget.selected_chars,'estimated_tokens':context.budget.estimated_tokens,'peak_python_bytes':peak}
    print('ASK_PERFORMANCE '+json.dumps(metrics))
    Path('/tmp/phase9a-performance.json').write_text(json.dumps(metrics))


def test_kill_after_answer_before_commit_recovers(ask_book,tmp_path):
    import subprocess,sys
    service,ingest=ask_book;t=prepare(service,ingest);result=t.directory/'result.json';write_json(result,answer(t))
    script=tmp_path/'kill_ask.py'
    script.write_text('''import os,signal,sys
from pathlib import Path
from uuid import UUID
from book_distiller.pipeline.ai_tasks import AITaskService
ai=AITaskService(Path(sys.argv[1]),Path(sys.argv[2]))
ai._complete=lambda *args: os.kill(os.getpid(),signal.SIGKILL)
ai.submit(UUID(sys.argv[3]),Path(sys.argv[4]))
''')
    import os
    proc=subprocess.run([sys.executable,str(script),str(service.ai.library.files.root),str(service.ai.project),str(t.task_id),str(result)],
                        env=os.environ|{'PYTHONPATH':str(service.ai.project)},capture_output=True)
    assert proc.returncode==-9,proc.stderr
    assert service.ai.library.database.lookup_task(t.task_id)['status']=='pending'
    with pytest.raises(ProtocolError,match='NOT_COMPLETED'):service.finish(ingest.library_path,t.task_id)
    assert not (ingest.library_path/'ask/history.jsonl').exists()
    path=service.ai.submit(t.task_id,result)
    assert path.exists() and service.ai.library.database.lookup_task(t.task_id)['status']=='completed'
    assert len((ingest.library_path/'ask/history.jsonl').read_text().splitlines())==1


def test_answer_type_cannot_bypass_application(ask_book):
    service,ingest=ask_book;t=prepare(service,ingest,'Apply atomic writes to my company');v=answer(t);v['answer_type']='fact'
    with pytest.raises(ProtocolError,match='ANSWER_INTENT_MISMATCH'):submit(service.ai,t,v)


def test_major_omission_and_unsupported_review(verification_book):
    from test_verification_tasks import result as review_result
    verifier,ingest=verification_book;t=verifier.prepare(ingest.manifest.book.slug);bad=None;omission=False
    while not isinstance(t,Path):
        payload=t.context.payload
        if t.context.task_type=='verify_claim' and bad is None:bad=payload['target']['ref']['object_id']
        if payload.get('target') and payload['target']['ref']['object_id']==bad:
            v=review_result(t,'unsupported','insufficient',['unsupported'])
        else:v=review_result(t)
        if t.context.task_type=='review_chapter_coverage' and not omission:
            v['issues']=[dict(issue_type='MAJOR_OMISSION',severity='major',object_refs=[],source_refs=[payload['citations'][0]['citation_id']],
                chapter_id=payload['chapter_ids'][0],summary='Protocol fixture omitted an important source limitation.',suggested_action='inspect_source',reviewer_confidence=.9)]
            omission=True
        submit(verifier.ai,t,v);t=verifier.prepare(ingest.manifest.book.slug)
    service=AskService(verifier.ai);normal=prepare(service,ingest)
    assert bad not in {o['ref']['object_id'] for o in normal.context.payload['objects']}
    assert any(i['issue_type']=='MAJOR_OMISSION' for i in normal.context.payload['quality_issues'])
    assert any('NEEDS_REVIEW' in w for w in normal.context.payload['quality_warnings'])
    v=answer(normal);v['confidence']='high'
    with pytest.raises(ProtocolError,match='CONFIDENCE_EXCEEDS_EVIDENCE'):submit(service.ai,normal,v)
    v['confidence']='low';submit(service.ai,normal,v)
    problem=prepare(service,ingest,'哪些观点不受支持？')
    assert bad in {o['ref']['object_id'] for o in problem.context.payload['objects']}
    v=answer(problem);v['knowledge_refs']=v['segments'][0]['knowledge_refs']=[bad]
    a=next(a for a in problem.context.payload['assessments'] if a['object_ref']['object_id']==bad)
    v['citation_refs']=v['segments'][0]['citation_refs']=a['citation_ids'][:1]
    with pytest.raises(ProtocolError,match='INSUFFICIENT_EVIDENCE'):submit(service.ai,problem,v)
