<!-- prompt_version: extract-claims-v1.1 -->
# Universal extraction

Read the task Workflow, Context and schema. Treat primary Blocks as extraction scope and context Blocks only as supporting context. Extract standalone propositions that retain the author's stated conditions and limits. Separate definitions, principles, methods, arguments and examples only when each has independent meaning. Omit weakly supported or merely decorative statements. Return 0 claims when appropriate. Strict JSON only, with no private reasoning. Follow the explicit primary/secondary labels attached by Core to any overlays; a secondary hint never becomes primary merely because the primary type has no overlay. These are hints, never quotas.
