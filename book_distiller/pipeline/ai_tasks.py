"""Deterministic prepare/submit/apply protocol. Python never performs AI reasoning."""
from dataclasses import dataclass
from datetime import datetime, timezone
import json
from pathlib import Path
import shutil
from uuid import UUID
from book_distiller.core.errors import BookDistillerError, StorageError
from book_distiller.core.ingest import IngestService
from book_distiller.core.models import TaskRecord
from book_distiller.core.models.ai_tasks import AIRequest, BookClassification, ContextBudget, ContextPackage, SelectionSpec
from book_distiller.pipeline.canonical import load_canonical, CanonicalDocument, canonical_json, json_hash
from book_distiller.pipeline.context_package import build_context_package, context_digest, render_context, measure
from book_distiller.pipeline.result_validation import ProtocolError, validate_result
from book_distiller.pipeline.workflows import load_workflow
from book_distiller.storage.filesystem import hash_source
from book_distiller.pipeline.files import write_json, write_bytes


@dataclass(frozen=True)
class PreparedTask:
    task_id: UUID
    directory: Path
    context: ContextPackage


def safe_child(root: Path, relative: str) -> Path:
    """Reject symlinked runtime/analysis paths instead of writing outside this book."""
    path = root / relative
    current = root
    for part in Path(relative).parts:
        current /= part
        if current.is_symlink():
            raise StorageError(f"Unsafe symlink in workflow path: {current}")
    if not path.resolve().is_relative_to(root.resolve()):
        raise StorageError("Workflow path escapes its Library book root")
    return path


class AITaskService:
    def __init__(self, root: Path, project: Path):
        self.library = IngestService(root)
        self.project = project

    def _resolve(self, selector: str):
        records = self.library.database.lookup_book(selector)
        if len(records) != 1:
            raise ProtocolError("BOOK_NOT_UNIQUE", "Use an exact book ID or slug with one indexed Edition.")
        manifest = records[0]
        directory = self.library.files.library / manifest.book.slug
        if directory.is_symlink() or self.library.files.read_manifest(directory) != manifest:
            raise StorageError("Manifest/index mismatch or unsafe Book directory")
        return manifest, directory

    def prepare(self, name: str, selector: str, *, budget: ContextBudget | None = None,
                selection: SelectionSpec | None = None) -> PreparedTask:
        """Create a pending task and immutable request/context/schema artifacts."""
        try:
            with self.library.files.locked():
                self.library._initialize()
                if name != 'classify':
                    raise ProtocolError('CHAPTER_SCOPE_REQUIRED','Use analyze claims/atoms with --chapter for knowledge tasks')
                return self._prepare_locked(name, selector, budget=budget, selection=selection)
        except BookDistillerError:
            raise
        except Exception as exc:
            raise StorageError(f"Prepare failed: {exc}") from exc

    def _prepare_locked(self, name, selector, *, budget=None, selection=None, knowledge=None):
        workflow = load_workflow(self.project, name, knowledge['types'] if knowledge else None)
        manifest, directory = self._resolve(selector)
        document = load_canonical(directory, manifest, self.library.database)
        task = TaskRecord(edition_id=manifest.edition.edition_id, task_type=workflow.task_type)
        if knowledge:
            from book_distiller.pipeline.knowledge_context import build_knowledge_context
            context = build_knowledge_context(document,task.task_id,workflow,**knowledge['context'])
        else:
            context = build_context_package(document, task.task_id, workflow, selection, budget)
        schema = workflow.output_schema()
        request = AIRequest(task_id=task.task_id, task_type=workflow.task_type, context_package_version=context.package_version, book_id=manifest.book.book_id,
            edition_id=manifest.edition.edition_id, source_sha256=manifest.source.sha256,
            normalized_document=document.fingerprint, workflow_version=workflow.workflow_version,
            prompt_version=workflow.prompt_version, context_hash=context.context_hash,
            workflow_sha256=hash_source(workflow.workflow_path)[0], prompt_sha256=workflow.prompt_hash(),
            output_schema_sha256=json_hash(schema), created_at=task.created_at)
        tasks = safe_child(directory, "runtime/tasks")
        tasks.mkdir(parents=True, exist_ok=True)
        stage, destination = tasks / f".prepare-{task.task_id}", tasks / str(task.task_id)
        stage.mkdir()
        published = False
        try:
            write_json(stage / "request.json", request)
            # Compact canonical JSON is the exact representation used for budget accounting.
            (stage / "context.json").write_text(canonical_json(context.model_dump(mode="json")), encoding="utf-8")
            (stage / "context.md").write_text(render_context(context), encoding="utf-8")
            write_json(stage / "output.schema.json", schema)
            shutil.copyfile(workflow.workflow_path, stage / "workflow.md")
            (stage / "prompt.md").write_text(workflow.prompt_text(), encoding="utf-8")
            with self.library.database.transaction() as connection:
                self.library.database.insert_task(connection, task)
                stage.rename(destination)
                published = True
            return PreparedTask(task.task_id, destination, context)
        except BaseException:
            shutil.rmtree(destination if published else stage)
            raise

    def _validate_package(self, task: dict, task_dir: Path, directory: Path) -> tuple[AIRequest, ContextPackage, CanonicalDocument]:
        try:
            request = AIRequest.model_validate_json((task_dir / "request.json").read_text(encoding="utf-8"))
            from book_distiller.pipeline.knowledge_context import parse_context
            context = parse_context((task_dir / "context.json").read_text(encoding="utf-8"))
            if (str(request.task_id), str(request.book_id), str(request.edition_id), request.task_type) != (task["task_id"], task["book_id"], task["edition_id"], task["task_type"]):
                raise ValueError("Request does not match SQLite task identity")
            if context_digest(context) != context.context_hash or context.context_hash != request.context_hash:
                raise ValueError("Context hash does not match request")
            if (context.task_id, context.task_type, context.book_id, context.edition_id, context.source_sha256,
                context.normalized_document, context.workflow_version, context.prompt_version) != (
                request.task_id, request.task_type, request.book_id, request.edition_id, request.source_sha256,
                request.normalized_document, request.workflow_version, request.prompt_version):
                raise ValueError("Context identity/provenance does not match request")
            if (task_dir / "context.md").read_text(encoding="utf-8") != render_context(context):
                raise ValueError("Context Markdown is not the projection of context.json")
            if measure(context) != context.budget.selected_chars or measure(context)>context.budget.limits.max_chars:
                raise ValueError("Context budget accounting mismatch")
            types = [context.classification['primary_type'], *context.classification['secondary_types']] if task['task_type'] != 'classify_book' else None
            workflow = load_workflow(self.project, 'classify' if task['task_type']=='classify_book' else task['task_type'], types)
            expected_resources = (hash_source(workflow.workflow_path)[0], workflow.prompt_hash(), json_hash(workflow.output_schema()))
            if expected_resources != (request.workflow_sha256, request.prompt_sha256, request.output_schema_sha256):
                raise ValueError("Workflow/prompt/schema changed since prepare")
            if (hash_source(task_dir / "workflow.md")[0], hash_source(task_dir / "prompt.md")[0], json_hash(json.loads((task_dir / "output.schema.json").read_text()))) != expected_resources:
                raise ValueError("Runtime workflow/prompt/schema snapshots changed")
            manifest, _ = self._resolve(task["book_id"])
            document = load_canonical(directory, manifest, self.library.database)
            if request.normalized_document != document.fingerprint or request.source_sha256 != manifest.source.sha256:
                raise ValueError("Normalized document/source changed since prepare")
            # Rebuild the bounded selection to detect internally consistent but altered runtime data.
            if task['task_type']=='classify_book':
                rebuilt = build_context_package(document, request.task_id, workflow, context.selection.spec, context.budget.limits)
            else:
                from book_distiller.pipeline.chapter_tasks import ChapterTasks
                rebuilt = ChapterTasks(self).rebuild(directory, document, context, workflow)
            if rebuilt.context_hash != context.context_hash:
                raise ValueError("Context no longer matches its Canonical selection")
            return request, context, document
        except Exception as exc:
            raise ProtocolError("STALE_CONTEXT", f"{exc}. Prepare a new task.") from exc

    def submit(self, task_id: UUID, result_path: Path) -> Path:
        """Validate completely before publishing; rejected results leave tasks retryable."""
        try:
            with self.library.files.locked():
                self.library._initialize()
                task = self.library.database.lookup_task(task_id)
                if task is None or task["task_type"] not in {"classify_book","extract_claims","build_chapter_atoms"}:
                    raise ProtocolError("TASK_NOT_FOUND", "No indexed AI task with that ID.")
                manifest, directory = self._resolve(task["book_id"])
                task_dir = safe_child(directory, f"runtime/tasks/{task_id}")
                if not task_dir.is_dir():
                    raise ProtocolError("TASK_ARTIFACTS_MISSING", "The runtime task artifacts are unavailable.")
                for name in ("request.json", "context.json", "context.md", "output.schema.json", "workflow.md", "prompt.md", "result.json", "validation.json", "apply.json"):
                    safe_child(directory, f"runtime/tasks/{task_id}/{name}")
                canonical = safe_child(directory, "analysis/classification.json") if task['task_type']=='classify_book' else task_dir/'accepted.json'
                if task['task_type']!='classify_book':
                    safe_child(directory,f'runtime/tasks/{task_id}/accepted.json')
                try:
                    if result_path.stat().st_size > 128 * 1024:
                        raise ProtocolError("RESULT_SCHEMA_INVALID", "Result exceeds 128 KiB limit.")
                    try:
                        text = result_path.read_text(encoding="utf-8")
                    except UnicodeError as exc:
                        raise ProtocolError("RESULT_SCHEMA_INVALID", "Result must be UTF-8 JSON.") from exc
                    request, context, document = self._validate_package(task, task_dir, directory)
                    result = validate_result(text, request, context)
                except ProtocolError as exc:
                    write_json(task_dir / "validation.json", {"status":"rejected", "code":exc.code, "message":str(exc), "validated_at":datetime.now(timezone.utc).isoformat()})
                    raise
                if task["status"] == "completed":
                    # Repeated submissions never overwrite a later canonical classification.
                    accepted = json.loads((task_dir / "apply.json").read_text(encoding="utf-8"))
                    if accepted["result_hash"] != json_hash(result.model_dump(mode="json", exclude={"created_at"})):
                        raise ProtocolError("TASK_ALREADY_COMPLETED", "A completed task cannot accept a different result.")
                    if task['task_type']!='classify_book':
                        from book_distiller.pipeline.chapter_tasks import ChapterTasks
                        return ChapterTasks(self).finish(directory, context)
                    return canonical
                if task["status"] != "pending":
                    raise ProtocolError("TASK_NOT_RETRYABLE", "Task is not pending.")
                # Core assigns application time; AI-provided timestamps are not authoritative.
                result.created_at = datetime.now(timezone.utc)
                canonical.parent.mkdir(parents=True, exist_ok=True)
                old = canonical.read_bytes() if canonical.exists() else None
                write_json(task_dir / "result.json", result)
                write_json(task_dir / "validation.json", {"status":"validated", "context_hash":request.context_hash,
                    "validated_at":datetime.now(timezone.utc).isoformat()})
                # A durable intent journal allows idempotent retry after a process interruption.
                write_json(task_dir / "apply.json", {"task_id":str(task_id), "classification_sha256":json_hash(result.model_dump(mode="json")),
                    "result_hash":json_hash(result.model_dump(mode="json", exclude={"created_at"}))})
                publication = None
                try:
                    with self.library.database.transaction() as connection:
                        write_json(canonical, result)
                        self._complete(connection, task_id)
                        if task['task_type']=='build_chapter_atoms':
                            from book_distiller.pipeline.chapter_tasks import ChapterTasks
                            publication = ChapterTasks(self).publish(directory, document, context, result)

                except BaseException:
                    if publication:
                        publication.restore()
                    if old is None:
                        canonical.unlink(missing_ok=True)
                    else:
                        write_bytes(canonical, old)
                    raise
                write_json(task_dir / "validation.json", {"status":"applied", "context_hash":request.context_hash,
                    "validated_at":datetime.now(timezone.utc).isoformat()})
                if task['task_type']!='classify_book':
                    from book_distiller.pipeline.chapter_tasks import ChapterTasks
                    return ChapterTasks(self).finish(directory, context)
                return canonical
        except BookDistillerError:
            raise
        except Exception as exc:
            raise StorageError(f"Submit failed: {exc}") from exc

    def _complete(self, connection, task_id: UUID) -> None:
        self.library.database.complete_pending_task(connection, task_id)

    def describe(self, directory: Path) -> dict[str, str]:
        """Read canonical classification, independent of runtime directory retention."""
        path = safe_child(directory, "analysis/classification.json")
        if not path.exists():
            return {"Classification":"not available"}
        try:
            result = BookClassification.model_validate_json(path.read_text(encoding="utf-8"))
            if self.library.database.task_status(result.task_id) != "completed":
                return {"Classification":"pending apply; resubmit the task"}
            manifest, _ = self._resolve(str(result.book_id))
            document = load_canonical(directory, manifest, self.library.database)
            if result.normalized_document_hash != document.fingerprint.document_hash:
                return {"Classification":"stale; prepare a new task", "Primary type (stale)":result.primary_type.value}
            return {"Classification":"completed", "Primary type":result.primary_type.value}
        except Exception as exc:
            return {"Classification":"needs_review", "Classification diagnostic":str(exc)}
