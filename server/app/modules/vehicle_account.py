"""一车一账：车辆费用台账（维修/加油/其他）。"""
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import get_current_user, require_perm
from ..models import (Che1Fuel, Che1Usage, RepairOrder, RepairQuote, User, Vehicle)

log = logging.getLogger("clglxt.vehicle_account")
router = APIRouter(prefix="/vehicle-account", tags=["vehicle-account"])


def _check_vehicle_scope(db: Session, user: User, v: Vehicle) -> None:
    """车辆可见性：统一走 DeptChecker（超管 / data:scope_all / 汽修厂服务范围 / 本部门）。"""
    from ..core.deps import DeptChecker
    DeptChecker(user, db).check_dept(v.department_id, "车辆台账")


@router.get("", dependencies=[Depends(require_perm("vehicle_account:view"))])
def get_account(
    plate_no: str = "",
    start_date: str = "", end_date: str = "",
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """查车辆台账（维修 + 加油 + 用车）。

    权限：vehicle_account:view（含费用金额，此前任意登录用户可查）。
    """
    if not plate_no.strip():
        raise HTTPException(400, "请提供车牌号")
    plate = plate_no.strip().upper()
    v = db.query(Vehicle).filter(Vehicle.plate_no == plate).first()
    if not v:
        raise HTTPException(404, "车辆不存在")

    _check_vehicle_scope(db, current_user, v)

    # 维修费
    repair_q = (db.query(RepairOrder, func.max(RepairQuote.total).label("amt"))
                .outerjoin(RepairQuote, RepairQuote.order_id == RepairOrder.id)
                .filter(RepairOrder.vehicle_id == v.id))
    if start_date:
        repair_q = repair_q.filter(RepairOrder.created_at >= start_date)
    if end_date:
        repair_q = repair_q.filter(RepairOrder.created_at <= end_date)
    repairs = repair_q.group_by(RepairOrder.id).all()

    # 加油记录
    fuel_q = db.query(Che1Fuel).filter(Che1Fuel.fms_vehicle_id == v.id)
    if start_date:
        fuel_q = fuel_q.filter(Che1Fuel.fuel_at >= start_date)
    if end_date:
        fuel_q = fuel_q.filter(Che1Fuel.fuel_at <= end_date)
    fuels = fuel_q.all()

    # 用车记录
    usage_q = db.query(Che1Usage).filter(Che1Usage.fms_vehicle_id == v.id)
    if start_date:
        usage_q = usage_q.filter(Che1Usage.out_at >= start_date)
    if end_date:
        usage_q = usage_q.filter(Che1Usage.out_at <= end_date)
    usages = usage_q.all()

    return {
        "plate_no": plate,
        "repair_count": len(repairs),
        "repair_amount": float(round(sum(Decimal(str(r[1] or 0)) for r in repairs), 2)),
        "fuel_count": len(fuels),
        "fuel_amount": float(round(sum(Decimal(str(f.amount or 0)) for f in fuels), 2)),
        "fuel_volume": sum(float(f.volume or 0) for f in fuels),
        "usage_count": len(usages),
    }


@router.get("/summary", dependencies=[Depends(require_perm("vehicle_account:view"))])
def get_monthly_summary(
    plate_no: str = "",
    year: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """按月汇总（维修/加油）。"""
    from datetime import date
    if not year:
        year = date.today().year

    plate = plate_no.strip().upper()
    if not plate:
        raise HTTPException(400, "请提供车牌号")
    v = db.query(Vehicle).filter(Vehicle.plate_no == plate).first()
    if not v:
        raise HTTPException(404, "车辆不存在")

    _check_vehicle_scope(db, current_user, v)

    return {
        "plate_no": plate,
        "year": year,
        "note": "月度汇总待实现（需要按月份 group by）",
    }
