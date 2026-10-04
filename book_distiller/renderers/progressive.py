"""Deterministic, bounded selections of existing text; never new summaries."""
import re

HIGH = ('meta_principle', 'core_idea', 'mental_model')
FIELDS = ('statement', 'summary', 'description', 'mechanism', 'when_to_use', 'limitations', 'examples', 'reasoning')


def title(obj):
    return obj.get('title') or obj.get('name') or obj.get('statement') or obj['id']


def texts(obj, detail=False):
    result = []
    for key in FIELDS if detail else FIELDS[:3]:
        value = obj.get(key)
        for text in value if isinstance(value, list) else [value]:
            if text:
                result.append({'field': key, 'text': str(text)})
    return result


def select(objects, assessments, issues, budgets):
    bad = {ref['object_id'] for issue in issues if issue['status'] == 'open' and
           (issue['severity'] in ('critical', 'major') or issue['issue_type'].lower() == 'synthesis_overreach')
           for ref in issue['object_refs']}
    strength = {'strong': 0, 'moderate': 1, 'weak': 2, 'insufficient': 3}
    def rank(o):
        a = assessments[o['id']]
        return (a['fidelity_verdict'] != 'supported', strength.get(a['evidence_strength'], 4),
                -o.get('importance', 0), HIGH.index(o['type']) if o['type'] in HIGH else 3, o['id'])
    ordered = sorted(objects.values(), key=rank)
    levels = {}
    for level, budget in enumerate(budgets):
        candidates = [o for o in ordered if
                      assessments[o['id']]['fidelity_verdict'] in (('supported', 'partially_supported') if level == 3 else ('supported',))
                      and (level == 3 or o['id'] not in bad) and (level == 3 or o['type'] in HIGH)]
        if level == 0:
            candidates.sort(key=lambda o: (HIGH.index(o['type']), rank(o)))
        limits = ({'meta_principle': 1, 'core_idea': 1, 'mental_model': 1} if level == 0 else
                  {'core_idea': 7, 'mental_model': 3, 'meta_principle': 2} if level == 1 else
                  {'core_idea': 25, 'mental_model': 12, 'meta_principle': 8, 'knowledge_atom': 16, 'atomic_claim': 24})
        if level == 3:
            for kind in ('knowledge_atom', 'atomic_claim'):
                total = sum(o['type'] == kind for o in objects.values())
                limits[kind] = min(limits[kind], max(1, int(total * 0.6)))
        items, counts, chars = [], {}, 0
        for obj in candidates:
            kind = obj['type']
            if counts.get(kind, 0) >= limits.get(kind, 0):
                continue
            fields = texts(obj, detail=level >= 2)
            if level == 0:
                fields = fields[:1]
            label = title(obj)
            size = len(label) + sum(len(f['text']) for f in fields)
            if level == 0 and size > budget:
                # Prefer an exact complete source sentence, then the canonical title.
                sentence = re.match(r'.*?(?:[。！？]|[.!?](?:\s|$))', fields[0]['text']) if fields else None
                if sentence and len(label) + len(sentence[0].strip()) <= budget:
                    fields = [{'field': fields[0]['field'] + ' (excerpt)', 'text': sentence[0].strip()}]
                    size = len(label) + len(fields[0]['text'])
                else:
                    fields = []
                    size = len(label)
            if chars + size > budget:
                continue
            items.append({'id': obj['id'], 'title': label, 'fields': fields})
            chars += size
            counts[kind] = counts.get(kind, 0) + 1
            if level == 0:
                break
        levels[f'l{level}'] = {'items': items, 'chars': chars, 'budget': budget,
                              'selection': 'existing canonical text only', 'omitted_objects': len(objects)-len(items)}
    levels['l4'] = {'object_ids': sorted(objects), 'complete': True}
    levels['l5'] = {'object_ids': sorted(assessments), 'layer': 'canonical source excerpts + verification'}
    return levels
