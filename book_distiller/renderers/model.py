"""Read-only projection of pinned Canonical Knowledge and Verification artifacts."""
from dataclasses import dataclass
import json
import unicodedata
from book_distiller.pipeline.ai_tasks import safe_child
from book_distiller.pipeline.result_validation import ProtocolError
from book_distiller.renderers import progressive, evidence

SEMANTIC = {'title', 'name', 'zh_name', 'statement', 'summary', 'description', 'mechanism', 'when_to_use',
            'limitations', 'examples', 'reasoning', 'importance', 'confidence', 'claim_type', 'atom_type',
            'concept_terms', 'promotion_reason', 'supporting_chapters', 'concept_ids', 'source_type'}
PREFIX = {'atomic_claim': 'claim', 'knowledge_atom': 'atom', 'core_idea': 'idea',
          'mental_model': 'model', 'meta_principle': 'principle'}


def read(path):
    with path.open(encoding='utf-8') as stream:
        return [json.loads(line) for line in stream if line.strip()] if path.suffix == '.jsonl' else json.load(stream)


def require(condition, message):
    if not condition:
        raise ProtocolError('READER_INVALID_REFERENCE', message)


def reader_title(manifest, document):
    """Display metadata first; provisional slug placeholders defer to real titles."""
    slug = manifest.book.slug
    candidates = [manifest.edition.display_title, manifest.book.title, document.book.title]
    for value in candidates:
        if value and value.strip() and value.strip() != slug:
            return value.strip()
    return slug


def normalize(text):
    return unicodedata.normalize('NFKC', text).lower()


@dataclass
class ReaderViewModel:
    data: dict

    @classmethod
    def load(cls, directory, document, manifest, classification, book_path, model, verification, rules):
        provenance = read(verification/'provenance.json')
        graph = provenance['evidence_paths']
        objects = {}
        def add(row, kind, id_key):
            key = row[id_key]
            from book_distiller.human.resolver import EffectiveKnowledgeResolver, ref as human_ref
            h=EffectiveKnowledgeResolver(directory)
            human=h.effective(human_ref(kind,row.get('generation_id',model['generation_id']),key,row.get('chapter_id')),row)
            row=human['value']
            require(key not in objects, 'Duplicate object ID')
            require(key in graph and graph[key]['object_ref']['object_type'] == kind, 'Missing verification path')
            lower = row.get('claim_ids', []) if kind == 'knowledge_atom' else row.get('atom_ids', []) + row.get('core_idea_ids', []) + row.get('model_ids', [])
            require(lower == graph[key]['lower_object_ids'], 'Verification graph differs from canonical object links')
            ref = graph[key]['object_ref']
            require(ref['book_id'] == model['book_id'] and ref['edition_id'] == model['edition_id'], 'Object identity mismatch')
            if kind in ('atomic_claim', 'knowledge_atom'):
                require(ref['chapter_id'] == row['chapter_id'] and ref['chapter_generation_id'] == row['generation_id'], 'Chapter object generation mismatch')
            else:
                require(ref['book_generation_id'] == model['generation_id'], 'Book object generation mismatch')
            objects[key] = {k: v for k, v in row.items() if k in SEMANTIC} | {
                'id': key, 'type': kind, 'anchor': PREFIX[kind]+'-'+key,
                'chapter_id': row.get('chapter_id'), 'ref': graph[key]['object_ref'],
                'lower_ids': lower, 'human':{k:human[k] for k in ('human_modified','human_verified','locked')},
                'user_notes':[n for n in h.notes() if n['target_ref']==human['target_ref']]}
        for ch, dep in sorted(model['dependencies']['chapters'].items()):
            path = safe_child(directory, dep['path'])
            for row in read(path/'claims.jsonl'):
                add(row, 'atomic_claim', 'claim_id')
            for row in read(path/'atoms.json')['atoms']:
                add(row, 'knowledge_atom', 'atom_id')
        for name, kind, key in [('core_ideas', 'core_idea', 'core_idea_id'), ('mental_models', 'mental_model', 'model_id'),
                                ('meta_principles', 'meta_principle', 'principle_id')]:
            for row in read(book_path/(name+'.json'))[name]:
                add(row, kind, key)
        require(set(objects) == set(graph), 'Verification path inventory mismatch')
        assessments = {}
        for name in ['claim_assessments.jsonl', 'atom_assessments.jsonl', 'idea_assessments.json', 'model_assessments.json', 'principle_assessments.json']:
            rows = read(verification/name)
            for row in rows if isinstance(rows, list) else rows['assessments']:
                key = row['object_ref']['object_id']
                require(key in objects and key not in assessments, 'Unknown/duplicate assessment')
                require(row['object_ref'] == objects[key]['ref'] and row['verification_generation_id'] == provenance['verification_generation_id'], 'Stale assessment binding')
                assessments[key] = row
        citations = read(verification/'citations.jsonl')
        citation_ids = {c['citation_id'] for c in citations}
        require(len(citations) == len(citation_ids), 'Duplicate citation')
        require(set(assessments) == set(objects), 'Missing assessment')
        concepts = {c['concept_id']: c for c in read(book_path/'concepts.json')['concepts']}
        from book_distiller.human.resolver import EffectiveKnowledgeResolver,ref as human_ref
        human=EffectiveKnowledgeResolver(directory)
        concepts={k:human.effective(human_ref('concept',model['generation_id'],k),v)['value'] for k,v in concepts.items()}
        issues = read(verification/'review_issues.json')['issues']
        for issue in issues:
            require(set(issue['source_refs']) <= citation_ids, 'Issue references missing citation')
            require(all(r['object_id'] in objects for r in issue['object_refs']), 'Issue references missing object')
        for key, obj in objects.items():
            require(set(obj['lower_ids']) <= objects.keys(), 'Broken object link')
            require(set(obj.get('concept_ids', [])) <= concepts.keys(), 'Broken concept link')
            require(set(assessments[key]['citation_ids']) <= citation_ids, 'Missing citation')
        for concept in concepts.values():
            require(set(concept['atom_ids']+concept['claim_ids']) <= objects.keys(), 'Broken concept evidence')
            require(all(r['target_concept_id'] in concepts for r in concept['relations']), 'Broken concept relation')
        relationships = read(book_path/'relationships.json')['decisions']
        for relation in relationships:
            require(set(relation['source_atom_ids']) <= objects.keys(), 'Broken relationship')
        source = manifest.source
        from pathlib import Path
        from book_distiller.bundle.receipt import source_display
        quality = read(verification/'quality_report.json')
        coverage = read(verification/'coverage_review.json')
        chapters = {c.chapter_id: c.model_dump(mode='json') for c in document.book.chapters}
        for ch in chapters.values():
            ch['atom_ids'] = [k for k, o in objects.items() if o['chapter_id'] == ch['chapter_id'] and o['type'] == 'knowledge_atom']
            ch['claim_ids'] = [k for k, o in objects.items() if o['chapter_id'] == ch['chapter_id'] and o['type'] == 'atomic_claim']
        data = {'book': {'title': reader_title(manifest, document), 'book_id': model['book_id'], 'edition_id': model['edition_id'],
            'book_generation_id': model['generation_id'], 'verification_generation_id': provenance['verification_generation_id'],
            'classification': classification.model_dump(mode='json'), 'chapters': chapters,
            'source': source_display(directory, manifest),
            'derived': True}, 'knowledge': {'objects': objects, 'relationships': relationships},
            'concepts': concepts, 'quality': {'report': quality, 'issues': issues, 'assessments': assessments, 'coverage': coverage},
            'evidence': evidence.build(document, citations, rules)}
        from book_distiller.human.resolver import EffectiveKnowledgeResolver
        data['user_notes']=EffectiveKnowledgeResolver(directory).notes()
        return cls.finish(data, rules)

    @classmethod
    def finish(cls, data, rules):
        objects = data['knowledge']['objects']
        data['progressive'] = progressive.select(objects, data['quality']['assessments'], data['quality']['issues'], rules['level_budgets'])
        # Concept/chapter contributions are references, not duplicated knowledge bodies.
        level = data['progressive']['l2']
        selected = [objects[item['id']] for item in level['items']]
        level['concept_ids'] = []
        for key in sorted({k for obj in selected for k in obj.get('concept_ids', [])}):
            size = len(data['concepts'][key]['canonical_name'])
            if len(level['concept_ids']) < 40 and level['chars'] + size <= level['budget']:
                level['concept_ids'].append(key)
                level['chars'] += size
        level['chapters'] = []
        for key, chapter in data['book']['chapters'].items():
            contributions = [obj['id'] for obj in selected if key in obj.get('supporting_chapters', [])]
            size = len(chapter['title']) + sum(len(progressive.title(objects[k])) for k in contributions)
            if level['chars'] + size <= level['budget']:
                level['chapters'].append({'chapter_id': key, 'object_ids': contributions})
                level['chars'] += size
        search = []
        for key, obj in objects.items():
            title = progressive.title(obj)
            body = ' '.join(f['text'] for f in progressive.texts(obj))
            search.append({'id': key, 'anchor': obj['anchor'], 'type': obj['type'], 'title': title[:180], 'preview': body[:180], 'text': normalize(title+' '+body)})
        for key, concept in data['concepts'].items():
            title = concept['canonical_name']
            search.append({'id': key, 'anchor': 'concept-'+key, 'type': 'concept', 'title': title, 'preview': (concept.get('zh_name') or ''),
                           'text': normalize(' '.join([title, (concept.get('zh_name') or ''), *concept['aliases']]))})
        data['search'] = sorted(search, key=lambda s: s['id'])
        high = [o for o in objects.values() if o['type'] in progressive.HIGH]
        data['cards'] = {'object_ids': [o['id'] for o in high]}
        nodes = {o['id']: {'title': progressive.title(o), 'anchor': o['anchor'],
                         'children': [k for k in o['lower_ids'] if objects[k]['type'] in progressive.HIGH]+o.get('concept_ids', [])} for o in high}
        nodes.update({k: {'title': c['canonical_name'], 'anchor': 'concept-'+k, 'children': []} for k, c in data['concepts'].items()})
        child_ids = {k for n in nodes.values() for k in n['children']}
        data['mindmap'] = {'nodes': nodes, 'roots': [k for k in nodes if k not in child_ids]}
        return cls(data)
