"""Explicit recovery file patterns; no whole-book recursive copy or exclusion list."""
from pathlib import Path
import re
from uuid import UUID
from book_distiller.bundle.service import CHAPTER_FILES, PARSE_FILES, READER_FILES, must
from book_distiller.bundle.archive import safe_name
from book_distiller.storage import book_knowledge, verification

U = r'[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}'
G = U + '(?:-' + U + ')?'
TASK_FILES = {'request.json','context.json','context.md','workflow.md','prompt.md','output.schema.json','result.json','validation.json','apply.json','accepted.json'}
GROUPS = {
    'normalized':('.parsed-generations', PARSE_FILES | {'raw/parse_metadata.json','raw/docling.json','raw/docling.md','raw/plaintext.txt'}),
    'chapter':('knowledge/.generations', CHAPTER_FILES),
    'book':('knowledge/.book-generations', book_knowledge.FILES | {'book_model.json'}),
    'verification':('verification/generations', verification.FILES | {'manifest.json'}),
}
PATTERNS = [
    (r'manifest\.json','metadata'),
    (r'analysis/classification\.json','canonical'),
    (rf'analysis/classification-generations/{U}\.json','canonical'),
    (r'human/(overrides|notes|rules)\.jsonl|human/state\.json','human'),
    (r'ask/history\.jsonl|ask/answers/'+U+r'\.json','ask'),
    (r'source/original\.(pdf|epub|txt|md|markdown|docx)','source'),
    (r'metadata/db-export\.json','metadata'),
    (rf'runtime/tasks/{U}/('+'|'.join(re.escape(n) for n in TASK_FILES)+')','runtime'),
    (rf'runtime/runs/{U}/(run\.json|plan\.json|events\.jsonl)','runtime'),
    (rf'runtime/generations/{U}/(generation\.json|chunks\.json|claims-fork\.json)','runtime'),
    (r'runtime/chapter-work/ch_\d{4,}\.json','runtime'),
    (rf'runtime/book-generations/{U}/(state\.json|items\.jsonl|reduce-\d+-\d+\.json)','runtime'),
    (rf'runtime/verification-generations/{U}/(state\.json|object-[0-9a-f]+\.json|assessment-[a-zA-Z0-9_-]+\.json)','runtime'),
    (r'runtime/(book-synthesis|verification-active|verification-failure|stale|chapter-reduce-ch_\d{4,})\.json','runtime'),
    (rf'knowledge/\.pending/{G}/(claims\.jsonl|chunks\.json|claims-receipt\.json)','canonical'),
    (r'reader-status\.js','derived'),
]
for kind,(parent,names) in GROUPS.items():
    PATTERNS.append((re.escape(parent)+'/'+G+'/('+'|'.join(re.escape(n) for n in names)+')', 'verification' if kind=='verification' else 'canonical'))
PATTERNS.append((r'\.reader-generations/'+G+'/('+'|'.join(re.escape(n) for n in READER_FILES)+')','derived'))


def category(name):
    safe_name(name)
    for pattern, kind in PATTERNS:
        if re.fullmatch(pattern,name):return kind
    raise ValueError('BACKUP_FILE_NOT_ALLOWED: '+name)


def plan(root):
    names={'manifest.json':'metadata'}
    # Each directory and depth is explicit. Unknown files are not opened.
    globs=['analysis/*.json','analysis/classification-generations/*.json','human/*','ask/history.jsonl','ask/answers/*.json',
           'runtime/*.json','runtime/tasks/*/*','runtime/runs/*/*','runtime/generations/*/*','runtime/chapter-work/*.json',
           'runtime/book-generations/*/*','runtime/verification-generations/*/*','knowledge/.pending/*/*','reader-status.js']
    for parent, files in GROUPS.values():
        globs.extend(parent+'/*/'+name for name in files)
    pointers={}
    for pointer in [root/'parsed',root/'knowledge/book',root/'verification/current',root/'output',*sorted((root/'knowledge/chapters').glob('ch_*'))]:
        if pointer.is_symlink():
            destination=pointer.resolve()
            must(destination.is_relative_to(root.resolve()) and destination.is_dir(),'unsafe backup pointer')
            pointers[pointer.relative_to(root).as_posix()]=destination.relative_to(root.resolve()).as_posix()
    if 'output' in pointers:
        globs.extend(pointers['output']+'/'+n for n in READER_FILES)
    for pattern in globs:
        for path in root.glob(pattern):
            name=path.relative_to(root).as_posix()
            try: kind=category(name)
            except ValueError: continue
            must(path.is_file() and not path.is_symlink() and not any(p.is_symlink() for p in path.parents if p != root and p.is_relative_to(root)), 'unsafe backup file '+name)
            names[name]=kind
    return names,pointers


def generations(names):
    result={kind:sorted({n[len(parent)+1:].split('/')[0] for n in names if n.startswith(parent+'/')}) for kind,(parent,_) in GROUPS.items()}
    result['reader']=sorted({n.split('/')[1] for n in names if n.startswith('.reader-generations/')})
    return result


def validate_layout(value):
    names={f.relative_path for f in value.files}
    must(len(names)==len(value.files),'duplicate backup inventory')
    must({'manifest.json','metadata/db-export.json'} <= names,'backup metadata missing')
    for f in value.files:must(category(f.relative_path)==f.category,'backup category')
    must(generations(names)==value.generation_inventory,'generation inventory')
    for pointer,destination in value.pointers.items():
        safe_name(pointer);safe_name(destination)
        allowed={'parsed':'.parsed-generations','knowledge/book':'knowledge/.book-generations','verification/current':'verification/generations','output':'.reader-generations'}
        parent=allowed.get(pointer)
        if re.fullmatch(r'knowledge/chapters/ch_\d{4,}',pointer):parent='knowledge/.generations'
        must(parent is not None and re.fullmatch(re.escape(parent)+'/'+G,destination) is not None,'backup pointer destination')
        must(any(n.startswith(destination+'/') for n in names),'missing pointer generation')
    must(len(value.generation_inventory['reader'])<=1,'only current Reader')
    if value.generation_inventory['reader']:
        must(value.pointers.get('output')=='.reader-generations/'+value.generation_inventory['reader'][0],'Reader pointer')
    must(bool([n for n in names if n.startswith('source/')])==value.source_included,'source inventory')
    must(value.checkpoint_inventory==sorted(n for n in names if n.startswith('runtime/')),'checkpoint inventory')
