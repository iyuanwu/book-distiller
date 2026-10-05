"""Intent routing only; dependency and override rules live in Core."""
import json
from pathlib import Path
import typer
from book_distiller.core.paths import find_project_root,storage_root
from book_distiller.core.errors import BookDistillerError
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.pipeline.runs import RunService
from book_distiller.human.service import HumanService
from book_distiller.human.resolver import EffectiveKnowledgeResolver


def ai():return AITaskService(storage_root(find_project_root()),find_project_root())


def output(value):typer.echo(json.dumps(value,ensure_ascii=False,indent=2,default=str))


def register(app):
    def execute(call):
        try:return call()
        except (BookDistillerError,ValueError,OSError) as exc:
            typer.echo(f'Error: {exc}',err=True);raise typer.Exit(1)

    def show_run(result):
        run,tasks=result
        output({'run_id':str(run.run_id),'status':run.status.value,'cursor':run.cursor,'steps':len(run.resolved_plan['steps']),
                'pending_tasks':[{'task_id':str(t.task_id),'directory':str(t.directory)} for t in tasks]})

    @app.command()
    def rerun(book:str,from_stage:str=typer.Option(...,'--from'),chapter:str|None=typer.Option(None,'--chapter'),
              through:str=typer.Option('render','--through'),dry_run:bool=typer.Option(False,'--dry-run')):
        """Plan a scoped rerun or start it, preserving immutable generations and locks."""
        service=RunService(ai())
        if dry_run:
            plan=execute(lambda:service.plan(book,from_stage,chapter,through))
            output(plan.model_dump())
            typer.echo('Will rerun: '+', '.join(s['stage']+(':'+s['chapter'] if s['chapter'] else '') for s in plan.steps))
            typer.echo('Will reuse: '+', '.join(plan.reused_generations))
            typer.echo('Protected: '+str(len(plan.protected_objects)))
            typer.echo('Downstream stale: '+', '.join(plan.downstream_stale))
            typer.echo('No files modified.')
        else:show_run(execute(lambda:service.create(book,from_stage,chapter,through)))

    @app.command()
    def resume(book:str,run:str|None=typer.Option(None,'--run')):
        """Continue the same user Run; completed executions never create new work."""
        show_run(execute(lambda:RunService(ai()).resume(book,run)))

    human=typer.Typer(help='Generation-bound human actions; HTML remains read-only.')
    app.add_typer(human,name='human')

    def resolver(book):
        service=ai();service.library._initialize();_,directory=service._resolve(book)
        return EffectiveKnowledgeResolver(directory)

    @human.command('list')
    def list_objects(book:str):output(execute(lambda:list(resolver(book).inventory().values())))

    @human.command('show')
    def show(book:str,target:Path=typer.Option(...,'--target')):
        output(execute(lambda:resolver(book).show(json.loads(target.read_text()))))

    @human.command('history')
    def history(book:str):output(execute(lambda:resolver(book).events))

    @human.command('apply')
    def apply(book:str,input:Path=typer.Option(...,'--input')):
        """Apply edit/verify/lock/unlock JSON containing target_ref and base_object_hash."""
        output(execute(lambda:HumanService(ai()).apply(book,json.loads(input.read_text()))))

    @human.command('note')
    def note(book:str,input:Path=typer.Option(...,'--input')):
        output(execute(lambda:HumanService(ai()).note(book,json.loads(input.read_text()))))

    @human.command('rule')
    def rule(book:str,input:Path=typer.Option(...,'--input')):
        output(execute(lambda:HumanService(ai()).rule(book,json.loads(input.read_text()))))
