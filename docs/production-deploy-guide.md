# clglxt 生产部署与灰度上线指南

> 本文档定位：**执行手册 + 验收门禁 + 排错**，是 `deploy.md`（安装步骤）之上的"上线编排层"。
> 核心原则：**灰度上线，先验证再放量，任何一步不过都不进入下一阶段。**

---

## 〇、先理解一件事：CI 绿 ≠ 生产就绪

CI 跑的是 **SQLite 临时库 + 单进程**，只证明 Python 逻辑正确。
生产是 **PostgreSQL + Redis + gunicorn/celery 三件套 + Nginx + 前端构建**，这条链路 CI 一条都没覆盖。

| CI 已覆盖 | CI 未覆盖（必须人工验证） |
|---|---|
| 单元/集成测试（31 项，SQLite） | 真实 PG 迁移、连接池、并发抢单（CAS） |
| 变异自检（测试有效性） | Redis 队列、Celery worker/beat 实际跑任务 |
| 工作区污染兜底 | 前端 `npm run build`、Nginx 反代、HTTPS |
| | 照片上传压缩、缩略图、APP 拍照 |
| | 备份恢复演练、内存/磁盘实测、弱网 APP |

**结论**：代码已就绪，但"能部署"≠"可对用户开放"。按本指南分阶段走。

---

## 一、灰度上线三阶段

### 阶段 1：测试库验证（不对外）

目标：在测试库完整跑一遍，确认 PG + Redis + Celery 链路通。

```bash
# 1.1 环境准备（宝塔软件商店）
apt update && apt install -y python3-venv python3-dev build-essential libpq-dev postgresql-client
# 宝塔软件商店一键装：PostgreSQL 15+ / Redis 7+ / Nginx

# 1.2 自检
bash server/deploy/check-env.sh

# 1.3 建库 + Redis
sudo bash server/deploy/init-postgres.sh   # 建 clglxt 库/用户 + 调优
sudo bash server/deploy/init-redis.sh      # maxmemory + 禁用危险命令

# 1.4 一键部署（见 deploy.md 路径 B）
sudo bash server/deploy/deploy.sh
```

**阶段 1 验收门禁**（全过才进阶段 2）：

- [ ] `curl -fsS http://127.0.0.1:8000/api/healthz` 返回 `{"ok":true,"db":"ok","service":"clglxt"}`
- [ ] `curl -fsS http://127.0.0.1:8000/api/install/status` 返回 `installed:true`（跑过安装向导后）
- [ ] 三件套进程：`systemctl status clglxt-web clglxt-worker clglxt-beat` 均 active
- [ ] `ss -tlnp | grep 8000` gunicorn 在监听
- [ ] `journalctl -u clglxt-web -n 50` 无 ERROR
- [ ] PG 建表 45 张：`psql -U clglxt -d clglxt -c "\dt" | wc -l`

### 阶段 2：灰度发布（小范围用户）

目标：对少量真实用户开放，验证业务链路。

```bash
# 2.1 跑上线自检（已修复路径，见脚本顶部注释）
bash scripts/smoke.sh

# 2.2 并发压测（可选，建议至少做一次）
ab -n 200 -c 50 https://你的域名/api/healthz   # 无 5xx

# 2.3 业务冒烟（人工，按 go-live-checklist 第 6 节）
# 登录→强制改密→建部门/类型/汽修厂→建车辆→提报→抢单→报价→推进
```

**阶段 2 验收门禁**（核心业务逐项打勾）：

- [ ] 登录 → 首次强制改密 → 旧密码失效
- [ ] 建部门 / 车辆类型 / 汽修厂
- [ ] 建车辆 → 出现在列表
- [ ] 提报维修单 → 抢单 → 报价 → 推进 → 列表可见
- [ ] **日检提报 + 照片上传**：
  - 网页端上传 5MB 原图 → 返回 size 明显变小（约 6-10%）
  - 缩略图 `?size=thumb` 正常加载
  - APP 端拍照 → 上传（Android 壳自动先压缩）
- [ ] 角色 / 人员创建 → 新账号登录权限正确
- [ ] **汽修厂账号只能看到指派单**（隔离生效）

### 阶段 3：全量开放（24h 观察）

```bash
# 上线后
df -h                                    # 磁盘无异常增长
systemctl status clglxt-*                # 三件套稳定
celery -A app.celery_app inspect scheduled   # 备份/清理任务在跑

# 24h 后复查 go-live-checklist 第 11 节
```

**阶段 3 验收门禁**：

- [ ] `df -h` 磁盘无异常增长
- [ ] 备份：管理后台 → 备份目标配置成功；手动触发一次 → `backup_runs.status=success`
- [ ] 备份产物含 `db.dump` + `uploads_*.tar.gz`
- [ ] beat 定时备份任务已注册
- [ ] **恢复演练**：在测试库实际恢复一次 pg_dump，确认可用
- [ ] `select count(*) from audit_logs` > 0（审计有写入）

---

## 二、安全门禁（上线前必须全过）

| 项 | 检查命令 | 期望 |
|---|---|---|
| JWT_SECRET 随机 32 字节 | `grep JWT_SECRET server/.env` | 64 位 hex，非默认值 |
| 关闭 /docs | `grep ENABLE_DOCS server/.env` | `false` |
| 关闭 DEBUG | `grep DEBUG server/.env` | `false` |
| .env 权限 | `ls -l server/.env` | 0644 且 `chown www:www` |
| 防火墙 | 仅 80/443/22 | 8000 不对外 |

> 安装向导自动生成随机 JWT_SECRET，但**部署后务必重启一次站点**让 `.env` 生效。

---

## 三、排错手册

### 3.1 服务起不来 / 502

```bash
# 1. 看日志（最直接）
journalctl -u clglxt-web -n 100 --no-pager
journalctl -u clglxt-worker -n 100 --no-pager

# 2. 手动前台跑 gunicorn，暴露真实报错
cd /www/wwwroot/clglxt/server && source .venv/bin/activate
gunicorn app.main:app -c gunicorn.conf.py
```

常见原因：
- `ModuleNotFoundError`：venv 没激活 / 依赖没装 → `pip install -r requirements.txt`
- PG 连不上：检查 `.env` 的 `DATABASE_URL`、PG 是否启动、密码是否正确
- 端口冲突：`ss -tlnp | grep 8000`

### 3.2 迁移失败

```bash
cd /www/wwwroot/clglxt/server && source .venv/bin/activate
alembic -c alembic.ini current     # 当前版本
alembic -c alembic.ini upgrade head  # 升级
alembic -c alembic.ini history     # 历史
```

> `alembic.ini` 里的 `changeme` 是占位符，`env.py` 会用 `.env` 的 `DATABASE_URL` 覆盖，不用改。

### 3.3 安装向导卡住

- 确认 PG 用户有建表权限（安装向导用 `create_all` 建表）
- 确认 `data/` 目录可写：`chown -R www:www server/data`
- 看浏览器 Network 面板的接口报错 + `journalctl -u clglxt-web`

### 3.4 抢单并发异常

- 检查 PG 的 `quote_owner_id` 是否被二次覆盖（CAS 条件更新语义）
- 若出现并发窗口，确认 `DB_POOL_SIZE` 与 gunicorn workers 匹配（pool ≈ workers × 2）

### 3.5 照片上传/压缩问题

- 检查 `CLGLXT_MAX_UPLOAD_MB`（默认 20）
- Nginx `client_max_body_size 50m;`（宝塔站点配置里）
- 压缩参数：`CLGLXT_IMAGE_MAX_DIM=1920`、`CLGLXT_IMAGE_QUALITY=78`

### 3.6 健康检查失败

```bash
curl -fsS http://127.0.0.1:8000/api/healthz
# 返回 ok:false 说明 DB 连不上；检查 PG 状态 + .env
```

---

## 四、回滚

```bash
# 4.1 代码回滚（从备份目录）
sudo bash server/deploy/rollback.sh <备份目录>
# rollback.sh 会从 systemd WorkingDirectory 自动读取真实部署路径

# 4.2 数据库回滚（PG 逻辑备份）
gunzip -c <备份目录>/db/clglxt_YYYYMMDD.sql.gz | \
  psql -h 127.0.0.1 -U clglxt -d clglxt -f -

# 4.3 回滚后验证
systemctl status clglxt-web clglxt-worker clglxt-beat
curl -fsS https://你的域名/api/healthz
```

> **上线前必须做一次回滚演练**，确认 `rollback.sh` 在当前部署路径下能跑通。

---

## 五、上线后监控（建议）

- **进程**：`systemctl status clglxt-*` 三件套
- **日志**：`journalctl -u clglxt-web -f` / 宝塔日志
- **磁盘**：`df -h`（上传/备份增长）
- **内存**：`top`（gunicorn+celery+pg+redis 合计 < 3GB，4GB 档位）
- **健康检查**：`/api/healthz` 可挂监控探针
- **备份**：`backup_runs.status` 定时查

---

## 六、文档索引

| 文档 | 用途 |
|---|---|
| `deploy.md` | 安装步骤详解（路径 A 向导 / 路径 B 脚本） |
| `go-live-checklist.md` | 上线检查表（逐项打勾） |
| `memory-tuning.md` | 内存/并发档位调优 |
| `production-deploy-guide.md` | **本文档**：灰度上线 + 验收门禁 + 排错 + 回滚 |
| `audit-report-v2.md` | 代码审计报告 |
