"""Redis 客户端与缓存/限频工具。

用途：
  * 登录限频（令牌桶）
  * 热点数据缓存（权限字典、系统设置）
  * Celery broker
"""
import json
import logging
import threading
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

    Redis 不可用时**降级到进程内限频**，而不是直接放行：
    放行等于 Redis 一挂登录爆破就没有任何限制；而直接拒绝又会
    让所有人登不进去。进程内限频精度差（多 worker 各自计数），
    但足以兜住单机暴力破解，且不影响可用性。
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
        log.warning("Redis 限频失败 key=%s，降级为进程内限频", key,
                    exc_info=True)
        return _local_rate_limit(key, limit, window)


# ── 进程内降级限频（Redis 不可用时） ──
_local_lock = threading.Lock()
_local_hits: dict[str, list[float]] = {}
_LOCAL_MAX_KEYS = 10_000


def _local_rate_limit(key: str, limit: int, window: int) -> tuple[bool, int]:
    """进程内滑动窗口。多 worker 场景下每个进程各算一份，精度有限。"""
    now = time.time()
    cutoff = now - window
    with _local_lock:
        # 防止无界增长：超过阈值时整体清一次过期键
        if len(_local_hits) > _LOCAL_MAX_KEYS:
            for k in [k for k, v in _local_hits.items() if not v or v[-1] < cutoff]:
                _local_hits.pop(k, None)
        hits = [t for t in _local_hits.get(key, []) if t > cutoff]
        if len(hits) >= limit:
            _local_hits[key] = hits
            return False, 0
        hits.append(now)
        _local_hits[key] = hits
        return True, limit - len(hits)


def rate_limit_reset(key: str) -> None:
    """清除限频记录（登录成功后调用，或管理员解锁）。"""
    with _local_lock:
        _local_hits.pop(key, None)
    try:
        _redis.delete(f"clglxt:rl:{key}")
    except Exception:
        log.warning("限频重置失败 key=%s", key, exc_info=True)
