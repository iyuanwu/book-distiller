# ADR-003：Codex as reasoning engine

状态：Accepted（V1 架构决定）

## 决定

V1 AI reasoning = Codex；No extra model API required。未来允许 Provider Adapter。

## 原因

复用 Codex 交互能力，避免在基线引入额外模型客户端与凭据管理。

## V1 边界

Phase 0 不调用 AI，不实现 Prompt Pipeline 或 Provider Adapter。
