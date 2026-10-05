"""Durable append-only journals; recover only incomplete final JSONL frames."""
import json
import os
from book_distiller.core.errors import StorageError


def read_events(path):
    if not path.exists():
        return []
    data = path.read_bytes()
    lines = data.splitlines(keepends=True)
    events = []
    for i, line in enumerate(lines):
        if not line.endswith(b'\n') and i == len(lines)-1:
            break  # a killed append is not a committed event
        try:
            events.append(json.loads(line))
        except (ValueError, UnicodeError) as exc:
            raise StorageError(f'Corrupt committed journal frame: {path}:{i+1}') from exc
    return events


def append_event(path, event):
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        data = path.read_bytes()
        if data and not data.endswith(b'\n'):
            # Preserve the interrupted tail for diagnosis before recovery.
            tail = data[data.rfind(b'\n')+1:]
            recovery = path.with_name(path.name+'.interrupted')
            with recovery.open('ab') as out:
                out.write(tail+b'\n'); out.flush(); os.fsync(out.fileno())
            with path.open('r+b') as out:
                out.truncate(len(data)-len(tail)); out.flush(); os.fsync(out.fileno())
    with path.open('ab') as out:
        out.write((json.dumps(event, ensure_ascii=False, allow_nan=False)+'\n').encode())
        out.flush(); os.fsync(out.fileno())
