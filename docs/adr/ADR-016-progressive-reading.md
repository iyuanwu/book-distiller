# ADR-016 — Deterministic progressive reading

Status: accepted for Phase 7.

Canonical Knowledge is the fact source; Verification is the evaluation source. ReaderViewModel is a disposable projection. Renderer never calls AI or repairs knowledge. It pins Book and Verification generations, checks current dependencies and resolves citations against the canonical normalized generation.

L0–L3 select existing fields in Python with deterministic order: supported first, evidence strength, importance, hierarchy and ID. L0 prefers a Meta Principle, then Core Idea; oversized statement uses an exact complete first sentence when it fits, otherwise its exact canonical title. This is intentionally a selected viewpoint, not a newly generated comprehensive synopsis. L1 contains only high-level knowledge, classification and quality. L2 adds bounded concept and chapter contributions from selected objects. L3 includes at most 16 Atoms / 24 Claims and approximately 60% of small lower-level inventories. L4 retains every object regardless of verdict; L5 retains assessment and source locations. Short books stay short.

L0–L2 exclude partial, unsupported, contradicted and unresolved major issues; synthesis_overreach also excludes the object. L3 admits partial with explicit badges. Unsupported/contradicted are retained only in full inspection views. Concept links and L2 chapter contributions cannot reintroduce excluded high-level objects.

HTML and Markdown consume the same selection text and reference lists. Cards and a shallow knowledge tree reference existing objects. No new summary, concept, claim, relation or confidence score is created. Original AI confidence and reviewer confidence remain separately labeled; Quality PASS does not establish external truth.

Known limits: L0 necessarily omits nuance; L2 and L3 intentionally share high-level objects. Display budgets are character budgets, not promises of reading time. All export data is derived and local; no Human Override, Book Ask or Phase 8 recovery framework is introduced.
