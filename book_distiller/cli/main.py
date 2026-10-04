"""Thin CLI for local Library, knowledge workflows and evidence verification."""
import json
import platform
import sys
from pathlib import Path
from uuid import UUID
import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text
from book_distiller.core.errors import BookDistillerError, ConfigurationError
from book_distiller.core.ingest import IngestService
from book_distiller.core.parse import ParseService
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.parsers.docling import docling_version
from book_distiller.core.models.library import Manifest
from book_distiller.core.paths import find_project_root, storage_root
from book_distiller.core.version import get_version

app = typer.Typer(help="Book Distiller — Phase 7 verified knowledge and progressive static reading.", no_args_is_help=True)


@app.command()
def version() -> None:
    """Show the Book Distiller version."""
    typer.echo(f"Book Distiller {get_version()}")


@app.command()
def doctor() -> None:
    """Check the environment and initialize/check the database."""
    checks: list[tuple[str, bool, str]] = [
        ("macOS", platform.system() == "Darwin", platform.system()),
        ("Python 3.12", sys.version_info[:2] == (3, 12), platform.python_version()),
    ]
    try:
        root = find_project_root()
    except ConfigurationError as exc:
        checks.append(("Project root", False, str(exc)))
        checks.extend((name, False, "Project root unavailable") for name in ("Inbox", "Library", "Backups", "Database"))
    else:
        checks.append(("Project root", True, str(root)))
        home = storage_root(root)
        checks.extend((name.title(), (home / name).is_dir(), str(home / name)) for name in ("inbox", "library", "backups"))
        try:
            IngestService(home).check()
        except BookDistillerError as exc:
            checks.append(("Database", False, str(exc)))
        else:
            checks.append(("Database", True, str(home / "data" / "book_distiller.sqlite3")))
    try:
        installed = docling_version()
    except Exception as exc:
        checks.append(("Docling", False, str(exc)))
    else:
        checks.append(("Docling", True, installed))
    table = Table(title="Book Distiller Doctor")
    for heading in ("Check", "Status", "Detail"):
        table.add_column(heading)
    for name, ok, detail in checks:
        table.add_row(name, "OK" if ok else "FAIL", detail)
    Console().print(table)
    if not all(ok for _, ok, _ in checks):
        raise typer.Exit(code=1)


def _service() -> IngestService:
    return IngestService(storage_root(find_project_root()))


def _error(exc: BookDistillerError) -> None:
    typer.echo(f"Error: {exc}", err=True)
    raise typer.Exit(code=1)


def _detail(manifest: Manifest, library: Path) -> None:
    table = Table(show_header=False)
    table.add_column("Field")
    table.add_column("Value")
    for label, value in (
        ("Book", manifest.book.title), ("Book ID", str(manifest.book.book_id)),
        ("Slug", manifest.book.slug), ("Edition", str(manifest.edition.edition_id)),
        ("Source", manifest.source.original_filename), ("Stored path", manifest.source.stored_path),
        ("SHA256", manifest.source.sha256), ("Copy mode", manifest.source.copy_mode),
        ("Library", str(library)), ("Status", manifest.book.status.value),
        ("Metadata", manifest.book.metadata_status), ("Created at", manifest.created_at.isoformat()),
    ):
        table.add_row(Text(label), Text(value))
    service = ParseService(storage_root(find_project_root()))
    for label, value in service.describe(library, manifest.edition.edition_id).items():
        table.add_row(Text(label), Text(value))
    ai = AITaskService(storage_root(find_project_root()), find_project_root())
    for label, value in ai.describe(library).items():
        table.add_row(Text(label), Text(value))
    if (library/'knowledge/chapters').exists():
        try:
            from book_distiller.pipeline.chapter_tasks import ChapterTasks
            from book_distiller.pipeline.canonical import load_canonical
            document=load_canonical(library,manifest,ai.library.database)
            for label,value in ChapterTasks(ai).describe(library,document).items():
                table.add_row(Text(label),Text(value))
        except BookDistillerError as exc:
            table.add_row('Knowledge','needs_review: '+str(exc))
        try:
            from book_distiller.pipeline.book_tasks import BookTasks
            document=load_canonical(library,manifest,ai.library.database)
            for label,value in BookTasks(ai).describe(library,document).items():
                table.add_row(Text(label),Text(value))
        except BookDistillerError as exc:
            table.add_row('Book synthesis','stale / needs_review: '+str(exc))
    if (library/'knowledge/book').is_symlink():
        try:
            from book_distiller.pipeline.verification_tasks import VerificationTasks
            from book_distiller.pipeline.canonical import load_canonical
            document=load_canonical(library,manifest,ai.library.database)
            for label,value in VerificationTasks(ai).describe(library,document).items():table.add_row(Text(label),Text(value))
        except BookDistillerError as exc:table.add_row('Verification','failed: '+str(exc))
    from book_distiller.renderers.service import RenderService
    for label, value in RenderService(storage_root(find_project_root()), find_project_root()).describe(str(manifest.book.book_id), library).items():
        table.add_row(Text(label), Text(value))
    Console().print(table)
    if manifest.source.copy_mode == "reference":
        typer.echo("Warning: 如果原文件以后移动或删除，该 Edition 的 Source 会失效。")


@app.command()
def ingest(file: Path = typer.Argument(..., help="Source file; its contents are not parsed."),
           no_copy: bool = typer.Option(False, "--no-copy", help="Reference the original file without copying."),
           title: str | None = typer.Option(None, "--title", help="Provisional display title.")) -> None:
    """Import a source into the local Library, idempotently by SHA256."""
    try:
        result = _service().ingest(file, copy=not no_copy, title=title)
    except BookDistillerError as exc:
        _error(exc)
    typer.echo("Already ingested." if result.already_ingested else "Book ingested successfully")
    _detail(result.manifest, result.library_path)


@app.command()
def status(book: str | None = typer.Argument(None, help="Exact book ID or slug.")) -> None:
    """List local editions or show a book's source and metadata."""
    try:
        service = _service()
        records = service.status(book)
    except BookDistillerError as exc:
        _error(exc)
    if book:
        for manifest in records:
            _detail(manifest, service.files.library / manifest.book.slug)
    elif not records:
        typer.echo("Library is empty. Use book ingest <FILE> to add a source.")
    else:
        table = Table("Book", "Slug", "Edition", "Status")
        for manifest in records:
            table.add_row(Text(manifest.book.title), Text(manifest.book.slug), str(manifest.edition.edition_id), manifest.book.status.value)
        Console().print(table)


@app.command()
def parse(book: str = typer.Argument(..., help="Exact book ID or slug."),
          force: bool = typer.Option(False, "--force", help="Safely replace the last successful parse.")) -> None:
    """Parse an indexed source and publish a canonical document. No AI distillation."""
    try:
        result = ParseService(storage_root(find_project_root())).parse(book, force=force)
    except BookDistillerError as exc:
        _error(exc)
    if result.already_parsed:
        typer.echo("Already parsed.")
    elif result.quality.status == "review_recommended":
        typer.echo("Parse completed with warnings.\nReview recommended.")
    else:
        typer.echo("Parse completed successfully.")
    typer.echo(f"Parser: {result.book.parser_metadata.parser} {result.book.parser_metadata.parser_version}")
    typer.echo(f"Blocks: {result.book.block_count} | Quality: {result.quality.status}")
    typer.echo(f"Artifacts: {result.parsed_path}")


workflow_app = typer.Typer(help="Prepare and submit local Codex tasks; no model API.")
app.add_typer(workflow_app, name="workflow")


@workflow_app.command("prepare")
def prepare_workflow(workflow: str, book: str, chapter: str | None = typer.Option(None,"--chapter")) -> None:
    """Create a budgeted Context Package and a pending AI Task."""
    from book_distiller.core.models.verification import VERIFY_WORKFLOWS
    if workflow in VERIFY_WORKFLOWS:
        verify_book(book,False)
        return
    from book_distiller.core.models.synthesis import BOOK_WORKFLOWS
    if workflow in BOOK_WORKFLOWS:
        if workflow == 'reduce_chapter_atoms':
            if chapter is None: raise typer.BadParameter('--chapter is required')
            analyze_atoms(book,chapter,True,False)
        else: analyze_book(book,False)
        return
    if workflow == 'extract_claims':
        if chapter is None: raise typer.BadParameter('--chapter is required')
        analyze_claims(book,chapter,False)
        return
    if workflow == 'build_chapter_atoms':
        if chapter is None: raise typer.BadParameter('--chapter is required')
        analyze_atoms(book,chapter,False,False)
        return
    try:
        result = AITaskService(storage_root(find_project_root()), find_project_root()).prepare(workflow, book)
    except BookDistillerError as exc:
        _error(exc)
    typer.echo("AI task prepared")
    typer.echo(f"Task ID: {result.task_id}")
    for filename in ("request.json", "workflow.md", "prompt.md", "context.md", "context.json", "output.schema.json"):
        typer.echo(f"{filename}: {result.directory / filename}")
    typer.echo(f"Context: {result.context.budget.selected_chars} chars; {result.context.budget.selected_blocks} blocks")


@workflow_app.command("submit")
def submit_workflow(task_id: UUID, result: Path = typer.Option(..., "--result")) -> None:
    """Validate a Codex result and atomically apply the canonical classification."""
    try:
        path = AITaskService(storage_root(find_project_root()), find_project_root()).submit(task_id, result)
    except BookDistillerError as exc:
        _error(exc)
    typer.echo(f"Result applied: {path}")


analyze_app=typer.Typer(help="Prepare Chapter tasks; Codex performs reasoning.")
app.add_typer(analyze_app,name="analyze")


def _chapter_service():
    from book_distiller.pipeline.chapter_tasks import ChapterTasks
    return ChapterTasks(AITaskService(storage_root(find_project_root()),find_project_root()))


def _show_task(task):
    typer.echo(f"Workflow: {task.context.task_type}")
    typer.echo(f"Task ID: {task.task_id}")
    typer.echo(f"Task directory: {task.directory}")
    typer.echo(f"Read: workflow.md, prompt.md, context.md, output.schema.json; write result.json")
    typer.echo(f"Context: {task.context.budget.selected_chars} chars")


@analyze_app.command("claims")
def analyze_claims(book: str, chapter: str = typer.Option(...,"--chapter"),
                   force: bool = typer.Option(False,"--force")):
    """Prepare pending Chunk tasks, reusing completed checkpoints unless --force."""
    try:
        generation,tasks=_chapter_service().prepare_claims(book,chapter,force=force)
    except BookDistillerError as exc: _error(exc)
    typer.echo(f"Generation: {generation.generation_id}")
    typer.echo(f"Chunks: {len(generation.claim_tasks)}; pending tasks: {len(tasks)}")
    for task in tasks: _show_task(task)
    if not tasks: typer.echo("Chapter Claims complete; prepare atoms next.")


@analyze_app.command("atoms")
def analyze_atoms(book: str, chapter: str = typer.Option(...,"--chapter"),
                  reduce: bool = typer.Option(False,"--reduce"), force: bool = typer.Option(False,"--force")):
    """Prepare one Chapter Atom task from all validated Chapter Claims."""
    try:
        if reduce:
            from book_distiller.pipeline.book_tasks import BookTasks
            task=BookTasks(_chapter_service().ai).prepare_chapter(book,chapter,force)
        else: task=_chapter_service().prepare_atoms(book,chapter)
    except BookDistillerError as exc: _error(exc)
    if isinstance(task,Path): typer.echo(f"Chapter published: {task}")
    else: _show_task(task)


@analyze_app.command("book")
def analyze_book(book: str, force: bool = typer.Option(False,"--force")):
    """Resume bounded Book synthesis; submit each Codex task then run again."""
    from book_distiller.pipeline.book_tasks import BookTasks
    try: result=BookTasks(_chapter_service().ai).prepare(book,force)
    except BookDistillerError as exc: _error(exc)
    if isinstance(result,Path): typer.echo(f"Book published: {result}")
    else: _show_task(result)


@app.command("verify")
def verify_book(book: str, force: bool = typer.Option(False,"--force")):
    """Prepare/resume local evidence verification; Codex performs the judgments."""
    from book_distiller.pipeline.verification_tasks import VerificationTasks
    service=VerificationTasks(_chapter_service().ai)
    try: result=service.prepare(book,force)
    except BookDistillerError as exc: _error(exc)
    if isinstance(result,Path): typer.echo(f"Verification published: {result}")
    else:
        _show_task(result)
        state=json.loads((service.root(result.directory.parents[2],result.context.scope['generation_id'])/'state.json').read_text())
        typer.echo(f"Verification generation: {state['generation_id']}")
        typer.echo(f"Progress: {state['cursor']} / {len(state['plan'])} tasks completed")




@app.command()
def render(book: str, force: bool = typer.Option(False, "--force"),
           no_open: bool = typer.Option(False, "--no-open")) -> None:
    """Generate the offline L0–L5 reader from current verified knowledge."""
    from book_distiller.renderers.service import RenderService
    import subprocess
    try:
        path, already, metadata = RenderService(storage_root(find_project_root()), find_project_root()).render(book, force)
    except BookDistillerError as exc:
        _error(exc)
    typer.echo("Already rendered." if already else "Book Reader generated — L0–L5 ready; Evidence ready")
    typer.echo(f"Quality     {metadata['quality_gate'].upper()}\nGeneration  {metadata['render_generation_id']}\nOutput      {path}")
    for warning in metadata['metrics']['warnings']:
        typer.echo('Warning: '+warning)
    if not no_open:
        typer.echo(f'Opening: {path}')
        try:
            subprocess.run(['open', str(path)], check=True, capture_output=True)
        except (OSError, subprocess.CalledProcessError):
            typer.echo('Could not open browser automatically. Open index.html manually.')


if __name__ == "__main__":
    app()
