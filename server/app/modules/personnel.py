"""人员管理：建号/改号/删号/重置密码/导入。

与 fms 的关键差异：
  * 汽修厂与部门账号互斥（_resolve_owner 强制二选一）
  * 软删：deleted_at 置 EPOCH，is_active=0
  * 重置密码：12 位随机 + 强制首改 + token_version 自增
  * 角色分配：按归属隔离 + _filter_dept_perms 防越权
  * TODO: 导入保留 xlsx 解析，模板沿用 fms 格式
"""
import logging
import secrets
import string

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import get_current_user, require_perm, invalidate_perm_cache
from ..core.security import hash_password, validate_password
from ..models import (Department, Role, RolePermission, User, UserRole, Supplier,
                      EPOCH, alive, utcnow_naive)

log = logging.getLogger("clglxt.personnel")
router = APIRouter(prefix="/personnel", tags=["personnel"])

# 账号归属 → 角色归属 的映射（_resolve_owner 返回 dept/supplier，
# 而 Role.side 用 internal/external 表达）
_OWNER_SIDE = {"dept": "internal", "supplier": "external"}


@router.get("", dependencies=[Depends(require_perm("system:user_manage"))])
def list_users(
    keyword: str = "",
    department_id: int | None = None,
    include_deleted: bool = False,
    db: Session = Depends(get_db),
):
    """列人员（含已删，排除 superuser）。"""
    from ..models import EPOCH
    q = db.query(User).filter(User.is_superuser == False)
    if keyword:
        like = f"%{keyword.strip()}%"
        q = q.filter((User.username.like(like)) | (User.name.like(like)))
    if department_id is not None:
        q = q.filter(User.department_id == department_id)
    if not include_deleted:
        q = q.filter(alive(User.deleted_at))

    users = q.order_by(User.id).all()
    return [_out_user(u, db) for u in users]


@router.post("", dependencies=[Depends(require_perm("system:user_manage"))])
def create_user(
    username: str,
    name: str,
    password: str | None = None,
    department_id: int | None = None,
    supplier_id: int | None = None,
    phone: str = "",
    email: str = "",
    role_ids: list[int] | None = None,
    db: Session = Depends(get_db),
):
    """建号。"""
    username = username.strip().lower()
    if not username:
        raise HTTPException(400, "用户名不能为空")
    if db.query(User).filter(User.username == username).first():
        raise HTTPException(400, "该登录账号已存在")

    owner_type, owner_id = _resolve_owner(db, department_id, supplier_id)

    if not password:
        password = _gen_password()
    else:
        # 客户端显式传密码时必须过强度校验（与安装向导/改密口径一致）
        err = validate_password(password, username)
        if err:
            raise HTTPException(400, f"密码不符合要求：{err}")

    user = User(
        username=username, name=name.strip(),
        password_hash=hash_password(password),
        department_id=owner_type == "dept" and owner_id or None,
        supplier_id=owner_type == "supplier" and owner_id or None,
        phone=phone, email=email,
        must_change_password=True,
    )
    db.add(user)
    db.flush()

    if role_ids:
        _assign_roles(db, user.id, role_ids, owner_type)

    db.commit()
    return {"id": user.id, "username": user.username, "temp_password": password}


@router.put("/{user_id}", dependencies=[Depends(require_perm("system:user_manage"))])
def update_user(
    user_id: int,
    name: str | None = None,
    phone: str | None = None,
    email: str | None = None,
    department_id: int | None = None,
    supplier_id: int | None = None,
    is_active: bool | None = None,
    role_ids: list[int] | None = None,
    db: Session = Depends(get_db),
):
    """改号。"""
    user = db.get(User, user_id)
    # 已加载对象用哨兵值比较；alive() 是 SQL 表达式，仅用于查询过滤
    if not user or user.deleted_at != EPOCH or user.is_superuser:
        raise HTTPException(404, "人员不存在")

    if name is not None:
        user.name = name.strip()
    if phone is not None:
        user.phone = phone
    if email is not None:
        user.email = email

    if department_id is not None or supplier_id is not None:
        dept = department_id if department_id is not None else user.department_id
        supp = supplier_id if supplier_id is not None else user.supplier_id
        owner_type, owner_id = _resolve_owner(db, dept, supp)
        user.department_id = owner_type == "dept" and owner_id or None
        user.supplier_id = owner_type == "supplier" and owner_id or None

    if is_active is not None:
        user.is_active = is_active

    if role_ids is not None:
        owner_type = "supplier" if user.supplier_id else "dept"
        db.query(UserRole).filter(UserRole.user_id == user_id).delete()
        _assign_roles(db, user_id, role_ids, owner_type)
        invalidate_perm_cache(user_id)   # 角色变更后立即失效，避免旧权限最多残留 60s

    db.commit()
    return {"ok": True}


@router.delete("/{user_id}", dependencies=[Depends(require_perm("system:user_manage"))])
def delete_user(user_id: int, db: Session = Depends(get_db)):
    """删号=软删+停用。"""
    user = db.get(User, user_id)
    if not user or user.deleted_at != EPOCH or user.is_superuser:
        raise HTTPException(404, "人员不存在")

    user.deleted_at = utcnow_naive()
    user.is_active = False
    user.token_version += 1          # 旧 token 立即失效
    invalidate_perm_cache(user_id)   # 权限缓存同步失效
    db.commit()
    return {"ok": True}


@router.post("/{user_id}/reset-password", dependencies=[Depends(require_perm("system:user_manage"))])
def reset_password(user_id: int, db: Session = Depends(get_db)):
    """重置密码。"""
    from ..core.security import hash_password
    user = db.get(User, user_id)
    if not user or user.is_superuser:
        raise HTTPException(404, "人员不存在")

    new_pwd = _gen_password()
    user.password_hash = hash_password(new_pwd)
    user.token_version += 1
    user.must_change_password = True
    db.commit()
    return {"temp_password": new_pwd}


def _resolve_owner(db: Session, department_id: int | None, supplier_id: int | None):
    """账号归属二选一。"""
    if department_id and supplier_id:
        raise HTTPException(400, "部门与汽修厂不能同时归属")
    if department_id:
        dept = db.get(Department, department_id)
        if not dept:
            raise HTTPException(404, "部门不存在")
        return ("dept", department_id)
    if supplier_id:
        supp = db.get(Supplier, supplier_id)
        if not supp:
            raise HTTPException(404, "汽修厂不存在")
        return ("supplier", supplier_id)
    raise HTTPException(400, "必须指定部门或汽修厂归属")


def _assign_roles(db: Session, user_id: int, role_ids: list[int], owner_type: str):
    """分角色（归属隔离）。

    owner_type 来自 _resolve_owner，取值为 "dept"/"supplier"；
    而 Role.side 取值为 "internal"/"external"。必须先映射再比较，
    否则内部部门账号永远匹配不上（"dept" != "internal"）。
    """
    side = _OWNER_SIDE.get(owner_type)
    if side is None:
        raise HTTPException(400, f"未知账号归属：{owner_type}")
    for rid in role_ids:
        role = db.get(Role, rid)
        if not role:
            raise HTTPException(404, f"角色 {rid} 不存在")
        if role.side != side:
            raise HTTPException(400, f"角色 {role.name} 归属 {role.side}，与账号不匹配")
        if db.query(UserRole).filter(UserRole.user_id == user_id, UserRole.role_id == rid).first():
            continue
        db.add(UserRole(user_id=user_id, role_id=rid))


def _gen_password(length: int = 12) -> str:
    """12 位随机密码。"""
    alphabet = string.ascii_letters + string.digits
    return ''.join(secrets.choice(alphabet) for _ in range(length))


def _out_user(user: User, db: Session) -> dict:
    roles = (db.query(Role).join(UserRole, UserRole.role_id == Role.id)
             .filter(UserRole.user_id == user.id).all())
    return {
        "id": user.id, "username": user.username, "name": user.name,
        "phone": user.phone, "email": user.email,
        "department_id": user.department_id, "supplier_id": user.supplier_id,
        "must_change_password": user.must_change_password,
        "is_active": user.is_active,
        "deleted_at": user.deleted_at if user.deleted_at != EPOCH else None,
        "is_deleted": user.deleted_at != EPOCH,
        "roles": [{"id": r.id, "name": r.name} for r in roles],
    }
