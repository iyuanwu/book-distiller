# ADR-022: Portable current-book snapshots

Status: accepted for Phase 9B. Phase 9C is not started.

## Decision

A `*.bookbundle.zip` carries one current Book/Edition, Canonical normalized document,
current Classification/Chapter/Book/Verification generations, Human journals and Book
Rules, and a current static Reader. It is not a Backup or execution recovery format.
Ask answers/history, runtime, Tasks/Runs, raw parser outputs, caches, historical knowledge
generations, SQLite, system prompts, and Global/Type Rule bodies are excluded.
Original source bytes are excluded by default; `--include-source` is explicit and verifies
the original SHA256 and size. Both modes contain source-derived normalized text and
potentially private Human Notes. An archive is not anonymized or encrypted.

The Pydantic `BookBundleManifest` 1.0 and generated schema define the exact inventory,
identities, generation bindings, Human and Book Rule hashes, external rule dependencies,
source mode, artifact versions and portable pointer destinations. SHA256 covers every
payload file. The manifest is not self-hashed; `bundle_content_hash` hashes the canonical
manifest excluding only its own hash and `created_at`. Output filename and ZIP timestamps
are outside this logical content identity. Checksums detect corruption, not author identity
or the truth of an AI assessment; bundles are not signed.

## Export and validation

Core constructs a `BundlePlan` from an explicit whitelist and managed current pointers.
It never copies a whole Book and subsequently excludes paths. Canonical Knowledge,
Verification and Human journals preserve their bytes and IDs. Source metadata becomes a
nonfunctional `/bundle-source/original.ext` provenance placeholder with a relative stored
path. Reader data/source links and display hashes are deterministically projected to the
portable location, preserving the Reader generation ID and all semantic content. Source
opens through `output/index.html` using `../source/original.ext` when included.

Export requires current dependencies, PASS or NEEDS_REVIEW, valid Human state, and a
current intact Reader. A stale Reader requires an explicit `book render`; no AI rerun is
implicit. Export writes a temporary ZIP, invokes the same full validator used by inspect
and import, then exclusively publishes a completed file in the destination directory.
An existing destination is never replaced.

Inspect and import stream ZIP members into private temporary staging: no `extractall`.
Reject absolute, drive, backslash, traversal, noncanonical, duplicate/case-colliding paths,
symlinks, special files, encrypted entries, undeclared/missing files, bad hashes, and unknown
schemas/versions. Ordinary directories are allowed only as parents of declared files.
`BundleLimits` centralizes configurable defaults: 50,000 entries, 8 GiB total expanded,
2 GiB per file, 2,000:1 compression ratio, 16 MiB manifest. Both declared sizes and actual
streamed bytes are bounded. ZIP byte integrity is followed by the narrower Book whitelist,
identity/schema/reference checks, all Citation ranges/hashes/SourceSpans, current Effective
Knowledge/Verification checks and deterministic Reader projection comparison. Imported
HTML/JavaScript must equal the trusted local renderer's output, not merely match a supplied
checksum. The validator does not execute scripts from an archive.

## Import and execution boundaries

Import validates before touching its target Library or database. It creates only Book and
Edition rows (SQLite schema remains 2), copies validated files to Library staging, recreates
only validated relative Core-owned current pointers, publishes and validates the actual
final location before the database transaction commits. Ordinary errors in copy, publication,
post-publication validation or commit roll back both records and this import's files.
Inspect creates no target Library, DB, Task, Run or reader output.

A generated `.bundle-import.json` pins imported artifact byte hashes and source policy.
This local receipt is never accepted as archive payload. Existing parse/classification/
chapter gates accept an intact imported artifact OR a completed local Task; they do not
invent historical execution records. Current resource/dependency, schema and integrity
checks remain active. Status identifies imported artifacts separately from local execution.
Source omission is permitted only under explicit import provenance. Reparse still requires
original bytes. New Ask/verification/knowledge work creates real local Tasks through the
existing protocol. No imported Run can be resumed because there is no imported Run.

Book/Edition/Source IDs, parse/Chapter/Book/Verification/Reader generations and human target
refs survive. Identical current snapshots are a no-op after comparison against current
content, not just an old import receipt. Changed generation/Human/Rule state, an existing
slug, or the same Source SHA under another identity is rejected. No overwrite, merge,
auto-unlock, similarity matching or semantic rebinding is offered.

Human historical events and rebase lineage remain in journals even though historical
Knowledge files are excluded. Historical unresolved notes stay historical; current targets
must resolve. Lock anchors are checked. Global/Type Rule IDs, body hashes and applicable
workflows are dependencies only. New Tasks use locally available rules with explicit
`missing_or_changed` warnings when dependencies cannot be matched. Existing snapshot
reading remains available. System invariants > Book > Type > Global remains unchanged.

## Limits and consequences

Derived Reader projection needs memory proportional to its existing structured view; ZIP
copy/checksum and original source handling stream bytes. Normalized source plus Reader
JSON/embedded data can dominate bundle size. The opt-in synthetic benchmark is
`tests/performance/bundle_roundtrip.py` (5,000 Claims, 1,000 Atoms, 5,000 Citations); its
judgments are test doubles, not real Codex evidence.

Compatibility is intentionally conservative: unknown Bundle/artifact versions and changed
workflow/renderer dependencies fail instead of guessing a migration. Global/Type Rule
bodies must be deliberately restored by the user; they are never reconstructed from hashes.
Cross-browser `file://` behavior still depends on the browser; DOM harness tests do not
constitute visual browser acceptance. Use the managed `output/index.html` entry path.

The filesystem and SQLite do not share a hardware transaction. Ordinary failure rollback
is covered; SIGKILL or power loss between final directory publication and DB commit can
leave an unindexed directory, detected by existing inventory checks. Automatic crash
recovery, full-state backup and disaster restore are outside Phase 9B and are not claimed.
