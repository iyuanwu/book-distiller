# ADR-001：Parser architecture

状态：Accepted（V1 架构决定）

## 决定

Docling = Primary Parser；MinerU = Optional Fallback，运行于独立环境。Parser output → Book Distiller Canonical Model。

## 原因

统一核心输入，同时隔离可选解析器依赖冲突。

## V1 边界

Phase 2 接入 Docling；Phase 11 接入 MinerU。Phase 0 不安装解析器、不解析 PDF/OCR。
