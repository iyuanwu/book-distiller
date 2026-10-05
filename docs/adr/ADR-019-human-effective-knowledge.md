# ADR-019: Append-only Human layer and effective knowledge

Status: Accepted for Phase 8 implementation

## Decision

Keep generated artifacts immutable. Human actions append to `human/overrides.jsonl`; notes and book rules use separate JSONL journals. `state.json` is a rebuildable projection. A torn final journal frame is preserved separately before the next append; malformed committed frames fail closed.

`TargetRef` binds object type, generation UUID, object ID and optional chapter. Actions are edit/verify/lock/unlock with a hash of the effective object observed by the caller. Core rejects noncurrent generations and stale hashes (`STALE_HUMAN_EDIT`). Field whitelists and the original Pydantic models validate edits. Identities, generation/source hashes, evidence bindings and lower object references are not editable through this API. Reference changes require a new validated workflow generation.

`EffectiveKnowledgeResolver` combines immutable base values with active generation-bound patches and independent human flags. Historical reads explicitly return the base generation. Reader, Verification snapshots, Book inputs, effective classification and atom-only Claims forks use this projection. Human semantic revisions participate in dependency hashes; the durable revision remains stable once a rerun consumes it. Rebase copies do not increment the semantic revision. Metadata-only verify/lock/unlock and notes only change Reader display state. Human verified never changes AI evidence assessments or Quality Gate.

Semantic edits invalidate: Claim -> target Atoms and Book/Verification/Reader; Atom/Concept/Core Idea/Mental Model -> Book/Verification/Reader; Meta Principle -> Verification/Reader; Classification -> chapter knowledge and all shared downstream. The existing Book synthesis is the smallest publication unit for high-level rebuilding; Phase 8 does not split it into separately published Idea/Model/Principle generations.

## Locked content

Core records an exact dependency closure: lower-object semantic identities and original normalized Block hashes. It carries locked values deterministically and assigns new generation-bound refs. Lower references must still exist and match; recursively changed Claims or source Blocks trigger `LOCK_DEPENDENCY_CONFLICT` and Run `needs_review`. Candidate dependencies are also checked, so matching ordinal IDs are insufficient. No embedding, semantic search, similar-text selection, or automatic unlock is used.

Protected Concepts/Ideas/Models enter the next synthesis Context before higher-level Tasks run. Publication appends `HUMAN_OVERRIDE_REBASED`; original edit/verify/lock events survive. Unlock appends a new event and allows subsequent AI regeneration. AI is instructed not to generate equivalent replacements; Core protects exact identities and values, not an inferred semantic-equivalence relation between arbitrary new sentences.

## Notes and rules

Notes are generation-bound User records, never Source citations or AI assessments. Rebasing an object projects its notes to the new ref while preserving the original note record and target lineage.

Rules live at private `data/user_rules/global.jsonl`, `data/user_rules/types/<type>.jsonl`, and `human/rules.jsonl`. Latest revision per rule ID wins within its scope; enabled applicable rules are ordered Global, Type, Book, ascending priority/ID within scope. Context states that later/more specific scope wins, with system/schema/evidence/lock invariants above every user rule. Python enforces structural invariants; semantic instruction conflicts still require Codex judgment. Rules never edit repository prompts or automatically stale existing knowledge.

History is intentionally uncompacted in Phase 8. Future compaction must preserve complete audit/lineage and cannot replace the append-only source of truth silently.
