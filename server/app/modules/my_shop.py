"""本厂管理（汽修厂自助）。

限制：
  * 仅限本厂管理员
  * 可分配的权限点仅限 ASSIGNABLE 白名单
  * 不可提升为超管
  * 管理员必须是本厂账号
"""
import json
import logging
import secrets
import string

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import get_current_user
from ..models import Role, RolePermission, Supplier, User, UserRole

log = logging.getLogger("clglxt.my_shop")
router = APIRouter(prefix="/my-shop", tags=["my-shop"])

# 汽修厂可分配的权限点白名单
ASSIGNABLE = {
    "repair:view", "repair:quote", "repair:report", "repair:accept",
    "contract:view", "vehicle:view",
}


def _get_my_shop(db: Session, user: User) -> Supplier:
    """当前用户所属汽修厂。"""
    if not user.supplier_id:
        raise HTTPException(403, "非汽修厂账号")
    shop = db.get(Supplier, user.supplier_id)
    if not shop:
        raise HTTPException(404, "汽修厂不存在")
    return shop


def _require_shop_admin(db: Session, user: User) -> Supplier:
    shop = _get_my_shop(db, user)
    if shop.admin_user_id != user.id and not user.is_superuser:
        raise HTTPException(403, "仅本厂管理员可操作")
    return shop


@router.get("")
def get_my_shop(db: Session = Depends(get_db),
                current_user: User = Depends(get_current_user)):
    shop = _get_my_shop(db, current_user)
    return {
        "id": shop.id, "name": shop.name, "contact": shop.contact,
        "phone": shop.phone, "address": shop.address,
        "is_active": shop.is_active,
        "is_admin": shop.admin_user_id == current_user.id,
        "see_contract_price": shop.see_contract_price,
    }


@router.get("/users")
def list_my_users(db: Session = Depends(get_db),
                  current_user: User = Depends(get_current_user)):
    shop = _require_shop_admin(db, current_user)
    users = db.query(User).filter(User.supplier_id == shop.id).all()
    return [{"id": u.id, "username": u.username, "name": u.name,
             "is_active": u.is_active, "phone": u.phone} for u in users]


@router.post("/users")
def create_my_user(
    username: str, name: str, phone: str = "",
    role_ids: list[int] | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """本厂建号（权限点受白名单限制）。"""
    shop = _require_shop_admin(db, current_user)
    from ..core.security import hash_password

    username = username.strip().lower()
    if not username:
        raise HTTPException(400, "用户名不能为空")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, "该登录账号已存在")

    pwd = ''.join(secrets.choice(string.ascii_letters + string.digits) for _ in range(12))
    user = User(username=username, name=name.strip(),
                password_hash=hash_password(pwd),
                supplier_id=shop.id, phone=phone,
                must_change_password=True, is_active=True)
    db.add(user)
    db.flush()

    for rid in (role_ids or []):
        role = db.get(Role, rid)
        if not role or role.side != "external":
            raise HTTPException(400, f"角色 {rid} 不可分配给汽修厂账号")
        # 权限点白名单校验
        perms = {p.perm_code for p in db.query(RolePermission)
                 .filter(RolePermission.role_id == rid).all()}
        if not perms.issubset(ASSIGNABLE):
            raise HTTPException(400, f"角色 {role.name} 含超出汽修厂范围的权限")
        db.add(UserRole(user_id=user.id, role_id=rid))

    db.commit()
    return {"id": user.id, "temp_password": pwd}


@router.delete("/users/{user_id}")
def delete_my_user(user_id: int, db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    shop = _require_shop_admin(db, current_user)
    target = db.get(User, user_id)
    if not target or target.supplier_id != shop.id:
        raise HTTPException(404, "账号不存在")
    if target.id == current_user.id:
        raise HTTPException(400, "不能删除自己")

    target.is_active = False
    target.token_version += 1
    db.commit()
    return {"ok": True}


@router.get("/assignable-perms")
def get_assignable_perms(_: object = Depends(get_current_user)):
    """汽修厂可分配的权限点。"""
    return sorted(ASSIGNABLE)
