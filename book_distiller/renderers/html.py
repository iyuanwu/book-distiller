"""Local classic scripts work over file:// without JSON fetch or a server."""
import json
import shutil
from jinja2 import Environment, FileSystemLoader, select_autoescape
from book_distiller.renderers import markdown
from book_distiller.core.errors import StorageError


def emit(view, destination, project, rules):
    data = view.data
    destination.mkdir(parents=True)
    (destination/'data').mkdir()
    assets = destination/'assets'
    assets.mkdir()
    for key, value in data.items():
        (destination/'data'/f'{key}.json').write_text(json.dumps(value, ensure_ascii=False, separators=(',', ':')), encoding='utf-8')
    bundle = json.dumps(data, ensure_ascii=False, separators=(',', ':'), allow_nan=False)
    for char in '<>&\u2028\u2029':
        bundle = bundle.replace(char, '\\u%04x' % ord(char))
    (assets/'book-data.js').write_text('window.BOOK_DISTILLER_DATA = '+bundle+';\n', encoding='utf-8')
    templates = project/'templates/reader'
    for name in ('app.js', 'reader.css'):
        shutil.copyfile(templates/name, assets/name)
    env = Environment(loader=FileSystemLoader(templates), autoescape=select_autoescape(['html']))
    (destination/'index.html').write_text(env.get_template('index.html').render(book=data['book'], quality=data['quality']['report']), encoding='utf-8')
    markdown.write(data, destination/'markdown')
    metrics = {'html_bytes': (destination/'index.html').stat().st_size,
               'data_bytes': sum(p.stat().st_size for p in (destination/'data').iterdir()),
               'evidence_chars': data['evidence']['embedded_chars'], 'search_entries': len(data['search']),
               'total_bytes': sum(p.stat().st_size for p in destination.rglob('*') if p.is_file())}
    if metrics['total_bytes'] > rules['max_output_bytes']:
        raise StorageError('Reader exceeds output size limit; previous reader retained')
    metrics['warnings'] = []
    if metrics['total_bytes'] > rules['warning_bytes']:
        metrics['warnings'].append('Large reader: output exceeds configured warning size')
    if any(c['excerpt_truncated'] for c in data['evidence']['entries'].values()):
        metrics['warnings'].append('Evidence excerpt display is bounded; use source locations for complete ranges')
    return metrics
