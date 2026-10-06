"""Book-scoped relational export, with no SQL identifiers supplied by an archive."""
import json
from book_distiller.backup.models import DBExport
from book_distiller.core.models import RunMetadata
from book_distiller.core.enums import BookStatus, StageStatus

TABLES=('books','editions','runs','tasks','run_tasks')


def export_rows(database, book_id, edition_id):
    with database.connect() as c:
        c.execute('BEGIN')
        values={
            'books':[dict(r) for r in c.execute('SELECT * FROM books WHERE book_id=?',(str(book_id),))],
            'editions':[dict(r) for r in c.execute('SELECT * FROM editions WHERE edition_id=?',(str(edition_id),))],
            'runs':[dict(r) for r in c.execute('SELECT * FROM runs WHERE edition_id=? ORDER BY run_id',(str(edition_id),))],
            'tasks':[dict(r) for r in c.execute('SELECT * FROM tasks WHERE edition_id=? ORDER BY task_id',(str(edition_id),))],
            'run_tasks':[dict(r) for r in c.execute('SELECT rt.* FROM run_tasks rt JOIN runs r ON rt.run_id=r.run_id WHERE r.edition_id=? ORDER BY rt.run_id,rt.task_id',(str(edition_id),))],
        }
    return DBExport.model_validate(values)


def normalize(value):
    result=value.model_copy(deep=True)
    for row in result.runs:
        if row.status=='processing':
            row.status='paused'
            if row.execution_json:
                run=RunMetadata.model_validate_json(row.execution_json)
                run.status=BookStatus.PAUSED
                row.execution_json=run.model_dump_json()
    for task in result.tasks:
        if task.status.value=='running':task.status=StageStatus.PENDING
    # Assignment validation converts strings back to enums.
    return DBExport.model_validate(result.model_dump(mode='json'))


def insert_rows(connection, value):
    rows=value.model_dump(mode='json')
    for table in TABLES:
        for row in rows[table]:
            # Keys come from the closed Pydantic model, never from raw ZIP JSON.
            columns=tuple(row)
            connection.execute(f"INSERT INTO {table} ({','.join(columns)}) VALUES ({','.join('?' for _ in columns)})",tuple(row[k] for k in columns))
