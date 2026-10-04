"""Resumable local verification planner on the shared AI Task protocol."""
import json,os,shutil
from pathlib import Path
from uuid import UUID,uuid4
from book_distiller.core.errors import BookDistillerError,StorageError
from book_distiller.core.models.verification import VERIFY_WORKFLOWS,VERIFY_NAMES,EvidenceAssessment,ReviewIssue,Citation
from book_distiller.pipeline.ai_tasks import PreparedTask,safe_child
from book_distiller.pipeline.canonical import load_canonical,json_hash
from book_distiller.pipeline.files import write_json,write_bytes
from book_distiller.pipeline.book_tasks import BookTasks
from book_distiller.pipeline.workflows import load_workflow
from book_distiller.pipeline.knowledge_context import parse_context
from book_distiller.pipeline.verification_context import build_verification_context
from book_distiller.pipeline.result_validation import ProtocolError,validate_result
from book_distiller.evidence.citations import SourceIndex,make_citation,resolve_citation
from book_distiller.evidence.graph import snapshot,read_object
from book_distiller.quality.review import assessment_issues,build_report,render_report
from book_distiller.storage.filesystem import hash_source
from book_distiller.storage import book_knowledge,verification as storage

class VerificationTasks:
    def __init__(self,ai):self.ai=ai;self.library=ai.library;self.books=BookTasks(ai)
    def rules(self):return json.loads((self.ai.project/'rules/quality/standard.json').read_text())
    def dependencies(self,directory,document,*,allow_missing_reference=False):
        book=book_knowledge.current(directory)
        if book is None:raise ProtocolError('BOOK_SYNTHESIS_REQUIRED','Complete Book synthesis first')
        model=json.loads((book/'book_model.json').read_text())
        if (model['book_id'],model['edition_id'])!=(str(document.book.book_id),str(document.book.edition_id)):
            raise ProtocolError('BROKEN_CITATION','Book model identity mismatch')
        classification,deps,_=self.books.dependencies(directory,document)
        if deps!=model['dependencies']:raise ProtocolError('STALE_CONTEXT','Book synthesis is stale; rerun it before verification')
        types=[classification.primary_type.value,*[t.value for t in classification.secondary_types]]
        resources={n:json_hash({'workflow':hash_source(w.workflow_path)[0],'prompt':w.prompt_hash(),'schema':w.output_schema()}) for n in sorted(VERIFY_WORKFLOWS) for w in [load_workflow(self.ai.project,n,types)]}
        manifest,_=self.ai._resolve(str(document.book.book_id))
        source=Path(manifest.source.stored_path) if manifest.source.copy_mode=='reference' else directory/manifest.source.stored_path
        missing_reference=allow_missing_reference and manifest.source.copy_mode=='reference' and not source.exists()
        if not missing_reference and (not source.exists() or hash_source(source)[0]!=document.book.source_sha256):raise ProtocolError('SOURCE_INTEGRITY_FAILURE','Original source missing or changed')
        return classification,{'book_generation_id':model['generation_id'],'book_manifest_hash':hash_source(book/'book_model.json')[0],
            'book_path':str(book.relative_to(directory)),'knowledge_dependencies':deps,'rules_hash':json_hash(self.rules()),'resources':resources},book,model
    def root(self,directory,gid):return safe_child(directory,f'runtime/verification-generations/{UUID(str(gid))}')
    def save(self,directory,state):write_json(self.root(directory,state['generation_id'])/'state.json',state)
    def verify(self,directory,document,state):
        _,deps,_,_=self.dependencies(directory,document)
        if deps!=state['dependencies']:raise ProtocolError('STALE_CONTEXT','Verification dependencies changed')
        if json.loads(safe_child(directory,'runtime/verification-active.json').read_text())['generation_id']!=state['generation_id']:
            raise ProtocolError('STALE_CONTEXT','Verification generation superseded')
    def new(self,directory,document,classification,deps,book,model):
        gid=str(uuid4());root=self.root(directory,gid);root.mkdir(parents=True)
        source=SourceIndex(document.blocks_path);entries,order=snapshot(directory,book,model,root,source)
        rules=self.rules();plan=[{'workflow':VERIFY_NAMES[entries[k]['ref']['object_type']],'key':k,'attempt':0} for k in order]
        coverage=[]
        for ch in source.chapters:
            for ordinal,bids in enumerate(source.batches(ch,rules['coverage_batch_blocks'],rules['max_source_chars'])):
                item={'workflow':'review_chapter_coverage','chapter_id':ch,'block_ids':bids,'batch':ordinal};plan.append(item);coverage.append(item)
        # Review only Phase 5 relation/Concept candidates, never all N^2 pairs.
        concepts=json.loads((book/'concepts.json').read_text())['concepts'];decisions=json.loads((book/'relationships.json').read_text())['decisions']
        candidates=[{'concept':c} for c in concepts]+[{'decision':d} for d in decisions]
        for i in range(0,len(candidates),rules['quality_batch_items']):plan.append({'workflow':'review_quality','candidates':candidates[i:i+rules['quality_batch_items']]})
        if not candidates:plan.append({'workflow':'review_quality','candidates':[]})
        state={'generation_id':gid,'dependencies':deps,'dependency_hash':json_hash(deps),'rules':rules,
            'classification':{'primary_type':classification.primary_type.value,'secondary_types':[v.value for v in classification.secondary_types],'tags':classification.tags},
            'classification_hash':deps['knowledge_dependencies']['classification_hash'],'entries':entries,'order':order,'plan':plan,
            'cursor':0,'pending':None,'task_plans':{},'tasks':[],'assessments':{},'citations':{},'coverage':[],'issues':[],'rechecks':{},'completed':False}
        self.save(directory,state);write_json(safe_child(directory,'runtime/verification-active.json'),{'generation_id':gid})
        return state
    def assessment(self,root,state,key):
        data=state['assessments'].get(key)
        if data is None:return None
        path=safe_child(root,data['file'])
        if hash_source(path)[0]!=data['hash']:raise ProtocolError('STALE_CONTEXT','Assessment checkpoint changed')
        return json.loads(path.read_text())
    def payload(self,directory,document,state,item):
        root=self.root(directory,state['generation_id']);source=SourceIndex(document.blocks_path);rules=state['rules'];entries=state['entries']
        target=None;lower=[];lower_assessments=[];objects=[];primary=[];available=0;selected_lower=0
        if item.get('key'):
            entry=entries[item['key']];target=read_object(root,entry);objects=[target];available=len(entry['lower'])
            lower_keys=entry['lower'][:rules['max_lower_objects']]
            lower=[read_object(root,entries[k]) for k in lower_keys];selected_lower=len(lower)
            lower_assessments=[self.assessment(root,state,k) for k in lower_keys]
            if any(a is None for a in lower_assessments):raise ProtocolError('LOWER_VERIFICATION_REQUIRED','Verify lower objects first')
            if lower:
                primary=sorted({state['citations'][cid]['block_id'] for a in lower_assessments for cid in a['citation_ids']})
                if not primary:primary=sorted({b for k in lower_keys for b in entries[k]['source_blocks']})
            else:primary=entry['source_blocks']
        elif item['workflow']=='review_chapter_coverage':
            primary=item['block_ids']
            keys=[k for k in state['order'] if entries[k]['ref']['chapter_id']==item['chapter_id'] and set(entries[k]['source_blocks'])&set(primary)]
            available=len(keys);keys=keys[:rules['max_lower_objects']];objects=[read_object(root,entries[k]) for k in keys];selected_lower=len(keys)
        else:
            keys=set()
            for candidate in item['candidates']:
                value=candidate.get('concept',candidate.get('decision'));keys.update(value.get('atom_ids',value.get('source_atom_ids',[])))
            if not keys<=entries.keys():raise ProtocolError('BROKEN_CITATION','Concept/relation references missing Atom')
            ordered=sorted(keys);available=len(ordered);selected=ordered[:rules['max_lower_objects']]
            objects=[read_object(root,entries[k]) for k in selected];selected_lower=len(selected)
            primary=sorted({b for k in selected for b in entries[k]['source_blocks']})
        radius=rules['recheck_neighbors'] if item['workflow']=='repair_evidence' else rules['initial_neighbors'] if target and target['ref']['object_type']=='atomic_claim' else 0
        selected=source.window(primary,radius);citations=[];excerpts=[];chars=0;omitted=[]
        # Primary source is never silently clipped. Bounded high-level sampling is explicit.
        for bid in [b for b in selected if b in primary]+[b for b in selected if b not in primary]:
            block=source.get(bid)
            if chars+len(block.text)>rules['max_source_chars']:
                if target and target['ref']['object_type']=='atomic_claim' and bid in primary:
                    raise ProtocolError('VERIFICATION_CONTEXT_TOO_LARGE','Required Claim evidence exceeds local source budget')
                omitted.append(bid);continue
            chars+=len(block.text);cit=make_citation(document,block,source.window([bid],1)).model_dump(mode='json')
            citations.append(cit);excerpts.append({'citation_id':cit['citation_id'],'block_id':bid,'chapter_id':block.chapter_id,'section_id':block.section_id,'text':block.text})
        original=[c['citation_id'] for c in citations if c['block_id'] in primary]
        if item['workflow']=='repair_evidence':
            original=self.assessment(root,state,item['key'])['original_citation_ids']
        semantic={'statement','title','summary','name','zh_name','description','mechanism','when_to_use','limitations','reasoning','examples','concept_terms','claim_type','atom_type','importance','confidence','promotion_reason','source_type'}
        def compact(obj):return {'ref':obj['ref'],'value':{k:v for k,v in obj['value'].items() if k in semantic}}
        target=compact(target) if target else None
        objects=[compact(o) for o in objects];lower=[compact(o) for o in lower]
        lower_assessments=[{k:a[k] for k in ('object_ref','citation_ids','evidence_strength','fidelity_verdict','unsupported_aspects','verification_summary','reviewer_confidence')} for a in lower_assessments]
        payload={'target':target,'objects':objects,'lower_objects':lower,'lower_assessments':lower_assessments,
            'lower_objects_available':available,'lower_objects_reviewed':selected_lower,'citations':citations,'source_excerpts':excerpts,
            'original_citation_ids':original,'chapter_ids':sorted({source.entries[b]['chapter_id'] for b in selected}),
            'omitted_source_blocks':omitted,'reviewed_block_ids':item.get('block_ids',[]),'candidates':item.get('candidates',[])}
        if item['workflow']=='repair_evidence':payload['previous_assessment']=self.assessment(root,state,item['key'])
        if item['workflow']=='review_chapter_coverage':
            ch=next(c for c in document.book.chapters if c.chapter_id==item['chapter_id']);payload['chapter_outline']=ch.model_dump(mode='json')
            dep=state['dependencies']['knowledge_dependencies']['chapters'].get(item['chapter_id'])
            if dep:
                chunks=json.loads((safe_child(directory,dep['path'])/'chunks.json').read_text())['chunks']
                payload['analysis_chunks']=[c for c in chunks if set(c['primary_blocks'])&set(primary)]
            else:payload['analysis_chunks']=[]
        return payload
    def prepare_task(self,directory,selector,document,state,item):
        scope={'generation_id':state['generation_id'],'dependency_hash':state['dependency_hash'],'classification_hash':state['classification_hash'],
            'rules':state['rules'],'item':item}
        payload=self.payload(directory,document,state,item)
        task=self.ai._prepare_locked(item['workflow'],selector,verification={'types':[state['classification']['primary_type'],*state['classification']['secondary_types']],
            'context':{'scope':scope,'payload':payload,'classification':state['classification']}})
        state['pending']=str(task.task_id);state['task_plans'][str(task.task_id)]=item;self.save(directory,state);return task
    def rebuild(self,directory,document,context,workflow):
        state=json.loads((self.root(directory,context.scope['generation_id'])/'state.json').read_text());self.verify(directory,document,state)
        item=state['task_plans'].get(str(context.task_id))
        if item is None or item!=context.scope['item']:raise ProtocolError('STALE_CONTEXT','Task outside verification plan')
        # Recheck tasks use their immutable pre-recheck assessment, not a later accepted revision.
        payload=self.payload(directory,document,state,item)
        if item['workflow']=='repair_evidence' and item.get('prior_assessment'):
            payload['previous_assessment']=item['prior_assessment'];payload['original_citation_ids']=item['prior_assessment']['original_citation_ids']
        return build_verification_context(document,context.task_id,workflow,context.scope,payload,state['classification'])
    def consume(self,directory,document,state):
        tid=state['pending'];task=self.library.database.lookup_task(tid);taskdir=safe_child(directory,f'runtime/tasks/{tid}')
        if task['status']!='completed':return PreparedTask(UUID(tid),taskdir,parse_context((taskdir/'context.json').read_text()))
        request,context,_=self.ai._validate_package(task,taskdir,directory);result=validate_result((taskdir/'accepted.json').read_text(),request,context)
        if json_hash(result.model_dump(mode='json',exclude={'created_at'}))!=json.loads((taskdir/'apply.json').read_text())['result_hash']:
            raise ProtocolError('STALE_CONTEXT','Accepted evaluation changed')
        item=state['task_plans'][tid];root=self.root(directory,state['generation_id'])
        for c in context.payload['citations']:state['citations'][c['citation_id']]=c
        if hasattr(result,'assessment'):
            key=item['key'];a=EvidenceAssessment(**result.model_dump(exclude={'assessment'}),**result.assessment.model_dump(),
                assessment_id='assessment_'+json_hash({'generation':state['generation_id'],'ref':result.assessment.object_ref.model_dump(mode='json')})[:24],
                auto_recheck_attempted=item['workflow']=='repair_evidence',original_citation_ids=context.payload['original_citation_ids'],
                lower_objects_reviewed=context.payload['lower_objects_reviewed'],lower_objects_available=context.payload['lower_objects_available'],
                source_selection_complete=not context.payload['omitted_source_blocks'])
            path=root/f'assessment-{key}-{item.get("attempt",0)}.json';write_json(path,a)
            state['assessments'][key]={'file':path.name,'hash':hash_source(path)[0]}
            if (a.object_ref.object_type=='atomic_claim' and a.evidence_strength in {'weak','insufficient'} and
                'context_insufficient' in a.reason_codes and state['rechecks'].get(key,0)<min(1,state['rules']['max_rechecks'])):
                state['rechecks'][key]=1
                state['plan'].insert(state['cursor']+1,{'workflow':'repair_evidence','key':key,'attempt':1,'prior_assessment':a.model_dump(mode='json')})
        else:
            if context.payload['omitted_source_blocks'] or context.payload['lower_objects_reviewed']<context.payload['lower_objects_available']:
                state['issues'].append(ReviewIssue(issue_id='issue_'+tid,issue_type='LOW_TRACEABILITY',severity='major',
                    summary='Bounded review Context omitted lower objects or source Blocks; this is not complete evidence coverage.',
                    suggested_action='inspect_source',reviewer_confidence=1.0,task_id=tid).model_dump(mode='json'))
            for issue in result.issues:
                data=issue.model_dump(mode='json');data.update(issue_id='issue_'+json_hash({'generation':state['generation_id'],'issue':data})[:24],
                    auto_recheck_attempted=False,status='open',task_id=tid)
                state['issues'].append(data)
            if item['workflow']=='review_chapter_coverage':
                state['coverage'].append({'chapter_id':item['chapter_id'],'batch':item['batch'],'block_ids':result.reviewed_block_ids,
                    'complete':not context.payload['omitted_source_blocks'] and context.payload['lower_objects_reviewed']==context.payload['lower_objects_available'],
                    'task_id':tid,'review_summary':result.review_summary})
        state['tasks'].append(tid);state['pending']=None;state['cursor']+=1;self.save(directory,state)
        return None
    def publish(self,directory,document,state):
        self.verify(directory,document,state);root=self.root(directory,state['generation_id']);source=SourceIndex(document.blocks_path)
        objects=[read_object(root,state['entries'][key]) for key in state['order']]
        assessments=[self.assessment(root,state,key) for key in state['order']]
        if any(a is None for a in assessments):raise ProtocolError('VERIFICATION_INCOMPLETE','Missing independent object assessment')
        citations=list(state['citations'].values())
        for c in citations:resolve_citation(document,c,source)
        issues=list(state['issues'])
        for obj,a in zip(objects,assessments):issues.extend(assessment_issues(obj,a))
        for ch in source.chapters:
            batches=[b for b in state['coverage'] if b['chapter_id']==ch]
            if not batches or not all(b['complete'] for b in batches):
                issues.append(ReviewIssue(issue_id='issue_'+json_hash({'ch':ch,'kind':'coverage'})[:24],issue_type='LOW_CHAPTER_COVERAGE',severity='major',
                    chapter_id=ch,summary='Coverage review omitted lower objects or source due to the bounded budget.',suggested_action='inspect_source',reviewer_confidence=1.0).model_dump(mode='json'))
        coverage=[{'chapter_id':ch,'complete':all(b['complete'] for b in state['coverage'] if b['chapter_id']==ch) and bool([b for b in state['coverage'] if b['chapter_id']==ch])} for ch in source.chapters]
        provenance={'verification_generation_id':state['generation_id'],'dependencies':state['dependencies'],'tasks':state['tasks'],
            'normalized_blocks_path':str(document.blocks_path.relative_to(directory.resolve())),
            'evidence_paths':{key:{'object_ref':e['ref'],'lower_object_ids':e['lower'],'original_source_block_ids':e['source_blocks']} for key,e in state['entries'].items()}}
        report,issues=build_report(objects,assessments,[c|{'resolved':True} for c in citations],coverage,issues,document.quality,list(source.chapters),state['rules'],provenance)
        parent=safe_child(directory,'verification/generations');parent.mkdir(parents=True,exist_ok=True);stage=parent/f'.stage-{uuid4()}';stage.mkdir()
        try:
            write_bytes(stage/'citations.jsonl',''.join(Citation.model_validate(c).model_dump_json()+'\n' for c in citations).encode())
            for kind,name,jsonl in [('atomic_claim','claim_assessments',True),('knowledge_atom','atom_assessments',True),('core_idea','idea_assessments',False),('mental_model','model_assessments',False),('meta_principle','principle_assessments',False)]:
                rows=[a for a in assessments if a['object_ref']['object_type']==kind]
                if jsonl:write_bytes(stage/(name+'.jsonl'),''.join(json.dumps(a,ensure_ascii=False)+'\n' for a in rows).encode())
                else:write_json(stage/(name+'.json'),{'assessments':rows})
            write_json(stage/'coverage_review.json',{'batches':state['coverage'],'chapters':coverage})
            write_json(stage/'review_issues.json',{'issues':[ReviewIssue.model_validate(i).model_dump(mode='json') for i in issues]})
            write_json(stage/'quality_report.json',report);write_json(stage/'provenance.json',provenance)
            write_bytes(stage/'quality_report.md',render_report(report).encode())
            write_json(stage/'manifest.json',{'generation_id':state['generation_id'],'dependencies':state['dependencies'],'artifact_hashes':{p.name:hash_source(p)[0] for p in stage.iterdir()}})
            destination=parent/f"{state['generation_id']}-{uuid4()}";stage.rename(destination);publication=storage.publish(directory,destination)
            try:state['completed']=True;self.save(directory,state)
            except BaseException:publication.restore();raise
            return directory/'verification/current'
        except BaseException:
            if stage.exists():shutil.rmtree(stage)
            raise
    def prepare(self,selector,force=False):
        directory=None
        try:
            with self.library.files.locked():
                self.library._initialize();manifest,directory=self.ai._resolve(selector);document=load_canonical(directory,manifest,self.library.database)
                classification,deps,book,model=self.dependencies(directory,document);active=safe_child(directory,'runtime/verification-active.json')
                if active.exists() and not force:state=json.loads((self.root(directory,json.loads(active.read_text())['generation_id'])/'state.json').read_text())
                else:state=self.new(directory,document,classification,deps,book,model)
                self.verify(directory,document,state)
                safe_child(directory,'runtime/verification-failure.json').unlink(missing_ok=True)
                if state['completed']:
                    published=storage.current(directory)
                    if published is None or json.loads((published/'manifest.json').read_text())['generation_id']!=state['generation_id']:
                        raise ProtocolError('VERIFICATION_CORRUPT','Completed verification pointer is missing or inconsistent')
                    return directory/'verification/current'
                if state['pending']:
                    pending=self.consume(directory,document,state)
                    if pending:return pending
                safe_child(directory,'runtime/verification-failure.json').unlink(missing_ok=True)
                if state['cursor']==len(state['plan']):return self.publish(directory,document,state)
                return self.prepare_task(directory,selector,document,state,state['plan'][state['cursor']])
        except Exception as exc:
            if directory:
                write_json(safe_child(directory,'runtime/verification-failure.json'),{'status':'stale' if 'STALE_CONTEXT' in str(exc) else 'failed','diagnostic':str(exc)})
            if isinstance(exc,BookDistillerError):raise
            raise StorageError(f'Verification failed: {exc}') from exc
    def describe(self,directory,document):
        try:
            failure=safe_child(directory,'runtime/verification-failure.json')
            if failure.exists():
                data=json.loads(failure.read_text());return {'Verification':data['status'],'Quality gate':data['status'],'Verification diagnostic':data['diagnostic']}
            path=storage.current(directory)
            active=safe_child(directory,'runtime/verification-active.json')
            if active.exists():
                state=json.loads((self.root(directory,json.loads(active.read_text())['generation_id'])/'state.json').read_text())
                if not state['completed']:
                    self.verify(directory,document,state)
                    result={'Verification':'in_progress','Quality gate':'pending',
                        'Verification progress':f"{state['cursor']} / {len(state['plan'])} tasks completed"}
                    if state['pending']:result['Pending verification task']=state['pending']
                    if path:result['Previous quality gate']=json.loads((path/'quality_report.json').read_text())['status']
                    return result
            if path is None:return {'Verification':'unavailable'}
            report=json.loads((path/'quality_report.json').read_text());meta=json.loads((path/'manifest.json').read_text())
            _,deps,_,_=self.dependencies(directory,document)
            if meta['dependencies']!=deps:return {'Verification':'stale','Quality gate':'stale'}
            trace=report['metrics']['citation_traceability_rate']['value']
            return {'Verification':'completed' if report['status']=='pass' else report['status'],'Quality gate':report['status'],
                'Citation trace':f'{trace:.1%}' if trace is not None else 'N/A','Major unsupported':str(report['metrics']['unsupported_major_objects']['value']),
                'Hallucinations':str(report['metrics']['high_confidence_hallucinations']['value'])}
        except Exception as exc:return {'Verification':'stale' if 'STALE_CONTEXT' in str(exc) else 'failed','Verification diagnostic':str(exc)}
