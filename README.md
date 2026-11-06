# clglxt 车辆维修管理系统

基于 **PostgreSQL + Redis + Celery + Vue 3** 的车辆维修管理系统，替代原 FMS。
定位：日均维修单 100–1000，支持更高并发。

---

## 一、快速开始（宝塔部署）

### 三步上线

```bash
# 1. 上传源码到宝塔站点目录（如 /www/wwwroot/clglxt）
#    把 server/ 整个目录传上去即可

# 2. 宝塔 → 网站 → Python 项目 → 添加项目
#    启动命令：cd /www/wwwroot/clglxt/server && bash start.sh

# 3. 浏览器访问你的域名 → 自动跳转安装向导
#    填数据库信息 + 管理员账号 → 一键初始化 → 进系统
```

安装向导会自动完成：建表（45 张）、建内置角色、写 `.env`、生成 JWT 密钥。

**前置依赖**（宝塔软件商店一键装）：

- PostgreSQL 15+
- Redis 7+
- Python 3.11+（含 `python3-venv`）

系统层还需（宝塔终端执行一次）：

```bash
apt update && apt install -y python3-venv python3-dev build-essential libpq-dev postgresql-client
```

> `postgresql-client` 提供 `pg_dump`，备份功能必需。环境自检一条命令：
> `python3 -V; psql --version; pg_dump --version; redis-cli --version; nginx -v`

**内存/并发档位**（详见 `docs/memory-tuning.md`）：

| 机器 | 常驻内存 | 关键参数 |
|---|---|---|
| 2 GB | ~1.1 GB | 1 worker · PG shared_buffers 256MB · pool 5 |
| 4 GB（推荐） | ~1.9 GB | 2 worker · shared_buffers 1GB · pool 10 · 并发 100+ |
| 8 GB | ~3 GB | 4 worker · shared_buffers 2GB · pool 20 · 并发 300+ |

### 安装完成后

```bash
# 重启站点使 .env 生效（宝塔：站点 → 重启）
# 或命令行：
systemctl restart clglxt-web clglxt-worker clglxt-beat
```

访问 `https://你的域名/` → 登录页 → 用安装时设的管理员账号登录（首次登录强制改密）。

---

## 二、日检照片上传（APP + 网页）

| 端 | 处理方式 |
|---|---|
| **安卓 APP** | 拍照后在端内压缩（Kotlin Bitmap，长边 ≤1920、质量 78），只传压缩后的 JPEG。 |
| **网页端** | 浏览器 canvas 预处理 + 服务端 Pillow 二次压缩兜底。 |
| **服务端** | 统一压缩（Pillow 线程池），`CLGLXT_IMAGE_MAX_DIM` / `CLGLXT_IMAGE_QUALITY` 可调。 |

照片经 `POST /api/daily-check/photos`（multipart）或 `POST /api/daily-check/photos/raw`（APP 原生 body）上传，返回 `id/url/thumb_url`。上传端点带 Bearer 鉴权 + MIME/扩展名双重校验 + EXIF 剥离（防隐私泄露）。缩略图用 `?size=thumb` 实时生成 400px。

**压缩实测**：12MP(13.7MB) → 1920×1440 / 948KB（6%）；24MP(27.5MB) → 1920×1280 / 637KB。小图不放大。

> 并发保护：压缩是 CPU 密集操作，服务端走**线程池 + 信号量限流**（`min(8, CPU核数)`），100 并发上传不会阻塞事件循环。

---

## 三、技术栈

### 后端

| 项 | 选型 |
|---|---|
| Web 框架 | FastAPI |
| ORM | SQLAlchemy 2.x + Alembic |
| 数据库 | PostgreSQL 15+ |
| 缓存/队列 | Redis 7+ |
| 异步任务 | Celery（队列：default/backup/ai/mail/notify） |
| Web 服务器 | Gunicorn + UvicornWorker |
| 认证 | JWT（Bearer + HttpOnly Cookie） |

### 前端

| 项 | 选型 |
|---|---|
| 框架 | Vue 3（Composition API） |
| 构建 | Vite |
| UI | Element Plus |
| 状态 | Pinia |
| HTTP | Axios |

### 安卓 APP

原生 Android WebView 壳（Kotlin + Gradle），加载站点 Web 前端，支持离线提示与返回键处理。

---

## 四、目录结构

```
clglxt/
├── server/                      # Python 后端
│   ├── app/
│   │   ├── core/               # config/database/redis/security/deps/audit/seed
│   │   │   ├── backup_engine.py    # 备份引擎（pg_dump / SQLite）
│   │   │   └── notify.py           # 通知（站内 + 邮件）
│   │   ├── modules/            # 16 个业务模块
│   │   │   ├── auth.py             # 登录/改密/me
│   │   │   ├── system.py           # 角色权限
│   │   │   ├── personnel.py        # 人员
│   │   │   ├── departments.py      # 部门
│   │   │   ├── vehicles.py         # 车辆/类型/性质
│   │   │   ├── contracts.py        # 合同
│   │   │   ├── repairs.py          # 维修单列表/建单
│   │   │   ├── repairs2.py         # 详情/推进/报价/取消
│   │   │   ├── daily_check.py      # 日检
│   │   │   ├── suppliers.py        # 汽修厂
│   │   │   ├── backup.py           # 备份目标/任务
│   │   │   ├── import_fms.py       # FMS 数据导入（预留）
│   │   │   └── ...
│   │   ├── workers/            # Celery 任务
│   │   │   ├── backup_tasks.py
│   │   │   ├── ai_tasks.py
│   │   │   ├── mail_tasks.py
│   │   │   └── scheduled_tasks.py  # beat 定时
│   │   ├── installer.py        # 安装向导（服务端 HTML + API）
│   │   ├── models.py           # 45 张表
│   │   ├── celery_app.py
│   │   └── main.py
│   ├── alembic/                # 数据库迁移
│   ├── deploy/                 # 部署脚本
│   ├── tests/test_e2e.py       # 全链路集成测试
│   ├── start.sh                # 站点启动脚本
│   ├── gunicorn.conf.py
│   └── requirements.txt
├── frontend/                    # Vue 3 前端
│   └── src/
│       ├── api/               # axios 客户端（auth/vehicles/repairs/system/...）
│       ├── views/             # 15 个页面
│       ├── stores/            # Pinia
│       ├── router/            # 路由 + 守卫
│       └── layouts/
├── android/                     # 安卓 APP（Kotlin WebView 壳）
└── docs/                        # 文档
```

---

## 五、核心设计

### 与老 FMS 的关键差异

| 项 | FMS | clglxt |
|---|---|---|
| 数据库 | SQLite | PostgreSQL 15 |
| 缓存 | 无 | Redis（限频/权限缓存） |
| 异步 | APScheduler 同步 | Celery + Redis |
| 备份 | SQLite backup API | pg_dump + 附件打包 |
| 单号生成 | 内存计数器（高并发重号） | `seq_counters` 表原子取号 |
| 并发控制 | CAS | `SELECT FOR UPDATE` + seen_seq |
| 飞牛适配 | Cookie+Bearer 双通道 | 单 Token 模式 |

### 关键实现点

- **单号原子取号**：PG 用 `INSERT ... ON CONFLICT DO UPDATE ... RETURNING`，并发不重号
- **流程推进并发控制**：`with_for_update()` 行锁 + `seen_seq` CAS，冲突返回 409
- **软删除**：`EPOCH = datetime(1970,1,1)` 哨兵，`alive()` 用 `like("1970-01-01%")`
- **权限**：服务端为唯一信任边界，`require_perm()` 兜底；前端隐藏菜单只是体验层
- **可见性隔离**：超管全看 / 汽修厂只看指派单 / 内部按部门或 `data:scope_all`

---

## 六、数据表（45 张）

业务 41 张 + Che1 系列 4 张。核心表：

`departments` `suppliers` `vehicle_types` `vehicle_natures` `vehicles` `users` `roles`
`role_permissions` `user_roles` `contracts` `contract_items` `flow_templates`
`repair_orders` `repair_actions` `repair_quotes` `repair_quote_items` `attachments`
`daily_check_templates` `daily_check_records` `audit_logs` `app_settings`
`backup_targets` `backup_jobs` `backup_runs` `backup_items` `notifications`
`seq_counters` `fence_points` `fence_alerts` `login_throttle` `ai_usage` `api_keys`
`che1_vehicles` `che1_usage_records` `che1_fuel_records` `che1_sync_runs`

---

## 七、开发

```bash
# 后端
cd server
python3 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
uvicorn app.main:app --reload --port 8000

# 前端
cd frontend
npm install
npm run dev          # http://localhost:5173（已配 /api 代理）
npm run build        # 产物直出 server/data/web/
```

### 测试

统一入口（历史脚本式测试已由 `tests/test_scripts_regression.py` 隔离纳入）：

```bash
cd server
source .venv/bin/activate        # 需先装 pytest: pip install pytest

pytest tests/ -q                 # 全量回归（31 项）
python scripts/mutation_check.py # 变异自检：验证测试本身抓得住缺陷
```

> 测试全程使用临时 SQLite + 临时数据目录，**不需要** PostgreSQL/Redis，
> 也不会读写生产 `.env`（有契约测试守护）。

#### CI（GitHub Actions）

`.github/workflows/ci.yml` 在 push / PR 时自动执行：

1. **全量回归** `pytest tests/ -q`
2. **变异自检** `python scripts/mutation_check.py`（测试有效性）
3. **污染兜底** 校验生产代码已还原、无 `.env`、无安装残留

本地复现 CI 一条命令：

```bash
cd server && pytest tests/ -q && python scripts/mutation_check.py
```


---

## 八、文档

- `docs/deploy.md` — 生产部署（宝塔 + systemd + nginx）
- `docs/deploy-plan.md` — 部署方案与取舍
- `docs/go-live-checklist.md` — 上线检查表
- `docs/review-report-v1.md` — 代码审查报告

---

## 九、APP 与 PWA

### 安卓 APP（`android/`）

- **原生 WebView 壳**（Kotlin + Gradle 8.7，minSdk 24 / target 34）
- **拍照端内压缩**：长边 ≤1920、JPEG 78%，只传压缩后文件（12MP 13MB → ~950KB）
- **首次启动弹窗填站点地址**（内网 IP 或域名），之后记住，不用改代码重编译
- 返回键 SPA 内后退，退到底才退出
- 拍照 / 相册双选（FileProvider + content:// URI）

打包：

```bash
cd android && ./gradlew assembleRelease
# 产物：app/build/outputs/apk/release/app-release.apk
```

详见 `android/README.md`。

### PWA

前端内置 `manifest.json` + `sw.js`：

- 手机浏览器「添加到主屏幕」→ 类原生体验
- 静态资源 Cache First，导航 Network First（断网可用）
- API 一律不缓存（数据实时性优先），照片 Stale-While-Revalidate

### 前端分包（已优化）

`manualChunks` 把依赖拆成 4 个长缓存包：

| 包 | 体积 | 说明 |
|---|---|---|
| `vendor-element` | 961 KB (gzip 295 KB) | Element Plus，很少变 |
| `vendor-vue` | 127 KB (gzip 49 KB) | Vue 全家桶 |
| `vendor` | 115 KB (gzip 41 KB) | 其他依赖 |
| `vendor-http` | 59 KB (gzip 22 KB) | Axios |
| `index`（业务） | **8.4 KB** | 业务代码，改版只重下这 8KB |

> 优化前单个主包 1.27 MB，每次发版用户都要重下；现在只需 8.4 KB。
