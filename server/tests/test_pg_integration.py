"""PostgreSQL 真库集成测试。

为什么单独一个文件：其余测试都用 SQLite，而 sync_sequences 是
**PG 专属 SQL**（pg_class / pg_attrdef / setval），SQLite 下直接 return，
伪连接单测只能验证语句形态，验证不了「序列是否真的对齐」。这个文件
在真 PG 上跑完整链路：建表 → seed(显式 id) → 同步 → 自增插入。

运行方式（需要一个可写的 PG 库）：

    # 起临时实例
    /usr/lib/postgresql/15/bin/initdb -D /tmp/clglxt_pg/data -U pgtest --auth=trust
    /usr/lib/postgresql/15/bin/pg_ctl -D /tmp/clglxt_pg/data \
        -o "-p 55432 -k /tmp/clglxt_pg -c listen_addresses=''" -l /tmp/clglxt_pg/pg.log start
    /usr/lib/postgresql/15/bin/createdb -h /tmp/clglxt_pg -p 55432 -U pgtest clglxt_test

    # 跑测试
    CLGLXT_TEST_PG_URL='postgresql+psycopg2://pgtest@/clglxt_test?host=/tmp/clglxt_pg&port=55432' \
        .venv/bin/python -m pytest tests/test_pg_integration.py -v

未设置 CLGLXT_TEST_PG_URL 时整个文件跳过，不影响 CI。
"""
from __future__ import annotations

import os

import pytest

_PG_URL = os.environ.get("CLGLXT_TEST_PG_URL", "")

pytestmark = pytest.mark.skipif(
    not _PG_URL,
    reason="未设置 CLGLXT_TEST_PG_URL，跳过 PostgreSQL 集成测试",
)


@pytest.fixture(scope="module")
def pg_engine():
    """建一个干净的表结构；模块结束后 DROP，避免污染。"""
    from sqlalchemy import create_engine

    eng = create_engine(_PG_URL, future=True, pool_pre_ping=True)
    with eng.connect() as c:
        if c.execute(__import__("sqlalchemy").text("SELECT 1")).scalar() != 1:
            pytest.skip("PG 不可用")
    from app.core.database import Base
    from app import models  # noqa: F401  注册模型

    Base.metadata.drop_all(bind=eng)
    Base.metadata.create_all(bind=eng)
    yield eng
    Base.metadata.drop_all(bind=eng)
    eng.dispose()


@pytest.fixture(scope="module")
def seeded(pg_engine):
    """按生产 seed 路径写入内置角色（显式 id=1/2/3）+ 超管。"""
    from sqlalchemy.orm import sessionmaker
    from app.core.seed import seed

    Session = sessionmaker(bind=pg_engine, autoflush=False,
                           expire_on_commit=False)
    db = Session()
    try:
        seed(db, org_name="集成测试公司", dept_name="集成测试部门")
    finally:
        db.close()
    return pg_engine


def test_seed_inserts_builtin_roles_with_explicit_ids(seeded):
    """前提校验：seed 确实用显式 id 插入（这才是需要同步序列的原因）。"""
    from sqlalchemy import text

    with seeded.connect() as c:
        ids = [r[0] for r in c.execute(
            text("SELECT id FROM roles ORDER BY id")).fetchall()]
    assert ids == [1, 2, 3], ids


def test_sequence_drift_is_real_before_sync(seeded):
    """未同步时自增插入必撞主键 —— 证明这个 bug 真实存在。"""
    from sqlalchemy import text
    from sqlalchemy.exc import IntegrityError

    with pytest.raises(IntegrityError):
        with seeded.begin() as c:
            c.execute(text(
                "INSERT INTO roles (name, side, is_system, remark) "
                "VALUES ('漂移测试','internal',false,'') RETURNING id"))


def test_sync_sequences_fixes_drift(seeded):
    """同步后自增 id 从 4 开始，连续插入不冲突。"""
    from sqlalchemy import text
    from app.core.database import sync_sequences

    sync_sequences(seeded)
    ids = []
    with seeded.begin() as c:
        for n in ("角色A", "角色B", "角色C"):
            ids.append(c.execute(
                text("INSERT INTO roles (name, side, is_system, remark) "
                     "VALUES (:n,'internal',false,'') RETURNING id"),
                {"n": n}).scalar())
    assert ids == [4, 5, 6], ids


def test_sync_sequences_never_moves_backwards(seeded):
    """重复同步不得把已前进的序列往回拉（否则 ID 复用）。"""
    from sqlalchemy import text
    from app.core.database import sync_sequences

    with seeded.begin() as c:
        first = c.execute(text(
            "INSERT INTO roles (name, side, is_system, remark) "
            "VALUES ('回退测试1','internal',false,'') RETURNING id")).scalar()

    sync_sequences(seeded)          # 序列已领先于 max(id)，应保持不动

    with seeded.begin() as c:
        nxt = c.execute(text(
            "INSERT INTO roles (name, side, is_system, remark) "
            "VALUES ('回退测试2','internal',false,'') RETURNING id")).scalar()
    assert nxt == first + 1, f"序列被回退：{first} → {nxt}"


def test_users_sequence_also_synced(seeded):
    """users 表同样走显式 id（admin=1），序列也必须对齐。"""
    from sqlalchemy import text
    from app.core.database import sync_sequences

    sync_sequences(seeded)
    with seeded.begin() as c:
        uid = c.execute(text(
            "INSERT INTO users (username, password_hash, name, phone, email, "
            "is_superuser, is_active, token_version, must_change_password, "
            "deleted_at, created_at) VALUES ('u_seq','x','U', '', '', false, "
            "true, 0, false, '1970-01-01', now()) RETURNING id")).scalar()
    assert uid == 2, f"users 序列未对齐，实际 id={uid}"


def test_alive_epoch_comparison_on_pg(seeded):
    """alive() 用等值比较（曾用 LIKE，PG 上 DateTime 不支持 LIKE）。"""
    from sqlalchemy import text
    from app.models import EPOCH, User, alive

    from sqlalchemy.orm import sessionmaker
    Session = sessionmaker(bind=seeded)
    db = Session()
    try:
        cnt = db.query(User).filter(alive(User.deleted_at)).count()
        assert cnt >= 1, "EPOCH 哨兵等值比较在 PG 上失效"
    finally:
        db.close()


def test_identity_and_serial_both_discovered(seeded):
    """发现逻辑要同时覆盖 SERIAL 与 IDENTITY 列。

    本项目模型用 Integer primary_key（PG 下是 SERIAL），
    这里断言至少发现了多张表，且 roles/users 都在其中。
    """
    from sqlalchemy import text

    from app.core.database import _SAFE_IDENT, sync_sequences

    _DISCOVER_SQL = """
    SELECT c.relname, a.attname
    FROM pg_class c
    JOIN pg_namespace n ON n.oid = c.relnamespace
    JOIN pg_attribute a ON a.attrelid = c.oid
    LEFT JOIN pg_attrdef d ON d.adrelid = c.oid AND d.adnum = a.attnum
    WHERE c.relkind = 'r'
      AND a.attnum > 0
      AND NOT a.attisdropped
      AND pg_table_is_visible(c.oid)
      AND (a.attidentity IN ('a','d')
           OR (d.adbin IS NOT NULL
               AND pg_get_expr(d.adbin, d.adrelid) LIKE 'nextval(%'))
    """
    with seeded.connect() as c:
        rows = c.execute(text(_DISCOVER_SQL)).fetchall()
    tables = {t for t, _ in rows}
    assert "roles" in tables and "users" in tables, tables
    assert len(tables) >= 20, f"只发现 {len(tables)} 张表的自增列，可能漏检"
    # 所有发现的标识符都必须通过白名单（否则会被跳过，等于没同步）
    bad = [(t, col) for t, col in rows
           if not _SAFE_IDENT.match(t) or not _SAFE_IDENT.match(col)]
    assert not bad, f"有标识符被白名单拦截，这些列不会同步：{bad}"
