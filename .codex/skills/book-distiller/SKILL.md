---
name: book-distiller
description: 在 Book Distiller 项目中导入本地书籍文件、查看 Library 状态、检查环境并按 V1 架构边界开发。当前 Phase 1 不支持正文解析、蒸馏或问书。
---

# Book Distiller

Current implementation phase: Phase 1

本地、Codex 驱动的书籍知识蒸馏系统；当前只管理 Source File，尚未支持真正蒸馏、正文解析或问书。
Skill 是自然语言入口；CLI/Core 负责确定性操作。

可以使用 `./book ingest <FILE>`、`./book status`、`./book status <slug-or-book-uuid>`，
以及 `./book --help`、`./book version`、`./book doctor`。默认复制原文件；`--no-copy`
保存外部绝对路径并提示移动或删除会使引用失效。`--title` 仅设置 provisional 标题，不推断作者或 ISBN。
支持 PDF/EPUB/TXT/MD/MARKDOWN/DOCX 后缀，但不检查正文或内容格式。
同 SHA256 返回已存在对象，不强制转换复制模式、不重复创建 Edition。

数据库位于 `data/book_distiller.sqlite3`；manifest 在 `library/<slug>/manifest.json`。
业务 UUID 与 slug 分离。新 ingest 记录已完成 Task，不创建 AI Run。
出现未索引 manifest、临时目录残留或 schema version 不兼容时保留现场并报告错误，
不要自动删除、篡改版本或声称已恢复。实现边界见根目录 `docs/adr/ADR-006-library-persistence.md`。

测试使用原创 `tests/fixtures/` 与临时目录。人工 CLI 测试通过 `BOOK_DISTILLER_HOME` 指定隔离数据根，
不自动导入私人 inbox 书籍。环境未初始化时使用 `./setup.sh`；缺少 Python 3.12 时报告安装建议。

V1：Docling 主解析器（Phase 2）；MinerU 可选独立环境（Phase 11）；Parser 输出转自主 Canonical Model；
Filesystem + SQLite；未来 JSON Knowledge Model、Codex 推理、Static HTML + Jinja2。
当前不提前实现 Parser、AI、知识层级、Citation、Quality Engine、HTML、队列调度或恢复业务。

后续 workflow 预留在根目录 `workflows/`；提示、Schema、规则位于 `prompts/`、`schemas/`、`rules/`。
明确进入相应 Phase 后才扩展；按需在 `references/` 添加说明链接。当前不含完整蒸馏 Prompt。
