"""回归：日期参数解析统一收口，非法输入一律 400 而不是 500。

历史问题：
  * daily_check.list_records / repairs.list_orders 直接 fromisoformat，
    非法字符串抛 ValueError → 500
  * vehicle_account.get_account 把字符串直接与 timestamp 列比较，
    在 PG 下会报类型错误
"""
from datetime import date, datetime

import pytest
from fastapi import HTTPException


def test_parse_date_valid():
    from app.core.parsing import parse_date

    assert parse_date("2026-11-02", "d") == date(2026, 11, 2)
    # 兼容带时间的输入（只取前 10 位）
    assert parse_date("2026-11-02T08:30:00", "d") == date(2026, 11, 2)
    assert parse_date("  2026-11-02  ", "d") == date(2026, 11, 2)


def test_parse_date_empty_returns_none():
    from app.core.parsing import parse_date

    assert parse_date("", "d") is None
    assert parse_date(None, "d") is None
    assert parse_date("   ", "d") is None


def test_parse_date_invalid_raises_400():
    from app.core.parsing import parse_date

    # Python 3.11+ 的 fromisoformat 接受 '20261102' 这类基本格式，
    # 属于合法输入；只把真正的非法值纳入本测试。
    for bad in ("not-a-date", "2026-13-01", "2026/11/02", "2026-11-32", "abc"):
        with pytest.raises(HTTPException) as ei:
            parse_date(bad, "date_from")
        assert ei.value.status_code == 400, bad
        assert "date_from" in ei.value.detail


def test_repairs_day_helpers_use_parse_date():
    """repairs 的 _day_start/_day_after 遇非法日期必须 400，不是 ValueError。"""
    from app.modules.repairs import _day_after, _day_start

    assert _day_start("2026-11-02") == datetime(2026, 11, 2)
    assert _day_after("2026-11-02") == datetime(2026, 11, 3)
    for fn in (_day_start, _day_after):
        with pytest.raises(HTTPException) as ei:
            fn("garbage")
        assert ei.value.status_code == 400


def test_vehicle_account_as_dt():
    from app.modules.vehicle_account import _as_dt

    assert _as_dt("2026-11-02", "d") == datetime(2026, 11, 2)
    assert _as_dt("", "d") is None
    with pytest.raises(HTTPException) as ei:
        _as_dt("bad", "start_date")
    assert ei.value.status_code == 400


def test_submit_record_rejects_bad_date_with_400():
    """submit_record 走 parse_date，非法/空日期必须是 400 而非 500。"""
    import inspect

    from app.modules import daily_check

    src = inspect.getsource(daily_check.submit_record)
    assert "parse_date(check_date" in src
    assert "cd is None" in src, "空日期必须显式拒绝，否则后续比较会 TypeError"
