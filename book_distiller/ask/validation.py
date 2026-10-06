"""Closed-world reference and evidence-strength validation for BookAnswer."""
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.ask.retrieval import quality_rank


def validate_answer(answer, context):
    scope, payload = context.scope, context.payload
    if answer.answer_id != answer.task_id or answer.question != scope['question']:
        raise ProtocolError('RESULT_TASK_MISMATCH', 'Answer identity/question differs from its Ask task')
    for field in ('book_generation_id','verification_generation_id','human_state_hash','question_hash','classification_hash','applicable_rule_hash'):
        if str(getattr(answer,field)) != str(scope[field]):
            raise ProtocolError('STALE_CONTEXT', f'Ask binding changed: {field}')
    if answer.answer_type != payload['intent']:
        raise ProtocolError('ANSWER_INTENT_MISMATCH', 'Answer type must match the prepared question intent')
    if answer.out_of_scope and answer.citation_refs:
        raise ProtocolError('OUT_OF_SCOPE_CITATION', 'Out-of-book answers cannot invent book support')
    objects = {o['ref']['object_id']:o for o in payload['objects']}
    citations = {c['citation_id'] for c in payload['citations']}
    notes = {n['note_id'] for n in payload['user_notes']}
    assessments = {a['object_ref']['object_id']:a for a in payload['assessments']}
    if not set(answer.knowledge_refs) <= objects.keys():
        raise ProtocolError('INVALID_OBJECT_REFERENCE', 'Answer references unselected effective knowledge')
    if not set(answer.citation_refs) <= citations:
        raise ProtocolError('BROKEN_CITATION', 'Answer cites evidence outside this Context')
    if not {str(n) for n in answer.human_note_refs} <= notes:
        raise ProtocolError('INVALID_NOTE_REFERENCE', 'Answer references unseen User Notes')
    if not set(payload['quality_warnings']) <= set(answer.quality_warnings):
        raise ProtocolError('QUALITY_WARNING_REQUIRED', 'Retain the book and selected issue warnings')
    if not objects and not answer.insufficient_evidence and not answer.human_note_refs:
        raise ProtocolError('INSUFFICIENT_EVIDENCE', 'No selected knowledge supports a positive answer')
    def closure(keys):
        found = set()
        def walk(key):
            if key in found: return
            found.add(key)
            for child in objects[key]['lower_ids']: walk(child)
        for key in keys: walk(key)
        return found
    used = closure(answer.knowledge_refs)
    for segment in answer.segments:
        allowed = {cid for key in closure(segment.knowledge_refs) for cid in assessments.get(key,{}).get('citation_ids',[])}
        if not set(segment.citation_refs) <= allowed:
            raise ProtocolError('BROKEN_CITATION', 'Segment citation has no path from its Knowledge refs')
        if segment.layer=='ai' and not answer.insufficient_evidence and not segment.citation_refs:
            raise ProtocolError('INSUFFICIENT_EVIDENCE','AI synthesis/application needs book evidence; only refusals may be uncited')
        if segment.layer=='source' and any(quality_rank(assessments.get(k))==5 for k in closure(segment.knowledge_refs)):
            raise ProtocolError('INSUFFICIENT_EVIDENCE','Unsupported objects cannot become Source-supported statements')
    if answer.confidence=='high':
        evaluated = [assessments[k] for k in used if k in assessments]
        if not evaluated or any(quality_rank(a)>0 for a in evaluated) or payload['limitations'] or any(
            i['severity'] in ('critical','major') for i in payload['quality_issues']):
            raise ProtocolError('CONFIDENCE_EXCEEDS_EVIDENCE','High requires complete strong support without relevant major issues')
    if answer.answer_type=='application' and any(s.layer=='source' for s in answer.segments):
        raise ProtocolError('APPLICATION_LAYER','Application must be explicitly AI, not an author statement')
