"""Closed-world reference and promotion checks; no semantic fidelity claims."""
import unicodedata
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.pipeline.reduction import reduced_items


def norm(value):
    return unicodedata.normalize('NFC',' '.join(value.split())).casefold()


def require_refs(refs,available,label):
    if len(refs)!=len(set(refs)) or not set(refs)<=set(available):
        raise ProtocolError('INVALID_'+label+'_REFERENCE','References must be unique and supplied in this task')


def validate_synthesis(result,context):
    scope=context.scope;payload=context.payload
    for key,value in {'generation_id':scope['generation_id'],'dependency_hash':scope['dependency_hash'],
                      'classification_hash':scope['dependencies']['classification_hash'],'input_hash':context.input_hash}.items():
        if str(getattr(result,key))!=str(value): raise ProtocolError('STALE_CONTEXT',f'Mismatched {key}')
    items={i['id']:i for i in payload.get('items',[])}
    concepts={c['concept_id']:c for c in payload.get('concepts',[])}
    ideas={i['core_idea_id']:i for i in payload.get('core_ideas',[])}
    models={m['model_id']:m for m in payload.get('mental_models',[])}
    def chapters(atom_ids): return sorted({ch for a in atom_ids for ch in items[a].get('chapters',[])})
    if context.task_type=='synthesis_reduce':
        reduced_items(result.groups,list(items.values()),{},'validation')
    elif context.task_type=='normalize_concepts':
        seen={};aliases={}
        existing={c['concept_id']:c for c in payload.get('existing_concepts',[])}
        allowed_terms={norm(t) for i in items.values() for t in i.get('concept_terms',[])}
        allowed_terms|={norm(t) for c in existing.values() for t in c['source_terms']}
        for concept in result.concepts:
            if concept.concept_id in seen: raise ProtocolError('CONCEPT_ALIAS_CONFLICT','Duplicate concept ID')
            seen[concept.concept_id]=concept
            require_refs(concept.atom_ids,items,'ATOM')
            claims={cid for a in concept.atom_ids for cid in items[a].get('claim_ids',[])}
            require_refs(concept.claim_ids,claims,'CLAIM')
            if not concept.atom_ids: raise ProtocolError('INVALID_ATOM_REFERENCE','Concept needs a source Atom/group')
            if not {norm(t) for t in concept.source_terms}<=allowed_terms:
                raise ProtocolError('CONCEPT_SOURCE_TERM_INVALID','Source terms must occur in input candidates')
            for alias in [concept.canonical_name,concept.zh_name,*concept.aliases]:
                if not alias: continue
                key=norm(alias)
                if key in aliases and aliases[key]!=concept.concept_id:
                    raise ProtocolError('CONCEPT_ALIAS_CONFLICT','One alias names two different concepts')
                aliases[key]=concept.concept_id
                for old in existing.values():
                    old_aliases=[old['canonical_name'],old.get('zh_name'),*old['aliases']]
                    if any(v and norm(v)==key for v in old_aliases) and old['concept_id']!=concept.concept_id:
                        raise ProtocolError('CONCEPT_ALIAS_CONFLICT','Reuse the existing canonical ID instead of renaming it')
        if not set(existing)<=set(seen):
            raise ProtocolError('CONCEPT_REGISTRY_LOSS','Existing concepts must be retained; empty support requires explicit review')
        for old_id,old in existing.items():
            new=seen[old_id]
            if norm(new.canonical_name)!=norm(old['canonical_name']):
                raise ProtocolError('CONCEPT_ALIAS_CONFLICT','Preserve existing canonical names')
            old_aliases={norm(v) for v in [old['canonical_name'],old.get('zh_name'),*old['aliases']] if v}
            new_aliases={norm(v) for v in [new.canonical_name,new.zh_name,*new.aliases] if v}
            if not old_aliases<=new_aliases:
                raise ProtocolError('CONCEPT_REGISTRY_LOSS','Existing aliases must be retained')
        for c in result.concepts:
            require_refs([r.target_concept_id for r in c.relations],seen,'CONCEPT')
            if any(r.target_concept_id==c.concept_id for r in c.relations):
                raise ProtocolError('CONCEPT_ALIAS_CONFLICT','Self relations are not useful')
    elif context.task_type=='build_core_ideas':
        ids=[i.core_idea_id for i in result.core_ideas]
        require_refs(ids,set(ids),'IDEA')
        for idea in result.core_ideas:
            require_refs(idea.atom_ids,items,'ATOM');require_refs(idea.concept_ids,concepts,'CONCEPT')
            if sorted(set(idea.supporting_chapters))!=chapters(idea.atom_ids):
                raise ProtocolError('INVALID_CHAPTER_REFERENCE','Supporting chapters must be derived from source atoms')
        for decision in result.decisions:
            require_refs(decision.source_atom_ids,items,'ATOM')
            require_refs(decision.canonical_concept_ids,concepts,'CONCEPT')
    elif context.task_type=='build_mental_models':
        ids=[m.model_id for m in result.mental_models];require_refs(ids,set(ids),'MODEL')
        for model in result.mental_models:
            require_refs(model.core_idea_ids,ideas,'IDEA');require_refs(model.atom_ids,items,'ATOM')
            require_refs(model.concept_ids,concepts,'CONCEPT')
    elif context.task_type=='build_meta_principles':
        ids=[p.principle_id for p in result.meta_principles];require_refs(ids,set(ids),'PRINCIPLE')
        for p in result.meta_principles:
            require_refs(p.core_idea_ids,ideas,'IDEA');require_refs(p.model_ids,models,'MODEL');require_refs(p.concept_ids,concepts,'CONCEPT')
            actual={ch for i in p.core_idea_ids for ch in ideas[i]['supporting_chapters']}
            actual|={ch for m in p.model_ids for ch in models[m]['supporting_chapters']}
            if len(actual)<2 or sorted(set(p.supporting_chapters))!=sorted(actual):
                raise ProtocolError('PRINCIPLE_WITH_INSUFFICIENT_LOWER_OBJECTS','Principles need supported multi-chapter scope')
    elif context.task_type=='reduce_chapter_atoms':
        for atom in result.atoms: require_refs(atom.claim_ids,items,'CLAIM')
