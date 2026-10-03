# ADR-007：Canonical Document 与原子解析产物

状态：Accepted（Phase 2）

## 决定

DoclingAdapter 通过公开 DocumentConverter、DoclingDocument 导出/遍历 API 返回中立 ParserResult。
Normalizer 不 import Docling 类型；TXT 使用自有 UTF-8 段落读取器。
Raw 可通过 Adapter.load_raw 重新生成中立输入，预留重新 Normalize，无 normalize-only CLI。

Canonical schema 1.0 使用 book.json + blocks.jsonl。book.json 保存元数据、章节结构和特殊内容索引；
Block 文本与 SourceSpan 只保存于流式 JSONL，不在 book.json 重复整本正文。
特殊内容（图、表、公式、代码）仍为统一 Block，索引只存 Block ID、caption、可选 asset_path。
当前不导出图片资产、不解释图表或公式。

物理 PDF 页从 index 0 / number 1 计数；其他格式页码为空，印刷页码不猜测。
SourceSpan 的字符区间是规范化 Block 文本中的半开区间，无法安全映射时为空；原字符范围保留 metadata。
Parser locator 不是全局引用 ID。Edition 内 ID 确定性顺序分配，Parser/结构变化可能使 ID 改变。
最高有效 heading level 划分 Chapter，其他级别以父级栈形成 Section；标题前导文本或无 heading 使用 synthetic Chapter。

## 权威关系

- Book/Edition 标识、Task 状态：SQLite。manifest 中重复标识必须与其一致。
- 单本可移植 Source 元数据：manifest；当前索引仍保持 Phase 1 校验一致性。
- 原始 Parser 内容：parsed/raw。
- Canonical Document 与 Parse Quality：parsed/normalized。
- 完整解析代、缓存键与产物校验：parsed/completion.json，并要求对应 Task completed。

不扩展 manifest.parse，也不升级 SQLite schema；避免解析状态多处写入。
旧 manifest 1.0 无需迁移即可解析。Raw metadata 记录 Parser/版本/源 hash/时间/格式/警告/错误。
Normalized 元数据记录 raw schema、normalizer version、Parser provenance。

## 发布与失败

准备 `.parse-staging-<task>`，写 JSON/JSONL 并校验，生成完整性摘要，重检 Source SHA256。
将其变成不可变 `.parsed-generations/<task>`，原子替换相对 symlink `parsed`，使 raw/normalized 同时切换。
Task 完成写入失败时回滚指针；普通 Parser/Normalizer 失败不动旧成功结果。
保留旧代和失败 JSON 以供检查，不提供 Snapshot、自动恢复或清理业务。
强制终止后可能残留 running Task、临时目录或未完成发布；这些不能成为缓存成功命中。

## 原因与边界

流式 Block 避免长书的多份巨型 JSON；单一原子指针避免分别替换两个目录时混用新旧结果。
仅保证本机遵守 Library 锁的进程间协调，不声称 SQLite 与文件系统具有单一事务。
质量只做可解释 Parse Quality，不执行 Citation Verify 或完整 Quality Engine。
PDF 使用标准 Docling 布局/表格模型，默认不启用 OCR、VLM、远程服务或 AI 蒸馏。
MinerU 回退、语义 Chunk、知识层级、RAG、HTML 等仍不实现。
