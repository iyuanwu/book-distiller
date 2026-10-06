"""Validated human writes under the same local lock as pipeline publication."""
from book_distiller.human.models import HumanAction, UserNote, HumanRule
from book_distiller.human.resolver import EffectiveKnowledgeResolver, KINDS
from book_distiller.pipeline.ai_tasks import safe_child
from book_distiller.pipeline.canonical import json_hash
from book_distiller.pipeline.files import write_json
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.storage.journal import append_event, read_events
from book_distiller.core.enums import BookType
from book_distiller.core.errors import ValidationError


class HumanService:
    def __init__(self, ai):self.ai=ai

    def anchors(self, directory, value):
        from book_distiller.normalize.book import read_blocks
        inventory = {obj['target_ref']['object_id']: obj['value']
                     for obj in EffectiveKnowledgeResolver(directory).inventory().values()}
        anchors, evidence, visited = {}, set(), set()
        def visit(item):
            evidence.update(e['block_id'] for e in item.get('evidence', []))
            ids = set().union(*(item.get(key, []) for key in
                ('claim_ids', 'atom_ids', 'core_idea_ids', 'model_ids', 'concept_ids')))
            for identity in sorted(ids - visited):
                visited.add(identity)
                if identity not in inventory:
                    raise ProtocolError('LOCK_DEPENDENCY_CONFLICT', 'Lower object missing: ' + identity)
                lower = inventory[identity]
                anchors['object:' + identity] = semantic_identity(lower)
                visit(lower)
        visit(value)
        if evidence:
            for block in read_blocks(directory / 'parsed/normalized/blocks.jsonl'):
                if block.block_id in evidence:
                    anchors['block:' + block.block_id] = json_hash(block.model_dump(mode='json'))
            if sum(k.startswith('block:') for k in anchors) != len(evidence):
                raise ProtocolError('LOCK_DEPENDENCY_CONFLICT', 'Evidence block missing')
        return anchors

    def apply(self, selector, action):
        try: action=HumanAction.model_validate(action)
        except ValueError as exc:raise ValidationError(str(exc)) from exc
        with self.ai.library.files.locked():
            self.ai.library._initialize();_,directory=self.ai._resolve(selector)
            resolver=EffectiveKnowledgeResolver(directory);current=resolver.show(action.target_ref)
            if current['base_object_hash']!=action.base_object_hash:
                raise ProtocolError('STALE_HUMAN_EDIT','Effective object changed; reopen before editing')
            if action.action!='edit' and action.patch:raise ValidationError('Only edit accepts a patch')
            if action.action=='edit':
                cls, fields=KINDS[action.target_ref.object_type]
                if not action.patch or not action.patch.keys()<=fields:
                    raise ValidationError('Patch fields forbidden; allowed: '+', '.join(sorted(fields)))
                try: cls.model_validate(current['value']|action.patch)
                except ValueError as exc:raise ValidationError(str(exc)) from exc
            event=action.model_dump(mode='json')
            if action.action=='lock':
                event['protected_value']=current['value'];event['anchors']=self.anchors(directory,current['value'])
            path=safe_child(directory,'human/overrides.jsonl')
            append_event(path,event)
            from book_distiller.pipeline.staleness import human_edit, reader_status
            if action.action=='edit':
                human_edit(directory,action.target_ref.model_dump(mode='json'))
            else:
                reader_status(directory,'display_stale','Human metadata changed; rerender to refresh badges')
            if action.action=='lock':
                from book_distiller.pipeline.run_events import related_event
                related_event(directory,'HUMAN_LOCK_APPLIED',target_ref=event['target_ref'],override_id=event['override_id'])
            self.rebuild(directory)
            return EffectiveKnowledgeResolver(directory).show(action.target_ref)

    def rebuild(self,directory):
        resolver=EffectiveKnowledgeResolver(directory)
        path=safe_child(directory,'human/state.json');path.parent.mkdir(parents=True,exist_ok=True)
        write_json(path,{'schema_version':'1.0','event_count':len(resolver.events),'objects':resolver.state})

    def note(self,selector,note):
        try:note=UserNote.model_validate(note)
        except ValueError as exc:raise ValidationError(str(exc)) from exc
        with self.ai.library.files.locked():
            self.ai.library._initialize();_,directory=self.ai._resolve(selector)
            if note.target_ref.object_type in KINDS:
                EffectiveKnowledgeResolver(directory).show(note.target_ref)
            else:
                validate_note_target(directory,note.target_ref)
            append_event(safe_child(directory,'human/notes.jsonl'),note.model_dump(mode='json'))
            from book_distiller.pipeline.staleness import reader_status
            reader_status(directory,'display_stale','User Note added; rerender to refresh notes')
            return note.model_dump(mode='json')

    def rule(self,selector,rule):
        try:rule=HumanRule.model_validate(rule)
        except ValueError as exc:raise ValidationError(str(exc)) from exc
        from book_distiller.pipeline.workflows import load_workflow
        for workflow in rule.applicable_workflows:load_workflow(self.ai.project,'classify' if workflow=='classify_book' else workflow)
        with self.ai.library.files.locked():
            self.ai.library._initialize();_,directory=self.ai._resolve(selector)
            if rule.scope=='book':path=safe_child(directory,'human/rules.jsonl')
            else:
                relative='data/user_rules/global.jsonl'
                if rule.scope=='type':
                    try:kind=BookType(rule.book_type).value
                    except ValueError as exc:raise ValidationError('Unknown book type') from exc
                    relative=f'data/user_rules/types/{kind}.jsonl'
                path=safe_child(self.ai.library.files.root,relative)
            append_event(path,rule.model_dump(mode='json'))
            return rule.model_dump(mode='json')


def semantic_identity(value):
    ignored={'generation_id','task_id','created_at','context_hash','classification_hash','claims_hash','normalized_document_hash','atom_refs','claim_refs'}
    return json_hash({k:v for k,v in value.items() if k not in ignored})


def validate_note_target(directory,target):
    from book_distiller.human.resolver import read
    if target.object_type in ('book','chapter'):
        book=read(directory/'parsed/normalized/book.json')
        receipt=read(directory/'parsed/completion.json')
        valid=target.object_id==book['book_id'] if target.object_type=='book' else target.object_id in {c['chapter_id'] for c in book['chapters']}
        if valid and str(target.generation_id)==receipt['task_id']:return
    elif target.object_type=='citation':
        from book_distiller.storage.verification import current
        path=current(directory)
        if path and read(path/'manifest.json')['generation_id']==str(target.generation_id):
            if any(v['citation_id']==target.object_id for v in read_events(path/'citations.jsonl')):return
    raise ProtocolError('STALE_HUMAN_EDIT','Note target is not current')


def guidance(home,directory,workflow,types):
    paths=[safe_child(home,'data/user_rules/global.jsonl')]
    paths += [safe_child(home,f'data/user_rules/types/{BookType(t).value}.jsonl') for t in sorted(set(types or []))]
    paths += [safe_child(directory,'human/rules.jsonl')]
    result=[]
    for path in paths:
        revisions={v['rule_id']:v for v in read_events(path)}
        rows=[HumanRule.model_validate(v) for v in revisions.values()]
        result += [v.model_dump(mode='json') for v in sorted(rows,key=lambda r:(r.priority,str(r.rule_id))) if v.enabled and workflow in v.applicable_workflows]
    value = {'precedence':'System invariants > Book > Type > Global; later scope wins. Never override schema, evidence integrity or locks.', 'rules':result}
    from book_distiller.bundle.receipt import external_rule_status
    dependencies = external_rule_status(directory,result,workflow)
    if dependencies:
        value['external_rule_dependencies'] = dependencies
        value['warnings'] = ['Imported snapshot has missing or changed external Human Rules; only locally available rules apply.'] if any(d['availability'] != 'available' for d in dependencies) else []
    return value
