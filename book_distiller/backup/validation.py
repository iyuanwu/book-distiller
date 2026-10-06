"""Recovery semantics: validate saved identities; Resume independently checks freshness."""
import json
from uuid import UUID
from book_distiller.bundle.service import must, read, validate_human
from book_distiller.backup.models import DBExport
from book_distiller.backup.plan import GROUPS, CHAPTER_FILES, PARSE_FILES, TASK_FILES
from book_distiller.core.models.library import Manifest
from book_distiller.core.models import RunMetadata
from book_distiller.core.models.ai_tasks import AIRequest, BookClassification
from book_distiller.core.models.knowledge import ChapterGeneration
from book_distiller.pipeline.knowledge_context import parse_context
from book_distiller.pipeline.context_package import context_digest, render_context
from book_distiller.pipeline.canonical import json_hash
from book_distiller.storage.filesystem import hash_source
from book_distiller.bundle.archive import safe_name
from book_distiller.storage.journal import read_events


def validate_state(root, value, db):
    manifest=Manifest.model_validate_json((root/'manifest.json').read_text())
    must((manifest.book.book_id,manifest.edition.edition_id,manifest.source.sha256)==(value.book_id,value.edition_id,value.source_sha256),'backup identity')
    from book_distiller.storage.database import Database
    rows=db.model_dump(mode='json')
    b,e=rows['books'][0],rows['editions'][0]
    indexed=Database._manifest(dict(b,**{k:v for k,v in e.items() if k not in ('book_id',)}))
    must(indexed==manifest,'backup DB/manifest identity')
    must(manifest.source.copy_mode=='copy' and str(manifest.source.original_path)=='/backup-source/original'+manifest.source.extension,'backup source projection')
    if value.source_included:must(hash_source(root/manifest.source.stored_path)==(value.source_sha256,manifest.source.file_size),'backup Source hash')
    must(db.run_tasks==value.run_task_inventory,'Run/Task relationship inventory')
    normalized=read(root/value.pointers['parsed']/'completion.json')['task_id'] if 'parsed' in value.pointers else None
    must(normalized==(str(value.normalized_generation_id) if value.normalized_generation_id else None),'normalized current generation')
    must({str(r.run_id):r.status for r in db.runs}==value.run_inventory,'Run inventory')
    must({str(t.task_id):t.status.value for t in db.tasks}==value.task_inventory,'Task inventory')
    names={f.relative_path for f in value.files}
    if 'reader-status.js' in names:
        raw=(root/'reader-status.js').read_text()
        prefix='window.BOOK_DISTILLER_LIVE_STATUS = '
        must(raw.startswith(prefix) and raw.endswith(';\n'),'Reader live status format')
        payload=json.loads(raw[len(prefix):-2])
        must(set(payload)=={'status','reason'} and payload['status'] in ('current','stale','display_stale') and isinstance(payload['reason'],str),'Reader live status schema')
        must(raw==prefix+json.dumps(payload,ensure_ascii=True)+';\n','Reader live status contains executable content')
        stale=read(root/'runtime/stale.json') if (root/'runtime/stale.json').exists() else {}
        must(not any(key!='render' for key in stale) or payload['status']=='stale','Reader live status hides stale Verification')
    must({n for n in names if n.startswith('source/')}==({manifest.source.stored_path} if value.source_included else set()),'unexpected Source file')
    task_ids={str(t.task_id) for t in db.tasks};run_ids={str(r.run_id) for r in db.runs}
    for name in names:
        if name.startswith('runtime/tasks/'):must(name.split('/')[2] in task_ids,'unindexed Task package')
        if name.startswith('runtime/runs/'):must(name.split('/')[2] in run_ids,'unindexed Run package')
    for row in db.runs:
        if row.execution_json:
            run=RunMetadata.model_validate_json(row.execution_json)
            path=root/'runtime/runs'/str(row.run_id)
            must(RunMetadata.model_validate_json((path/'run.json').read_text())==run,'Run projection/DB mismatch')
            must(read(path/'plan.json')==run.resolved_plan,'Run plan mismatch')
            for event in read_events(path/'events.jsonl'):
                from book_distiller.pipeline.run_events import RunEvent
                RunEvent.model_validate({k:v for k,v in event.items() if k in ('event','at','details')})
                if event.get('task_id'):must(event['task_id'] in task_ids,'Run event Task reference')
    for row in db.tasks:
        path=root/'runtime/tasks'/str(row.task_id)
        if not path.exists():
            must(row.task_type in ('ingest','parse'),'missing Task protocol package')
            continue
        request=AIRequest.model_validate_json((path/'request.json').read_text())
        context=parse_context((path/'context.json').read_text())
        must((request.task_id,request.edition_id,request.book_id,request.task_type)==(row.task_id,value.edition_id,value.book_id,row.task_type),'Task request identity')
        must((context.task_id,context.book_id,context.edition_id)==(row.task_id,value.book_id,value.edition_id),'Task Context identity')
        must(context_digest(context)==context.context_hash==request.context_hash,'Task Context hash')
        must((path/'context.md').read_text()==render_context(context),'Task Context projection')
        for name in ('workflow.md','prompt.md','output.schema.json'):must((path/name).is_file(),'Task resources missing')
        must((hash_source(path/'workflow.md')[0],hash_source(path/'prompt.md')[0],json_hash(read(path/'output.schema.json')))==
             (request.workflow_sha256,request.prompt_sha256,request.output_schema_sha256),'saved Task resource hashes')
        must(context.source_sha256==request.source_sha256==value.source_sha256 and context.normalized_document==request.normalized_document,'Task source binding')
        accepted=path/'accepted.json'
        if not accepted.exists() and row.task_type in ('classify','classify_book'):
            accepted=root/'analysis/classification-generations'/f'{row.task_id}.json'
        if not accepted.exists() and row.task_type=='ask_book':
            accepted=root/'ask/answers'/f'{row.task_id}.json'
        if row.status.value=='completed':
            must(accepted.is_file(),'completed Task result missing')
            from book_distiller.pipeline.result_validation import validate_result
            validate_result(accepted.read_text(),request,context)
            result=read(accepted)
            must(result.get('task_id')==str(row.task_id) and result.get('book_id')==str(value.book_id) and result.get('edition_id')==str(value.edition_id),'Task result identity')
            apply=read(path/'apply.json')
            must(apply['result_hash']==json_hash({k:v for k,v in result.items() if k!='created_at'}),'completed Task result hash')
    # Every retained generation must contain its full committed manifest inventory.
    for kind,ids in value.generation_inventory.items():
        if kind=='reader':continue
        parent,allowed=GROUPS[kind]
        for gid in ids:
            path=root/parent/gid
            filename={'normalized':'completion.json','chapter':'chapter.json','book':'book_model.json','verification':'manifest.json'}[kind]
            meta=read(path/filename)
            if kind=='chapter':
                generation=ChapterGeneration.model_validate(meta['generation'])
                must((generation.book_id,generation.edition_id)==(value.book_id,value.edition_id),'Chapter identity')
                identity=str(generation.generation_id)
            else:
                identity=str(meta['task_id' if kind=='normalized' else 'generation_id'])
                if kind=='normalized':
                    from book_distiller.core.models.normalized import NormalizedBook
                    from book_distiller.storage.parsed import ParseReceipt
                    ParseReceipt.model_validate(meta)
                    normalized=NormalizedBook.model_validate_json((path/'normalized/book.json').read_text())
                    must((normalized.book_id,normalized.edition_id,normalized.source_sha256)==(value.book_id,value.edition_id,value.source_sha256),'historical normalized identity')
                if kind=='book':
                    must((meta['book_id'],meta['edition_id'],meta['schema_version'])==(str(value.book_id),str(value.edition_id),'1.0'),'historical Book identity/schema')
                    for chapter,dep in meta['dependencies']['chapters'].items():
                        safe_name(dep['path'])
                        must(dep['path']+'/chapter.json' in names,'historical Chapter dependency missing')
                        linked=read(root/dep['path']/'chapter.json')['generation']
                        must(linked['generation_id']==dep['generation_id'] and linked['chapter_id']==chapter,'historical Chapter dependency identity')
                if kind=='verification':
                    dep=meta['dependencies'];safe_name(dep['book_path'])
                    must(dep['book_path']+'/book_model.json' in names,'historical Book dependency missing')
                    linked=read(root/dep['book_path']/'book_model.json')
                    must(linked['generation_id']==dep['book_generation_id'],'historical Verification dependency identity')
            must(gid==identity or gid.startswith(identity+'-'),'generation identity/path')
            for name,digest in meta['artifact_hashes'].items():
                safe_name(name)
                must(name in allowed,'generation manifest path')
                # Bundles may have legitimately omitted raw parse diagnostics.
                if kind=='normalized' and name.startswith('raw/') and not (path/name).exists():continue
                must(hash_source(path/name)[0]==digest,'historical generation checksum '+name)
    human=validate_human(root)
    must(human.display_hash()==value.human_state_hash,'Human effective state')
    must({n:len(read_events(root/'human'/f'{n}.jsonl')) for n in ('overrides','notes','rules')}==value.human_inventory,'Human journal inventory')
    # Check runtime references before they can be consumed by resume adapters.
    for name in sorted(n for n in names if n.startswith('runtime/') and n.endswith('.json') and '/tasks/' not in n and '/runs/' not in n):
        data=read(root/name)
        def walk(obj):
            if isinstance(obj,dict):
                for key,item in obj.items():
                    if key in ('pending','atom_task') and item:must(str(item) in task_ids,'checkpoint Task missing')
                    if key in ('claim_tasks',) and isinstance(item,dict):must(set(item.values())<=task_ids,'checkpoint Claim Task missing')
                    if key=='tasks' and isinstance(item,list):must(set(item)<=task_ids,'checkpoint Tasks missing')
                    if key in ('file','frontier','path','normalized_blocks_path') and isinstance(item,str):
                        safe_name(item)
                        candidates=[root/item,(root/name).parent/item]
                        must(any(p.is_file() or p.is_dir() for p in candidates),'checkpoint file missing '+item)
                    walk(item)
            elif isinstance(obj,list):
                for item in obj:walk(item)
        walk(data)
    return manifest
