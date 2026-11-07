"""维修管理 - 第二部分：详情、流程推进、报价、报价项。

本文件是 repairs 模块的第二部分，用于避免单文件过大。
被 modules/repairs.py 的 router 通过 include 方式挂载。
"""
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import func
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import get_current_user, require_perm
from ..models import (Attachment, ContractItem, RepairAction, RepairOrder,
                      RepairQuote, RepairQuoteItem, User, Vehicle)

log = logging.getLogger("clglxt.repairs2")

router = APIRouter(prefix="/repairs", tags=["repairs"])


def _has_perm(db, user, code: str) -> bool:
    """内联权限判定。"""
    from ..core.deps import _load_perms
    if user.is_superuser:
        return True
    perms = _load_perms(db, user.id)
    return code in perms or "*" in perms


def _dec(value, field: str) -> Decimal:
    """安全转 Decimal：非法输入返回 400 而不是 500。"""
    try:
        return Decimal(str(value))
    except (ArithmeticError, TypeError, ValueError):
        raise HTTPException(400, f"{field}格式无效")


# advance 允许的动作白名单（与前端状态机一致：处理中只能「通过」或「驳回」）
_ADVANCE_ACTIONS = {"accept", "reject"}


def _check_view(db: Session, user: User, order: RepairOrder):
    from .repairs import _can_view_order
    if not _can_view_order(db, user, order):
        raise HTTPException(403, "无权查看该维修单")


@router.get("/{order_id}")
def get_order(order_id: int, db: Session = Depends(get_db),
              current_user: User = Depends(get_current_user)):
    """维修单详情（含报价、附件、流转）。"""
    o = db.get(RepairOrder, order_id)
    if not o:
        raise HTTPException(404, "维修单不存在")
    _check_view(db, current_user, o)

    v = db.get(Vehicle, o.vehicle_id)
    quotes = db.query(RepairQuote).filter(RepairQuote.order_id == o.id).all()
    acts = (db.query(RepairAction).filter(RepairAction.order_id == o.id)
            .order_by(RepairAction.id).all())
    atts = db.query(Attachment).filter(Attachment.order_id == o.id).all()

    return {
        "id": o.id, "order_no": o.order_no, "status": o.status,
        "current_seq": o.current_seq,
        "vehicle": {"id": v.id, "plate_no": v.plate_no,
                    "brand": v.brand, "nature": v.nature} if v else None,
        "description": o.description,
        "reporter_id": o.reporter_id,
        "target_supplier_id": o.target_supplier_id,
        "quote_owner_id": o.quote_owner_id,
        "created_at": o.created_at.isoformat() if o.created_at else None,
        "finished_at": o.finished_at.isoformat() if o.finished_at else None,
        "repair_start": o.repair_start.isoformat() if o.repair_start else None,
        "repair_end": o.repair_end.isoformat() if o.repair_end else None,
        "actions": [{"id": a.id, "seq": a.seq, "action": a.action,
                     "note": a.note, "user_id": a.user_id,
                     "created_at": a.created_at.isoformat() if a.created_at else None}
                    for a in acts],
        "attachments": [{"id": a.id, "kind": a.kind, "orig_name": a.orig_name,
                         "step_seq": a.step_seq, "size": a.size}
                        for a in atts],
        "quotes": [{"id": q.id, "total": q.total,
                    "has_diff": q.has_diff,
                    "user_id": q.user_id}
                   for q in quotes],
    }


@router.post("/{order_id}/advance", dependencies=[Depends(require_perm("repair:approve"))])
def advance_order(
    order_id: int,
    action: str,
    note: str = "",
    seen_seq: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """推进维修流程（SELECT FOR UPDATE + seen_seq CAS 并发控制）。

    权限：repair:approve。之前无权限点，任何能查看该单的账号都能 accept/reject，
    绕过内部审批。
    """
    if action not in _ADVANCE_ACTIONS:
        raise HTTPException(400, f"不支持的动作：{action}")

    # 行锁：pg 的 FOR UPDATE 阻止并发读同一行，等事务提交后才放行
    o = (db.query(RepairOrder)
         .filter(RepairOrder.id == order_id)
         .with_for_update()
         .first())
    if not o:
        raise HTTPException(404, "维修单不存在")
    _check_view(db, current_user, o)

    if o.status != "processing":
        raise HTTPException(400, "该维修单已结束")

    # seen_seq CAS：客户端看到的 seq 必须匹配，否则说明被他人推进过
    if not seen_seq:
        raise HTTPException(422, "seen_seq 必传，取自详情接口的 current_seq")
    if o.current_seq != seen_seq:
        raise HTTPException(409, "该维修单已被他人推进，请刷新")

    o.current_seq += 1
    if action == "accept":
        o.status = "completed"
        o.finished_at = func.now()
    elif action == "reject":
        o.status = "rejected"
        o.finished_at = func.now()

    db.add(RepairAction(order_id=o.id, seq=o.current_seq,
                        user_id=current_user.id, action=action,
                        note=note[:500]))
    db.commit()
    return {"ok": True, "current_seq": o.current_seq, "status": o.status}


@router.post("/{order_id}/quote/claim")
def claim_quote(order_id: int, db: Session = Depends(get_db),
                current_user: User = Depends(require_perm("repair:quote"))):
    """抢单报价（原子抢占）。"""
    o = db.get(RepairOrder, order_id)
    if not o:
        raise HTTPException(404, "维修单不存在")
    # 归属校验：汽修厂只能抢「服务范围内车辆」且未指派给他厂的单
    if current_user.supplier_id is not None:
        if o.target_supplier_id and o.target_supplier_id != current_user.supplier_id:
            raise HTTPException(403, "该维修单已指派其他汽修厂")
        from ..core.deps import DeptChecker
        v = db.get(Vehicle, o.vehicle_id)
        if not v or v.department_id is None:
            raise HTTPException(403, "无权抢该维修单")
        DeptChecker(current_user, db).check_dept(v.department_id, "维修单")
    if o.quote_owner_id:
        raise HTTPException(409, "该维修单已被他人抢单")

    # 条件更新 CAS
    updated = (db.query(RepairOrder)
               .filter(RepairOrder.id == order_id, RepairOrder.quote_owner_id.is_(None))
               .update({RepairOrder.quote_owner_id: current_user.id},
                       synchronize_session=False))
    if not updated:
        raise HTTPException(409, "抢单失败，该维修单已被他人抢单")

    db.commit()
    return {"ok": True, "quote_owner_id": current_user.id}


@router.post("/{order_id}/quote")
def submit_quote(
    order_id: int,
    payload: dict,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_perm("repair:quote")),
):
    """提交报价。JSON body: {contract_id?, note?, items:[{part_name,qty,part_price,labor_price,contract_item_id?}]}"""
    contract_id = payload.get("contract_id")
    items = payload.get("items") or []
    note = str(payload.get("note") or "")

    o = db.get(RepairOrder, order_id)
    if not o:
        raise HTTPException(404, "维修单不存在")
    if o.status != "processing":
        raise HTTPException(400, "维修单已结束")
    if o.quote_owner_id and o.quote_owner_id != current_user.id:
        raise HTTPException(403, "该维修单已被他人抢单")
    # 归属校验：汽修厂只能对服务范围内车辆报价，且不能报已指派他厂的单
    if current_user.supplier_id is not None:
        if o.target_supplier_id and o.target_supplier_id != current_user.supplier_id:
            raise HTTPException(403, "该维修单已指派其他汽修厂")
        from ..core.deps import DeptChecker
        v = db.get(Vehicle, o.vehicle_id)
        if not v or v.department_id is None:
            raise HTTPException(403, "无权为该维修单报价")
        DeptChecker(current_user, db).check_dept(v.department_id, "维修单")
    if not items:
        raise HTTPException(400, "报价明细不能为空")

    total_part = Decimal("0")
    total_labor = Decimal("0")
    has_diff = False

    q = RepairQuote(order_id=o.id, user_id=current_user.id,
                    contract_id=contract_id, note=note[:500])
    db.add(q)
    db.flush()          # 先落库拿到 q.id，明细才能挂上 quote_id

    for it in items:
        name = str(it.get("part_name", "")).strip()
        if not name:
            raise HTTPException(400, "配件名称不能为空")
        qty = _dec(it.get("qty", 1), "数量")
        pp = _dec(it.get("part_price", 0), "配件单价")
        lp = _dec(it.get("labor_price", 0), "工时费")
        if qty <= 0:
            raise HTTPException(400, f"数量必须大于 0：{name}")
        if pp < 0 or lp < 0:
            raise HTTPException(400, f"金额不能为负：{name}")
        ci_id = it.get("contract_item_id")

        # 与合同价比对（精确相等，不用浮点容差）
        is_diff = False
        cpp, clp = None, None
        if contract_id and ci_id:
            ci = db.get(ContractItem, ci_id)
            if ci:
                cpp, clp = ci.part_price, ci.labor_price
                if pp != cpp or lp != clp:
                    is_diff = True
                    has_diff = True

        db.add(RepairQuoteItem(
            quote_id=q.id, part_name=name, qty=qty,
            part_price=pp, labor_price=lp,
            contract_item_id=ci_id,
            contract_part_price=cpp, contract_labor_price=clp,
            is_diff=is_diff,
        ))
        total_part += qty * pp
        total_labor += qty * lp

    q.total_part = round(total_part, 2)
    q.total_labor = round(total_labor, 2)
    q.total = round(total_part + total_labor, 2)
    q.has_diff = has_diff
    db.commit()
    return {"id": q.id, "total": q.total, "has_diff": q.has_diff}



@router.post("/{order_id}/cancel")
def cancel_order(order_id: int, payload: dict | None = None,
                 db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    """取消维修单（保留记录，不物理删）。权限：提报人本人 / repair:manage / 超管。"""
    reason = str((payload or {}).get("reason") or "")
    o = db.get(RepairOrder, order_id)
    if not o:
        raise HTTPException(404, "维修单不存在")
    _check_view(db, current_user, o)
    if not (current_user.is_superuser or o.reporter_id == current_user.id
            or _has_perm(db, current_user, "repair:manage")):
        raise HTTPException(403, "无权取消该维修单")
    if o.status != "processing":
        raise HTTPException(400, "该维修单已结束")

    o.status = "cancelled"
    o.finished_at = func.now()
    db.add(RepairAction(order_id=o.id, seq=o.current_seq,
                        user_id=current_user.id, action="cancel",
                        note=f"取消原因：{reason[:400]}"))
    db.commit()
    return {"ok": True}
