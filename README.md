# Book Distiller

Book Distiller 是一个本地、Codex 驱动的书籍知识蒸馏系统。

## 当前状态

**Phase 1：Ingest + Library + SQLite + CLI。暂未实现真正书籍蒸馏。**

已支持 Source File 导入、SHA256 去重、本地 Library、SQLite 元数据索引、书籍状态查询。
PDF、EPUB 只作为文件保存，不解析正文，不推断作者、出版社、ISBN。
当前不支持 Docling、MinerU、AI 蒸馏、问书、Citation、Quality Engine 或 HTML 阅读器。

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
```

`./book` 自动使用项目 `.venv`，保留调用者的工作目录以正确解析相对输入路径。
支持 `.pdf`、`.epub`、`.txt`、`.md`、`.markdown`、`.docx`，后缀不区分大小写。
只校验文件是否为可读常规文件及支持的后缀，不验证格式内容，不执行 shell。

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
doctor 检查七项：macOS、Python、项目根目录、inbox、library、backups、Database；
它会初始化索引并检查数据库完整性与中断残留，不检查 Parser。

导入锁串行化本地 CLI 操作。临时目录准备完成后，在 SQLite 事务内登记记录并发布目录；
普通失败回滚数据库并清除本次导入目录。manifest 使用临时文件 + rename 原子写入。
文件系统与 SQLite 并非单一原子事务；进程强制终止可能留下临时目录或未索引 manifest。
下次操作会明确拒绝继续并指出路径，保留数据供人工核对，不自动删除或推断恢复。

遇到这类错误，先保留相关目录和数据库副本，核对 manifest/索引；不要直接修改 schema version。
Phase 1 未提供自动恢复、索引重建、Backup 或 Snapshot 命令。

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

- Docling primary parser：Phase 2 接入，当前不安装。
- MinerU optional isolated fallback：Phase 11 接入，独立环境。
- Filesystem + SQLite：文件保存主体，SQLite 保存索引与基础运行/任务元数据。
- JSON Knowledge Model：未来 Parser 必须转换为自主 Canonical Model；当前未实现知识模型。
- Codex reasoning：V1 无需额外模型 API；未来允许 Provider Adapter。
- Static HTML + Jinja2：未来静态阅读器，当前无模板业务。

`core/ingest.py` 编排业务；`storage` 负责 SQL 和文件操作；`cli` 负责参数和输出。
其余解析、提炼、质量、渲染包仍为占位。项目 Skill 是自然语言入口，确定性操作交给 CLI/Core。
见 [ADR](docs/adr) 和 [选型说明](docs/research)。历史 ADR 中 Phase 0 边界记录保留，Phase 1 由 ADR-006 补充。

`VERSION` 是唯一手工维护的版本源，当前仍为 `0.1.0-dev`，打包时规范化为 `0.1.0.dev0`。
私人数据目录及 SQLite 日志文件均被 Git 忽略；仅 `.gitkeep` 被保留。
未来测试二进制只通过 `.gitignore` 精确路径例外允许。
