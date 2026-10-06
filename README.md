# Book Distiller

Book Distiller 是一个本地、Codex 驱动的书籍知识蒸馏系统。

## 当前状态

**Phase 9A：Book Ask。基于 Effective Knowledge、当前 Verification 和 Source Evidence 的单书问答，保留 Phase 1–8 的全部能力。**

已支持 Ingest、Library、SQLite、Docling Parse、Canonical Normalization、确定性 Parse Quality、Codex Workflow Protocol、Context Package、Book Classification、Analysis Chunk、Atomic Claims、Chapter Knowledge Atoms、Concept Registry、跨章关联/去重决策、Core Ideas、Mental Models、Meta Principles、Book Memory、Citation Verification、Fidelity Review、Coverage Review 和 Quality Gate。
Docling 在进程内解析 PDF、EPUB、DOCX、Markdown；TXT 由轻量 PlainTextAdapter 读取 UTF-8 段落。
不推断作者、出版社或 ISBN。仍未完成完整 V1 蒸馏；未实现 Embedding、Vector DB、GraphRAG、Bundle、Backup、Restore、跨书 Ask、Benchmark、MinerU fallback 或外部研究。

## 环境与安装

要求 macOS、Python 3.12（>=3.12,<3.13）和 Git。
缺少 Python 3.12 时请自行安装并加入 PATH，例如使用已有 Homebrew：

```bash
brew install python@3.12
./setup.sh
```

setup 可重复运行，复用并检查 `.venv`，安装项目及开发依赖，执行 doctor 和 pytest。
不会清空 inbox、library 或 backups，也不会自动安装系统 Python。
已有 `.venv` 时直接复用其中的 Python 3.12，无需再次调整 PATH。
本次本机环境由 Codex 随附 Python 3.12 创建；若该运行时被移除，需要用自行安装的 Python 3.12 重建虚拟环境。

## 命令

```bash
./book --help
./book version
./book doctor
./book ingest tests/fixtures/sample.md
./book ingest "/path/to/穷查理宝典.pdf" --title "穷查理宝典"
./book ingest "/path/to/另一版本.epub" --no-copy
./book status
./book status sample
./book status <book-uuid>
./book parse <slug-or-book-uuid>
./book parse <slug-or-book-uuid> --force
```

`./book` 自动使用项目 `.venv`，保留调用者的工作目录以正确解析相对输入路径。
支持 `.pdf`、`.epub`、`.txt`、`.md`、`.markdown`、`.docx`，后缀不区分大小写。
ingest 只校验文件与后缀；parse 才进行正文解析，不执行 shell。

默认复制到 `library/<slug>/source/original.<ext>`，manifest 保留原始文件名。
`--no-copy` 不创建 source 副本，存储原文件绝对路径；**原文件移动或删除后引用会失效**。
同 SHA256 二次导入成功返回已有对象，不复制、不创建 Edition 或 Task，也不修改原导入的标题和模式。
已有副本丢失、变更或 manifest 损坏时明确报错；重复导入不是自动修复命令。

文件名或 `--title` 提供初步标题，`metadata_status = provisional`。
slug 保留中文等 Unicode 字符，冲突加 `-2`、`-3`。业务 ID 是稳定 UUID，与书名、slug 无关。
status 支持精确 slug 或 Book UUID，不按标题模糊猜测。`pending` 表示尚未进行后续书籍处理，
不代表有后台任务在运行。每次新导入记录 completed ingest Task；不制造 AI Run。

## 数据位置与一致性

默认使用项目根目录：

```text
library/<slug>/manifest.json
library/<slug>/source/original.<ext>  # 仅 copy 模式
data/book_distiller.sqlite3
data/.ingest.lock
```

Manifest version 为 `1.0`，SQLite schema version 为 `1`。Manifest 中 `book.title`
是 Canonical Book 的初步标题，对应数据库 `canonical_title`；`edition.display_title` 是版本标题。
时间为带时区 ISO 8601。Library 内路径相对单本目录，外部引用为绝对路径。

数据库通过标准库 sqlite3 实现，books、editions、runs、tasks 具有主键、必要唯一约束和外键。
未知或无版本的已有数据库拒绝自动修改。ingest、status、doctor 可安全初始化新数据库。
doctor 检查八项：macOS、Python、项目根目录、inbox、library、backups、Database、Docling。
它会初始化索引并检查数据库完整性；Docling 仅检查公共模块 import 和版本，不创建转换器、不解析 PDF、不下载模型。

导入锁串行化本地 CLI 操作。临时目录准备完成后，在 SQLite 事务内登记记录并发布目录；
普通失败回滚数据库并清除本次导入目录。manifest 使用临时文件 + rename 原子写入。
文件系统与 SQLite 并非单一原子事务；进程强制终止可能留下临时目录或未索引 manifest。
下次操作会明确拒绝继续并指出路径，保留数据供人工核对，不自动删除或推断恢复。

遇到这类错误，先保留相关目录和数据库副本，核对 manifest/索引；不要直接修改 schema version。
当前未提供自动恢复、索引重建、Backup 或 Snapshot 命令。

## 隔离测试环境

`BOOK_DISTILLER_HOME` 覆盖数据根目录，不改变项目源码位置。人工 smoke test 可使用：

```bash
export BOOK_DISTILLER_HOME="$(mktemp -d)"
mkdir -p "$BOOK_DISTILLER_HOME"/{inbox,library,backups}
./book ingest tests/fixtures/sample.md
./book status
./book status sample
./book doctor
unset BOOK_DISTILLER_HOME
```

以上测试对象留在独立临时目录，不进入正式 Library。

```bash
source .venv/bin/activate
pytest
```

自动测试使用临时存储和原创 fixtures，不访问私人书籍，覆盖基线、导入/引用/去重、并发、
异常回滚、中断检测、版本拒绝及 CLI。状态查询检查 manifest、文件存在性与大小；
不会每次重算所有书的 Hash。重复导入会校验已有 Source 的完整 Hash。

## V1 架构原则

- Docling primary parser：已接入进程内公共 API，当前实测 2.132.0。
- MinerU optional isolated fallback：Phase 11 接入，独立环境。
- Filesystem + SQLite：文件保存主体，SQLite 保存索引与基础运行/任务元数据。
- Canonical Document Model：已实现 NormalizedBook；知识模型限 AtomicClaim / KnowledgeAtom，尚无更高层综合。
- Codex reasoning：V1 无需额外模型 API；未来允许 Provider Adapter。
- Static HTML + Jinja2：纯本地三栏 Reader，无服务端、前端框架或远程依赖。

`core/ingest.py` / `core/parse.py` 编排业务；`storage` 负责持久化；`cli` 负责参数与输出。
`parsers` 适配第三方结构，`normalize` 只消费中立记录；证据语义验证、完整质量和渲染包仍为占位。项目 Skill 是自然语言入口，确定性操作交给 CLI/Core。
见 [ADR](docs/adr) 和 [选型说明](docs/research)。历史 ADR 中 Phase 0 边界记录保留，Phase 1 由 ADR-006 补充。

`VERSION` 是唯一手工维护的版本源，当前仍为 `0.1.0-dev`，打包时规范化为 `0.1.0.dev0`。
私人数据目录及 SQLite 日志文件均被 Git 忽略；仅 `.gitkeep` 被保留。
未来测试二进制只通过 `.gitignore` 精确路径例外允许。


## Parse：产物、幂等与边界

```text
library/<slug>/
├── manifest.json                     # 仍为 Phase 1 manifest 1.0
├── parsed -> .parsed-generations/<task-uuid>
│   ├── raw/
│   │   ├── docling.json               # Docling lossless public JSON export
│   │   ├── docling.md                 # 人工检查，不是 Canonical Model
│   │   └── parse_metadata.json
│   ├── normalized/
│   │   ├── book.json                  # 小型元数据、章节和特殊内容索引
│   │   ├── blocks.jsonl               # 统一正文流，可逐行读取
│   │   └── quality.json
│   └── completion.json               # 任务、缓存键、产物完整性摘要
└── parse_failures/<task-uuid>.json     # 失败诊断（如有）
```

TXT raw 保存为 `plaintext.txt`，不伪装为 Docling 产物。
PDF 目前默认关闭 OCR，启用表格结构解析，不启用远程推理服务。
扫描件若没有可提取文本会 failed；正文过少但仍有文本、遗漏图形等问题需要人工检查，
质量 pass 只表示所列确定性检查通过，不保证语义完整或证据有效。
首次 PDF 解析可能从 Hugging Face 下载 Docling 布局/表格模型，模型缓存不属于 Git。
下载失败明确记录失败任务，可在网络恢复后再次 parse；不会触发 MinerU。

同 source SHA256、parser、parser version、normalized schema、normalizer version、配置与阈值，
且完整产物与 completed Task 有效时返回 `Already parsed.`。每次仍重新校验 Source SHA256。
外部引用被修改时报告 `External source has changed since ingest.`，要求重新 ingest 为新 Source。
`--force` 只重跑 Parser + Normalize。新结果在临时目录完成并校验后原子切换 parsed 链接；
raw 与 normalized 同时更新。失败不会先删除旧成功结果，旧代目录保留。
这不是完整 Pipeline Rerun、Snapshot 或自动恢复服务。

Task 使用已有 pending → running → completed/failed 状态；review_recommended 的解析仍 completed。
status detail 分别展示最近成功解析与最新 Task，避免失败的 force 覆盖旧成功结果的事实。
强制终止可能留下 running Task/临时目录/未完成的发布；系统报告需要核对，不自动恢复或清理。
SQLite schema 保持 1、manifest 保持 1.0；没有添加重复的 manifest.parse。
解析事实来自完整 parsed 产物，Task 状态来自 SQLite。详见 [ADR-007](docs/adr/ADR-007-normalized-document-storage.md)。

## Canonical 与 SourceSpan 约定

- NormalizedBook schema / normalizer version 均为 `1.0`，不 import Docling 类型。
- Block type 包含 title、heading、paragraph、list_item、quote、table、formula、code、figure、caption、footnote、other。
- Edition 内顺序 ID：`ch_0001`、`sec_0001_0001`、`blk_000001`；特殊内容索引指向统一 Block。
- PDF `source_page_index` 为 0-based；`source_page_number` 为 1-based physical page。
- EPUB、DOCX、Markdown、TXT 不伪造 PDF 页码；printed_page_label 未可靠提供时始终 null。
- char_start/char_end 为规范化 Block 文本的半开区间；跨页且无法安全映射时为 null。
- bbox 保留 PDF 坐标与 top-left / bottom-left 原点；parser_locator 只是来源元数据。
- 最高有效 heading level 建 Chapter，深层 heading 建 Section 栈；title 不自动当 Chapter。
  无明确 heading 或首个 heading 前内容使用 synthetic `Document` Chapter。不猜章节语义。
- 只做 NFC 与换行归一，不改写、不去重、不摘要；raw 输出保留以供回溯。

## Parse Quality 与测试

阈值集中在 `QualityThresholds`：source_map_coverage ≥ 0.95、empty_page_ratio ≤ 0.30、
至少 1 个字符。无可用文本/Parser 失败为 failed；覆盖不足、空页超阈值、Parser 警告或部分成功为
review_recommended；其他情况 pass。每个 issue 包含 code、severity、message、metric、threshold。
不制造一个无法解释的综合分数。非分页格式的页相关指标为 null。

```bash
pytest                              # 不运行真实转换，不下载模型
pytest --run-docling-real            # 显式运行原创 PDF/Markdown/DOCX/EPUB 真实转换
```

普通测试用中立 records、公开 Docling model fixture 和 mocked converter；所有数据仍隔离。
真实集成覆盖原创双页 PDF 的页码、heading、paragraph、order、SourceSpan；
同时覆盖 .md、.markdown、.docx、.epub。PDF fixture 的原创说明见 tests/fixtures/README.md。


## Classification：Codex 与本地 Core 协议

在项目中让 Codex 执行“给这本书分类”，项目 Skill 会解析目标、确认 Parse、prepare、阅读上下文、做出分类判断、写 result、submit 并读回正式结果。未解析时会先明确告知再 parse；解析失败停止。Python 不调用任何 LLM API，也不自行分类。

```bash
./book workflow prepare classify <slug-or-book-uuid>
# 当前 Codex 读取返回路径的 workflow.md、prompt.md、context.md、output.schema.json
# 依据 context.json 的身份/版本字段，将严格 JSON 写入同目录 result.json
./book workflow submit <task-uuid> --result <runtime-task-dir>/result.json
./book status <slug-or-book-uuid>
```

```text
library/<slug>/
├── runtime/tasks/<task-uuid>/
│   ├── request.json
│   ├── context.json / context.md
│   ├── output.schema.json
│   ├── workflow.md / prompt.md
│   ├── result.json              # 提交时出现
│   ├── validation.json          # 验证诊断
│   └── apply.json               # 发布意图及结果摘要
└── analysis/classification.json # 当前分类唯一权威
```

Task 使用现有 pending → completed，格式/证据错误可修正后重试同一 Task。STALE_CONTEXT 要重新 prepare 并重新判断，不能只替换 hash。强制重解析也会使旧 Context 失效。完成后的重复 submit 不覆盖更新的分类；runtime 不必永久保留，但删除后不能重试该 Task。manifest 不复制分类，SQLite 只保存 Task 状态，Run 未启用。

ContextPackage 1.0 只读取 Canonical 正文，按 ID/Chapter/Section/顺序范围流式选取。默认分类采样上限 40 Blocks、80 条目录、30,000 字符、10,000 估算 tokens；正文太大时保留全书位置覆盖并截断片段。预算取 JSON/Markdown 较大值，两者是替代表达。实际选取/遗漏/截断与版本和 SHA256 均可审计。摘要 hash 排除自身字段，同一 Task 的相同输入输出稳定。

分类支持十个基础书型，至多 3 个不重复次类型、12 个 lowercase kebab-case 标签。confidence 为 0–1 主观置信程度，不是数学概率。证据必须指向本 Context 的 Block；只保存短 rationale_summary，不记录隐藏思维链。review_recommended 解析仍可分类，但会保留并展示来源质量警告。人类确认/锁定字段固定 false，尚无人工覆盖操作。

Pydantic 是 Schema 源头，仓库 schemas/types/classification.schema.json 为其生成快照。Workflow 是任务规则权威，Prompt 提供执行指导，Skill 负责路由。详见 [ADR-008](docs/adr/ADR-008-ai-task-protocol.md) 与 [ADR-009](docs/adr/ADR-009-context-package.md)。自动测试包含 10,000 Blocks 的流式/内存/预算验证及协议拒绝、过期、重试与原子写入验证。

runtime、context 和 classification 都是私人 Library 数据，不进入 Git；未来 Bundle 是否携带 runtime 暂不决定。本节描述 Phase 3 边界；后续阶段能力以顶部当前状态为准。


## Chapter Atomization（Phase 4）

在 Codex 中说“提取第 X 章 Claims”或“分析第 X 章知识原子”。Skill 用 NormalizedBook 的 Chapter ID/标题准确映射目标，先确保 Parse 和当前 Classification，再逐任务完成真实 AI 判断。CLI 只 prepare/validate/apply，不调用 LLM，不自动等待模型 API。

```bash
./book analyze claims <book> --chapter ch_0003
# 对每个 pending Task 读取 workflow.md / prompt.md / context.md / output.schema.json
# Codex 将结构化 Claims 写入各自 result.json（允许零条）
./book workflow submit <task-id> --result <task-dir>/result.json
./book analyze atoms <book> --chapter ch_0003
# Codex 基于完整 Chapter Claims 构建 Atoms，再 submit
./book workflow submit <atom-task-id> --result <atom-task-dir>/result.json
./book status <book>
```

`workflow prepare extract_claims/build_chapter_atoms <book> --chapter <id>` 也可路由相同流程。普通继续会复用已完成 Chunk；明确重建使用 `analyze claims ... --force`，不会先删除旧知识结果。参数必须为确切 ch_XXXX，synthetic Document 不等于原书第一章。章节目录来自 parsed/normalized/book.json。

Chunk 默认 10,000 正文字符、3,500 估算 tokens、30 Blocks，Section 优先切分，最多 1 个相邻普通正文 Block 作 context-only overlap。Table/Code/Formula 不拆，结构允许时 Figure/Caption 成对。超大单元独立成 Chunk 并警告；无法完整装入有界 Context 时明确停止，不截断证据。

```text
library/<book>/
├── runtime/generations/<uuid>/generation.json + chunks.json
├── runtime/tasks/<uuid>/request/context/schema/result/validation/accepted...
└── knowledge/
    ├── .pending/<generation>/claims.jsonl + chunks.json + claims-receipt.json
    ├── .generations/<generation>-<publication>/
    │   ├── claims.jsonl
    │   ├── atoms.json
    │   ├── chunks.json
    │   ├── claims-receipt.json
    │   └── chapter.json
    └── chapters/ch_XXXX -> ../.generations/<generation>-<publication>
```

整章 Claims 全部完成后形成候选 generation，Claims-only 请求到此停止。只有 Atoms 也成功才一次切换章节 canonical 链接。失败保留上一代，runtime 不是最终知识权威。每个 Atom 经 claim_ids → Claim evidence.block_id → Normalized Block → SourceSpan 回到来源；chapter.json 保存对应 normalized generation 路径。这是结构追溯，不是 Citation Verify。

Knowledge Context 为 1.1，原分类协议仍为 1.0。Classification、normalized generation、workflow/prompt/schema 变化会使相关任务 stale。Universal + Investment / Philosophy / Business 提示按分类组合并 Hash 绑定；不硬编码具体书。Claim / Atom schema 都是 1.0，由 Pydantic 生成 JSON Schema。source_type 仅 source；reasoning 是原书公开论证，不存私有思维链。importance 仅指当前章，confidence 不是 Fidelity。

Atom 输入完整保留 Chapter Claims，固定上限为 120,000 Context 字符、40,000 估算 tokens（加载前 Claims 文件上限 2 MiB）。超出返回 CHAPTER_CONTEXT_TOO_LARGE，保留所有已成功 Claims 和旧章节结果，不丢弃、不无限扩容。Phase 5 可用显式 `--reduce` 进入分层聚合，原普通路径保持兼容。

结构指标仅包括 Claims/Atoms 数量、assigned/unassigned、evidence 有效性、完全重复与平均每 Atom 的 Claims。没有知识数量 KPI；0 Claims/Atoms、unassigned Claims 都允许。更多存储、恢复和版本约束见 [ADR-010](docs/adr/ADR-010-knowledge-atomization.md)。

本节描述 Phase 4 章节层边界；后续阶段能力以顶部当前状态为准。逐章分析不等于完成整书流程。

## Book Knowledge Synthesis（Phase 5）

```bash
./book analyze book <book>
./book workflow submit <task-id> --result <task-dir>/result.json
./book analyze book <book>  # 消费已接受结果并准备下一步，直到 Book published
./book analyze book <book> --force  # 明确重建或 stale 后开始新 generation
./book analyze atoms <book> --chapter ch_0001 --reduce
./book status <book>
```

CLI 不调用模型；当前 Codex 读取 task 的 Workflow / Prompt / Context / Schema 完成判断。四步依次是 normalize_concepts、build_core_ideas、build_mental_models、build_meta_principles。超预算输入先进入 synthesis_reduce；长章最终使用 reduce_chapter_atoms。任务之间可中断恢复，已完成提交保持幂等。Context 1.2 使用 60,000 字符 / 20,000 估算 tokens 上限，批次最多 24 项 / 14,000 JSON 字符；单项、概念词表或已有 Registry 本身无法容纳时明确报告预算限制。不会静默丢弃引用或无限扩容。

`--reduce` 逐批读取完整 Claims 的声明及 terms，保存原始 Claim 谱系，最终展开后与完整原 Claims 一起发布。它不推翻 Context 1.1 的正常章节路径；classification 仍是 1.0。中间摘要不是正式 Knowledge Object，也不等同于已经核验的原文。

正式结构：

```text
knowledge/
  chapters/ch_XXXX -> ../.generations/<chapter-generation>-<publication>/
  book -> .book-generations/<book-generation>-<publication>/
  .book-generations/<book-generation>-<publication>/
    book_model.json
    concepts.json
    core_ideas.json
    mental_models.json
    meta_principles.json
    relationships.json
    book_memory.json
    provenance.json
```

Book model 记录所有当前已完成 Chapter generation、不可变路径及哈希。发布的 atom_refs / claim_refs 显式包含 chapter_id、chapter_generation_id 和对象 ID；旧 Book 通过 pinned generation 解析，绝不追随当前 Chapter 链接。章节、classification、normalized generation 或工作流版本变化会使 Book status stale；旧完整结果保留。Book generation 全部成功才一次切换，发布后的 metadata 写入失败会恢复旧指针。

Concept alias normalization 与 Atom merge 独立；源 Chapter Atoms 永不删除。Core Ideas 必须有 Atom，Model 必须有下层来源和源内机制，Principle 至少有两个 Ideas 或两个 Models 且跨至少两章；允许零个 Model/Principle。所有指标只是结构诊断，不是 Fidelity 或质量分数。

Book Memory 1.0 由 Python 确定字段、排序和裁剪，默认 12,000 字符，记录选取/遗漏数量与 hash。它包含身份、章节状态、概念和高层对象摘要及 generation-bound 来源引用，不包含全部正文。Context Builder 可显式 include_book_memory=True；同时校验 Memory hash 与依赖，并计入预算。

边界与恢复规则见 ADR-011、ADR-012、ADR-013。Phase 5 测试使用原创三章 fixture 和隔离存储；1,200 Atom 压测使用合成数据，不能作为语义质量证明。真实 Codex smoke 的验收记录见 docs/phase5-acceptance.md。

## Evidence & Quality Foundation（Phase 6）

```bash
./book verify <book>            # 创建或恢复同一次核验，显示 Task 和进度
# 当前 Codex 读取 Task 的 workflow/prompt/context/output.schema.json，独立判断
./book workflow submit <task-id> --result <task-directory>/result.json
./book verify <book>            # 继续直到 Verification published
./book verify <book> --force    # 明确重新核验；不重跑知识蒸馏
./book status <book>
```

前置条件是当前 classification、Chapter generations 和 Book synthesis。CLI 不调用模型；自然语言“核验这本书”“检查幻觉/重大遗漏”由 Skill 完成 AI 循环。顺序为 Claim → Atom → Core Idea → Mental Model → Meta Principle → Chapter coverage → Concept/relationship review。每层独立回到实际来源判断，不向上继承评分。

Citation 1.0 用 edition、normalized generation、Block、半开字符范围派生稳定 ID；保存 SourceSpan/页码/locator/hash，不复制正文。strength 与 verdict 分开。Context 1.3 保持旧协议兼容，默认上限 48,000 字符，原文 16,000 字符，下层对象 12 个。原始 Claim evidence 加同 Section/Chapter 前后一个 Block；仅 context_insufficient 且 weak/insufficient 可安排一次局部 recheck，扩至前后三个 Block。新证据单独保存在 supplemental_citation_ids，原 Claim 不改写。过大完整块明确报错；高层采样不完整显示 needs_review。

Coverage 逐 Chapter 以最多八个源 Block 的批次检查重要内容是否缺失，并保留 batch source refs。Concept/关系候选语义审查不做全量 N² 或 embedding。问题只能建议 review / Phase 4、5 rerun，不自动改写 Knowledge Model 或创造 Claim。

```text
verification/
  current -> generations/<verification-generation>-<publication>/
  generations/<verification-generation>-<publication>/
    manifest.json
    citations.jsonl
    claim_assessments.jsonl
    atom_assessments.jsonl
    idea_assessments.json
    model_assessments.json
    principle_assessments.json
    coverage_review.json
    review_issues.json
    quality_report.json
    quality_report.md
    provenance.json
runtime/verification-generations/<uuid>/state.json   # 私人恢复检查点
```

完整报告才原子切换 current；失败保留旧完整 generation，运行错误另有诊断。normalized、Chapter、Book、classification、workflow/prompt/schema 或 quality rules 变化会令旧核验 stale。成功对象检查点可复用，后续失败不重做全部前置对象。

`book_distiller.evidence.paths.evidence_path(library_book_directory, object_id)` 提供可查询的 generation-bound 证据图，动态取得 excerpt、context、Chapter、PDF page 和原始位置，可供定向证据检查使用。该查询函数不产生 HTML，且历史路径解析能力不表示旧 generation 仍是当前结果。

Quality report 明示公式、分子、分母、阈值、verdict/issue counts 和 rechecks。`pass` 仅表示当前模型通过本地证据规则；内容问题为 `needs_review`；损坏引用/来源/产物或不能完成验证为 `failed`。Fidelity = major 对象权重之和 / major 对象数，权重 supported=1、partial=.5、unsupported/contradicted=0；高 Fidelity 不能覆盖重大幻觉。计数项分母为 null，零样本比例也为 null。

parse completeness 只是已规范化 Block 的结构代理；章节覆盖不是识别准确率；major_omission_chapter_rate 是已审查章节中的遗漏发生比例，不是有 Gold Set 的关键思想遗漏率。长输入采样和语义判断的误报/漏报仍需人工 benchmark。具体决策与阈值见 [ADR-014](docs/adr/ADR-014-evidence-verification.md)、[ADR-015](docs/adr/ADR-015-quality-gate.md)、[规则](rules/quality/standard.json)。测试协议替身与真实 Codex smoke 分开记录。


## Progressive Reading / Static Reader（Phase 7）

```bash
./book render <book>
./book render <book> --no-open
./book render <book> --force
./book status <book>
```

Book synthesis 和 Verification 必须为当前依赖；Quality Gate `pass` 与 `needs_review` 可生成，后者保留醒目警告。没有核验、failed 或 stale 默认拒绝。Reader 不调用 AI、不重新总结，不写入 Canonical Knowledge 或 Verification。

输出 `library/<slug>/output/` 是完整 `.reader-generations/<uuid>` 的原子链接，含 `index.html`、`assets/`、`data/`、`markdown/` 和 `render_manifest.json`。打开失败只警告，可手动双击 index.html。相同输入返回 Already rendered；`--force` 失败保留此前成功 Reader。status 对 generation、依赖、模板版本和导出完整性检查 completed / stale / unavailable。旧导出是生成时快照，离线页面不会自行探测后来发生的 canonical 变化；以 CLI status 为准。

- **HTML = interactive reading**：Jinja2、系统字体、CSS、Vanilla JS；classic JS 数据包支持 `file://`，无需 fetch、服务器、Node 或网络。
- **Markdown = portable text reading**：L0.md、L1.md、L2.md、L3.md、knowledge-model.md、quality-report.md。
- **JSON = structured machine-readable view**：book、progressive、concepts、knowledge、evidence、quality、search、mindmap、cards。它们是可重建的 derived export，canonical 仍在 knowledge/、verification/。

L0 选受支持的最高层既有 statement，过长时截取原有完整首句，仍超限时使用既有标题；不会创造一句新的“全书总结”。L1 选至多 7 Ideas / 3 Models / 2 Principles。L2 加机制、限制、有限概念目录和已选对象的章节贡献。L3 增加部分主要 Atoms/Claims（最多 16 / 24，且常规不超过各类 60%）；小模型可明显短于标称阅读时间。不为满足 3/15/60 分钟而填充内容。

L0–L2 只选 supported，强证据先于重要度；重大未解决问题和 synthesis overreach 不提升为快速阅读结论。L3 可显示 partially_supported，unsupported / contradicted 保留在完整 L4/L5 并标明。L4 分组分页展示全部对象与关系；搜索支持中文、英文、别名、Claim 文本。左侧目录/章节，中间阅读，右侧对象核验/证据；窄屏用抽屉。Hash deep links 支持浏览器 Back/Forward。知识地图只包含 Principle / Model / Idea / Concept，Cards 是高层对象视图。

L5 从 Citation→Block→char range 动态生成导出片段，保留 SourceSpan/parser locator；PDF 页是物理页，无页码时显示 Chapter/Section/Block。默认显示 400 字符、可展开到 800；周边上下文默认折叠、每块至多 160 字符。总嵌入证据默认 100 万字符，先保主要 Citation 范围，再保上下文；截短或预算省略明确标注，所有 Citation 位置保留。不复制整本 source。no-copy 原件已丢失时允许读取已核验 canonical 证据，Open Original unavailable；存在但变更的原件仍拒绝。

规则在 `rules/reader/standard.json`：L0–L3 展示预算 300 / 2500 / 10000 / 24000 字符；L2 字符数包含选入概念名称和章节贡献。大小指标包含 HTML、data、证据字符、search entries、总文件字节；超过 20 MB 警告，超过 64 MB 拒绝发布。JSON 与 JS bundle 各保留一份数据，大小指标计入两者。所有书籍文本使用 textContent / Jinja autoescape；无 CDN、外部字体、追踪或网络请求。

阅读器可重新删除生成，不能反向写入知识。只在 Ingest→Parse→Classification→Claims→Atoms→Book synthesis→Verification→Render 全部完成后称“这本书的可阅读蒸馏结果已经生成”；整个 V1 尚未全部完成。浏览器对 file:// 的 UI 自动化或原件打开可能有额外限制，路径与位置仍可手工使用。

设计决策：[ADR-016](docs/adr/ADR-016-progressive-reading.md)、[ADR-017](docs/adr/ADR-017-static-reader.md)。


## Resume / Rerun / Human Override（Phase 8）

```bash
./book rerun <book> --from claims --chapter ch_0001 --dry-run
./book rerun <book> --from claims --chapter ch_0001
./book rerun <book> --from atoms --chapter ch_0001 --through atoms
./book rerun <book> --from book
./book rerun <book> --from verification
./book rerun <book> --from render
./book resume <book>
./book resume <book> --run <run-uuid>
./book human list <book>
./book human show <book> --target /tmp/target.json
./book human apply <book> --input /tmp/action.json
./book human history <book>
./book human note <book> --input /tmp/note.json
./book human rule <book> --input /tmp/rule.json
```

阶段名为 parse、classification、claims、atoms、book、verification、render。重跑默认到 render；`--through` 限制本次执行的末端，下游仍标记 stale。`--chapter` 只适用于 claims/atoms。Dry-run 展示 Will rerun、Will reuse、Protected、Downstream stale，不写文件。锁冲突停止并报告 needs_review，不能使用 ignore-locks。

Run 返回 pending Tasks 时仍需当前 Codex 读取每个 workflow/prompt/context/schema，提交真实判断，然后 resume。没有后台 AI。已完成 Task 的合格检查点复用；完成后的同一 Run 再 resume 不产生新 generation。旧 stage CLI 是低层适配器，不自动转换为 Run；没有 Run 时 resume 明确报错。

`human list/show` 返回 generation-bound `target_ref` 和 `base_object_hash`。将它们原样放入 action：

```json
{"action":"edit","target_ref":{"object_type":"atomic_claim","generation_id":"<uuid>","object_id":"claim_ch_0001_0001_001","chapter_id":"ch_0001"},"base_object_hash":"<current hash>","patch":{"statement":"经人工核对的陈述"},"reason":"补回原书限定条件"}
```

verify/lock/unlock 使用相同身份与最新 hash，patch 留空。编辑后旧 hash 会被拒绝；identity、evidence 和下层 refs 不可编辑。历史 AI JSON 不变，人工动作追加到私有 event log；state.json 可重建。Human verified 不是 Quality PASS。

Note JSON 为 `{"target_ref":{...},"text":"这个观点值得回看第3章。"}`。Rule JSON 为 `{"scope":"book","applicable_workflows":["build_core_ideas"],"instruction":"Do not promote examples to Core Ideas."}`；type scope 还需 book_type。结构/证据/锁不变量 > Book > Type > Global。规则只进入之后新 Task 的 Human Guidance，既有 Context 冻结，不自动重跑 Library。用户规则不写入 prompt 文件。

内容 edit 使相应下游失效；verify/lock/unlock/note 只让 Reader 显示过期。Reader 使用有效人工内容，显示人工修改/确认/锁定/User Note。重新打开 managed output 会读取独立 stale 提示；已打开页面需刷新。复制出去的静态导出仅代表生成时 SNAPSHOT。

所有 Run、Human、Context、知识、核验、Reader 和源书数据仍在 gitignored Library/data 或隔离 HOME 中。详见 [ADR-018](docs/adr/ADR-018-run-resume-rerun.md)、[ADR-019](docs/adr/ADR-019-human-effective-knowledge.md)、[ADR-020](docs/adr/ADR-020-reader-human-state.md)。


## Book Ask（Phase 9A）

Book Ask 是 evidence-constrained QA。Core 对当前 Effective Knowledge 做确定性词法检索、证据链展开和有限 Context 选择；当前 Codex 按 `ask_book` workflow 回答，再由 Core 验证引用和身份后发布。No external search / No embedding / No vector DB / No cross-book Ask。

```bash
./book status <book>
./book ask prepare <book> "What should remain readable while a candidate is prepared?"
# 当前 Codex 阅读返回 Task 的 workflow.md / prompt.md / context.json / context.md / output.schema.json
# 生成严格 BookAnswer JSON；不能用 Python、模板或测试 double 替代真实语义判断
./book ask submit <task-uuid> --result <result.json>
./book ask show <book> <answer-uuid>
```

Skill 可路由“问这本书”“作者为什么”“原文在哪里”“第3章讲什么”“X与Y有什么区别”“用书里的模型分析”“我写过什么笔记”。对话 follow-up 先展开成完整问题，Core 不读取隐藏聊天上下文。

- PASS：ready。NEEDS_REVIEW：允许，但 Context 和答案必须带整书及相关问题警告。FAILED / stale：blocked，先完成所需重跑与核验，没有 `--ignore-quality`。
- 当前知识只通过 `EffectiveKnowledgeResolver` 读取。Human modified 使用有效文本；Human verified / locked 不增加 AI evidence strength。
- Source、AI synthesis/application、User Note 以 answer segments 区分。笔记不冒充作者证据。Book Memory 只用于方向，不是 Citation。
- 证据不足是有效答案：`insufficient_evidence=true`。超出书籍范围还标记 `out_of_scope=true`，不联网、不用常识补全。
- Context 1.4 默认 40,000 chars / 13,333 estimated tokens，最多 48 个对象、检查前 128 个候选根；保留完整引用链或显式省略，单条 Source excerpt 最多 1,800 chars，超长已核验范围会省略并限制 confidence。旧 Context 1.0–1.3 继续兼容。
- Task 绑定 question、Book / Verification generations、Human state、classification、适用规则和版本资源。prepare 后变化拒绝旧结果；格式错误允许同 Task retry。
- 私人 `library/<book>/ask/answers/<task-id>.json` 是答案权威，`history.jsonl` 仅索引。answer 与 Task 提交完成后补索引；索引失败可 `ask show` 修复，无需再次调用 Codex。保存的答案是绑定旧输入的快照，不自动宣称仍是当前答案。
- Ask 只增加答案及 Task/runtime 记录，不修改 Knowledge、Verification、Quality Gate、Human state。SQLite 保持 schema 2。

约束与边界见 [ADR-021](docs/adr/ADR-021-book-ask.md)。Answer Schema 由 Pydantic 生成，见 `schemas/types/book-answer.schema.json`。
