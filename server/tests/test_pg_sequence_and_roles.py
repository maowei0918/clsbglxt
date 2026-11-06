"""回归：PG 自增序列同步 + 账号归属/角色归属映射。

覆盖两个已修复的缺陷：
  1. sync_sequences —— 内置角色/超管用显式 id 插入，不推进 PG 序列，
     安装后首次通过 API 建角色/建号会撞主键。函数必须：
       * 在 SQLite 下完全跳过（生产/测试用 SQLite 时不做任何事）
       * 在 PostgreSQL 下逐列 setval 到 max(id)+1
  2. personnel._assign_roles —— _resolve_owner 返回 "dept"/"supplier"，
     而 Role.side 是 "internal"/"external"，必须先映射再比较。

实现方式：不依赖真实 PG 实例（CI 无 PG）。用一个假 engine/连接记录
执行的 SQL，校验行为而不是校验数据库状态。

运行：
    cd server && python -m pytest tests/test_pg_sequence_and_roles.py -v
"""
from __future__ import annotations

import os
import sys

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from sqlalchemy import text  # noqa: E402

from app.core.database import sync_sequences  # noqa: E402
from app.modules.personnel import _OWNER_SIDE  # noqa: E402


# ─────────────────────────── 假 PG 引擎 ───────────────────────────


class _FakeResult:
    def __init__(self, rows=None, scalar=None):
        self._rows = rows or []
        self._scalar = scalar

    def fetchall(self):
        return self._rows

    def first(self):
        return self._rows[0] if self._rows else None

    def scalar(self):
        return self._scalar


class _FakeConn:
    """记录所有执行过的 SQL，按语句特征返回预设结果。

    seq_pos: (last_value, is_called)，模拟序列当前位置；
    table_max: 模拟表内 max(id)（返回 COALESCE 查询时给出）。
    """

    def __init__(self, disc_columns, has_seq=True, seq_pos=(1, True),
                 table_max=0):
        self.executed: list[tuple[str, dict]] = []
        self._disc = disc_columns
        self._has_seq = has_seq
        self._seq_pos = seq_pos
        self._table_max = table_max

    def execute(self, clause, params=None):
        sql = str(clause)
        self.executed.append((sql, params or {}))
        if "pg_class" in sql:                      # 列出所有自增列
            return _FakeResult(rows=self._disc)
        if "pg_get_serial_sequence" in sql:        # 查某列的序列名
            t = params["t"]
            c = params["c"]
            return _FakeResult(
                scalar=f'public."{t}_{c}_seq"' if self._has_seq else None)
        if "last_value" in sql:                    # 读序列当前位置
            return _FakeResult(rows=[self._seq_pos])
        if "COALESCE(MAX(" in sql:                 # 读表内 max(id)
            return _FakeResult(scalar=self._table_max + 1)
        return _FakeResult()                       # setval

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


class _FakeEngine:
    def __init__(self, dialect, conn):
        self.dialect = type("D", (), {"name": dialect})()
        self._conn = conn

    def begin(self):
        return self._conn


def test_sync_sequences_skips_sqlite():
    """SQLite 下必须是彻底 no-op：不查序列、不 setval。"""
    conn = _FakeConn(disc_columns=[("roles", "id")])
    sync_sequences(_FakeEngine("sqlite", conn))
    assert conn.executed == []


def test_sync_sequences_setval_all_pg_sequence_columns():
    """PG 下序列落后于 max(id) 时，每列都要 setval 前进到 max(id)+1。"""
    disc = [("roles", "id"), ("users", "id"), ("departments", "id")]
    # 序列停在 1（种子插入 1/2/3 后没推进），表内 max 为 3
    conn = _FakeConn(disc, seq_pos=(1, True), table_max=3)
    sync_sequences(_FakeEngine("postgresql", conn))

    setvals = [(s, p) for s, p in conn.executed if "setval" in s]
    assert len(setvals) == 3, f"应对 3 个列各执行一次 setval，实际 {len(setvals)}"

    for sql, params in setvals:
        assert params["v"] == 4, "应把序列设到 max(id)+1 = 4"
        assert params["seq"].endswith("_seq\"")

    # 表名必须原样进 SQL，且做了合法标识符转义（双引号）
    joined = " ".join(s for s, _ in conn.executed)
    for table in ("roles", "users", "departments"):
        assert f'FROM "{table}"' in joined


def test_sync_sequences_never_moves_sequence_backwards():
    """序列已领先时绝不回退：否则 ID 复用会撞软删行 / 外键。"""
    conn = _FakeConn([("roles", "id")], seq_pos=(100, True), table_max=3)
    sync_sequences(_FakeEngine("postgresql", conn))
    assert not any("setval" in s for s, _ in conn.executed), (
        "序列位置 101 已大于 max(id)+1=4，不应执行 setval")


def test_sync_sequences_handles_unused_sequence():
    """序列尚未发出过值（is_called=false）时，按 1 起算比较。"""
    conn = _FakeConn([("roles", "id")], seq_pos=(1, False), table_max=0)
    sync_sequences(_FakeEngine("postgresql", conn))
    # 空表 + 未使用的序列 → 两边都是 1，无需 setval
    assert not any("setval" in s for s, _ in conn.executed)


def test_sync_sequences_skips_columns_without_sequence():
    """pg_get_serial_sequence 返回空时跳过，不能拼出 setval(NULL)。"""
    conn = _FakeConn([("ghost", "id")], has_seq=False)
    sync_sequences(_FakeEngine("postgresql", conn))
    assert not any("setval" in s for s, _ in conn.executed)


def test_sync_sequences_is_idempotent_shape():
    """重复调用不产生 setval 累积（第二次序列已覆盖，应无 setval）。"""
    conn = _FakeConn([("roles", "id")], seq_pos=(1, True), table_max=3)
    eng = _FakeEngine("postgresql", conn)
    sync_sequences(eng)
    assert any("setval" in s for s, _ in conn.executed)
    # 第二次：模拟序列已被设到 4
    conn._seq_pos = (4, True)
    before = sum(1 for s, _ in conn.executed if "setval" in s)
    sync_sequences(eng)
    after = sum(1 for s, _ in conn.executed if "setval" in s)
    assert after == before, "序列已覆盖时不应再次 setval"


# ─────────────────────────── 调用顺序约束 ───────────────────────────


def test_installer_syncs_sequences_after_seed_commit():
    """installer 里 sync_sequences 必须在种子 db.commit() 之后。

    种子用显式 id 插 1/2/3；若在 create_all 之后、seed 之前同步，
    表还是空的，setval 到 1，首个自增照样撞主键。这是最容易改回去
    的坑，用源码顺序静态锁死。
    """
    import inspect

    from app import installer

    src = inspect.getsource(installer.save_install)
    i_create = src.index("create_all")
    i_commit = src.index("db.commit()")
    i_sync = src.index("sync_sequences(engine)")
    assert i_create < i_commit < i_sync, (
        "顺序必须是 建表 → seed 提交 → 同步序列；"
        f"实际 create_all={i_create}, commit={i_commit}, sync={i_sync}"
    )


# ─────────────────────────── 归属映射 ───────────────────────────


def test_owner_side_mapping_is_complete():
    """_resolve_owner 的所有返回值都必须有映射，否则建号必 500。"""
    assert _OWNER_SIDE == {"dept": "internal", "supplier": "external"}


def test_owner_side_values_match_role_side_vocabulary():
    """映射目标必须是 Role.side 的真实取值，否则永远匹配不上。"""
    from app.models import Role

    side_col = Role.__table__.c.side
    assert side_col is not None
    valid = {v for v in _OWNER_SIDE.values()}
    # 与 models 定义的内置角色 side 取值对照
    assert valid == {"internal", "external"}