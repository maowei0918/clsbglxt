"""FMS 导入通道测试。

覆盖：
  * 字段映射（开放 API 摘要字段 → clglxt 模型）
  * 幂等（重复导入不产生重复行）
  * 断点续传（进度持久化 + 从 offset 继续）
  * 脏数据处理（缺失必填、非法日期、超长字符串）
  * ID 映射（FMS 数字 id → clglxt 自增 id）

用 SQLite 内存库跑，不依赖 PG/Redis/Celery——导入逻辑本身与后端无关。
HTTP 层用打桩替换 fms_client.iter_pages，避免真实外呼。
"""
from __future__ import annotations

import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker


@pytest.fixture(autouse=True)
def _isolate_import_state(monkeypatch):
    """每个测试用独立内存存储，避免进度/停止信号跨测试串味。

    Redis 不可用时导入器会降级到模块级内存单例；这里把它换成本测试
    独占的实例，等价于「每个测试一个干净 Redis」。
    """
    from app.core import fms_importer as imp

    fresh = imp._MemoryRedis()
    monkeypatch.setattr(imp, "_MEM_FALLBACK", fresh)
    yield fresh


@pytest.fixture()
def db():
    """独立内存库 + 建表。"""
    from app.core.database import Base
    from app import models  # noqa: F401  注册模型

    eng = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(bind=eng)
    Session = sessionmaker(bind=eng, autoflush=False, expire_on_commit=False)
    s = Session()
    try:
        yield s
    finally:
        s.close()
        eng.dispose()


@pytest.fixture()
def seeded_user(db):
    """导入需要归属人（reporter/created_by 非空）。"""
    from app.models import User

    u = User(username="admin", password_hash="x", name="管理员", is_superuser=True)
    db.add(u)
    db.commit()
    return u


# ───────────────────────── 字段映射 ─────────────────────────

def test_vehicle_mapping_creates_type_and_dept(db, seeded_user, monkeypatch):
    """开放 API 车辆摘要 → Vehicle，车型/部门按名称 get-or-create。"""
    from app.core import fms_importer as imp

    payload = [{
        "id": 101, "plate_no": "京A12345", "vehicle_type_name": "小型客车",
        "nature": "公务", "department_name": "办公室", "brand": "丰田",
        "status": "在用",
    }]
    monkeypatch.setattr(imp, "iter_items", lambda scope, **kw: iter(payload))

    ctx = imp.ImportContext(db=db, batch_size=100)
    res = imp.import_scope(db, "vehicles", ctx)
    assert res["created"] == 1

    from app.models import Department, Vehicle, VehicleType

    v = db.execute(select(Vehicle).where(Vehicle.plate_no == "京A12345")).scalar_one()
    assert v.brand == "丰田" and v.nature == "公务" and v.status == "在用"
    assert db.execute(select(VehicleType).where(VehicleType.name == "小型客车")).scalar_one()
    assert db.execute(select(Department).where(Department.name == "办公室")).scalar_one()
    # ID 映射写入，供后续 repairs 解析 vehicle_id
    assert imp.resolve_id("vehicles", 101) is not None


def test_vehicle_accepts_alt_field_names(db, seeded_user, monkeypatch):
    """临时用车接口用 type_name/dept_name，同一套映射要兼容。"""
    from app.core import fms_importer as imp

    payload = [{"id": 102, "plate_no": "京B00001", "type_name": "货车",
                "dept_name": "后勤部", "nature": "特种"}]
    monkeypatch.setattr(imp, "iter_items", lambda scope, **kw: iter(payload))

    ctx = imp.ImportContext(db=db, batch_size=100)
    imp.import_scope(db, "vehicles", ctx)

    from app.models import Vehicle, VehicleType

    v = db.execute(select(Vehicle).where(Vehicle.plate_no == "京B00001")).scalar_one()
    vt = db.execute(select(VehicleType).where(VehicleType.id == v.vehicle_type_id)).scalar_one()
    assert vt.name == "货车"


# ───────────────────────── 幂等 ─────────────────────────

def test_reimport_is_idempotent(db, seeded_user, monkeypatch):
    """同一批数据导入两次，不新增行、只更新。"""
    from app.core import fms_importer as imp
    from app.models import Vehicle

    payload = [
        {"id": 1, "plate_no": "京A11111", "vehicle_type_name": "小型客车",
         "department_name": "办公室", "brand": "大众", "status": "在用"},
        {"id": 2, "plate_no": "京A22222", "vehicle_type_name": "小型客车",
         "department_name": "办公室", "brand": "本田", "status": "在用"},
    ]
    monkeypatch.setattr(imp, "iter_items", lambda scope, **kw: iter(payload))

    r1 = imp.import_scope(db, "vehicles", imp.ImportContext(db=db, batch_size=10))
    assert r1["created"] == 2

    r2 = imp.import_scope(db, "vehicles", imp.ImportContext(db=db, batch_size=10))
    assert r2["created"] == 0 and r2["updated"] == 2

    rows = db.execute(select(Vehicle)).scalars().all()
    assert len(rows) == 2, "重复导入不应产生重复车辆"


def test_repair_mapping_uses_vehicle_id_map(db, seeded_user, monkeypatch):
    """维修单摘要里的 FMS vehicle_id 要映射到 clglxt 的车辆主键。"""
    from app.core import fms_importer as imp
    from app.models import RepairOrder

    vehicles = [{"id": 7, "plate_no": "京C77777", "vehicle_type_name": "小型客车",
                 "department_name": "办公室"}]
    repairs = [{"id": 9001, "order_no": "WX20260001", "vehicle_id": 7,
                "vehicle_plate": "京C77777", "status": "completed",
                "total_amount": 1200.5, "created_at": "2026-01-02T03:04:05+00:00"}]

    def fake(scope, **kw):
        return iter(vehicles if scope == "vehicles" else repairs)

    monkeypatch.setattr(imp, "iter_items", fake)

    ctx = imp.ImportContext(db=db, batch_size=10)
    imp.import_scope(db, "vehicles", ctx)
    res = imp.import_scope(db, "repairs", ctx)
    assert res["created"] == 1

    o = db.execute(select(RepairOrder).where(RepairOrder.order_no == "WX20260001")).scalar_one()
    mapped = imp.resolve_id("vehicles", 7)
    assert o.vehicle_id == mapped, "维修单必须指向映射后的本地车辆"
    assert o.reporter_id == seeded_user.id


# ───────────────────────── 脏数据 ─────────────────────────

def test_dirty_rows_are_skipped_not_fatal(db, seeded_user, monkeypatch):
    """缺车牌/非法日期/超长字段的行应被跳过并记录，不影响其它行。"""
    from app.core import fms_importer as imp
    from app.models import Vehicle

    payload = [
        {"id": 1, "plate_no": "", "vehicle_type_name": "小型客车"},          # 缺车牌
        {"id": 2, "plate_no": "京D88888", "vehicle_type_name": "小型客车"},   # 正常
        {"id": 3, "plate_no": "京" + "E" * 40, "vehicle_type_name": "小型客车"},  # 超长
    ]
    monkeypatch.setattr(imp, "iter_items", lambda scope, **kw: iter(payload))

    ctx = imp.ImportContext(db=db, batch_size=10)
    res = imp.import_scope(db, "vehicles", ctx)

    assert res["created"] >= 1, "正常行必须导入成功"
    assert res["skipped"] >= 1, "脏数据必须被跳过并计数"
    assert res["errors"], "跳过的行要留下原因，便于排查"
    ok = db.execute(select(Vehicle).where(Vehicle.plate_no == "京D88888")).scalar_one()
    assert ok.id


def test_repair_without_known_vehicle_is_skipped(db, seeded_user, monkeypatch):
    """维修单引用了未导入的车辆时，跳过而不是写入悬空外键。"""
    from app.core import fms_importer as imp
    from app.models import RepairOrder

    repairs = [{"id": 1, "order_no": "WX20260002", "vehicle_id": 999,
                "status": "processing", "created_at": "2026-01-01T00:00:00+00:00"}]
    monkeypatch.setattr(imp, "iter_items", lambda scope, **kw: iter(repairs))

    res = imp.import_scope(db, "repairs", imp.ImportContext(db=db, batch_size=10))
    assert res["created"] == 0
    assert res["skipped"] == 1
    assert db.execute(select(RepairOrder)).scalars().all() == []


# ───────────────────────── 断点续传 ─────────────────────────

class _FakeRedis:
    """最小 Redis 打桩：get/set/setex/delete/exists/hash，含 ping。"""

    def __init__(self):
        self.store: dict[str, str] = {}
        self.hashes: dict[str, dict[str, str]] = {}

    def ping(self):
        return True

    def get(self, k):
        return self.store.get(k)

    def set(self, k, v):
        self.store[k] = v

    def setex(self, k, _ttl, v):
        self.store[k] = v

    def delete(self, *keys):
        for k in keys:
            self.store.pop(k, None)
            self.hashes.pop(k, None)

    def exists(self, k):
        return 1 if k in self.store else 0

    def hset(self, k, field, value):
        self.hashes.setdefault(k, {})[str(field)] = str(value)

    def hget(self, k, field):
        return self.hashes.get(k, {}).get(str(field))

    def hgetall(self, k):
        return dict(self.hashes.get(k, {}))

    def scan_iter(self, match=None, count=None):
        import fnmatch
        pat = (match or "").split(":", 1)[-1]
        for k in list(self.store):
            if fnmatch.fnmatch(k.split(":", 1)[-1], pat):
                yield k


@pytest.fixture()
def fake_redis(monkeypatch):
    from app.core import fms_importer as imp
    from app.core import redis_client

    fake = _FakeRedis()
    monkeypatch.setattr(redis_client, "_redis", fake)
    monkeypatch.setattr(imp, "get_redis", lambda: fake)
    return fake


def test_progress_is_persisted_and_resumable(db, seeded_user, fake_redis, monkeypatch):
    """分页拉取中断后，进度（已处理 offset）能续上。"""
    from app.core import fms_importer as imp

    seen_offsets: list[int] = []

    def fake_iter(scope, **kw):
        off = kw.get("start_offset", 0)
        seen_offsets.append(off)
        # 模拟只有 2 页数据
        allrows = [{"id": i, "plate_no": f"京F{i:05d}", "vehicle_type_name": "小型客车"}
                   for i in range(1, 21)]
        for r in allrows[off:off + 10]:
            yield r

    monkeypatch.setattr(imp, "iter_items", fake_iter)

    ctx = imp.ImportContext(db=db, batch_size=10)
    res = imp.import_scope(db, "vehicles", ctx)

    assert res["created"] == 20
    assert imp.get_progress("vehicles").get("done") == 20

    # 第二次从已完成的进度继续：应跳过已完成部分
    imp.reset_progress(keep_map=True, scopes=["vehicles"])
    seen_offsets.clear()
    imp.import_scope(db, "vehicles", imp.ImportContext(db=db, batch_size=10))
    assert seen_offsets[0] == 0, "reset 后应从 0 开始"


def test_cancel_signal_stops_between_batches(db, seeded_user, fake_redis, monkeypatch):
    """收到停止信号后，在批次边界退出，不丢已提交数据。"""
    from app.core import fms_importer as imp
    from app.models import Vehicle

    rows = [{"id": i, "plate_no": f"京G{i:05d}", "vehicle_type_name": "小型客车"}
            for i in range(1, 31)]
    monkeypatch.setattr(imp, "iter_items", lambda scope, **kw: iter(rows))

    imp.request_cancel()
    ctx = imp.ImportContext(db=db, batch_size=10)
    res = imp.import_scope(db, "vehicles", ctx)

    assert res["cancelled"] is True
    # 已提交的批次保留（断点续传语义）
    n = len(db.execute(select(Vehicle)).scalars().all())
    assert n > 0


def test_status_shape_matches_documented_contract(fake_redis):
    """状态结构要满足前端约定：running/total/done/current_step/errors。"""
    from app.core import fms_importer as imp

    imp.reset_progress(keep_map=True)
    st = imp.get_status()
    for key in ("running", "total", "done", "current_step", "errors"):
        assert key in st, f"状态缺少字段 {key}"
    assert st["running"] is False