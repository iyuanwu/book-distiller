# ADR-015: Transparent local quality gate

Status: accepted for Phase 6.

## Decision

Quality evaluates the current generation without becoming another Knowledge Model. `rules/quality/standard.json` centralizes thresholds, major-object definition and verdict weights. Claims/Atoms with importance >= 0.80 are major; all Ideas/Models/Principles are major. A high-confidence hallucination has original confidence >= 0.80 and verdict unsupported or contradicted.

Every metric has formula, numerator, denominator and value. Counts use denominator null (not a fabricated ratio); ratios with no observations use null. Verdict counts and major verdict counts remain visible alongside Fidelity. Fidelity = (supported + 0.5 × partially_supported) / all major objects; unsupported and contradicted have zero weight. An incomplete assessment inventory is a failure, not an opportunity to omit difficult objects from the denominator.

| Metric | Formula |
| --- | --- |
| parse_completeness | mapped normalized Blocks / normalized source-relevant Blocks; structural proxy |
| chapter_knowledge_coverage | recognized Chapters with Claims and Atoms / recognized Chapters |
| claim_evidence_reference_coverage | Claims with resolvable original references / Claims |
| verified_claim_support_rate | supported Claims / Claims |
| core_idea_evidence_coverage | supported Ideas with citations / Ideas |
| citation_traceability_rate | resolved catalog Citations / catalog Citations |
| unsupported_major_objects | count of major unsupported/contradicted objects |
| high_confidence_hallucinations | count using original confidence and final verdict |
| exact_duplicate_rate | excess identical normalized Claim/Atom texts / all Claims + Atoms |
| major_omission_count | count of open omission findings |
| major_omission_chapter_rate | fully reviewed Chapters with omission / fully reviewed Chapters |
| review_issue_count | count of open issues |
| coverage_review_completion | fully reviewed source Chapters / recognized Chapters |

Parse completeness is not source-text accuracy or Chapter recognition accuracy. The omission ratio is a **Chapter incidence ratio**, never a gold-set key-idea omission rate. No runtime claim of true key-idea omission below 10% is made.

## Coverage and duplicates

Every recognized source Chapter is partitioned into batches of at most eight Blocks and the source-character budget. Each task sees source, outline, overlapping Analysis Chunks and bounded relevant Claims/Atoms. It must explicitly acknowledge every Block. Findings are combined, identical issue IDs deduplicated, and a Chapter counts complete only when all batches are complete. Missing important ideas produce MAJOR_OMISSION with source citations; no Claim is created. This local relevance selection can miss a paraphrase whose original references point elsewhere, so findings remain review items rather than automatic deletions.

Python detects exact duplicate Claim statements and Atom summaries after NFC, whitespace normalization and case folding. Semantic review uses existing Concept and relation candidates in bounded batches, not all N² comparisons. It can report DUPLICATE_KNOWLEDGE, CONCEPT_DISTORTION, OVERCLAIM and SYNTHESIS_OVERREACH without modifying the registry. It cannot guarantee discovery of every duplicate outside those candidates.

## Gate

- `failed`: broken canonical references, unavailable/changed source, corrupt verification or incomplete assessment inventory. Runtime failure journal retains a diagnostic; no partial quality generation is published.
- `needs_review`: content concerns or unmet thresholds. Minimum parse structural coverage .95, Chapter knowledge coverage .95, Idea evidence coverage .90, citation traceability .95; unsupported major objects and high-confidence hallucinations must both be zero; exact duplicates and omission Chapter incidence must each be < .10. Any major omission, open major/critical issue, incomplete review or parse warning also prevents pass.
- `pass`: completed evaluation and all configured gates satisfied. Empty knowledge cannot pass. Legitimately absent Ideas/Models/Principles have no synthetic ratio; supported Claims/Atoms still require complete review.

A high aggregate Fidelity never overrides a hallucination or a major omission. Status describes local evidence support, not external truth. These initial thresholds are engineering defaults, not empirically calibrated accuracy guarantees; benchmark calibration remains later work.
