"""Deterministic selection, bounded exports and synthetic large-book acceptance."""
from copy import deepcopy
import json
from pathlib import Path
import time
import tracemalloc
import pytest
from book_distiller.renderers.model import ReaderViewModel, normalize
from book_distiller.renderers.progressive import select
from book_distiller.renderers.html import emit
from book_distiller.core.errors import StorageError

PROJECT = Path(__file__).parents[2]
RULES = json.loads((PROJECT/'rules/reader/standard.json').read_text())


def fixture_data():
    report = dict(status='pass', metrics={}, thresholds={}, verdict_counts={}, major_verdict_counts={},
                  issue_counts={}, automatic_rechecks=0, remaining_review_items=[], gate_reasons=[],
                  major_objects_reviewed=0, major_objects_total=0)
    return {'book': {'title': '原创 Reader fixture', 'edition_id': 'fixture', 'source': {'available': False, 'path': '/missing', 'uri': None},
                     'chapters': {}, 'classification': {'primary_type': 'technical'}},
            'quality': {'report': report, 'assessments': {}, 'issues': [], 'coverage': {'batches': []}},
            'knowledge': {'objects': {}, 'relationships': []}, 'concepts': {}, 'evidence': {'entries': {}, 'embedded_chars': 0}}


def add(data, id, kind='core_idea', importance=.8, verdict='supported', strength='strong', **kwargs):
    data['knowledge']['objects'][id] = dict(id=id, type=kind, title=id, statement='已有知识文本 '+id,
        importance=importance, confidence=.9, anchor='idea-'+id, lower_ids=[], **kwargs)
    data['quality']['assessments'][id] = dict(fidelity_verdict=verdict, evidence_strength=strength, citation_ids=[],
        reviewer_confidence=.8, verification_summary='源文支持', unsupported_aspects=[])


@pytest.mark.parametrize('level', range(4))
def test_deterministic_and_budget(level):
    data = fixture_data()
    for n in range(80):
        add(data, str(n), importance=n/100)
    first = ReaderViewModel.finish(deepcopy(data), RULES).data['progressive'][f'l{level}']
    data['knowledge']['objects'] = dict(reversed(list(data['knowledge']['objects'].items())))
    second = ReaderViewModel.finish(data, RULES).data['progressive'][f'l{level}']
    assert first == second and first['chars'] <= RULES['level_budgets'][level]
    assert first['items'][0]['id'] == '79'


def test_verification_before_importance_and_issue_filter():
    data = fixture_data()
    add(data,'supported',importance=.1)
    add(data,'moderate',importance=1,strength='moderate')
    add(data,'unsupported',importance=1,verdict='unsupported')
    add(data,'contradicted',importance=1,verdict='contradicted')
    add(data,'partial',importance=1,verdict='partially_supported')
    add(data,'overreach',importance=1)
    data['quality']['issues'] = [dict(status='open', severity='warning', issue_type='SYNTHESIS_OVERREACH', object_refs=[{'object_id':'overreach'}])]
    p = ReaderViewModel.finish(data,RULES).data['progressive']
    for level in ('l0','l1','l2'):
        ids = [i['id'] for i in p[level]['items']]
        assert ids[0] == 'supported'
        assert not set(ids)&{'unsupported','contradicted','partial','overreach'}
    assert 'partial' in [i['id'] for i in p['l3']['items']]
    assert set(p['l4']['object_ids']) == set(data['knowledge']['objects'])


def test_l3_does_not_dump_all_lower_objects():
    data = fixture_data()
    for kind in ('atomic_claim','knowledge_atom'):
        for i in range(100):add(data,kind+str(i),kind)
    view = ReaderViewModel.finish(data,RULES).data['progressive']['l3']
    kinds = [data['knowledge']['objects'][i['id']]['type'] for i in view['items']]
    assert kinds.count('atomic_claim') <= 24 and kinds.count('knowledge_atom') <= 16
    assert len(view['items']) < 200


def test_l0_long_text_uses_exact_title():
    data = fixture_data();add(data,'Short canonical title')
    data['knowledge']['objects']['Short canonical title']['statement'] = 'a'*1000
    view=ReaderViewModel.finish(data,RULES).data['progressive']['l0']
    assert view['items'] == [{'id':'Short canonical title','title':'Short canonical title','fields':[]}]


def test_search_unicode_alias_claim_model():
    data=fixture_data();add(data,'claim','atomic_claim');add(data,'model','mental_model')
    data['knowledge']['objects']['model']['description']='Atomic Publish'
    data['concepts']['concept']={'canonical_name':'Checkpoint', 'zh_name':'检查点', 'aliases':['resume point'], 'relations':[]}
    result=ReaderViewModel.finish(data,RULES).data['search']
    for query,target in [('检查点','concept'),('CHECKPOINT','concept'),('resume point','concept'),('已有知识文本 claim','claim'),('ＡＴＯＭＩＣ','model')]:
        assert target in [s['id'] for s in result if normalize(query) in s['text']]


def test_escaped_offline_export_and_shared_markdown(tmp_path):
    data=fixture_data();payload='</script><script>alert("x")</script>&\u2028'
    data['book']['title']=payload;add(data,'idea')
    data['knowledge']['objects']['idea']['statement']=payload
    view=ReaderViewModel.finish(data,RULES);out=tmp_path/'reader';emit(view,out,PROJECT,RULES)
    html=(out/'index.html').read_text();bundle=(out/'assets/book-data.js').read_text();js=(out/'assets/app.js').read_text()
    assert '&lt;/script&gt;' in html and payload not in html
    assert '</script>' not in bundle and '\\u003c' in bundle and '\\u2028' in bundle
    assert 'fetch(' not in js and 'innerHTML' not in js and 'https://' not in html
    decoded=json.loads(bundle.removeprefix('window.BOOK_DISTILLER_DATA = ').rstrip(';\n'))
    assert decoded['book']['title']==payload
    for level in range(4):
        from html import escape
        text=(out/f'markdown/L{level}.md').read_text()
        for item in view.data['progressive'][f'l{level}']['items']:
            for field in item['fields']:assert escape(field['text']) in text


def test_output_size_limit(tmp_path):
    data=fixture_data();add(data,'idea')
    with pytest.raises(StorageError,match='size limit'):
        emit(ReaderViewModel.finish(data,RULES),tmp_path/'reader',PROJECT,RULES|{'max_output_bytes':10})


def test_large_1000_atoms_5000_claims(tmp_path):
    start=time.monotonic();tracemalloc.start()
    data=fixture_data()
    for i in range(5000):
        add(data,f'claim_{i:05d}','atomic_claim')
        data['knowledge']['objects'][f'claim_{i:05d}']['anchor']=f'claim-claim_{i:05d}'
    for i in range(1000):
        add(data,f'atom_{i:05d}','knowledge_atom')
        obj=data['knowledge']['objects'][f'atom_{i:05d}'];obj['anchor']=f'atom-atom_{i:05d}';obj['lower_ids']=[f'claim_{j:05d}' for j in range(i*5,i*5+5)]
    view=ReaderViewModel.finish(data,RULES)
    metrics=emit(view,tmp_path/'reader',PROJECT,RULES)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert len(view.data['search'])==6000 and len(view.data['progressive']['l4']['object_ids'])==6000
    assert all(set(o['lower_ids']) <= data['knowledge']['objects'].keys() for o in data['knowledge']['objects'].values())
    assert all(s['anchor']==data['knowledge']['objects'][s['id']]['anchor'] for s in data['search'])
    assert peak < 150_000_000 and metrics['total_bytes'] < 20_000_000
    assert any(s['id']=='claim_04999' for s in data['search'] if 'claim_04999' in s['text'])
    # Real output performance record; test data may be inspected without touching formal Library.
    result=metrics|{'peak_python_bytes':peak,'seconds':round(time.monotonic()-start,3),'atoms':1000,'claims':5000,'output':str(tmp_path/'reader')}
    print('\nLARGE_READER '+json.dumps(result))


def test_l0_first_sentence_is_exact_not_paraphrased():
    data=fixture_data();add(data,'principle','meta_principle')
    data['knowledge']['objects']['principle']['statement']='保留先前状态。'+('细节与边界。'*100)
    view=ReaderViewModel.finish(data,RULES).data['progressive']['l0']
    assert view['items'][0]['fields']==[{'field':'statement (excerpt)','text':'保留先前状态。'}]


@pytest.mark.parametrize('scenario', ['empty', 'valid', 'invalid', 'malformed', 'navigation', 'history', 'search'])
def test_actual_reader_script_routes(tmp_path, scenario):
    """Exercise the real app.js, including initialization, not a copied route regex."""
    import shutil
    import subprocess
    node = shutil.which('node')
    if node is None:
        pytest.skip('Node is needed only for deterministic reader JS unit tests')
    data = fixture_data()
    add(data, 'idea')
    data['book']['chapters']['ch_0001'] = dict(chapter_id='ch_0001',title='Original chapter',atom_ids=[],claim_ids=[])
    view = ReaderViewModel.finish(data, RULES)
    payload = tmp_path/'view.json'
    payload.write_text(json.dumps(view.data))
    result = subprocess.run([node, str(PROJECT/'tests/fixtures/reader/route_harness.cjs'),
        str(PROJECT/'templates/reader/app.js'), str(payload), scenario], capture_output=True, text=True)
    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)['passed']


@pytest.mark.parametrize('edition,book,canonical,expected', [
    ('Display title','Book title','Canonical title','Display title'),
    ('  ','Book title','Canonical title','Book title'),
    ('fixture-slug','fixture-slug','Canonical title','Canonical title'),
    ('fixture-slug','fixture-slug','fixture-slug','fixture-slug'),
])
def test_reader_display_title_priority(edition,book,canonical,expected):
    from types import SimpleNamespace as NS
    from book_distiller.renderers.model import reader_title
    manifest=NS(edition=NS(display_title=edition),book=NS(title=book,slug='fixture-slug'))
    assert reader_title(manifest,NS(book=NS(title=canonical)))==expected
