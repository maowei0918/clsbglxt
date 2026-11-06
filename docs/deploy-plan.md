# clglxt 部署方案决策记录（方案 C 终稿）

> **本文只记录「为什么这么部署」的决策与取舍，不再重复操作步骤。**
> 具体怎么做，请直接看：
> - **首次安装**：`docs/deploy.md`（宝塔 + systemd + nginx 两条路径）
> - **灰度上线 / 验收门禁 / 回滚**：`docs/production-deploy-guide.md`
> - **逐项打勾核对**：`docs/go-live-checklist.md`

---

## 一、方案定位（决策理由）

| 决策 | 结论 | 理由 |
| --- | --- | --- |
| 技术架构 | PostgreSQL + Redis + Celery 全链路 | 目标日均维修单 100–1000，需要异步任务（备份/审计清理/图片压缩）与连接池 |
| 是否先复刻 SQLite | **否**，直接上 PG | 避免「先跑通再改造」带来的二次迁移与兼容坑；CAS 抢单等依赖真实 DB 语义 |
| 安装方式 | **浏览器安装向导（推荐）** | 像 WordPress：上传源码 → 访问域名 → 填 DB/Redis/管理员 → 一键初始化（建 45 表 + seed 内置角色 + 生成 JWT_SECRET + 写安装锁） |
| 数据迁移 | 通过老 FMS API 拉取导入 | `/import-fms` 占位，FMS 停机前实施；不走双写 |
| 依赖版本 | 宽松 `>=` 约束（`requirements.txt`） | 「能跑就行」，生产部署时按实测锁定 |

> 已知差异提醒：CI 全程跑 **SQLite + 单进程**，只验证 Python 逻辑；生产是
> **PG + Redis + Celery 三件套 + Nginx**，这条链路由 `production-deploy-guide.md`
> 的验收门禁覆盖，CI 不替代。

---

## 二、部署流程

**不再维护副本**，以 `docs/deploy.md` 为唯一操作来源。

- 路径 A（推荐）：源码上传 → 宝塔建库/建站 → `start.sh` 或 systemd → 安装向导
- 路径 B（脚本）：`init-postgres.sh` → `init-redis.sh` → 填 `.env` → `deploy.sh`

> ⚠️ 安装完成后**必须重启一次站点**让新 `.env` 生效，再访问 `/login`（首次强制改密）。

---

## 三、更新与回滚（上线后）

| 场景 | 命令 |
| --- | --- |
| 代码更新 | `sudo bash server/deploy/update.sh`（自动备份→迁移→重启→失败自动回滚） |
| 回滚代码 | `sudo bash server/deploy/rollback.sh <备份目录>`（默认**只回滚代码，不动 DB**） |
| 连 DB 一起回滚 | 上一条加 `--with-db`（破坏性，人工确认） |
| 上线自检 | `bash scripts/smoke.sh`（已修复路径定位，cd 到 `server/` 再跑 alembic） |

---

## 四、参考链接

- `docs/deploy.md` — 首次安装详解
- `docs/production-deploy-guide.md` — 灰度上线 / 验收门禁 / 排错 / 回滚
- `docs/go-live-checklist.md` — 上线检查表
- `docs/memory-tuning.md` — 内存/并发档位
- `docs/review-report-v1.md` / `docs/audit-report-v2.md` — 代码审查与安全修复清单
