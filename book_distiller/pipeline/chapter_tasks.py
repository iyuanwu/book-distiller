"""Chapter lifecycle built on the shared AI task protocol; no AI client."""
import json
import shutil
import unicodedata
from pathlib import Path
from uuid import UUID, uuid4
from book_distiller.core.errors import BookDistillerError, StorageError
from book_distiller.core.models.ai_tasks import BookClassification
from book_distiller.core.models.knowledge import (ChapterGeneration, ChunkManifest, ChunkBudget, ClaimResult,
    AtomicClaim, KnowledgeAtom)
from book_distiller.normalize.book import read_blocks
from book_distiller.pipeline.ai_tasks import safe_child, PreparedTask
from book_distiller.pipeline.canonical import load_canonical, json_hash, canonical_json
from book_distiller.pipeline.chunking import build_chunks
from book_distiller.pipeline.files import write_json, write_bytes
from book_distiller.pipeline.knowledge_context import build_knowledge_context, parse_context
from book_distiller.pipeline.result_validation import ProtocolError, validate_result
from book_distiller.pipeline.workflows import load_workflow
from book_distiller.storage.filesystem import hash_source
from book_distiller.storage import knowledge as storage


class ChapterTasks:
    def __init__(self, ai):
        self.ai=ai
        self.library=ai.library

    def classification(self,directory,document):
        path=safe_child(directory,'analysis/classification.json')
        if not path.exists(): raise ProtocolError('CLASSIFICATION_REQUIRED','Run classify first')
        value=BookClassification.model_validate_json(path.read_text(encoding='utf-8'))
        if (value.book_id,value.edition_id,value.normalized_document_hash)!=(document.book.book_id,document.book.edition_id,document.fingerprint.document_hash) or self.library.database.task_status(value.task_id)!='completed':
            raise ProtocolError('STALE_CONTEXT','Classification must be completed for the current parse')
        from book_distiller.human.resolver import EffectiveKnowledgeResolver, ref
        value=BookClassification.model_validate(EffectiveKnowledgeResolver(directory).effective(ref('classification',value.task_id,'classification'),value.model_dump(mode='json'))['value'])
        return value,json_hash(value.model_dump(mode='json'))

    def resources(self,classification):
        types=[classification.primary_type.value,*[t.value for t in classification.secondary_types]]
        result={}
        for name in ('extract_claims','build_chapter_atoms'):
            workflow=load_workflow(self.ai.project,name,types)
            result[name+'.workflow']=hash_source(workflow.workflow_path)[0]
            result[name+'.prompt']=workflow.prompt_hash()
            result[name+'.schema']=json_hash(workflow.output_schema())
        return result

    def generation_path(self,directory,generation_id):
        return safe_child(directory,f'runtime/generations/{UUID(str(generation_id))}')

    def active_path(self,directory,chapter_id):
        # chapter_id always comes from validated Canonical metadata or ChapterGeneration.
        return safe_child(directory,f'runtime/chapter-work/{chapter_id}.json')

    def load_generation(self,directory,generation_id):
        path=self.generation_path(directory,generation_id)
        safe_child(directory,str((path/'generation.json').relative_to(directory)))
        return ChapterGeneration.model_validate_json((path/'generation.json').read_text())

    def verify_generation(self,directory,document,generation):
        classification,digest=self.classification(directory,document)
        active=json.loads(self.active_path(directory,generation.chapter_id).read_text())
        if (generation.normalized_document_hash!=document.fingerprint.document_hash or
            generation.classification_hash!=digest or generation.resources!=self.resources(classification) or
            active['generation_id']!=str(generation.generation_id)):
            raise ProtocolError('STALE_CONTEXT','Chapter generation, classification or workflow changed; prepare a new generation with --force')
        path=self.generation_path(directory,generation.generation_id)/'chunks.json'
        chunks=ChunkManifest.model_validate_json(path.read_text())
        if json_hash(chunks.model_dump(mode='json'))!=generation.chunks_hash:
            raise ProtocolError('STALE_CONTEXT','Chunk manifest changed')
        rebuilt=build_chunks(read_blocks(document.blocks_path),generation.chapter_id,document.fingerprint.document_hash,chunks.budget)
        if rebuilt!=chunks:
            raise ProtocolError('STALE_CONTEXT','Chunk manifest no longer matches Canonical blocks')
        return classification,chunks

    def prepare_claims(self,selector,chapter,*,force=False,budget=None):
        try:
            with self.library.files.locked():
                self.library._initialize()
                manifest,directory=self.ai._resolve(selector)
                document=load_canonical(directory,manifest,self.library.database)
                candidates=[c for c in document.book.chapters if c.chapter_id==chapter]
                if not candidates:
                    raise ProtocolError('CHAPTER_NOT_FOUND','Use a Canonical chapter ID: '+', '.join(f'{c.chapter_id}={c.title}' for c in document.book.chapters))
                chapter=candidates[0].chapter_id
                classification,digest=self.classification(directory,document)
                active=self.active_path(directory,chapter)
                if active.exists() and not force:
                    generation=self.load_generation(directory,json.loads(active.read_text())['generation_id'])
                    classification,chunks=self.verify_generation(directory,document,generation)
                    if budget is not None and budget!=chunks.budget:
                        raise ProtocolError('CHUNK_CONFIG_CHANGED','Use --force to rebuild with different chunk budgets')
                else:
                    chunks=build_chunks(read_blocks(document.blocks_path),chapter,document.fingerprint.document_hash,budget)
                    generation=ChapterGeneration(generation_id=uuid4(),book_id=manifest.book.book_id,edition_id=manifest.edition.edition_id,
                        chapter_id=chapter,normalized_document_hash=document.fingerprint.document_hash,classification_hash=digest,
                        chunks_hash=json_hash(chunks.model_dump(mode='json')),resources=self.resources(classification))
                    path=self.generation_path(directory,generation.generation_id)
                    path.mkdir(parents=True)
                    write_json(path/'chunks.json',chunks)
                    write_json(path/'generation.json',generation)
                    active.parent.mkdir(parents=True,exist_ok=True)
                    write_json(active,{'generation_id':str(generation.generation_id)})
                prepared=[]
                for chunk in chunks.chunks:
                    task_id=generation.claim_tasks.get(chunk.chunk_id)
                    if task_id:
                        task_dir=safe_child(directory,f'runtime/tasks/{task_id}')
                        if self.library.database.task_status(task_id)=='completed': continue
                        prepared.append(PreparedTask(task_id,task_dir,parse_context((task_dir/'context.json').read_text())))
                        continue
                    task=self.ai._prepare_locked('extract_claims',selector,knowledge={
                        'types':[classification.primary_type.value,*[t.value for t in classification.secondary_types]],
                        'context':dict(generation=generation,classification=classification,chunk=chunk)})
                    generation.claim_tasks[chunk.chunk_id]=task.task_id
                    write_json(self.generation_path(directory,generation.generation_id)/'generation.json',generation)
                    prepared.append(task)
                if not prepared: self.collect_claims(directory,document,generation)
                return generation,prepared
        except BookDistillerError: raise
        except Exception as exc: raise StorageError(f'Chapter prepare failed: {exc}') from exc

    def fork_claims(self,selector,chapter):
        with self.library.files.locked():
            self.library._initialize();manifest,directory=self.ai._resolve(selector)
            document=load_canonical(directory,manifest,self.library.database)
            current=storage.current(directory,chapter)
            if current is None:raise ProtocolError('CLAIMS_REQUIRED','No completed chapter to reuse')
            info=json.loads((current/'chapter.json').read_text())
            old=ChapterGeneration.model_validate(info['generation'])
            classification,digest=self.classification(directory,document)
            if old.normalized_document_hash!=document.fingerprint.document_hash or old.classification_hash!=digest:
                raise ProtocolError('STALE_CONTEXT','Rerun Claims before Atoms')
            generation=old.model_copy(update={'generation_id':uuid4(),'atom_task':None})
            path=self.generation_path(directory,generation.generation_id);path.mkdir(parents=True)
            write_json(path/'generation.json',generation)
            shutil.copyfile(current/'chunks.json',path/'chunks.json')
            write_json(path/'claims-fork.json',{'path':str((current/'claims.jsonl').relative_to(directory.resolve())), 'hash':hash_source(current/'claims.jsonl')[0]})
            write_json(self.active_path(directory,chapter),{'generation_id':str(generation.generation_id)})
            self.collect_claims(directory,document,generation)
            return generation

    def pending_path(self,directory,generation):
        return safe_child(directory,f'knowledge/.pending/{generation.generation_id}')

    def collect_claims(self,directory,document,generation):
        _,chunks=self.verify_generation(directory,document,generation)
        if not (self.generation_path(directory,generation.generation_id)/'claims-fork.json').exists() and (len(generation.claim_tasks)!=len(chunks.chunks) or any(self.library.database.task_status(t)!='completed' for t in generation.claim_tasks.values())):
            raise ProtocolError('CLAIMS_INCOMPLETE','Submit every Chunk task; completed tasks will be reused')
        fork=self.generation_path(directory,generation.generation_id)/'claims-fork.json'
        if fork.exists():
            info=json.loads(fork.read_text());source=safe_child(directory,info['path'])
            if hash_source(source)[0]!=info['hash']:raise ProtocolError('STALE_CONTEXT','Reused Claims changed')
            claims=[AtomicClaim.model_validate_json(line) for line in source.read_text().splitlines()]
            from book_distiller.human.resolver import EffectiveKnowledgeResolver,ref
            resolver=EffectiveKnowledgeResolver(directory)
            claims=[AtomicClaim.model_validate(resolver.effective(ref('atomic_claim',c.generation_id,c.claim_id,c.chapter_id),c.model_dump(mode='json'))['value']|{'generation_id':str(generation.generation_id)}) for c in claims]
            return self.write_claims(directory,document,generation,chunks,claims)
        claims=[]
        for chunk in chunks.chunks:
            task_id=generation.claim_tasks[chunk.chunk_id]
            task_dir=safe_child(directory,f'runtime/tasks/{task_id}')
            accepted=safe_child(directory,f'runtime/tasks/{task_id}/accepted.json')
            task=self.library.database.lookup_task(task_id)
            request,context,_=self.ai._validate_package(task,task_dir,directory)
            result=validate_result(accepted.read_text(),request,context)
            receipt=json.loads((task_dir/'apply.json').read_text())
            if receipt['result_hash']!=json_hash(result.model_dump(mode='json',exclude={'created_at'})):
                raise ProtocolError('STALE_CONTEXT','Accepted Claim result was altered')
            meta=result.model_dump(exclude={'claims'})
            for ordinal,claim in enumerate(result.claims,1):
                claims.append(AtomicClaim(**meta,**claim.model_dump(),
                    claim_id=f'claim_{generation.chapter_id}_{chunk.chunk_id.rsplit("_",1)[1]}_{ordinal:03d}'))
        return self.write_claims(directory,document,generation,chunks,claims)

    def write_claims(self,directory,document,generation,chunks,claims):
        from book_distiller.human.locks import merge_claims
        claims=merge_claims(self.ai,directory,generation,claims)
        # Re-check the current generation and every reference before publishing Chapter Claims.
        referenced={e.block_id for c in claims for e in c.evidence}
        actual={b.block_id for b in read_blocks(document.blocks_path) if b.chapter_id==generation.chapter_id and b.block_id in referenced}
        if actual!=referenced: raise ProtocolError('RESULT_EVIDENCE_INVALID','Claim source Blocks disappeared')
        destination=self.pending_path(directory,generation)
        destination.mkdir(parents=True,exist_ok=True)
        content=''.join(c.model_dump_json()+'\n' for c in claims).encode()
        write_bytes(destination/'claims.jsonl',content)
        write_json(destination/'chunks.json',chunks)
        write_json(destination/'claims-receipt.json',{'generation':generation.model_dump(mode='json'),
            'claims_hash':hash_source(destination/'claims.jsonl')[0],'claim_count':len(claims)})
        return claims,hash_source(destination/'claims.jsonl')[0]

    def load_claims(self,directory,generation):
        path=self.pending_path(directory,generation)
        receipt=json.loads((path/'claims-receipt.json').read_text())
        source=path/'claims.jsonl'
        if source.stat().st_size>2*1024*1024:
            raise ProtocolError('CHAPTER_CONTEXT_TOO_LARGE','Chapter Claims exceed bounded atom input')
        digest=hash_source(source)[0]
        if receipt['claims_hash']!=digest or receipt['generation']['generation_id']!=str(generation.generation_id):
            raise ProtocolError('STALE_CONTEXT','Canonical Chapter Claims changed')
        claims=[AtomicClaim.model_validate_json(line) for line in source.read_text().splitlines()]
        if len(claims)!=receipt['claim_count'] or any(c.chapter_id!=generation.chapter_id or c.generation_id!=generation.generation_id for c in claims):
            raise ProtocolError('STALE_CONTEXT','Chapter Claims identity mismatch')
        return claims,digest

    def prepare_atoms(self,selector,chapter):
        try:
            with self.library.files.locked():
                self.library._initialize()
                manifest,directory=self.ai._resolve(selector)
                document=load_canonical(directory,manifest,self.library.database)
                if chapter not in {c.chapter_id for c in document.book.chapters}: raise ProtocolError('CHAPTER_NOT_FOUND',chapter)
                active=self.active_path(directory,chapter)
                if not active.exists(): raise ProtocolError('CLAIMS_REQUIRED','Prepare/submit Chapter Claims first')
                generation=self.load_generation(directory,json.loads(active.read_text())['generation_id'])
                classification,_=self.verify_generation(directory,document,generation)
                if generation.atom_task:
                    path=safe_child(directory,f'runtime/tasks/{generation.atom_task}')
                    return PreparedTask(generation.atom_task,path,parse_context((path/'context.json').read_text()))
                claims,digest=self.collect_claims(directory,document,generation)
                task=self.ai._prepare_locked('build_chapter_atoms',selector,knowledge={
                    'types':[classification.primary_type.value,*[t.value for t in classification.secondary_types]],
                    'context':dict(generation=generation,classification=classification,claims=claims,claims_hash=digest)})
                generation.atom_task=task.task_id
                write_json(self.generation_path(directory,generation.generation_id)/'generation.json',generation)
                return task
        except BookDistillerError: raise
        except Exception as exc: raise StorageError(f'Atom prepare failed: {exc}') from exc

    def rebuild(self,directory,document,context,workflow):
        generation=self.load_generation(directory,context.generation.generation_id)
        classification,chunks=self.verify_generation(directory,document,generation)
        if context.task_type=='extract_claims':
            chunk=next(c for c in chunks.chunks if c.chunk_id==context.chunk.chunk_id)
            if generation.claim_tasks.get(chunk.chunk_id)!=context.task_id:
                raise ProtocolError('STALE_CONTEXT','Task not in Chapter generation')
            return build_knowledge_context(document,context.task_id,workflow,generation,classification,chunk=chunk)
        if generation.atom_task!=context.task_id:
            raise ProtocolError('STALE_CONTEXT','Atom Task not in Chapter generation')
        claims,digest=self.load_claims(directory,generation)
        return build_knowledge_context(document,context.task_id,workflow,generation,classification,claims=claims,claims_hash=digest)

    def finish(self,directory,context):
        if context.task_type=='build_chapter_atoms':
            return directory/'knowledge/chapters'/context.generation.chapter_id
        # Each accepted Chunk is a checkpoint; canonical Chapter Claims only after all succeed.
        generation=self.load_generation(directory,context.generation.generation_id)
        if len(generation.claim_tasks) and all(self.library.database.task_status(t)=='completed' for t in generation.claim_tasks.values()):
            manifest,_=self.ai._resolve(str(generation.book_id))
            document=load_canonical(directory,manifest,self.library.database)
            self.collect_claims(directory,document,generation)
            return self.pending_path(directory,generation)/'claims.jsonl'
        return directory/f'runtime/tasks/{context.task_id}/accepted.json'

    def publish(self,directory,document,context,result):
        generation=self.load_generation(directory,context.generation.generation_id)
        self.verify_generation(directory,document,generation)
        claims,digest=self.load_claims(directory,generation)
        if digest!=result.claims_hash: raise ProtocolError('STALE_CONTEXT','Claim hash changed before publish')
        refs={e.block_id for c in claims for e in c.evidence}
        actual={b.block_id for b in read_blocks(document.blocks_path) if b.chapter_id==generation.chapter_id and b.block_id in refs}
        if actual!=refs: raise ProtocolError('RESULT_EVIDENCE_INVALID','Evidence missing at publish')
        atoms=[KnowledgeAtom(**result.model_dump(exclude={'atoms'}),**a.model_dump(),atom_id=f'atom_{generation.chapter_id}_{i:03d}') for i,a in enumerate(result.atoms,1)]
        from book_distiller.human.locks import merge_atoms
        atoms=merge_atoms(self.ai,directory,generation,atoms,claims)
        assigned={cid for a in atoms for cid in a.claim_ids}
        statements=[unicodedata.normalize('NFC',' '.join(c.statement.split())).casefold() for c in claims]
        warnings=[]
        if not claims: warnings.append('NO_CLAIMS_EXTRACTED')
        if claims and len(assigned)/len(claims)<.5: warnings.append('HIGH_UNASSIGNED_CLAIM_RATIO')
        duplicate_count=len(statements)-len(set(statements))
        if duplicate_count: warnings.append('EXACT_DUPLICATE_CLAIMS')
        metrics={'claims':len(claims),'atoms':len(atoms),'assigned_claims':len(assigned),'unassigned_claims':len(claims)-len(assigned),
            'claims_with_evidence':sum(bool(c.evidence) for c in claims),'evidence_block_validity':1.0 if refs else None,
            'duplicate_exact_claims':duplicate_count,'average_claims_per_atom':sum(len(a.claim_ids) for a in atoms)/len(atoms) if atoms else 0}
        current=storage.current(directory,generation.chapter_id)
        if current:
            published=json.loads((current/'chapter.json').read_text())['generation']
            if published['generation_id']==str(generation.generation_id) and published['atom_task']==str(result.task_id):
                import os
                return storage.Publication(directory/'knowledge/chapters'/generation.chapter_id,os.readlink(directory/'knowledge/chapters'/generation.chapter_id))
        parent=safe_child(directory,'knowledge/.generations');parent.mkdir(parents=True,exist_ok=True)
        stage=parent/f'.stage-{uuid4()}';stage.mkdir()
        try:
            for name in ('claims.jsonl','chunks.json','claims-receipt.json'):
                shutil.copyfile(self.pending_path(directory,generation)/name,stage/name)
            write_json(stage/'atoms.json',{'schema_version':'1.0','atoms':[a.model_dump(mode='json') for a in atoms]})
            from book_distiller.human.resolver import EffectiveKnowledgeResolver
            write_json(stage/'chapter.json',{'human_claims_hash':EffectiveKnowledgeResolver(directory).semantic_hash({'atomic_claim'},generation.chapter_id),'generation':generation.model_dump(mode='json'),'status':'completed',
                'normalized_document':document.fingerprint.model_dump(mode='json'),
                'normalized_blocks_path':f'.parsed-generations/{document.fingerprint.parse_task_id}/normalized/blocks.jsonl',
                'source_parse_quality':context.source_parse_quality,'chunk_count':len(generation.claim_tasks),
                'metrics':metrics,'warnings':warnings,'versions':{'chunker':'analysis-chunker-v1',
                    'claims_schema':'1.0','atoms_schema':'1.0','context_package':'1.1',
                    'workflow':result.workflow_version,'prompt':result.prompt_version},
                'artifact_hashes':{name:hash_source(stage/name)[0] for name in ('claims.jsonl','chunks.json','claims-receipt.json','atoms.json')}})
            destination=parent/f'{generation.generation_id}-{uuid4()}'
            stage.rename(destination)
            safe_child(directory,'knowledge/chapters')
            return storage.publish(directory,generation.chapter_id,destination)
        except BaseException:
            if stage.exists(): shutil.rmtree(stage)
            raise

    def describe(self,directory,document):
        total_claims=total_atoms=complete=0
        stale=0
        for chapter in document.book.chapters:
            generation=storage.current(directory,chapter.chapter_id)
            if generation is None: continue
            info=json.loads((generation/'chapter.json').read_text())
            for name,digest in info['artifact_hashes'].items():
                if name not in {'claims.jsonl','chunks.json','claims-receipt.json','atoms.json'} or hash_source(generation/name)[0]!=digest:
                    raise StorageError('Knowledge artifact integrity mismatch')
            metadata=ChapterGeneration.model_validate(info['generation'])
            _,classification_hash=self.classification(directory,document)
            from book_distiller.human.resolver import EffectiveKnowledgeResolver
            if (info.get('human_claims_hash',json_hash([]))!=EffectiveKnowledgeResolver(directory).semantic_hash({'atomic_claim'},chapter.chapter_id) or metadata.normalized_document_hash!=document.fingerprint.document_hash or metadata.classification_hash!=classification_hash):
                stale+=1;continue
            if self.library.database.task_status(metadata.atom_task)!='completed':
                raise StorageError('Chapter pending apply; resubmit its Atom task')
            complete+=1;total_claims+=info['metrics']['claims'];total_atoms+=info['metrics']['atoms']
        return {'Knowledge Chapters':f'{complete} / {len(document.book.chapters)} atomized',
            'Claims':str(total_claims),'Atoms':str(total_atoms),'Stale knowledge chapters':str(stale)}
