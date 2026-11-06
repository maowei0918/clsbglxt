"""FMS 开放只读 API 客户端（一次性数据迁移用）。

数据源：老 FMS v2 的对外只读接口（见 fms/v2/server/app/v2/openapi.py）
  GET /api/v2/open/{vehicles|repairs|vehicle-uses|daily-checks|contracts}
鉴权：Authorization: Bearer fms_xxx
分页：offset/limit，返回 {items, total, has_more, next_offset}
限频：每密钥 60 次/分钟（进程内内存计数）

安全：
  * SSRF 防护——基址 host 必须在 CLGLXT_SSRF_ALLOWED_HOSTS 白名单内
  * 只读——仅发 GET，不触碰任何写接口
"""
from __future__ import annotations

import logging
import time
from urllib.parse import urlparse

import httpx

from .config import (FMS_API_KEY, FMS_BASE_URL, FMS_MAX_RETRIES,
                     FMS_PAGE_SIZE, FMS_RATE_LIMIT_RETRIES,
                     FMS_RATE_LIMIT_WAIT, FMS_TIMEOUT, SSRF_ALLOWED_HOSTS)

log = logging.getLogger("clglxt.fms.client")

# 可拉取的开放端点（scope → 路径）
ENDPOINTS = {
    "vehicles": "vehicles",
    "repairs": "repairs",
    "vehicle_uses": "vehicle-uses",
    "daily_checks": "daily-checks",
    "contracts": "contracts",
}


class FmsClientError(RuntimeError):
    """FMS 客户端错误基类。"""


class FmsConfigError(FmsClientError):
    """配置缺失或非法（基址、密钥、白名单）。"""


class FmsHttpError(FmsClientError):
    """FMS 返回非 2xx。"""


def _check_config() -> None:
    if not FMS_BASE_URL:
        raise FmsConfigError("未配置 CLGLXT_FMS_BASE_URL（老 FMS 服务基址）")
    if not FMS_API_KEY:
        raise FmsConfigError("未配置 CLGLXT_FMS_API_KEY（fms_ 开头的开放接口密钥）")


def _check_host() -> None:
    """SSRF 防护：只允许访问白名单内的 host（含端口）。"""
    host = urlparse(FMS_BASE_URL).netloc
    if not host:
        raise FmsConfigError(f"CLGLXT_FMS_BASE_URL 无法解析 host：{FMS_BASE_URL}")
    allowed = {h.strip() for h in SSRF_ALLOWED_HOSTS if h.strip()}
    if host not in allowed:
        raise FmsConfigError(
            f"FMS 主机 {host} 不在 SSRF 白名单内，请在 CLGLXT_SSRF_ALLOWED_HOSTS 中添加"
        )


def _headers() -> dict:
    return {"Authorization": f"Bearer {FMS_API_KEY}", "Accept": "application/json"}


def _retry_after(resp: httpx.Response) -> float | None:
    """解析 429 的 Retry-After 头（秒或 HTTP 日期），解析不出返回 None。"""
    raw = (resp.headers.get("Retry-After") or "").strip()
    if not raw:
        return None
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass
    try:
        from email.utils import parsedate_to_datetime
        dt = parsedate_to_datetime(raw)
        if dt is None:
            return None
        return max(0.0, dt.timestamp() - time.time())
    except (TypeError, ValueError):
        return None


def _request(path: str, params: dict | None = None) -> dict:
    """带重试的 GET，返回 JSON。

    429（限频）与网络错误分开退避：
      * 429：FMS 是「每密钥每分钟 60 次」滑动窗口，短退避（1/2/4/8 秒）
        根本等不出窗口，实测 4 次重试全 429 后整批失败。因此按
        Retry-After 或 FMS_RATE_LIMIT_WAIT（缺省 60 秒，即一个完整窗口）
        等待，重试次数单独用 FMS_RATE_LIMIT_RETRIES 控制。
      * 网络错误：指数退避 2**attempt。
    """
    _check_config()
    _check_host()
    url = f"{FMS_BASE_URL}/api/v2/open/{path}"
    last_err: Exception | None = None
    rate_hits = 0
    attempt = 0
    while attempt <= FMS_MAX_RETRIES:
        try:
            resp = httpx.get(url, params=params or {}, headers=_headers(),
                             timeout=FMS_TIMEOUT, follow_redirects=False)
            if resp.status_code == 429:
                rate_hits += 1
                if rate_hits > FMS_RATE_LIMIT_RETRIES:
                    raise FmsHttpError(
                        f"FMS 限频未恢复（429，已等待重试 {FMS_RATE_LIMIT_RETRIES} 次）"
                        f"：请调大 CLGLXT_FMS_PAGE_SIZE 或降低并发，path={path}")
                wait = _retry_after(resp)
                if wait is None:
                    wait = FMS_RATE_LIMIT_WAIT
                log.warning("FMS 限频(429)，%.0f 秒后重试 path=%s（第 %s/%s 次）",
                            wait, path, rate_hits, FMS_RATE_LIMIT_RETRIES)
                time.sleep(wait)
                attempt += 1
                continue
            if resp.status_code == 401:
                raise FmsHttpError("FMS 密钥无效或已撤销（401）")
            if resp.status_code == 403:
                raise FmsHttpError(f"FMS 密钥缺少该数据 scope（403, path={path}）")
            resp.raise_for_status()
            return resp.json()
        except FmsHttpError:
            raise
        except (httpx.TimeoutException, httpx.ConnectError, httpx.HTTPError) as e:
            last_err = e
            if attempt < FMS_MAX_RETRIES:
                wait = 2 ** attempt
                log.warning("请求失败(%s)，%s 秒后重试 path=%s", e, wait, path)
                time.sleep(wait)
                attempt += 1
                continue
            break
    raise FmsHttpError(f"请求失败，已重试 {FMS_MAX_RETRIES} 次：{last_err}")


def list_page(scope: str, offset: int = 0, limit: int | None = None,
              filters: dict | None = None) -> dict:
    """拉取单页数据。scope 见 ENDPOINTS。"""
    if scope not in ENDPOINTS:
        raise FmsClientError(f"未知 scope：{scope}（可选：{', '.join(ENDPOINTS)}）")
    params: dict = {"offset": offset, "limit": limit or FMS_PAGE_SIZE}
    if filters:
        params.update({k: v for k, v in filters.items() if v not in (None, "")})
    return _request(ENDPOINTS[scope], params)


def iter_pages(scope: str, filters: dict | None = None,
               start_offset: int = 0, limit: int | None = None):
    """按 FMS 分页协议迭代所有数据页，yield 每页 items。

    start_offset：断点续传起点（已提交的行直接跳过，不重复拉取）。
    limit：单页条数，缺省用配置的 FMS_PAGE_SIZE。

    以 has_more / next_offset 驱动；若对端未提供（字段兜底），回退到
    total 判断，避免死循环。
    """
    offset = start_offset
    seen = set()
    while True:
        page = list_page(scope, offset=offset, limit=limit, filters=filters)
        items = page.get("items") or []
        for it in items:
            # 去重兜底：同一 offset 内若重复返回同 id 则跳过
            key = it.get("id")
            if key is not None:
                if key in seen:
                    continue
                seen.add(key)
            yield it
        if not items:
            return
        if page.get("has_more") and page.get("next_offset") is not None:
            nxt = int(page["next_offset"])
            if nxt <= offset:          # 防死循环：对端未推进
                log.warning("FMS 分页未推进 offset=%s，停止 scope=%s", offset, scope)
                return
            offset = nxt
            continue
        # 无 has_more 字段时的兜底：用 total 判断
        total = page.get("total")
        if isinstance(total, int) and offset + len(items) >= total:
            return
        if not page.get("has_more"):
            return


def test_connection() -> dict:
    """连通性自检：请求最小页，返回诊断信息（用于「测试连接」按钮）。"""
    _check_config()
    _check_host()
    started = time.time()
    try:
        page = list_page("vehicles", offset=0, limit=1)
        return {
            "ok": True,
            "base_url": FMS_BASE_URL,
            "elapsed_ms": int((time.time() - started) * 1000),
            "sample_total": page.get("total", 0),
        }
    except Exception as e:  # noqa: BLE001
        return {"ok": False, "base_url": FMS_BASE_URL,
                "elapsed_ms": int((time.time() - started) * 1000),
                "error": str(e)}


def available_scopes() -> list[str]:
    return sorted(ENDPOINTS)
