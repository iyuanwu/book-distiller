"""Per-book history recovery with shared SafeZip and durable orphan ownership."""
from contextlib import contextmanager, ExitStack
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4, UUID
import fcntl
import json
import os
import shutil
from book_distiller.backup.models import BookBackupManifest, BackupFile, DBExport
from book_distiller.backup.plan import plan, generations, validate_layout, READER_FILES
from book_distiller.backup.database import export_rows, insert_rows, normalize
from book_distiller.backup.validation import validate_state
from book_distiller.bundle.archive import ArchiveProfile, BundleLimits, content_hash, validate_archive, publish_archive
from book_distiller.bundle.service import BundleService, must, write, read, restore_pointers, make_receipt
from book_distiller.bundle.models import ExternalRule
from book_distiller.core.errors import StorageError
from book_distiller.core.ingest import IngestService
from book_distiller.core.version import get_version
from book_distiller.core.models import RunMetadata
from book_distiller.core.models.library import Manifest
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.pipeline.canonical import json_hash
from book_distiller.pipeline.files import write_json
from book_distiller.storage.filesystem import hash_source
from book_distiller.storage.journal import read_events
from book_distiller.human.resolver import EffectiveKnowledgeResolver
from book_distiller.renderers.service import RenderService
from book_distiller.renderers.model import ReaderViewModel
from book_distiller.renderers.html import emit

PROFILE=ArchiveProfile('backup_manifest.json',BookBackupManifest,'backup_content_hash')


def sync_directory(path):
    fd=os.open(path,os.O_RDONLY)
    try:os.fsync(fd)
    finally:os.close(fd)


class BackupService:
    def __init__(self,home,project,limits=BundleLimits()):
        self.home,self.project,self.limits=Path(home).resolve(),Path(project).resolve(),limits
        self.library=IngestService(self.home)

    @contextmanager
    def staged_ai(self,root,db):
        with TemporaryDirectory(prefix='bookbackup-index-') as temp:
            library=IngestService(Path(temp));library.database.initialize()
            with library.database.transaction() as c:insert_rows(c,db)
            class StagedAI(AITaskService):
                def _resolve(self,selector):
                    return Manifest.model_validate_json((root/'manifest.json').read_text()),root
            yield StagedAI(Path(temp),self.project)

    def receipt(self,root,value):
        make_receipt(root,{f.relative_path:f.sha256 for f in value.files},value.source_included,value.source_sha256,
                     [d.model_dump(mode='json') for d in value.external_rule_dependencies],value.backup_content_hash)
        receipt=read(root/'.bundle-import.json');receipt['recovery_artifact']=True
        write(root/'.bundle-import.json',receipt)

    def external_rules(self,root):
        from book_distiller.bundle.receipt import receipt
        from book_distiller.human.service import guidance
        from book_distiller.core.models.synthesis import BOOK_WORKFLOWS
        from book_distiller.core.models.verification import VERIFY_WORKFLOWS
        prior=receipt(root) or {}
        dependencies={r['rule_id']:ExternalRule.model_validate(r) for r in prior.get('external_rule_dependencies',[])}
        types=read(root/'analysis/classification.json') if (root/'analysis/classification.json').exists() else {}
        for workflow in {'classify','classify_book','extract_claims','build_chapter_atoms','ask_book'}|BOOK_WORKFLOWS|VERIFY_WORKFLOWS:
            for rule in guidance(self.home,root,workflow,[types.get('primary_type'),*types.get('secondary_types',[])])['rules']:
                if rule['scope']!='book':dependencies[rule['rule_id']]=ExternalRule(rule_id=rule['rule_id'],scope=rule['scope'],book_type=rule['book_type'],sha256=json_hash(rule),applicable_workflows=rule['applicable_workflows'])
        return sorted(dependencies.values(),key=lambda d:str(d.rule_id))

    def export(self,selector,destination,include_external_source=False):
        destination=Path(destination).expanduser().absolute()
        must(destination.name.endswith('.bookbackup.zip') and not destination.exists(),'backup output must be a new .bookbackup.zip')
        self.library._initialize()
        manifest,root=AITaskService(self.home,self.project)._resolve(selector)
        # Non-blocking leases avoid deadlock against Resume (run lease -> Library lock).
        with ExitStack() as leases:
            db=export_rows(self.library.database,manifest.book.book_id,manifest.edition.edition_id)
            for row in db.runs:
                lock=root/'runtime/runs'/str(row.run_id)/'execution.lock'
                if lock.parent.exists():
                    stream=leases.enter_context(lock.open('a+b'))
                    try:fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
                    except BlockingIOError as exc:raise StorageError('BACKUP_RUN_BUSY: pause the active run first') from exc
            with self.library.files.locked():
                must({r.run_id for r in export_rows(self.library.database,manifest.book.book_id,manifest.edition.edition_id).runs}=={r.run_id for r in db.runs},'BACKUP_RUN_CHANGED: retry snapshot')
                return self._export(manifest,root,destination,include_external_source)

    def _export(self,manifest,root,destination,include_external_source):
        names,pointers=plan(root)
        db=export_rows(self.library.database,manifest.book.book_id,manifest.edition.edition_id)
        included=(manifest.source.copy_mode=='copy' and (root/manifest.source.stored_path).is_file()) or include_external_source
        source_mode='copied' if manifest.source.copy_mode=='copy' else 'external'
        with TemporaryDirectory(prefix='bookbackup-export-') as temp:
            stage=Path(temp)/'book';stage.mkdir()
            for name in names:
                path=stage/name;path.parent.mkdir(parents=True,exist_ok=True)
                shutil.copyfile(root/name,path)
            portable=manifest.model_copy(deep=True)
            portable.source.copy_mode='copy';portable.source.stored_path='source/original'+manifest.source.extension
            portable.source.original_path=Path('/backup-source/original'+manifest.source.extension)
            write(stage/'manifest.json',portable.model_dump(mode='json'))
            edition=db.editions[0]
            edition.copy_mode='copy';edition.source_stored_path=portable.source.stored_path;edition.source_original_path=str(portable.source.original_path)
            if included:
                source=Path(manifest.source.stored_path) if manifest.source.copy_mode=='reference' else root/manifest.source.stored_path
                must(source.is_file() and not source.is_symlink(),'source unavailable')
                path=stage/portable.source.stored_path;path.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,path)
                names[portable.source.stored_path]='source'
            # SQLite execution_json is canonical; repair the crash-prone run.json projection.
            for row in db.runs:
                if row.execution_json:
                    name=f'runtime/runs/{row.run_id}/run.json';write(stage/name,json.loads(row.execution_json));names[name]='runtime'
            write(stage/'metadata/db-export.json',db.model_dump(mode='json'));names['metadata/db-export.json']='metadata'
            dependencies=self.external_rules(root)
            restore_pointers(stage,pointers)
            provisional=BookBackupManifest(created_by_version=get_version(),book_id=manifest.book.book_id,edition_id=manifest.edition.edition_id,
                title=manifest.edition.display_title,source_sha256=manifest.source.sha256,source_mode=source_mode,source_included=included,
                normalized_generation_id=read(stage/pointers['parsed']/'completion.json')['task_id'] if 'parsed' in pointers else None,
                run_task_inventory=db.run_tasks,generation_inventory=generations(names),human_inventory={n:len(read_events(stage/'human'/f'{n}.jsonl')) for n in ('overrides','notes','rules')},
                human_state_hash=EffectiveKnowledgeResolver(stage).display_hash(),run_inventory={str(r.run_id):r.status for r in db.runs},
                task_inventory={str(t.task_id):t.status.value for t in db.tasks},checkpoint_inventory=sorted(n for n in names if n.startswith('runtime/')),
                external_rule_dependencies=dependencies,pointers=pointers,files=[],backup_content_hash='0'*64)
            provisional.files=self.entries(stage,names);self.receipt(stage,provisional)
            # Re-project only a current Reader for portable Source links; preserve its generation ID.
            if 'output' in pointers and RenderService(self.home,self.project).describe(str(manifest.book.book_id),root)['Reader']=='completed':
                with self.staged_ai(stage,db) as ai:
                    service=RenderService(ai.library.files.root,self.project);service.ai=ai
                    _,metadata,args=service.inputs(str(manifest.book.book_id))
                    output=stage/pointers['output'];original=read(output/'render_manifest.json')
                    shutil.rmtree(output)
                    metrics=emit(ReaderViewModel.load(stage,*args),output,self.project,args[-1])
                    metadata|={k:original[k] for k in ('render_generation_id','generated_at','level_chars')}
                    metadata|={'metrics':metrics,'artifact_hashes':{p.relative_to(output).as_posix():hash_source(p)[0] for p in output.rglob('*') if p.is_file() and p.name!='render_manifest.json'}}
                    write(output/'render_manifest.json',metadata)
            elif 'output' in pointers:
                # A stale Reader remains a historical view, but never carries executable
                # archive-supplied code or old-machine Source links into a restore.
                from book_distiller.bundle.receipt import source_display
                output=stage/pointers['output'];metadata=read(output/'render_manifest.json')
                data={Path(n).stem:read(output/n) for n in READER_FILES if n.startswith('data/') and (output/n).exists()}
                data['book']['source']=source_display(stage,portable)
                shutil.rmtree(output)
                metrics=emit(ReaderViewModel(data),output,self.project,read(self.project/'rules/reader/standard.json'))
                metadata.update(metrics=metrics,artifact_hashes={p.relative_to(output).as_posix():hash_source(p)[0] for p in output.rglob('*') if p.is_file()})
                write(output/'render_manifest.json',metadata)
            provisional.files=self.entries(stage,names)
            provisional.backup_content_hash=content_hash(provisional,PROFILE.hash_field)
            publish_archive(destination,stage,provisional,self.inspect,PROFILE)
            return provisional

    @staticmethod
    def entries(root,names):
        return [BackupFile(relative_path=n,category=c,sha256=hash_source(root/n)[0],size=(root/n).stat().st_size) for n,c in sorted(names.items())]

    @contextmanager
    def validated(self,archive):
        with TemporaryDirectory(prefix='bookbackup-inspect-') as temp:
            root=Path(temp)/'book';root.mkdir()
            value=validate_archive(archive,root,self.limits,PROFILE)
            try:
                validate_layout(value)
                db=DBExport.model_validate_json((root/'metadata/db-export.json').read_text())
                restore_pointers(root,value.pointers);self.receipt(root,value)
                manifest=validate_state(root,value,db)
                with self.staged_ai(root,db) as ai:self.check_current(root,value,ai)
            except StorageError:raise
            except (ValueError,OSError,KeyError,TypeError) as exc:raise StorageError('BACKUP_INVALID: '+str(exc)) from exc
            yield value,root,manifest,db

    def inspect(self,archive):
        with self.validated(archive) as (value,*_):return value

    def check_current(self,root,value,ai):
        from book_distiller.storage import book_knowledge,verification,reader
        from book_distiller.pipeline.canonical import load_canonical
        if 'parsed' in value.pointers:load_canonical(root,ai._resolve(str(value.book_id))[0],ai.library.database)
        book_knowledge.current(root);verification.current(root)
        if 'output' in value.pointers:must(reader.intact(reader.current(root)),'Reader integrity')
        # Readiness may legitimately be stale in a recovery artifact, never invent PASS.
        from book_distiller.ask.service import AskService
        service=RenderService(ai.library.files.root,self.project);service.ai=ai
        status=service.describe(str(value.book_id),root)['Reader']
        if 'output' in value.pointers:
            if status=='completed':
                _,metadata,args=service.inputs(str(value.book_id))
                view=ReaderViewModel.load(root,*args);rules=args[-1]
            else:
                output=reader.current(root)
                data={Path(n).stem:read(output/n) for n in READER_FILES if n.startswith('data/') and (output/n).exists()}
                from book_distiller.bundle.receipt import source_display
                must(data['book']['source']==source_display(root,ai._resolve(str(value.book_id))[0]),'stale Reader source projection')
                view=ReaderViewModel(data);rules=read(self.project/'rules/reader/standard.json')
            with TemporaryDirectory(prefix='bookbackup-reader-check-') as temp:
                output=Path(temp)/'reader'
                emit(view,output,self.project,rules)
                expected={p.relative_to(output).as_posix():hash_source(p)[0] for p in output.rglob('*') if p.is_file()}
                must(expected==read(reader.current(root)/'render_manifest.json')['artifact_hashes'],'Reader canonical/safe projection')
        return {'Reader':status,**AskService(ai).describe(str(value.book_id))}

    def prepare(self,staged,prepared,value,db):
        for entry in value.files:
            path=prepared/entry.relative_path;path.parent.mkdir(parents=True,exist_ok=True)
            shutil.copyfile(staged/entry.relative_path,path)
        restore_pointers(prepared,value.pointers);self.receipt(prepared,value)
        recovered=normalize(db)
        for row in recovered.runs:
            if row.execution_json:write(prepared/f'runtime/runs/{row.run_id}/run.json',json.loads(row.execution_json))
        write(prepared/'metadata/db-export.json',recovered.model_dump(mode='json'))
        recovery={'version':'1.0','backup_content_hash':value.backup_content_hash,'original_run_states':value.run_inventory,'original_task_states':value.task_inventory,
                  'files':{f.relative_path:hash_source(prepared/f.relative_path)[0] for f in value.files},'pointers':value.pointers,'db_hash':json_hash(recovered.model_dump(mode='json'))}
        write(prepared/'.backup-restore.json',recovery)
        # Fsync selected files plus directory metadata before publication/intent recovery.
        for path in [prepared/f.relative_path for f in value.files]+[prepared/'.backup-restore.json',prepared/'.bundle-import.json']:
            with path.open('rb') as stream:os.fsync(stream.fileno())
        for path in sorted({p.parent for p in prepared.rglob('*')},key=lambda p:len(p.parts),reverse=True):sync_directory(path)
        return recovered

    def same_state(self,root,value,db=None,expected_db=None):
        marker=root/'.backup-restore.json'
        must(marker.is_file() and not marker.is_symlink(),'RESTORE_COLLISION: missing recovery provenance')
        saved=read(marker)
        must(saved['backup_content_hash']==value.backup_content_hash,'RESTORE_COLLISION: different backup')
        expected={f.relative_path for f in value.files}
        expected_hashes={f.relative_path:f.sha256 for f in value.files}
        if expected_db is not None:
            import hashlib
            def digest(value):return hashlib.sha256((json.dumps(value,ensure_ascii=False,indent=2)+'\n').encode()).hexdigest()
            recovered=normalize(expected_db)
            expected_hashes['metadata/db-export.json']=digest(recovered.model_dump(mode='json'))
            for row in recovered.runs:
                if row.execution_json:expected_hashes[f'runtime/runs/{row.run_id}/run.json']=digest(json.loads(row.execution_json))
            must(saved['files']==expected_hashes,'RESTORE_COLLISION: recovery file projection')
            must(saved['db_hash']==json_hash(recovered.model_dump(mode='json')),'RESTORE_COLLISION: recovery DB projection')
        must(set(saved['files'])==expected,'RESTORE_COLLISION: inventory')
        for name,digest in saved['files'].items():
            path=root/name
            must(not path.is_symlink() and path.resolve().is_relative_to(root.resolve()) and not any(p.is_symlink() for p in path.parents if p != root and p.is_relative_to(root)) and path.is_file() and hash_source(path)[0]==digest,'RESTORE_COLLISION: changed '+name)
        current,pointers=plan(root)
        must(set(current)<=expected and pointers==value.pointers,'RESTORE_COLLISION: new history or pointers')
        if db is not None:must(json_hash(db.model_dump(mode='json'))==saved['db_hash'],'RESTORE_COLLISION: DB/Run history')

    def restore(self,archive):
        with self.validated(archive) as (value,staged,manifest,db):
            with self.library.files.locked():
                self.library.database.initialize()
                destination=self.library.files.library/manifest.book.slug
                intents=self.home/'data/restore-intents'
                must(not intents.is_symlink() and not (self.home/'data/restore-recovery').is_symlink(),'RESTORE_UNSAFE_RECOVERY_DIRECTORY')
                intents.mkdir(exist_ok=True)
                intent=intents/f'{value.book_id}.json'
                existing=self.library.database.lookup_book(str(value.book_id))
                if existing:
                    must(len(existing)==1 and existing[0].edition.edition_id==value.edition_id,'RESTORE_COLLISION: identity')
                    self.same_state(destination,value,export_rows(self.library.database,value.book_id,value.edition_id),db)
                    if intent.exists():intent.unlink();sync_directory(intents)
                    return value,destination,True
                if destination.exists() or destination.is_symlink():
                    must(intent.is_file() and not intent.is_symlink() and not destination.is_symlink(),'RESTORE_ORPHAN_UNOWNED: preserve existing directory')
                    ownership=read(intent)
                    must(ownership['book_id']==str(value.book_id) and ownership['slug']==manifest.book.slug and ownership['backup_content_hash']==value.backup_content_hash,'RESTORE_ORPHAN_CONFLICT')
                    self.same_state(destination,value,expected_db=db)
                    # Preserve the orphan as forensic recovery evidence, never delete unknown files.
                    quarantine=self.home/'data/restore-recovery'/str(UUID(ownership['transaction_id']));quarantine.parent.mkdir(exist_ok=True)
                    must(not quarantine.exists(),'RESTORE_RECOVERY_DESTINATION_EXISTS')
                    destination.rename(quarantine);sync_directory(self.library.files.library)
                    intent.unlink();sync_directory(intents)
                if intent.exists():
                    ownership=read(intent)
                    must(ownership['book_id']==str(value.book_id) and ownership['slug']==manifest.book.slug and ownership['backup_content_hash']==value.backup_content_hash,'RESTORE_ORPHAN_CONFLICT')
                    name=ownership['staging_name']
                    must(name.startswith('.ingest-') and Path(name).name==name,'RESTORE_UNSAFE_INTENT')
                    abandoned=self.library.files.library/name
                    if abandoned.exists():
                        must(abandoned.is_dir() and not abandoned.is_symlink(),'RESTORE_UNSAFE_STAGING')
                        quarantine=self.home/'data/restore-recovery'/str(UUID(ownership['transaction_id']));quarantine.parent.mkdir(exist_ok=True)
                        must(not quarantine.exists(),'RESTORE_RECOVERY_DESTINATION_EXISTS')
                        abandoned.rename(quarantine)
                    intent.unlink();sync_directory(intents)
                self.library._initialize()
                must(self.library.database.lookup_hash(manifest.source.sha256) is None,'RESTORE_COLLISION: Source identity')
                must(not self.library.database.lookup_book(manifest.book.slug),'RESTORE_COLLISION: slug')
                prepared=self.library.files.stage();committed=False;published=None
                ownership={'version':'1.0','transaction_id':str(uuid4()),'book_id':str(value.book_id),'slug':manifest.book.slug,'backup_content_hash':value.backup_content_hash,'staging_name':prepared.name}
                try:
                    write_json(intent,ownership);sync_directory(intents)
                    recovered=self.prepare(staged,prepared,value,db)
                    with self.library.database.transaction() as connection:
                        published=self.library.files.publish(prepared,manifest.book.slug);sync_directory(self.library.files.library)
                        insert_rows(connection,recovered)
                        self.post_validate(published,value,recovered)
                    committed=True
                    intent.unlink();sync_directory(intents)
                finally:
                    if not committed:
                        cleanup=published or prepared
                        if published is None and not prepared.exists() and destination.is_dir():cleanup=destination
                        self.library.files.discard(cleanup)
                        intent.unlink(missing_ok=True)
                return value,published,False

    def post_validate(self,root,value,db):
        self.same_state(root,value,db)
        recovered=value.model_copy(update={'run_inventory':{str(r.run_id):r.status for r in db.runs},'task_inventory':{str(t.task_id):t.status.value for t in db.tasks}})
        validate_state(root,recovered,db)
        with self.staged_ai(root,db) as ai:self.check_current(root,recovered,ai)
