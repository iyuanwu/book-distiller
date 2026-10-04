"""Checkpointed Book synthesis on the shared file protocol; never calls a model."""
import json
from datetime import datetime,timezone
import shutil
import os
from pathlib import Path
from uuid import UUID, uuid4
from book_distiller.core.errors import BookDistillerError,StorageError
from book_distiller.core.models.synthesis import BOOK_WORKFLOWS,RESULT_MODELS
from book_distiller.core.models.knowledge import KnowledgeAtom,AtomicClaim
from book_distiller.pipeline.ai_tasks import safe_child,PreparedTask
from book_distiller.pipeline.canonical import load_canonical,json_hash,canonical_json
from book_distiller.pipeline.files import write_json
from book_distiller.pipeline.chapter_tasks import ChapterTasks
from book_distiller.pipeline.knowledge_context import parse_context
from book_distiller.pipeline.synthesis_context import build_synthesis_context
from book_distiller.pipeline.reduction import batches,reduced_items,expand
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.pipeline.workflows import load_workflow
from book_distiller.pipeline.book_memory import build_memory
from book_distiller.storage.filesystem import hash_source
from book_distiller.storage import knowledge as chapter_storage
from book_distiller.storage import book_knowledge as storage


def read_items(path):
    with path.open(encoding='utf-8') as stream:
        for line in stream:
            yield json.loads(line)


class BookTasks:
    def __init__(self,ai):
        self.ai=ai;self.library=ai.library;self.chapters=ChapterTasks(ai)

    def resources(self,classification):
        types=[classification.primary_type.value,*[t.value for t in classification.secondary_types]]
        return {name:json_hash({'workflow':hash_source(w.workflow_path)[0],'prompt':w.prompt_hash(),'schema':w.output_schema()})
            for name in sorted(BOOK_WORKFLOWS) for w in [load_workflow(self.ai.project,name,types)]}

    def dependencies(self,directory,document):
        classification,chash=self.chapters.classification(directory,document)
        deps={'normalized_document_hash':document.fingerprint.document_hash,'classification_hash':chash,'chapters':{},
              'resources':self.resources(classification)}
        paths={}
        for chapter in document.book.chapters:
            path=chapter_storage.current(directory,chapter.chapter_id)
            if path is None: continue
            info=json.loads((path/'chapter.json').read_text())
            meta=info['generation']
            if meta['normalized_document_hash']!=document.fingerprint.document_hash or meta['classification_hash']!=chash:
                raise ProtocolError('STALE_CONTEXT',f'{chapter.chapter_id} must be atomized for the current source/classification')
            if self.library.database.task_status(meta['atom_task'])!='completed':
                raise ProtocolError('CHAPTER_INCOMPLETE',chapter.chapter_id)
            if set(info['artifact_hashes'])!={'claims.jsonl','chunks.json','claims-receipt.json','atoms.json'}:
                raise StorageError('Incomplete Chapter artifact inventory')
            for name,digest in info['artifact_hashes'].items():
                if (path/name).is_symlink() or hash_source(path/name)[0]!=digest:
                    raise StorageError('Chapter artifact integrity mismatch')
            deps['chapters'][chapter.chapter_id]={'generation_id':meta['generation_id'],
                'path':str(path.relative_to(directory.resolve())),'manifest_hash':hash_source(path/'chapter.json')[0]}
            paths[chapter.chapter_id]=path
        return classification,deps,paths

    def state_path(self,directory,generation_id):
        from uuid import UUID
        return safe_child(directory,f'runtime/book-generations/{UUID(str(generation_id))}/state.json')

    def active_path(self,directory,mode='book',chapter=None):
        suffix='book-synthesis' if mode=='book' else f'chapter-reduce-{chapter}'
        return safe_child(directory,f'runtime/{suffix}.json')

    def save(self,directory,state):
        write_json(self.state_path(directory,state['generation_id']),state)

    def _snapshot(self,paths,destination):
        """Load at most one Chapter's Atoms, stream Claims; never read Blocks body."""
        atom_chapters={};atom_claims={}
        with destination.open('w',encoding='utf-8') as stream:
            for chapter,path in paths.items():
                terms={}
                for claim in read_items(path/'claims.jsonl'):
                    c=AtomicClaim.model_validate(claim)
                    terms[c.claim_id]=c.concept_terms
                for raw in json.loads((path/'atoms.json').read_text())['atoms']:
                    atom=KnowledgeAtom.model_validate(raw)
                    if atom.chapter_id!=chapter or atom.atom_id in atom_chapters or not set(atom.claim_ids)<=terms.keys():
                        raise ProtocolError('INVALID_SOURCE_REFERENCE','Chapter atom identity or claims invalid')
                    atom_chapters[atom.atom_id]=chapter;atom_claims[atom.atom_id]=atom.claim_ids
                    item={'id':atom.atom_id,'title':atom.title,'summary':atom.summary,'reasoning':atom.reasoning,
                        'examples':atom.examples,'concept_terms':sorted(set(atom.concept_terms)|{t for cid in atom.claim_ids for t in terms[cid]}),
                        'claim_ids':atom.claim_ids,'chapters':[chapter]}
                    stream.write(canonical_json(item)+'\n')
        return atom_chapters,atom_claims

    def _new(self,directory,document,classification,deps,paths,mode='book',chapter=None,claims_path=None):
        gid=str(uuid4());root=self.state_path(directory,gid).parent;root.mkdir(parents=True)
        if mode=='book':
            atom_chapters,atom_claims=self._snapshot(paths,root/'items.jsonl')
        else:
            atom_chapters={};atom_claims={}
            with (root/'items.jsonl').open('w',encoding='utf-8') as stream:
                for c in read_items(claims_path):
                    item={'id':c['claim_id'],'title':c['claim_type'],'summary':c['statement'],
                        'concept_terms':c['concept_terms'],'chapters':[chapter]}
                    atom_chapters[c['claim_id']]=chapter
                    stream.write(canonical_json(item)+'\n')
        previous=storage.current(directory) if mode=='book' else None
        registry=json.loads((previous/'concepts.json').read_text())['concepts'] if previous else []
        state={'generation_id':gid,'mode':mode,'chapter_id':chapter,'dependencies':deps,'dependency_hash':json_hash(deps),
            'classification':{'primary_type':classification.primary_type.value,'secondary_types':[v.value for v in classification.secondary_types],
                              'tags':classification.tags},'source_hash':hash_source(root/'items.jsonl')[0],
            'previous_book_hash':hash_source(previous/'book_model.json')[0] if previous else None,
            'existing_concepts':registry,'atom_chapters':atom_chapters,'atom_claims':atom_claims,'lineage':{},
            'frontier':'items.jsonl','level':0,'batch_index':0,'step':'reduce','pending':None,'plans':{},'tasks':[],
            'concepts':[],'core_ideas':[],'mental_models':[],'meta_principles':[],'decisions':[], 'completed':False}
        self.save(directory,state)
        write_json(self.active_path(directory,mode,chapter),{'generation_id':gid})
        return state

    def verify(self,directory,document,state):
        if state['mode']=='book':
            _,deps,_=self.dependencies(directory,document)
        else:
            _,deps=self.chapter_dependencies(directory,document,state['chapter_id'])
        if deps!=state['dependencies']:
            raise ProtocolError('STALE_CONTEXT','Chapter generations, source, classification or workflow changed; start a fresh generation')
        active=json.loads(self.active_path(directory,state['mode'],state['chapter_id']).read_text())
        if active['generation_id']!=state['generation_id']: raise ProtocolError('STALE_CONTEXT','Generation superseded')
        root=self.state_path(directory,state['generation_id']).parent
        if hash_source(root/'items.jsonl')[0]!=state['source_hash']: raise ProtocolError('STALE_CONTEXT','Input snapshot changed')
        if state['mode']=='book':
            previous=storage.current(directory)
            if previous:
                model=json.loads((previous/'book_model.json').read_text())
                if model['generation_id']!=state['generation_id'] and hash_source(previous/'book_model.json')[0]!=state['previous_book_hash']:
                    raise ProtocolError('STALE_CONTEXT','Existing Concept Registry changed')
            elif state['previous_book_hash'] is not None: raise ProtocolError('STALE_CONTEXT','Existing Book Model disappeared')

    def _payload(self,state,items):
        payload={'items':items}
        step=state['step']
        if step=='normalize_concepts':
            payload['existing_concepts']=[{k:c[k] for k in ('concept_id','canonical_name','zh_name','aliases','source_terms','relations')} for c in state['existing_concepts']]
        else:
            payload['concepts']=[{k:c[k] for k in ('concept_id','canonical_name','zh_name','aliases','source_terms')} for c in state['concepts']]
        if step in {'build_mental_models','build_meta_principles'}:
            payload['core_ideas']=[{k:i[k] for k in ('core_idea_id','title','statement','supporting_chapters','concept_ids')} for i in state['core_ideas']]
        if step=='build_meta_principles':
            payload['mental_models']=state['mental_models_context']
        return payload

    def _prepare(self,directory,selector,document,state,name,payload):
        scope={'generation_id':state['generation_id'],'mode':state['mode'],'chapter_id':state['chapter_id'],
            'dependencies':state['dependencies'],'dependency_hash':state['dependency_hash'],
            'step':state['step'],'level':state['level'],'batch_index':state['batch_index']}
        task=self.ai._prepare_locked(name,selector,synthesis={'types':[state['classification']['primary_type'],*state['classification']['secondary_types']],
            'context':{'scope':scope,'payload':payload,'classification':state['classification']}})
        state['pending']=str(task.task_id)
        state['plans'][str(task.task_id)]={'name':name,'scope':scope,'payload':payload}
        self.save(directory,state)
        return task

    def rebuild(self,directory,document,context,workflow):
        state=json.loads(self.state_path(directory,context.scope['generation_id']).read_text())
        self.verify(directory,document,state)
        plan=state['plans'].get(str(context.task_id))
        if plan is None or plan['name']!=context.task_type: raise ProtocolError('STALE_CONTEXT','Task not in generation')
        return build_synthesis_context(document,context.task_id,workflow,plan['scope'],plan['payload'],state['classification'])

    def _consume(self,directory,document,state):
        tid=state['pending'];task_dir=safe_child(directory,f'runtime/tasks/{tid}')
        task=self.library.database.lookup_task(tid)
        if task['status']!='completed':
            return PreparedTask(UUID(task['task_id']),task_dir,parse_context((task_dir/'context.json').read_text()))
        from book_distiller.pipeline.result_validation import validate_result
        request,context,_=self.ai._validate_package(task,task_dir,directory)
        result=validate_result((task_dir/'accepted.json').read_text(),request,context)
        receipt=json.loads((task_dir/'apply.json').read_text())
        if json_hash(result.model_dump(mode='json',exclude={'created_at'}))!=receipt['result_hash']:
            raise ProtocolError('STALE_CONTEXT','Accepted synthesis result changed')
        items=context.payload.get('items',[]);available={i['id'] for i in items}
        root=self.state_path(directory,state['generation_id']).parent
        if context.task_type=='synthesis_reduce':
            output=reduced_items(result.groups,items,state['lineage'],f"group_{state['level']:03d}_{state['batch_index']:04d}")
            write_json(root/f"reduce-{state['level']}-{state['batch_index']}.json",{'items':output})
            state['batch_index']+=1
        elif context.task_type=='normalize_concepts':
            state['concepts']=[c.model_dump(mode='json') for c in result.concepts]
            for c in state['concepts']: c['atom_ids']=expand(c['atom_ids'],available,state['lineage'])
            state['step']='build_core_ideas'
        elif context.task_type=='build_core_ideas':
            state['core_ideas']=[i.model_dump(mode='json') for i in result.core_ideas]
            state['decisions']=[d.model_dump(mode='json') for d in result.decisions]
            for idea in state['core_ideas']: idea['atom_ids']=expand(idea['atom_ids'],available,state['lineage'])
            for d in state['decisions']: d['source_atom_ids']=expand(d['source_atom_ids'],available,state['lineage'])
            state['step']='build_mental_models'
        elif context.task_type=='build_mental_models':
            state['mental_models']=[m.model_dump(mode='json') for m in result.mental_models]
            ideas={i['core_idea_id']:i for i in state['core_ideas']}
            state['mental_models_context']=[]
            for m in state['mental_models']:
                m['atom_ids']=expand(m['atom_ids'],available,state['lineage'])
                chapters={state['atom_chapters'][a] for a in m['atom_ids']}
                chapters|={ch for i in m['core_idea_ids'] for ch in ideas[i]['supporting_chapters']}
                state['mental_models_context'].append({k:v for k,v in m.items() if k!='atom_ids'}|{'supporting_chapters':sorted(chapters)})
            state['step']='build_meta_principles'
        elif context.task_type=='build_meta_principles':
            state['meta_principles']=[p.model_dump(mode='json') for p in result.meta_principles]
            state['step']='publish'
        else:
            state['chapter_atoms']=[a.model_dump(mode='json') for a in result.atoms]
            for atom in state['chapter_atoms']:
                atom['claim_ids']=expand(atom['claim_ids'],available,state['lineage'])
            state['step']='publish';state['final_task']=tid
        state['tasks'].append(tid);state['pending']=None
        self.save(directory,state)
        return None

    def advance(self,directory,selector,document,state):
        self.verify(directory,document,state)
        if state['completed']:
            return directory/'knowledge/book' if state['mode']=='book' else directory/'knowledge/chapters'/state['chapter_id']
        if state['pending']:
            pending=self._consume(directory,document,state)
            if pending: return pending
        root=self.state_path(directory,state['generation_id']).parent
        if state['step']=='reduce':
            count=0;first=None
            for group in batches(read_items(root/state['frontier'])):
                count+=1
                if first is None: first=group
                if count-1==state['batch_index'] and (count>1 or state['batch_index']>0):
                    return self._prepare(directory,selector,document,state,'synthesis_reduce',{'items':group})
            if count<=1 and state['batch_index']==0:
                state['step']='normalize_concepts' if state['mode']=='book' else 'reduce_chapter_atoms'
                self.save(directory,state)
            elif state['batch_index']==0:
                return self._prepare(directory,selector,document,state,'synthesis_reduce',{'items':first})
            else:
                # Every batch was accepted. Runtime aggregation is rewritten, never appended.
                next_path=root/f"level-{state['level']+1}.jsonl"
                with next_path.open('w',encoding='utf-8') as stream:
                    for i in range(count):
                        for item in json.loads((root/f"reduce-{state['level']}-{i}.json").read_text())['items']:
                            stream.write(canonical_json(item)+'\n')
                state['level']+=1;state['frontier']=next_path.name;state['batch_index']=0
                if state['level']>20: raise ProtocolError('REDUCE_NOT_PROGRESSING','Reduce exceeded finite depth')
                self.save(directory,state)
                return self.advance(directory,selector,document,state)
        if state['step']=='publish':
            pointer=directory/'knowledge/book' if state['mode']=='book' else directory/'knowledge/chapters'/state['chapter_id']
            previous=os.readlink(pointer) if pointer.is_symlink() else None
            path=self.publish(directory,document,state) if state['mode']=='book' else self.publish_chapter(directory,document,state)
            try:
                state['completed']=True;self.save(directory,state)
            except BaseException:
                chapter_storage.Publication(pointer,previous).restore()
                raise
            return path
        items=list(read_items(root/state['frontier']))
        return self._prepare(directory,selector,document,state,state['step'],self._payload(state,items))

    def prepare(self,selector,force=False):
        try:
            with self.library.files.locked():
                self.library._initialize();manifest,directory=self.ai._resolve(selector)
                document=load_canonical(directory,manifest,self.library.database)
                classification,deps,paths=self.dependencies(directory,document)
                if not paths: raise ProtocolError('CHAPTERS_REQUIRED','Complete at least one Chapter before Book synthesis')
                active=self.active_path(directory)
                if active.exists() and not force:
                    state=json.loads(self.state_path(directory,json.loads(active.read_text())['generation_id']).read_text())
                else: state=self._new(directory,document,classification,deps,paths)
                return self.advance(directory,selector,document,state)
        except BookDistillerError: raise
        except Exception as exc: raise StorageError(f'Book synthesis failed: {exc}') from exc

    def publish(self,directory,document,state):
        self.verify(directory,document,state)
        assigned={a for i in state['core_ideas'] for a in i['atom_ids']}
        metrics={'concept_count':len(state['concepts']),'core_idea_count':len(state['core_ideas']),
            'mental_model_count':len(state['mental_models']),'meta_principle_count':len(state['meta_principles']),
            'atoms_assigned_to_core_ideas':len(assigned),'orphan_atoms':len(set(state['atom_chapters'])-assigned),
            'core_ideas_with_multiple_chapters':sum(len(i['supporting_chapters'])>1 for i in state['core_ideas']),
            'models_with_support':len(state['mental_models']),'principles_with_support':len(state['meta_principles']),
            'relation_count':len(state['decisions'])+sum(len(c['relations']) for c in state['concepts'])}
        warnings=[]
        if metrics['core_ideas_with_multiple_chapters']==0: warnings.append('NO_CROSS_CHAPTER_CORE_IDEAS')
        if metrics['orphan_atoms']>len(state['atom_chapters'])/2: warnings.append('ORPHAN_ATOMS_HIGH')
        from book_distiller.pipeline.book_references import bind_objects,atom_ref
        identity={'book_id':str(document.book.book_id),'edition_id':str(document.book.edition_id),
            'generation_id':state['generation_id'],'dependency_hash':state['dependency_hash'],'chapter_generations':{k:v['generation_id'] for k,v in state['dependencies']['chapters'].items()}}
        outline=[{'chapter_id':c.chapter_id,'title':c.title[:120],'status':'atomized' if c.chapter_id in state['dependencies']['chapters'] else 'unavailable'} for c in document.book.chapters]
        memory=build_memory(identity,state['classification'],outline,state['concepts'],state['core_ideas'],state['mental_models'],state['meta_principles'],state['decisions'],source_refs={a:atom_ref(state,a) for a in state['atom_chapters']})
        parent=safe_child(directory,'knowledge/.book-generations');parent.mkdir(parents=True,exist_ok=True)
        stage=parent/f'.stage-{uuid4()}';stage.mkdir()
        try:
            for key in ('concepts','core_ideas','mental_models','meta_principles'):
                write_json(stage/f'{key}.json',{'schema_version':'1.0','generation_id':state['generation_id'],key:bind_objects(state,key)})
            write_json(stage/'relationships.json',{'schema_version':'1.0','decisions':bind_objects(state,'decisions')})
            write_json(stage/'book_memory.json',memory)
            write_json(stage/'provenance.json',{'dependencies':state['dependencies'],'tasks':state['tasks'],'lineage':state['lineage'],
                'atom_chapters':state['atom_chapters'],'atom_claims':state['atom_claims']})
            write_json(stage/'book_model.json',{'schema_version':'1.0',**identity,'dependencies':state['dependencies'],
                'artifacts':{key:f'{key}.json' for key in ('concepts','core_ideas','mental_models','meta_principles','relationships','book_memory')},
                'created_at':datetime.now(timezone.utc).isoformat(),'metrics':metrics,'warnings':warnings,'source_parse_quality':document.quality.status,
                'artifact_hashes':{p.name:hash_source(p)[0] for p in stage.iterdir()},'book_memory_hash':memory['book_memory_hash']})
            destination=parent/f"{state['generation_id']}-{uuid4()}";stage.rename(destination)
            storage.publish(directory,destination)
            return directory/'knowledge/book'
        except BaseException:
            if stage.exists(): shutil.rmtree(stage)
            raise

    def chapter_dependencies(self,directory,document,chapter):
        if chapter not in {c.chapter_id for c in document.book.chapters}:
            raise ProtocolError('CHAPTER_NOT_FOUND',chapter)
        active=self.chapters.active_path(directory,chapter)
        if not active.exists(): raise ProtocolError('CLAIMS_REQUIRED','Complete Chapter Claims first')
        generation=self.chapters.load_generation(directory,json.loads(active.read_text())['generation_id'])
        classification,_=self.chapters.verify_generation(directory,document,generation)
        pending=self.chapters.pending_path(directory,generation)
        if not (pending/'claims-receipt.json').exists():
            raise ProtocolError('CLAIMS_INCOMPLETE','Submit every Claim task first')
        receipt=json.loads((pending/'claims-receipt.json').read_text())
        digest=hash_source(pending/'claims.jsonl')[0]
        if digest!=receipt['claims_hash'] or receipt['generation']['generation_id']!=str(generation.generation_id):
            raise ProtocolError('STALE_CONTEXT','Claim snapshot changed')
        if any(self.library.database.task_status(t)!='completed' for t in generation.claim_tasks.values()):
            raise ProtocolError('CLAIMS_INCOMPLETE','Claim tasks incomplete')
        return classification,{'chapter_generation_id':str(generation.generation_id),'chapter_id':chapter,
            'normalized_document_hash':document.fingerprint.document_hash,'classification_hash':generation.classification_hash,
            'claims_hash':digest,'claim_count':receipt['claim_count'],'resources':self.resources(classification)}

    def prepare_chapter(self,selector,chapter,force=False):
        try:
            with self.library.files.locked():
                self.library._initialize();manifest,directory=self.ai._resolve(selector)
                document=load_canonical(directory,manifest,self.library.database)
                classification,deps=self.chapter_dependencies(directory,document,chapter)
                active=self.active_path(directory,'chapter',chapter)
                if active.exists() and not force:
                    state=json.loads(self.state_path(directory,json.loads(active.read_text())['generation_id']).read_text())
                else:
                    generation=self.chapters.load_generation(directory,deps['chapter_generation_id'])
                    state=self._new(directory,document,classification,deps,{},'chapter',chapter,
                        self.chapters.pending_path(directory,generation)/'claims.jsonl')
                return self.advance(directory,selector,document,state)
        except BookDistillerError: raise
        except Exception as exc: raise StorageError(f'Chapter reduce failed: {exc}') from exc

    def publish_chapter(self,directory,document,state):
        self.verify(directory,document,state)
        from book_distiller.normalize.book import read_blocks
        generation=self.chapters.load_generation(directory,state['dependencies']['chapter_generation_id'])
        pending=self.chapters.pending_path(directory,generation)
        ids=set();refs=set();count=0
        for raw in read_items(pending/'claims.jsonl'):
            c=AtomicClaim.model_validate(raw)
            if c.chapter_id!=generation.chapter_id or c.generation_id!=generation.generation_id or c.claim_id in ids:
                raise ProtocolError('INVALID_SOURCE_REFERENCE','Invalid Chapter Claims identity')
            ids.add(c.claim_id);refs.update(e.block_id for e in c.evidence);count+=1
        if count!=state['dependencies']['claim_count']:
            raise ProtocolError('STALE_CONTEXT','Claim count changed')
        actual={b.block_id for b in read_blocks(document.blocks_path) if b.chapter_id==generation.chapter_id and b.block_id in refs}
        if actual!=refs: raise ProtocolError('RESULT_EVIDENCE_INVALID','Evidence missing at publication')
        task_dir=safe_child(directory,f"runtime/tasks/{state['final_task']}")
        result=RESULT_MODELS['reduce_chapter_atoms'].model_validate_json((task_dir/'accepted.json').read_text())
        metadata=result.model_dump(exclude={'atoms','dependency_hash','input_hash'})
        metadata.update(generation_id=generation.generation_id,chapter_id=generation.chapter_id,
            claims_hash=state['dependencies']['claims_hash'])
        atoms=[]
        for ordinal,a in enumerate(state['chapter_atoms'],1):
            if not set(a['claim_ids'])<=ids: raise ProtocolError('INVALID_SOURCE_REFERENCE','Unknown Claim')
            atoms.append(KnowledgeAtom(**metadata,**a,atom_id=f'atom_{generation.chapter_id}_{ordinal:03d}'))
        assigned={c for a in atoms for c in a.claim_ids}
        metrics={'claims':count,'atoms':len(atoms),'assigned_claims':len(assigned),'unassigned_claims':count-len(assigned),
            'claims_with_evidence':count,'evidence_block_validity':1.0 if refs else None,
            'average_claims_per_atom':sum(len(a.claim_ids) for a in atoms)/len(atoms) if atoms else 0}
        generation.atom_task=result.task_id
        parent=safe_child(directory,'knowledge/.generations');parent.mkdir(parents=True,exist_ok=True)
        stage=parent/f'.stage-{uuid4()}';stage.mkdir()
        publication=None
        previous=self.chapters.load_generation(directory,generation.generation_id)
        try:
            for name in ('claims.jsonl','chunks.json','claims-receipt.json'):
                shutil.copyfile(pending/name,stage/name)
            write_json(stage/'atoms.json',{'schema_version':'1.0','atoms':[a.model_dump(mode='json') for a in atoms]})
            write_json(stage/'chapter.json',{'generation':generation.model_dump(mode='json'),'status':'completed',
                'normalized_document':document.fingerprint.model_dump(mode='json'),
                'normalized_blocks_path':f'.parsed-generations/{document.fingerprint.parse_task_id}/normalized/blocks.jsonl',
                'source_parse_quality':document.quality.status,'chunk_count':len(generation.claim_tasks),
                'metrics':metrics,'warnings':[],'versions':{'context_package':'1.2','workflow':result.workflow_version,'prompt':result.prompt_version},
                'reduction':{'generation_id':state['generation_id'],'tasks':state['tasks'],'lineage':state['lineage']},
                'artifact_hashes':{name:hash_source(stage/name)[0] for name in ('claims.jsonl','chunks.json','claims-receipt.json','atoms.json')}})
            destination=parent/f'{generation.generation_id}-{uuid4()}';stage.rename(destination)
            write_json(self.chapters.generation_path(directory,generation.generation_id)/'generation.json',generation)
            publication=chapter_storage.publish(directory,generation.chapter_id,destination)
            return directory/'knowledge/chapters'/generation.chapter_id
        except BaseException:
            if publication: publication.restore()
            write_json(self.chapters.generation_path(directory,generation.generation_id)/'generation.json',previous)
            if stage.exists(): shutil.rmtree(stage)
            raise

    def describe(self,directory,document):
        path=storage.current(directory)
        if path is None: return {'Book synthesis':'unavailable'}
        model=json.loads((path/'book_model.json').read_text())
        try:
            _,deps,_=self.dependencies(directory,document)
            status='completed' if deps==model['dependencies'] else 'stale'
        except BookDistillerError: status='stale'
        return {'Book synthesis':status,**{label:str(model['metrics'][field]) for label,field in
            [('Concepts','concept_count'),('Core Ideas','core_idea_count'),('Mental Models','mental_model_count'),('Meta Principles','meta_principle_count')]}}
