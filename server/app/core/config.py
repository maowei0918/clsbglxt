"""全局配置。所有值从环境变量读取，带默认值。"""
import os
from pathlib import Path

from dotenv import load_dotenv

# 在读取任何环境变量之前，先加载 .env 文件（若有）。
# 保证 gunicorn/systemd/宝塔 启动时都能读到安装向导写入的配置。
load_dotenv(str(Path(__file__).resolve().parents[2] / ".env"))

# ── 路径 ──
BASE_DIR = Path(__file__).resolve().parents[2]  # server/app/core/ → server/
DATA_DIR = Path(os.getenv("CLGLXT_DATA_DIR", str(BASE_DIR / "data")))
UPLOAD_DIR = DATA_DIR / "uploads"
BACKUP_DIR = DATA_DIR / "backups"
DATA_DIR_PENDING = DATA_DIR / ".migrate_pending"
DATA_DIR.mkdir(parents=True, exist_ok=True)
UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
BACKUP_DIR.mkdir(parents=True, exist_ok=True)

# ── 应用 ──
APP_TITLE = os.getenv("CLGLXT_APP_TITLE", "clglxt 车辆管理系统")
APP_VERSION = "1.0.0"
ENABLE_DOCS = os.getenv("CLGLXT_ENABLE_DOCS", "false").lower() == "true"

# ── 数据库（PostgreSQL） ──
DATABASE_URL = os.getenv(
    "CLGLXT_DATABASE_URL",
    "postgresql+psycopg2://clglxt:changeme@localhost:5432/clglxt",
)
DB_POOL_SIZE = int(os.getenv("CLGLXT_DB_POOL_SIZE", "10"))
DB_MAX_OVERFLOW = int(os.getenv("CLGLXT_DB_MAX_OVERFLOW", "20"))
DB_POOL_TIMEOUT = int(os.getenv("CLGLXT_DB_POOL_TIMEOUT", "30"))
DB_POOL_RECYCLE = int(os.getenv("CLGLXT_DB_POOL_RECYCLE", "3600"))

# ── Redis ──
REDIS_URL = os.getenv("CLGLXT_REDIS_URL", "redis://localhost:6379/0")
REDIS_CELERY_URL = os.getenv("CLGLXT_REDIS_CELERY_URL", "redis://localhost:6379/1")

# ── Celery ──
CELERY_BROKER_URL = os.getenv("CLGLXT_CELERY_BROKER_URL", REDIS_CELERY_URL)
CELERY_RESULT_BACKEND = os.getenv("CLGLXT_CELERY_RESULT_BACKEND", REDIS_CELERY_URL)
CELERY_WORKER_CONCURRENCY = int(os.getenv("CLGLXT_CELERY_WORKER_CONCURRENCY", "2"))
CELERY_TASK_TIME_LIMIT = int(os.getenv("CLGLXT_CELERY_TASK_TIME_LIMIT", "3600"))

# ── 认证 ──
# JWT 密钥：禁止裸跑默认值。
# 安装前（未生成 .env）允许空值；安装后 lifespan 启动时校验（见 main.py）。
JWT_SECRET = os.getenv("CLGLXT_JWT_SECRET", "")
JWT_ALGORITHM = "HS256"
ACCESS_TOKEN_EXPIRE_MINUTES = int(os.getenv("CLGLXT_TOKEN_EXPIRE", "1440"))  # 24h
MEDIA_TOKEN_EXPIRE_MINUTES = 1440

# 强制改密时允许访问的路径白名单
MUST_CHANGE_ALLOWED = (
    "/api/auth/me",
    "/api/auth/change-password",
    "/api/auth/logout",
)

# ── 安全 ──
CORS_ORIGINS = os.getenv("CLGLXT_CORS_ORIGINS", "").split(",") if os.getenv("CLGLXT_CORS_ORIGINS") else []
# 可信代理（取真实 IP）。逗号分隔的 IP/网段。
TRUSTED_PROXIES = os.getenv("CLGLXT_TRUSTED_PROXIES", "127.0.0.1,::1").split(",")
# 上传限制
MAX_UPLOAD_MB = int(os.getenv("CLGLXT_MAX_UPLOAD_MB", "50"))
MAX_ORDER_UPLOAD_MB = int(os.getenv("CLGLXT_MAX_ORDER_UPLOAD_MB", "200"))
MIN_FREE_MB = int(os.getenv("CLGLXT_MIN_FREE_MB", "1024"))

# ── 审计 ──
AUDIT_RETENTION_DAYS = int(os.getenv("CLGLXT_AUDIT_RETENTION_DAYS", "180"))
AUDIT_MIDDLEWARE_DEDUP_SECONDS = int(os.getenv("CLGLXT_AUDIT_DEDUP_SECONDS", "60"))

# ── 临时用车 ──
# 临时用车记录 end_date 超过 N 天后自动归档（0 表示不自动归档）
VEHICLE_USE_AUTO_ARCHIVE_DAYS = int(os.getenv("CLGLXT_VEHICLE_USE_AUTO_ARCHIVE_DAYS", "30"))

# ── AI ──
AI_DAILY_QUOTA = int(os.getenv("CLGLXT_AI_DAILY_QUOTA", "20"))

# ── 备份 ──
BACKUP_SKIP_TABLES = os.getenv("CLGLXT_BACKUP_SKIP_TABLES", "").split(",") if os.getenv("CLGLXT_BACKUP_SKIP_TABLES") else []

# ── 邮箱 ──
SMTP_HOST = os.getenv("CLGLXT_SMTP_HOST", "")
SMTP_PORT = int(os.getenv("CLGLXT_SMTP_PORT", "587"))
SMTP_USER = os.getenv("CLGLXT_SMTP_USER", "")
SMTP_PASS = os.getenv("CLGLXT_SMTP_PASS", "")
SMTP_FROM = os.getenv("CLGLXT_SMTP_FROM", "")

# ── 出站策略（SSRF） ──
SSRF_ALLOWED_HOSTS = os.getenv("CLGLXT_SSRF_ALLOWED_HOSTS", "").split(",") if os.getenv("CLGLXT_SSRF_ALLOWED_HOSTS") else []

# ── FMS 数据导入（一次性迁移，FMS 停机前实施） ──
# 老 FMS 对外只读 API 基址，例如 https://fms.example.com
FMS_BASE_URL = os.getenv("CLGLXT_FMS_BASE_URL", "").rstrip("/")
# FMS 开放接口密钥（fms_ 开头，scope 需含 vehicles/repairs/... 的 :read）
FMS_API_KEY = os.getenv("CLGLXT_FMS_API_KEY", "")
# 单页条数（FMS 开放接口上限 500）
FMS_PAGE_SIZE = int(os.getenv("CLGLXT_FMS_PAGE_SIZE", "200"))
# 单次 HTTP 超时（秒）
FMS_TIMEOUT = float(os.getenv("CLGLXT_FMS_TIMEOUT", "30"))
# 失败重试次数（指数退避）
FMS_MAX_RETRIES = int(os.getenv("CLGLXT_FMS_MAX_RETRIES", "3"))
