"""Opt-in recovery benchmark: 5000 Claims, 1000 Atoms, 5000 Citations plus history.
Uses synthetic protocol fixtures, never model execution. --home must not exist.
"""
from pathlib import Path
import argparse
import json
import shutil
import sys
from uuid import uuid4
sys.path.insert(0,str(Path(__file__).resolve().parent))
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
from bundle_roundtrip import build, measured, PROJECT
from book_distiller.backup.service import BackupService
from book_distiller.backup.plan import GROUPS
from book_distiller.bundle.service import write
from book_distiller.core.models import RunMetadata, TaskRecord
from book_distiller.core.enums import BookStatus
from book_distiller.pipeline.ai_tasks import AITaskService
from book_distiller.pipeline.runs import RunService
from book_distiller.human.models import HumanAction, UserNote
from book_distiller.human.resolver import EffectiveKnowledgeResolver
from book_distiller.human.service import HumanService
from book_distiller.storage.journal import append_event
from book_distiller.renderers.service import RenderService


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--home',type=Path,required=True);args=parser.parse_args()
    home=args.home.resolve();home.mkdir()
    i=build(home/'a');root=i.library_path
    # Publication retries retain the same logical generation in distinct physical directories.
    # Explicitly copy known generation files; no whole-book copytree.
    for kind in ('chapter','book','verification'):
        parent,files=GROUPS[kind]
        source=next((root/parent).iterdir());identity=source.name[:36]
        for _ in range(10):
            destination=root/parent/(identity+'-'+str(uuid4()));destination.mkdir()
            for name in files:shutil.copyfile(source/name,destination/name)
    ai=AITaskService(home/'a',PROJECT);runs=RunService(ai)
    obj=next(v for v in EffectiveKnowledgeResolver(root).inventory().values() if v['target_ref']['object_type']=='atomic_claim')
    # Valid append-only metadata history, with explicit synthetic benchmark attribution.
    for n in range(500):
        event=HumanAction(action='verify',target_ref=obj['target_ref'],base_object_hash=obj['base_object_hash'],reason='Synthetic benchmark history')
        append_event(root/'human/overrides.jsonl',event.model_dump(mode='json'))
        note=UserNote(target_ref=obj['target_ref'],text=f'Synthetic benchmark note {n}.')
        append_event(root/'human/notes.jsonl',note.model_dump(mode='json'))
    HumanService(ai).rebuild(root)
    for n in range(100):
        tasks=[TaskRecord(edition_id=i.manifest.edition.edition_id,task_type='parse') for _ in range(10)]
        run=RunMetadata(book_id=i.manifest.book.book_id,edition_id=i.manifest.edition.edition_id,status=BookStatus.PAUSED,
            requested_scope={'from':'parse','through':'parse'},resolved_plan={'steps':[{'stage':'parse','chapter':None}]},checkpoints={'0':{'tasks':[str(t.task_id) for t in tasks]}})
        with ai.library.database.transaction() as c:
            ai.library.database.insert_run(c,run)
            for task in tasks:
                ai.library.database.insert_task(c,task)
                c.execute('INSERT INTO run_tasks VALUES (?,?,?,?)',(str(run.run_id),str(task.task_id),'parse',None))
        (root/f'runtime/runs/{run.run_id}').mkdir(parents=True)
        runs.save(root,run);write(root/f'runtime/runs/{run.run_id}/plan.json',run.resolved_plan);runs.event(root,run,'RUN_PAUSED',reason='Synthetic benchmark fixture')
    RenderService(home/'a',PROJECT).render(i.manifest.book.slug,force=True)
    service=BackupService(home/'a',PROJECT);archive=home/'large.bookbackup.zip'
    value,export=measured(lambda:service.export(i.manifest.book.slug,archive))
    _,validate=measured(lambda:service.inspect(archive))
    _,restore=measured(lambda:BackupService(home/'c',PROJECT).restore(archive))
    result={'fixture':'synthetic protocol history; no model calls; historical generations include retained publication retries',
        'claims':5000,'atoms':1000,'citations':5000,'generations':{k:len(v) for k,v in value.generation_inventory.items()},
        'human_events':value.human_inventory,'runs':len(value.run_inventory),'tasks':len(value.task_inventory),
        'files':len(value.files),'raw_bytes':sum(f.size for f in value.files),'zip_bytes':archive.stat().st_size,
        'export_with_self_validation':export,'independent_validation':validate,'restore_with_validation':restore}
    write(home/'performance.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':main()
