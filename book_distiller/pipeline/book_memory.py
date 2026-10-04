"""Deterministic bounded derived memory, rebuilt from each successful generation."""
from book_distiller.pipeline.canonical import canonical_json,json_hash
from book_distiller.pipeline.result_validation import ProtocolError


def build_memory(identity,classification,outline,concepts,ideas,models,principles,decisions,max_chars=12000,source_refs=None):
    memory={'schema_version':'1.0','builder_version':'book-memory-v1.0','identity':identity,
        'classification':classification,'outline':outline,'meta_principles':[], 'mental_models':[],
        'core_ideas':[],'concepts':[],'unresolved_conflicts':[],
        'max_chars':max_chars,'selected_chars':0,'omitted':{},'book_memory_hash':'0'*64}
    def finalize():
        for _ in range(10):
            n=len(canonical_json(memory))
            if n==memory['selected_chars']: break
            memory['selected_chars']=n
        memory['book_memory_hash']=json_hash({k:v for k,v in memory.items() if k!='book_memory_hash'})
        return memory['selected_chars']<=max_chars
    groups=[('meta_principles',principles,'principle_id','statement'),('mental_models',models,'model_id','description'),
        ('core_ideas',ideas,'core_idea_id','statement'),('concepts',concepts,'concept_id','canonical_name')]
    memory['omitted']={key:len(rows) for key,rows,_,_ in groups}
    conflicts=[d for d in decisions if d['decision'] in {'potential_tension','contradicts'}]
    memory['omitted']['unresolved_conflicts']=len(conflicts)
    # Identity/generation bindings never silently disappear. Long outlines keep status IDs,
    # not chapter prose; fail explicitly if even the invariant metadata cannot fit.
    if not finalize(): raise ProtocolError('BOOK_MEMORY_BUDGET_TOO_SMALL','Identity and chapter status exceed memory budget')
    for key,rows,id_key,text_key in groups:
        for obj in sorted(rows,key=lambda x:(-x.get('importance',0),x[id_key])):
            entry={'id':obj[id_key],'text':obj[text_key][:360]}
            if obj.get('atom_ids'):
                if source_refs is not None: entry['source_atom_refs']=[source_refs[a] for a in obj['atom_ids'][:3]]
                else: entry['source_atom_ids']=obj['atom_ids'][:3]
            if obj.get('core_idea_ids'): entry['source_idea_ids']=obj['core_idea_ids'][:3]
            memory[key].append(entry);memory['omitted'][key]-=1
            if not finalize():
                memory[key].pop();memory['omitted'][key]+=1
                finalize()
    for conflict in sorted(conflicts,key=lambda d:(d['decision'],sorted(d['source_atom_ids']),d['reason'])):
        entry={'relation':conflict['decision'],'atom_ids':conflict['source_atom_ids'][:4],'reason':conflict['reason'][:200]}
        if source_refs is not None:
            entry['atom_refs']=[source_refs[a] for a in entry.pop('atom_ids')]
        memory['unresolved_conflicts'].append(entry);memory['omitted']['unresolved_conflicts']-=1
        if not finalize():
            memory['unresolved_conflicts'].pop();memory['omitted']['unresolved_conflicts']+=1;finalize()
    return memory
