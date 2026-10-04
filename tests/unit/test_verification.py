import json
from pathlib import Path
from uuid import uuid4
from types import SimpleNamespace
import pytest
from pydantic import ValidationError
from book_distiller.core.models.normalized import NormalizedBlock,SourceSpan
from book_distiller.core.models.verification import AssessmentDraft
from book_distiller.evidence.citations import make_citation,resolve_citation,SourceIndex
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.quality.review import build_report


def ref(kind='atomic_claim',key='claim_1'):
    return dict(object_type=kind,object_id=key,book_id=str(uuid4()),edition_id=str(uuid4()),chapter_id='ch_0001',chapter_generation_id=str(uuid4()))

@pytest.mark.parametrize('verdict,strength,valid',[
 ('supported','strong',True),('supported','moderate',True),('partially_supported','weak',True),('partially_supported','strong',False),
 ('unsupported','strong',False),('contradicted','moderate',False),('unsupported','insufficient',True),('contradicted','insufficient',True)])
def test_verdict_strength_contract(verdict,strength,valid):
    data=dict(object_ref=ref(),citation_ids=['cit_1'],evidence_strength=strength,fidelity_verdict=verdict,reviewer_confidence=.9,verification_summary='Short public reason.')
    if valid:AssessmentDraft(**data)
    else:
        with pytest.raises(ValidationError):AssessmentDraft(**data)

def test_citation_stable_range_generation_pages(tmp_path):
    block=NormalizedBlock(block_id='blk_000001',chapter_id='ch_0001',type='paragraph',text='Source passage.',order=1,
        source_spans=[SourceSpan(block_id='blk_000001',char_start=0,char_end=15,parser='docling',parser_locator='#/texts/0',source_page_index=2,source_page_number=3)])
    path=tmp_path/'blocks.jsonl';path.write_text(block.model_dump_json()+'\n')
    doc=SimpleNamespace(book=SimpleNamespace(book_id=uuid4(),edition_id=uuid4()),fingerprint=SimpleNamespace(parse_task_id=uuid4()),blocks_path=path)
    a=make_citation(doc,block);assert make_citation(doc,block)==a
    assert a.page_number==3 and resolve_citation(doc,a)['text']=='Source passage.'
    assert make_citation(doc,block,start=1).citation_id!=a.citation_id
    doc.fingerprint.parse_task_id=uuid4();assert make_citation(doc,block).citation_id!=a.citation_id
    with pytest.raises(ProtocolError,match='BROKEN_CITATION'):resolve_citation(doc,a)
    with pytest.raises(ProtocolError,match='BROKEN_CITATION'):SourceIndex(path).get('blk_missing')

def test_quality_formulas_and_three_gates():
    rules=json.loads((Path(__file__).parents[2]/'rules/quality/standard.json').read_text())
    objects=[];assessments=[]
    for kind,key,text in [('atomic_claim','c','Claim statement'),('knowledge_atom','a','Atom summary'),('core_idea','i','Core idea')]:
        r=ref(kind,key);objects.append({'ref':r,'value':{'statement':text,'summary':text,'importance':.9,'confidence':.95},'source_blocks':['b']})
        assessments.append({'object_ref':r,'fidelity_verdict':'supported','citation_ids':['cit'],'auto_recheck_attempted':False})
    parse=SimpleNamespace(blocks_with_source_span=10,total_source_relevant_blocks=10,status='pass')
    args=[objects,assessments,[{'resolved':True}],[{'chapter_id':'ch_0001','complete':True}],[],parse,['ch_0001'],rules,{}]
    report,_=build_report(*args);assert report['status']=='pass';assert report['metrics']['fidelity_rate']['numerator']==3
    assessments[0]['fidelity_verdict']='unsupported';report,_=build_report(*args)
    assert report['status']=='needs_review' and report['metrics']['high_confidence_hallucinations']['value']==1
    assert report['metrics']['fidelity_rate']['numerator']==2 and report['metrics']['fidelity_rate']['denominator']==3
    args[2]=[{'resolved':False}];assert build_report(*args)[0]['status']=='failed'


def test_source_index_streaming_batches_and_section_locality(tmp_path):
    import tracemalloc
    path=tmp_path/'blocks.jsonl'
    with path.open('w') as stream:
        for i in range(10000):
            block=NormalizedBlock(block_id=f'blk_{i+1:06d}',chapter_id=f'ch_{i//1000+1:04d}',section_id=f'sec_{i//100+1:04d}',type='paragraph',text='Original load text. '*100,order=i+1,
                source_spans=[SourceSpan(block_id=f'blk_{i+1:06d}',parser='plain_text',char_start=0,char_end=2000)])
            stream.write(block.model_dump_json()+'\n')
    tracemalloc.start();index=SourceIndex(path);peak=tracemalloc.get_traced_memory()[1];tracemalloc.stop()
    assert peak<12*1024*1024 and len(index.entries)==10000
    assert 'blk_000101' not in index.window(['blk_000100'],3)
    assert 'blk_001001' not in index.window(['blk_001000'],3)
    batches=list(index.batches('ch_0001',8,16000));assert sum(map(len,batches))==1000
    assert all(len(b)<=8 and sum(index.entries[k]['chars'] for k in b)<=16000 for b in batches)
    with pytest.raises(ProtocolError,match='VERIFICATION_CONTEXT_TOO_LARGE'):list(index.batches('ch_0001',8,10))
    print(f'source index: blocks=10000 peak_bytes={peak} source_bytes={path.stat().st_size}')


def test_citation_no_physical_page_and_text_hash(tmp_path):
    block=NormalizedBlock(block_id='blk_000001',chapter_id='ch_0001',type='paragraph',text='Markdown source.',order=1,
        source_spans=[SourceSpan(block_id='blk_000001',char_start=0,char_end=16,parser='docling',parser_locator='#/texts/0')])
    path=tmp_path/'blocks.jsonl';path.write_text(block.model_dump_json()+'\n')
    doc=SimpleNamespace(book=SimpleNamespace(book_id=uuid4(),edition_id=uuid4()),fingerprint=SimpleNamespace(parse_task_id=uuid4()),blocks_path=path)
    cit=make_citation(doc,block);assert cit.page_index is None and cit.page_number is None
    assert 'text' not in cit.model_dump()
    bad=cit.model_copy(update={'text_hash':'0'*64})
    with pytest.raises(ProtocolError,match='text hash'):resolve_citation(doc,bad)
    with pytest.raises(ProtocolError,match='range'):make_citation(doc,block,end=999)

@pytest.mark.parametrize('duplicates,expected',[(0,'pass'),(1,'needs_review')])
def test_duplicate_threshold_is_exclusive(duplicates,expected):
    rules=json.loads((Path(__file__).parents[2]/'rules/quality/standard.json').read_text())
    objects=[];assessments=[]
    for i in range(10):
        kind='atomic_claim' if i<5 else 'knowledge_atom';r=ref(kind,str(i));text=f'Text {i}'
        if duplicates and i==9:text='Text 8'
        objects.append({'ref':r,'value':{'statement':text,'summary':text,'importance':.9,'confidence':.9},'source_blocks':['b']})
        assessments.append({'object_ref':r,'fidelity_verdict':'supported','citation_ids':['cit'],'auto_recheck_attempted':False})
    parse=SimpleNamespace(blocks_with_source_span=95,total_source_relevant_blocks=100,status='pass')
    report,_=build_report(objects,assessments,[{'resolved':True}],[{'chapter_id':'ch_0001','complete':True}],[],parse,['ch_0001'],rules,{})
    assert report['metrics']['exact_duplicate_rate']['value']==duplicates/10
    assert report['status']==expected
    assert json.loads(json.dumps(report))==report


def test_coverage_issue_merge_and_no_invented_denominator():
    rules=json.loads((Path(__file__).parents[2]/'rules/quality/standard.json').read_text())
    issue={'issue_id':'same-batch-finding','issue_type':'MAJOR_OMISSION','chapter_id':'ch_0001','status':'open','severity':'major'}
    parse=SimpleNamespace(blocks_with_source_span=1,total_source_relevant_blocks=1,status='pass')
    report,issues=build_report([],[],[],[{'chapter_id':'ch_0001','complete':True}],[issue,issue],parse,['ch_0001'],rules,{})
    assert len(issues)==1 and report['metrics']['major_omission_count']['value']==1
    assert report['metrics']['major_omission_count']['denominator'] is None
    assert report['metrics']['major_omission_chapter_rate']['denominator']==1
    assert report['status']=='needs_review'


def test_verification_schema_snapshots():
    from book_distiller.core.models.verification import Citation,EvidenceAssessment,ReviewIssue,ObjectRef,VERIFY_RESULT_MODELS
    from book_distiller.pipeline.workflows import load_workflow
    root=Path(__file__).parents[2]
    models={'citation':Citation,'evidence-assessment':EvidenceAssessment,'review-issue':ReviewIssue,'verification-object-reference':ObjectRef}
    models.update({k.replace('_','-')+'-result':v for k,v in VERIFY_RESULT_MODELS.items()})
    for name,model in models.items():assert json.loads((root/f'schemas/types/{name}.schema.json').read_text())==model.model_json_schema()
    for name,model in VERIFY_RESULT_MODELS.items():assert load_workflow(root,name,['technical']).output_schema()==model.model_json_schema()

@pytest.mark.parametrize('kind',['UNSUPPORTED_CLAIM','CONTRADICTED_CLAIM','OVERCLAIM','WEAK_EVIDENCE','BROKEN_CITATION','MAJOR_OMISSION','DUPLICATE_KNOWLEDGE','CONCEPT_DISTORTION','SYNTHESIS_OVERREACH','LOW_TRACEABILITY','LOW_CHAPTER_COVERAGE'])
def test_all_quality_issue_types_roundtrip(kind):
    from book_distiller.core.models.verification import ReviewIssue
    issue=ReviewIssue(issue_id='issue_test',issue_type=kind,severity='major',object_refs=[ref()],source_refs=['cit_1'],chapter_id='ch_0001',
        summary='Public test finding.',suggested_action='inspect_source',reviewer_confidence=.9)
    assert ReviewIssue.model_validate_json(issue.model_dump_json())==issue
    for bad in [{'issue_type':'UNKNOWN'},{'severity':'fatal'},{'status':'silently_fixed'},{'reviewer_confidence':1.1}]:
        with pytest.raises(ValidationError):ReviewIssue.model_validate(issue.model_dump()|bad)


def test_quality_report_and_issue_schema_snapshots():
    from book_distiller.core.models.verification import QualityReport,ReviewIssue
    root=Path(__file__).parents[2]
    for name,model in [('quality-report',QualityReport),('quality-issue',ReviewIssue)]:
        assert json.loads((root/f'schemas/types/{name}.schema.json').read_text())==model.model_json_schema()

@pytest.mark.parametrize('kind',['atomic_claim','knowledge_atom','core_idea','mental_model','meta_principle'])
def test_assessment_target_bindings_and_independent_layers(kind):
    from book_distiller.core.models.verification import VerifyResult
    from book_distiller.pipeline.verification_validation import validate_verification
    r=ref(kind);r['book_generation_id']=str(uuid4())
    gid=str(uuid4());scope={'generation_id':gid,'dependency_hash':'a'*64,'classification_hash':'b'*64}
    payload={'target':{'ref':r},'objects':[{'ref':r}],'lower_assessments':[],
        'citations':[{'citation_id':'cit_1'},{'citation_id':'cit_2'}],'original_citation_ids':['cit_1','cit_2'],
        'lower_objects_reviewed':0,'lower_objects_available':0,'omitted_source_blocks':[],
        'source_excerpts':[{'citation_id':'cit_1','chapter_id':'ch_0001'},{'citation_id':'cit_2','chapter_id':'ch_0002'}]}
    context=SimpleNamespace(scope=scope,payload=payload,input_hash='c'*64,task_type='verify_'+kind)
    a=AssessmentDraft(object_ref=r,citation_ids=['cit_1','cit_2'],evidence_strength='strong',fidelity_verdict='supported',reviewer_confidence=.9,verification_summary='Explicit protocol fixture.')
    result=SimpleNamespace(verification_generation_id=gid,dependency_hash='a'*64,classification_hash='b'*64,input_hash='c'*64,assessment=a)
    # Validator requires supplied Citation blocks, but judges no source semantics itself.
    for i,c in enumerate(payload['citations']):c['block_id']=f'blk_{i+1:06d}'
    validate_verification(result,context)
    if kind=='meta_principle':
        a.citation_ids=['cit_1']
        with pytest.raises(ProtocolError,match='INSUFFICIENT_PRINCIPLE_SUPPORT'):validate_verification(result,context)
        a.citation_ids=['cit_1','cit_2']
    a.object_ref=a.object_ref.model_copy(update={'object_id':'foreign'})
    with pytest.raises(ProtocolError,match='INVALID_OBJECT_REFERENCE'):validate_verification(result,context)
