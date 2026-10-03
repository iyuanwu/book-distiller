"""Thin Phase 1 CLI for environment checks and local Library operations."""
import platform
import sys
from pathlib import Path
import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text
from book_distiller.core.errors import BookDistillerError, ConfigurationError
from book_distiller.core.ingest import IngestService
from book_distiller.core.models.library import Manifest
from book_distiller.core.paths import find_project_root, storage_root
from book_distiller.core.version import get_version

app = typer.Typer(help="Book Distiller — Phase 1 Library. Distillation is not implemented.", no_args_is_help=True)


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


if __name__ == "__main__":
    app()

