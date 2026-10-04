"""Closed-world evaluation validation; the model alone makes semantic judgments."""
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.pipeline.canonical import canonical_json


def validate_verification(result,context):
    scope=context.scope;payload=context.payload
    for key,val in {'verification_generation_id':scope['generation_id'],'dependency_hash':scope['dependency_hash'],
        'input_hash':context.input_hash,'classification_hash':scope['classification_hash']}.items():
        if str(getattr(result,key))!=str(val):raise ProtocolError('STALE_CONTEXT',f'Mismatched {key}')
    citations={c['citation_id'] for c in payload['citations']}
    refs={canonical_json(v['ref']) for v in payload.get('objects',[])}
    refs|={canonical_json(v['object_ref']) for v in payload.get('lower_assessments',[])}
    blocks={c['block_id'] for c in payload['citations']}
    if hasattr(result,'assessment'):
        a=result.assessment
        if a.object_ref.model_dump(mode='json')!=payload['target']['ref']:raise ProtocolError('INVALID_OBJECT_REFERENCE','Assessment target mismatch')
        if not set(a.citation_ids)<=citations:raise ProtocolError('BROKEN_CITATION','Cite only supplied source ranges')
        original=set(payload['original_citation_ids'])
        if set(a.supplemental_citation_ids)!=set(a.citation_ids)-original:raise ProtocolError('INVALID_SUPPLEMENTAL_CITATION','Supplemental references must identify newly selected evidence')
        if context.task_type!='repair_evidence' and a.supplemental_citation_ids:raise ProtocolError('INVALID_SUPPLEMENTAL_CITATION','New Claim evidence requires the explicit local recheck')
        if (payload['lower_objects_reviewed']<payload['lower_objects_available'] or payload['omitted_source_blocks']) and a.evidence_strength=='strong':raise ProtocolError('INCOMPLETE_EVIDENCE','Incomplete lower-level review cannot be strong')
        if (a.object_ref.object_type=='meta_principle' and a.evidence_strength=='strong' and
            len({s['chapter_id'] for s in payload['source_excerpts'] if s['citation_id'] in a.citation_ids})<2):
            raise ProtocolError('INSUFFICIENT_PRINCIPLE_SUPPORT','Strong Principle requires actual cross-Chapter citations')
    else:
        if context.task_type=='review_chapter_coverage':
            if set(result.reviewed_block_ids)!=set(payload['reviewed_block_ids']) or len(result.reviewed_block_ids)!=len(set(result.reviewed_block_ids)):
                raise ProtocolError('COVERAGE_INCOMPLETE','Review every source Block in this batch exactly once')
        for issue in result.issues:
            if any(canonical_json(r.model_dump(mode='json')) not in refs for r in issue.object_refs):raise ProtocolError('INVALID_OBJECT_REFERENCE','Issue references unseen object')
            if not set(issue.source_refs)<=citations:raise ProtocolError('BROKEN_CITATION','Issue references unseen citation')
            if issue.chapter_id and issue.chapter_id not in payload['chapter_ids']:raise ProtocolError('INVALID_CHAPTER_REFERENCE','Issue chapter outside scope')
            if issue.issue_type=='MAJOR_OMISSION' and (not issue.source_refs or not issue.chapter_id):raise ProtocolError('OMISSION_SOURCE_REQUIRED','Omission needs source and Chapter')
            if issue.issue_type=='MAJOR_OMISSION':
                source_chapters={s['citation_id']:s['chapter_id'] for s in payload['source_excerpts']}
                if any(source_chapters.get(cid)!=issue.chapter_id for cid in issue.source_refs):
                    raise ProtocolError('INVALID_CHAPTER_REFERENCE','Omission source must belong to its Chapter')
            if issue.issue_type in {'OVERCLAIM','CONCEPT_DISTORTION','DUPLICATE_KNOWLEDGE','SYNTHESIS_OVERREACH'} and not issue.object_refs:
                raise ProtocolError('INVALID_OBJECT_REFERENCE','Knowledge issue requires affected object references')
