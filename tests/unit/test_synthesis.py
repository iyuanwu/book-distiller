import json
import tracemalloc
import pytest
from pydantic import ValidationError
from book_distiller.core.models.synthesis import IntermediateGroup,MetaPrinciple
from book_distiller.pipeline.reduction import batches,reduced_items,expand
from book_distiller.pipeline.book_memory import build_memory
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.pipeline.canonical import canonical_json,json_hash


def test_recursive_1200_atoms_bounded_and_lossless():
    tracemalloc.start()
    frontier=({'id':f'atom_{i}','title':'Storage','summary':'Prepare complete writes. '*30,'concept_terms':['atomic writes'],'chapters':[f'ch_{i%5:04d}']} for i in range(1200))
    lineage={};level=0;contexts=0
    while True:
        result=[];parts=0
        for part in batches(frontier):
            assert len(part)<=24 and len(canonical_json(part))<=14000
            groups=[IntermediateGroup(title='Publication',summary='Prepare complete writes.',source_ids=[v['id'] for v in part])]
            result.extend(reduced_items(groups,part,lineage,f'l{level}b{parts}'));parts+=1;contexts+=1
        if parts==1: break
        frontier=iter(result);level+=1
        assert level<10
    assert len(expand([v['id'] for v in result],{v['id'] for v in result},lineage))==1200
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert peak<16*1024*1024 and contexts>50


@pytest.mark.parametrize('refs',[['a'],['a','a'],['a','z']])
def test_reduce_cannot_drop_duplicate_or_invent(refs):
    with pytest.raises(ProtocolError,match='REDUCE_REFERENCE_INVALID'):
        reduced_items([IntermediateGroup(title='X',summary='X',source_ids=refs)],[{'id':'a'},{'id':'b'}],{},'g')


def test_reduce_must_progress_and_oversized_is_explicit():
    items=[{'id':'a'},{'id':'b'}]
    with pytest.raises(ProtocolError,match='REDUCE_NOT_PROGRESSING'):
        reduced_items([IntermediateGroup(title='X',summary='X',source_ids=[i['id']]) for i in items],items,{},'g')
    with pytest.raises(ProtocolError,match='SYNTHESIS_CONTEXT_TOO_LARGE'):
        list(batches([{'id':'a','summary':'x'*14000}]))


def test_book_memory_deterministic_budget_hash_priority():
    ideas=[{'core_idea_id':f'idea_{i:03d}','statement':'Source point. '*100,'atom_ids':['a'],'importance':.9} for i in range(100)]
    concepts=[{'concept_id':f'concept_{i}','canonical_name':'Concept'} for i in range(100)]
    args=({}, {'primary_type':'technical'},[],concepts,ideas,[],[],[])
    first=build_memory(*args,max_chars=2200)
    second=build_memory({},args[1],[],list(reversed(concepts)),list(reversed(ideas)),[],[],[],max_chars=2200)
    assert first==second and first['selected_chars']==len(canonical_json(first))<=2200
    assert first['book_memory_hash']==json_hash({k:v for k,v in first.items() if k!='book_memory_hash'})
    assert first['core_ideas'] and first['omitted']['core_ideas']>0
    with pytest.raises(ProtocolError,match='BOOK_MEMORY_BUDGET_TOO_SMALL'): build_memory(*args,max_chars=10)


def test_meta_principle_requires_multiple_supports():
    with pytest.raises(ValidationError):
        MetaPrinciple(principle_id='principle_001',title='X',statement='X',core_idea_ids=['idea_001'],supporting_chapters=['ch_0001','ch_0002'],promotion_reason='X',confidence=.9)


def test_synthesis_schema_snapshots_and_overlays():
    from pathlib import Path
    from book_distiller.core.models.synthesis import RESULT_MODELS,PublishedConcept,PublishedCoreIdea,PublishedMentalModel,MetaPrinciple,BookMemory
    from book_distiller.pipeline.workflows import load_workflow
    root=Path(__file__).parents[2]
    models={'canonical-concept':PublishedConcept,'core-idea':PublishedCoreIdea,'mental-model':PublishedMentalModel,'meta-principle':MetaPrinciple,'book-memory':BookMemory}
    models.update({name.replace('_','-')+'-result':model for name,model in RESULT_MODELS.items()})
    for name,model in models.items():
        assert json.loads((root/f'schemas/types/{name}.schema.json').read_text())==model.model_json_schema()
    workflow=load_workflow(root,'build_core_ideas',['investment','business','philosophy'])
    assert len(workflow.overlay_paths)==3 and workflow.prompt_text().count('Primary type emphasis')==1
    assert workflow.output_schema()==load_workflow(root,'build_core_ideas',['technical']).output_schema()
