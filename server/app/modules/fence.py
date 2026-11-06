"""夜间停车围栏：围栏点、告警、判定台账、坐标判定。"""
import logging
import math
from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import (DeptChecker, get_current_user, require_internal_dep,
                         require_perm)
from ..models import FenceAlert, FenceNightRun, FencePoint, User, Vehicle

log = logging.getLogger("clglxt.fence")
router = APIRouter(prefix="/fence", tags=["fence"])


class FencePositionIn(BaseModel):
    """单个车辆的实时坐标。"""
    plate_no: str
    lng: float | None = None
    lat: float | None = None


class FenceEvaluateIn(BaseModel):
    """围栏判定请求体。"""
    positions: list[FencePositionIn] = Field(default_factory=list)
    night_date: str | None = None


def _haversine_m(lng1: float, lat1: float, lng2: float, lat2: float) -> float:
    """两点球面距离（米）。"""
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = math.radians(lat2 - lat1)
    dl = math.radians(lng2 - lng1)
    a = (math.sin(dp / 2) ** 2
         + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2)
    return 2 * r * math.asin(math.sqrt(a))


def _visible_plate_ids(db: Session, user: User) -> list[str] | None:
    """当前用户可见的车牌集合；None 表示不限制（超管 / data:scope_all）。

    汽修厂按服务范围（dept_ids）→ 车辆 → 车牌；内部账号按本部门 → 车辆 → 车牌。
    """
    dc = DeptChecker(user, db)
    dept_ids = dc.visible_dept_ids()
    if dept_ids is None:
        return None
    rows = (db.query(Vehicle.plate_no)
            .filter(Vehicle.department_id.in_(dept_ids)).all())
    return [r[0] for r in rows] or ["__none__"]


@router.get("/points", dependencies=[Depends(require_perm("fence:view"))])
def list_fence_points(vehicle_id: int | None = None,
                      db: Session = Depends(get_db),
                      current_user: User = Depends(get_current_user)):
    """列围栏点（部门隔离）。"""
    q = db.query(FencePoint).filter(FencePoint.enabled == True)
    if vehicle_id:
        q = q.filter(FencePoint.vehicle_id == vehicle_id)
    plates = _visible_plate_ids(db, current_user)
    if plates is not None:
        q = q.filter(FencePoint.plate_no.in_(plates))
    rows = q.order_by(FencePoint.vehicle_id).all()
    return [{"id": p.id, "plate_no": p.plate_no,
             "input_text": p.input_text, "address": p.address,
             "lng": p.lng, "lat": p.lat, "radius_m": p.radius_m}
            for p in rows]


@router.post("/points", dependencies=[Depends(get_current_user)])
def add_fence_point(
    plate_no: str, input_text: str, address: str,
    lng: float | None = None, lat: float | None = None,
    radius_m: int = 0, db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """新增围栏点。"""
    plate = plate_no.strip().upper()
    v = db.query(Vehicle).filter(Vehicle.plate_no == plate).first()
    if not v:
        raise HTTPException(404, "车辆不存在")

    # 统一部门可见性校验（超管 / data:scope_all / 汽修厂服务范围 / 本部门）
    DeptChecker(current_user, db).check_dept(v.department_id, "车辆")

    p = FencePoint(vehicle_id=v.id, plate_no=plate,
                   input_text=input_text, address=address,
                   lng=lng, lat=lat, radius_m=radius_m,
                   resolved_by="manual")
    db.add(p)
    db.commit()
    return {"id": p.id}


@router.get("/alerts", dependencies=[Depends(require_perm("fence:view"))])
def list_alerts(night_date: str | None = None,
                kind: str | None = None,
                db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """围栏告警（部门隔离）。

    之前无任何隔离，任意登录用户可看全公司车辆夜间位置告警。
    """
    q = db.query(FenceAlert)
    if night_date:
        q = q.filter(FenceAlert.night_date == night_date)
    if kind:
        q = q.filter(FenceAlert.kind == kind)
    plates = _visible_plate_ids(db, current_user)
    if plates is not None:
        q = q.filter(FenceAlert.plate_no.in_(plates))
    rows = q.order_by(FenceAlert.night_date.desc(), FenceAlert.id).all()
    return [{"id": a.id, "plate_no": a.plate_no,
             "night_date": a.night_date, "kind": a.kind,
             "distance_m": a.distance_m, "source": a.source}
            for a in rows]


@router.get("/night-runs", dependencies=[Depends(require_perm("fence:view"))])
def list_night_runs(night_date: str | None = None,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(get_current_user)):
    """判定台账（部门隔离：仅返回当前用户可见车牌的告警汇总）。

    台账按日期聚合，无法精确按部门切分，故用「该日可见车牌的告警数」重算，
    避免暴露全公司数据。
    """
    q = db.query(FenceNightRun)
    if night_date:
        q = q.filter(FenceNightRun.night_date == night_date)
    plates = _visible_plate_ids(db, current_user)
    rows = q.order_by(FenceNightRun.night_date.desc()).all()
    out = []
    for r in rows:
        item = {"night_date": r.night_date, "checked": r.checked,
                "alerts": r.alerts, "result": r.result}
        if plates is not None:
            # 该日可见车牌的告警数（替代全局 alerts）
            cnt = (db.query(func.count(FenceAlert.id))
                   .filter(FenceAlert.night_date == r.night_date,
                           FenceAlert.plate_no.in_(plates)).scalar())
            item["alerts"] = int(cnt or 0)
            item["result"] = "alert" if cnt else "ok"
            item["visible_only"] = True
        out.append(item)
    return out


@router.post("/evaluate",
             dependencies=[Depends(require_perm("fence:manage")),
                           Depends(require_internal_dep())])
def evaluate_fence(
    payload: FenceEvaluateIn,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """围栏判定：输入车辆坐标，越界则落库告警并通知。

    positions 示例：
        [{"plate_no": "京A12345", "lng": 120.38, "lat": 36.06}]

    调用方：GPS 接入网关 / 夜间定时任务。
    同一日期重复调用会先清空当日旧告警，保证幂等。

    权限：fence:manage（且仅内部账号）。之前任意登录用户可触发，
    会清空全公司当日告警并发通知（通知轰炸 + 数据破坏）。
    """
    from ..core.notify import notify

    positions = [p.model_dump() for p in payload.positions]
    day = payload.night_date or date.today().isoformat()
    fences = {p.plate_no: p for p in
              db.query(FencePoint).filter(FencePoint.enabled == True).all()}

    # 清空该日期旧告警（幂等）
    for old in db.query(FenceAlert).filter(FenceAlert.night_date == day).all():
        db.delete(old)
    db.flush()

    created: list[FenceAlert] = []
    skipped = 0
    for pos in positions:
        plate = str(pos.get("plate_no", "")).strip().upper()
        lng, lat = pos.get("lng"), pos.get("lat")
        fp = fences.get(plate)
        if not fp or lng is None or lat is None:
            skipped += 1
            continue
        if fp.lng is None or fp.lat is None or not fp.radius_m:
            skipped += 1
            continue
        try:
            dist = _haversine_m(float(lng), float(lat), float(fp.lng), float(fp.lat))
        except (TypeError, ValueError):
            skipped += 1
            continue
        if dist <= fp.radius_m:
            continue
        a = FenceAlert(plate_no=plate, night_date=day,
                       kind="out_of_range", distance_m=int(round(dist)),
                       source="gps")
        db.add(a)
        created.append(a)
    db.flush()

    # 通知车辆所属部门（按部门去重）
    notified = 0
    seen: set[int] = set()
    for a in created:
        v = db.query(Vehicle).filter(Vehicle.plate_no == a.plate_no).first()
        if not v or not v.department_id or v.department_id in seen:
            continue
        seen.add(v.department_id)
        for u in (db.query(User)
                  .filter(User.department_id == v.department_id,
                          User.is_active == 1).all()):
            notify(db, u.id, f"围栏告警 {a.plate_no}",
                   f"{day} 车辆越界约 {a.distance_m} 米",
                   kind="fence", target_type="fence_alert", target_id=a.id)
            notified += 1

    # 判定台账
    run = (db.query(FenceNightRun)
           .filter(FenceNightRun.night_date == day).first())
    if run is None:
        run = FenceNightRun(night_date=day)
        db.add(run)
    run.checked = len(positions)
    run.alerts = len(created)
    run.result = "alert" if created else "ok"
    db.commit()

    return {"night_date": day, "checked": len(positions),
            "alerts": len(created), "notified": notified, "skipped": skipped,
            "result": run.result}
