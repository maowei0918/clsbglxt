# clglxt 生产部署文档

> 两条路径：**A. 浏览器安装向导（推荐，像 WordPress）** / **B. 脚本自动化部署**

---

## 路径 A：源码上传 + 安装向导（推荐）

### 1. 宝塔软件商店一键装
- PostgreSQL 15+ （记下端口、postgres 密码）
- Redis 7+
- Nginx（宝塔自带）
- Python 3.11+：`apt install -y python3-venv python3-dev build-essential`

### 2. 宝塔建库
数据库 → PostgreSQL → 添加数据库
```
库名: clglxt
用户: clglxt
密码: <强密码>
```

### 3. 上传源码 + 装依赖
```bash
mkdir -p /www/wwwroot/clglxt && cd /www/wwwroot/clglxt
# 上传 server/ frontend/ client/ docs/ 整个项目目录
python3 -m venv .venv
source .venv/bin/activate
pip install -r server/requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
```

### 4. 宝塔建站点
- 站点 → 添加站点，PHP 版本选「纯静态」
- 域名绑定你的域名，开启 SSL
- **反向代理** → 目标 URL `http://127.0.0.1:8000`，提交

或用 `server/deploy/nginx-clglxt.conf` 手动配置（已含限流与安全头）。

### 5. 启动后端
宝塔「Python 项目」新建项目，或用：
```bash
bash /www/wwwroot/clglxt/server/start.sh
```
systemd 方式：
```bash
cp server/deploy/clglxt-web.service      /etc/systemd/system/
cp server/deploy/clglxt-worker.service   /etc/systemd/system/
cp server/deploy/clglxt-beat.service     /etc/systemd/system/
systemctl daemon-reload && systemctl enable --now clglxt-web clglxt-worker clglxt-beat
```

### 6. 访问域名 → 安装向导
浏览器打开 `https://你的域名/` → 自动跳 `/install`

1. **数据库**：地址 `127.0.0.1`、端口 `5432`、库 `clglxt`、用户 `clglxt`、密码 → 点「测试连接」
2. **Redis**：`redis://127.0.0.1:6379/0`
3. **站点**：站点名称、管理员用户名、密码（8 位以上，含字母数字）
4. 点「开始安装」

安装完成后：
- 自动建 45 张表
- seed 3 个内置角色（内部管理员/汽修厂管理员/超管）+ 管理员 + 超管通配权限
- 生成 32 字节 JWT_SECRET 写入 `.env`
- 写 `data/.install_done` 锁

> **必须重启一次站点**（宝塔：站点 → 重启 / `systemctl restart clglxt-web`），
> 让新 `.env` 生效，然后 `https://域名/login` 登录。首次登录强制改密。

### 7. 验证
```bash
curl -fsS https://你的域名/api/healthz
curl -fsS https://你的域名/api/install/status   # 应返回 installed=true
```

---

## 路径 B：脚本自动化部署

1. **初始化 PG**：`sudo bash server/deploy/init-postgres.sh`（建库建用户 + 调优）
2. **初始化 Redis**：`sudo bash server/deploy/init-redis.sh`（maxmemory 1gb、禁用 FLUSHALL/FLUSHDB/CONFIG）
3. **填 .env**：`cp server/.env.production.example server/.env && vim server/.env`
   - 必填：`CLGLXT_JWT_SECRET`（`openssl rand -hex 32`）、`CLGLXT_DATABASE_URL`、Redis URL
4. **一键部署**：`sudo bash server/deploy/deploy.sh`
   - 建目录 / venv / 装依赖 / 拷 .env / `alembic upgrade head` / 写 systemd / 重启服务
5. **手动建管理员**（脚本路径不走安装向导）：
   ```bash
   cd /www/wwwroot/clglxt/server && source .venv/bin/activate
   python -c "from app.core.database import SessionLocal; from app.core.seed import seed; db=SessionLocal(); seed(db, org_name='青岛车队', dept_name='车队'); db.close()"
   ```
6. **建标记**：`touch data/.install_done`

---

## 目录约定
```
/www/wwwroot/clglxt/server    后端代码
/www/wwwroot/clglxt/server/data   上传文件 + .install_done 安装锁
/www/wwwroot/clglxt/frontend  前端源码（方案 B）
/var/log/clglxt/              日志
```

## 服务管理
```bash
systemctl status clglxt-web clglxt-worker clglxt-beat
journalctl -u clglxt-web -f      # Web 日志
journalctl -u clglxt-worker -f   # 后台任务日志
```

## 升级流程
1. 停服：`systemctl stop clglxt-web clglxt-worker clglxt-beat`
2. 备份：`pg_dump clglxt | gzip > db-$(date +%F).sql.gz` + `cp -a server server.bak`
3. 拉新代码
4. 跑迁移：`alembic upgrade head`
5. 起服：`systemctl start clglxt-web clglxt-worker clglxt-beat`

## 回滚
`bash server/deploy/rollback.sh /var/backups/clglxt`

## 安全要点
- JWT_SECRET 64 位 hex，绝不入库 / 入版本库
- `.env` 权限 0644（需运行用户可读），或 0640 + 属组 www
- Redis 禁用 FLUSHALL/FLUSHDB/CONFIG
- Nginx 限流：登录 5 次/分、API 30 次/秒
- 上传目录禁止执行脚本
- 安装完成后 `/install` 与安装 API 自动 403

## 性能调优（按机器）
| 内存 | PG shared_buffers | Redis maxmemory | gunicorn workers |
|---|---|---|---|
| 4 GB | 1 GB | 1 GB | 6 |
| 8 GB | 2 GB | 2 GB | 12 |
| 16 GB | 4 GB | 3 GB | 16 |

## 故障排查
| 现象 | 排查 |
|---|---|
| 502 Bad Gateway | gunicorn 没起 → `journalctl -u clglxt-web` |
| 一直跳 /install | `data/.install_done` 不存在 → 重新走安装向导 |
| 装完登录报「表不存在」 | 忘了重启站点，`.env` 未生效 |
| 连接 PG 失败 | 检查 `CLGLXT_DATABASE_URL`、`pg_hba.conf` 是否放行 127.0.0.1 |
| 限流误伤 | 删 Redis `login:*` 键，或调 Nginx `limit_req` |
| AI 审核失败 | Celery broker 未连接 → `journalctl -u clglxt-worker` |
| 写 .env 失败 | 站点目录属主不是 www → `chown -R www:www /www/wwwroot/clglxt` |
