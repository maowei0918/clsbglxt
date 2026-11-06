# ============================================================
# gunicorn 生产启动配置
# 放到 /etc/gunicorn.d/clglxt.py
# ============================================================

# 绑定端口/主机（宝塔 Nginx 反代时可改成 0.0.0.0:8000）
bind = "127.0.0.1:8000"
# bind = "unix:/var/run/clglxt/gunicorn.sock"  # 生产更推荐用 socket

# 轻量配置：2 worker（4G 内存友好）
workers = 2
worker_class = "uvicorn.workers.UvicornWorker"
# worker_class = "gunicorn.workers.SyncWorker"

# 每个 worker 最大并发（连接池调大后可调高）
worker_connections = 100

# 优雅重启超时（秒）
timeout = 90

# 日志级别
loglevel = "info"
accesslog = "/var/log/clglxt/gunicorn.access.log"
errorlog = "/var/log/clglxt/gunicorn.error.log"

# 静态文件缓存（CSS/JS/图片）
max_requests = 1000
max_requests_jitter = 50

# 环境变量（.env 路径）
# gunicorn 会自动读 .env 里的 CLGLXT_* 变量
preload = True

# gunicorn 26 的 control socket 在 systemd 下无权限（默认 /run/user/0），
# 不用该功能，显式关闭避免每次启动报 Permission denied。
control_socket_disable = True
# env_file = ".env"

# 启动前执行 Alembic upgrade
# pre_start = "alembic upgrade head"
