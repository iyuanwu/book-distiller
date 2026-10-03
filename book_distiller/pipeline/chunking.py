"""Deterministic structural chunking, retaining IDs instead of chapter body text."""
import math
from collections.abc import Iterable, Iterator
from book_distiller.core.models.normalized import NormalizedBlock
from book_distiller.core.models.knowledge import AnalysisChunk, ChunkBudget, ChunkManifest
from book_distiller.core.errors import ValidationError


def units(blocks: Iterable[NormalizedBlock]) -> Iterator[list[NormalizedBlock]]:
    """Keep adjacent figure/caption pairs together when Chapter/Section agree."""
    pending = None
    for block in blocks:
        if pending is not None:
            if (pending.type, block.type) in {('figure','caption'),('caption','figure')} and (pending.chapter_id,pending.section_id)==(block.chapter_id,block.section_id):
                yield [pending,block]
                pending = None
                continue
            yield [pending]
        pending = block
    if pending is not None:
        yield [pending]


def build_chunks(blocks: Iterable[NormalizedBlock], chapter_id: str, document_hash: str,
                 budget: ChunkBudget | None = None) -> ChunkManifest:
    limits = budget or ChunkBudget()
    cap = min(limits.max_chars,limits.estimated_max_tokens*3)
    chunks: list[AnalysisChunk] = []
    primary: list[NormalizedBlock] = []
    overlap: list[NormalizedBlock] = []
    last_tail: list[NormalizedBlock] = []
    last_order = 0

    def emit():
        nonlocal primary, overlap, last_tail
        if not primary: return
        chars = sum(len(b.text) for b in overlap+primary)
        oversized = chars>cap or len(overlap+primary)>limits.max_blocks
        chunks.append(AnalysisChunk(chunk_id=f'chunk_{chapter_id}_{len(chunks)+1:04d}',chapter_id=chapter_id,
            section_ids=list(dict.fromkeys(b.section_id for b in primary if b.section_id)),
            primary_blocks=[b.block_id for b in primary], context_blocks=[b.block_id for b in overlap],
            first_order=primary[0].order,last_order=primary[-1].order,chars=chars,
            estimated_tokens=math.ceil(chars/3),oversized=oversized,
            warnings=['OVERSIZED_STRUCTURAL_UNIT'] if oversized else []))
        # Only ordinary prose may serve as overlap; never split a protected unit.
        last_tail=[]
        for b in reversed(primary):
            if b.type not in {'paragraph','list_item','quote'} or len(last_tail)>=limits.overlap_blocks: break
            last_tail.insert(0,b)
        primary,overlap=[],[]

    selected=(b for b in blocks if b.chapter_id==chapter_id)
    for unit in units(selected):
        if unit[0].order<=last_order:
            raise ValidationError('CHUNK_ORDER_INVALID: blocks must be strictly ordered')
        last_order=unit[-1].order
        if primary and (primary[-1].section_id != unit[0].section_id or
                        sum(len(b.text) for b in overlap+primary+unit)>cap or
                        len(overlap+primary+unit)>limits.max_blocks):
            emit()
        if not primary:
            overlap=[b for b in last_tail if b.section_id==unit[0].section_id]
            while overlap and (sum(len(b.text) for b in overlap+unit)>cap or len(overlap+unit)>limits.max_blocks):
                overlap.pop(0)
        primary.extend(unit)
        if sum(len(b.text) for b in primary)>cap or len(primary)>limits.max_blocks:
            emit()
    emit()
    if not chunks:
        raise ValidationError('CHAPTER_EMPTY: no blocks in this chapter')
    return ChunkManifest(chapter_id=chapter_id, normalized_document_hash=document_hash,budget=limits,chunks=chunks)
