# ADR-021: Effective Knowledge and evidence-constrained Book Ask

Status: Accepted for Phase 9A

## Decision

Ask reuses the AI Task Protocol and adds `ask_book`, BookAnswer 1.0 and Context Package 1.4. Older Context contracts remain unchanged. SQLite schema 2 stores generic Ask Tasks; no migration or second execution protocol is required.

Current knowledge comes exclusively from EffectiveKnowledgeResolver. The current Verification must match Book, chapter, normalized source, classification, human semantic revisions and verification resources. An explicit stale pipeline ledger also blocks Ask. PASS allows Ask; NEEDS_REVIEW carries global and relevant issue warnings; FAILED and stale refuse. No quality bypass is offered.

AskRetriever operates on structured metadata: NFKC lowercase terms, exact titles/names/aliases, Chinese bigrams, Claim terms and chapter selectors. Ranking is deterministic with stable IDs and independent AI evidence quality. Complete lower-reference closure expands selected Concepts, Ideas, Models and Principles to Claims. Unsupported and contradicted objects are excluded from normal support, but may be inspected by explicit problem questions. No embedding, vector database, external search or GraphRAG is added.

Context contains only bounded selected Effective values, human flags, assessments, related issues, relationships, current Notes and Source citations. The default cap is 40,000 characters / 13,333 estimated tokens, up to 48 selected objects and 128 examined roots. Full object chains either fit or are omitted; omission counts are visible. Citation ranges over 1,800 characters are omitted rather than silently substituted with an uncited shorter range. SourceIndex retains offsets/hierarchy, streaming one block at a time and loading selected evidence on demand. No complete Source text is retained in memory. Book Memory is orientation, never citation.

Human Guidance uses existing Global → Type → Book scope precedence; system invariants remain highest. Applicable rule changes invalidate a prepared Ask even though older workflows retain their established frozen-rule behavior. Human verified and locked remain independent of AI support. Natural-language rule conflicts are not solved by a general rule interpreter.

BookAnswer distinguishes Source, AI synthesis/application and User segments, each with provenance refs. Answer text must exactly project its segments. Source needs Knowledge and Citation refs; User requires Note refs and cannot use citations to impersonate Source. AI application is not an author statement. High confidence means complete strong cited support without relevant major issues, not model certainty. Insufficient evidence and out-of-scope are valid outputs. Semantic fidelity remains Codex's explicit bounded-context judgment; schema validation is not a semantic proof.

## Identity and publication

The Context binds question hash, Book and Verification generations, human display state, classification identity, applicable rule hash, retrieval/schema/workflow/prompt versions and existing normalized-source identity. Existing prepare/submit also pins workflow, prompt and output schema bytes. Submit rebuilds the bounded Context and resolves citations against current SourceSpan/range hashes, then validates the closed-world refs and result before any answer write. A mismatch requires a new Task; schema errors allow same-task retry.

Answers are private derived artifacts under `library/<book>/ask/answers/<task-id>.json`; they are never Canonical Knowledge. Existing durable apply receipt and SQLite transaction mark the validated answer complete. A caught publication failure restores the prior file; a process kill before database commit can leave a pending file, which is not treated as completed and is recoverable by submitting its durable result. History append happens after commit and is idempotent per answer ID. A history failure does not undo the complete answer; `ask show` repairs the missing index from the answer and validates its publication receipt without Codex. A history record alone is never evidence of completion. Historical answers remain input-bound snapshots.

## Scope and consequences

Ask does not modify Knowledge, Verification, Quality Gate, Human state or source. It has no HTML chat UI and no unbounded conversation memory. Follow-ups must be expanded into complete questions before prepare. Formal Library remains untouched by isolated tests and smoke.

Lexical recall and multilingual alias coverage are limited. Large high-fanout objects may be omitted when complete chains cannot fit. Cross-chapter and application synthesis still require careful model judgments. NEEDS_REVIEW warnings qualify available evidence, not a promise of correctness. Answer history currently grows without compaction. No Bundle, Backup, Restore, cross-book Ask, MinerU or Phase 9B is included.
