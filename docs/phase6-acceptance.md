# Phase 6 acceptance record

Date: 2026-10-04. Phase 5 baseline: local commit `00b434a`, 207 complete offline tests. Phase 6 changes remain uncommitted for user review; nothing pushed.

## Real Codex smoke, separate from pytest

The current Codex evaluated the original three-Chapter `tests/fixtures/book-synthesis.md` through local Task Contexts, wrote public assessment JSON and submitted it through the shared CLI. Python serialized authored judgments, checked protocols and computed metrics; it did not substitute semantic test doubles or call a model API. No private book was used.

Normal model: 19 Claims, 11 Atoms, five Ideas, one Model, one Principle; 37 independent assessments plus three coverage batches and two candidate-review batches (42 tasks). Verdicts: supported 37, partial 0, unsupported 0, contradicted 0. High-level objects use moderate strength for source integration. Gate pass; major Fidelity 35/35; 14/14 catalog Citations resolve. All 37 object paths were programmatically resolved, including Principle → Model → Idea → Atom → Claim → Assessment → Citation → Block → SourceSpan.

The error fixture was an isolated copied library, explicitly edited only to create semantic validation cases. Its original normalized source remained unchanged; normal canonical knowledge hashes were checked unchanged. The fixture removed the isolation Claims/Atom and dependent Idea/Concept references while preserving the complete important source paragraph. It also planted one unsupported Claim and one overclaimed Atom. This construction is test input preparation, not verifier rewriting.

- Correct source statement: supported / strong.
- Correct statement with only a Chapter-heading original citation: initial insufficient/context_insufficient; exactly one local recheck selected the body paragraph as supplemental, yielding supported / strong. Original Claim text/evidence remained unchanged.
- AES-256 assertion: original confidence .99; unsupported / insufficient; contributes one high-confidence hallucination. No search until agreement.
- Hardware-recovery guarantee added to an Atom: partially_supported / weak, even though all its lower Claims are supported. OVERCLAIM and SYNTHESIS_OVERREACH recorded.
- Coverage: normal three reviewed Chapters have zero detected omissions; the isolated copy has one major omission in Chapter 3, source `blk_000012`, about test isolation and its distinction from checkpoints. No new Claim was created. No minor omission was reported. No false-positive major omission was observed in this small normal fixture; this is not an accuracy/omission-rate benchmark.
- Error fixture: 33 objects, one recheck, three coverage and two candidate reviews (39 tasks); supported 31 / partial 1 / unsupported 1 / contradicted 0. Gate needs_review despite Fidelity 29.5/31. It has one unsupported major object, one high-confidence hallucination, one omission candidate and seven open issues. Two OVERCLAIM issues reflect object verification and candidate review; issues are findings, not unique bad-object counts. Identical repeated batch issue IDs are merged.

## Deterministic verification

Protocol tests cover stable Citation ranges/generations/pages/text hashes; all 11 issue types and serialization; independent target binding for all five object levels; same-task retry; supplemental citations and one recheck; missing Blocks; source/report/pointer corruption; stale normalized/Chapter/Book/classification; old same-ID evidence paths resolving pinned generations; metadata failure after publication restoring the previous pointer; coverage validation; exact-duplicate exclusive threshold; transparent metrics and report schemas.

The synthetic scale test builds 1,000 Claims + 500 Atoms + one Idea. It checkpoints five assessments, recreates the service and confirms the same pending Task and prior successful assessments. Measured planner Python peak allocation: 8,036,524 bytes. A separate 10,000-Block, 23,358,894-byte source index test measured 3,206,098 bytes peak Python allocation, with bounded Chapter/Section windows and batches. These are tracemalloc measurements, not total process RSS or a full long-book semantic benchmark.

`quality_report.md` renders the canonical gate, all formulas/counts, thresholds, four verdict counts, issues, rechecks and remaining items. Quality PASS means local source support under current rules, not external truth. Private runtime, source, canonical knowledge, citations and verification results stay outside Git.

## Limits

This smoke is deliberately constructed and not blinded. It establishes behavior on known positive and negative cases, not verifier precision/recall. High-level abstraction remains more subjective. Local coverage relevance can miss paraphrases referenced elsewhere; bounded lower-object sampling is explicitly incomplete and prevents pass. Candidate review cannot discover every semantic duplicate outside the supplied Phase 5 candidates. Recheck is locally bounded, but confirmation bias remains possible. Initial gate thresholds have not been calibrated against a human gold set. Repeated source/index integrity scans favor correctness over long-book throughput.

No L0–L5, HTML, card/mindmap renderer, Book Ask, RAG, embeddings, vector DB, GraphRAG, MinerU, external research or Phase 7 implementation was added.

## Final validation

- Ordinary `.venv/bin/python -m pytest -q`: **254 passed, 0 failed, 5 skipped**. The five skips are opt-in real Docling cases.
- `HF_HUB_OFFLINE=1 HF_HUB_DISABLE_XET=1 .venv/bin/python -m pytest --run-docling-real -q`: **259 passed, 0 failed, 0 skipped**, including PDF, `.md`, `.markdown`, DOCX and EPUB. Docling 2.132.0; two upstream deprecation warnings.
- Phase 6 unit/protocol suite: **52 passed, 0 failed, 0 skipped**. This is a subset of the totals above, not additional tests.
- Skill static validator: valid. CLI pending/resume/force/status tested; in-progress generations are distinguished from the previous complete quality report.
- Real Codex tasks are not counted as pytest tests. Maximum normal/error smoke Context sizes: 39,052 / 37,453 characters; maximum source text: 3,151 / 2,938 characters, within configured budgets.
- Formal Library empty; books/editions/runs/tasks each have zero rows. Git privacy checks pass; Phase 6 uncommitted and unpushed.
