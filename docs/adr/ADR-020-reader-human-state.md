# ADR-020: Reader Human layer and live stale projection

Status: Accepted for Phase 8 implementation

Preserve Phase 7 layout, routes and deterministic exports. Object cards display Human modified, Human verified, Locked and separately labeled User Notes. AI Verification remains independent.

Reader input hashes include Human display state. Semantic edits invalidate downstream stages and refuse a new normal export until verification is current. Metadata-only changes require only rerender.

Immutable HTML is a generation-time snapshot. A small adjacent `reader-status.js` is a replaceable local status projection outside export generations; Human actions and scoped reruns update it, and successful render clears it. Reopening the managed `output/index.html` loads it under the existing file-compatible CSP and replaces the top gate label with `STALE / NEEDS RE-VERIFICATION` or `READER DISPLAY STALE`. No network, server, fetch permission or Phase 9 feature is introduced.

An already-open static page does not poll disk; reload/reopen is required. Copied exports or direct historical generation paths may not have the adjacent status script. Their gate is explicitly labeled SNAPSHOT and does not claim to validate the current Library. CLI status and canonical dependency checks remain authoritative. Corrupt/missing current dependencies fail closed at render time.
