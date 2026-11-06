"""密码哈希与 JWT。

与 fms 保持一致的语义：
  * bcrypt cost=12
  * JWT 双 scope（access / media），token_version 机制踢人
  * dummy hash 防账号枚举
"""
import hashlib
import hmac
import secrets
from datetime import datetime, timedelta, timezone

import bcrypt
import jwt

from .config import (ACCESS_TOKEN_EXPIRE_MINUTES, JWT_ALGORITHM, JWT_SECRET,
                     MEDIA_TOKEN_EXPIRE_MINUTES)

# 防御：密钥为空/默认值时，用进程内随机密钥兜底，避免用可预测密钥签发 token。
# 已安装状态下 main.py 的 lifespan 会直接拒绝启动，因此生产不会走到这里。
if not JWT_SECRET or JWT_SECRET in ("change-this-in-production",
                                    "dev-only-insecure-secret"):
    JWT_SECRET = secrets.token_hex(32)


def now() -> datetime:
    return datetime.now(timezone.utc)


# ─────────────────────────── 密码 ───────────────────────────

def hash_password(password: str) -> str:
    return bcrypt.hashpw(password.encode("utf-8"), bcrypt.gensalt(rounds=12)).decode()


def verify_password(plain: str, hashed: str) -> bool:
    try:
        return bcrypt.checkpw(plain.encode("utf-8"), hashed.encode("utf-8"))
    except Exception:
        return False


# 账号不存在时也校验一遍这个假 hash，让"账号不存在"和"密码错"耗时一致。
# 否则时序差异就是账号枚举的侧信道。
_DUMMY_HASH = bcrypt.hashpw(b"__clglxt_dummy__", bcrypt.gensalt(rounds=12)).decode()

def verify_dummy() -> bool:
    verify_password("__clglxt_dummy__", _DUMMY_HASH)
    return False


def validate_password(password: str, username: str = "") -> str | None:
    """密码强度校验，返回错误提示或 None。与 fms 保持同一规则。"""
    if len(password) < 8:
        return "密码至少 8 位"
    if len(password.encode("utf-8")) > 72:
        return "密码不能超过 72 字节"
    import re
    if not re.search(r"[A-Za-z]", password) or not re.search(r"[0-9]", password):
        return "密码需同时包含字母和数字"
    low = password.lower()
    weak = ("password", "passwd", "admin", "letmein", "welcome", "monkey",
            "dragon", "master", "qwerty", "abc123", "iloveyou", "woaini", "clglxt",
            "123456", "888888", "111111", "000000", "666666")
    for t in weak:
        if t in low:
            return f"密码不能包含常见弱口令片段（如 {t}）"
    if username and username.lower() in low:
        return "密码不能包含用户名"
    return None


# ─────────────────────────── JWT ───────────────────────────

def create_access_token(user_id: int, token_version: int, is_superuser: bool = False,
                        expire_minutes: int | None = None, username: str = "") -> str:
    expire = now() + timedelta(minutes=expire_minutes or ACCESS_TOKEN_EXPIRE_MINUTES)
    payload = {
        "uid": user_id,
        "tv": token_version,
        "sub": str(username or user_id),
        "scope": "access",
        "exp": expire,
        "iat": now(),
        "jti": secrets.token_hex(8),
    }
    if is_superuser:
        payload["su"] = True
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def create_media_token(user_id: int, token_version: int) -> str:
    """媒体访问 token（附件下载），独立 scope，便于单独吊销。"""
    expire = now() + timedelta(minutes=MEDIA_TOKEN_EXPIRE_MINUTES)
    payload = {"uid": user_id, "tv": token_version, "scope": "media",
               "exp": expire, "iat": now()}
    return jwt.encode(payload, JWT_SECRET, algorithm=JWT_ALGORITHM)


def decode_token(token: str) -> dict | None:
    try:
        return jwt.decode(token, JWT_SECRET, algorithms=[JWT_ALGORITHM])
    except jwt.ExpiredSignatureError:
        return None
    except jwt.InvalidTokenError:
        return None


# ─────────────────────────── 随机/哈希 ───────────────────────────

def random_password(length: int = 12) -> str:
    """生成随机密码（至少一位大小写+数字）。"""
    import string
    while True:
        pwd = "".join(secrets.choice(string.ascii_letters + string.digits)
                      for _ in range(length))
        if (any(c.isupper() for c in pwd) and any(c.islower() for c in pwd)
                and any(c.isdigit() for c in pwd)):
            return pwd


def sha256_hex(data: str | bytes) -> str:
    if isinstance(data, str):
        data = data.encode()
    return hashlib.sha256(data).hexdigest()


def constant_time_eq(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
