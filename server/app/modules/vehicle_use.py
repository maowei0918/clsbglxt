"""临时用车备案。"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import DeptChecker, get_current_user, require_perm
from ..core.parsing import parse_date as _parse_date
from ..models import Department, TempVehicleUse, User, Vehicle, now as db_now

log = logging.getLogger("clglxt.vehicle_use")
router = APIRouter(prefix="/vehicle-use", tags=["vehicle-use"])


@router.get("")
def list_uses(
    keyword: str = "",
    start_date_from: str = "", start_date_to: str = "",
    department_id: int | None = None,
    include_archived: bool = False,
    limit: int = Query(50, le=200), offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """列临时用车记录（部门隔离）。

    默认隐藏已归档记录；传 include_archived=true 可一并查看。
    防 IDOR：客户端显式传 department_id 不再被视为放行条件——
    非超管只能看本部门（有 data:scope_all 或汽修厂服务范围的按 DeptChecker 判定）。
    """
    q = db.query(TempVehicleUse)
    # 统一部门隔离：显式传 department_id 也要过可见性校验
    q = DeptChecker(current_user, db).apply_dept_filter(
        q, TempVehicleUse.department_id, department_id)
    if not include_archived:
        q = q.filter(TempVehicleUse.archived.is_(False))
    if keyword.strip():
        q = q.filter(TempVehicleUse.plate_no.like(f"%{keyword.strip().upper()}%"))
    # 字符串直接与 Date 列比较在 PostgreSQL 下会报类型错误，先转 date
    d_from = _parse_date(start_date_from, "start_date_from")
    d_to = _parse_date(start_date_to, "start_date_to")
    if d_from:
        q = q.filter(TempVehicleUse.start_date >= d_from)
    if d_to:
        q = q.filter(TempVehicleUse.start_date <= d_to)
    rows = q.order_by(TempVehicleUse.start_date.desc()).offset(offset).limit(limit).all()
    return [_out_use(u) for u in rows]


@router.post("")
def create_use(
    plate_no: str,
    start_date: str,
    end_date: str,
    reason: str = "",
    park_location: str = "",
    remark: str = "",
    department_id: int | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """备案临时用车。"""
    # 日期格式/区间校验（避免非法字符串直接进 Date 列导致 500）
    sd = _parse_date(start_date, "start_date")
    ed = _parse_date(end_date, "end_date")
    if sd is None or ed is None:
        raise HTTPException(400, "开始/结束日期不能为空")
    if ed < sd:
        raise HTTPException(400, "结束日期不能早于开始日期")

    plate = plate_no.strip().upper()
    v = db.query(Vehicle).filter(Vehicle.plate_no == plate).first()

    # 部门归属：忽略客户端显式传入的 department_id（防越权写入其他部门）。
    # 以车辆所属部门为准，无车辆时回退到当前用户部门；超管可显式指定。
    dc = DeptChecker(current_user, db)
    if current_user.is_superuser and department_id:
        target_dept = department_id
    else:
        target_dept = v.department_id if (v and v.department_id) else current_user.department_id
    if not target_dept:
        raise HTTPException(400, "无法确定部门归属")
    if not db.get(Department, target_dept):
        raise HTTPException(404, "部门不存在")
    dc.check_dept(target_dept, "部门")

    use = TempVehicleUse(
        plate_no=plate,
        vehicle_id=v.id if v else None,
        vehicle_type_id=v.vehicle_type_id if v else None,
        nature=v.nature if v else "",
        department_id=target_dept,
        start_date=sd,
        end_date=ed,
        reason=reason,
        park_location=park_location,
        remark=remark,
        created_by=current_user.id,
    )
    db.add(use)
    db.commit()
    return {"id": use.id}


@router.delete("/{use_id}")
def delete_use(use_id: int, db: Session = Depends(get_db),
               current_user: User = Depends(get_current_user)):
    use = db.get(TempVehicleUse, use_id)
    if not use:
        raise HTTPException(404, "记录不存在")
    # 统一走 DeptChecker：超管/全局账号/服务范围账号与普通用户同一套规则
    DeptChecker(current_user, db).check_dept(use.department_id, "该记录")

    db.delete(use)
    db.commit()
    return {"ok": True}


@router.post("/{use_id}/archive", dependencies=[Depends(require_perm("vehicle:manage"))])
def archive_use(use_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """归档一条临时用车记录（保留数据，列表默认不展示）。"""
    use = db.get(TempVehicleUse, use_id)
    if not use:
        raise HTTPException(404, "记录不存在")
    DeptChecker(current_user, db).check_dept(use.department_id, "该记录")
    if use.archived:
        return {"ok": True, "archived": True}  # 幂等
    use.archived = True
    use.archived_at = db_now()
    db.commit()
    log.info("临时用车记录 %s 已归档（操作人 %s）", use.id, current_user.id)
    return {"ok": True, "archived": True}


@router.post("/{use_id}/restore", dependencies=[Depends(require_perm("vehicle:manage"))])
def restore_use(use_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    """恢复一条已归档的临时用车记录。"""
    use = db.get(TempVehicleUse, use_id)
    if not use:
        raise HTTPException(404, "记录不存在")
    DeptChecker(current_user, db).check_dept(use.department_id, "该记录")
    if not use.archived:
        return {"ok": True, "archived": False}  # 幂等
    use.archived = False
    use.archived_at = None
    db.commit()
    log.info("临时用车记录 %s 已恢复（操作人 %s）", use.id, current_user.id)
    return {"ok": True, "archived": False}


def _out_use(u: TempVehicleUse) -> dict:
    return {
        "id": u.id, "plate_no": u.plate_no,
        "vehicle_id": u.vehicle_id,
        "vehicle_type_name": u.vehicle_type.name if u.vehicle_type else None,
        "nature": u.nature,
        "department_id": u.department_id,
        "department_name": u.department.name if u.department else None,
        "start_date": u.start_date.isoformat() if u.start_date else None,
        "end_date": u.end_date.isoformat() if u.end_date else None,
        "reason": u.reason, "park_location": u.park_location,
        "remark": u.remark,
        "created_by": u.created_by,
        "created_at": u.created_at.isoformat() if u.created_at else None,
        "archived": bool(u.archived),
        "archived_at": u.archived_at.isoformat() if u.archived_at else None,
    }
