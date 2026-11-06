"""汽修厂管理：CRUD + 可见范围配置 + 停用踢下线。"""
import json
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import get_current_user, require_perm, invalidate_perm_cache
from ..models import Supplier, User, alive

log = logging.getLogger("clglxt.suppliers")
router = APIRouter(prefix="/suppliers", tags=["suppliers"])


@router.get("")
def list_suppliers(db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    rows = db.query(Supplier).order_by(Supplier.id).all()
    return [{"id": s.id, "name": s.name, "contact": s.contact,
             "phone": s.phone, "address": s.address,
             "is_active": s.is_active,
             "serves_all_depts": s.serves_all_depts,
             "dept_ids": s.dept_ids,
             "see_all_orders": s.see_all_orders,
             "see_contract_price": s.see_contract_price,
             "allow_templates": s.allow_templates}
            for s in rows]


@router.post("")
def create_supplier(
    name: str, contact: str = "", phone: str = "",
    address: str = "", remark: str = "",
    serves_all_depts: bool = False, dept_ids: list[int] | None = None,
    see_all_orders: bool = False, see_contract_price: bool = True,
    allow_templates: bool = False,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_perm("system:user_manage")),
):
    if db.query(Supplier).filter(Supplier.name == name).first():
        raise HTTPException(400, "汽修厂名称已存在")
    s = Supplier(name=name, contact=contact, phone=phone, address=address,
                 remark=remark, serves_all_depts=serves_all_depts,
                 dept_ids=json.dumps(dept_ids or []),
                 see_all_orders=see_all_orders,
                 see_contract_price=see_contract_price,
                 allow_templates=allow_templates)
    db.add(s)
    db.commit()
    return {"id": s.id}


@router.put("/{sid}")
def update_supplier(
    sid: int, name: str | None = None, contact: str | None = None,
    phone: str | None = None, address: str | None = None,
    remark: str | None = None,
    serves_all_depts: bool | None = None,
    dept_ids: list[int] | None = None,
    see_all_orders: bool | None = None,
    see_contract_price: bool | None = None,
    allow_templates: bool | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(require_perm("system:user_manage")),
):
    s = db.get(Supplier, sid)
    if not s:
        raise HTTPException(404, "汽修厂不存在")
    if name is not None:
        if db.query(Supplier).filter(Supplier.name == name, Supplier.id != sid).first():
            raise HTTPException(400, "汽修厂名称已存在")
        s.name = name
    if contact is not None:
        s.contact = contact
    if phone is not None:
        s.phone = phone
    if address is not None:
        s.address = address
    if remark is not None:
        s.remark = remark
    if serves_all_depts is not None:
        s.serves_all_depts = serves_all_depts
    if dept_ids is not None:
        s.dept_ids = json.dumps(dept_ids)
    if see_all_orders is not None:
        s.see_all_orders = see_all_orders
    if see_contract_price is not None:
        s.see_contract_price = see_contract_price
    if allow_templates is not None:
        s.allow_templates = allow_templates
    if is_active is not None:
        s.is_active = is_active
        if not is_active:
            # 停用即踢下线：自增该厂所有账号的 token_version + 失效权限缓存
            users = db.query(User).filter(User.supplier_id == sid).all()
            for u in users:
                u.token_version += 1
                invalidate_perm_cache(u.id)
    db.commit()
    return {"ok": True}


@router.delete("/{sid}")
def delete_supplier(sid: int, db: Session = Depends(get_db),
                    current_user: User = Depends(require_perm("system:user_manage"))):
    s = db.get(Supplier, sid)
    if not s:
        raise HTTPException(404, "汽修厂不存在")
    # 检查账号归属（alive() = deleted_at == EPOCH 哨兵，不能用 is_(None)）
    if db.query(User).filter(User.supplier_id == sid,
                            alive(User.deleted_at)).first():
        raise HTTPException(400, "该汽修厂下仍有账号，请先处理账号归属")
    db.delete(s)
    db.commit()
    return {"ok": True}
