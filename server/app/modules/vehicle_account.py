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


def _as_dt(value: str, field: str):
    """'YYYY-MM-DD' → 该日 00:00 的 datetime；空串 → None；非法 → 400。

    summary 接口只传年份（无日期参数），get_account 用它统一收口。
    """
    from datetime import datetime
    from ..core.parsing import parse_date
    d = parse_date(value, field)
    return datetime(d.year, d.month, d.day) if d else None


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

    # 日期参数统一解析：字符串直接与 timestamp 列比较在 PG 下会报类型错误，
    # 非法格式也可能 500。转成 datetime 后再过滤。
    t_from = _as_dt(start_date, "start_date")
    t_to = _as_dt(end_date, "end_date")

    # 维修费
    repair_q = (db.query(RepairOrder, func.max(RepairQuote.total).label("amt"))
                .outerjoin(RepairQuote, RepairQuote.order_id == RepairOrder.id)
                .filter(RepairOrder.vehicle_id == v.id))
    if t_from:
        repair_q = repair_q.filter(RepairOrder.created_at >= t_from)
    if t_to:
        repair_q = repair_q.filter(RepairOrder.created_at <= t_to)
    repairs = repair_q.group_by(RepairOrder.id).all()

    # 加油记录
    fuel_q = db.query(Che1Fuel).filter(Che1Fuel.fms_vehicle_id == v.id)
    if t_from:
        fuel_q = fuel_q.filter(Che1Fuel.fuel_at >= t_from)
    if t_to:
        fuel_q = fuel_q.filter(Che1Fuel.fuel_at <= t_to)
    fuels = fuel_q.all()

    # 用车记录
    usage_q = db.query(Che1Usage).filter(Che1Usage.fms_vehicle_id == v.id)
    if t_from:
        usage_q = usage_q.filter(Che1Usage.out_at >= t_from)
    if t_to:
        usage_q = usage_q.filter(Che1Usage.out_at <= t_to)
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
