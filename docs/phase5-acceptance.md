# Phase 5 acceptance — 2026-10-04

Phase 4 baseline is local commit `385ee8d`. Phase 5 remains uncommitted for user review. No push and no Phase 6 implementation.

## Real Codex smoke

The current Codex read the task workflow, prompt, schema and selected Context, authored the judgments, and submitted through the shared CLI protocol. Python helper scripts only serialized those judgments and copied request bindings; no external model API or test-double responses were used for this smoke. The subsequent Registry continuity rerun retained the reviewed judgments after checking unchanged semantic inputs and all workflow/prompt/schema snapshots. Automated stress/protocol tests use explicit test doubles and are separate evidence.

Source: original `tests/fixtures/book-synthesis.md`, real Docling 2.132.0 Markdown parsing. Three real, non-synthetic Chapters. The first fixture attempt used title labels rather than headings and was corrected to Markdown section headings before the final smoke. All data lives in an isolated temporary BOOK_DISTILLER_HOME; formal Library/database remain empty.

Final results: 19 Claims, 11 Atoms, 14 Concepts, 5 Core Ideas, 1 Mental Model, 1 Meta Principle. Four of five Ideas cross Chapters. Ten Atoms support Ideas; the remaining Atom is the sample blue log heading. It is not promoted. The largest real synthesis Context is 14,312 characters. Two complete Book generations were published; there are no partial stage directories. Repeating analyze book returns the same published generation.

## Source and promotion review

All 14 Concepts were inspected: staging area/暂存区, validation/验证, atomic replacement/原子替换, last known good result/上一份可用结果, publication boundary, prepare–validate–publish framework/准备—验证—发布框架, checksum/校验和, generation identity/代标识, staleness/过期状态, Idempotency/幂等性/safe repeat submission, retry, checkpoint/检查点, failure injection/故障注入, and test isolation/测试隔离. Canonical IDs, names and aliases survived a Registry rerun. Checksum vs generation identity, retry vs idempotency, and checkpoint vs isolation remain distinct related concepts.

The five reviewed Ideas concern complete validated publication, content plus generation freshness, testing actual guarantee boundaries, distinct roles of checkpoint and isolation, and repeated-operation contracts. The first four have multi-Chapter support; the last combines two retry propositions within Chapter 2. They integrate complementary constraints, not simply Chapter titles.

The source's named prepare–validate–publish procedure became the one Model. Its mechanism remains preparation, contract checks and a pointer switch; applicability and limitations are source-derived. The sole Principle joins explicit/recoverable transitions with current prerequisites and boundary experiments. It cites three Ideas and the Model across all three Chapters, and retains the hardware-loss limitation. The decorative heading is excluded from higher objects.

Four Atom decisions are related_to, not merge_atoms: the shared discipline has material file/collection, definition/application or guarantee/experiment differences. This is deliberate restraint, not missing dedup support. Merge and tension/contradiction record preservation are separately tested with protocol fixtures. No genuine contradiction was manufactured in this source.

This is Codex sample review, not independent human approval, Citation Verify or a Fidelity claim. The user still reviews Phase 5 acceptance.

## Generation-bound evidence

Current Book generation: `993ed1ca-57bf-4adc-bb0c-1c5497a85974`.

Example actually resolved:

`principle_001 → model_001 → idea_001 → atom_ch_0001_001 → claim_ch_0001_0001_001 → blk_000002`

The Atom and Claim bind Chapter generation `53255cf0-f5ce-4192-bf87-00596248e549`. The immutable Chapter path comes from the Book dependency inventory, never the current Chapter link. SourceSpan: Docling `#/texts/1`, characters `[0,385)`, no physical or printed page for Markdown. All 19 evidence edges reached by the Ideas resolve to real Blocks with SourceSpans.

## Reduce and memory

The full shared-protocol 1,200-Atom stress completed 97 Reduce batches and four final synthesis stages. Maximum complete Context: 17,200 characters; measured Python tracemalloc peak: 14,601,606 bytes (about 13.9 MiB). This is Python allocation peak, not process RSS. Input references: 1,200; expanded final references: 1,200; missing: 0; duplicates: 0. Intermediate input assignment is exactly once; distinct final objects may legitimately share support.

The stress uses synthetic repetitive Atoms to test batching, protocol and provenance, not semantic preservation. Summaries can lose qualifications; reference preservation does not prove semantic fidelity. A single oversized item, accumulated vocabulary or Registry that cannot fit a bounded Context produces an explicit limitation rather than silent omission. General long-book semantic quality remains unverified.

A separate long-Chapter test makes 250 Claims exceed the ordinary 120,000-character Context. Bounded Reduce publishes an Atom referencing all 250 originals and preserves the exact claims.jsonl hash. The normal Phase 4 pipeline remains available.

Book Memory 1.0 is 9,353 / 12,000 canonical JSON characters. SHA256: `7c632c963265fca0ba8a1563951aed35a7ea7a30627afddfe9f37eacefd26e30`. It includes identity and dependency hash, outline/status, classification, prioritized high-level objects, Concept summaries, bounded generation-bound source references, conflict summaries and omission counts. Python controls ordering/cropping, not Codex.

## Recovery and tests

Tests cover schema/reference rejection and corrected retry, UUID type stability on resume, existing alias conflicts, Concept/Atom relation separation, unsupported promotions and zero Principles, Chapter/classification/normalized staleness, immutable old-generation resolution after same-ID Chapter replacement, stage failure retention, pointer-switch failure, and metadata failure after a completed pointer switch. The latter restores the old pointer; a retry succeeds. Book publication has no separate SQLite current-generation flag that could compete with its canonical pointer.

Final validation: ordinary pytest 202 passed / 0 failed / 5 intentionally skipped; full offline suite 207 passed / 0 failed / 0 skipped; Phase 5 focused suite 25 passed / 0 failed / 0 skipped. Doctor: all checks OK. The real suite reports two upstream Docling deprecation warnings. Offline Docling uses `HF_HUB_OFFLINE=1 HF_HUB_DISABLE_XET=1`; ordinary pytest intentionally skips its five opt-in cases.

## Scope and privacy

Only code, schemas, workflows/prompts, original fixture, tests and documentation belong in Git. Actual Book generations, Memory, task contexts, sources and SQLite remain in ignored or isolated storage. The only tracked PDF is the existing original test fixture.

No Citation Verify, Fidelity Review, Quality Gate, L0–L5, HTML, Book Ask, RAG, embeddings, vector DB, GraphRAG, MinerU, external research or Human Override was added. Optional book-specific schema extension proposals were not implemented. Historical generation cleanup and history UX remain deferred.
