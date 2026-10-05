from book_distiller.pipeline.dependencies import compatible_dependencies
"""Derived reader transaction. No AI execution, canonical writes or new facts."""
from datetime import datetime, timezone
from pathlib import Path
import json
import os
import shutil
from uuid import uuid4
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.pipeline.canonical import load_canonical, json_hash
from book_distiller.pipeline.verification_tasks import VerificationTasks
from book_distiller.storage import verification, reader
from book_distiller.storage.knowledge import Publication
from book_distiller.storage.filesystem import hash_source
from book_distiller.core.errors import BookDistillerError, StorageError
from book_distiller.renderers.model import ReaderViewModel, read, reader_title
from book_distiller.renderers.html import emit

READER_VERSION = '1.0'


class RenderService:
    def __init__(self, home, project):
        self.ai = AITaskService(home, project)
        self.project = project

    def inputs(self, selector):
        manifest, directory = self.ai._resolve(selector)
        from book_distiller.pipeline.staleness import read as stale_state
        if any(k!='render' for k in stale_state(directory)):
            raise StorageError('STALE / NEEDS RE-VERIFICATION: unfinished pipeline invalidation')
        document = load_canonical(directory, manifest, self.ai.library.database)
        classification, deps, book, model = VerificationTasks(self.ai).dependencies(directory, document, allow_missing_reference=True)
        verified = verification.current(directory)
        if verified is None:
            raise StorageError('VERIFICATION_REQUIRED: complete verification before render')
        evaluation = read(verified/'manifest.json')
        if not compatible_dependencies(evaluation['dependencies'],deps):
            raise StorageError('STALE: Current knowledge or verification is stale. Re-run the required pipeline before rendering.')
        gate = read(verified/'quality_report.json')['status']
        if gate not in ('pass', 'needs_review'):
            raise StorageError('FAILED: quality gate refuses normal reader publication')
        rules = read(self.project/'rules/reader/standard.json')
        resources = [*sorted((self.project/'templates/reader').glob('*')),
                     *sorted((self.project/'book_distiller/renderers').glob('*.py')),
                     self.project/'book_distiller/storage/reader.py', self.project/'rules/reader/standard.json']
        original = Path(manifest.source.stored_path) if manifest.source.copy_mode == 'reference' else directory/manifest.source.stored_path
        from book_distiller.human.resolver import EffectiveKnowledgeResolver
        hashes = {'human_display':EffectiveKnowledgeResolver(directory).display_hash(),'book': hash_source(book/'book_model.json')[0], 'verification': hash_source(verified/'manifest.json')[0],
                  'normalized': document.fingerprint.document_hash, 'classification': deps['knowledge_dependencies']['classification_hash'],
                  'renderer': json_hash({str(p.relative_to(self.project)): hash_source(p)[0] for p in resources}),
                  'display': json_hash({'title': reader_title(manifest, document), 'source': str(original), 'available': original.exists()}), 'reader_version': READER_VERSION}
        metadata = {'book_id': str(manifest.book.book_id), 'edition_id': str(manifest.edition.edition_id),
                    'book_generation_id': model['generation_id'], 'verification_generation_id': evaluation['generation_id'],
                    'reader_version': READER_VERSION, 'quality_gate': gate, 'source_sha256': manifest.source.sha256,
                    'input_hashes': hashes, 'input_hash': json_hash(hashes)}
        return directory, metadata, (document, manifest, classification, book, model, verified, rules)

    def render(self, selector, force=False):
        with self.ai.library.files.locked():
            directory, metadata, args = self.inputs(selector)
            old = reader.current(directory)
            if old and reader.intact(old) and read(old/'render_manifest.json')['input_hash'] == metadata['input_hash'] and not force:
                return directory/'output/index.html', True, read(old/'render_manifest.json')
            root = directory/'.reader-generations'
            if root.is_symlink():
                raise StorageError('Unsafe reader generation root')
            destination = root/str(uuid4())
            previous = os.readlink(directory/'output') if (directory/'output').is_symlink() else None
            try:
                view = ReaderViewModel.load(directory, *args)
                metrics = emit(view, destination, self.project, args[-1])
                metadata |= {'render_generation_id': destination.name, 'generated_at': datetime.now(timezone.utc).isoformat(),
                             'metrics': metrics, 'level_chars': {k: v['chars'] for k, v in view.data['progressive'].items() if 'chars' in v},
                             'artifact_hashes': {str(p.relative_to(destination)): hash_source(p)[0] for p in destination.rglob('*') if p.is_file()}}
                manifest_path = destination/'render_manifest.json'
                payload_bytes = metrics['total_bytes']
                metrics['payload_bytes'] = payload_bytes
                for _ in range(5):
                    manifest_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding='utf-8')
                    total = payload_bytes + manifest_path.stat().st_size
                    if total == metrics['total_bytes']:
                        break
                    metrics['total_bytes'] = total
                if total > args[-1]['max_output_bytes']:
                    raise StorageError('Reader exceeds output size limit; previous reader retained')
                _, fresh, _ = self.inputs(selector)
                if fresh['input_hash'] != metadata['input_hash']:
                    raise StorageError('STALE: dependencies changed during render')
                reader.publish(directory, destination)
                from book_distiller.pipeline.staleness import reader_status, complete
                complete(directory,'render')
                reader_status(directory,'current','Current verified reader')
            except BaseException:
                pointer = directory/'output'
                if pointer.is_symlink() and os.readlink(pointer) == '.reader-generations/'+destination.name:
                    Publication(pointer, previous).restore()
                if destination.exists():
                    shutil.rmtree(destination)
                raise
            return directory/'output/index.html', False, metadata

    def describe(self, selector, directory):
        try:
            path = reader.current(directory)
            if path is None:
                return {'Reader': 'unavailable'}
            from book_distiller.pipeline.staleness import read as stale_state
            if stale_state(directory):return {'Reader':'stale','Reader path':str(directory/'output/index.html')}
            _, metadata, _ = self.inputs(selector)
            state = 'completed' if reader.intact(path) and read(path/'render_manifest.json')['input_hash'] == metadata['input_hash'] else 'stale'
            return {'Reader': state, 'Reader path': str(directory/'output/index.html')}
        except (BookDistillerError, OSError, ValueError, KeyError):
            return {'Reader': 'stale', 'Reader path': str(directory/'output/index.html')}
