#!/usr/bin/env python3
"""repairs2 抢单 CAS 并发专项。

验证 claim_quote 的条件 UPDATE 语义：
  * N 个具备 repair:quote 权限的账号并发抢同一维修单
  * 恰好 1 个成功（200），其余全部 409
  * 落库 quote_owner_id 与成功者一致，且无二次覆盖
  * 已被抢占的单再次抢，仍返回 409

实现要点：
  * 用 SQLite 文件库（非 :memory:），多线程各持独立 Session
  * 直接通过 TestClient 打 HTTP，走真实依赖链（get_db / 鉴权覆盖）
  * 用 Barrier 同时放行，放大竞态窗口

隔离设计（重要）：
  * 「基础设施」与「业务数据」分层：
      - _infra（module/autouse）：替换 Redis、最终拆库、删临时文件，不含业务数据
      - case（function）：每个测试前 drop_all + create_all + 重建账号与维修单
  * 因此每个测试拿到的都是**未被抢过**的干净订单，测试之间零共享、可乱序、
    可单独运行，不依赖执行顺序。
"""
from __future__ import annotations

import os
import sys
import tempfile
import threading
import atexit
import shutil
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

_TMP = tempfile.mkdtemp(prefix="clglxt_claim_")
atexit.register(shutil.rmtree, _TMP, True)  # 异常退出也回收（含收集阶段崩溃）
os.environ["CLGLXT_JWT_SECRET"] = "x" * 64
os.environ["CLGLXT_DATABASE_URL"] = f"sqlite:///{_TMP}/claim.db"
os.environ["CLGLXT_DATA_DIR"] = _TMP

from fastapi import Request  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlalchemy import create_engine  # noqa: E402
from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.database import Base, get_db  # noqa: E402
from app.core.deps import get_current_user  # noqa: E402
from app.main import app  # noqa: E402
from app.models import (  # noqa: E402
    Department, RepairOrder, Role, RolePermission, Supplier,
    User, UserRole, Vehicle, VehicleType,
)

ENGINE = create_engine(
    f"sqlite:///{_TMP}/claim.db",
    connect_args={"check_same_thread": False, "timeout": 30},
)
SessionLocal = sessionmaker(bind=ENGINE, autoflush=False, expire_on_commit=False)

N_CLAIMERS = 8


class _FakeRedis:
    """权限缓存用内存版，避免依赖真实 Redis。"""
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
    """模块级基础设施：替换 Redis、最终拆库。不持有任何业务数据。"""
    import app.core.redis_client as rc
    real = rc._redis
    rc._redis = _FakeRedis()
    try:
        yield
    finally:
        Base.metadata.drop_all(ENGINE)
        ENGINE.dispose()
        rc._redis = real
        # 整个临时目录一起回收：只删 claim.db 会留下空目录，反复跑会堆积在 /tmp
        import shutil
        shutil.rmtree(_TMP, ignore_errors=True)


@pytest.fixture(scope="module")
def client():
    """共享 TestClient；身份通过 X-Test-Uid 请求头注入。"""
    def _get_db():
        db = SessionLocal()
        try:
            yield db
        finally:
            db.close()

    def _get_user(request: Request):
        uid = int(request.headers.get("x-test-uid", "0"))
        db = SessionLocal()
        try:
            return db.get(User, uid)
        finally:
            db.close()

    app.dependency_overrides[get_db] = _get_db
    app.dependency_overrides[get_current_user] = _get_user
    tc = TestClient(app, raise_server_exceptions=True)
    yield tc
    tc.close()
    app.dependency_overrides.clear()


def _headers(uid: int) -> dict:
    return {"X-Test-Uid": str(uid)}


def _seed_case() -> dict:
    """重建一份干净业务数据：部门/车型/车辆/供应商 + N 个账号 + 1 个未抢维修单。"""
    _FakeRedis._store.clear()
    Base.metadata.drop_all(ENGINE)
    Base.metadata.create_all(ENGINE)

    db = SessionLocal()
    try:
        d = Department(name="一部", remark="")
        db.add(d)
        db.flush()
        vt = VehicleType(name="卡车")
        db.add(vt)
        db.flush()
        v = Vehicle(plate_no="京A90001", department_id=d.id,
                    vehicle_type_id=vt.id, nature="自有")
        db.add(v)
        db.flush()
        shop = Supplier(name="抢单测试厂", serves_all_depts=True)
        db.add(shop)
        db.flush()

        uids = []
        for i in range(N_CLAIMERS):
            u = User(username=f"claimer{i}", password_hash="x", name=f"c{i}",
                     supplier_id=shop.id, is_active=1, is_superuser=0)
            db.add(u)
            db.flush()
            r = Role(name=f"quote-role-{i}", side="external")
            db.add(r)
            db.flush()
            db.add(UserRole(user_id=u.id, role_id=r.id))
            db.add(RolePermission(role_id=r.id, perm_code="repair:quote"))
            uids.append(u.id)

        o = RepairOrder(order_no="WX-CLAIM-1", vehicle_id=v.id,
                        reporter_id=uids[0], current_seq=1, status="processing",
                        target_supplier_id=shop.id, description="并发抢单")
        db.add(o)
        db.commit()

        return {"uids": uids, "order_id": o.id}
    finally:
        db.close()


@pytest.fixture(scope="function")
def case() -> dict:
    """每个测试独立、干净的业务数据；测试之间零共享，可单独运行 / 乱序。"""
    return _seed_case()


def test_claim_single_winner_under_concurrency(case, client):
    """8 并发抢同一单：恰好 1 个 200，其余 409，落库一致。"""
    tc = client
    uids = case["uids"]
    oid = case["order_id"]
    n = len(uids)
    barrier = threading.Barrier(n)

    def _claim(uid):
        barrier.wait(timeout=10)
        r = tc.post(f"/api/repairs/{oid}/quote/claim", headers=_headers(uid))
        return uid, r.status_code

    with ThreadPoolExecutor(max_workers=n) as pool:
        results = list(pool.map(_claim, uids))

    winners = [uid for uid, code in results if code == 200]
    conflicts = [uid for uid, code in results if code == 409]
    others = [(uid, code) for uid, code in results
              if code not in (200, 409)]

    assert not others, f"出现非预期状态码: {others}"
    assert len(winners) == 1, f"应恰好 1 个成功，实际 {len(winners)}: {results}"
    assert len(conflicts) == n - 1, f"其余应全部 409: {results}"

    db = SessionLocal()
    try:
        o = db.get(RepairOrder, oid)
        assert o.quote_owner_id == winners[0], (
            f"落库 owner={o.quote_owner_id} 与成功者 {winners[0]} 不一致")
    finally:
        db.close()


def test_second_claim_returns_409(case, client):
    """先抢成功再抢 -> 409，且 owner 不被二次覆盖。自给自足，不依赖其它测试。"""
    uids = case["uids"]
    oid = case["order_id"]

    first = client.post(f"/api/repairs/{oid}/quote/claim",
                        headers=_headers(uids[0]))
    assert first.status_code == 200, first.text

    second = client.post(f"/api/repairs/{oid}/quote/claim",
                         headers=_headers(uids[-1]))
    assert second.status_code == 409, second.text

    db = SessionLocal()
    try:
        o = db.get(RepairOrder, oid)
        assert o.quote_owner_id == uids[0], (
            f"二次抢单覆盖了 owner：落库 {o.quote_owner_id}，应为 {uids[0]}")
    finally:
        db.close()
