"""Opt-in synthetic 5,000 Claim / 1,000 Atom snapshot benchmark (no model calls).
Run: .venv/bin/python tests/performance/bundle_roundtrip.py --home /tmp/bundle-benchmark
Fixture construction is outside measurements. Nothing is written to the formal Library.
"""
from pathlib import Path
import argparse
from copy import deepcopy
import json
import sys
import time
import tracemalloc
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'integration'))
from test_chapter_tasks import chapter_book
from test_book_tasks import ready
from test_verification_tasks import verification_book, complete
from book_distiller.bundle.service import BundleService, write
from book_distiller.pipeline.canonical import load_canonical, json_hash
from book_distiller.pipeline.book_tasks import BookTasks
from book_distiller.renderers.service import RenderService
from book_distiller.renderers.model import read
from book_distiller.storage.filesystem import hash_source
from book_distiller.evidence.citations import SourceIndex, make_citation
from book_distiller.evidence.graph import snapshot, read_object
from book_distiller.quality.review import build_report, render_report
from book_distiller.storage import verification

PROJECT=Path(__file__).resolve().parents[2]


def jsonl(path,rows):path.write_text(''.join(json.dumps(v)+'\n' for v in rows))


def build(home):
    home.mkdir(parents=True)
    fixture=chapter_book.__wrapped__(home,home)
    verifier,ingest=verification_book.__wrapped__(ready.__wrapped__(fixture))
    complete(verifier,ingest)
    root=ingest.library_path;ch=root/'knowledge/chapters/ch_0001';book=root/'knowledge/book';verified=verification.current(root)
    document=load_canonical(root,ingest.manifest,verifier.library.database)
    claims=read(ch/'claims.jsonl');atoms=read(ch/'atoms.json')['atoms']
    claim_seed,atom_seed=deepcopy(claims[0]),deepcopy(atoms[0])
    source=SourceIndex(document.blocks_path)
    block=source.get(claim_seed['evidence'][0]['block_id'])
    citations=[]
    claims=[dict(deepcopy(claim_seed),claim_id=claims[i]['claim_id'] if i<len(claims) else f'claim_benchmark_{i:05d}',statement=f'Synthetic benchmark claim {i}: validate before publication.') for i in range(5000)]
    for i in range(5000):
        start=i//100;end=start+1+i%100
        assert end<=len(block.text)
        citations.append(make_citation(document,block,start=start,end=end).model_dump(mode='json'))
    atoms=[dict(deepcopy(atom_seed),atom_id=atoms[i]['atom_id'] if i<len(atoms) else f'atom_benchmark_{i:05d}',title=f'Synthetic benchmark Atom {i}',summary=f'Synthetic group {i} preserves publication.',claim_ids=[c['claim_id'] for c in claims[i*5:i*5+5]]) for i in range(1000)]
    jsonl(ch/'claims.jsonl',claims)
    artifact=read(ch/'atoms.json');artifact['atoms']=atoms;write(ch/'atoms.json',artifact)
    cm=read(ch/'chapter.json');cm['metrics'].update(claims=5000,atoms=1000)
    cm['artifact_hashes']={n:hash_source(ch/n)[0] for n in cm['artifact_hashes']};write(ch/'chapter.json',cm)
    model=read(book/'book_model.json');model['dependencies']=BookTasks(verifier.ai).dependencies(root,document)[1];write(book/'book_model.json',model)
    deps=verifier.dependencies(root,document)[1]
    snapshots=home/'synthetic-objects';snapshots.mkdir()
    entries,order=snapshot(root,book.resolve(),model,snapshots,source)
    seeds={}
    for name in ['claim_assessments.jsonl','atom_assessments.jsonl','idea_assessments.json']:
        rows=read(verified/name)
        for row in rows if isinstance(rows,list) else rows['assessments']:seeds[row['object_ref']['object_type']]=row
    refs={c['claim_id']:[citations[i]['citation_id']] for i,c in enumerate(claims)}
    assessments=[]
    for key in order:
        entry=entries[key];kind=entry['ref']['object_type']
        if kind!='atomic_claim':refs[key]=sorted({cid for child in entry['lower'] for cid in refs[child]})[:64]
        row=dict(deepcopy(seeds[kind]),object_ref=entry['ref'],assessment_id='assessment_benchmark_'+key,citation_ids=refs[key],original_citation_ids=refs[key],
                 dependency_hash=json_hash(deps),lower_objects_reviewed=len(entry['lower']),lower_objects_available=len(entry['lower']),verification_summary='Synthetic benchmark assessment; not a real model judgment.')
        assessments.append(row)
    provenance=read(verified/'provenance.json');provenance['dependencies']=deps
    provenance['evidence_paths']={k:{'object_ref':e['ref'],'lower_object_ids':e['lower'],'original_source_block_ids':e['source_blocks']} for k,e in entries.items()}
    coverage=read(verified/'coverage_review.json')['chapters']
    report,issues=build_report([read_object(snapshots,entries[k]) for k in order],assessments,[c|{'resolved':True} for c in citations],coverage,[],document.quality,list(source.chapters),verifier.rules(),provenance)
    for kind,name in [('atomic_claim','claim_assessments.jsonl'),('knowledge_atom','atom_assessments.jsonl'),('core_idea','idea_assessments.json')]:
        rows=[a for a in assessments if a['object_ref']['object_type']==kind]
        if name.endswith('jsonl'):jsonl(verified/name,rows)
        else:write(verified/name,{'assessments':rows})
    jsonl(verified/'citations.jsonl',citations)
    write(verified/'provenance.json',provenance);write(verified/'quality_report.json',report)
    write(verified/'review_issues.json',{'issues':issues});(verified/'quality_report.md').write_text(render_report(report))
    vm=read(verified/'manifest.json');vm['dependencies']=deps;vm['artifact_hashes']={n:hash_source(verified/n)[0] for n in vm['artifact_hashes']};write(verified/'manifest.json',vm)
    RenderService(home,PROJECT).render(ingest.manifest.book.slug)
    return ingest


def measured(call):
    tracemalloc.start();start=time.perf_counter()
    result=call()
    elapsed=time.perf_counter()-start;_,peak=tracemalloc.get_traced_memory();tracemalloc.stop()
    return result,{'seconds':round(elapsed,4),'peak_python_bytes':peak}


def main():
    parser=argparse.ArgumentParser();parser.add_argument('--home',type=Path,required=True);args=parser.parse_args()
    home=args.home.resolve();ingest=build(home/'a')
    service=BundleService(home/'a',PROJECT);archive=home/'large.bookbundle.zip'
    bundle,export=measured(lambda:service.export(ingest.manifest.book.slug,archive))
    _,validate=measured(lambda:service.inspect(archive))
    _,imported=measured(lambda:BundleService(home/'b',PROJECT).import_bundle(archive))
    result={'fixture':'synthetic protocol data, no model','claims':5000,'atoms':1000,'citations':5000,'file_count':len(bundle.files),
            'uncompressed_bytes':sum(f.size for f in bundle.files),'zip_bytes':archive.stat().st_size,
            'largest_file':max((f.model_dump(mode='json') for f in bundle.files),key=lambda f:f['size']),
            'export_with_self_validation':export,'independent_validation':validate,'import_with_validation':imported}
    result['compression_ratio']=result['uncompressed_bytes']/result['zip_bytes']
    write(home/'performance.json',result);print(json.dumps(result,indent=2))

if __name__=='__main__':main()
