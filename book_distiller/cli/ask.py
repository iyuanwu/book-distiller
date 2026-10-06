"""Thin CLI routes; all retrieval and publication rules belong to Core."""
from pathlib import Path
from uuid import UUID
import json
import typer
from book_distiller.core.errors import BookDistillerError
from book_distiller.core.paths import find_project_root, storage_root
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.ask.service import AskService


def display(path):
    value = json.loads(path.read_text())
    if value['insufficient_evidence']: typer.echo('INSUFFICIENT EVIDENCE' + (' / OUT OF SCOPE' if value['out_of_scope'] else ''))
    typer.echo('Answer:')
    labels = {'source':'📖 Source', 'ai':'🤖 AI synthesis / application', 'user':'✍️ User Note (not author content)'}
    for segment in value['segments']:
        typer.echo(labels[segment['layer']] + '\n' + segment['text'])
    typer.echo('Evidence:\n' + '\n'.join('- '+ref for ref in value['citation_refs']))
    typer.echo('Confidence: '+value['confidence'])
    for warning in value['quality_warnings']: typer.echo('Quality warning: '+warning)
    typer.echo('Answer artifact: '+str(path))


def register(app):
    ask = typer.Typer(help='Single-book evidence-constrained Ask through the AI Task Protocol.')
    app.add_typer(ask,name='ask')
    def service(): return AITaskService(storage_root(find_project_root()),find_project_root())
    def execute(call):
        try: return call()
        except (BookDistillerError, ValueError, OSError) as exc:
            typer.echo(f'Error: {exc}',err=True); raise typer.Exit(1)

    @ask.command()
    def prepare(book: str, question: str):
        task = execute(lambda: AskService(service()).prepare(book,question))
        typer.echo(f'Task: {task.task_id}\nContext: {task.directory}\nRead workflow.md, prompt.md, context.json, context.md and output.schema.json; submit strict BookAnswer JSON.')

    @ask.command()
    def submit(task_id: UUID, result: Path = typer.Option(...,'--result')):
        display(execute(lambda: service().submit(task_id,result)))

    @ask.command()
    def show(book: str, answer_id: UUID):
        def read_answer():
            ai = service()
            with ai.library.files.locked():
                _, directory = ai._resolve(book)
                path = AskService(ai).finish(directory,answer_id)
                typer.echo('Saved answer snapshot; generation and Human state are pinned in its artifact.')
                display(path)
        execute(read_answer)
