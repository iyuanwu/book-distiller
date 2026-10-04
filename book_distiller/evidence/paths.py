"""Read-only evidence card/path data; no renderer and no knowledge rewriting."""
import json,hashlib
from types import SimpleNamespace
from uuid import UUID
from book_distiller.storage import verification
from book_distiller.pipeline.ai_tasks import safe_child
from book_distiller.storage.filesystem import hash_source
from book_distiller.evidence.citations import SourceIndex,resolve_citation
from book_distiller.pipeline.book_references import resolve_reference
from book_distiller.core.models.verification import Citation
from book_distiller.pipeline.result_validation import ProtocolError


def evidence_path(directory,object_id):
    root=verification.current(directory)
    if root is None:raise ProtocolError('VERIFICATION_REQUIRED','No published evaluation')
    provenance=json.loads((root/'provenance.json').read_text());graph=provenance['evidence_paths']
    if object_id not in graph:raise ProtocolError('INVALID_OBJECT_REFERENCE','Object outside this verification generation')
    source=safe_child(directory,provenance['normalized_blocks_path'])
    # Fingerprint pins source bytes through the Book's normalized dependency manifest.
    book=safe_child(directory,provenance['dependencies']['book_path'])
    model=json.loads((book/'book_model.json').read_text())
    if hash_source(book/'book_model.json')[0]!=provenance['dependencies']['book_manifest_hash']:raise ProtocolError('BROKEN_CITATION','Pinned Book manifest changed')
    chapter=next(iter(model['dependencies']['chapters'].values()))
    chpath=safe_child(directory,chapter['path']);chmeta=json.loads((chpath/'chapter.json').read_text())
    if hash_source(chpath/'chapter.json')[0]!=chapter['manifest_hash'] or hash_source(source)[0]!=chmeta['normalized_document']['blocks_sha256']:
        raise ProtocolError('BROKEN_CITATION','Pinned source fingerprint changed')
    index=SourceIndex(source);citations={c['citation_id']:c for c in map(json.loads,(root/'citations.jsonl').read_text().splitlines())}
    document=SimpleNamespace(book=SimpleNamespace(book_id=UUID(model['book_id']),edition_id=UUID(model['edition_id'])),
        fingerprint=SimpleNamespace(parse_task_id=UUID(chmeta['normalized_document']['parse_task_id'])),blocks_path=source)
    assessments={}
    for name in ['claim_assessments.jsonl','atom_assessments.jsonl','idea_assessments.json','model_assessments.json','principle_assessments.json']:
        with (root/name).open() as stream:
            rows=(json.loads(line) for line in stream) if name.endswith('jsonl') else json.load(stream)['assessments']
            for a in rows:assessments[a['object_ref']['object_id']]=a
    visited={}
    def walk(key):
        if key in visited:return
        node=graph[key];a=assessments[key];cards=[]
        ref=node['object_ref']
        if a['object_ref']!=ref or a['verification_generation_id']!=provenance['verification_generation_id']:
            raise ProtocolError('BROKEN_CITATION','Assessment binding mismatch')
        if ref['object_type'] in {'atomic_claim','knowledge_atom'}:
            kind='claim' if ref['object_type']=='atomic_claim' else 'atom'
            obj=resolve_reference(directory,model,{'chapter_id':ref['chapter_id'],'chapter_generation_id':ref['chapter_generation_id'],kind+'_id':key})
        else:
            name,id_key={'core_idea':('core_ideas','core_idea_id'),'mental_model':('mental_models','model_id'),'meta_principle':('meta_principles','principle_id')}[ref['object_type']]
            path=book/(name+'.json')
            if hash_source(path)[0]!=model['artifact_hashes'][path.name]:raise ProtocolError('BROKEN_CITATION','Pinned knowledge changed')
            obj=next(row for row in json.loads(path.read_text())[name] if row[id_key]==key)
        for cid in a['citation_ids']:
            citation=Citation.model_validate(citations[cid]);block=index.get(citation.block_id)
            text=resolve_citation(document,citation,index)['text']
            cards.append({'citation':citation.model_dump(mode='json'),'source_excerpt':text,'chapter_id':block.chapter_id,
                'context':[{'block_id':bid,'text':index.get(bid).text} for bid in citation.context_block_ids]})
        visited[key]={'object_ref':node['object_ref'],'evidence_strength':a['evidence_strength'],'fidelity_verdict':a['fidelity_verdict'],
            'object':obj,'assessment':{k:a[k] for k in ('assessment_id','task_id','verification_generation_id','verification_summary','original_citation_ids','supplemental_citation_ids')},
            'lower_object_ids':node['lower_object_ids'],'citations':cards}
        for child in node['lower_object_ids']:walk(child)
    walk(object_id)
    return {'verification_generation_id':provenance['verification_generation_id'],'root_object_id':object_id,'nodes':visited}
