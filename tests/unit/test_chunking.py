from book_distiller.core.models.normalized import NormalizedBlock
from book_distiller.core.models.knowledge import ChunkBudget
from book_distiller.pipeline.chunking import build_chunks


def block(i,kind='paragraph',chars=300,chapter='ch_0001',section='sec_1'):
    return NormalizedBlock(block_id=f'blk_{i:06d}',chapter_id=chapter,section_id=section,order=i,type=kind,text='x'*chars)


def test_chunk_scope_order_budget_stability_overlap():
    blocks=[block(i) for i in range(1,15)]+[block(15,chapter='ch_0002')]
    budget=ChunkBudget(max_chars=1000,estimated_max_tokens=334,max_blocks=4)
    first=build_chunks(iter(blocks),'ch_0001','0'*64,budget)
    assert first==build_chunks(iter(blocks),'ch_0001','0'*64,budget)
    ids=[bid for chunk in first.chunks for bid in chunk.primary_blocks]
    assert ids==[f'blk_{i:06d}' for i in range(1,15)]
    for c in first.chunks:
        assert c.chars<=1000 and c.estimated_tokens<=334
        assert not set(c.primary_blocks)&set(c.context_blocks)
        assert len(c.context_blocks)<=1
    assert first.chunks[1].context_blocks


def test_section_boundaries_and_protected_units():
    blocks=[block(1,section='s1'),block(2,section='s2'),block(3,'figure',500,section='s2'),
            block(4,'caption',400,section='s2'),block(5,'table',2000,section='s2'),
            block(6,'code',2000,section='s2'),block(7,'formula',2000,section='s2')]
    result=build_chunks(blocks,'ch_0001','0'*64,ChunkBudget(max_chars=1000,estimated_max_tokens=334))
    assert all(len(c.section_ids)==1 for c in result.chunks)
    pair=next(c for c in result.chunks if 'blk_000003' in c.primary_blocks)
    assert 'blk_000004' in pair.primary_blocks
    for i in (5,6,7):
        c=next(c for c in result.chunks if f'blk_{i:06d}' in c.primary_blocks)
        assert c.primary_blocks==[f'blk_{i:06d}'] and c.oversized
        assert c.chars==2000 and c.warnings


def test_zero_overlap_and_oversized_pair():
    result=build_chunks([block(1,'figure',800),block(2,'caption',800),block(3)],'ch_0001','0'*64,
        ChunkBudget(max_chars=1000,estimated_max_tokens=334,overlap_blocks=0))
    assert result.chunks[0].primary_blocks==['blk_000001','blk_000002']
    assert result.chunks[0].oversized
    assert not any(c.context_blocks for c in result.chunks)


def test_large_chapter_retains_bounded_body_memory():
    import tracemalloc
    tracemalloc.start()
    result=build_chunks((block(i,chars=1000) for i in range(1,10001)),'ch_0001','0'*64)
    _,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    assert peak<12*1024*1024
    assert sum(len(c.primary_blocks) for c in result.chunks)==10000
    assert all(c.chars<=10000 for c in result.chunks)
