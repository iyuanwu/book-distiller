"""User executions over existing checkpoint adapters; Codex remains the only AI."""
import json
import fcntl
from contextlib import contextmanager
from datetime import datetime, timezone
from uuid import UUID
from pathlib import Path
from book_distiller.core.models import RunMetadata
from book_distiller.core.enums import BookStatus, StageStatus
from book_distiller.pipeline.dependencies import RerunPlanner, GRAPH_VERSION
from book_distiller.pipeline.ai_tasks import safe_child, PreparedTask
from book_distiller.pipeline.canonical import json_hash
from book_distiller.pipeline.files import write_json
from book_distiller.pipeline.result_validation import ProtocolError, validate_result
from book_distiller.storage.journal import append_event
from book_distiller.human.resolver import EffectiveKnowledgeResolver


def now():return datetime.now(timezone.utc)


class RunService:
    def __init__(self,ai):self.ai=ai;self.db=ai.library.database

    def resolve(self,selector):
        self.ai.library._initialize()
        return self.ai._resolve(selector)

    def snapshot(self,directory):
        result={}
        for stage,path,file,key in [('parse','parsed','completion.json','task_id'),('book','knowledge/book','book_model.json','generation_id'),('verification','verification/current','manifest.json','generation_id'),('render','output','render_manifest.json','render_generation_id')]:
            p=directory/path/file
            if p.exists():result[stage]=json.loads(p.read_text())[key]
        p=directory/'analysis/classification.json'
        if p.exists():result['classification']=json.loads(p.read_text())['task_id']
        folder=directory/'knowledge/chapters'
        if folder.exists():
            for path in sorted(folder.iterdir()):
                meta=json.loads((path/'chapter.json').read_text())['generation']
                for stage in ('claims','atoms'):result[stage+':'+path.name]=meta['generation_id']
        return result

    def plan(self,selector,stage,chapter=None,through='render'):
        # Existing DB read only: dry-run must not migrate, lock, or write any file.
        manifest,directory=self.ai._resolve(selector)
        path=directory/'parsed/normalized/book.json'
        chapters=[c['chapter_id'] for c in json.loads(path.read_text())['chapters']] if path.exists() else []
        protected=EffectiveKnowledgeResolver(directory).protected()
        plan = RerunPlanner().plan(stage,chapters,chapter,through,self.snapshot(directory),[p['target_ref'] for p in protected])
        reused_tasks=set()
        for key in plan.reused_generations:
            stage,_,chapter_id=key.partition(':')
            if stage in ('parse','classification'):
                reused_tasks.add(plan.reused_generations[key])
            elif stage in ('claims','atoms'):
                generation=json.loads((directory/f'knowledge/chapters/{chapter_id}/chapter.json').read_text())['generation']
                reused_tasks.update(generation['claim_tasks'].values())
                if generation.get('atom_task'):reused_tasks.add(generation['atom_task'])
            elif stage in ('book','verification'):
                path=directory/('knowledge/book' if stage=='book' else 'verification/current')/'provenance.json'
                if path.exists():reused_tasks.update(json.loads(path.read_text()).get('tasks',[]))
        plan.tasks_to_reuse=sorted(reused_tasks)
        return plan

    def root(self,directory,rid):return safe_child(directory,f'runtime/runs/{UUID(str(rid))}')

    def event(self,directory,run,kind,**data):
        from book_distiller.pipeline.run_events import RunEvent
        append_event(self.root(directory,run.run_id)/'events.jsonl',RunEvent(event=kind,details=data).model_dump(mode='json'))

    def save(self,directory,run):
        run.updated_at=now();payload=run.model_dump(mode='json')
        # SQLite is canonical execution state; JSON is a repairable journal projection.
        with self.db.transaction() as c:
            c.execute('UPDATE runs SET status=?, updated_at=?, execution_json=? WHERE run_id=?',
                      (run.status.value,run.updated_at.isoformat(),json.dumps(payload),str(run.run_id)))
        write_json(self.root(directory,run.run_id)/'run.json',payload)

    def create(self,selector,stage,chapter=None,through='render'):
        with self.ai.library.files.locked():
            manifest,directory=self.resolve(selector);plan=self.plan(selector,stage,chapter,through)
            run=RunMetadata(book_id=manifest.book.book_id,edition_id=manifest.edition.edition_id,operation='rerun',
                requested_scope={'from':plan.requested_stage,**plan.requested_scope},resolved_plan=plan.model_dump(),
                source_sha256=manifest.source.sha256,normalized_generation_id=self.snapshot(directory).get('parse'))
            root=self.root(directory,run.run_id);root.mkdir(parents=True)
            write_json(root/'plan.json',plan.model_dump())
            with self.db.transaction() as c:self.db.insert_run(c,run)
            self.save(directory,run);self.event(directory,run,'RUN_CREATED')
            from book_distiller.pipeline.staleness import invalidate
            invalidate(directory,run.resolved_plan,run.run_id)
            self.event(directory,run,'DOWNSTREAM_INVALIDATED',stages=plan.downstream_stale)
        return self.resume(selector,str(run.run_id))

    @contextmanager
    def lease(self,directory,rid):
        with (self.root(directory,rid)/'execution.lock').open('a+b') as handle:
            try:fcntl.flock(handle,fcntl.LOCK_EX|fcntl.LOCK_NB)
            except BlockingIOError as exc:raise ProtocolError('RUN_BUSY','Run is executing in another local process') from exc
            try:yield
            finally:fcntl.flock(handle,fcntl.LOCK_UN)

    def load(self,manifest,rid=None):
        with self.db.connect() as c:
            if rid:
                row=c.execute('SELECT execution_json FROM runs WHERE edition_id=? AND run_id=?',(str(manifest.edition.edition_id),str(UUID(rid)))).fetchone()
            else:
                row=c.execute("SELECT execution_json FROM runs WHERE edition_id=? AND status IN ('pending','processing','paused','failed','needs_review') AND execution_json IS NOT NULL ORDER BY created_at DESC LIMIT 1",(str(manifest.edition.edition_id),)).fetchone()
        if not row or not row[0]:raise ProtocolError('NO_RESUMABLE_RUN','No resumable run found.')
        return RunMetadata.model_validate_json(row[0])

    def resume(self,selector,rid=None):
        manifest,directory=self.resolve(selector);run=self.load(manifest,rid)
        with self.lease(directory,run.run_id):
            run=self.load(manifest,str(run.run_id))
            if run.status==BookStatus.COMPLETED:return run,[]
            if run.pipeline_graph_version!=GRAPH_VERSION:raise ProtocolError('STALE_RUN','Pipeline graph version changed')
            if run.source_sha256!=manifest.source.sha256:raise ProtocolError('STALE_RUN','Source identity changed')
            current_parse=self.snapshot(directory).get('parse')
            if run.normalized_generation_id and current_parse!=run.normalized_generation_id and not (run.cursor < len(run.resolved_plan['steps']) and run.resolved_plan['steps'][run.cursor]['stage']=='parse'):
                plan=self.plan(selector,'classification',through=run.resolved_plan['requested_scope']['through'])
                for checkpoint in run.checkpoints.values():
                    for tid in checkpoint['tasks']:self.event(directory,run,'TASK_INVALIDATED',task_id=tid,reason='Normalized generation changed')
                run.resolved_plan=plan.model_dump();run.checkpoints={};run.cursor=0
                run.normalized_generation_id=current_parse
                write_json(self.root(directory,run.run_id)/'plan.json',run.resolved_plan)
            self.event(directory,run,'RUN_RESUMED' if run.started_at else 'RUN_STARTED')
            run.started_at=run.started_at or now();run.status=BookStatus.PROCESSING;self.save(directory,run)
            try:
                # Planning is metadata-only. Evidence hashes are checked at execution.
                from book_distiller.human.service import HumanService
                resolver=EffectiveKnowledgeResolver(directory)
                for obj in resolver.protected():
                    saved=resolver.state[json_hash(obj['target_ref'])]
                    if HumanService(self.ai).anchors(directory,obj['value'])!=saved.get('anchors',{}):
                        raise ProtocolError('LOCK_DEPENDENCY_CONFLICT',str(obj['target_ref']))
                if run.resolved_plan.get('blocking_lock_conflicts'):
                    raise ProtocolError('LOCK_DEPENDENCY_CONFLICT',str(run.resolved_plan['blocking_lock_conflicts']))
                steps=run.resolved_plan['steps']
                while run.cursor<len(steps):
                    step=steps[run.cursor];key=str(run.cursor)
                    checkpoint=run.checkpoints.setdefault(key,{'started':False,'tasks':[]})
                    try:
                        self.validate_checkpoints(directory,run,checkpoint)
                    except ProtocolError as exc:
                        if exc.code!='STALE_CONTEXT':raise
                        for tid in checkpoint['tasks']:self.event(directory,run,'TASK_INVALIDATED',task_id=tid,reason=str(exc))
                        checkpoint['tasks']=[];checkpoint['started']=False
                        self.save(directory,run)
                    pending=self.advance(selector,directory,run,step,checkpoint)
                    if pending:
                        checkpoint['tasks']=list(dict.fromkeys(checkpoint['tasks']+[str(t.task_id) for t in pending]))
                        with self.db.transaction() as c:
                            for task in pending:c.execute('INSERT OR IGNORE INTO run_tasks VALUES (?,?,?,?)',(str(run.run_id),str(task.task_id),step['stage'],step['chapter']))
                        for task in pending:self.event(directory,run,'TASK_PREPARED',task_id=str(task.task_id))
                        run.status=BookStatus.PAUSED;self.save(directory,run);self.event(directory,run,'RUN_PAUSED',reason='Awaiting Codex results')
                        return run,pending
                    if step['stage']=='parse':
                        run.normalized_generation_id=self.snapshot(directory).get('parse')
                        refreshed=self.plan(selector,'parse',through=run.resolved_plan['requested_scope']['through'])
                        run.resolved_plan=refreshed.model_dump();steps=run.resolved_plan['steps']
                        write_json(self.root(directory,run.run_id)/'plan.json',run.resolved_plan)
                    checkpoint['completed']=True
                    from book_distiller.pipeline.staleness import complete
                    complete(directory,step['stage'],step['chapter'])
                    self.event(directory,run,'CLAIMS_READY' if step['stage']=='claims' else 'GENERATION_PUBLISHED',stage=step['stage'],chapter=step['chapter'])
                    run.cursor+=1;self.save(directory,run)
                run.status=BookStatus.COMPLETED;run.completed_at=now();run.failure_summary=None
                self.save(directory,run);self.event(directory,run,'RUN_COMPLETED');return run,[]
            except BaseException as exc:
                run.status=BookStatus.NEEDS_REVIEW if 'LOCK_DEPENDENCY_CONFLICT' in str(exc) else BookStatus.PAUSED if isinstance(exc,KeyboardInterrupt) else BookStatus.FAILED
                run.failure_summary=str(exc);self.save(directory,run)
                self.event(directory,run,'RUN_PAUSED' if isinstance(exc,KeyboardInterrupt) else 'RUN_FAILED',reason=str(exc))
                raise

    def validate_checkpoints(self,directory,run,checkpoint):
        from book_distiller.pipeline.dependencies import ResumePlanner
        indexed=[self.db.lookup_task(UUID(tid)) for tid in checkpoint['tasks']]
        if any(task is None for task in indexed):
            raise ProtocolError('STALE_CONTEXT','Checkpoint task is missing from SQLite')
        checkpoint['eligibility']=ResumePlanner().plan(indexed)
        for task in indexed:
            tid=task['task_id'];path=safe_child(directory,f'runtime/tasks/{tid}')
            if task['status'] in ('running','processing'):
                self.db.update_task_status(UUID(tid),StageStatus.PENDING);task['status']='pending'
            request,context,_=self.ai._validate_package(task,path,directory)
            if task['status']=='pending' and (path/'apply.json').exists() and (path/'result.json').exists():
                # Durable apply intent: replay deterministic submit, never ask Codex again.
                result=validate_result((path/'result.json').read_text(),request,context)
                receipt=json.loads((path/'apply.json').read_text())
                if json_hash(result.model_dump(mode='json',exclude={'created_at'}))!=receipt['result_hash']:
                    raise ProtocolError('STALE_CONTEXT','Pending apply receipt mismatch')
                self.ai.submit(UUID(tid),path/'result.json')
                task['status']='completed'
            if task['status']=='completed':
                accepted=path/'accepted.json' if task['task_type']!='classify_book' else directory/f'analysis/classification-generations/{tid}.json'
                result=validate_result(accepted.read_text(),request,context)
                receipt=json.loads((path/'apply.json').read_text())
                if json_hash(result.model_dump(mode='json',exclude={'created_at'}))!=receipt['result_hash']:
                    raise ProtocolError('STALE_CONTEXT','Accepted checkpoint hash mismatch')
                completed=checkpoint.setdefault('completed_tasks',[])
                self.event(directory,run,'TASK_REUSED' if tid in completed else 'TASK_COMPLETED',task_id=tid)
                if tid not in completed:completed.append(tid)

    def advance(self,selector,directory,run,step,checkpoint):
        from book_distiller.pipeline.chapter_tasks import ChapterTasks
        from book_distiller.pipeline.book_tasks import BookTasks
        from book_distiller.pipeline.verification_tasks import VerificationTasks
        from book_distiller.core.parse import ParseService
        from book_distiller.renderers.service import RenderService
        stage,ch=step['stage'],step['chapter'];fresh=not checkpoint['started']
        marker = {'claims':f'runtime/chapter-work/{ch}.json','atoms':f'runtime/chapter-work/{ch}.json',
                  'book':'runtime/book-synthesis.json','verification':'runtime/verification-active.json'}.get(stage)
        active=json.loads((directory/marker).read_text()) if marker and (directory/marker).exists() else None
        if checkpoint['started'] and not checkpoint['tasks'] and not checkpoint.get('adapter_finished') and marker:
            fresh=active==checkpoint.get('active_before')
        # Persist the intent before execution. A generation pointer saved below resolves replay.
        if fresh:
            checkpoint['active_before']=active
            checkpoint['before']=self.snapshot(directory)
            checkpoint['started']=True;self.save(directory,run)
        if stage=='parse':
            service=ParseService(self.ai.library.files.root)
            if not fresh:
                service.recover(selector)
            already_published=self.snapshot(directory).get('parse')!=checkpoint['before'].get('parse')
            service.parse(selector,force=fresh and not already_published);return []
        if stage=='classification':
            if checkpoint['tasks']:
                tid=checkpoint['tasks'][0]
                if self.db.task_status(UUID(tid))=='completed':return []
                from book_distiller.pipeline.knowledge_context import parse_context
                path=safe_child(directory,f'runtime/tasks/{tid}')
                return [PreparedTask(UUID(tid),path,parse_context((path/'context.json').read_text()))]
            task=self.ai.prepare('classify',selector);return [task]
        if stage=='claims':
            _,tasks=ChapterTasks(self.ai).prepare_claims(selector,ch,force=fresh);return tasks
        if stage=='atoms':
            if fresh and run.resolved_plan['requested_stage']=='atoms':
                ChapterTasks(self.ai).fork_claims(selector,ch)
            task=ChapterTasks(self.ai).prepare_atoms(selector,ch)
            if self.db.task_status(task.task_id)=='completed':
                from book_distiller.human.locks import rebase_chapter
                rebase_chapter(self.ai,directory,task.context.generation)
                return []
            return [task]
        if stage=='book':result=BookTasks(self.ai).prepare(selector,force=fresh)
        elif stage=='verification':result=VerificationTasks(self.ai).prepare(selector,force=fresh)
        else:
            RenderService(self.ai.library.files.root,self.ai.project).render(selector,force=fresh);return []
        return [] if isinstance(result,Path) else [result]
