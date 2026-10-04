# ADR-012: Concept Registry is distinct from Atom merge

Status: accepted for Phase 5.

Concepts use `concept_<slug>` identifiers, a canonical name, optional Chinese name, aliases, source terms and generation-bound source references. Normalize receives current Atom terms, their Claim terms and the existing Registry. Case-folded NFC aliases must not name two concepts. Existing IDs, canonical names and aliases are retained; unsupported old concepts require explicit review, never silent disappearance. Human verified/locked fields remain false and have no editing interface.

Only `same_as` and `related_to` are Concept relations. Uncertain equivalence stays related. Sharing a Concept does not make two propositions equivalent. Cross-chapter decisions independently record merge_atoms, related_to, potential_tension, contradicts, supports or depends_on, supporting source references, short public reasons and confidence. Source Chapter Atoms remain intact. Generation-local Core Ideas can cite multiple original Atoms without asserting those Atoms are identical.

Confidence is an AI judgment, not measured truth. Alias semantics and merge validity receive real Codex sample review, while Python checks structure, identities and references. Formal semantic verification belongs to Phase 6.
