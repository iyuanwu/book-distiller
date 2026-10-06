"""Thin commands for a current-book snapshot, never a Library backup."""
from pathlib import Path
import typer
from book_distiller.core.errors import BookDistillerError
from book_distiller.core.paths import find_project_root, storage_root
from book_distiller.bundle.service import BundleService


def register(app):
    bundle = typer.Typer(help='Export, inspect and import a portable current Book snapshot.')
    app.add_typer(bundle, name='bundle')
    def service():
        project = find_project_root()
        return BundleService(storage_root(project), project)
    def execute(call):
        try:
            return call()
        except (BookDistillerError, OSError, ValueError) as exc:
            typer.echo('Error: '+str(exc), err=True)
            raise typer.Exit(1)
    def display(value):
        typer.echo(f'Title: {value.title}\nBook: {value.book_id}\nEdition: {value.edition_id}\nBundle schema: {value.bundle_version}\nCreated: {value.created_at.isoformat()}\nQuality: {value.quality_gate.upper()}\nSource included: {value.source_included}\nContains source-derived text: True\nAsk ready: {value.ask_ready}\nNormalized: {value.normalized_generation_id}\nBook generation: {value.book_generation_id}\nVerification: {value.verification_generation_id}\nReader: {value.reader_generation_id}\nHuman state: {value.human_state_hash}\nFiles: {len(value.files)}\nUncompressed bytes: {sum(f.size for f in value.files)}\nContent hash: {value.bundle_content_hash}\nChecksum and references: PASS')
        if value.external_rule_dependencies:
            typer.echo('External Global/Type Rule bodies excluded. New work uses only locally available rules:')
            for rule in value.external_rule_dependencies:
                typer.echo(f'  {rule.scope}: {rule.rule_id} sha256={rule.sha256}')
        if not value.source_included:
            typer.echo('Original source unavailable: Open Original and reparse unavailable; normalized Citation/Evidence/Ask remain available.')
    @bundle.command('export')
    def export(book: str, output: Path | None = typer.Option(None, '--output'), include_source: bool = typer.Option(False, '--include-source')):
        output = output or Path(book+'.bookbundle.zip')
        result = execute(lambda: service().export(book, output, include_source))
        display(result)
        typer.echo('Bundle: '+str(output.resolve()))
    @bundle.command('inspect')
    def inspect(archive: Path):
        display(execute(lambda: service().inspect(archive)))
    @bundle.command('import')
    def import_bundle(archive: Path):
        value, path, already = execute(lambda: service().import_bundle(archive))
        typer.echo('Already imported (identical snapshot).' if already else 'Book Bundle imported. Book/Edition registered; no historical Tasks or Runs imported.')
        display(value)
        typer.echo('Library: '+str(path))
