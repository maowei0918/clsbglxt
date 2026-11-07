#!/usr/bin/env python3
"""批次4 安全回归：凭据不经 URL / 强制改密 / 备份路径 / 解压炸弹 / 限频降级 / 审计 IP。

覆盖：
  * auth._read_credentials  不再接受 query 传密码（避免 access log 明文）
  * deps.get_current_user   must_change_password 时拦截业务接口
  * backup._check_base_path  拒绝相对路径、拒绝 Web 静态目录、拒绝源码目录
  * image_utils             超大像素图在解码前被拦截（400 而非 500）
  * redis_client.rate_limit Redis 不可用时降级为进程内限频（不放行）
  * audit.client_ip         非可信代理直连时忽略 X-Forwarded-For

实现方式：纯函数/单元级 + TestClient，不依赖真实 Redis / 外部 DB。

运行：
    cd server && .venv/bin/python -m pytest tests/test_security_batch4.py -v
"""
from __future__ import annotations

import io
import os
import sys
import tempfile
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

os.environ["CLGLXT_JWT_SECRET"] = "x" * 64
os.environ["CLGLXT_DATABASE_URL"] = "sqlite:///./test_batch4.db"
_B4_DATA_DIR = tempfile.mkdtemp(prefix="clglxt_b4_")
os.environ["CLGLXT_DATA_DIR"] = _B4_DATA_DIR


# ─────────────────── 凭据不得走 query ───────────────────

def test_credentials_reject_query_string():
    """密码出现在 URL 会进 Nginx/Gunicorn access log，必须不再读取。"""
    import asyncio
    from starlette.requests import Request

    from app.modules.auth import _read_credentials

    scope = {
        "type": "http", "method": "POST", "path": "/api/auth/login",
        "query_string": b"username=admin&password=Secret123",
        "headers": [(b"content-type", b"application/json")],
        "client": ("1.2.3.4", 1234),
    }

    async def _empty_body():
        return b""

    req = Request(scope, receive=_empty_body)
    u, p = asyncio.run(_read_credentials(req))
    assert u == "" and p == "", f"query 凭据不应被读取，实际读到 {u}/{p}"


def test_credentials_accept_json_body():
    """正常路径：JSON body 仍然必须可用（前端 / Android 都走这）。"""
    import asyncio
    import json as _json
    from starlette.requests import Request

    from app.modules.auth import _read_credentials

    payload = _json.dumps({"username": "admin", "password": "S3cret"}).encode()
    scope = {
        "type": "http", "method": "POST", "path": "/api/auth/login",
        "query_string": b"",
        "headers": [(b"content-type", b"application/json")],
        "client": ("1.2.3.4", 1234),
    }

    async def _recv():
        return {"type": "http.request", "body": payload, "more_body": False}

    req = Request(scope, receive=_recv)
    u, p = asyncio.run(_read_credentials(req))
    assert (u, p) == ("admin", "S3cret")


# ─────────────────── 强制改密拦截 ───────────────────

def test_must_change_password_blocks_business_api():
    """must_change_password=True 的用户访问业务接口应 403，白名单除外。"""
    from fastapi import HTTPException

    from app.core import deps
    from app.core.config import MUST_CHANGE_ALLOWED
    from app.core.security import create_access_token

    class _U:
        id = 1
        is_active = True
        token_version = 1
        must_change_password = True

        class deleted_at:
            year = 1970  # 未删除

    class _DB:
        def get(self, model, pk):
            return _U()

    def _req(path):
        class R:
            url = type("U", (), {"path": path})()
            headers = {"authorization": f"Bearer {create_access_token(1, 1)}"}
            cookies = {}
        return R()

    # 业务接口 → 403
    for bad in ("/api/vehicles", "/api/repairs", "/api/backup/targets"):
        with pytest.raises(HTTPException) as ei:
            deps.get_current_user(_req(bad), db=_DB())
        assert ei.value.status_code == 403, bad

    # 白名单接口 → 放行
    for ok in MUST_CHANGE_ALLOWED:
        deps.get_current_user(_req(ok), db=_DB())


# ─────────────────── 备份路径校验 ───────────────────

def test_backup_base_path_validation():
    from fastapi import HTTPException

    from app.core.config import BASE_DIR, DATA_DIR
    from app.modules.backup import _WEB_DIR, _check_base_path

    # 相对路径 → 拒
    with pytest.raises(HTTPException) as e1:
        _check_base_path("backups")
    assert e1.value.status_code == 400

    # Web 静态目录 → 拒（dump 会被匿名下载）
    with pytest.raises(HTTPException) as e2:
        _check_base_path(str(_WEB_DIR))
    assert e2.value.status_code == 400
    with pytest.raises(HTTPException):
        _check_base_path(str(_WEB_DIR / "sub"))

    # 应用源码目录 → 拒
    with pytest.raises(HTTPException):
        _check_base_path(str(BASE_DIR))

    # 合法绝对路径 → 放行并归一化
    good = str(DATA_DIR / "backups")
    assert _check_base_path(good) == good
    # 空串表示用默认目录
    assert _check_base_path("") == ""


def test_backup_target_ignores_unknown_fields():
    """白名单模型：客户端不能注入模型里不存在的列。"""
    from app.modules.backup import TargetIn

    t = TargetIn(name="夜间备份", kind="local",
                 base_path="/var/backups/clglxt",
                 hacked="x")  # type: ignore[call-arg]
    assert "hacked" not in t.model_dump()


# ─────────────────── 解压炸弹 ───────────────────

def test_decompression_bomb_rejected_before_decode():
    """超大像素图必须在解码前被拦（ValueError，不是 OOM 也不是 500）。"""
    from PIL import Image

    from app.core.image_utils import MAX_DECODE_PIXELS, compress_image

    # 16000x16000 = 256MP，磁盘仅几百 KB，解码要吃 ~730MB
    im = Image.new("RGB", (16000, 16000), (255, 255, 255))
    buf = io.BytesIO()
    im.save(buf, format="PNG")
    raw = buf.getvalue()
    assert len(raw) < 5 * 1024 * 1024, "炸弹样本应远小于解码后体积"
    assert 16000 * 16000 > MAX_DECODE_PIXELS

    with pytest.raises(ValueError):
        compress_image(raw)


def test_normal_photo_still_compresses():
    """正常手机照片不能被误伤。"""
    from PIL import Image

    from app.core.image_utils import compress_image

    im = Image.new("RGB", (4000, 3000), (120, 130, 140))  # 12MP
    buf = io.BytesIO()
    im.save(buf, format="JPEG")

    blob, mime, w, h = compress_image(buf.getvalue(), max_dim=1920)
    assert mime == "image/jpeg"
    assert max(w, h) == 1920
    assert len(blob) < len(buf.getvalue())


# ─────────────────── 限频降级不放行 ───────────────────

def test_rate_limit_degrades_closed_when_redis_down():
    """Redis 挂掉时不能放行（旧实现 fail-open，等于爆破无限制）。"""
    from app.core import redis_client as rc

    class _Down:
        def eval(self, *a, **k):
            raise ConnectionError("redis down")

        def delete(self, *a, **k):
            raise ConnectionError("redis down")

    orig = rc._redis
    rc._redis = _Down()
    try:
        rc.rate_limit_reset("t:down")
        results = [rc.rate_limit("t:down", 3, 60)[0] for _ in range(5)]
        assert results == [True, True, True, False, False], results
    finally:
        rc._redis = orig
        rc.rate_limit_reset("t:down")


def test_rate_limit_window_rolls():
    from app.core import redis_client as rc

    orig = rc._redis

    class _Down:
        def eval(self, *a, **k):
            raise ConnectionError("down")

        def delete(self, *a, **k):
            raise ConnectionError("down")

    rc._redis = _Down()
    try:
        rc.rate_limit_reset("t:win")
        for _ in range(2):
            rc.rate_limit("t:win", 2, 1)
        assert rc.rate_limit("t:win", 2, 1)[0] is False
        time.sleep(1.2)
        assert rc.rate_limit("t:win", 2, 1)[0] is True
    finally:
        rc._redis = orig
        rc.rate_limit_reset("t:win")


# ─────────────────── 审计 IP 不可伪造 ───────────────────

def test_audit_client_ip_ignores_xff_from_untrusted():
    """非可信代理直连时，X-Forwarded-For 不可信（否则审计可投毒）。"""
    from starlette.requests import Request

    from app.core.audit import client_ip

    def _mk(client_host, xff):
        scope = {
            "type": "http", "method": "GET", "path": "/x",
            "query_string": b"", "headers": [],
            "client": (client_host, 1234),
        }
        if xff:
            scope["headers"] = [(b"x-forwarded-for", xff.encode())]
        return Request(scope)

    # 攻击者直连（非可信代理）伪造 XFF → 应取真实直连 IP
    assert client_ip(_mk("203.0.113.9", "1.1.1.1")) == "203.0.113.9"
    # 可信代理转发 → 取 XFF 首个
    assert client_ip(_mk("127.0.0.1", "8.8.8.8, 10.0.0.1")) == "8.8.8.8"


# ─────────────────── 磁盘余量保护 ───────────────────

def test_disk_free_space_guard():
    """磁盘余量不足时必须拒绝写入（否则写满盘拖垮 PG）。"""
    from app.core.image_utils import ensure_free_space

    # 要求保留 1TB 而任何机器都没有 → 必须抛 ValueError
    with pytest.raises(ValueError, match="磁盘空间不足"):
        ensure_free_space("/tmp", need_bytes=1, min_free_mb=1024 * 1024)
    # 正常余量 → 放行
    ensure_free_space("/tmp", need_bytes=1024, min_free_mb=0)


def test_upload_path_traversal_blocked():
    """saved_path 的 ../ 穿越必须被拒（否则任意文件读）。"""
    from app.core.image_utils import resolve_upload_path

    with pytest.raises(ValueError):
        resolve_upload_path("../../etc/passwd")
    with pytest.raises(ValueError):
        resolve_upload_path("a/../../b")
    # 正常相对路径 → 放行
    p = resolve_upload_path("daily_check/2026/01/x.jpg")
    assert "daily_check" in str(p)


# ─────────────────── 附件路径白名单 ───────────────────

def test_attachment_rejects_client_paths():
    """维修单附件 path 不能是绝对路径或包含 ..（任意文件读入口）。"""
    from fastapi import HTTPException

    from app.modules.repairs import _validate_att_path

    assert _validate_att_path("daily_check/2026/01/x.jpg") is True
    for bad in ("/etc/passwd", "../x.jpg", "a/../../b", "x\x00.jpg"):
        with pytest.raises(HTTPException):
            _validate_att_path(bad)


# ─────────────────── 数据目录隔离自检 ───────────────────

def test_data_dir_isolated():
    """确保本测试没往 server/data 写东西。"""
    from app.core.config import DATA_DIR

    assert str(DATA_DIR).startswith("/tmp") or "tmp" in str(DATA_DIR), DATA_DIR