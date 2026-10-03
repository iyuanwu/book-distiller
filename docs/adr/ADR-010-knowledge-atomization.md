# ADR-010: Chapter Knowledge Atomization

Status: accepted for Phase 4. No cross-chapter synthesis.

## Protocol and authority

The only chain is Normalized Blocks → Atomic Claims → Chapter Knowledge Atoms. Both new workflows reuse AITaskService's file preparation, request identity, schema/resource hashing, strict submit validation, retry and database completion. Python never calls an LLM. Task types are extract_claims and build_chapter_atoms; statuses remain pending/completed. SQLite schema 1 and unused Run are unchanged. Generation UUID groups a Chapter's tasks; it is not a Run.

Classification Context 1.0 remains unchanged. KnowledgeContext is an additive typed extension with package/builder version 1.1: stable generation metadata, classification, Chapter, current Chunk or full formal Chapter Claims. Its hash includes all content except itself. The growing checkpoint task map is excluded from the Context snapshot so preparing later tasks cannot invalidate earlier ones. Task-to-generation membership is separately validated. Each task has its own ID and hash.

Classification must be completed and refer to the current normalized generation. Its whole-file semantic hash participates in knowledge binding. Universal prompts plus finite investment/philosophy/business overlays are selected deterministically; primary emphasis and subordinate secondary hints are explicit, even when the primary has no custom overlay. All types use the same evidence-preserving schema. Overlay bytes join the prompt hash; workflow and schema hashes are also bound. No fixture-specific or implicit book-specific extension exists.

## Deterministic chunks

analysis-chunker-v1 streams blocks by Chapter, respecting Section changes first and preserving order. Default budget is 10,000 text characters, 3,500 estimated tokens at 3 chars/token, 30 blocks. Stable ordered chunk IDs depend on Chapter and ordinal, not random task identity. Primary blocks cover every Chapter block exactly once. Optional overlap is 0–2 prior ordinary prose blocks (default 1), same Section, included only if it fits. Evidence needs at least one primary Block.

Table, Code and Formula blocks are indivisible. Adjacent Figure/Caption pairs in the same Chapter/Section stay together. A single structural unit over budget forms an oversized Chunk with warning. Claim Context never silently truncates it: complete input may use a bounded envelope up to 200,000 characters, otherwise CHUNK_CONTEXT_TOO_LARGE stops processing. Context metadata overhead is counted separately from Chunk body budget; final full Context accounting remains enforced.

Chunk manifests retain IDs, ranges, budgets, normalized hash and version without repeating body text. Chunk IDs are rerun comparison coordinates, not permanent semantic identities. Claim IDs combine Chapter, Chunk ordinal and Claim ordinal; Atom IDs combine Chapter and Atom ordinal. Changed AI ordering can change IDs; these are not semantic identities either.

## Claims, Atoms and evidence

Claim/Atom schema 1.0 uses source_type=source only. Core enriches validated drafts with stable IDs, generation/task identity, hashes, versions, source quality and timestamps. Claims store Block references, not copied source text. Atoms store Claim references; Claims remain the authority for evidence. All references are constrained to the task/chapter, and rechecked against the current normalized generation before publication. SourceSpan stays in Normalized Blocks. Chapter metadata records the normalized parse fingerprint and relative immutable blocks path, so old generations remain auditable after a reparse.

Zero Claims and zero Atoms are valid. A Claim may support several Atoms; unassigned Claims are allowed. reasoning is public argument structure expressed by the source Claims, not hidden model reasoning; examples must be source examples. These semantic requirements are workflow rules, not something structural Python validation can prove.

Metrics are counts, assigned/unassigned Claims, evidence ID validity, exact normalized duplicate statements and average Claims per Atom. Warnings include no claims and high unassigned ratio (>50%). Exact duplicates are reported without automatically deleting claims or breaking IDs. There is no Fidelity, full-book coverage or Quality Gate.

## Storage, checkpoints and publication

A Chapter generation begins in runtime/generations/<UUID>/ with generation.json and chunks.json. runtime/chapter-work/<chapter>.json identifies the active attempt. Shared runtime/tasks/<task>/ stores each task's accepted result; completed tasks are reused on continuation. --force starts a fresh attempt and makes older in-flight tasks stale. Failed validation remains retryable.

After every Chunk succeeds, Core validates the collection and writes the complete candidate claims.jsonl plus chunks.json and a receipt under knowledge/.pending/<generation>/. This is formal candidate Chapter Claims input to the Atom task, not yet the current chapter result. A claims-only request stops here. No partial Chunk is appended to current canonical data.

After Atom validation, Core creates a complete immutable directory under knowledge/.generations/<generation>-<publication>/ containing claims.jsonl, atoms.json, chunks.json, claims-receipt.json and chapter.json. One relative symlink knowledge/chapters/<chapter> switches all artifacts together. chapter.json holds versions, normalized provenance, metrics, structural warnings and file hashes. Current results do not depend on runtime retention. Older successful generations are preserved.

Ordinary publication/SQLite failures restore the prior pointer and accepted result while rolling back Task completion. Filesystem and SQLite are not one crash transaction: a process killed after pointer replacement and before commit can leave a complete generation pointing to a pending Atom Task. Status reports pending apply; resubmit the same Atom task to validate and complete. No automatic broad cleanup/resume or version UI is provided. Unpublished directories may remain for diagnosis. Deleting runtime before pending work completes prevents continuation.

## Bounded Atom input

The Atom task reads all Chapter Claims and metadata, no default Chapter body. It has a fixed 120,000-character / 40,000-estimated-token Context cap; candidate Claims JSONL is bounded at 2 MiB before loading. Exceeding either returns CHAPTER_CONTEXT_TOO_LARGE. Accepted Claims and the previous published chapter remain intact; no Claim is dropped, no automatic larger budget or recursive reduce is attempted. A future hierarchical workflow can consume the saved Claims but is not implemented in Phase 4.

Knowledge results, candidates, runtime and source books stay in ignored Library storage. Only code, generated schemas, workflows/prompts, original fixtures and tests belong in Git. Core Ideas, Mental Models, Meta Principles, Book Memory, cross-chapter synthesis, full-book deduplication, Citation Verify, Fidelity/Quality Gate, HTML, RAG and MinerU remain absent.
