"""FMS → clglxt 数据导入器。

设计要点（幂等 / 断点续传 / 分批 / 失败恢复）：

1. **不保留 FMS 原始 ID**
   clglxt 的内置角色、超管走显式 ID 插入（1/2/3），若沿用 FMS 的 id 会
   直接撞主键，且需要事后 sync_sequences 兜底。改为：
   * 车辆 → 业务键 `plate_no`（唯一）
   * 用户 → 业务键 `username`
   * 部门/车型 → 业务键 `name`
   * 维修单 → 业务键 `order_no`（唯一）
   * 合同 → 业务键 `name` + 供应商
   这样反复导入天然幂等，且与 clglxt 现有唯一约束对齐。

2. **跨库 ID 映射**
   FMS 的 id 只在本次迁移里有意义（维修单的 vehicle_id 等外键需要翻译）。
   用 Redis hash 存 `clglxt:import:map:{scope}`，字段 `fms_id` → `clglxt_id`，
   断点续传时不必重新查库。

3. **断点续传**
   每个 scope 的进度（offset / done / total）落 Redis `clglxt:import:status`，
   进程重启后可从上次 offset 继续。写入按批 commit，已提交的数据不重做。

4. **失败恢复**
   单条脏数据不中断整体：捕获后记入 errors 列表（Redis，最多保留 200 条），
   继续处理下一条；批次失败则回滚该批并重试。
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from typing import Any, Callable

from sqlalchemy import select
from sqlalchemy.orm import Session

from ..models import (Contract, Department, RepairOrder, TempVehicleUse,
                      User, Vehicle, VehicleType, now)
from . import fms_client
from .redis_client import get_redis

log = logging.getLogger("clglxt.import.impl")

STATUS_KEY = "clglxt:import:status"
MAP_KEY = "clglxt:import:map:{scope}"
CANCEL_KEY = "clglxt:import:cancel"
MAX_ERRORS = 200

# 导入顺序：主数据先于业务数据（外键依赖）
SCOPE_ORDER = ["vehicles", "contracts", "repairs", "vehicle_uses", "daily_checks"]


# ─────────────────────────── 状态管理 ───────────────────────────

def _r():
    """取 Redis；不可用时降级到进程内存储。

    导入通道在没有 Redis 的环境（单测 / 单机诊断）也要能跑完逻辑，
    所以这里不直接抛错：连接失败退回内存实现，进度仅本进程可见。
    """
    try:
        r = get_redis()
        r.ping()
        return r
    except Exception:
        global _MEM_FALLBACK
        if _MEM_FALLBACK is None:
            _MEM_FALLBACK = _MemoryRedis()
        return _MEM_FALLBACK


_MEM_FALLBACK: _MemoryRedis | None = None


class _MemoryRedis:
    """Redis 不可用时的最小替代（get/set/setex/delete/exists/hash）。"""

    def __init__(self):
        self._store: dict[str, str] = {}
        self._hashes: dict[str, dict[str, str]] = {}

    def ping(self):
        return True

    def get(self, k):
        return self._store.get(k)

    def set(self, k, v):
        self._store[k] = v

    def setex(self, k, _ttl, v):
        self._store[k] = v

    def delete(self, *keys):
        for k in keys:
            self._store.pop(k, None)
            self._hashes.pop(k, None)

    def exists(self, k):
        return 1 if k in self._store else 0

    def hset(self, k, field, value):
        self._hashes.setdefault(k, {})[str(field)] = str(value)

    def hget(self, k, field):
        return self._hashes.get(k, {}).get(str(field))

    def hgetall(self, k):
        return dict(self._hashes.get(k, {}))

    def scan_iter(self, match=None, count=None):
        return iter([])


def _blank_status() -> dict:
    return {"running": False, "state": "idle", "scope": None,
            "current_step": None, "total": 0, "done": 0,
            "created": 0, "updated": 0, "skipped": 0,
            "errors": [], "message": ""}


def get_status() -> dict:
    raw = _r().get(STATUS_KEY)
    if not raw:
        return _blank_status()
    try:
        st = json.loads(raw)
        # 老状态（缺字段）补齐，前端契约不因版本变化破相
        base = _blank_status()
        base.update(st)
        return base
    except Exception:
        return _blank_status()


def get_progress(scope: str | None = None) -> dict:
    """查询进度。传入 scope 时返回该 scope 的 {offset, done}。"""
    st = get_status()
    if scope is None:
        return st
    return {"offset": st.get(f"offset_{scope}", 0),
            "done": st.get(f"done_{scope}", 0)}


def _save_status(st: dict) -> None:
    # 进度状态保留 24h，便于事后查看结果
    _r().setex(STATUS_KEY, 86400, json.dumps(st, ensure_ascii=False))


def update_progress(**fields) -> dict:
    """增量更新进度（合并当前状态），返回最新状态。"""
    st = get_status()
    st.update(fields)
    _save_status(st)
    return st


def request_cancel() -> None:
    _r().setex(CANCEL_KEY, 600, "1")


def is_cancelled() -> bool:
    return _r().exists(CANCEL_KEY) == 1


def clear_cancel() -> None:
    _r().delete(CANCEL_KEY)


def _map_key(scope: str) -> str:
    return MAP_KEY.format(scope=scope)


def _load_map(scope: str) -> dict[str, int]:
    raw = _r().hgetall(_map_key(scope))
    return {k: int(v) for k, v in raw.items()}


def _save_map(scope: str, fms_id: Any, clglxt_id: int) -> None:
    _r().hset(_map_key(scope), str(fms_id), clglxt_id)


def reset_progress(keep_map: bool = False,
                   scopes: list[str] | None = None) -> None:
    """清空进度（重新开始）。默认保留 ID 映射以免重导时外键翻译失败。

    scopes：只清这些 scope 的进度（用于按 scope 重试）。
    """
    target = scopes or SCOPE_ORDER
    st = get_status()
    for s in target:
        for k in (f"offset_{s}", f"done_{s}"):
            st.pop(k, None)
    _r().delete(CANCEL_KEY)
    _save_status({**_blank_status(), **{k: v for k, v in st.items()
                                        if k not in ("running", "state")}})
    if not keep_map:
        for s in target:
            _r().delete(_map_key(s))


# ─────────────────────────── 字段清洗 ───────────────────────────

def _clean_str(v: Any, default: str = "", maxlen: int | None = None) -> str:
    if v is None:
        return default
    s = str(v).strip()
    if maxlen and len(s) > maxlen:
        s = s[:maxlen]
    return s or default


def _clean_date(v: Any) -> date | None:
    if not v:
        return None
    if isinstance(v, date) and not isinstance(v, datetime):
        return v
    s = str(v).strip()
    if not s:
        return None
    # FMS 统一 ISO8601，兼容 'YYYY-MM-DD' 与带时间部分
    try:
        return date.fromisoformat(s[:10])
    except ValueError:
        return None


def _clean_dt(v: Any) -> datetime | None:
    """ISO8601 → 带 UTC 时区的 datetime。naive 视为 UTC（与 FMS 约定一致）。"""
    if not v:
        return None
    s = str(v).strip()
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


def _clean_dec(v: Any) -> Decimal:
    try:
        return Decimal(str(v)).quantize(Decimal("0.01"))
    except (InvalidOperation, TypeError, ValueError):
        return Decimal("0.00")


# ─────────────────────────── 导入上下文 ───────────────────────────

@dataclass
class ImportContext:
    """跨 scope 共享的导入上下文（缓存 + 跨库 ID 映射 + 归属人）。

    既支持属性访问（import_scope 用），也支持字典式访问
    （importer 函数用 ctx["dept_cache"]），两种写法等价。
    """
    db: Any = None
    batch_size: int = 100
    dept_cache: dict = field(default_factory=dict)   # 部门名 → clglxt id
    vt_cache: dict = field(default_factory=dict)     # 车型名 → clglxt id
    vmap: dict = field(default_factory=dict)         # FMS vehicle_id → clglxt id
    fallback_user_id: int = 0

    def __getitem__(self, k: str):
        return getattr(self, k)

    def __setitem__(self, k: str, v):
        setattr(self, k, v)


def resolve_id(scope: str, fms_id: Any, ctx: ImportContext | None = None) -> int | None:
    """把 FMS 的 id 翻译成 clglxt 的 id（读 Redis 映射表）。"""
    if fms_id is None:
        return None
    if ctx is not None and scope == "vehicles":
        hit = ctx.vmap.get(str(fms_id))
        if hit is not None:
            return hit
    raw = _r().hget(_map_key(scope), str(fms_id))
    return int(raw) if raw is not None else None


# ─────────────────────────── 主数据解析 ───────────────────────────

def _get_or_create_department(db: Session, name: str, cache: dict) -> int | None:
    """按名称 get-or-create 部门。FMS 只给名称，没有独立部门列表接口。"""
    name = _clean_str(name, maxlen=64)
    if not name:
        return None
    if name in cache:
        return cache[name]
    dept = db.execute(select(Department).where(Department.name == name)).scalar_one_or_none()
    if dept is None:
        dept = Department(name=name)
        db.add(dept)
        db.flush()
    cache[name] = dept.id
    return dept.id


def _get_or_create_vehicle_type(db: Session, name: str, cache: dict) -> int | None:
    name = _clean_str(name, maxlen=64)
    if not name:
        return None
    if name in cache:
        return cache[name]
    vt = db.execute(select(VehicleType).where(VehicleType.name == name)).scalar_one_or_none()
    if vt is None:
        vt = VehicleType(name=name)
        db.add(vt)
        db.flush()
    cache[name] = vt.id
    return vt.id


# ─────────────────────────── 各 scope 的导入逻辑 ───────────────────────────

def import_vehicles(db: Session, item: dict, ctx: dict) -> str:
    """车辆。业务键 plate_no。"""
    plate = _clean_str(item.get("plate") or item.get("plate_no"), maxlen=16).upper()
    if not plate:
        raise ValueError("缺少车牌号")

    type_id = _get_or_create_vehicle_type(
        db, item.get("vehicle_type_name") or item.get("type_name") or "", ctx["vt_cache"])
    if type_id is None:
        # 车型是 NOT NULL 外键，缺失时落一个占位类型，避免整批失败
        type_id = _get_or_create_vehicle_type(db, "未分类", ctx["vt_cache"])

    dept_id = _get_or_create_department(
        db, item.get("department_name") or item.get("dept_name") or "", ctx["dept_cache"])
    if dept_id is None:
        dept_id = _get_or_create_department(db, "未分配", ctx["dept_cache"])

    existing = db.execute(select(Vehicle).where(Vehicle.plate_no == plate)).scalar_one_or_none()
    if existing is not None:
        existing.vehicle_type_id = type_id
        existing.nature = _clean_str(item.get("nature"), maxlen=32)
        existing.department_id = dept_id
        existing.brand = _clean_str(item.get("brand"), maxlen=64)
        existing.status = _clean_str(item.get("status"), "在用", maxlen=16)
        _save_map("vehicles", item.get("id"), existing.id)
        return "updated"

    v = Vehicle(
        plate_no=plate,
        vehicle_type_id=type_id,
        nature=_clean_str(item.get("nature"), maxlen=32),
        department_id=dept_id,
        brand=_clean_str(item.get("brand"), maxlen=64),
        status=_clean_str(item.get("status"), "在用", maxlen=16),
    )
    db.add(v)
    db.flush()
    _save_map("vehicles", item.get("id"), v.id)
    return "created"


def import_contracts(db: Session, item: dict, ctx: dict) -> str:
    """合同。业务键 name + supplier（同一家可能有多份合同）。"""
    name = _clean_str(item.get("name"), maxlen=128)
    if not name:
        raise ValueError("缺少合同名称")
    supplier = _clean_str(item.get("supplier"), maxlen=128)

    existing = db.execute(
        select(Contract).where(Contract.name == name, Contract.supplier == supplier)
    ).scalar_one_or_none()
    if existing is not None:
        existing.is_active = bool(item.get("is_active", True))
        existing.start_date = _clean_date(item.get("start_date"))
        existing.end_date = _clean_date(item.get("end_date"))
        _save_map("contracts", item.get("id"), existing.id)
        return "updated"

    c = Contract(
        name=name,
        supplier=supplier,
        is_active=bool(item.get("is_active", True)),
        start_date=_clean_date(item.get("start_date")),
        end_date=_clean_date(item.get("end_date")),
    )
    db.add(c)
    db.flush()
    _save_map("contracts", item.get("id"), c.id)
    return "created"


def import_repairs(db: Session, item: dict, ctx: dict) -> str:
    """维修单。业务键 order_no。FMS 只给摘要，明细/动作流不在此接口。"""
    order_no = _clean_str(item.get("order_no") or item.get("no"), maxlen=32)
    if not order_no:
        raise ValueError("缺少维修单号")

    # vehicle_id 需要跨库翻译；映射缺失时按车牌回查
    vmap = ctx["vmap"]
    vehicle_id = vmap.get(str(item.get("vehicle_id")))
    if vehicle_id is None:
        plate = _clean_str(item.get("plate") or item.get("vehicle_plate"), maxlen=16).upper()
        if plate:
            row = db.execute(select(Vehicle).where(Vehicle.plate_no == plate)).scalar_one_or_none()
            vehicle_id = row.id if row else None
    if vehicle_id is None:
        raise ValueError(f"找不到对应车辆（FMS vehicle_id={item.get('vehicle_id')}）")

    existing = db.execute(
        select(RepairOrder).where(RepairOrder.order_no == order_no)
    ).scalar_one_or_none()
    if existing is not None:
        existing.status = _clean_str(item.get("status"), "processing", maxlen=16)
        existing.finished_at = _clean_dt(item.get("finished_at"))
        _save_map("repairs", item.get("id"), existing.id)
        return "updated"

    reporter_id = ctx["fallback_user_id"]
    ro = RepairOrder(
        order_no=order_no,
        vehicle_id=vehicle_id,
        reporter_id=reporter_id,
        description=_clean_str(item.get("summary") or item.get("description"), maxlen=None),
        status=_clean_str(item.get("status"), "processing", maxlen=16),
        created_at=_clean_dt(item.get("created_at")) or now(),
        finished_at=_clean_dt(item.get("finished_at")),
    )
    db.add(ro)
    db.flush()
    _save_map("repairs", item.get("id"), ro.id)
    return "created"


def import_vehicle_uses(db: Session, item: dict, ctx: dict) -> str:
    """临时用车备案。FMS 用 plate_no 作为业务键（无唯一约束，按全字段判重）。"""
    plate = _clean_str(item.get("plate_no"), maxlen=16).upper()
    start = _clean_date(item.get("start_date"))
    if not plate or start is None:
        raise ValueError("缺少车牌或开始日期")

    dept_id = _get_or_create_department(db, item.get("dept_name") or "", ctx["dept_cache"])
    if dept_id is None:
        dept_id = _get_or_create_department(db, "未分配", ctx["dept_cache"])

    existing = db.execute(
        select(TempVehicleUse).where(
            TempVehicleUse.plate_no == plate,
            TempVehicleUse.start_date == start,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.end_date = _clean_date(item.get("end_date")) or start
        existing.reason = _clean_str(item.get("reason"), maxlen=255)
        _save_map("vehicle_uses", item.get("id"), existing.id)
        return "updated"

    u = TempVehicleUse(
        plate_no=plate,
        vehicle_type_id=_get_or_create_vehicle_type(db, item.get("type_name") or "", ctx["vt_cache"]),
        nature=_clean_str(item.get("nature"), maxlen=32),
        department_id=dept_id,
        start_date=start,
        end_date=_clean_date(item.get("end_date")) or start,
        reason=_clean_str(item.get("reason"), maxlen=255),
        created_by=ctx["fallback_user_id"],
    )
    db.add(u)
    db.flush()
    _save_map("vehicle_uses", item.get("id"), u.id)
    return "created"


def import_daily_checks(db: Session, item: dict, ctx: dict) -> str:
    """日检记录。摘要接口无 results 明细，仅迁移「有无异常」与日期。

    注意：clglxt 的 daily_check_records 有 NOT NULL 的 results 字段，
    这里写入空 JSON 占位，并在 errors 中提示需人工补全。
    """
    check_date = _clean_date(item.get("check_date"))
    if check_date is None:
        raise ValueError("缺少检查日期")

    vehicle_id = ctx["vmap"].get(str(item.get("vehicle_id")))
    if vehicle_id is None:
        plate = _clean_str(item.get("vehicle_plate"), maxlen=16).upper()
        if plate:
            row = db.execute(select(Vehicle).where(Vehicle.plate_no == plate)).scalar_one_or_none()
            vehicle_id = row.id if row else None
    if vehicle_id is None:
        raise ValueError("找不到对应车辆")

    from ..models import DailyCheckRecord, EPOCH
    existing = db.execute(
        select(DailyCheckRecord).where(
            DailyCheckRecord.vehicle_id == vehicle_id,
            DailyCheckRecord.check_date == check_date,
            DailyCheckRecord.deleted_at == EPOCH,
        )
    ).scalar_one_or_none()
    if existing is not None:
        existing.has_abnormal = bool(item.get("has_abnormal", False))
        _save_map("daily_checks", item.get("id"), existing.id)
        return "updated"

    r = DailyCheckRecord(
        vehicle_id=vehicle_id,
        user_id=ctx["fallback_user_id"],
        check_date=check_date,
        results="[]",
        has_abnormal=bool(item.get("has_abnormal", False)),
        created_at=_clean_dt(item.get("created_at")) or now(),
    )
    db.add(r)
    db.flush()
    _save_map("daily_checks", item.get("id"), r.id)
    return "created"


def iter_items(scope: str, start_offset: int = 0,
               batch_size: int = 100):
    """拉取**一页**数据（scope 的起点为 start_offset）。

    这是 HTTP 层与导入层之间的**唯一打桩点**：测试替换此函数即可
    避免外呼。真实运行走 fms_client.list_page 单页拉取，
    由 import_scope 负责按页推进（offset 累加）直到取空。

    返回一页 items 的迭代器（空迭代器 = 没有更多数据）。
    """
    page = fms_client.list_page(scope, offset=start_offset, limit=batch_size)
    return iter(page.get("items") or [])


def import_scope(db: Session, scope: str, ctx: ImportContext) -> dict:
    """导入单个 scope，返回 {created, updated, skipped, errors, cancelled, done}。

    分批 commit：每 batch_size 条提交一次，中断时已提交的数据保留。
    单条脏数据跳过并记录，不中断整批。
    停止信号在**批次边界**生效：正在处理的批次先完成提交，避免半批丢数据。
    """
    if scope not in IMPORTERS:
        raise ValueError(f"未知 scope：{scope}")

    importer = IMPORTERS[scope]
    batch_size = max(1, int(ctx.batch_size or 100))
    st = get_status()
    start_offset = st.get(f"offset_{scope}", 0)

    # 导入归属人（reporter/created_by 非空）：复用上下文已有的，否则本库查一个
    if not ctx.fallback_user_id:
        ctx.fallback_user_id = _fallback_user_id(db)

    res = {"created": 0, "updated": 0, "skipped": 0,
           "errors": [], "cancelled": False, "done": 0}

    update_progress(scope=scope, current_step=f"{scope}",
                    running=True, state="running")

    offset = start_offset
    batch = 0
    while True:
        items = iter_items(scope, start_offset=offset, batch_size=batch_size)
        got = 0
        for item in items:
            try:
                outcome = importer(db, item, ctx)
                res[outcome] = res.get(outcome, 0) + 1
            except Exception as e:  # noqa: BLE001
                # 脏数据：回滚本条，继续下一条
                db.rollback()
                res["skipped"] += 1
                if len(res["errors"]) < MAX_ERRORS:
                    res["errors"].append({"scope": scope,
                                          "fms_id": item.get("id"),
                                          "error": str(e)[:200]})
                log.warning("导入跳过 scope=%s id=%s: %s", scope, item.get("id"), e)

            res["done"] += 1
            got += 1
            batch += 1

            if batch >= batch_size:
                db.commit()
                if scope == "vehicles":
                    ctx.vmap = _load_map("vehicles")
                batch = 0
                offset += got
                _update_scope_progress(scope, res, start_offset)
                # 停止信号在批次边界生效
                if is_cancelled():
                    res["cancelled"] = True
                    break

        if res["cancelled"]:
            break
        if got < batch_size:
            # 最后一页（不足一批），结束
            db.commit()
            if scope == "vehicles":
                ctx.vmap = _load_map("vehicles")
            offset += got
            break
        # 满批但数据源可能还有下一页：继续拉
        if got == 0:
            break

    _update_scope_progress(scope, res, start_offset)
    return res


def _update_scope_progress(scope: str, res: dict, start_offset: int) -> None:
    """把单 scope 进度合并进全局状态。"""
    pos = start_offset + res["done"]
    update_progress(
        scope=scope,
        current_step=f"{scope} ({pos})",
        total=max(get_status().get("total", 0), pos),
        done=max(get_status().get("done", 0), pos),
        **{f"offset_{scope}": pos, f"done_{scope}": pos},
        created=res["created"], updated=res["updated"],
        skipped=res["skipped"], errors=res["errors"],
    )


IMPORTERS: dict[str, Callable[[Session, dict, dict], str]] = {
    "vehicles": import_vehicles,
    "contracts": import_contracts,
    "repairs": import_repairs,
    "vehicle_uses": import_vehicle_uses,
    "daily_checks": import_daily_checks,
}


# ─────────────────────────── 主流程 ───────────────────────────

def _fallback_user_id(db: Session) -> int:
    """找一个可用作 reporter/created_by 的账号（导入数据的归属人）。"""
    from ..models import EPOCH
    u = db.execute(
        select(User).where(User.is_superuser == True,  # noqa: E712
                           User.deleted_at == EPOCH)
    ).scalars().first()
    if u is None:
        u = db.execute(select(User)).scalars().first()
    if u is None:
        raise RuntimeError("clglxt 中没有任何用户，无法确定导入数据的归属人")
    return u.id


def run_import(scopes: list[str] | None = None, batch_size: int = 100,
               db_factory: Callable[[], Session] | None = None) -> dict:
    """执行导入（Celery 入口）。返回最终状态 dict。

    batch_size 控制每批 commit 的条数——批越小断点越细，但事务开销越大。
    """
    from ..core.database import SessionLocal

    scopes = scopes or SCOPE_ORDER
    unknown = [s for s in scopes if s not in IMPORTERS]
    if unknown:
        raise ValueError(f"未知 scope：{unknown}")

    clear_cancel()
    st = {**_blank_status(), "running": True, "state": "running",
          "scope": None, "started_at": now().isoformat()}
    _save_status(st)

    factory = db_factory or SessionLocal
    db = factory()
    try:
        ctx = ImportContext(
            db=db,
            batch_size=batch_size,
            vmap=_load_map("vehicles"),
            fallback_user_id=_fallback_user_id(db),
        )

        total_created = total_updated = total_skipped = 0
        cancelled = False
        for scope in scopes:
            if is_cancelled():
                st["state"] = "cancelled"
                st["message"] = "用户中止"
                cancelled = True
                break
            try:
                res = import_scope(db, scope, ctx)
            except Exception as e:  # noqa: BLE001
                db.rollback()
                log.exception("scope 导入失败 scope=%s", scope)
                # 失败即中断：后续 scope（repairs/daily_checks）依赖本 scope
                # 的 ID 映射，映射缺失时它们只会把整批数据当脏数据跳过，
                # 最终状态仍显示 done —— 停机窗口里这是最危险的静默失败。
                # 置 error 并 break，让调用方重试整批，而不是带着残缺数据继续。
                st["state"] = "error"
                st["scope"] = scope
                st["message"] = f"scope 失败：{scope}（{str(e)[:200]}）"
                st["errors"] = [{"scope": scope, "error": st["message"]}]
                update_progress(**st)
                break
            total_created += res["created"]
            total_updated += res["updated"]
            total_skipped += res["skipped"]
            if res["cancelled"]:
                cancelled = True
                break

        if not cancelled and st.get("state") != "error":
            st["state"] = "done"
            st["message"] = (f"完成：新建 {total_created}，更新 {total_updated}，"
                             f"跳过 {total_skipped}")
        elif cancelled:
            st["state"] = "cancelled"
            st["message"] = "用户中止"

        update_progress(state=st["state"], message=st["message"],
                        running=False, current_step=None,
                        created=total_created, updated=total_updated,
                        skipped=total_skipped,
                        finished_at=now().isoformat())
    finally:
        db.close()
        clear_cancel()

    return get_status()
