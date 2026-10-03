---
name: book-distiller
description: 在 Book Distiller 中导入本地书籍文件、解析为 Canonical Document、查看 Library/Parse 状态并按 V1 边界开发。当前 Phase 2 不支持理解、总结、蒸馏或问书。
---

# Book Distiller

Current implementation phase: Phase 2

本地、Codex 驱动的书籍知识蒸馏系统；当前只能导入和结构解析，不能声称已经理解、总结、蒸馏或问书。
Skill 是自然语言入口；CLI/Core 负责确定性操作。

可用：`./book ingest <FILE>`、`./book parse <slug-or-book-uuid>`、`./book status`、
`./book status <slug-or-book-uuid>`、`./book doctor`、`./book version`。
默认复制 source；`--no-copy` 保留外部绝对路径，移动或删除会使引用失效。`--title` 仅设置 provisional 标题。
SHA256 重复 ingest 返回已有 Edition。每次 parse 重检 Source；变更的外部文件必须重新 ingest，不能当作原 Edition。

Docling 是进程内主 Parser，支持 PDF、EPUB、DOCX、Markdown；TXT 是自有轻量 UTF-8 段落读取器。
首次 PDF 解析可能下载模型；doctor 仅 import/版本检查，不能启动实际转换。
PDF 默认关闭 OCR；扫描件无可用文本会失败。不得自动调用或安装 MinerU。
Docling 类型只能在 Adapter 层，Normalizer 和上层使用自主 NormalizedBook/Block/SourceSpan。
无可靠 heading 时用 synthetic Chapter，不能调用 AI 猜章节。不得改写、去重思想或总结文本。

解析结果位于 `library/<slug>/parsed/raw` 与 `parsed/normalized`。
`parsed` 是指向完整结果代的相对链接，`--force` 安全重跑 Parser + Normalize，不是完整 Pipeline Rerun。
缓存键含源 hash、Parser/版本、normalized schema、normalizer version 和配置；有效结果返回 Already parsed。
失败保留旧成功产物，失败 Task 与诊断另存。review_recommended 仍表示解析完成，需要人工查看，不等于蒸馏质量。

SQLite 位于 `data/book_distiller.sqlite3`，schema 仍为 1；旧 manifest 1.0 不改结构，不重复保存 parse 状态。
身份/Task 以 SQLite 为准，Source 可移植元数据在 manifest，Raw/Normalized 主体在文件系统。
遇到残留临时目录、running Task、损坏产物或未知 schema version 时保留现场并报告，不自动恢复或删除。
权威关系、顺序 ID、PDF 页码约定和发布方式见 `docs/adr/ADR-007-normalized-document-storage.md`。

自动测试只使用原创 fixtures 和隔离存储。普通 pytest 不下载大型模型；
只有显式 `pytest --run-docling-real` 才执行真实转换。人工 smoke test 使用 BOOK_DISTILLER_HOME 临时目录，
不自动处理私人 inbox。环境通过 ./setup.sh 安装，缺 Python 3.12 时给安装建议。

仍禁止实现 MinerU（Phase 11）、AI 分类/推理 Workflow、知识层级模型、语义 Chunk、Citation Verify、
完整 Quality Engine、RAG、HTML、问书、队列调度或自动恢复业务。
后续 workflow/prompts/schemas/rules 目录仍按明确阶段扩展，当前不含完整蒸馏 Prompt。
