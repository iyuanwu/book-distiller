"""Reader integration on isolated protocol fixtures; no AI and no private books."""
import json
from pathlib import Path
from uuid import uuid4
import pytest
from typer.testing import CliRunner
from test_chapter_tasks import chapter_book
from test_book_tasks import ready
from test_verification_tasks import verification_book, complete
from book_distiller.renderers.service import RenderService
from book_distiller.renderers.model import ReaderViewModel, read
from book_distiller.storage import reader, verification
from book_distiller.storage.filesystem import hash_source
from book_distiller.core.errors import BookDistillerError
from book_distiller.cli.main import app

PROJECT = Path(__file__).parents[2]

@pytest.fixture
def rendered(verification_book, isolated_storage):
    verify, ingest = verification_book
    complete(verify, ingest)
    service = RenderService(isolated_storage, PROJECT)
    return service, ingest, verify


def mutate_verification(ingest, name, change):
    path = verification.current(ingest.library_path)
    data = read(path/name)
    change(data)
    (path/name).write_text(json.dumps(data))
    manifest = read(path/'manifest.json')
    manifest['artifact_hashes'][name] = hash_source(path/name)[0]
    (path/'manifest.json').write_text(json.dumps(manifest))


def test_reader_complete_idempotent_force_no_canonical_write(rendered):
    s, i, _ = rendered
    before = {str(p): hash_source(p)[0] for folder in ['knowledge', 'verification', '.parsed-generations'] for p in (i.library_path/folder).rglob('*') if p.is_file()}
    path, already, metadata = s.render(i.manifest.book.slug)
    assert not already and path.is_file()
    assert metadata['quality_gate'] == 'needs_review'
    assert 'This knowledge model requires review.' in path.read_text()
    for filename in ['L0.md','L1.md','L2.md','L3.md','knowledge-model.md','quality-report.md']:
        assert (path.parent/'markdown'/filename).is_file()
    assert s.render(i.manifest.book.slug)[1]
    old = path.resolve()
    assert s.describe(i.manifest.book.slug,i.library_path)['Reader'] == 'completed'
    s.render(i.manifest.book.slug, True)
    assert old.is_file() and path.resolve() != old
    assert before == {str(p): hash_source(Path(p))[0] for p in before}
    data = read(path.parent/'data/knowledge.json')
    evidence = read(path.parent/'data/evidence.json')['entries']
    quality = read(path.parent/'data/quality.json')
    assert all(set(o['lower_ids']) <= data['objects'].keys() for o in data['objects'].values())
    assert all(set(a['citation_ids']) <= evidence.keys() for a in quality['assessments'].values())
    assert all(c['block_id'] and c['source_spans'] and c['page_number'] is None for c in evidence.values())


@pytest.mark.parametrize('when', ['emit', 'before_switch', 'after_switch'])
def test_atomic_failure_keeps_previous(rendered, monkeypatch, when):
    s, i, _ = rendered
    path, _, _ = s.render(i.manifest.book.slug)
    old = path.resolve()
    def fail(*args, **kwargs):
        raise OSError('injected reader failure')
    if when == 'emit':
        monkeypatch.setattr('book_distiller.renderers.service.emit', fail)
    elif when == 'before_switch':
        monkeypatch.setattr(reader, 'publish', fail)
    else:
        original = reader.publish
        def after(*args):
            original(*args)
            fail()
        monkeypatch.setattr(reader, 'publish', after)
    with pytest.raises(OSError, match='injected'):
        s.render(i.manifest.book.slug, True)
    assert path.resolve() == old and reader.intact(old.parent)


def test_new_verification_is_stale(rendered):
    s, i, v = rendered
    s.render(i.manifest.book.slug)
    complete(v, i, True)
    assert s.describe(i.manifest.book.slug, i.library_path)['Reader'] == 'stale'
    assert not s.render(i.manifest.book.slug)[1]


def test_no_verification_refused(verification_book, isolated_storage):
    _, i = verification_book
    with pytest.raises(BookDistillerError, match='VERIFICATION_REQUIRED'):
        RenderService(isolated_storage, PROJECT).render(i.manifest.book.slug)
    assert not (i.library_path/'output').exists()


@pytest.mark.parametrize('failure', ['failed', 'stale', 'missing_citation', 'missing_object'])
def test_invalid_current_inputs_refused(rendered, failure):
    s, i, _ = rendered
    path, _, _ = s.render(i.manifest.book.slug)
    old = path.resolve()
    if failure == 'failed':
        mutate_verification(i, 'quality_report.json', lambda d: d.update(status='failed'))
    elif failure == 'stale':
        p = verification.current(i.library_path)/'manifest.json'
        d = read(p);d['dependencies']['book_generation_id'] = str(uuid4());p.write_text(json.dumps(d))
    elif failure == 'missing_citation':
        mutate_verification(i, 'idea_assessments.json', lambda d: d['assessments'][0].update(citation_ids=['cit_missing']))
    else:
        mutate_verification(i, 'provenance.json', lambda d: next(iter(d['evidence_paths'].values()))['lower_object_ids'].append('missing'))
    with pytest.raises(BookDistillerError):
        s.render(i.manifest.book.slug, True)
    assert path.resolve() == old


def test_missing_reference_source_allows_evidence(rendered, monkeypatch, tmp_path):
    s, i, _ = rendered
    real = s.ai._resolve
    manifest = i.manifest.model_copy(deep=True)
    manifest.source.copy_mode = 'reference'
    manifest.source.stored_path = str(tmp_path/'missing-original.md')
    monkeypatch.setattr(s.ai, '_resolve', lambda selector: (manifest, i.library_path))
    path, _, _ = s.render(i.manifest.book.slug)
    book = read(path.parent/'data/book.json')
    assert not book['source']['available'] and book['source']['uri'] is None
    assert read(path.parent/'data/evidence.json')['embedded_chars'] > 0
    # The exception is reader-only: ordinary verification still refuses the source.
    from book_distiller.pipeline.verification_tasks import VerificationTasks
    from book_distiller.pipeline.canonical import load_canonical
    doc = load_canonical(i.library_path, manifest, s.ai.library.database)
    with pytest.raises(BookDistillerError, match='SOURCE_INTEGRITY_FAILURE'):
        VerificationTasks(s.ai).dependencies(i.library_path, doc)
    monkeypatch.setattr(s.ai, '_resolve', real)


def test_changed_existing_original_refused(rendered):
    s, i, _ = rendered
    (i.library_path/i.manifest.source.stored_path).write_text('changed')
    with pytest.raises(BookDistillerError, match='SOURCE_INTEGRITY_FAILURE'):
        s.render(i.manifest.book.slug)


def test_data_corruption_regenerates_and_status_stale(rendered):
    s, i, _ = rendered
    p, _, _ = s.render(i.manifest.book.slug)
    (p.parent/'assets/book-data.js').write_text('bad')
    assert s.describe(i.manifest.book.slug, i.library_path)['Reader'] == 'stale'
    assert not s.render(i.manifest.book.slug)[1]
    manifest_path = p.parent/'render_manifest.json'
    metadata = read(manifest_path)
    p.unlink()
    metadata['artifact_hashes'].pop('index.html')
    manifest_path.write_text(json.dumps(metadata))
    assert s.describe(i.manifest.book.slug, i.library_path)['Reader'] == 'stale'
    assert not s.render(i.manifest.book.slug)[1] and p.is_file()


def test_cli_browser_failure_is_warning(rendered, monkeypatch):
    _, i, _ = rendered
    def fail(*args, **kwargs):
        assert args[0][0] == 'open' and not kwargs.get('shell')
        raise OSError('browser unavailable')
    monkeypatch.setattr('subprocess.run', fail)
    result = CliRunner().invoke(app, ['render', i.manifest.book.slug])
    assert result.exit_code == 0, result.output
    assert 'Could not open browser' in result.output and 'NEEDS_REVIEW' in result.output
    result = CliRunner().invoke(app, ['render', i.manifest.book.slug, '--no-open'])
    assert result.exit_code == 0 and 'Already rendered.' in result.output
    assert 'Reader' in CliRunner().invoke(app, ['status', i.manifest.book.slug]).output


def test_excerpt_range_and_budget_are_explicit(rendered):
    from book_distiller.renderers.evidence import build
    from book_distiller.evidence.citations import SourceIndex, resolve_citation
    s, i, _ = rendered
    directory, _, args = s.inputs(i.manifest.book.slug)
    document, _, _, _, _, verified, rules = args
    citations = read(verified/'citations.jsonl')
    evidence = build(document, citations, rules | {'max_evidence_chars': 70, 'excerpt_chars': 40})
    assert evidence['embedded_chars'] == 70
    assert any(c['budget_omitted'] for c in evidence['entries'].values())
    index = SourceIndex(document.blocks_path)
    for cid, c in evidence['entries'].items():
        original = next(row for row in citations if row['citation_id'] == cid)
        assert resolve_citation(document, original, index)['text'].startswith(c['excerpt'])
        assert c['source_spans'] == original['source_spans']
        assert c['excerpt_truncated'] == (c['excerpt_chars'] < c['range_chars'])


def test_renderer_never_prepares_ai_tasks(rendered, monkeypatch):
    s, i, _ = rendered
    def forbidden(*a, **kw):
        raise AssertionError('Renderer must not prepare new AI tasks')
    monkeypatch.setattr(s.ai, 'prepare', forbidden)
    s.render(i.manifest.book.slug)


def test_display_title_drives_view_and_invalidates_reader_cache(rendered):
    s, i, _ = rendered
    path, _, _ = s.render(i.manifest.book.slug)
    manifest, directory = s.ai._resolve(i.manifest.book.slug)
    old = manifest.model_copy(deep=True)
    manifest.edition.display_title = 'Original Display Title'
    with s.ai.library.files.locked():
        try:
            with s.ai.library.database.transaction() as connection:
                connection.execute('UPDATE editions SET display_title = ? WHERE edition_id = ?',
                    (manifest.edition.display_title, str(manifest.edition.edition_id)))
                s.ai.library.files.write_manifest(directory, manifest)
        except BaseException:
            s.ai.library.files.write_manifest(directory, old)
            raise
    assert s.describe(i.manifest.book.slug, directory)['Reader'] == 'stale'
    path, already, _ = s.render(i.manifest.book.slug)
    assert not already
    assert read(path.parent/'data/book.json')['title'] == 'Original Display Title'
    assert '<h1>Original Display Title</h1>' in path.read_text()
    assert s.render(i.manifest.book.slug)[1]
