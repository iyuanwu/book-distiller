# ADR-014: Local evidence verification

Status: accepted for Phase 6.

## Decision

Knowledge Model is the original distillation; Verification is a separate evaluation of that immutable generation. Verification never silently changes a Claim, Atom, Concept Registry or Book Memory. Unsupported knowledge requires review or a Phase 4/5 rerun.

SourceSpan is parser provenance; Citation is a reusable evidence reference. Citation 1.0 uses `cit_` plus the first 32 SHA-256 hex characters of canonical JSON containing edition, normalized parse generation, Block and half-open character range. It stores parser locations, SourceSpans and a text hash, not source prose. PDF indices are zero-based and physical page numbers one-based; EPUB/Markdown/TXT without physical pages keep null. Dynamic `evidence_path(directory, object_id)` resolves pinned generation paths to excerpts and context, not current Chapter links.

Evidence strength (strong/moderate/weak/insufficient) and fidelity verdict (supported/partially_supported/unsupported/contradicted) are independent fields with consistency validation. Unsupported/contradicted requires insufficient; partial support cannot be strong. Reviewer confidence is subjective, distinct from the original object's confidence. Only short public reasons are persisted, never private reasoning.

Claims, Atoms, Ideas, Models and Principles each receive their own semantic task in that order. Lower assessments are inputs, not inherited scores. Mechanisms, use cases, limitations and cross-chapter generalization must be checked against source. Python checks identities, reference closure, hashes, contracts and budgets; the current Codex judges semantic support. No model API, external facts, web search or embeddings are used.

## Context and recheck

The compatible protocol adds Context Package 1.3; 1.0/1.1/1.2 remain unchanged. SourceIndex streams JSONL and retains byte offsets/hierarchy, not all source text. Default local budgets are 48,000 context characters, 16,000 source characters and 12 lower objects. Claims get original evidence plus one neighboring Block on either side within the same Section/Chapter. An indivisible required Block exceeding budget fails explicitly. Bounded higher-level selection records available/reviewed counts and omitted Blocks; incomplete selection cannot receive strong evidence and forces needs_review.

A Claim with weak/insufficient evidence and context_insufficient may receive **one** automatic repair_evidence task, expanding to three neighbors within the same Section/Chapter. Only this task may select new supplemental citations. Original citations are retained separately. Repair never changes the statement, scans an entire book to find agreement, or performs an automatic knowledge rerun. A remaining unsupported result is valid.

## Generation and publication

A verification generation pins normalized document/hash, Chapter generations/manifests, Book generation/manifest, classification and workflow/prompt/schema/rule hashes. Original source bytes are checked too. Runtime snapshots and accepted assessment checkpoints permit resume after later failure. A changed dependency is stale; invalid source/canonical references are failed.

All five assessment layers, bounded coverage batches, candidate reviews and deterministic metrics complete before one pointer publishes the immutable verification generation. Artifact inventory and hashes are checked on reads. Metadata-save failure after pointer change restores the previous pointer. Completed-task replay uses shared Task Protocol hashes; rejected output retains the pending task. Historical evidence paths use pinned normalized and Chapter paths. Process death after a complete pointer switch may leave runtime awaiting reconciliation, but cannot expose a partial generation.

## Limits

Metadata and object summaries still scale with object count; each task currently rebuilds the offset index and checks dependency hashes. This trades speed for straightforward integrity checks. High-level selection is bounded and explicitly marks incomplete review; it does not establish exhaustive semantic support for long objects. Source verification establishes what this source supports, not whether the source is true in the external world.
