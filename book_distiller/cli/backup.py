"""Intent routing only; recovery policy lives in Core."""
from pathlib import Path
import typer
from book_distiller.backup.service import BackupService
from book_distiller.core.errors import BookDistillerError
from book_distiller.core.paths import find_project_root, storage_root


def register(app):
    backup=typer.Typer(help='Back up one Book including history and resumable execution state.')
    app.add_typer(backup,name='backup')
    def service():
        project=find_project_root()
        return BackupService(storage_root(project),project)
    def execute(call):
        try:return call()
        except (BookDistillerError,OSError,ValueError) as exc:
            typer.echo('Error: '+str(exc),err=True);raise typer.Exit(1)
    def display(v):
        typer.echo(f'Title: {v.title}\nBook: {v.book_id}\nEdition: {v.edition_id}\nBackup version: {v.backup_version}\nSQLite schema: {v.sqlite_schema_version}\nSource mode: {v.source_mode}\nSource included: {v.source_included}\nCreated: {v.created_at.isoformat()}')
        typer.echo('Generations: '+', '.join(f'{k}={len(ids)}' for k,ids in v.generation_inventory.items()))
        typer.echo(f'Human events: {v.human_inventory}\nRuns: {len(v.run_inventory)}\nTasks: {len(v.task_inventory)}\nResumable runs: {sum(s!="completed" for s in v.run_inventory.values())}\nAsk history included: {v.ask_history_included}\nFiles: {len(v.files)}\nRaw bytes: {sum(f.size for f in v.files)}\nContent hash: {v.backup_content_hash}\nIntegrity: PASS')
        if not v.source_included:typer.echo('Original source unavailable after restore; reparse unavailable. Existing Knowledge/Verification/Reader/Ask retained.')
        if v.external_rule_dependencies:typer.echo('Global/Type Rule dependencies recorded; bodies excluded. Missing/changed rules are reported by Human Guidance.')
    @backup.command('create')
    def create(book:str,output:Path|None=typer.Option(None,'--output'),include_external_source:bool=typer.Option(False,'--include-external-source')):
        output=output or Path(book+'.bookbackup.zip')
        value=execute(lambda:service().export(book,output,include_external_source))
        typer.echo('Book backup created');display(value);typer.echo('Output: '+str(output.absolute()))
    @backup.command('inspect')
    def inspect(archive:Path):display(execute(lambda:service().inspect(archive)))
    @app.command('restore')
    def restore(archive:Path):
        v,root,already=execute(lambda:service().restore(archive))
        typer.echo('Already restored (identical state).' if already else 'Book restored')
        display(v);typer.echo('Library: '+str(root))
        typer.echo('Resume available: '+str(any(s!='completed' for s in v.run_inventory.values())))
        from book_distiller.cli.main import _detail
        manifest=service().library.status(str(v.book_id))[0]
        _detail(manifest,root)
