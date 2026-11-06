"""FastAPI 应用入口。

挂载路由、安全头、审计中间件、Celery 启动。
"""
import logging
import os
import re
import uuid
from contextlib import asynccontextmanager
from datetime import timedelta
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from .core import audit as audit_mod
from .core.config import (APP_TITLE, APP_VERSION, AUDIT_RETENTION_DAYS,
                          AUDIT_MIDDLEWARE_DEDUP_SECONDS, CORS_ORIGINS,
                          DATA_DIR, DATA_DIR_PENDING, ENABLE_DOCS,
                          JWT_SECRET)
from .core.database import health_check
from .core.security import decode_token
from .core.version import __version__

log = logging.getLogger("clglxt.app")

_DIST_DIR = DATA_DIR / "web"

APP_CSP = (
    "default-src 'self'; "
    "script-src 'self' 'unsafe-inline'; "
    "style-src 'self' 'unsafe-inline'; "
    "img-src 'self' data: blob:; "
    "media-src 'self' blob:; "
    "font-src 'self' data:; "
    "connect-src 'self'; "
    "object-src 'none'; "
    "base-uri 'self'; "
    "form-action 'self'; "
    "frame-ancestors 'self'"
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用启动时执行。"""
    log.info("clglxt 启动，version=%s", __version__)
    from .installer import is_installed
    if not is_installed():
        log.warning("系统尚未安装，请访问 /install 完成安装向导")
    else:
        # 生产强校验：已安装就必须有独立 JWT 密钥，禁止空值/已知默认值
        if not JWT_SECRET or JWT_SECRET in (
                "change-this-in-production", "dev-only-insecure-secret"):
            raise RuntimeError(
                "CLGLXT_JWT_SECRET 未配置或使用了默认值，拒绝启动。"
                "生成：openssl rand -hex 32 → 写入 server/.env → 重启")
        if len(JWT_SECRET) < 32:
            raise RuntimeError(
                f"CLGLXT_JWT_SECRET 过短（{len(JWT_SECRET)} 字符），至少 32 字符。"
                "生成：openssl rand -hex 32")
        try:
            from .core.database import init_db
            init_db()
            log.info("数据库表就绪")
        except Exception:
            log.exception("数据库初始化失败（不影响安装向导）")
    yield


app = FastAPI(
    title=APP_TITLE,
    version=__version__,
    lifespan=lifespan,
    docs_url="/docs" if ENABLE_DOCS else None,
    redoc_url="/redoc" if ENABLE_DOCS else None,
    openapi_url="/openapi.json" if ENABLE_DOCS else None,
)


# ─────────────────────────── 中间件 ───────────────────────────

# 维护模式守卫
@app.middleware("http")
async def maintenance_guard(request: Request, call_next):
    """数据目录迁移期间阻断写操作。"""
    if DATA_DIR_PENDING.exists() and request.method not in ("GET", "HEAD", "OPTIONS"):
        return JSONResponse({"detail": "系统正在迁移数据目录，请稍候再试"}, status_code=503)
    return await call_next(request)


from .core.audit import audit_request, build_ctx
from .core.database import SessionLocal

# 审计中间件（全量写操作留痕）
@app.middleware("http")
async def audit_mutations(request: Request, call_next):
    """全量操作留痕：方法/路径/状态/IP/设备/uid（从 token 解，不查库）。"""
    ctx = build_ctx(request)
    response = await call_next(request)
    path = request.url.path

    if path.startswith("/api/") and is_installed():
        uid = 0
        username = ""
        auth = request.headers.get("authorization", "")
        token = auth[7:].strip() if auth.lower().startswith("bearer ") else \
            request.cookies.get("clglxt_at") or request.cookies.get("clglxt_mt") or ""
        payload = decode_token(token) if token else None
        if payload:
            uid = int(payload.get("uid") or 0)
            username = str(payload.get("sub") or "")

        db = SessionLocal()
        try:
            audit_request(
                db=db, ctx=ctx, user_id=uid,
                method=request.method, path=path,
                status=response.status_code,
                username=username, user_name="",
            )
            db.commit()
        except Exception:
            db.rollback()
            log.warning("审计写入失败 path=%s", path, exc_info=True)
        finally:
            db.close()
    return response


# CORS
if CORS_ORIGINS:
    app.add_middleware(
        CORSMiddleware,
        allow_origins=CORS_ORIGINS,
        allow_credentials=False,
        allow_methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "Accept"],
        max_age=600,
    )


# 安全响应头
@app.middleware("http")
async def security_headers(request: Request, call_next):
    """生产安全响应头。"""
    response = await call_next(request)
    response.headers.setdefault("X-Content-Type-Options", "nosniff")
    response.headers.setdefault("X-Frame-Options", "SAMEORIGIN")
    response.headers.setdefault("Referrer-Policy", "no-referrer")
    response.headers.setdefault("Permissions-Policy", "camera=(), microphone=(), geolocation=()")
    response.headers.setdefault("X-Permitted-Cross-Domain-Policies", "none")
    if request.url.path.startswith("/api/files/"):
        response.headers.setdefault("Content-Security-Policy", "default-src 'none'; sandbox")
    else:
        response.headers.setdefault("Content-Security-Policy", APP_CSP)
    return response


# ─────────────────────────── 路由挂载 ───────────────────────────

from .modules import (  # noqa: E402
    auth, contracts, daily_check, departments, import_fms, my_shop, notifications, personnel,
    repairs, repairs2, suppliers, system, vehicles, fence, vehicle_use,
    vehicle_account, backup,
)
from .modules.daily_check_photos import router as daily_check_photos_router  # noqa: E402

API_PREFIX = "/api"
for m in (auth, system, personnel, departments, vehicles, contracts, repairs,
          repairs2, daily_check, notifications, suppliers, my_shop, fence, vehicle_use,
          vehicle_account, import_fms, backup):
    app.include_router(m.router, prefix=API_PREFIX)

# 日检照片上传/读取/删除（独立路由，前缀 /api/daily-check/photos）
app.include_router(daily_check_photos_router, prefix=API_PREFIX)

# 安装向导（/install 页面 + /api/install/*）
from .installer import router as installer_router, is_installed  # noqa: E402
app.include_router(installer_router)


@app.get("/healthz", include_in_schema=False)
def healthz():
    """兼容裸 /healthz 的健康检查。"""
    db_ok = health_check()
    return JSONResponse({"ok": db_ok, "db": "ok" if db_ok else "down"})


@app.get("/api/healthz")
def healthz():
    """健康检查。"""
    db_ok = health_check()
    payload = {"ok": db_ok, "db": "ok" if db_ok else "down"}
    if db_ok:
        payload["service"] = "clglxt"
        payload["version"] = __version__
    if not db_ok:
        return JSONResponse(payload, status_code=503)
    return payload


@app.get("/api/ping.js")
def ping_js(cb: str = "clglxtPing"):
    """移动端连接探测专用：返回可执行 JS。"""
    name = re.sub(r"[^A-Za-z0-9_$]", "", cb) or "clglxtPing"
    payload = f'{{"ok": true, "service": "clglxt", "version": "{__version__}"}}'
    return Response(content=f"{name}({payload});", media_type="application/javascript")


# 前端静态资源（打包后，且系统已安装才挂载）
if is_installed() and _DIST_DIR.exists():
    app.mount("/", StaticFiles(directory=str(_DIST_DIR), html=True), name="web")
else:
    @app.get("/", include_in_schema=False)
    def root():
        """未安装或前端未部署 → 跳安装向导（已安装跳登录由安装器负责）。"""
        if not is_installed():
            return RedirectResponse("/install", status_code=302)
        return RedirectResponse("/login", status_code=302)
