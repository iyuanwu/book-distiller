# ADR-006：Library 持久化与导入一致性

状态：Accepted（Phase 1）

## 决定

- 全局索引位于 `data/book_distiller.sqlite3`，schema version 从 1 开始；独立于单本 Library。
- 保留 Phase 0 的 UUID 业务 ID，不使用自增主键，不引入另一套带前缀 ID；slug 只是可读路径和选择器。
- manifest version 从 1.0 开始，Pydantic 校验。book.title 对应 SQLite canonical_title。
- Library 内复制路径相对单本目录；reference 存储原文件绝对路径。
- SHA256 在 editions 上唯一。首次导入创建 Book、Edition、completed ingest Task；runs 表不自动产生记录。
- Filesystem 锁协调本地进程，SQLite BEGIN IMMEDIATE 提供事务；临时目录准备、事务内写索引、rename 发布、提交。
- 普通异常在锁内回滚本次目录与数据库，保留所有已存在对象。
- 无法把 SQLite 与文件系统变成单一原子提交。被杀进程留下 staging/未索引 manifest 时，下次操作明确报错并保留现场供人工核对。

## 原因

稳定标识、可移植 manifest 与全局索引分离，为未来迁移、Backup 和 Book Bundle 保留基础。
标准库 sqlite3、fcntl、hashlib 足以支持本地单机需求，无需 ORM、队列或分布式事务。
先准备可避免未完成文件进入有效索引；异常回滚和残留检测避免静默产生不可用记录。

## V1 边界

只建 Book/Edition/Run/Task 持久化，不调度队列、不执行 Run，不解析正文、不实现知识模型。
不自动合并不同版本到同一本 Canonical Book。基础模型保留 Phase 0 字段兼容性。
并发保障适用于使用同一数据根目录和本实现锁的进程；不承诺对外部手工 SQL/文件写入提供事务隔离。
不实现自动中断恢复、索引重建、Snapshot、Backup。未知数据库版本拒绝自动迁移。
`BOOK_DISTILLER_HOME` 用于显式指定独立数据根目录，测试不得污染默认 Library。
