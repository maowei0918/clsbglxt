# 上线检查表（go-live checklist）

> 逐项打勾，全部 ✅ 后方可对真实用户开放。
> 用法：`bash scripts/smoke.sh` 跑自动部分；人工部分按本表核对。

---

## 1. 服务器环境

- [ ] Debian 11/12 x86_64，**≥4GB 内存 / ≥100GB SSD**
- [ ] 宝塔面板已装（软件商店一键装）：**Python 3.11+、PostgreSQL 15+、Redis 7+、Nginx 1.22+**
- [ ] 系统包齐全：`python3-venv python3-dev build-essential libpq-dev postgresql-client`
- [ ] 自检命令 5 条全部出版本号：
  ```bash
  python3 -V; psql --version; pg_dump --version; redis-cli --version; nginx -v; python3 -c "import venv"
  ```

## 2. 数据库与缓存

- [ ] 执行 `bash server/deploy/init-postgres.sh`，记录输出的数据库密码
- [ ] `pg_isready` 返回 accepting connections
- [ ] `redis-cli ping` 返回 PONG
- [ ] Redis 已按 docs/memory-tuning.md 配置 maxmemory + LRU

## 3. 应用部署

- [ ] 源码上传至 `/www/wwwroot/clglxt/`（server 目录）
- [ ] `cd server && python3 -m venv .venv && source .venv/bin/activate`
- [ ] `pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple`
- [ ] `alembic upgrade head` 成功（45 张表）
- [ ] 浏览器访问域名 → 自动跳 `/install` 安装向导
- [ ] 安装向导完成：填 PG 信息 + 管理员账号 + 部门 → 提示重启
- [ ] `data/.install_done` 存在、`.env` 生成且权限 0644
- [ ] 重启站点后 `/login` 可用、首次登录强制改密

## 4. 服务进程（三件套）

- [ ] `systemctl status clglxt-web` active (running)
- [ ] `systemctl status clglxt-worker` active (running)
- [ ] `systemctl status clglxt-beat` active (running)
- [ ] `ss -tlnp | grep 8000` gunicorn 在监听
- [ ] `journalctl -u clglxt-web -n 50` 无 ERROR

## 5. Nginx 反向代理

- [ ] `nginx -t` 通过（**沙箱无法验证，宝塔里保存配置会自动 -t**）
- [ ] `client_max_body_size 50m;`（照片上传必需）
- [ ] `proxy_read_timeout 300;`（大图压缩耗时）
- [ ] HTTPS 证书已签（宝塔一键 Let's Encrypt）
- [ ] HTTP 自动跳 HTTPS

## 6. 核心业务流程（人工点一遍）

- [ ] 登录 → 首次强制改密 → 旧密码失效
- [ ] 建部门 / 车辆类型 / 汽修厂
- [ ] 建车辆 → 出现在列表
- [ ] 提报维修单 → 抢单 → 报价 → 推进 → 列表可见
- [ ] **日检提报 + 照片上传**：
  - 网页端上传 5MB 原图 → 返回 size 明显变小（约 6-10%）
  - 缩略图 `?size=thumb` 正常加载
  - APP 端拍照 → 上传（Android 壳自动先压缩）
- [ ] 角色 / 人员创建 → 新账号登录权限正确
- [ ] 汽修厂账号只能看到指派单（隔离生效）

## 7. 稳定性 & 并发

- [ ] `bash scripts/smoke.sh` 全绿
- [ ] 并发验证（可选）：`ab -n 200 -c 50 $BASE_URL/healthz` 无 5xx
- [ ] gunicorn 4 worker、`DB_POOL_SIZE=15`、`CELERY_CONCURRENCY=4`（并发 100+ 档）
- [ ] `CLGLXT_MAX_UPLOAD_MB=20`、`CLGLXT_IMAGE_MAX_DIM=1920`、`CLGLXT_IMAGE_QUALITY=78`
- [ ] 内存占用符合预期（top 看 gunicorn+celery+pg+redis 合计 < 3GB）

## 8. 备份与恢复

- [ ] 备份目标配置成功（管理后台 → 备份）
- [ ] 手动触发一次备份 → `backup_runs.status = success`
- [ ] 备份产物包含 `db.dump` + `uploads_*.tar.gz`
- [ ] beat 定时备份任务已注册（`celery -A app.celery_app inspect scheduled`）
- [ ] **恢复演练**：在测试库实际恢复一次 pg_dump，确认可用

## 9. 安全

- [ ] JWT_SECRET 为随机 32 字节（安装向导自动生成）
- [ ] `CLGLXT_ENABLE_DOCS=false`（生产关闭 /docs）
- [ ] `CLGLXT_DEBUG=false`
- [ ] `.env` 权限 0644 且 `chown www:www`
- [ ] 管理员密码非弱口令（首次强制改密已生效）
- [ ] 审计日志有写入（`select count(*) from audit_logs` > 0）
- [ ] 防火墙仅开放 80/443/22，8000 不对外

## 10. 移动端

- [ ] 手机浏览器访问 → 可登录可提报（PWA 可用）
- [ ] 「添加到主屏幕」→ 图标 + 全屏模式
- [ ] 安卓 APK 安装 → 登录 → 拍照上传（原生压缩生效）
- [ ] 弱网 / 离线时：页面有提示，不崩溃

## 11. 上线后 24h 观察

- [ ] `df -h` 磁盘无异常增长
- [ ] `free -m` 内存无持续上涨（怀疑泄漏）
- [ ] `tail -f /var/log/clglxt/gunicorn.error.log` 无刷屏异常
- [ ] 日检照片量符合预期（每周 du -sh data/uploads）
- [ ] 备份任务 03:15 自动跑过一次

---

**全部打勾后**：将本文档归档到 `docs/go-live-signoff-YYYYMMDD.md`，记录上线时间和值班人。
