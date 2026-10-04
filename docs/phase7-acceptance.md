# Phase 7 acceptance — 2026-10-04

## Result

Implementation and automated checks pass. **Overall acceptance remains FAIL / incomplete pending real browser visual and interaction verification.** The installed browser tool rejected file:// and explicitly prohibited alternate browser surfaces or indirect workarounds. No browser screenshot, real Back/Forward interaction, or responsive visual result is claimed. A local manual verification request was presented to the user. No Phase 8 work was started.

Phase 6 was frozen before implementation as local commit `48056e1` (`feat: add evidence verification and quality gate`), after 254 ordinary tests plus 5 real Docling cases passed. No push. Phase 7 remains uncommitted for review.

## Architecture and outputs

One ReaderViewModel consumes current, dependency-checked Book and Verification generations. It never prepares AI tasks or writes canonical knowledge. Jinja autoescape, textContent, local CSS and classic JS data bundle implement a three-column offline reader without fetch, a web server, fonts, frameworks or external assets.

`output -> .reader-generations/<uuid>` contains index.html, assets/app.js, assets/reader.css, assets/book-data.js, nine derived JSON files, six Markdown files and render_manifest.json. Metadata binds Book/Edition, Book/Verification generations, reader version, original SHA256, renderer/input hashes and output hashes. Cache validation requires the complete output inventory; a rewritten hash inventory cannot make missing index.html count as a completed reader.

Markdown files: L0.md, L1.md, L2.md, L3.md, knowledge-model.md, quality-report.md. HTML and Markdown use the same Python selections. Cards and mind map reuse existing high-level objects.

## Real fixture exports

The existing Phase 6 original fixture is Reliable Local Data Tools, registered with provisional title/slug `book-synthesis`. No new AI inference was performed.

| Metric | Normal | Error-injected fixture |
| --- | ---: | ---: |
| Quality gate | pass | needs_review |
| Chapters | 3 | 3 |
| Claims | 19 | 17 |
| Atoms | 11 | 10 |
| Concepts | 14 | 13 |
| Core Ideas / Models / Principles | 5 / 1 / 1 | 4 / 1 / 1 |
| L0 chars | 228 | 228 |
| L1 chars | 1762 | 1489 |
| L2 chars | 3150 | 2749 |
| L3 chars | 5942 | 5577 |
| Citation entries | 14 | 14 |
| Embedded evidence chars including context | 6503 | 6503 |
| Search entries | 51 | 46 |
| Mind-map nodes | 21 | 19 |
| Cards | 7 | 6 |
| HTML bytes | 2017 | 2035 |
| Total output bytes including manifest | 491232 | 464087 |

Output sizes reflect this implementation's final asset files; generation IDs/timestamps are excluded from deterministic input identity. All fixture output is in isolated temporary homes, outside formal Library and Git. Local root pointers are `/tmp/book-distiller-phase5-root` and `/tmp/phase6-errors-root`; these temporary artifacts may be removed by the OS later.

All object, concept, chapter, citation, search-anchor and mind-map references were checked against generated targets. Exact chain resolved:

`principle_001 → model_001 → idea_001 → atom_ch_0001_001 → claim_ch_0001_0001_001 → assessment_9860d026c49d9ecfce023493 → cit_640423182ec5886224d2afe1481042d4 → blk_000002 → #/texts/1`

The Citation char range, text hash and SourceSpan were revalidated from canonical blocks. Markdown source has null physical pages, not page 0. PDF source locations retain physical page numbering. Missing no-copy originals leave Open Original unavailable while preserving canonical evidence viewing; changed existing originals still refuse rendering.

## Selection and interaction design

L0 selects an existing supported highest-level statement, uses an exact complete first sentence if needed, then a title-only fallback. L1 selects bounded high-level knowledge; L2 adds mechanisms, limitations, concepts and chapter contributions; L3 adds selected Atoms/Claims. Strong evidence precedes importance. Partial enters L3; unsupported/contradicted stay visible in L4/L5 but are excluded from quick levels. Open major issues and synthesis overreach do not re-enter L2 via chapter links.

L4 retains all objects, concepts and relationships with 40-item pages. Search is normalized lowercase/NFKC and supports Chinese, English, aliases and Claim statements. L5 paginates citations, starts source previews at 400 chars, caps each at 800, and collapses local context. Right-panel citations show three at a time. Citation locations remain present when an evidence budget is exhausted. Original confidence and reviewer confidence are separate, with supplemental recheck citations labeled.

The HTML parser confirmed local assets exist and unique shell IDs; JS passed `node --check`. Export tests verify escaping, shared Markdown text and no fetch/innerHTML/external assets. **These checks are not substitutes for observed browser behavior or visual inspection.**

## Tests and performance

- Ordinary suite: **282 passed, 5 skipped** (real Docling cases opt-in).
- Full offline suite: **287 passed, 0 failed, 0 skipped**; Docling **2.132.0**, PDF / MD / Markdown / DOCX / EPUB (5 cases). Two existing upstream deprecation warnings.
- Phase 7: **28 tests** (12 unit + 16 integration), covering selection, budgets, Unicode search, HTML/data escaping, no AI tasks, missing references, failed/no-verification/stale refusal, source absence/change, actual new Verification generation staleness, idempotency, force and injected publication failures before/after pointer switch.
- Large synthetic export: **1000 Atoms + 5000 Claims**, 6000 search entries and complete L4 reference inventory; approximately **8.10 MB**, **32.74 MB peak Python allocations**, **0.64 s** measured with tracemalloc. These synthetic values are not a browser startup benchmark. Full DOM rendering/search interaction was not run in a browser.
- `./book doctor` all OK; CLI repeated render returned Already rendered; force created a new complete generation; both real fixture Reader statuses are completed.

## Privacy and boundaries

Formal Library contains only .gitkeep. Formal DB books/editions/tasks/runs counts remain 0. All captured normal canonical Knowledge hashes are unchanged. Git tracks no generated Reader, evidence excerpts, private books, SQLite or model cache. No extra dependency was added. Skill validation and git diff --check pass.

No Book Ask, RAG, embeddings, MinerU, external research, Human Override, server, cloud sync, accounts, cross-book synthesis or Phase 8 feature was implemented.

## Remaining acceptance and limitations

Real L0–L5 navigation, Back/Forward, search, evidence interaction, responsive layout and visual readability still need browser/manual confirmation due to the tool security restriction. No claim of Chrome/Safari portability or screenshot approval is made.

L0 deliberately simplifies to an exact selected sentence; full qualifications remain in its object. L1 is short for this small fixture; no artificial content fills a reading-time quota. L2/L3 intentionally overlap high-level knowledge. L4 uses paging to manage volume; L5 limits previews and context with explicit omission markers. The large synthetic export stays below configured guards, but diverse real-book browser performance remains unmeasured. An open static page is a generation snapshot; it cannot detect later canonical staleness by itself—use CLI status.

**Ready for Phase 8: NO — browser acceptance is outstanding.**

## Browser follow-up — route/title repair

The user subsequently confirmed real Mac file:// loading of HTML/CSS/JS, the three-column layout, PASS banner and right quality data. The observed blocker was the unavailable center view. Root cause: a source-location formatter named `location` shadowed the browser Location object; reading `location.hash.slice(...)` threw and fell into the invalid-route branch. Search hash writes were also shadowed.

The formatter is now `sourceLocation`; browser hash reads/writes explicitly use `window.location`. Empty and invalid/expired/malformed targets resolve to the existing `l0` route. Only those entries use history.replaceState; valid targets remain intact, and hashchange does not add history entries. Invalid targets show a lightweight notice alongside readable L0 content.

Reader titles now prefer Edition display_title, then Book title, canonical title, finally slug; slug-valued provisional placeholders defer to a meaningful title. The original isolated fixture had only slug-valued titles. Its Edition display metadata and matching SQLite index were corrected to the user-specified `Reliable Local Data Tools`. No normalized, Knowledge or Verification artifact changed (71 captured hashes unchanged). The resolved display title participates in render cache invalidation.

A Node VM unit harness executes the actual app.js with deterministic DOM/History doubles, including first initialization. It reproduces the old script's failure and verifies empty/valid/invalid/malformed hashes, all generated left-navigation targets and corresponding views, back/forward history, and search. The regenerated real fixture's script/data passed the same seven scenarios and all 18 sidebar targets. This is script execution testing, not a browser layout engine or a claim of repaired-browser visual confirmation.

The fixture was regenerated with `render book-synthesis --force --no-open`; its Reader is completed, and repeat render returns Already rendered. The fixed output is ready for the user's real browser re-acceptance. The tool's file:// restriction still prevents agent-operated browser confirmation. Phase 7 remains uncommitted; no Phase 8 work.

Follow-up validation: ordinary pytest **294 passed / 5 skipped** (real Docling opt-in); Reader unit tests **23 passed**. This repair adds **12 test cases**: 7 actual-script routing/history/search cases, 4 title-priority cases, and 1 display-title cache/publication integration case. No additional Docling conversion was needed for this route/display-only repair. `node --check` and `git diff --check` pass.
