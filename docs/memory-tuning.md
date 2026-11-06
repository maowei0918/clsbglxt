# 内存与并发调优指南（clglxt）

目标：在 **4 GB 内存** 机器上稳定支撑 **100+ 并发用户**，兼顾日检照片上传压缩。

---

## 一、内存账（4 GB 机器，并发 100+）

| 进程 | 配置 | RSS |
|---|---|---|
| gunicorn master + 4 worker | `workers=4`, UvicornWorker | ~600 MB |
| Celery worker（threads 池） | `-P threads -c 4` | ~250 MB |
| Celery beat | 单进程 | ~80 MB |
| PostgreSQL | `shared_buffers=1GB`, `max_connections=200` | ~1.2 GB |
| Redis | `maxmemory=256mb` + LRU | ~300 MB |
| Nginx + 系统 | - | ~200 MB |
| **合计** | - | **~2.6 GB** |

**2 GB 机器**：gunicorn 2 worker、`shared_buffers=256MB`、Redis 128mb、`DB_POOL_SIZE=5`、`CELERY_CONCURRENCY=2` → ~1.4 GB。

---

## 二、按机器规格一键切档

### 4 GB / 4 核（推荐，并发 100+）

```bash
# .env
CLGLXT_DB_POOL_SIZE=15
CLGLXT_DB_MAX_OVERFLOW=20
CLGLXT_CELERY_WORKER_CONCURRENCY=4
```

```bash
# gunicorn.conf.py
workers = 4
worker_class = "uvicorn.workers.UvicornWorker"
worker_connections = 100
```

```ini
# PostgreSQL（init-postgres.sh 已追加）
max_connections = 200
shared_buffers = 1GB
work_mem = 16MB
```

```ini
# Redis（deploy/redis-clglxt.conf）
maxmemory 256mb
maxmemory-policy allkeys-lru
```

### 2 GB / 2 核（轻量）

```bash
# gunicorn.conf.py
workers = 2
# .env
CLGLXT_DB_POOL_SIZE=5
CLGLXT_DB_MAX_OVERFLOW=10
CLGLXT_CELERY_WORKER_CONCURRENCY=2
```

```ini
# PostgreSQL
max_connections = 80
shared_buffers = 256MB
work_mem = 8MB
```

```ini
# Redis
maxmemory 128mb
maxmemory-policy allkeys-lru
```

---

## 三、并发 100+ 关键配置

### 1. 限流（Redis 滑动窗口）

`app/core/redis_client.py` 内置 `rate_limit()` Lua 滑动窗口。

建议阈值（可在 .env 调整）：

| 端点 | 阈值 | 说明 |
|---|---|---|
| `/api/auth/login` | 5 次 / 分钟 / IP | 防爆破 |
| `/api/daily-check/photos` | 30 次 / 分钟 / 用户 | 防批量上传 |
| `/api/repairs` 等写操作 | 60 次 / 分钟 / 用户 | 通用 |

Redis 挂了 → `rate_limit()` 自动降级为放行（不误杀），稳定性优先。

### 2. DB 连接池（SQLAlchemy）

- `DB_POOL_SIZE` = 常驻连接数（每个 worker 独立池）
- `DB_MAX_OVERFLOW` = 峰值可扩张
- **总连接上限** = `workers × (pool_size + max_overflow)` ≤ `PG max_connections × 0.8`
- 4 worker × (15+20) = 140 ≤ 200 × 0.8 = 160 ✅

### 3. 上传限流（防内存溢出）

- 单张原图 ≤ `CLGLXT_MAX_UPLOAD_MB`（默认 20 MB）
- 服务端压缩后通常 200–1000 KB
- Nginx `client_max_body_size 25m`（留余量）
- 单用户并发上传 ≤ 10 张（前端排队）

### 4. 任务队列拆分

`celery_app.py` 已按 `default / backup / ai / mail / notify` 拆队列。

备份重 I/O 任务单独队列，避免阻塞业务 API。

---

## 四、Redis 持久化策略（生产）

**默认推荐**：`appendonly yes` + `save ""`（每秒 fsync）。

```ini
appendonly yes
appendfsync everysec
auto-aof-rewrite-percentage 100
auto-aof-rewrite-min-size 64mb
```

- 备份/队列数据可丢（beat 会补触发）
- 站内通知走 PG，不依赖 Redis
- 断线重连后任务不丢（acks_late + reject_on_worker_lost）

若要更稳（缓存可接受丢失）：

```ini
save ""
appendonly no
```

→ 纯内存，重启清空缓存/队列，beat 重新触发。

---

## 五、Nginx 调优（反向代理 + 静态文件）

```nginx
# /etc/nginx/conf.d/clglxt.conf
server {
    listen 80;
    server_name clglxt.example.com;

    client_max_body_size 25m;          # 留余量给 multipart 开销
    client_body_buffer_size 1m;        # 小文件内存缓冲
    client_body_timeout 60s;           # 照片上传更从容

    location /api/ {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_read_timeout 120s;       # 照片压缩慢请求
    }

    location / {
        root /www/wwwroot/clglxt/server/data/web;
        try_files $uri $uri/ /index.html;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }
}
```

---

## 六、照片上传 & 压缩

### 流程

```
┌──────────────┐    压缩前      ┌──────────────┐    压缩后     ┌───────────┐
| APP  WebView | ──(原生压缩)──→ | 上传端点     │ ──(压缩)──→ | 磁盘/PG   |
| (Android)    │   质量 80%     | /photos/     │             |           |
└──────────────┘                └──────────────┘             └───────────┘
       ↑                              ↑
  节省流量+快传                  服务端兜底：长边 ≤ 1920、JPEG 78%
```

### APP 端压缩（Kotlin）

```kotlin
// MainActivity.kt 中的 JS 桥
@JavascriptInterface
fun compressImage(base64: String, quality: Int = 80, maxDim: Int = 1920): String {
    val bytes = Base64.decode(base64, Base64.DEFAULT)
    val bmp = BitmapFactory.decodeByteArray(bytes, 0, bytes.size)
    val (w, h) = if (bmp.width > bmp.height)
        maxDim to (bmp.height * maxDim / bmp.width)
    else (bmp.width * maxDim / bmp.height) to maxDim
    val out = Bitmap.createScaledBitmap(bmp, w, h, true)
    val bos = ByteArrayOutputStream()
    out.compress(Bitmap.CompressFormat.JPEG, quality, bos)
    return Base64.encodeToString(bos.toByteArray(), Base64.NO_WRAP)
}
```

> 网页端无原生压缩能力，由服务端兜底压缩（质量 78%，长边 ≤ 1920）。
> 实测 12 MP 原图 13 MB → 压缩后 ~950 KB（节省 93% 存储 + 带宽）。

### 服务端保护

- **MIME 白名单**：仅 `image/jpeg|png|webp|heic`
- **像素炸弹防御**：`Image.open(io.BytesIO(raw))` 前先读 ≥ 1MB 到内存
- **单用户限流**：30 张/分钟（Redis 滑动窗口，挂了自动降级）
- **单文件上限**：20 MB（`CLGLXT_MAX_UPLOAD_MB`）

---

## 七、监控 & 告警（建议）

```bash
# 内存使用
free -h
ps -o pid,rss,cmd -C python3 | head

# PG 连接数
sudo -u postgres psql -c "SELECT count(*) FROM pg_stat_activity;"

# Celery 任务堆积
celery -A app.celery_app inspect active_queues

# Nginx 状态
curl -s http://127.0.0.1/nginx_status 2>/dev/null | head
```

当 Redis / Celery 挂掉时：

- API 正常响应（降级放行）
- 缓存不命中 → 直接查 PG，慢但不挂
- 审计日志仍可写（直写 PG）
- 站内通知走 PG，邮件走 SMTP
