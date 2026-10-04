# ADR-017 — Offline static reader and atomic output publication

Status: accepted for Phase 7.

Jinja2 creates a small escaped HTML shell, local CSS and classic Vanilla JS. Python emits JSON exports plus `assets/book-data.js`. Classic scripts avoid file:// JSON fetch restrictions; no server, package manager, frontend framework, external font or network dependency is needed. DOM content uses textContent; data escapes script delimiters and Unicode line separators. CSP disallows network connections and inline scripts. Hash navigation handles objects, chapters, concepts, citations, search and levels with browser history.

Output is `output -> .reader-generations/<uuid>`. Build the complete generation first, hash its artifacts, recheck all inputs, then atomically switch one symlink. Failures before or after pointer replacement restore the prior pointer and remove only the incomplete new generation. Generation/version/template/source availability changes invalidate cache; same intact input returns Already rendered. Generated timestamps do not change the input fingerprint. Manifest records book/edition IDs, both generations, reader version, source SHA256, input hashes, metrics and output hashes. CLI status reports completed / stale / unavailable. An already-open offline page is a historical snapshot; it cannot detect later canonical changes without re-rendering/status.

Citation text is resolved once per citation from canonical Block char ranges. Each excerpt is at most 800 chars, surrounding blocks at most 160 each, and all embedded evidence is bounded by one shared budget. Primary excerpts precede optional context. Missing budget text stays explicitly flagged, with its location and SourceSpan intact. Knowledge objects are never removed to meet the evidence budget. The full original is not embedded. Missing reference-mode originals are permitted only for viewing existing validated canonical evidence; normal verification retains its stricter source requirement.

L4/group/search views paginate, avoiding thousands of simultaneous DOM cards. 20 MB output warns; 64 MB refuses publication. Size includes both JSON and JS exports and Markdown. Mac opening uses subprocess arguments, no shell, and failure is a warning after successful publication. Browser policy may block file:// automation or original-file links; no server or security-policy bypass is provided. File path and PDF physical page remain visible.

All generated artifacts remain in the ignored private Library or isolated test homes. Original fixture exports and screenshots must not be committed.
