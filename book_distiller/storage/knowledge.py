"""One chapter pointer publishes Claims, Atoms and their manifest together."""
from dataclasses import dataclass
import os
from pathlib import Path
from uuid import uuid4
from book_distiller.core.errors import StorageError


@dataclass
class Publication:
    pointer: Path
    previous: str | None

    def restore(self):
        if self.previous is None:
            self.pointer.unlink(missing_ok=True)
        else:
            switch(self.pointer,self.previous)


def switch(pointer: Path, target: str):
    temporary=pointer.with_name(f'.{pointer.name}-{uuid4()}.tmp')
    try:
        temporary.symlink_to(target,target_is_directory=True)
        temporary.replace(pointer)
    finally:
        temporary.unlink(missing_ok=True)


def current(root: Path, chapter_id: str) -> Path | None:
    pointer=root/'knowledge/chapters'/chapter_id
    if not pointer.is_symlink():
        if pointer.exists(): raise StorageError('Knowledge chapter pointer must be a managed symlink')
        return None
    target=Path(os.readlink(pointer))
    if len(target.parts)!=3 or target.parts[:2]!=('..','.generations'):
        raise StorageError('Unsafe knowledge generation pointer')
    destination=pointer.parent/target
    if (root/'knowledge').is_symlink() or pointer.parent.is_symlink() or (root/'knowledge/.generations').is_symlink() or destination.is_symlink() or not destination.is_dir():
        raise StorageError('Missing or unsafe knowledge generation')
    return destination.resolve()


def publish(root: Path, chapter_id: str, destination: Path) -> Publication:
    pointer=root/'knowledge/chapters'/chapter_id
    pointer.parent.mkdir(parents=True,exist_ok=True)
    current(root,chapter_id)  # validates the old pointer before replacement
    old=os.readlink(pointer) if pointer.is_symlink() else None
    switch(pointer,f'../.generations/{destination.name}')
    return Publication(pointer,old)
