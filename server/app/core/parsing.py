"""请求参数解析工具。

历史问题：日期参数直接 fromisoformat / 与 DateTime 列比较，
非法字符串在 Python 层抛 ValueError → 500；vehicle_account 把
字符串直接和 PG 的 timestamp 列比较还会触发类型错误。
统一在这里收口：非法输入一律 400，空串返回 None（不过滤）。
"""
from datetime import date

from fastapi import HTTPException


def parse_date(value: str, field: str = "日期") -> date | None:
    """'YYYY-MM-DD' → date；空串 → None；非法格式 → HTTP 400。

    只取前 10 位，兼容 '2026-11-02T08:00:00' 这类带时间的输入。
    """
    raw = str(value or "").strip()
    if not raw:
        return None
    try:
        return date.fromisoformat(raw[:10])
    except ValueError:
        raise HTTPException(400, f"{field}格式无效，应为 YYYY-MM-DD")
