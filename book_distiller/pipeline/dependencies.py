"""Authoritative, deterministic pipeline dependency graph. No source body reads."""
from book_distiller.core.models import CommonModel
from pydantic import Field
from book_distiller.pipeline.result_validation import ProtocolError

GRAPH_VERSION = 'pipeline-graph-v1'
STAGES = ('parse', 'classification', 'claims', 'atoms', 'book', 'verification', 'render')
EDGES = {'parse': ('classification',), 'classification': ('claims', 'book'),
         'claims': ('atoms',), 'atoms': ('book',), 'book': ('verification',),
         'verification': ('render',), 'render': ()}
ALIASES = {'chapter_claims': 'claims', 'chapter_atoms': 'atoms', 'book_synthesis': 'book', 'reader': 'render', 'verify': 'verification'}


def stage_name(value):
    value = ALIASES.get(value, value)
    if value not in STAGES:
        raise ProtocolError('INVALID_STAGE', ', '.join(STAGES))
    return value


def downstream(stage):
    found = set()
    def visit(node):
        for child in EDGES[node]:
            if child not in found:
                found.add(child)
                visit(child)
    visit(stage_name(stage))
    return [s for s in STAGES if s in found]


class RerunPlan(CommonModel):
    pipeline_graph_version: str = GRAPH_VERSION
    requested_stage: str
    requested_scope: dict
    affected_chapters: list[str]
    steps: list[dict]
    reused_generations: dict = Field(default_factory=dict)
    invalidated_generations: dict = Field(default_factory=dict)
    tasks_to_reuse: list[str] = Field(default_factory=list)
    tasks_to_create: list[dict] = Field(default_factory=list)
    protected_objects: list[dict] = Field(default_factory=list)
    blocking_lock_conflicts: list[dict] = Field(default_factory=list)
    downstream_stale: list[str] = Field(default_factory=list)
    estimated_steps: int = 0


class RerunPlanner:
    def plan(self, stage, chapters, chapter=None, through='render', generations=None, protected=None):
        stage, through = stage_name(stage), stage_name(through)
        if STAGES.index(through) < STAGES.index(stage):
            raise ProtocolError('INVALID_SCOPE', '--through precedes --from')
        if chapter and (stage not in ('claims', 'atoms') or chapter not in chapters):
            raise ProtocolError('INVALID_SCOPE', '--chapter requires claims/atoms and a Canonical chapter ID')
        affected = [chapter] if chapter else sorted(chapters)
        cascade = [stage, *downstream(stage)]
        steps = [{'stage': s, 'chapter': ch} for s in cascade if STAGES.index(s) <= STAGES.index(through)
                 for ch in (affected if s in ('claims', 'atoms') else [None])]
        invalid = {}; reused = {}
        for key, value in sorted((generations or {}).items()):
            kind, _, ch = key.partition(':')
            (invalid if kind in cascade and (not ch or ch in affected) else reused)[key] = value
        return RerunPlan(requested_stage=stage, requested_scope={'chapter': chapter, 'through': through},
            affected_chapters=affected, steps=steps, reused_generations=reused, invalidated_generations=invalid,
            tasks_to_create=steps, protected_objects=protected or [], downstream_stale=downstream(stage), estimated_steps=len(steps))


def compatible_dependencies(current, saved):
    from book_distiller.pipeline.canonical import json_hash
    def normalize(value):
        if not isinstance(value,dict):
            return value
        return {k:normalize(v) for k,v in value.items()
                if not (k in {'human_atoms_hash','human_semantic_hash'} and v == json_hash([]))}
    return normalize(current) == normalize(saved)


class ResumePlanner:
    """Metadata-only eligibility pass; Core validates each reused package before use."""
    def plan(self, tasks):
        plan = {'reuse_candidates':[], 'pending':[], 'recover':[], 'invalidated':[]}
        for task in sorted(tasks,key=lambda t:str(t['task_id'])):
            key = ('invalidated' if task.get('stale') else 'reuse_candidates' if task['status']=='completed'
                   else 'recover' if task['status'] in ('running','processing') else 'pending')
            plan[key].append(str(task['task_id']))
        return plan
