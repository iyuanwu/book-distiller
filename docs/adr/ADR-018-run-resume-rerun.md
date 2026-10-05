# ADR-018: User Runs, checkpoint recovery and scoped rerun

Status: Accepted for Phase 8 implementation

## Decision

Extend the existing `RunMetadata` and SQLite `runs` table rather than introduce a second execution model. Schema 2 adds `execution_json` and a `run_tasks` association. Migration is transactional, preserves schema-1 records, and rejects unknown newer schemas before writing.

A user rerun creates a Run with an explicit requested scope and deterministic resolved plan. `resume` continues the same Run ID. Pending AI work is `paused`: there is no background model worker. Existing stage commands remain available as low-level adapters; their old tasks are not automatically adopted into a user Run. Missing resumable Run is an explicit error.

SQLite execution JSON is authoritative. `runtime/runs/<id>/run.json`, `plan.json`, and `events.jsonl` are local audit/projection files. AI context remains in its existing Task directory. Journal entries are diagnostic, not canonical execution truth. The event schema includes preparation/completion/reuse/invalidation, run lifecycle, publication, downstream invalidation and human lock/rebase events.

The centralized `pipeline-graph-v1` graph is:

`parse -> classification -> claims[chapter] -> atoms[chapter] -> book -> verification -> render`

Classification also invalidates Book synthesis. Chapter scope affects only that chapter and its shared Book downstream. `--through` bounds execution, not invalidation. Claims-only leaves a candidate; Chapter Claims and Atoms publish together. Plans report generation/task reuse, stage work, protected refs, conflict placeholders and downstream stale state. Stage steps expand into bounded AI Tasks during execution; they are not an exact advance estimate of model-call count. Dry-run performs no migrations, writes, or source-body reads. Detailed evidence-hash validation of protected objects occurs when executing the Run; an empty planning conflict list is not proof that execution cannot encounter a lock conflict.

## Resume and recovery

A nonblocking per-Run `flock` lease rejects simultaneous execution. OS process death releases it; lockfile existence is not ownership. Adapter mutations retain the existing library lock. Before reuse, Core checks task identity, request/context digest, readable context projection, workflow/prompt/schema snapshots, source/normalized identity, upstream dependencies, accepted schema and apply receipt hash. Human Guidance is frozen per Task. New rules apply to future Tasks only.

An accepted apply intent left with a pending SQLite Task is replayed through deterministic `submit`; Codex is not asked again. Chapter, Book and Verification publication recovery recognizes an already-current generation. Protected Book refs are persisted before switching the pointer so rebase can be replayed. Finished Runs return without new work. Interrupted parser candidates are retained under `parse_failures`; parser internals are not checkpointed, so incomplete parse restarts. A sealed current parse is integrity-checked and reconciled with SQLite.

`ResumePlanner` is a metadata-only eligibility pass. Reuse candidates are not trusted until the Core checks above succeed. The 1000-task benchmark measures planning, not parsing, context construction, full integrity validation, or model latency.

## Boundaries

This is a single-host filesystem/SQLite mechanism, not a distributed queue. Completed Run resume is a no-op; intentional new work uses rerun. Source changes require a new ingest identity. A changed dependency or protected object may require a new scoped run or explicit human correction; no fuzzy rebinding or automatic unlock is allowed. Historical generation directories are retained. Reader candidate failure preserves the old published Reader.
