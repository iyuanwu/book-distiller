"""Stable Citation derivation and bounded random access to Canonical source."""
import hashlib
import json
from book_distiller.core.models.normalized import NormalizedBlock
from book_distiller.core.models.verification import Citation
from book_distiller.pipeline.canonical import json_hash
from book_distiller.pipeline.result_validation import ProtocolError

class SourceIndex:
    """Only offsets and hierarchy stay in memory; full book text is never loaded."""
    def __init__(self,path):
        self.path=path;self.entries={};self.chapters={}
        with path.open('rb') as stream:
            while True:
                offset=stream.tell();line=stream.readline()
                if not line:break
                b=NormalizedBlock.model_validate_json(line)
                if b.block_id in self.entries:raise ProtocolError('BROKEN_CITATION','Duplicate Block ID')
                self.entries[b.block_id]={'offset':offset,'chapter_id':b.chapter_id,'section_id':b.section_id,'chars':len(b.text)}
                self.chapters.setdefault(b.chapter_id,[]).append(b.block_id)
    def get(self,block_id):
        if block_id not in self.entries:raise ProtocolError('BROKEN_CITATION',f'Missing Block {block_id}')
        with self.path.open('rb') as stream:
            stream.seek(self.entries[block_id]['offset']);return NormalizedBlock.model_validate_json(stream.readline())
    def window(self,ids,radius):
        selected=set(ids)
        for bid in ids:
            if bid not in self.entries:raise ProtocolError('BROKEN_CITATION',f'Missing Block {bid}')
            e=self.entries[bid];chapter=self.chapters[e['chapter_id']];i=chapter.index(bid)
            for candidate in chapter[max(0,i-radius):i+radius+1]:
                # A local expansion never crosses Chapter or a known Section boundary.
                if self.entries[candidate]['section_id']==e['section_id']:selected.add(candidate)
        return sorted(selected,key=lambda b:self.entries[b]['offset'])
    def batches(self,chapter,max_blocks,max_chars):
        batch=[];size=0
        for bid in self.chapters.get(chapter,[]):
            length=self.entries[bid]['chars']
            if length>max_chars:raise ProtocolError('VERIFICATION_CONTEXT_TOO_LARGE',f'Indivisible source Block {bid} exceeds budget')
            if batch and (len(batch)>=max_blocks or size+length>max_chars):yield batch;batch=[];size=0
            batch.append(bid);size+=length
        if batch:yield batch


def make_citation(document,block,context_ids=(),start=0,end=None):
    end=len(block.text) if end is None else end
    if not block.source_spans or not 0<=start<=end<=len(block.text):raise ProtocolError('BROKEN_CITATION','Missing SourceSpan or invalid range')
    relevant=[s for s in block.source_spans if s.char_start is None or (s.char_start<=start and s.char_end>=end)]
    if not relevant:raise ProtocolError('BROKEN_CITATION','Range not contained by a SourceSpan')
    identity={'edition':str(document.book.edition_id),'normalized_generation':str(document.fingerprint.parse_task_id),'block':block.block_id,'start':start,'end':end}
    first=relevant[0]
    return Citation(citation_id='cit_'+json_hash(identity)[:32],book_id=document.book.book_id,edition_id=document.book.edition_id,
        normalized_generation_id=document.fingerprint.parse_task_id,block_id=block.block_id,char_start=start,char_end=end,
        page_index=first.source_page_index,page_number=first.source_page_number,parser=first.parser,parser_locator=first.parser_locator,
        source_spans=relevant,context_block_ids=sorted(set(context_ids)-{block.block_id}),
        text_hash=hashlib.sha256(block.text[start:end].encode()).hexdigest())


def resolve_citation(document,citation,index=None):
    citation=Citation.model_validate(citation)
    if (citation.book_id,citation.edition_id,citation.normalized_generation_id)!=(document.book.book_id,document.book.edition_id,document.fingerprint.parse_task_id):
        raise ProtocolError('BROKEN_CITATION','Citation generation mismatch')
    index=index or SourceIndex(document.blocks_path)
    block=index.get(citation.block_id)
    for bid in citation.context_block_ids:
        context=index.get(bid)
        if (context.chapter_id,context.section_id)!=(block.chapter_id,block.section_id):
            raise ProtocolError('BROKEN_CITATION','Citation context crosses local source boundary')
    expected=make_citation(document,block,citation.context_block_ids,citation.char_start,citation.char_end)
    if expected!=citation:raise ProtocolError('BROKEN_CITATION','Citation identity, position or text hash changed')
    return {'text':block.text[citation.char_start:citation.char_end],'source_spans':[s.model_dump(mode='json') for s in expected.source_spans]}
