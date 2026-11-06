"""合同管理：合同 + 明细 + 车型绑定 + 比对。"""
import logging
from decimal import Decimal

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import get_current_user, require_perm
from ..models import (Contract, ContractItem, ContractVehicleType, Supplier, User,
                      VehicleType)

log = logging.getLogger("clglxt.contracts")
router = APIRouter(prefix="/contracts", tags=["contracts"])


def _can_see_price(db: Session, user: User) -> bool:
    """合同价格可见性。

    Supplier.see_contract_price 字段此前全库零使用：汽修厂配置为不可见时，
    明细里的 part_price、labor_price 一律返回 None，避免价格条款泄露。
    内部账号与超管恒可见。
    """
    if user.is_superuser or user.supplier_id is None:
        return True
    shop = db.get(Supplier, user.supplier_id)
    return bool(shop and shop.see_contract_price)


@router.get("", dependencies=[Depends(require_perm("contract:view"))])
def list_contracts(keyword: str = "", is_active: bool | None = None,
                   db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    """合同列表（需 contract:view）。"""
    q = db.query(Contract)
    if keyword.strip():
        q = q.filter(Contract.name.like(f"%{keyword.strip()}%"))
    if is_active is not None:
        q = q.filter(Contract.is_active == is_active)
    see_price = _can_see_price(db, current_user)
    rows = q.order_by(Contract.id.desc()).all()
    return [_out_contract(c, db, see_price) for c in rows]


@router.post("", dependencies=[Depends(require_perm("contract:manage"))])
def create_contract(
    name: str,
    supplier: str = "",
    start_date: str | None = None,
    end_date: str | None = None,
    natures: str = "",
    vehicle_type_ids: list[int] | None = None,
    db: Session = Depends(get_db),
):
    name = name.strip()
    if not name:
        raise HTTPException(400, "合同名称不能为空")
    if db.query(Contract).filter(Contract.name == name).first():
        raise HTTPException(400, "该合同名称已存在")

    c = Contract(name=name, supplier=supplier, start_date=start_date,
                 end_date=end_date, natures=natures)
    db.add(c)
    db.flush()

    for tid in (vehicle_type_ids or []):
        if not db.get(VehicleType, tid):
            raise HTTPException(404, f"车辆类型 {tid} 不存在")
        db.add(ContractVehicleType(contract_id=c.id, vehicle_type_id=tid))

    db.commit()
    return {"id": c.id, "name": c.name}


@router.get("/{contract_id}", dependencies=[Depends(require_perm("contract:view"))])
def get_contract(contract_id: int, db: Session = Depends(get_db),
                 current_user: User = Depends(get_current_user)):
    c = db.get(Contract, contract_id)
    if not c:
        raise HTTPException(404, "合同不存在")
    see_price = _can_see_price(db, current_user)
    data = _out_contract(c, db, see_price)
    items = db.query(ContractItem).filter(ContractItem.contract_id == contract_id).all()
    data["items"] = [_out_item(i, see_price) for i in items]
    return data


@router.put("/{contract_id}", dependencies=[Depends(require_perm("contract:manage"))])
def update_contract(contract_id: int, payload: dict, db: Session = Depends(get_db)):
    c = db.get(Contract, contract_id)
    if not c:
        raise HTTPException(404, "合同不存在")

    if "name" in payload:
        name = str(payload["name"]).strip()
        if name and name != c.name:
            if db.query(Contract).filter(Contract.name == name).first():
                raise HTTPException(400, "该合同名称已存在")
            c.name = name
    for field in ("supplier", "start_date", "end_date", "natures"):
        if field in payload:
            setattr(c, field, payload[field])
    if "is_active" in payload:
        c.is_active = bool(payload["is_active"])

    if "vehicle_type_ids" in payload:
        db.query(ContractVehicleType).filter(
            ContractVehicleType.contract_id == contract_id).delete()
        for tid in (payload["vehicle_type_ids"] or []):
            db.add(ContractVehicleType(contract_id=contract_id, vehicle_type_id=tid))

    db.commit()
    return {"ok": True}


@router.delete("/{contract_id}", dependencies=[Depends(require_perm("contract:manage"))])
def delete_contract(contract_id: int, db: Session = Depends(get_db)):
    c = db.get(Contract, contract_id)
    if not c:
        raise HTTPException(404, "合同不存在")
    db.query(ContractVehicleType).filter(ContractVehicleType.contract_id == contract_id).delete()
    db.query(ContractItem).filter(ContractItem.contract_id == contract_id).delete()
    db.delete(c)
    db.commit()
    return {"ok": True}


# ─────────────── 合同明细 ───────────────

@router.post("/{contract_id}/items", dependencies=[Depends(require_perm("contract:manage"))])
def add_item(contract_id: int, part_name: str, vehicle_type_id: int,
             spec: str = "", unit: str = "个",
             part_price: float = 0, labor_price: float = 0, remark: str = "",
             db: Session = Depends(get_db)):
    if not db.get(Contract, contract_id):
        raise HTTPException(404, "合同不存在")
    if not db.get(VehicleType, vehicle_type_id):
        raise HTTPException(404, "车辆类型不存在")
    # 用 Decimal 收口：避免 float 二进制误差进入台账（会计必须精确到分）
    # 非法金额返回 400 而不是 500
    try:
        pp = Decimal(str(part_price)).quantize(Decimal("0.01"))
        lp = Decimal(str(labor_price)).quantize(Decimal("0.01"))
    except (ArithmeticError, TypeError, ValueError):
        raise HTTPException(400, "金额格式无效")
    if pp < 0 or lp < 0:
        raise HTTPException(400, "金额不能为负")
    if pp > 10_000_000 or lp > 10_000_000:
        raise HTTPException(400, "金额超过上限（1000万）")

    item = ContractItem(contract_id=contract_id, vehicle_type_id=vehicle_type_id,
                        part_name=part_name.strip() if part_name else "", spec=spec, unit=unit,
                        part_price=pp, labor_price=lp, remark=remark)
    db.add(item)
    db.commit()
    return {"id": item.id}


@router.delete("/items/{item_id}", dependencies=[Depends(require_perm("contract:manage"))])
def delete_item(item_id: int, db: Session = Depends(get_db)):
    item = db.get(ContractItem, item_id)
    if not item:
        raise HTTPException(404, "明细不存在")
    db.delete(item)
    db.commit()
    return {"ok": True}


def _out_contract(c: Contract, db: Session, see_price: bool = True) -> dict:
    types = (db.query(VehicleType)
             .join(ContractVehicleType, ContractVehicleType.vehicle_type_id == VehicleType.id)
             .filter(ContractVehicleType.contract_id == c.id).all())
    return {
        "id": c.id, "name": c.name, "supplier": c.supplier,
        "start_date": c.start_date.isoformat() if c.start_date else None,
        "end_date": c.end_date.isoformat() if c.end_date else None,
        "is_active": c.is_active, "natures": c.natures,
        "price_visible": see_price,
        "vehicle_types": [{"id": t.id, "name": t.name} for t in types],
        "created_at": c.created_at.isoformat() if c.created_at else None,
    }


def _out_item(i: ContractItem, see_price: bool = True) -> dict:
    return {
        "id": i.id, "contract_id": i.contract_id,
        "vehicle_type_id": i.vehicle_type_id,
        "vehicle_type_name": i.vehicle_type.name if i.vehicle_type else None,
        "part_name": i.part_name, "spec": i.spec, "unit": i.unit,
        "part_price": float(i.part_price or 0) if see_price else None,
        "labor_price": float(i.labor_price or 0) if see_price else None,
        "remark": i.remark,
    }
