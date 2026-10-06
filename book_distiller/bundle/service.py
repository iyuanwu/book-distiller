"""Explicit snapshot planning and validated, conservative portable import."""
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import TemporaryDirectory, mkstemp
from uuid import UUID
import json
import os
import re
import shutil
import zipfile

from book_distiller.bundle.archive import BundleLimits, MANIFEST, content_hash, safe_name, validate_archive
from book_distiller.bundle.models import BookBundleManifest, BundleFile, ExternalRule
from book_distiller.core.errors import StorageError
from book_distiller.core.ingest import IngestService
from book_distiller.core.models.library import Manifest
from book_distiller.core.version import get_version
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.pipeline.canonical import load_canonical, json_hash
from book_distiller.storage.filesystem import hash_source
from book_distiller.storage import book_knowledge, knowledge, verification, reader
from book_distiller.storage.parsed import ParsedStorage
from book_distiller.renderers.service import RenderService
from book_distiller.renderers.model import ReaderViewModel, read
from book_distiller.renderers.html import emit
from book_distiller.human.resolver import EffectiveKnowledgeResolver, KINDS
from book_distiller.human.models import HumanAction, HumanRule, UserNote, TargetRef
from book_distiller.storage.journal import read_events
from book_distiller.evidence.citations import SourceIndex, resolve_citation
from book_distiller.ask.service import AskService

CHAPTER_FILES = {'chapter.json', 'claims.jsonl', 'chunks.json', 'claims-receipt.json', 'atoms.json'}
PARSE_FILES = {'completion.json', 'normalized/book.json', 'normalized/blocks.jsonl', 'normalized/quality.json'}
HUMAN_FILES = {'human/overrides.jsonl', 'human/notes.jsonl', 'human/rules.jsonl'}
READER_FILES = reader.REQUIRED | {'data/user_notes.json', 'render_manifest.json'}


def write(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')


def must(condition, message):
    if not condition:
        raise StorageError('BUNDLE_INVALID: ' + message)


def inventory(root, names):
    return {name: hash_source(root / name)[0] for name in names}


def make_receipt(root, files, source_included, source_hash, dependencies=(), snapshot_hash=None):
    write(root / '.bundle-import.json', {'receipt_version':'1.0', 'source_included':source_included,
          'source_sha256':source_hash, 'files':files, 'external_rule_dependencies':list(dependencies),
          'bundle_content_hash':snapshot_hash})


@dataclass
class BundlePlan:
    """Only these paths may be opened by export; never scan/copy the whole Book."""
    files: dict[str, str]
    pointers: dict[str, str]


def pointer_files(root):
    """Inventory current managed generations using the existing integrity validators."""
    files = {'manifest.json':'metadata', 'analysis/classification.json':'canonical'}
    pointers = {}
    def add(pointer, destination, names, category):
        must(destination is not None, 'missing ' + pointer)
        relative = destination.resolve().relative_to(root.resolve()).as_posix()
        pointers[pointer] = relative
        files.update({relative + '/' + name:category for name in names})
    add('parsed', ParsedStorage(root).current(), PARSE_FILES, 'canonical')
    normalized = read(root / 'parsed/normalized/book.json')
    for chapter in normalized['chapters']:
        key = chapter['chapter_id']
        path = knowledge.current(root, key)
        if path:
            add('knowledge/chapters/' + key, path, CHAPTER_FILES, 'canonical')
    add('knowledge/book', book_knowledge.current(root), book_knowledge.FILES | {'book_model.json'}, 'canonical')
    add('verification/current', verification.current(root), verification.FILES | {'manifest.json'}, 'verification')
    output = reader.current(root)
    must(output is not None and reader.intact(output), 'Reader missing or corrupt; run book render first')
    add('output', output, {name for name in READER_FILES if (output/name).is_file()}, 'derived')
    for name in HUMAN_FILES:
        if (root/name).exists():
            files[name] = 'human'
    return BundlePlan(files, pointers)


def validate_layout(bundle):
    """Do not allow a declared checksum to authorize arbitrary destination paths."""
    must(bundle.versions == {'normalized':'1.0','context_package':'1.4','book_answer':'1.0','reader':'1.0'}, 'unsupported artifact versions')
    pointers = bundle.pointers
    expected = {'manifest.json':'metadata', 'analysis/classification.json':'canonical'}
    must(set(pointers) == {'parsed','knowledge/book','verification/current','output'} |
         {'knowledge/chapters/' + ch for ch in bundle.chapter_generation_ids}, 'pointer inventory')
    groups = [('parsed', '.parsed-generations', str(bundle.normalized_generation_id), PARSE_FILES, 'canonical'),
              ('knowledge/book', 'knowledge/.book-generations', str(bundle.book_generation_id), book_knowledge.FILES|{'book_model.json'}, 'canonical'),
              ('verification/current', 'verification/generations', str(bundle.verification_generation_id), verification.FILES|{'manifest.json'}, 'verification'),
              ('output', '.reader-generations', str(bundle.reader_generation_id), READER_FILES-{'data/user_notes.json'}, 'derived')]
    for chapter, gid in bundle.chapter_generation_ids.items():
        must(re.fullmatch(r'ch_\d{4,}', chapter), 'chapter ID')
        groups.append(('knowledge/chapters/'+chapter, 'knowledge/.generations', str(gid), CHAPTER_FILES, 'canonical'))
    for pointer, parent, gid, names, category in groups:
        path = safe_name(pointers[pointer])
        parts = path.split('/')
        must('/'.join(parts[:-1]) == parent, 'generation parent')
        suffix = parts[-1]
        must(suffix == gid or suffix.startswith(gid + '-'), 'generation ID/path mismatch')
        if suffix != gid:
            UUID(suffix[len(gid)+1:])
        expected.update({path+'/'+name:category for name in names})
    optional = {name:'human' for name in HUMAN_FILES}
    optional[pointers['output']+'/data/user_notes.json'] = 'derived'
    actual = {f.relative_path:f.category for f in bundle.files}
    if bundle.source_included:
        source = [n for n in actual if n.startswith('source/')]
        must(len(source) == 1 and re.fullmatch(r'source/original\.(pdf|epub|txt|md|markdown|docx)', source[0]), 'source inventory')
        expected[source[0]] = 'source'
    must(set(expected) <= set(actual) <= set(expected)|set(optional), 'file whitelist')
    must(all(category == (expected | optional).get(name) for name, category in actual.items()), 'file category')


def restore_pointers(root, pointers):
    for name, destination in pointers.items():
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.symlink_to(os.path.relpath(root/destination, path.parent), target_is_directory=True)


def validate_human(root):
    for name in HUMAN_FILES:
        p = root/name
        if p.exists():
            must(not p.read_bytes() or p.read_bytes().endswith(b'\n'), 'incomplete human journal')
    for value in read_events(root/'human/overrides.jsonl'):
        target = TargetRef.model_validate(value['target_ref'])
        if value['action'] == 'rebase':
            old = TargetRef.model_validate(value['old_ref'])
            must((old.object_type, old.object_id, old.chapter_id) == (target.object_type, target.object_id, target.chapter_id), 'semantic rebind')
            continue
        permitted = {k:v for k,v in value.items() if k not in ('anchors','protected_value')}
        action = HumanAction.model_validate(permitted)
        must(action.action == 'edit' or not action.patch, 'non-edit patch')
        if action.action == 'edit':
            must(target.object_type in KINDS and bool(action.patch) and set(action.patch)<=KINDS[target.object_type][1], 'forbidden human field')
    for value in read_events(root/'human/notes.jsonl'):
        UserNote.model_validate(value)
    for value in read_events(root/'human/rules.jsonl'):
        must(HumanRule.model_validate(value).scope == 'book', 'non-book Rule body')
    human = EffectiveKnowledgeResolver(root)
    values = human.inventory()
    current_generations = {v['target_ref']['generation_id'] for v in values.values()}
    for key, state in human.state.items():
        if state['target_ref']['generation_id'] in current_generations:
            must(key in values, 'Human target missing in current generation')
    # Historical journal targets deliberately need no historical Knowledge files.
    return human


class BundleService:
    def __init__(self, home, project, limits=BundleLimits()):
        self.home, self.project, self.limits = Path(home).resolve(), Path(project).resolve(), limits
        self.library = IngestService(self.home)

    @contextmanager
    def staged_library(self, stage, manifest):
        """Validation uses an isolated Book/Edition index, with zero Task/Run rows."""
        with TemporaryDirectory(prefix='bookbundle-validation-') as temporary:
            home = Path(temporary)
            library = IngestService(home)
            library.database.initialize()
            with library.database.transaction() as connection:
                library.database.insert_manifest(connection, manifest)
            class StagedAI(AITaskService):
                def _resolve(self, selector):
                    records = self.library.database.lookup_book(selector)
                    must(len(records) == 1 and records[0] == manifest, 'staged identity')
                    must(Manifest.model_validate_json((stage/'manifest.json').read_text()) == manifest, 'staged manifest/index')
                    return manifest, stage.resolve()
            yield StagedAI(home, self.project)

    def validate_book(self, root, bundle, ai):
        manifest = Manifest.model_validate_json((root/'manifest.json').read_text())
        must((str(manifest.book.book_id),str(manifest.edition.edition_id),manifest.source.sha256) ==
             (str(bundle.book_id),str(bundle.edition_id),bundle.source_sha256), 'Book/Edition/Source identity')
        must(manifest.source.copy_mode == 'copy' and str(manifest.source.original_path) == '/bundle-source/original'+manifest.source.extension, 'nonportable source metadata')
        if bundle.source_included:
            must(hash_source(root/manifest.source.stored_path) == (manifest.source.sha256,manifest.source.file_size), 'source integrity')
        document = load_canonical(root, manifest, ai.library.database)
        must(str(document.fingerprint.parse_task_id) == str(bundle.normalized_generation_id), 'normalized generation')
        must(hash_source(root/'analysis/classification.json')[0] == bundle.classification_hash, 'classification hash')
        must(read(root/'analysis/classification.json')['task_id'] == str(bundle.classification_id), 'classification identity')
        human = validate_human(root)
        must(human.display_hash() == bundle.human_state_hash, 'Human state hash')
        rules = read_events(root/'human/rules.jsonl')
        must(json_hash(rules) == bundle.book_rule_hash, 'Book Rule hash')
        # This checks current identities, resource/schema versions, semantic revisions and gate.
        service = RenderService(ai.library.files.root, self.project)
        service.ai = ai
        _, metadata, args = service.inputs(str(bundle.book_id))
        must(metadata['book_generation_id'] == str(bundle.book_generation_id) and
             metadata['verification_generation_id'] == str(bundle.verification_generation_id) and
             metadata['quality_gate'] == bundle.quality_gate, 'Book/Verification generation or gate')
        chapters = args[4]['dependencies']['chapters']
        must({k:v['generation_id'] for k,v in chapters.items()} == {k:str(v) for k,v in bundle.chapter_generation_ids.items()}, 'chapter dependencies')
        for key, dep in chapters.items():
            must(dep['path'] == bundle.pointers['knowledge/chapters/'+key], 'Chapter dependency path')
        source = SourceIndex(document.blocks_path)
        from book_distiller.human.service import HumanService, validate_note_target
        effective_objects = human.inventory()
        current_human_generations = {v['target_ref']['generation_id'] for v in effective_objects.values()}
        for item in effective_objects.values():
            value, target = item['value'], item['target_ref']
            must(value.get('book_id',str(bundle.book_id)) == str(bundle.book_id) and value.get('edition_id',str(bundle.edition_id)) == str(bundle.edition_id), 'knowledge identity')
            if target['object_type'] in ('atomic_claim','knowledge_atom'):
                must(value['generation_id'] == target['generation_id'] and value['chapter_id'] == target['chapter_id'], 'knowledge generation')
                must(value['normalized_document_hash'] == document.fingerprint.document_hash and value['source_sha256'] == bundle.source_sha256, 'knowledge source binding')
                for evidence in value.get('evidence',[]):
                    must(evidence['block_id'] in source.entries and source.entries[evidence['block_id']]['chapter_id'] == value['chapter_id'], 'Claim evidence reference')
            if item['locked']:
                must(HumanService(ai).anchors(root,value) == human.state[json_hash(target)].get('anchors',{}), 'LOCK_DEPENDENCY_CONFLICT')
        for note in human.notes():
            target = TargetRef.model_validate(note['target_ref'])
            # Old unresolved annotations remain historical; never bind by similarity.
            if target.object_type in KINDS:
                if str(target.generation_id) in current_human_generations:
                    human.show(target)
            elif str(target.generation_id) in {str(bundle.normalized_generation_id),str(bundle.verification_generation_id)}:
                validate_note_target(root,target)
        must(len(source.entries) == document.book.block_count, 'normalized Block count')
        for citation in read(root/bundle.pointers['verification/current']/'citations.jsonl'):
            resolve_citation(document, citation, source)
        view = ReaderViewModel.load(root, *args)
        must(bundle.title == view.data['book']['title'], 'display title')
        output = reader.current(root)
        must(reader.intact(output), 'Reader checksum')
        render_manifest = read(output/'render_manifest.json')
        must(render_manifest['input_hash'] == metadata['input_hash'] and render_manifest['render_generation_id'] == str(bundle.reader_generation_id), 'Reader stale')
        # Deterministic derived bytes must actually represent canonical evidence, not merely hash correctly.
        with TemporaryDirectory(prefix='bookbundle-reader-check-') as temp:
            expected = Path(temp)/'reader'
            emit(view, expected, self.project, args[-1])
            actual_hashes = {p.relative_to(expected).as_posix():hash_source(p)[0] for p in expected.rglob('*') if p.is_file()}
            must(actual_hashes == render_manifest['artifact_hashes'], 'Reader projection differs from Effective Knowledge')
        must(AskService(ai).describe(str(bundle.book_id))['Ask'].startswith('ready'), 'Ask unavailable')
        return manifest

    @contextmanager
    def validated(self, archive):
        with TemporaryDirectory(prefix='bookbundle-inspect-') as temp:
            root = Path(temp)/'book'
            root.mkdir()
            bundle = validate_archive(archive, root, self.limits)
            try:
                validate_layout(bundle)
                restore_pointers(root, bundle.pointers)
                make_receipt(root, {f.relative_path:f.sha256 for f in bundle.files}, bundle.source_included,
                             bundle.source_sha256, [r.model_dump(mode='json') for r in bundle.external_rule_dependencies], bundle.bundle_content_hash)
                manifest = Manifest.model_validate_json((root/'manifest.json').read_text())
                with self.staged_library(root, manifest) as ai:
                    self.validate_book(root, bundle, ai)
                yield bundle, root, manifest
            except StorageError:
                raise
            except (OSError, ValueError, KeyError, TypeError) as exc:
                raise StorageError('BUNDLE_INVALID: ' + str(exc)) from exc

    def inspect(self, archive):
        with self.validated(archive) as (bundle, _, _):
            return bundle

    def export(self, selector, destination, include_source=False):
        with self.library.files.locked():
            return self._export_locked(selector,destination,include_source)

    def _export_locked(self, selector, destination, include_source=False):
        destination = Path(destination).expanduser().resolve()
        must(destination.name.endswith('.bookbundle.zip'), 'filename must end .bookbundle.zip')
        must(not destination.exists(), 'destination already exists')
        self.library._initialize()
        ai = AITaskService(self.home, self.project)
        manifest, root = ai._resolve(selector)
        render = RenderService(self.home, self.project)
        must(render.describe(selector, root)['Reader'] == 'completed', 'Reader stale; run book render first')
        render.inputs(selector)
        plan = pointer_files(root)
        if include_source:
            plan.files['source/original'+manifest.source.extension] = 'source'
        types = read(root/'analysis/classification.json')
        from book_distiller.human.service import guidance
        from book_distiller.core.models.synthesis import BOOK_WORKFLOWS
        from book_distiller.core.models.verification import VERIFY_WORKFLOWS
        dependencies = {}
        from book_distiller.bundle.receipt import receipt
        imported = receipt(root)
        if imported:
            dependencies.update({d['rule_id']:ExternalRule.model_validate(d) for d in imported.get('external_rule_dependencies',[])})
        for workflow in {'classify','classify_book','extract_claims','build_chapter_atoms','ask_book'} | BOOK_WORKFLOWS | VERIFY_WORKFLOWS:
            for rule in guidance(self.home,root,workflow,[types['primary_type'],*types['secondary_types']])['rules']:
                if rule['scope'] != 'book':
                    dependencies[rule['rule_id']] = ExternalRule(rule_id=rule['rule_id'],scope=rule['scope'],book_type=rule['book_type'],sha256=json_hash(rule),applicable_workflows=rule['applicable_workflows'])
        with TemporaryDirectory(prefix='bookbundle-export-') as temp:
            stage = Path(temp)/'book';stage.mkdir()
            portable = manifest.model_copy(deep=True)
            portable.source.copy_mode = 'copy'
            portable.source.stored_path = 'source/original'+manifest.source.extension
            portable.source.original_path = Path('/bundle-source/original'+manifest.source.extension)
            for name, category in plan.files.items():
                if name == 'manifest.json' or category == 'derived':
                    continue
                source = root/name
                if category == 'source':
                    source = Path(manifest.source.stored_path) if manifest.source.copy_mode == 'reference' else root/manifest.source.stored_path
                must(not source.is_symlink() and source.is_file(), 'unsafe/missing selected file '+name)
                path = stage/name;path.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(source, path)
            write(stage/'manifest.json',portable.model_dump(mode='json'))
            base_files = {n for n,c in plan.files.items() if c != 'derived'}
            make_receipt(stage, inventory(stage,base_files),include_source,manifest.source.sha256,
                         [r.model_dump(mode='json') for r in dependencies.values()])
            restore_pointers(stage, plan.pointers)
            # Projection only: preserve Reader identity, rebase original-source links and display hash.
            with self.staged_library(stage, portable) as staged_ai:
                rs = RenderService(staged_ai.library.files.root,self.project)
                rs.ai = staged_ai
                _, metadata, args = rs.inputs(str(manifest.book.book_id))
                output = stage/plan.pointers['output']
                metrics = emit(ReaderViewModel.load(stage,*args),output,self.project,args[-1])
                original = read(reader.current(root)/'render_manifest.json')
                metadata |= {k:original[k] for k in ('render_generation_id','generated_at','level_chars')}
                metadata |= {'metrics':metrics,'artifact_hashes':{p.relative_to(output).as_posix():hash_source(p)[0] for p in output.rglob('*') if p.is_file()}}
                write(output/'render_manifest.json',metadata)
            # Derive exact whitelist from emitted known Reader files, never arbitrary user directories.
            plan.files = {n:c for n,c in plan.files.items() if c != 'derived'} | {
                plan.pointers['output']+'/'+n:'derived' for n in READER_FILES if (output/n).is_file()}
            files = [BundleFile(relative_path=n,sha256=hash_source(stage/n)[0],size=(stage/n).stat().st_size,category=c) for n,c in sorted(plan.files.items())]
            human=EffectiveKnowledgeResolver(stage)
            model=read(stage/plan.pointers['knowledge/book']/'book_model.json')
            bundle=BookBundleManifest(created_by_version=get_version(),book_id=manifest.book.book_id,edition_id=manifest.edition.edition_id,
                title=read(output/'data/book.json')['title'],source_sha256=manifest.source.sha256,source_mode='included' if include_source else 'normalized_only',source_included=include_source,
                normalized_generation_id=read(stage/plan.pointers['parsed']/'completion.json')['task_id'],
                classification_id=types['task_id'],classification_hash=hash_source(stage/'analysis/classification.json')[0],
                chapter_generation_ids={k:v['generation_id'] for k,v in model['dependencies']['chapters'].items()},
                book_generation_id=model['generation_id'],verification_generation_id=metadata['verification_generation_id'],reader_generation_id=metadata['render_generation_id'],
                human_state_hash=human.display_hash(),book_rule_hash=json_hash(read_events(stage/'human/rules.jsonl')),
                external_rule_dependencies=sorted(dependencies.values(),key=lambda r:str(r.rule_id)),quality_gate=metadata['quality_gate'],
                versions={'normalized':'1.0','context_package':'1.4','book_answer':'1.0','reader':'1.0'},pointers=plan.pointers,files=files,bundle_content_hash='0'*64)
            bundle.bundle_content_hash=content_hash(bundle)
            destination.parent.mkdir(parents=True,exist_ok=True)
            fd, tmp = mkstemp(prefix='.'+destination.name+'.',suffix='.tmp',dir=destination.parent);os.close(fd)
            try:
                with zipfile.ZipFile(tmp,'w',compression=zipfile.ZIP_DEFLATED,compresslevel=6) as archive:
                    archive.writestr(MANIFEST,bundle.model_dump_json(indent=2))
                    for entry in files:
                        archive.write(stage/entry.relative_path,entry.relative_path)
                self.inspect(Path(tmp))
                with open(tmp,'rb') as stream:os.fsync(stream.fileno())
                # Exclusive final publication protects an existing bundle even from a racing exporter.
                os.link(tmp,destination)
            finally:
                Path(tmp).unlink(missing_ok=True)
            return bundle

    def import_bundle(self, archive):
        with self.validated(archive) as (bundle, staged, manifest):
            with self.library.files.locked():
                self.library._initialize()
                existing = self.library.database.lookup_book(str(manifest.book.book_id))
                if existing:
                    must(len(existing)==1 and existing[0].edition.edition_id==manifest.edition.edition_id,'BUNDLE_COLLISION: identity')
                    target=self.library.files.library/existing[0].book.slug
                    # Re-export current effective state to compare; no stale receipt can turn changed Human data into a no-op.
                    with TemporaryDirectory(prefix='bookbundle-collision-') as temp:
                        candidate=Path(temp)/'current.bookbundle.zip'
                        current = self._export_locked(str(bundle.book_id),candidate,bundle.source_included)
                        must(current.bundle_content_hash==bundle.bundle_content_hash,'BUNDLE_COLLISION: generation or Human state differs')
                    return bundle,target,True
                must(self.library.database.lookup_hash(manifest.source.sha256) is None,'BUNDLE_COLLISION: same Source under another identity')
                must(not self.library.database.lookup_book(manifest.book.slug),'BUNDLE_COLLISION: slug')
                destination=self.library.files.library/manifest.book.slug
                must(not destination.exists() and not destination.is_symlink(),'BUNDLE_COLLISION: destination exists')
                prepared=self.library.files.stage();published=None;committed=False
                try:
                    for entry in bundle.files:
                        path=prepared/entry.relative_path;path.parent.mkdir(parents=True,exist_ok=True)
                        shutil.copyfile(staged/entry.relative_path,path)
                    shutil.copyfile(staged/'.bundle-import.json',prepared/'.bundle-import.json')
                    restore_pointers(prepared,bundle.pointers)
                    with self.library.database.transaction() as connection:
                        self.library.database.insert_manifest(connection,manifest)
                        published=self.library.files.publish(prepared,manifest.book.slug)
                        # Check the actual destination using an independent temporary identity index before commit.
                        with self.staged_library(published,manifest) as ai:
                            self.validate_book(published,bundle,ai)
                    committed=True
                finally:
                    if not committed:
                        # A publication wrapper may raise after its atomic rename returned.
                        # This destination was absent under the same Library lock before staging.
                        cleanup = published or prepared
                        if published is None and not prepared.exists() and destination.is_dir() and not destination.is_symlink():
                            cleanup = destination
                        self.library.files.discard(cleanup)
                return bundle,published,False
