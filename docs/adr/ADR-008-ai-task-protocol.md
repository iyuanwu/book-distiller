# ADR-008: File-based AI Task Protocol

Status: accepted, Phase 3.

Python performs deterministic orchestration and never calls an LLM API. Current Codex reads the prepared materials, makes the classification judgment, and writes structured JSON. Only `classify_book` is implemented. This is classification infrastructure, not book distillation.

Existing schema-1 Task rows are sufficient: `pending` at prepare, `completed` after successful apply. No new enum, SQLite migration, fake Run, queue, or background worker. Failed validation leaves the same pending task retryable. The library lock serializes mutations.

Each `library/<slug>/runtime/tasks/<uuid>/` contains request.json, context.json, context.md, output.schema.json, workflow.md and prompt.md snapshots. Submission adds result.json, validation.json and an apply.json intent/hash receipt. Runtime is private, ignored by Git, and is not a Canonical Knowledge Model. Bundle inclusion remains undecided.

Workflow defines the task/rules; the Pydantic-generated schema defines structure; Prompt gives execution guidance; Skill routes user intent. Workflow and prompt have independent explicit versions, and their bytes and schema are hashed in request.json. Changes require a version bump; even an accidental same-version edit invalidates prepared tasks. No private chain of thought is requested or stored. A short rationale, selected Block evidence and subjective confidence are sufficient.

Prepare binds task/book/edition/source hash, normalized schema and generation fingerprint, workflow/prompt versions and Context hash. Submit verifies indexed identity, JSON schema, strict values, versions, hashes, Markdown projection, resource snapshots and the current Canonical document. It rebuilds the bounded selection before accepting results. Evidence must belong to the selected Context blocks. A forced parse creates a new generation and makes old requests stale even if its text and schema are unchanged. Source hashes are compared with manifest/index/Canonical provenance; AI tasks do not reopen the source document.

`analysis/classification.json` is the sole current classification authority. No full classification is duplicated into manifest or SQLite. Core assigns created_at; only Core writes this file. Status reads it even after runtime deletion. A reparse makes its displayed classification stale. Subsequent tasks may replace it, while old runtime tasks retain audit information. Repeating a completed submission never overwrites a newer classification; changing a completed result is rejected.

Apply validates before writing, fsyncs a unique temporary JSON file, atomically replaces canonical, and completes the Task in a SQLite transaction. Ordinary exceptions roll back the database and restore the prior canonical bytes. Filesystem and SQLite do not form one crash-atomic transaction: a killed process between replace and commit may expose a complete canonical file with a pending Task. Status reports pending apply; re-submit the same task to validate/reapply/complete. If commit succeeded but final validation receipt did not, canonical plus completed Task remain authoritative. This is bounded retry, not a general resume/history system. Runtime removal before completion prevents retry and is not automatic.

Human fields are fixed false. Human override, locking, full history, other workflows and all knowledge extraction remain deferred.
