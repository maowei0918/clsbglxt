"""Redis 客户端与缓存/限频工具。

用途：
  * 登录限频（令牌桶）
  * 热点数据缓存（权限字典、系统设置）
  * Celery broker
"""
import json
import logging
import time
import uuid
from typing import Any

import redis

from .config import REDIS_URL

log = logging.getLogger("clglxt.redis")

_pool = redis.ConnectionPool.from_url(
    REDIS_URL,
    max_connections=50,
    decode_responses=True,
    socket_timeout=1.5,
    socket_connect_timeout=1.5,
    health_check_interval=30,
    retry_on_timeout=False,
)
_redis = redis.Redis(connection_pool=_pool)


def get_redis() -> redis.Redis:
    return _redis


def health_check() -> bool:
    try:
        return _redis.ping()
    except Exception:
        log.exception("Redis 健康检查失败")
        return False


# ─────────────────────────── 缓存 ───────────────────────────

def cache_get(key: str) -> Any | None:
    """读缓存。JSON 反序列化。"""
    try:
        raw = _redis.get(f"clglxt:cache:{key}")
        return json.loads(raw) if raw else None
    except Exception:
        log.warning("缓存读取失败 key=%s", key, exc_info=True)
        return None


def cache_set(key: str, value: Any, ttl: int = 300) -> None:
    """写缓存，默认 5 分钟。"""
    try:
        _redis.setex(f"clglxt:cache:{key}", ttl, json.dumps(value, ensure_ascii=False))
    except Exception:
        log.warning("缓存写入失败 key=%s", key, exc_info=True)


def cache_del(*keys: str) -> None:
    try:
        if keys:
            _redis.delete(*[f"clglxt:cache:{k}" for k in keys])
    except Exception:
        log.warning("缓存删除失败 keys=%s", keys, exc_info=True)


def cache_flush_prefix(prefix: str) -> int:
    """按前缀批量删（用 scan_iter 避免 KEYS 阻塞）。"""
    try:
        pattern = f"clglxt:cache:{prefix}*"
        n = 0
        for k in _redis.scan_iter(match=pattern, count=200):
            _redis.delete(k)
            n += 1
        return n
    except Exception:
        log.warning("缓存批量删除失败 prefix=%s", prefix, exc_info=True)
        return 0


# ─────────────────────── 限频（滑动窗口） ───────────────────────

def rate_limit(key: str, limit: int, window: int) -> tuple[bool, int]:
    """滑动窗口限频。

    返回 (是否允许, 剩余配额)。用 Redis sorted set 存时间戳，
    ZREMRANGEBYSCORE 清过期、ZCARD 计数、ZADD 记录本次。

    原子性由 Lua 脚本保证（多个 worker 同时调用不会超发）。
    """
    lua = """
    local key = KEYS[1]
    local now = tonumber(ARGV[1])
    local window = tonumber(ARGV[2])
    local limit = tonumber(ARGV[3])
    redis.call('ZREMRANGEBYSCORE', key, 0, now - window)
    local count = redis.call('ZCARD', key)
    if count >= limit then
        return {0, 0}
    end
    redis.call('ZADD', key, now, ARGV[4])
    redis.call('EXPIRE', key, window)
    return {1, limit - count - 1}
    """
    try:
        now = int(time.time() * 1000)
        member = f"{now}-{uuid.uuid4().hex}"
        r = _redis.eval(lua, 1, f"clglxt:rl:{key}",
                        now, window * 1000, limit, member)
        return bool(r[0]), int(r[1])
    except Exception:
        log.warning("限频检查失败 key=%s（按放行处理）", key, exc_info=True)
        return True, limit


def rate_limit_reset(key: str) -> None:
    try:
        _redis.delete(f"clglxt:rl:{key}")
    except Exception:
        log.warning("限频重置失败 key=%s", key, exc_info=True)
