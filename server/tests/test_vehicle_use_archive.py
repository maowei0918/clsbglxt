#!/usr/bin/env python3
"""临时用车归档功能回归：列表过滤 / 归档恢复 / 权限 / 部门隔离 / 自动归档任务。

实现方式：TestClient + dependency_overrides 注入身份与 DB，
不依赖登录接口 / Redis / JWT，保证 CI 稳定。

运行：
    cd server && python -m pytest tests/test_vehicle_use_archive.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date, timedelta

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 测试环境变量必须在导入 app 之前设置（强制覆盖 .env / 外层环境）
os.environ["CLGLXT_JWT_SECRET"] = "y" * 64
_TMP = tempfile.mkdtemp(prefix="clglxt_arch_")
os.environ["CLGLXT_DATA_DIR"] = _TMP
os.environ["CLGLXT_DATABASE_URL"] = f"sqlite:///{_TMP}/archive.db"
# 自动归档宽限天数设小，便于测试过期记录被归档
os.environ["CLGLXT_VEHICLE_USE_AUTO_ARCHIVE_DAYS"] = "30"

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.main import app  # noqa: E402
from app.core.database import Base, get_db  # noqa: E402
from app.core.deps import get_current_user  # noqa: E402
from app.models import (  # noqa: E402
    Department, Role, RolePermission, TempVehicleUse, User, UserRole, Vehicle,
    VehicleType, now as db_now,
)

TODAY = date.today()
CUTOFF = TODAY - timedelta(days=30)  # 与 VEHICLE_USE_AUTO_ARCHIVE_DAYS 一致


class _FakeRedis:
    """内存版 Redis，避免测试依赖真实 Redis 连接。"""
    _store: dict = {}

    def get(self, key):
        return self._store.get(key)

    def setex(self, key, ttl, value):
        self._store[key] = value

    def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)

    def ping(self):
        return True

    def scan_iter(self, **kwargs):
        return iter(list(self._store.keys()))


@pytest.fixture(scope="module", autouse=True)
def _infra():
    import app.core.redis_client as rc
    real = rc._redis
    rc._redis = _FakeRedis()
    try:
        yield
    finally:
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)
        rc._redis = real


@pytest.fixture(scope="module")
def SessionLocal():
    eng = create_engine(os.environ["CLGLXT_DATABASE_URL"],
                        connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    sm = sessionmaker(bind=eng, autocommit=False, autoflush=False)
    yield sm
    eng.dispose()
    Base.metadata.drop_all(eng)


@pytest.fixture(scope="module")
def seed(SessionLocal):
    db = SessionLocal()
    try:
        d1 = Department(name="运输一部", remark="D1")
        d2 = Department(name="运输二部", remark="D2")
        db.add_all([d1, d2])
        db.flush()

        vt = VehicleType(name="卡车", remark="测试车型")
        db.add(vt)
        db.flush()
        v1 = Vehicle(plate_no="京A00001", department_id=d1.id,
                     vehicle_type_id=vt.id, nature="自有", status="在用")
        db.add(v1)
        db.flush()

        def mkuser(name, **kw):
            u = User(username=name, password_hash="x", is_active=1,
                     name=name, **kw)
            db.add(u)
            db.flush()
            return u

        def grant(user, *perms):
            r = Role(name=f"role-{user.username}", side="internal")
            db.add(r)
            db.flush()
            db.add(UserRole(user_id=user.id, role_id=r.id))
            for p in perms:
                db.add(RolePermission(role_id=r.id, perm_code=p))
            return r

        su = mkuser("super", is_superuser=1)
        # d1 有 vehicle:manage
        mgr1 = mkuser("mgr1", department_id=d1.id, is_superuser=0)
        grant(mgr1, "vehicle:manage")
        # d2 有 vehicle:manage
        mgr2 = mkuser("mgr2", department_id=d2.id, is_superuser=0)
        grant(mgr2, "vehicle:manage")
        # d1 无 vehicle:manage（对照：测 403）
        noperm = mkuser("noperm", department_id=d1.id, is_superuser=0)
        grant(noperm, "vehicle_use:view")

        # 临时用车：过期未归档 / 未过期 / 已归档
        expired = TempVehicleUse(plate_no="京A00001", department_id=d1.id,
                                 vehicle_id=v1.id, vehicle_type_id=vt.id,
                                 nature="自有",
                                 start_date=CUTOFF - timedelta(days=3),
                                 end_date=CUTOFF - timedelta(days=2),
                                 reason="外派", created_by=mgr1.id,
                                 archived=False)
        active = TempVehicleUse(plate_no="京A00001", department_id=d1.id,
                                vehicle_id=v1.id, vehicle_type_id=vt.id,
                                nature="自有",
                                start_date=TODAY - timedelta(days=1),
                                end_date=TODAY + timedelta(days=1),
                                reason="外派", created_by=mgr1.id,
                                archived=False)
        archived = TempVehicleUse(plate_no="京A00002", department_id=d2.id,
                                  start_date=CUTOFF - timedelta(days=10),
                                  end_date=CUTOFF - timedelta(days=9),
                                  reason="外派", created_by=mgr2.id,
                                  archived=True,
                                  archived_at=db_now())
        db.add_all([expired, active, archived])
        db.commit()

        ids = {
            "d1": d1.id, "d2": d2.id, "su": su.id,
            "mgr1": mgr1.id, "mgr2": mgr2.id, "noperm": noperm.id,
            "expired": expired.id, "active": active.id, "archived": archived.id,
        }
        yield ids
    finally:
        db.close()


@pytest.fixture(scope="module")
def client(SessionLocal, seed):
    db = SessionLocal()

    def _get_db():
        try:
            yield db
        finally:
            pass

    app.dependency_overrides[get_db] = _get_db
    _tc = TestClient(app, raise_server_exceptions=True)

    def _make(user_id: int):
        def _get_user():
            return db.get(User, user_id)
        app.dependency_overrides[get_current_user] = _get_user
        return _tc

    yield _make
    _tc.close()
    db.close()
    app.dependency_overrides.clear()


def as_user(client, uid):
    return client(uid)


# ─────────────── 列表过滤 ───────────────

def test_list_hides_archived_by_default(client, seed):
    r = as_user(client, seed["mgr1"]).get("/api/vehicle-use")
    assert r.status_code == 200
    plates = {x["plate_no"] for x in r.json()}
    assert "京A00001" in plates  # active 可见
    assert "京A00002" not in plates  # archived 默认隐藏
    # 默认过滤 archived
    for x in r.json():
        assert x["archived"] is False


def test_list_include_archived(client, seed):
    """include_archived=true 时能看到已归档记录（用超管跨部门验证）。"""
    r = as_user(client, seed["su"]).get(
        "/api/vehicle-use", params={"include_archived": "true"})
    assert r.status_code == 200
    rows = {x["plate_no"]: x for x in r.json()}
    assert "京A00002" in rows
    assert rows["京A00002"]["archived"] is True
    assert rows["京A00002"]["archived_at"] is not None


def test_include_archived_still_respects_dept_isolation(client, seed):
    """部门隔离优先于 include_archived：mgr1 看不到 d2 的已归档记录。"""
    r = as_user(client, seed["mgr1"]).get(
        "/api/vehicle-use", params={"include_archived": "true"})
    assert r.status_code == 200
    assert "京A00002" not in {x["plate_no"] for x in r.json()}


# ─────────────── 归档 / 恢复 ───────────────

def test_archive_requires_vehicle_manage(client, seed):
    # 无权限 → 403
    r = as_user(client, seed["noperm"]).post(
        f"/api/vehicle-use/{seed['active']}/archive")
    assert r.status_code == 403, r.text
    # 有权限 → 200
    r = as_user(client, seed["mgr1"]).post(
        f"/api/vehicle-use/{seed['active']}/archive")
    assert r.status_code == 200, r.text
    assert r.json().get("archived") is True


def test_archive_is_idempotent(client, seed):
    r1 = as_user(client, seed["mgr1"]).post(
        f"/api/vehicle-use/{seed['active']}/archive")
    assert r1.status_code == 200
    r2 = as_user(client, seed["mgr1"]).post(
        f"/api/vehicle-use/{seed['active']}/archive")
    assert r2.status_code == 200
    assert r2.json().get("archived") is True


def test_restore_after_archive(client, seed):
    # 先归档再恢复
    as_user(client, seed["mgr1"]).post(
        f"/api/vehicle-use/{seed['active']}/archive")
    r = as_user(client, seed["mgr1"]).post(
        f"/api/vehicle-use/{seed['active']}/restore")
    assert r.status_code == 200, r.text
    assert r.json().get("archived") is False
    # 恢复后列表默认可见
    r = as_user(client, seed["mgr1"]).get("/api/vehicle-use")
    assert "京A00001" in {x["plate_no"] for x in r.json()}


def test_archive_dept_isolation(client, seed):
    """mgr1（d1）不能归档/恢复 d2 的记录。"""
    # archive 用 archived 记录（d2）：check_dept 应先拦下，archived 状态不变
    r = as_user(client, seed["mgr1"]).post(
        f"/api/vehicle-use/{seed['archived']}/archive")
    assert r.status_code == 403, r.text
    # restore 同理
    r = as_user(client, seed["mgr1"]).post(
        f"/api/vehicle-use/{seed['archived']}/restore")
    assert r.status_code == 403, r.text


# ─────────────── 自动归档任务 ───────────────

def test_auto_archive_expired(SessionLocal, seed, monkeypatch):
    """直接调用任务函数：过期未归档的记录被归档，未过期不动。"""
    from app.workers.scheduled_tasks import auto_archive_expired_vehicle_uses
    import app.core.config as cfg

    # 任务函数在 worker 进程里自己 create_engine(DATABASE_URL)，
    # 这里把模块级常量指向本测试的库，避免被其它测试文件先导入时锁定的值污染。
    monkeypatch.setattr(cfg, "DATABASE_URL", f"sqlite:///{_TMP}/archive.db")

    db = SessionLocal()
    try:
        before_expired = db.get(TempVehicleUse, seed["expired"])
        before_active = db.get(TempVehicleUse, seed["active"])
        assert before_expired.archived is False
        assert before_active.archived is False
    finally:
        db.close()

    result = auto_archive_expired_vehicle_uses()
    assert result["archived"] >= 1

    db = SessionLocal()
    try:
        after_expired = db.get(TempVehicleUse, seed["expired"])
        after_active = db.get(TempVehicleUse, seed["active"])
        assert after_expired.archived is True
        assert after_expired.archived_at is not None
        assert after_active.archived is False
    finally:
        db.close()
