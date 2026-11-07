"""操作审计：记录谁、什么时候、在哪、用什么设备、做了什么。

与 fms 语义一致，但写入改为异步队列（Celery），避免审计写入拖慢业务请求。
"""
import hashlib
import logging
import re
from dataclasses import dataclass, field

from fastapi import Request

log = logging.getLogger("clglxt.audit")


# ─────────────────────────── 设备识别 ───────────────────────────

@dataclass
class DeviceInfo:
    client: str = ""
    os: str = ""
    model: str = ""
    browser: str = ""

    def summary(self) -> str:
        parts = [p for p in (self.client, self.os, self.model) if p]
        return " / ".join(parts) if parts else "未知设备"


_UA_RULES = [
    # (匹配关键字, client, os 提取, browser 提取)
    (r"MicroMessenger", "微信浏览器", r"Android ([\d.]+)|iPhone OS ([\d_]+)", r"MicroMessenger/([\d.]+)"),
    (r"okhttp", "Android App", r"Android ([\d.]+)", None),
    (r"Dalvik", "Android App", r"Android ([\d.]+)", None),
    (r"Windows NT 10", "电脑浏览器", r"Windows NT 10", None),
    (r"Windows NT", "电脑浏览器", r"Windows NT ([\d.]+)", None),
    (r"Macintosh", "电脑浏览器", r"Mac OS X ([\d_.]+)", None),
    (r"Linux", "电脑浏览器", r"Linux", None),
    (r"iPhone", "iPhone", r"iPhone OS ([\d_]+)", None),
    (r"iPad", "iPad", r"iPad; CPU OS ([\d_]+)", None),
    (r"Android", "Android 手机", r"Android ([\d.]+)", None),
]

_BROWSER_RULES = [
    (r"Edg/([\d.]+)", "Edge"),
    (r"Chrome/([\d.]+)", "Chrome"),
    (r"Firefox/([\d.]+)", "Firefox"),
    (r"Safari/([\d.]+)", "Safari"),
]


def parse_ua(ua: str) -> DeviceInfo:
    """从 User-Agent 解析设备信息。解析不出来就给空，不做猜测。"""
    info = DeviceInfo()
    if not ua:
        return info

    for pattern, client, os_re, br_re in _UA_RULES:
        if re.search(pattern, ua):
            info.client = client
            m = re.search(os_re, ua)
            if m:
                info.os = re.sub(r"_", ".", m.group(0))
            # 机型：Android 的括号内第二段通常是机型
            m2 = re.search(r"Android [\d.]+;\s*([^;)]+)", ua)
            if m2:
                info.model = m2.group(1).strip()[:64]
            break

    for pattern, name in _BROWSER_RULES:
        m = re.search(pattern, ua)
        if m:
            info.browser = f"{name} {m.group(1).split('.')[0]}"
            break

    return info


def client_ip(request: Request) -> str:
    """取真实客户端 IP（仅当直连方是可信代理时才信 XFF）。

    与 modules/auth.py 的 client_ip 保持同一套信任模型：
    X-Forwarded-For 是客户端可控的头，直接取会导致审计日志
    IP 可被任意伪造（审计投毒），也会绕过按 IP 的限频。
    """
    from ..core.config import TRUSTED_PROXIES
    direct = request.client.host if request.client else ""
    if direct in TRUSTED_PROXIES:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()[:64]
        real = request.headers.get("x-real-ip", "")
        if real:
            return real.strip()[:64]
    return direct[:64]


def ua_hash(ua: str) -> str:
    return hashlib.md5(ua.encode()).hexdigest()[:32] if ua else ""


@dataclass
class AuditContext:
    ip: str = ""
    ua: str = ""
    device: DeviceInfo = field(default_factory=DeviceInfo)
    ua_hash: str = ""


def build_ctx(request: Request) -> AuditContext:
    ua = request.headers.get("user-agent", "")[:300]
    return AuditContext(
        ip=client_ip(request),
        ua=ua,
        device=parse_ua(ua),
        ua_hash=ua_hash(ua),
    )


# ─────────────────────────── 写入 ───────────────────────────

def audit(db, user_id: int, action: str, target: str = "", detail: str = "",
          ctx: AuditContext | None = None) -> None:
    """业务代码里主动写一条审计。

    ctx 为 None 时从 request.state 取（由中间件塞进去）。
    只 add 不 commit——由调用方在自己事务里提交，保证原子性。
    """
    from ..models import AuditLog

    row = AuditLog(
        user_id=user_id or 0,
        action=action[:64],
        target=(target or "")[:128],
        detail=(detail or "")[:4000],
    )
    if ctx:
        row.ip = ctx.ip
        row.ua = ctx.ua
        row.ua_hash = ctx.ua_hash
        row.client = ctx.device.client
        row.os = ctx.device.os
        row.model = ctx.device.model
        row.browser = ctx.device.browser
    db.add(row)


def audit_request(db, ctx: AuditContext, user_id: int, method: str, path: str,
                  status: int, target: str = "", detail: str = "",
                  request_id: str = "", username: str = "",
                  user_name: str = "") -> None:
    """中间件用的完整版（带请求元信息）。"""
    from ..models import AuditLog

    row = AuditLog(
        user_id=user_id or 0,
        action=f"{method} {path}"[:64],
        target=(target or path)[:128],
        detail=(detail or "")[:4000],
        method=method[:8],
        path=path[:200],
        status=status,
        request_id=request_id[:32],
        username=username[:32],
        user_name=user_name[:32],
    )
    if ctx:
        row.ip = ctx.ip
        row.ua = ctx.ua
        row.ua_hash = ctx.ua_hash
        row.client = ctx.device.client
        row.os = ctx.device.os
        row.model = ctx.device.model
        row.browser = ctx.device.browser
    db.add(row)
