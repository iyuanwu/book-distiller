"""Structural, identity and provenance validation; not Citation Verify."""
from book_distiller.core.models.knowledge import ClaimResult, AtomResult
from book_distiller.pipeline.result_validation import ProtocolError


def validate_knowledge(result, context):
    generation=context.generation
    for field,expected in {'generation_id':generation.generation_id,'chapter_id':generation.chapter_id,
        'classification_hash':generation.classification_hash,'chunker_version':'analysis-chunker-v1'}.items():
        if getattr(result,field)!=expected:
            raise ProtocolError('STALE_CONTEXT',f'Knowledge {field} differs from Context')
    if isinstance(result,ClaimResult):
        if result.chunk_id!=context.chunk.chunk_id:
            raise ProtocolError('RESULT_TASK_MISMATCH','Wrong chunk_id')
        available={b.block_id for b in context.blocks if b.chapter_id==generation.chapter_id}
        primary=set(context.chunk.primary_blocks)
        for claim in result.claims:
            ids={e.block_id for e in claim.evidence}
            if not ids<=available or not ids&primary:
                raise ProtocolError('RESULT_EVIDENCE_INVALID','Every Claim must use this Chunk and at least one primary Block')
    else:
        if result.claims_hash!=context.claims_hash:
            raise ProtocolError('STALE_CONTEXT','Chapter Claims changed')
        available={c.claim_id for c in context.claims if c.chapter_id==generation.chapter_id}
        for atom in result.atoms:
            if not set(atom.claim_ids)<=available:
                raise ProtocolError('INVALID_CLAIM_REFERENCE','Atom references absent or foreign Chapter Claims')
