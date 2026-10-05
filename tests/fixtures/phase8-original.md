## Chapter 1: Safe notebook publication

### Prepare

A local notebook writes a candidate into a separate temporary file. Preparing this file must leave the previous published notebook readable.

### Validate

Before publishing the candidate, the notebook checks its required fields. An invalid candidate is rejected and the previous published version remains available.

### Publish

After validation succeeds, an atomic pointer switch exposes the complete candidate. The pointer switch does not make a separate database write part of the same transaction.

## Chapter 2: Continue interrupted work

A completed task is a checkpoint only while its input identity remains unchanged. Resuming an interrupted job reuses valid completed checkpoints and executes the remaining tasks. A changed source generation invalidates a previous answer even if a short object identifier is reused.

## Chapter 3: Human decisions

A human correction is stored separately from an immutable generated notebook. A locked correction must survive later generation. When its evidence disappears, the system reports a dependency conflict instead of silently choosing similar evidence. An annotation records the user's note and is not evidence of what the original author said.
