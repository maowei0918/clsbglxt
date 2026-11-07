"""维修管理：维修单 CRUD + 流程推进 + 报价。

与 fms 的核心差异：
  * 单号用 PG 原生 SELECT nextval（替代 sqlite 内存计数器）
  * 并发控制用 SELECT FOR UPDATE 替代 CAS
"""
import logging
from pathlib import Path
from datetime import date, datetime

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import DeptChecker, get_current_user, require_perm
from ..core.parsing import parse_date
from ..models import (Attachment, RepairAction, RepairOrder,
                      Supplier, User, Vehicle)

log = logging.getLogger("clglxt.repairs")
router = APIRouter(prefix="/repairs", tags=["repairs"])

# 维修单状态
STATUS = {"processing", "completed", "rejected", "cancelled"}


def _day_start(s: str) -> datetime:
    """'2026-11-02' → datetime(2026-11-02 00:00)；非法格式 → 400。"""
    d = parse_date(s, "date_from")
    return datetime(d.year, d.month, d.day)


def _day_after(s: str) -> datetime:
    """'2026-11-02' → datetime(2026-11-03 00:00)（右开区间）。"""
    from datetime import timedelta
    d = parse_date(s, "date_to")
    return datetime(d.year, d.month, d.day) + timedelta(days=1)


def _can_view_order(db: Session, user: User, order: RepairOrder) -> bool:
    """多级可见性判定（与 list_orders 同口径）。

    * 超管 / data:scope_all：可见
    * 汽修厂：指派给本厂 或 本人提报
    * 内部账号：本人提报 或 车辆属本部门
    """
    if user.is_superuser:
        return True
    if user.supplier_id and order.target_supplier_id == user.supplier_id:
        return True
    if order.reporter_id == user.id:
        return True
    dc = DeptChecker(user, db)
    if dc.has_scope_all():
        return True
    v = db.get(Vehicle, order.vehicle_id)
    return bool(v and v.department_id and v.department_id == user.department_id)


@router.get("")
def list_orders(
    keyword: str = "", status: str = "",
    shop_id: int | None = None,
    department_id: int | None = None,
    vehicle_type_id: int | None = None,
    nature: str = "",
    date_from: str = "", date_to: str = "",
    scope: str = "all",
    limit: int = Query(20, le=100), offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """维修单列表（部门隔离 + 可见性判定）。"""
    q = db.query(RepairOrder)
    if keyword.strip():
        kw = keyword.strip().upper()
        q = q.filter(or_(RepairOrder.order_no.like(f"%{kw}%"),
                         RepairOrder.description.like(f"%{kw}%")))
    if status:
        q = q.filter(RepairOrder.status == status)
    if shop_id:
        q = q.filter(RepairOrder.target_supplier_id == shop_id)
    if department_id:
        q = q.join(Vehicle, RepairOrder.vehicle_id == Vehicle.id) \
           .filter(Vehicle.department_id == department_id)
    if date_from:
        q = q.filter(RepairOrder.created_at >= _day_start(date_from))
    if date_to:
        # 含当日：用「次日零点前」做右开区间，避免 <= "YYYY-MM-DD" 漏掉当天
        q = q.filter(RepairOrder.created_at < _day_after(date_to))

    # 可见性隔离：与 _can_view_order 完全同口径（列表是详情条件的 SQL 化）
    #   * 超管：全部
    #   * 汽修厂：指派给本厂 或 本人提报
    #   * 内部账号：data:scope_all 全部；否则 本部门车辆 或 本人提报
    # 之前列表漏了「本人提报（车辆属于其他部门）」这一支，导致列表看不到、
    # 详情却能打开的口径漂移。
    dc = DeptChecker(current_user, db)
    if current_user.is_superuser:
        pass
    elif current_user.supplier_id:
        q = q.filter(or_(RepairOrder.target_supplier_id == current_user.supplier_id,
                         RepairOrder.reporter_id == current_user.id))
    elif dc.has_scope_all():
        pass
    else:
        conds = [RepairOrder.reporter_id == current_user.id]
        if current_user.department_id:
            conds.append(RepairOrder.vehicle_id.in_(
                db.query(Vehicle.id).filter(
                    Vehicle.department_id == current_user.department_id)))
        q = q.filter(or_(*conds))

    total = q.count()
    rows = q.order_by(RepairOrder.id.desc()).offset(offset).limit(limit).all()
    return [{"id": o.id, "order_no": o.order_no,
             "vehicle_id": o.vehicle_id, "status": o.status,
             "current_seq": o.current_seq,
             "created_at": o.created_at.isoformat() if o.created_at else None}
            for o in rows], total


@router.post("", dependencies=[Depends(require_perm("repair:report"))])
def create_order(
    vehicle_id: int,
    description: str = "",
    supplier_id: int | None = None,
    template_id: int | None = None,
    repair_start: str | None = None,
    repair_end: str | None = None,
    notes: str = "",
    attachments: list[dict] | None = None,
    idem_key: str | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """创建维修单（带幂等键防重复提交）。

    权限：repair:report（内部提报）。汽修厂账号走报价/施工，不创建工单，
    避免「给自己指派维修单」绕过内部审批。
    """
    if current_user.supplier_id is not None:
        raise HTTPException(403, "汽修厂账号不能创建维修单")
    # 幂等键去重
    if idem_key:
        old = db.query(RepairOrder).filter(
            RepairOrder.reporter_id == current_user.id,
            RepairOrder.idem_key == idem_key).first()
        if old:
            return {"id": old.id, "order_no": old.order_no, "idempotent": True}

    v = db.get(Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "车辆不存在")

    # 车辆归属校验（统一走 DeptChecker：本部门 / data:scope_all / 超管）
    DeptChecker(current_user, db).check_dept(v.department_id, "车辆")

    # 汽修厂服务部门校验
    if supplier_id:
        shop = db.get(Supplier, supplier_id)
        if not shop:
            raise HTTPException(404, "汽修厂不存在")
        if not shop.is_active:
            raise HTTPException(400, "该汽修厂已停用")

    # 生成单号
    order_no = _gen_order_no(db, current_user)

    o = RepairOrder(
        order_no=order_no,
        vehicle_id=vehicle_id,
        reporter_id=current_user.id,
        description=description,
        current_seq=1,
        status="processing",
        target_supplier_id=supplier_id,
        template_id=template_id,
        idem_key=idem_key,
    )
    db.add(o)
    db.flush()

    db.add(RepairAction(order_id=o.id, seq=1,
                        user_id=current_user.id, action="create",
                        note=description[:500]))

    # 附件路径必须落在 UPLOAD_DIR 内：客户端传入的 path 不能作为
    # 未来下载端点的任意文件读入口。拒绝绝对路径与 ../ 穿越。
    for att in (attachments or []):
        raw_path = str(att.get("path", ""))
        _validate_att_path(raw_path)
        db.add(Attachment(order_id=o.id, step_seq=1,
                          saved_path=raw_path,
                          orig_name=att.get("orig_name", ""),
                          mime=att.get("mime", ""),
                          size=int(att.get("size", 0)),
                          kind=att.get("kind", "photo")))

    db.commit()
    _notify_new_order(db, o, v, current_user.id)
    return {"id": o.id, "order_no": o.order_no}


def _validate_att_path(raw_path: str) -> bool:
    """校验附件相对路径：拒绝绝对路径、.. 穿越与空字节。

    客户端传的 path 不能作为未来下载端点的任意文件读入口。
    """
    if not raw_path or "\x00" in raw_path or Path(raw_path).is_absolute():
        raise HTTPException(400, "附件路径非法")
    if ".." in Path(raw_path).parts:
        raise HTTPException(400, "附件路径非法")
    return True


def _gen_order_no(db: Session, user: User) -> str:
    """生成单号：WX + YYYYMMDD + 4位序号。

    用 seq_counters 表做原子取号：
      INSERT ... ON CONFLICT DO UPDATE ... RETURNING value
    并发下由数据库保证不重号（PG 支持，SQLite 用行锁退化实现）。
    """
    from sqlalchemy.dialects.postgresql import insert as pg_insert
    from sqlalchemy.dialects.sqlite import insert as lite_insert
    from ..models import SeqCounter

    today = date.today().strftime("%Y%m%d")
    key = f"repair_order:{today}"

    dialect = db.bind.dialect.name
    if dialect == "postgresql":
        stmt = pg_insert(SeqCounter).values(key=key, value=1)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SeqCounter.key],
            set_={"value": SeqCounter.__table__.c.value + 1},
        ).returning(SeqCounter.value)
        seq = db.execute(stmt).scalar_one()
    elif dialect == "sqlite":
        stmt = lite_insert(SeqCounter).values(key=key, value=1)
        stmt = stmt.on_conflict_do_update(
            index_elements=[SeqCounter.key],
            set_={"value": SeqCounter.__table__.c.value + 1},
        ).returning(SeqCounter.value)
        seq = db.execute(stmt).scalar_one()
    else:
        # 兜底：行锁 + 自增
        row = db.query(SeqCounter).filter(SeqCounter.key == key).with_for_update().first()
        if row:
            row.value += 1
            seq = row.value
        else:
            db.add(SeqCounter(key=key, value=1))
            seq = 1
        db.flush()

    return f"WX{today}{seq:04d}"


def _notify_new_order(db: Session, o: RepairOrder, v: Vehicle, reporter_id: int) -> None:
    """维修单创建后通知相关人：有供应商通知汽修厂，否则通知车辆部门，兜底超管。"""
    from ..core.notify import notify
    from ..models import User

    if o.target_supplier_id:
        targets = (db.query(User)
                   .filter(User.supplier_id == o.target_supplier_id,
                           User.is_active == 1, User.id != reporter_id).all())
    elif v.department_id:
        targets = (db.query(User)
                   .filter(User.department_id == v.department_id,
                           User.is_active == 1, User.id != reporter_id).all())
    else:
        targets = []
    if not targets:
        targets = (db.query(User)
                   .filter(User.is_superuser == 1, User.id != reporter_id).all())

    if targets:
        title = f"新维修工单 {o.order_no}"
        body = f"车牌 {v.plate_no}：{(o.description or '')[:200]}"
        for u in targets:
            notify(db, u.id, title, body, kind="repair",
                   target_type="repair_order", target_id=o.id, actor_id=reporter_id)
        # notify() 不再内部 commit（保持事务边界），此处为后置独立流程
        db.commit()
