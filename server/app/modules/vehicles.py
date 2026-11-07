"""车辆管理：车辆 + 类型 + 性质 + 围栏点。"""
import logging
import re

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import DeptChecker, get_current_user, require_perm
from ..models import (DailyCheckRecord, Department, FencePoint, RepairOrder, User,
                      Vehicle, VehicleNature, VehicleType)

log = logging.getLogger("clglxt.vehicles")
router = APIRouter(prefix="/vehicles", tags=["vehicles"])

# 车牌正则（蓝牌/黄牌/新能源）
_PLATE_RE = re.compile(
    r'^[京津沪渝冀豫云辽黑湘皖鲁新苏浙赣鄂桂甘晋蒙陕吉闽贵粤青藏川宁琼使领]'
    r'[A-HJ-NP-Z]'
    r'([0-9]{5}|[DF][0-9]{5}|[0-9]{5}[DF]|[A-HJ-NP-Z0-9]{5,6})$'
)


@router.get("")
def list_vehicles(
    keyword: str = "",
    department_id: int | None = None,
    vehicle_type_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """车辆列表（部门隔离）。

    防 IDOR：显式传 department_id 也要过可见性校验，非超管不能跨部门查。
    """
    q = db.query(Vehicle)
    if keyword.strip():
        like = f"%{keyword.strip().upper()}%"
        q = q.filter(Vehicle.plate_no.like(like))
    if vehicle_type_id is not None:
        q = q.filter(Vehicle.vehicle_type_id == vehicle_type_id)
    q = DeptChecker(current_user, db).apply_dept_filter(
        q, Vehicle.department_id, department_id)
    rows = q.order_by(Vehicle.id).all()
    return [_out_vehicle(v) for v in rows]


# ── 静态路径路由必须先于 /{vehicle_id} 注册，否则被 int 参数拦截 → 422 ──

@router.get("/types/all")
def list_all_types(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    rows = db.query(VehicleType).order_by(VehicleType.id).all()
    return [{"id": t.id, "name": t.name, "remark": t.remark} for t in rows]


@router.get("/natures/all")
def list_all_natures(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    rows = db.query(VehicleNature).order_by(VehicleNature.sort_order, VehicleNature.id).all()
    return [{"id": n.id, "name": n.name, "remark": n.remark, "sort_order": n.sort_order}
            for n in rows]


@router.post("", dependencies=[Depends(require_perm("vehicle:manage"))])
def create_vehicle(
    plate_no: str,
    vehicle_type_id: int,
    department_id: int,
    nature: str = "",
    brand: str = "",
    status: str = "在用",
    fence_points: list[dict] | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    plate = plate_no.strip().upper()
    if not _PLATE_RE.match(plate):
        raise HTTPException(400, "车牌号格式不正确")
    if db.query(Vehicle).filter(Vehicle.plate_no == plate).first():
        raise HTTPException(400, "该车牌号已存在")
    if not db.get(VehicleType, vehicle_type_id):
        raise HTTPException(404, "车辆类型不存在")
    if not db.get(Department, department_id):
        raise HTTPException(404, "部门不存在")
    # 防越权写入：不能在不可见部门下建档
    DeptChecker(current_user, db).check_dept(department_id, "部门")

    v = Vehicle(plate_no=plate, vehicle_type_id=vehicle_type_id,
                department_id=department_id, nature=nature, brand=brand, status=status)
    db.add(v)
    db.flush()

    # 围栏点
    for pt in (fence_points or []):
        db.add(FencePoint(vehicle_id=v.id, plate_no=plate,
                          input_text=pt.get("input_text", ""),
                          address=pt.get("address", ""),
                          lng=pt.get("lng"), lat=pt.get("lat"),
                          radius_m=pt.get("radius_m", 0),
                          resolved_by=pt.get("resolved_by", "manual")))

    db.commit()
    return {"id": v.id, "plate_no": v.plate_no}


@router.get("/{vehicle_id}")
def get_vehicle(vehicle_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """车辆详情（部门隔离，防 IDOR）。"""
    v = db.get(Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "车辆不存在")
    DeptChecker(current_user, db).check_dept(v.department_id, "车辆")
    data = _out_vehicle(v)
    pts = db.query(FencePoint).filter(FencePoint.vehicle_id == vehicle_id,
                                      FencePoint.enabled == True).all()
    data["fence_points"] = [{"id": p.id, "input_text": p.input_text, "address": p.address,
                             "lng": p.lng, "lat": p.lat, "radius_m": p.radius_m}
                            for p in pts]
    return data


@router.put("/{vehicle_id}", dependencies=[Depends(require_perm("vehicle:manage"))])
def update_vehicle(vehicle_id: int, payload: dict, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """编辑车辆。fence_points 语义：不传=不动 / 空数组=清空 / 非空=替换。"""
    v = db.get(Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "车辆不存在")
    dc = DeptChecker(current_user, db)
    dc.check_dept(v.department_id, "车辆")

    if "plate_no" in payload:
        plate = str(payload["plate_no"]).strip().upper()
        if not _PLATE_RE.match(plate):
            raise HTTPException(400, "车牌号格式不正确")
        if plate != v.plate_no:
            if db.query(Vehicle).filter(Vehicle.plate_no == plate).first():
                raise HTTPException(400, "该车牌号已存在")
            v.plate_no = plate

    for field in ("nature", "brand", "status"):
        if field in payload:
            setattr(v, field, payload[field])
    if "vehicle_type_id" in payload:
        if not db.get(VehicleType, payload["vehicle_type_id"]):
            raise HTTPException(404, "车辆类型不存在")
        v.vehicle_type_id = payload["vehicle_type_id"]
    if "department_id" in payload:
        new_dept = payload["department_id"]
        if not db.get(Department, new_dept):
            raise HTTPException(404, "部门不存在")
        # 防止把车辆迁到/迁出不可见部门（越权改写归属）
        dc.check_dept(new_dept, "部门")
        v.department_id = new_dept

    if "fence_points" in payload:
        db.query(FencePoint).filter(FencePoint.vehicle_id == vehicle_id).delete()
        for pt in (payload["fence_points"] or []):
            db.add(FencePoint(vehicle_id=v.id, plate_no=v.plate_no,
                              input_text=pt.get("input_text", ""),
                              address=pt.get("address", ""),
                              lng=pt.get("lng"), lat=pt.get("lat"),
                              radius_m=pt.get("radius_m", 0),
                              resolved_by=pt.get("resolved_by", "manual")))

    db.commit()
    return {"ok": True}


@router.delete("/{vehicle_id}", dependencies=[Depends(require_perm("vehicle:manage"))])
def delete_vehicle(vehicle_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    from ..models import EPOCH
    v = db.get(Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "车辆不存在")
    DeptChecker(current_user, db).check_dept(v.department_id, "车辆")

    if db.query(RepairOrder).filter(RepairOrder.vehicle_id == vehicle_id).count() > 0:
        raise HTTPException(400, "该车辆存在维修记录，无法删除")
    if db.query(DailyCheckRecord).filter(DailyCheckRecord.vehicle_id == vehicle_id,
                                          DailyCheckRecord.deleted_at == EPOCH).count() > 0:
        raise HTTPException(400, "该车辆存在日检记录，无法删除")

    db.query(FencePoint).filter(FencePoint.vehicle_id == vehicle_id).delete()
    db.delete(v)
    db.commit()
    return {"ok": True}


# ───────────── 车辆类型 ─────────────

@router.post("/types", dependencies=[Depends(require_perm("vehicle:manage"))])
def create_vehicle_type(name: str, remark: str = "", db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "类型名称不能为空")
    if db.query(VehicleType).filter(VehicleType.name == name).first():
        raise HTTPException(400, "该车辆类型已存在")
    t = VehicleType(name=name, remark=remark)
    db.add(t)
    db.commit()
    return {"id": t.id, "name": t.name}


# ───────────── 车辆性质 ─────────────

@router.post("/natures", dependencies=[Depends(require_perm("vehicle:manage"))])
def create_nature(name: str, remark: str = "", sort_order: int = 0,
                  db: Session = Depends(get_db)):
    name = name.strip()
    if not name:
        raise HTTPException(400, "名称不能为空")
    if db.query(VehicleNature).filter(VehicleNature.name == name).first():
        raise HTTPException(400, "该车辆性质已存在")
    n = VehicleNature(name=name, remark=remark, sort_order=sort_order)
    db.add(n)
    db.commit()
    return {"id": n.id, "name": n.name}


def _out_vehicle(v: Vehicle) -> dict:
    return {
        "id": v.id, "plate_no": v.plate_no,
        "vehicle_type_id": v.vehicle_type_id,
        "vehicle_type_name": v.vehicle_type.name if v.vehicle_type else None,
        "department_id": v.department_id,
        "department_name": v.department.name if v.department else None,
        "nature": v.nature, "brand": v.brand, "status": v.status,
        "created_at": v.created_at.isoformat() if v.created_at else None,
    }
