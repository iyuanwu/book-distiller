"""Lexical retrieval contracts; no model execution."""
import json
from pathlib import Path
import pytest
from book_distiller.ask.retrieval import AskRetriever, intent
from book_distiller.core.models.ask import BookAnswer


def obj(key, kind='atomic_claim', lower=(), **value):
    return {'ref':{'object_id':key,'object_type':kind,'chapter_id':'ch_0003'}, 'value':value,'lower_ids':list(lower),'human':{}}


def assessment(verdict='supported',strength='strong'):
    return {'fidelity_verdict':verdict,'evidence_strength':strength}


@pytest.mark.parametrize('question',['publication', '发布', 'PUBLISH', 'ｐｕｂｌｉｃａｔｉｏｎ'])
def test_canonical_alias_chinese_english(question):
    r = AskRetriever({'concept_x':obj('concept_x','concept',canonical_name='publication',aliases=['publish'],zh_name='发布')},{})
    assert r.retrieve(question).ranked_ids == ['concept_x']


@pytest.mark.parametrize('question',['checkpoint', '第3章讲什么？', 'chapter 3', 'Exact title'])
def test_title_claim_term_chapter(question):
    r = AskRetriever({'a':obj('a',title='Exact title',concept_terms=['checkpoint'])},{})
    assert r.retrieve(question).ranked_ids == ['a']


def test_expansion_and_supported_preference():
    objects = {'idea':obj('idea','core_idea',['atom'],title='publication'),
               'atom':obj('atom','knowledge_atom',['claim'],title='publication'),
               'claim':obj('claim',statement='publication')}
    a = {'idea':assessment('partially_supported','weak'), 'atom':assessment(), 'claim':assessment('unsupported','insufficient')}
    r = AskRetriever(objects,a)
    assert r.retrieve('publication').ranked_ids == ['atom','idea']
    assert r.expand('idea') == []
    assert r.expand('idea',allow_unsupported=True) == ['atom','claim','idea']
    assert 'claim' in r.retrieve('unsupported publication').ranked_ids
    assert not r.expand('idea',allow_unsupported=True,max_objects=2)


@pytest.mark.parametrize('question,expected', [('why validate?', 'why'), ('什么是发布？','concept'), ('X和Y有什么区别？','comparison'),
    ('How do X and Y relate?','relationship'),('where is publication supported?','source_location'),('用模型分析我的公司','application'),('我的笔记','meta')])
def test_intents(question,expected): assert intent(question)==expected


def test_schema_snapshot():
    assert json.loads((Path(__file__).parents[2]/'schemas/types/book-answer.schema.json').read_text())==BookAnswer.model_json_schema()


def test_notebook_is_not_note_and_possessive_is_not_keyword():
    from book_distiller.ask.retrieval import terms
    assert intent('How does the local notebook prepare its candidate?')=='fact'
    assert intent('What notes did I write?')=='meta'
    assert 's' not in terms("Apple's price")


def test_model_principle_and_concept_expansion():
    objects={'claim':obj('claim'), 'atom':obj('atom','knowledge_atom',['claim']),
             'idea':obj('idea','core_idea',['atom']), 'model':obj('model','mental_model',['idea']),
             'principle':obj('principle','meta_principle',['model','idea']), 'concept':obj('concept','concept',['atom'])}
    r=AskRetriever(objects,{})
    assert r.expand('principle')==['atom','claim','idea','model','principle']
    assert r.expand('concept')==['atom','claim','concept']


@pytest.mark.parametrize('question',['哪些观点不受支持？','有哪些错误？','核验失败的观点是什么？'])
def test_explicit_problem_question(question):
    r=AskRetriever({'bad':obj('bad',statement='publication')},{'bad':assessment('unsupported','insufficient')})
    assert r.retrieve(question).ranked_ids==['bad']
