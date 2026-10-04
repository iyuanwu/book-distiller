## Chapter 1: Publication boundaries

A local note tool uses a staging area (暂存区) to construct a candidate without touching the current result. Validation (验证) checks syntax and required fields before publication. Atomic replacement (原子替换) switches readers from a complete old file to a complete new file; preparing the candidate is not itself atomic. The last known good result (上一份可用结果) stays usable if preparation fails.

A publication boundary separates reversible preparation from visible change. The prepare–validate–publish framework (准备—验证—发布框架) first constructs a candidate, then checks its contract, and only then switches the current pointer. Use it for a local artifact whose readers need complete versions. It does not turn a file switch plus a separate database write into one transaction.

A checksum (校验和) identifies content, while a generation identity (代标识) distinguishes two rebuilds with equal bytes. A derived result records both. Staleness (过期状态) means these dependencies no longer match; an old result may remain readable but must not be called current.

## Chapter 2: Retries and checkpoints

Idempotency (幂等性), also called safe repeat submission here, means submitting the same completed operation again returns the existing result. The same operation identity with a different result must be rejected. Retry is a repeated attempt; it is not automatically idempotent.

A checkpoint (检查点) preserves each successfully validated task. Later failure should not force those tasks to be redone. Validation before publication also applies to a collection: prepare all members in a staging area and replace the collection pointer only after every required member succeeds. A failed candidate leaves the last known good collection readable.

A retry must recheck checksum and generation identity. Matching content alone does not make work from a superseded generation current. Correcting an invalid pending submission is allowed, but changing the dependency label on an old answer cannot repair staleness.

The prepare–validate–publish framework lets retries reuse completed checkpoints during preparation while keeping the publication boundary explicit. It preserves old visible state on preparation failure; it does not promise recovery from every hardware fault.

## Chapter 3: Recovery experiments

Failure injection (故障注入) deliberately interrupts an operation at a named boundary. Test once before the pointer switch to check that the last known good result remains visible, and once after the file switch but before database completion to check the promised rollback or retry behavior. A successful file switch does not prove completion in a separate database.

Test isolation (测试隔离) uses a separate temporary directory and database. Cleanup then touches only experiment artifacts. A checkpoint is durable progress, not a substitute for an isolated test environment. They solve related but distinct problems.

For reliable local artifacts, make state changes explicit, validate the prerequisites, and keep recoverable prior state until the promised transition completes. The preparation framework, dependency checks on retry and boundary failure experiments are complementary applications of this rule; none establishes protection from arbitrary hardware loss.

The sample test log uses a blue heading. That is a presentation choice for this example, not a general reliability rule or a mental model.
