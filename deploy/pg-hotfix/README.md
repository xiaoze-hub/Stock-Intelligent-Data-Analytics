# PG 迁移热修集 (生产现状固化, 2026-08-30)

migrations.py 是 v0.4.18 镜像版 + Hermes 对 24 个 SQLite-only 迁移的
`return  # PG hotfix` 处置(在 PostgreSQL 上跳过建表/改表语句)。

- 用途: 小主机(panwatch-tqfix 镜像)冷启动。生产 PG 库 schema_migrations
  中这些版本的 checksum 即此文件产物, 换任何其他版本都会触发重跑并在 PG 上崩溃。
- 注意: 仓库 src/web/migrations.py 是更新的开发版, 两者不同, 勿互相覆盖。
- 正式修复: 把这 24 个迁移按 `_dialect_is_pg()` 双分支方言化(SQLite 用
  AUTOINCREMENT, PG 用 BIGSERIAL), 下一版镜像发布时替换本目录。
