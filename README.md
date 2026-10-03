# Book Distiller

Book Distiller 是一个本地、Codex 驱动的书籍知识蒸馏系统。

## 当前状态

**Phase 3：Codex Integration Foundation。暂未实现真正书籍蒸馏。**

已支持 Ingest、Library、SQLite、Docling Parse、Canonical Normalization、确定性 Parse Quality、Codex Workflow Protocol、Context Package 和 Book Classification。
Docling 在进程内解析 PDF、EPUB、DOCX、Markdown；TXT 由轻量 PlainTextAdapter 读取 UTF-8 段落。
不推断作者、出版社或 ISBN。仍未实现 MinerU、AI 蒸馏、知识提炼、问书、Citation Verify、完整 Quality Engine 或 HTML 阅读器。

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
- Canonical Document Model：已实现 NormalizedBook；知识提炼模型仍未实现。
- Codex reasoning：V1 无需额外模型 API；未来允许 Provider Adapter。
- Static HTML + Jinja2：未来静态阅读器，当前无模板业务。

`core/ingest.py` / `core/parse.py` 编排业务；`storage` 负责持久化；`cli` 负责参数与输出。
`parsers` 适配第三方结构，`normalize` 只消费中立记录；知识提炼、证据、完整质量、渲染包仍为占位。项目 Skill 是自然语言入口，确定性操作交给 CLI/Core。
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

runtime、context 和 classification 都是私人 Library 数据，不进入 Git；未来 Bundle 是否携带 runtime 暂不决定。仍不支持 Book Distillation、Atomic Claims、Knowledge Atoms、Citation Verify、HTML、Book Ask、RAG 或 MinerU。
