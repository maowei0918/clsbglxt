#!/usr/bin/env python3
"""批次3 安全专项回归：部门隔离 / 汽修厂隔离 / 权限点 / 照片 IDOR / 越权写。

覆盖模块：
  * vehicle_use   临时用车（列表部门隔离、创建时忽略客户端 department_id）
  * repairs       维修单列表可见性（超管 / 汽修厂 / 内部部门 / 本人提报）
  * repairs2      维修单详情 _can_view_order 与列表同口径
  * vehicle_account 台账费用金额需 vehicle_account:view + 车辆可见
  * fence         围栏点列表按可见车牌过滤
  * daily_check   日检记录部门隔离、删除需 daily_check:manage
  * daily_check_photos  照片 IDOR（未关联仅本人、已关联按部门）

实现方式：TestClient + dependency_overrides 注入身份与 DB，
不依赖登录接口 / Redis / JWT，保证 CI 稳定。

运行：
    cd server && python -m pytest tests/test_security_batch3.py -v
"""
from __future__ import annotations

import os
import sys
import tempfile
from datetime import date

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# 测试环境变量必须在导入 app 之前设置（强制覆盖 .env / 外层环境）
os.environ["CLGLXT_JWT_SECRET"] = "x" * 64
os.environ["CLGLXT_DATABASE_URL"] = "sqlite:///./test_batch3.db"
# 把数据目录（含 uploads）隔离到临时目录，避免污染 server/data
# 存成模块级变量，供 module 级 teardown 回收（否则每跑一次在 /tmp 留一个目录）
_B3_DATA_DIR = tempfile.mkdtemp(prefix="clglxt_b3_")
os.environ["CLGLXT_DATA_DIR"] = _B3_DATA_DIR

from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.database import Base, get_db  # noqa: E402
from app.core.deps import get_current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    DailyCheckPhoto,
    DailyCheckRecord,
    DailyCheckTemplate,
    Department,
    FencePoint,
    RepairOrder,
    Role,
    RolePermission,
    Supplier,
    TempVehicleUse,
    User,
    UserRole,
    Vehicle,
    VehicleType,
)
import json as _json


@pytest.fixture(scope="module")
def engine():
    eng = create_engine("sqlite:///./test_batch3.db",
                        connect_args={"check_same_thread": False})
    Base.metadata.drop_all(eng)
    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()
    try:
        os.remove("test_batch3.db")
    except OSError:
        pass


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
def _fake_redis():
    import app.core.redis_client as rc
    real = rc._redis
    rc._redis = _FakeRedis()
    yield
    rc._redis = real
    import shutil
    shutil.rmtree(_B3_DATA_DIR, ignore_errors=True)


@pytest.fixture(scope="module")
def SessionLocal(engine):
    return sessionmaker(bind=engine, autocommit=False, autoflush=False)


@pytest.fixture(scope="module")
def seed(SessionLocal):
    """构造两部门 + 汽修厂 + 多身份用户 + 数据。"""
    db = SessionLocal()
    try:
        d1 = Department(name="运输一部", remark="D1")
        d2 = Department(name="运输二部", remark="D2")
        db.add_all([d1, d2])
        db.flush()

        shop = Supplier(name="顺达汽修", is_active=True, serves_all_depts=False,
                        dept_ids=f"[{d1.id}]")
        db.add(shop)
        db.flush()

        def mkuser(name, **kw):
            u = User(username=name, password_hash="x", is_active=1,
                     name=name, **kw)
            db.add(u)
            db.flush()
            return u

        def grant(user, *perms):
            """为该用户建唯一角色并授权（Role.name 有 unique 约束）。"""
            r = Role(name=f"role-{user.username}", side="internal")
            db.add(r)
            db.flush()
            db.add(UserRole(user_id=user.id, role_id=r.id))
            for p in perms:
                db.add(RolePermission(role_id=r.id, perm_code=p))
            return r

        su = mkuser("super", is_superuser=1)
        # 内部账号：仅本部门，无 data:scope_all
        u1 = mkuser("inner1", department_id=d1.id, is_superuser=0)
        u2 = mkuser("inner2", department_id=d2.id, is_superuser=0)
        # 汽修厂账号（带 repair:report，确保测到「汽修厂禁止创建」而非权限缺失）
        ushop = mkuser("shopuser", supplier_id=shop.id, is_superuser=0)
        # 无任何权限点的普通内部账号（验证 require_perm 兜底）
        u0 = mkuser("inner0", department_id=d1.id, is_superuser=0)
        # 有 data:scope_all 的内部账号
        uall = mkuser("innerall", department_id=d1.id, is_superuser=0)

        _base = ("vehicle_account:view", "repair:report", "fence:view")
        grant(u1, *_base)
        grant(u2, *_base, "daily_check:manage")
        grant(ushop, "repair:report")
        grant(uall, "data:scope_all", *_base)

        vt = VehicleType(name="卡车", remark="测试车型")
        db.add(vt)
        db.flush()

        v1 = Vehicle(plate_no="京A00001", department_id=d1.id,
                     vehicle_type_id=vt.id, nature="自有", brand="东风", status="在用")
        v2 = Vehicle(plate_no="京A00002", department_id=d2.id,
                     vehicle_type_id=vt.id, nature="自有", brand="解放", status="在用")
        db.add_all([v1, v2])
        db.flush()

        # 维修单：d1 车辆派给汽修厂；d2 车辆未派厂
        o1 = RepairOrder(order_no="WX1", vehicle_id=v1.id, reporter_id=u1.id,
                         current_seq=1, status="processing",
                         target_supplier_id=shop.id, description="刹车异响")
        o2 = RepairOrder(order_no="WX2", vehicle_id=v2.id, reporter_id=u2.id,
                         current_seq=1, status="processing",
                         target_supplier_id=None, description="轮胎漏气")
        # o3：d2 车辆但由 u1（d1）提报 → 本人提报应可见
        o3 = RepairOrder(order_no="WX3", vehicle_id=v2.id, reporter_id=u1.id,
                         current_seq=1, status="processing",
                         target_supplier_id=None, description="跨部门提报")
        db.add_all([o1, o2, o3])

        # 临时用车：各部门一条
        db.add_all([
            TempVehicleUse(plate_no="京A00001", department_id=d1.id,
                           start_date=date(2026, 1, 5), end_date=date(2026, 1, 6),
                           reason="外派", created_by=u1.id),
            TempVehicleUse(plate_no="京A00002", department_id=d2.id,
                           start_date=date(2026, 1, 7), end_date=date(2026, 1, 8),
                           reason="外派", created_by=u2.id),
        ])

        # 围栏点
        db.add_all([
            FencePoint(vehicle_id=v1.id, plate_no=v1.plate_no, input_text="家",
                       address="A", enabled=True),
            FencePoint(vehicle_id=v2.id, plate_no=v2.plate_no, input_text="厂",
                       address="B", enabled=True),
        ])

        # 日检模板 + 记录（items/results/photos 均为 JSON 字符串列）
        tpl = DailyCheckTemplate(name="日常",
                                 items=_json.dumps(
                                     [{"key": "brake", "label": "刹车"}],
                                     ensure_ascii=False))
        db.add(tpl)
        db.flush()
        r1 = DailyCheckRecord(vehicle_id=v1.id, user_id=u1.id,
                              check_date=date(2026, 1, 5),
                              results="[]", has_abnormal=False, photos="[]")
        r2 = DailyCheckRecord(vehicle_id=v2.id, user_id=u2.id,
                              check_date=date(2026, 1, 6),
                              results="[]", has_abnormal=False, photos="[]")
        db.add_all([r1, r2])
        db.flush()

        # 照片：p_orphan 未关联（u2 上传）、p_linked 关联 d1 记录（u1 上传）
        from pathlib import Path
        from app.core.config import UPLOAD_DIR
        sub = Path(UPLOAD_DIR) / "daily_check" / "test"
        sub.mkdir(parents=True, exist_ok=True)
        (sub / "a.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        (sub / "b.jpg").write_bytes(b"\xff\xd8\xff\xd9")
        p_orphan = DailyCheckPhoto(user_id=u2.id, saved_path="daily_check/test/a.jpg",
                                   mime="image/jpeg", size=4, record_id=None)
        p_linked = DailyCheckPhoto(user_id=u1.id, saved_path="daily_check/test/b.jpg",
                                   mime="image/jpeg", size=4, record_id=r1.id)
        db.add_all([p_orphan, p_linked])
        db.commit()

        ids = {
            "d1": d1.id, "d2": d2.id, "shop": shop.id,
            "su": su.id, "u1": u1.id, "u2": u2.id, "ushop": ushop.id,
            "uall": uall.id, "r_all": None, "u0": u0.id,
            "v1": v1.id, "v2": v2.id,
            "o1": o1.id, "o2": o2.id, "o3": o3.id,
            "r1": r1.id, "r2": r2.id,
            "p_orphan": p_orphan.id, "p_linked": p_linked.id,
            "tpl": tpl.id,
        }
        yield ids
    finally:
        db.close()


@pytest.fixture(scope="module")
def client(SessionLocal, seed):
    """共享一个 TestClient；按用例切换身份，避免重复启动 portal。"""
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


# ─────────────── 临时用车部门隔离 ───────────────

def test_vehicle_use_list_dept_isolation(client, seed):
    r = as_user(client, seed["u1"]).get("/api/vehicle-use")
    assert r.status_code == 200
    plates = {x["plate_no"] for x in r.json()}
    assert plates == {"京A00001"}, f"d1 用户越界看到 {plates}"

    r = as_user(client, seed["su"]).get("/api/vehicle-use")
    assert r.status_code == 200
    assert {x["plate_no"] for x in r.json()} == {"京A00001", "京A00002"}


def test_vehicle_use_list_ignores_client_department(client, seed):
    """显式传不可见部门应被拒（防客户端 department_id 越权）。"""
    r = as_user(client, seed["u1"]).get(
        f"/api/vehicle-use?department_id={seed['d2']}")
    assert r.status_code == 403, r.text
    # 不传部门时只看本部门
    r = as_user(client, seed["u1"]).get("/api/vehicle-use")
    assert r.status_code == 200
    assert {x["plate_no"] for x in r.json()} == {"京A00001"}


def test_vehicle_use_create_ignores_client_department(client, seed, SessionLocal):
    """创建时客户端 department_id 被忽略，以车辆部门为准。"""
    r = as_user(client, seed["u1"]).post(
        "/api/vehicle-use",
        params={"plate_no": "京A00001", "start_date": "2026-02-01",
                "end_date": "2026-02-02", "reason": "测试",
                "department_id": seed["d2"]})
    assert r.status_code == 200, r.text
    db = SessionLocal()
    try:
        row = db.query(TempVehicleUse).filter(
            TempVehicleUse.start_date == date(2026, 2, 1)).first()
        assert row is not None
        assert row.department_id == seed["d1"]
    finally:
        db.close()


# ─────────────── 维修单可见性 ───────────────

def _order_ids(r):
    body = r.json()
    rows = body[0] if isinstance(body, list) else body.get("items", [])
    return {x["id"] for x in rows}


def test_repairs_list_superuser_sees_all(client, seed):
    r = as_user(client, seed["su"]).get("/api/repairs")
    assert r.status_code == 200
    assert _order_ids(r) == {seed["o1"], seed["o2"], seed["o3"]}


def test_repairs_list_shop_sees_assigned_and_own(client, seed):
    """汽修厂：只看指派给本厂 或 本人提报。"""
    r = as_user(client, seed["ushop"]).get("/api/repairs")
    assert r.status_code == 200
    assert _order_ids(r) == {seed["o1"]}, r.text


def test_repairs_list_inner_dept_scope(client, seed):
    """内部账号：本部门车辆 + 本人提报（跨部门提报也可见）。"""
    r = as_user(client, seed["u1"]).get("/api/repairs")
    assert r.status_code == 200
    got = _order_ids(r)
    assert seed["o1"] in got
    assert seed["o3"] in got, "本人提报的跨部门工单应可见"
    assert seed["o2"] not in got, "他部门且非本人提报不应可见"


def test_repairs_list_scope_all_sees_all(client, seed):
    r = as_user(client, seed["uall"]).get("/api/repairs")
    assert r.status_code == 200
    assert _order_ids(r) == {seed["o1"], seed["o2"], seed["o3"]}


def test_repairs_detail_matches_list_visibility(client, seed):
    """详情与列表同口径：u1 可看 o3，不可看 o2。"""
    assert as_user(client, seed["u1"]).get(
        f"/api/repairs/{seed['o3']}").status_code == 200
    assert as_user(client, seed["u1"]).get(
        f"/api/repairs/{seed['o2']}").status_code == 403


def test_repairs_shop_cannot_create(client, seed):
    """汽修厂账号不能创建维修单（防自派单绕过审批）。"""
    r = as_user(client, seed["ushop"]).post(
        "/api/repairs", params={"vehicle_id": seed["v1"], "description": "x"})
    assert r.status_code == 403, r.text


def test_repairs_inner_cannot_report_other_dept_vehicle(client, seed):
    """内部账号不能给他部门车辆提报。"""
    r = as_user(client, seed["u1"]).post(
        "/api/repairs", params={"vehicle_id": seed["v2"], "description": "x"})
    assert r.status_code == 403, r.text


# ─────────────── 台账权限 ───────────────

def test_vehicle_account_requires_perm(client, seed):
    """无 vehicle_account:view 的内部账号应 403。"""
    r = as_user(client, seed["u0"]).get("/api/vehicle-account?plate_no=京A00001")
    assert r.status_code == 403, r.text


def test_vehicle_account_dept_scope(client, seed):
    """有权限但跨部门车辆应 403。"""
    r = as_user(client, seed["u1"]).get("/api/vehicle-account?plate_no=京A00002")
    assert r.status_code == 403, r.text


def test_vehicle_account_superuser_ok(client, seed):
    r = as_user(client, seed["su"]).get("/api/vehicle-account?plate_no=京A00001")
    assert r.status_code == 200, r.text
    assert r.json()["plate_no"] == "京A00001"


# ─────────────── 围栏点隔离 ───────────────

def test_fence_points_dept_isolation(client, seed):
    r = as_user(client, seed["u1"]).get("/api/fence/points")
    assert r.status_code == 200
    plates = {x["plate_no"] for x in r.json()}
    assert plates == {"京A00001"}, f"越界看到 {plates}"


def test_fence_add_point_other_dept_forbidden(client, seed):
    r = as_user(client, seed["u1"]).post(
        "/api/fence/points",
        params={"plate_no": "京A00002", "input_text": "家", "address": "X"})
    assert r.status_code == 403, r.text


# ─────────────── 日检记录隔离 ───────────────

def test_daily_check_records_dept_isolation(client, seed):
    r = as_user(client, seed["u1"]).get("/api/daily-check/records")
    assert r.status_code == 200
    ids = {x["id"] for x in r.json()["items"]}
    assert ids == {seed["r1"]}, f"越界看到 {ids}"


def test_daily_check_delete_requires_manage_perm(client, seed):
    """普通内部账号无 daily_check:manage → 403。"""
    r = as_user(client, seed["u1"]).delete(f"/api/daily-check/records/{seed['r1']}")
    assert r.status_code == 403, r.text


# ─────────────── 照片 IDOR ───────────────

def test_photo_orphan_only_owner_or_superuser(client, seed):
    """未关联记录的照片：非上传者一律 403。"""
    r = as_user(client, seed["u1"]).get(f"/api/daily-check/photos/{seed['p_orphan']}")
    assert r.status_code == 403, r.text
    r = as_user(client, seed["u2"]).get(f"/api/daily-check/photos/{seed['p_orphan']}")
    assert r.status_code == 200, r.text
    r = as_user(client, seed["su"]).get(f"/api/daily-check/photos/{seed['p_orphan']}")
    assert r.status_code == 200, r.text


def test_photo_linked_by_dept_scope(client, seed):
    """已关联 d1 记录：d1 用户可读，d2 用户 403。"""
    r = as_user(client, seed["u1"]).get(f"/api/daily-check/photos/{seed['p_linked']}")
    assert r.status_code == 200, r.text
    r = as_user(client, seed["u2"]).get(f"/api/daily-check/photos/{seed['p_linked']}")
    assert r.status_code == 403, r.text


def test_photo_delete_only_owner(client, seed):
    """非上传者不能删照片。"""
    r = as_user(client, seed["u1"]).delete(f"/api/daily-check/photos/{seed['p_orphan']}")
    assert r.status_code == 403, r.text
