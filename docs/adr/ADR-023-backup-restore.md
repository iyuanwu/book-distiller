# ADR-023: Per-book Backup / Restore

Status: accepted for Phase 9C; SQLite remains schema 2.

A Bundle is a current portable snapshot. A Backup is a private recovery artifact for one Book/Edition. V1 has no whole-Library backup, cloud storage, encryption, merge or destructive replacement.

## Recovery contract

`BookBackupManifest` 1.0 inventories explicit allowed files, all retained Chapter/Book/Verification generations, normalized generations, current Reader, Human append-only journals and state, Book Rules, Ask answers/history, Run/Task metadata and required checkpoint packages. SQLite rows are a closed, versioned `metadata/db-export.json` containing only books, editions, runs, tasks and run_tasks for the selected Edition. No SQLite binary is copied. Global/Type Rule stores are excluded; IDs, applicability and hashes are dependencies, not instructions. Immutable historical Task Context Packages can retain the applicable Human Guidance text already used by that Task: stripping it would invalidate its Context hash. Such snapshots are audit/execution history only and are never installed as active Global/Type Rules.

Copied Source is included when present. External Source is excluded unless `--include-external-source` is explicit. The restored source path is local and managed even when bytes are absent, so the old machine's path cannot accidentally satisfy availability. Omitted Source prevents reparse; valid normalized evidence remains usable.

Processing Run becomes paused; running Task becomes pending. IDs, cursor, scope, failure information and parent/resume links remain. Original statuses are audited in `.backup-restore.json`. Leases, execution.lock, PIDs, sockets and unrelated runtime/log/cache files are never restored. Active OS Run leases make export fail with BACKUP_RUN_BUSY. Completed result bytes and generation/Human histories remain unchanged. `run.json` is a repairable projection of canonical SQLite execution_json.

## Shared archive implementation

Bundle and Backup share `ArchiveProfile`, bounded SafeZip streaming, path/duplicate/special-file checks, checksums and exclusive self-validated publication. Profile-specific semantic validation checks DB references, Run/Task/checkpoint identities, stored Context/resource/result hashes, generation manifests and Human state. Resume still rebuilds current Context and checks current workflow/schema/input/generation hashes. A valid historical package is not a waiver of stale checks.

## Restore transaction and crash recovery

Validate the whole archive privately before opening the target Library. Under the Library lock: reject conflicts; stage only declared files; create fresh relative managed pointers and local provenance; normalize process states; begin SQLite transaction; publish directory; insert the relational subset; post-validate; commit. Ordinary exceptions roll back both owned files and SQL rows. Identical state is a no-op; any Human, generation, file, Task or Run change rejects. No merge/force-overwrite exists.

A fsynced `data/restore-intents/<book_id>.json` records ownership, transaction ID, backup hash, destination slug and staging name. A crash after rename and before SQL commit leaves a detectable owned orphan. Retry checks the same archive and recovered file projection, moves the orphan into `data/restore-recovery/<transaction_id>` for preservation, then completes a fresh restore. An abandoned owned staging directory is similarly preserved. Unknown or modified orphan directories fail closed. A committed book is never rolled back because an intent remained.

This is recoverable publication, not a hardware atomic filesystem/SQLite transaction. Fsync improves durability; media loss or inconsistent bytes are detected by checksums and require the original backup. Quarantined recovery directories are retained for manual review and consume space.

## Limits

All retained canonical/execution history increases archive size; no compaction, incremental chains or retention deletion is introduced. Only current Reader is stored. Current and stale Readers use trusted templates and bounded JSON projections, including portable Source links, without changing their generation IDs. Stale Readers retain their historical data and remain stale. The live reader-status.js file must be exactly a Core JSON status assignment; appended scripts and a current flag that hides semantic invalidation are rejected. Checksummed executable Reader files are not trusted solely because their checksum matches. Ask history is included by default. External Rule dependencies may differ on the target; new Context Packages report the missing/changed dependency and use only locally available rule bodies. Future DB/archive versions require an explicit migration, not speculative acceptance.
