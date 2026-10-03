# ADR-002：Canonical data model

状态：Accepted（V1 架构决定）

## 决定

第三方 Parser 数据格式不得成为核心模型；所有 Parser 必须转换为 Book Distiller 自己的 Canonical Model。

## 原因

避免核心、知识提炼与渲染被供应商格式绑定。

## V1 边界

JSON 为主要数据表达；Phase 0 仅定义最小公共记录，不提前设计 NormalizedBook 或 Citation。

## Phase 2 落地

已实现自主 NormalizedBook / Chapter / Section / Block / SourceSpan，版本 1.0。
上层只能使用该 Canonical Model；docling.json/md 仅供调试、重建和迁移。
具体存储、ID 和来源映射规则见 ADR-007-normalized-document-storage.md。
