"""Deterministic protection with exact dependency hashes, never semantic rebinding."""
from uuid import uuid4
from datetime import datetime, timezone
from book_distiller.human.resolver import EffectiveKnowledgeResolver, ref, KINDS
from book_distiller.human.service import HumanService
from book_distiller.pipeline.canonical import json_hash
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.pipeline.ai_tasks import safe_child
from book_distiller.storage.journal import append_event


def protected(ai,directory,kind,chapter=None):
    resolver=EffectiveKnowledgeResolver(directory);service=HumanService(ai);out=[]
    for obj in resolver.protected():
        target=obj['target_ref']
        if target['object_type']!=kind or chapter is not None and target['chapter_id']!=chapter:continue
        saved=resolver.state[json_hash(target)]
        if service.anchors(directory,obj['value'])!=saved.get('anchors',{}):
            raise ProtocolError('LOCK_DEPENDENCY_CONFLICT',str(target))
        out.append(obj)
    return out


def rebase(directory,old_ref,new_ref):
    if old_ref==new_ref:return
    path=safe_child(directory,'human/overrides.jsonl')
    resolver=EffectiveKnowledgeResolver(directory)
    if any(e['action']=='rebase' and e.get('old_ref')==old_ref and e['target_ref']==new_ref for e in resolver.events):return
    append_event(path,{'action':'rebase','event':'HUMAN_OVERRIDE_REBASED','override_id':str(uuid4()),
        'old_ref':old_ref,'target_ref':new_ref,'created_at':datetime.now(timezone.utc).isoformat()})
    from book_distiller.pipeline.run_events import related_event
    related_event(directory,'HUMAN_OVERRIDE_REBASED',old_ref=old_ref,target_ref=new_ref)


def protect_classification(ai,directory,result):
    for obj in protected(ai,directory,'classification'):
        fields=KINDS['classification'][1] | {'evidence','confidence'}
        result=type(result).model_validate(result.model_dump(mode='json')|{k:obj['value'][k] for k in fields})
        rebase(directory,obj['target_ref'],ref('classification',result.task_id,'classification'))
    return result


def merge_claims(ai,directory,generation,claims):
    from book_distiller.core.models.knowledge import AtomicClaim
    for obj in protected(ai,directory,'atomic_claim',generation.chapter_id):
        value=dict(obj['value'],generation_id=str(generation.generation_id),classification_hash=generation.classification_hash,
                   normalized_document_hash=generation.normalized_document_hash)
        claims=[c for c in claims if c.claim_id!=value['claim_id']]
        claims.append(AtomicClaim.model_validate(value))
    return sorted(claims,key=lambda c:c.claim_id)


def merge_atoms(ai,directory,generation,atoms,claims):
    from book_distiller.core.models.knowledge import KnowledgeAtom
    from book_distiller.human.service import semantic_identity
    ids={c.claim_id:semantic_identity(c.model_dump(mode='json')) for c in claims}
    resolver=EffectiveKnowledgeResolver(directory)
    for obj in protected(ai,directory,'knowledge_atom',generation.chapter_id):
        anchors=resolver.state[json_hash(obj['target_ref'])]['anchors']
        if any(ids.get(k.removeprefix('object:'))!=v for k,v in anchors.items() if k.startswith('object:')):
            raise ProtocolError('LOCK_DEPENDENCY_CONFLICT','Locked Atom dependencies changed in candidate Claims')
        value=dict(obj['value'],generation_id=str(generation.generation_id),classification_hash=generation.classification_hash,
                   normalized_document_hash=generation.normalized_document_hash)
        atoms=[a for a in atoms if a.atom_id!=value['atom_id']];atoms.append(KnowledgeAtom.model_validate(value))
    return atoms


def rebase_chapter(ai,directory,generation):
    # Called only after successful publication; recoverable by repeating this operation.
    resolver=EffectiveKnowledgeResolver(directory)
    for state in list(resolver.state.values()):
        target=state['target_ref']
        fork=(directory/f'runtime/generations/{generation.generation_id}/claims-fork.json').exists()
        if (state['locked'] or fork and target['object_type']=='atomic_claim') and target['chapter_id']==generation.chapter_id and target['object_type'] in ('atomic_claim','knowledge_atom'):
            rebase(directory,target,ref(target['object_type'],generation.generation_id,target['object_id'],generation.chapter_id))


def protect_book_state(ai,directory,state,only=None):
    for kind,key,idkey in [('concept','concepts','concept_id'),('core_idea','core_ideas','core_idea_id'),('mental_model','mental_models','model_id'),('meta_principle','meta_principles','principle_id')]:
        if only is not None and kind != only:
            continue
        for obj in protected(ai,directory,kind):
            value={k:v for k,v in obj['value'].items() if k not in ('atom_refs','claim_refs')}
            # A changed lower object is a conflict even if its ordinal ID survived.
            for refs,collection,identity in [('concept_ids','concepts','concept_id'),('core_idea_ids','core_ideas','core_idea_id'),('model_ids','mental_models','model_id')]:
                from book_distiller.human.service import semantic_identity
                old=EffectiveKnowledgeResolver(directory).inventory().values()
                previous={v['target_ref']['object_id']:semantic_identity(v['value']) for v in old}
                current={v[identity]:semantic_identity(v) for v in state[collection]}
                if any(previous.get(k)!=current.get(k) for k in value.get(refs,[])):
                    raise ProtocolError('LOCK_DEPENDENCY_CONFLICT','Locked high-level object dependency changed')
            state[key]=[v for v in state[key] if v[idkey]!=value[idkey]]+[value]
