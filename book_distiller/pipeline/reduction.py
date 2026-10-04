"""Bounded structural batching and lossless reference expansion; no semantic AI."""
from collections.abc import Iterable, Iterator
from book_distiller.pipeline.canonical import canonical_json
from book_distiller.pipeline.result_validation import ProtocolError

BATCH_CHARS = 14000
BATCH_ITEMS = 24


def batches(items: Iterable[dict], max_chars=BATCH_CHARS,max_items=BATCH_ITEMS) -> Iterator[list[dict]]:
    batch=[];size=2
    for item in items:
        length=len(canonical_json(item))+1
        if length+2>max_chars:
            raise ProtocolError('SYNTHESIS_CONTEXT_TOO_LARGE','A single item exceeds the bounded batch; preserve it for review')
        if batch and (size+length>max_chars or len(batch)>=max_items):
            yield batch;batch=[];size=2
        batch.append(item);size+=length
    if batch: yield batch


def reduced_items(groups, inputs, lineage, prefix):
    available={item['id']:item for item in inputs}
    refs=[r for group in groups for r in group.source_ids]
    if set(refs)!=set(available) or len(refs)!=len(set(refs)):
        raise ProtocolError('REDUCE_REFERENCE_INVALID','Groups must partition every supplied item exactly once')
    if len(groups)>max(1,len(inputs)//2):
        raise ProtocolError('REDUCE_NOT_PROGRESSING','Reduce must at least halve item count while retaining all references')
    output=[]
    for i,group in enumerate(groups,1):
        item_id=f'{prefix}_{i:03d}'
        roots=sorted({r for source in group.source_ids for r in lineage.get(source,[source])})
        lineage[item_id]=roots
        output.append({'id':item_id,'title':group.title,'summary':group.summary,'caveats':group.caveats,
            'concept_terms':sorted({term for source in group.source_ids for term in available[source].get('concept_terms',[])}),
            'chapters':sorted({ch for source in group.source_ids for ch in available[source].get('chapters',[])})})
    return output


def expand(ids,available,lineage):
    if len(ids)!=len(set(ids)) or not set(ids)<=set(available):
        raise ProtocolError('INVALID_SOURCE_REFERENCE','References must be unique IDs actually supplied to this Task')
    return sorted({root for value in ids for root in lineage.get(value,[value])})
