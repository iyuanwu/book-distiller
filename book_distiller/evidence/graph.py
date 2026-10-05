"""Immutable generation-bound knowledge graph and file-backed object snapshots."""
import json
from book_distiller.core.models.verification import ObjectRef
from book_distiller.core.models.knowledge import AtomicClaim,KnowledgeAtom
from book_distiller.core.models.synthesis import PublishedCoreIdea,PublishedMentalModel,MetaPrinciple
from book_distiller.pipeline.canonical import json_hash
from book_distiller.pipeline.files import write_json
from book_distiller.pipeline.ai_tasks import safe_child
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.storage.filesystem import hash_source


def snapshot(directory,book_path,model,destination,source):
    entries={};order=[]
    from book_distiller.human.resolver import EffectiveKnowledgeResolver, ref as human_ref
    resolver=EffectiveKnowledgeResolver(directory)
    def check_binding(value,chapter,generation):
        if (str(value.book_id)!=model['book_id'] or str(value.edition_id)!=model['edition_id'] or
            str(value.generation_id)!=generation or value.chapter_id!=chapter or
            value.normalized_document_hash!=model['dependencies']['normalized_document_hash'] or
            value.classification_hash!=model['dependencies']['classification_hash']):
            raise ProtocolError('BROKEN_CITATION','Knowledge object dependency binding mismatch')
    def put(kind,identity,value,chapter=None,gid=None):
        ref=ObjectRef(object_type=kind,object_id=identity,book_id=model['book_id'],edition_id=model['edition_id'],chapter_id=chapter,
            chapter_generation_id=gid,book_generation_id=model['generation_id'] if chapter is None else None).model_dump(mode='json')
        if identity in entries:raise ProtocolError('BROKEN_CITATION','Duplicate knowledge identity')
        value=resolver.effective(human_ref(kind,gid if chapter else model['generation_id'],identity,chapter),value)['value']
        name='object-'+json_hash(ref)+'.json';data={'ref':ref,'value':value}
        write_json(destination/name,data)
        entries[identity]={'ref':ref,'file':name,'hash':hash_source(destination/name)[0],'lower':[],'source_blocks':[]}
        order.append(identity)
    for ch,dep in model['dependencies']['chapters'].items():
        path=safe_child(directory,dep['path'])
        if hash_source(path/'chapter.json')[0]!=dep['manifest_hash']:raise ProtocolError('BROKEN_CITATION','Chapter dependency hash mismatch')
        with (path/'claims.jsonl').open() as stream:
            for line in stream:
                c=AtomicClaim.model_validate_json(line)
                check_binding(c,ch,dep['generation_id'])
                put('atomic_claim',c.claim_id,c.model_dump(mode='json'),ch,dep['generation_id'])
                for ev in c.evidence:
                    if ev.block_id not in source.entries or source.entries[ev.block_id]['chapter_id']!=ch:raise ProtocolError('BROKEN_CITATION','Claim references missing/foreign Chapter Block')
                entries[c.claim_id]['source_blocks']=[e.block_id for e in c.evidence]
    for ch,dep in model['dependencies']['chapters'].items():
        path=safe_child(directory,dep['path'])
        for raw in json.loads((path/'atoms.json').read_text())['atoms']:
            a=KnowledgeAtom.model_validate(raw)
            check_binding(a,ch,dep['generation_id'])
            put('knowledge_atom',a.atom_id,a.model_dump(mode='json'),ch,dep['generation_id'])
            entries[a.atom_id]['lower']=a.claim_ids
    for kind,name,key,cls in [('core_idea','core_ideas.json','core_idea_id',PublishedCoreIdea),('mental_model','mental_models.json','model_id',PublishedMentalModel),('meta_principle','meta_principles.json','principle_id',MetaPrinciple)]:
        artifact=json.loads((book_path/name).read_text())
        if artifact['generation_id']!=model['generation_id']:raise ProtocolError('BROKEN_CITATION','Book artifact generation mismatch')
        for raw in artifact[name[:-5]]:
            value=cls.model_validate(raw).model_dump(mode='json');put(kind,value[key],value)
            lower=value.get('atom_ids',[])+value.get('core_idea_ids',[])+value.get('model_ids',[])
            if len(lower)!=len(set(lower)):raise ProtocolError('BROKEN_CITATION','Duplicate lower reference')
            entries[value[key]]['lower']=lower
            if 'atom_refs' in value:
                expected=[]
                for aid in value['atom_ids']:
                    if aid not in entries:raise ProtocolError('BROKEN_CITATION',f'Unknown Atom {aid}')
                    ref=entries[aid]['ref'];expected.append({'chapter_id':ref['chapter_id'],'chapter_generation_id':ref['chapter_generation_id'],'atom_id':aid})
                if value['atom_refs']!=expected:raise ProtocolError('BROKEN_CITATION','Atom refs must bind pinned generations')
    allowed={'knowledge_atom':{'atomic_claim'},'core_idea':{'knowledge_atom'},'mental_model':{'core_idea','knowledge_atom'},'meta_principle':{'core_idea','mental_model'}}
    for key in order:
        entry=entries[key];kind=entry['ref']['object_type']
        if kind=='atomic_claim':continue
        if not entry['lower']:raise ProtocolError('BROKEN_CITATION','Knowledge object lacks lower support')
        blocks=set()
        for lower in entry['lower']:
            child=entries.get(lower)
            if child is None or child['ref']['object_type'] not in allowed[kind]:raise ProtocolError('BROKEN_CITATION',f'Invalid lower object {lower}')
            if kind=='knowledge_atom' and child['ref']['chapter_id']!=entry['ref']['chapter_id']:raise ProtocolError('BROKEN_CITATION','Cross-Chapter Claim ref')
            blocks.update(child['source_blocks'])
        entry['source_blocks']=sorted(blocks)
    return entries,order


def read_object(root,entry):
    path=safe_child(root,entry['file'])
    if hash_source(path)[0]!=entry['hash']:raise ProtocolError('STALE_CONTEXT','Knowledge snapshot changed')
    obj=json.loads(path.read_text())
    return obj|{'source_blocks':entry['source_blocks']}
