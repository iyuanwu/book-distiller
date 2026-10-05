"""Auditable, rebuildable run events; execution state remains in SQLite."""
from datetime import datetime, timezone
from typing import Literal
from pydantic import Field, AwareDatetime
from book_distiller.core.models import CommonModel
from book_distiller.storage.journal import append_event


class RunEvent(CommonModel):
    event: Literal['RUN_CREATED','RUN_STARTED','TASK_PREPARED','TASK_COMPLETED',
        'TASK_REUSED','TASK_INVALIDATED','TASK_FAILED','RUN_PAUSED','RUN_RESUMED',
        'GENERATION_PUBLISHED','DOWNSTREAM_INVALIDATED','HUMAN_LOCK_APPLIED',
        'HUMAN_OVERRIDE_REBASED','RUN_COMPLETED','RUN_FAILED','CLAIMS_READY']
    at: AwareDatetime = Field(default_factory=lambda: datetime.now(timezone.utc))
    details: dict = Field(default_factory=dict)


def related_event(directory, kind, **details):
    """Attach a human publication event to the latest local run, if one exists."""
    import json
    candidates = []
    for path in (directory/'runtime/runs').glob('*/run.json'):
        value = json.loads(path.read_text())
        candidates.append((value['updated_at'], path.parent))
    if candidates:
        root = max(candidates, key=lambda pair:pair[0])[1]
        append_event(root/'events.jsonl',RunEvent(event=kind,details=details).model_dump(mode='json'))
