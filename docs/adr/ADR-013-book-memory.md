# ADR-013: Derived, bounded Book Memory

Status: accepted for Phase 5.

Book Memory 1.0 is derived context infrastructure, not an extra Knowledge Object level. Python rebuilds it for each completed Book generation. It contains identity/generation bindings, classification, chapter outline/status, prioritized Principles, Models, Ideas, Concepts, unresolved conflicts and important source references. It never embeds all Blocks, Claims or Atoms.

The default bound is 12,000 canonical JSON characters. Python fixes field selection, importance/ID ordering, short text/reference excerpts and omission counts. Higher objects receive priority. Identity and generation bindings cannot be dropped; if these alone exceed the budget, publication reports BOOK_MEMORY_BUDGET_TOO_SMALL and preserves the old Book. The hash excludes only its own field; selected_chars includes the fixed-width hash. This is deterministic for the same objects and identity, not a promise of identical AI reruns.

Context 1.2 builder supports optional `include_book_memory=True`, checks the supplied memory hash and includes it in input/context hashes and budget accounting. It is not forced into classification or every synthesis stage. Consumers must also validate the memory's generation dependency against their selected Book. Memory does not upgrade stale knowledge into current truth. Its excerpt references resolve within the Book's immutable generation inventory.
