"""One effective knowledge projection. Historical artifacts are never patched."""
import json
from book_distiller.pipeline.canonical import json_hash
from book_distiller.pipeline.ai_tasks import safe_child
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.storage.journal import read_events
from book_distiller.core.models.ai_tasks import BookClassification
from book_distiller.core.models.knowledge import AtomicClaim, KnowledgeAtom
from book_distiller.core.models.synthesis import PublishedConcept, PublishedCoreIdea, PublishedMentalModel, MetaPrinciple

KINDS = {
    'classification': (BookClassification, {'primary_type','secondary_types','tags','rationale_summary'}),
    'atomic_claim': (AtomicClaim, {'statement','claim_type','concept_terms','importance'}),
    'knowledge_atom': (KnowledgeAtom, {'title','summary','atom_type','concept_terms','reasoning','examples','importance'}),
    'concept': (PublishedConcept, {'canonical_name','zh_name','aliases'}),
    'core_idea': (PublishedCoreIdea, {'title','statement','importance','promotion_reason'}),
    'mental_model': (PublishedMentalModel, {'name','zh_name','description','mechanism','when_to_use','limitations','promotion_reason'}),
    'meta_principle': (MetaPrinciple, {'title','statement','promotion_reason'}),
}


def read(path):
    return json.loads(path.read_text())


def ref(kind, gid, identity, chapter=None):
    return {'object_type':kind,'generation_id':str(gid),'object_id':identity,'chapter_id':chapter}


class EffectiveKnowledgeResolver:
    def __init__(self, directory):
        self.directory = directory
        self.events = read_events(safe_child(directory,'human/overrides.jsonl'))
        self.state = {}
        for event in self.events:
            key = json_hash(event['target_ref'])
            if event['action'] == 'rebase':
                old = self.state.get(json_hash(event['old_ref']))
                if old:
                    self.state[key] = dict(__import__('copy').deepcopy(old), target_ref=event['target_ref'], rebased_from=event['old_ref'])
                continue
            entry = self.state.setdefault(key, {'target_ref':event['target_ref'],'patch':{},'human_verified':False,'locked':False,'override_ids':[]})
            entry['override_ids'].append(event['override_id'])
            if event['action']=='edit':
                entry['patch'].update(event['patch'])
                entry['human_verified']=False
                if entry['locked']:
                    entry['protected_value']=entry['protected_value'] | event['patch']
            elif event['action']=='verify': entry['human_verified']=True
            elif event['action']=='lock':
                entry['locked']=True
                entry['anchors']=event.get('anchors',{})
                entry['protected_value']=event['protected_value']
            elif event['action']=='unlock': entry['locked']=False

    def effective(self, target, value, *, historical=False):
        state = {} if historical else self.state.get(json_hash(target),{})
        patched = value | state.get('patch',{})
        if target['object_type'] in KINDS:
            patched = KINDS[target['object_type']][0].model_validate(patched).model_dump(mode='json')
        return {'target_ref':target,'base':value,'value':patched,'base_object_hash':json_hash(patched),
                'human_modified':bool(state.get('patch')),'human_verified':state.get('human_verified',False),
                'locked':state.get('locked',False),'rebased_from':state.get('rebased_from')}

    def inventory(self):
        from book_distiller.storage import knowledge, book_knowledge
        out = {}
        def add(target, value): out[json_hash(target)] = self.effective(target,value)
        p=safe_child(self.directory,'analysis/classification.json')
        if p.exists():
            v=read(p);add(ref('classification',v['task_id'],'classification'),v)
        folder=self.directory/'knowledge/chapters'
        if folder.exists():
            for pointer in sorted(folder.iterdir()):
                path=knowledge.current(self.directory,pointer.name)
                if path is None: continue
                info=read(path/'chapter.json');gid=info['generation']['generation_id']
                for line in (path/'claims.jsonl').read_text().splitlines():
                    v=json.loads(line);add(ref('atomic_claim',gid,v['claim_id'],pointer.name),v)
                for v in read(path/'atoms.json')['atoms']:
                    add(ref('knowledge_atom',gid,v['atom_id'],pointer.name),v)
        book=book_knowledge.current(self.directory)
        if book:
            gid=read(book/'book_model.json')['generation_id']
            for kind,name,key in [('concept','concepts','concept_id'),('core_idea','core_ideas','core_idea_id'),('mental_model','mental_models','model_id'),('meta_principle','meta_principles','principle_id')]:
                for v in read(book/(name+'.json'))[name]:add(ref(kind,gid,v[key]),v)
        return out

    def show(self, target):
        target = target.model_dump(mode='json') if hasattr(target,'model_dump') else target
        value=self.inventory().get(json_hash(target))
        if value is None:raise ProtocolError('STALE_HUMAN_EDIT','Target is not in the current generation; reopen the object')
        return value

    def semantic_hash(self, kinds=None, chapter=None):
        patches = {}
        # Durable semantic revision: a completed rerun has consumed this revision.
        # Rebase copies do not change it; metadata never enters it.
        for s in self.state.values():
            target=s['target_ref']
            if s['patch'] and (kinds is None or target['object_type'] in kinds) and (chapter is None or target['chapter_id']==chapter):
                patches[(target['object_type'],target['chapter_id'] or '',target['object_id'])]=s['patch']
        return json_hash([(list(k),v) for k,v in sorted(patches.items())])

    def display_hash(self):
        return json_hash({'state':self.state,'notes':self.notes()})

    def notes(self):
        notes=read_events(safe_child(self.directory,'human/notes.jsonl'))
        for note in notes:
            original=note['target_ref']
            for event in self.events:
                if event['action']=='rebase' and event['old_ref']==note['target_ref']:
                    note['target_ref']=event['target_ref']
            if original!=note['target_ref']:note['original_target_ref']=original
        return notes

    def protected(self):
        return [v for v in self.inventory().values() if v['locked']]
