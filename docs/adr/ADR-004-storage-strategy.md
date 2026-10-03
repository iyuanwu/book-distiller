# ADR-004：Storage strategy

状态：Accepted（V1 架构决定）

## 决定

Files + SQLite。Knowledge Model 主体 = JSON；SQLite 负责索引、状态、运行记录。

## 原因

文件便于查看与迁移；SQLite 适合本地结构化查询。

## V1 边界

Phase 0 只建立数据目录和公共模型；不建业务表、不实现备份与 Bundle。
