"""Portable text views from the same selections consumed by the static UI."""
from html import escape
from book_distiller.renderers.progressive import title, texts


def write(data, directory):
    directory.mkdir()
    objects = data['knowledge']['objects']
    for n in range(4):
        view = data['progressive'][f'l{n}']
        lines = [f'# L{n} — {escape(data["book"]["title"])}', '',
                 f'Quality gate: {data["quality"]["report"]["status"]}', '',
                 'Deterministic selection of canonical AI knowledge; not source quotations.', '']
        if n == 1:
            lines += ['Classification: '+data['book']['classification']['primary_type']]
        for item in view['items']:
            obj = objects[item['id']]
            lines += [f'## {escape(item["title"])}', f'Object: {obj["id"]}',
                      f'Verification: {data["quality"]["assessments"][obj["id"]]["fidelity_verdict"]}', '']
            for field in item['fields']:
                lines += [f'{field["field"]}: {escape(field["text"])}', '']
        if n == 2:
            lines += ['## Concepts', *[escape(data['concepts'][k]['canonical_name']) for k in view['concept_ids']],
                      '## Chapters']
            for contribution in view['chapters']:
                lines += [escape(data['book']['chapters'][contribution['chapter_id']]['title']),
                          *[escape(title(objects[k])) for k in contribution['object_ids']]]
        (directory/f'L{n}.md').write_text('\n\n'.join(lines), encoding='utf-8')
    lines = ['# Complete derived Knowledge Model', 'Canonical authority remains knowledge/ and verification/.']
    for obj in objects.values():
        lines += [f'## {escape(title(obj))}', f'ID: {obj["id"]}',
                  f'Verification: {data["quality"]["assessments"][obj["id"]]["fidelity_verdict"]}',
                  *[f'{f["field"]}: {escape(f["text"])}' for f in texts(obj, True)],
                  'Links: '+', '.join(obj['lower_ids'])]
    import json
    for name in ('concepts',):
        lines += [f'## {name}', '```json', json.dumps(data[name], ensure_ascii=False, indent=2).replace('`', '\\u0060'), '```']
    lines += ['## Relationships', '```json', json.dumps(data['knowledge']['relationships'], ensure_ascii=False, indent=2).replace('`', '\\u0060'), '```']
    (directory/'knowledge-model.md').write_text('\n\n'.join(lines), encoding='utf-8')
    from book_distiller.quality.review import render_report
    (directory/'quality-report.md').write_text(escape(render_report(data['quality']['report'])), encoding='utf-8')
