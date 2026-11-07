"""人员接口回归：软删判定在 SQLite（naive datetime）下必须正确。

历史缺陷：personnel.py 用 `user.deleted_at != EPOCH` 判定存活。
EPOCH 是 aware datetime，而 SQLite 读回 naive datetime，
两者比较恒为 True → 所有用户被误判为已删除 →
改号/删号一律 404、列表 is_deleted 全部显示 True。

生产 PG 无此问题（timestamptz 读回仍带 tzinfo），所以 CI 全绿却掩盖了
SQLite 环境的彻底损坏。本测试直接在模型层锁死判定语义。
"""
import os
import tempfile

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

os.environ.setdefault("CLGLXT_DATA_DIR", tempfile.mkdtemp())

from app.models import (Base, Department, EPOCH, User, is_deleted,  # noqa: E402
                        utcnow_naive)


@pytest.fixture()
def db():
    path = os.path.join(tempfile.mkdtemp(), "softdel.db")
    engine = create_engine(f"sqlite:///{path}")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(Department(id=1, name="D1", remark=""))
        s.flush()
        s.add(User(id=1, username="u1", name="用户一", phone="", email="",
                   password_hash="x", must_change_password=False,
                   is_active=True, is_superuser=False, token_version=0,
                   deleted_at=EPOCH, department_id=1))
        s.commit()
    yield engine


def test_sqlite_reads_naive_datetime(db):
    """先固化前提：SQLite 读回的 deleted_at 没有 tzinfo。"""
    with Session(db) as s:
        u = s.get(User, 1)
        assert u.deleted_at.tzinfo is None, "前提变化：SQLite 现在保留 tzinfo 了"


def test_epoch_comparison_is_always_true_on_sqlite(db):
    """固化历史 bug：直接比较 aware EPOCH 会恒为不等。"""
    with Session(db) as s:
        u = s.get(User, 1)
        assert (u.deleted_at != EPOCH) is True, (
            "若此断言失败说明 naive/aware 行为已变，需重新评估判定逻辑")


def test_new_user_is_not_deleted(db):
    """修复后：新用户必须被判为存活。"""
    with Session(db) as s:
        u = s.get(User, 1)
        assert is_deleted(u) is False


def test_soft_deleted_user_is_detected(db):
    """软删后必须被判为已删除。"""
    with Session(db) as s:
        u = s.get(User, 1)
        u.deleted_at = utcnow_naive()
        s.commit()
    with Session(db) as s:
        assert is_deleted(s.get(User, 1)) is True


def test_personnel_update_and_delete_work_on_sqlite(db):
    """端到端：改号/删号在 SQLite 下必须成功（修复前恒 404）。"""
    from fastapi.testclient import TestClient

    from app.core import database as dbmod
    from app.core.database import get_db
    from app.main import app

    def _override():
        s = Session(db)
        try:
            yield s
        finally:
            s.close()

    # 造一个持 system:user_manage 的超管
    with Session(db) as s:
        s.add(User(id=99, username="boss", name="管理", phone="", email="",
                   password_hash="x", must_change_password=False,
                   is_active=True, is_superuser=True, token_version=0,
                   deleted_at=EPOCH))
        s.commit()
        boss = s.get(User, 99)
        from app.core.security import create_access_token
        token = create_access_token(boss.id, boss.token_version, True)

    app.dependency_overrides[get_db] = _override
    try:
        client = TestClient(app)
        h = {"Authorization": f"Bearer {token}"}
        r = client.put("/api/personnel/1", params={"name": "改名后"}, headers=h)
        assert r.status_code == 200, f"改号失败（修复前应为 404）：{r.text}"
        assert r.json().get("ok") is True

        r = client.get("/api/personnel", headers=h)
        assert r.status_code == 200
        rows = {u["id"]: u for u in r.json()}
        assert rows[1]["name"] == "改名后", "改名未生效"
        assert rows[1]["is_deleted"] is False, "存活用户被误标为已删除"

        r = client.delete("/api/personnel/1", headers=h)
        assert r.status_code == 200, f"删号失败：{r.text}"
    finally:
        app.dependency_overrides.pop(get_db, None)