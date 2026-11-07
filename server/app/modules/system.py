"""角色管理（权限点管理）。

角色 → 权限点 → 部门范围。
内置角色 is_system 标记，删除前检查引用。
"""
import logging

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import require_perm
from ..models import Role, RolePermission, UserRole

log = logging.getLogger("clglxt.system")
router = APIRouter(prefix="/system", tags=["system"])


@router.get("/roles", dependencies=[Depends(require_perm("system:role_manage"))])
def list_roles(db: Session = Depends(get_db)):
    """列表所有角色。"""
    roles = db.query(Role).order_by(Role.id).all()
    result = []
    for r in roles:
        perms = (db.query(RolePermission.perm_code)
                 .filter(RolePermission.role_id == r.id).all())
        result.append({
            "id": r.id, "name": r.name, "remark": r.remark,
            "is_system": r.is_system, "side": r.side,
            "perms": [p.perm_code for p in perms],
        })
    return result


@router.post("/roles", dependencies=[Depends(require_perm("system:role_manage"))])
def create_role(
    name: str,
    remark: str = "",
    side: str = "internal",
    perms: list[str] | None = None,
    db: Session = Depends(get_db),
):
    """新建角色。"""
    name = name.strip()
    if not name:
        raise HTTPException(400, "角色名称不能为空")
    if db.query(Role).filter(Role.name == name).first():
        raise HTTPException(400, "该角色名称已存在")
    if side not in ("internal", "external"):
        raise HTTPException(400, "归属必须是 internal 或 external")

    role = Role(name=name, remark=remark, side=side)
    db.add(role)
    db.flush()

    if perms:
        known_perms = _all_known_perms()
        for code in perms:
            if code and code in known_perms:
                db.add(RolePermission(role_id=role.id, perm_code=code))

    db.commit()
    return {"id": role.id, "name": role.name}


@router.put("/roles/{role_id}", dependencies=[Depends(require_perm("system:role_manage"))])
def update_role(
    role_id: int,
    name: str | None = None,
    remark: str | None = None,
    side: str | None = None,
    perms: list[str] | None = None,
    db: Session = Depends(get_db),
):
    """改角色（权限点除旧布新）。"""
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404, "角色不存在")

    if name is not None:
        name = name.strip()
        if name and name != role.name:
            if db.query(Role).filter(Role.name == name).first():
                raise HTTPException(400, "该角色名称已存在")
            role.name = name

    if remark is not None:
        role.remark = remark

    if side is not None:
        if side not in ("internal", "external"):
            raise HTTPException(400, "归属必须是 internal 或 external")
        # 归属切换前检查当前绑定人员与新归属匹配
        if side != role.side:
            _validate_side_change(db, role.id, side)
        role.side = side

    if perms is not None:
        known_perms = _all_known_perms()
        invalid = [c for c in perms if c and c not in known_perms]
        if invalid:
            raise HTTPException(400, f"未知权限点: {invalid}")
        db.query(RolePermission).filter(RolePermission.role_id == role_id).delete()
        for code in perms:
            if code:
                db.add(RolePermission(role_id=role_id, perm_code=code))

    db.commit()
    return {"ok": True}


@router.delete("/roles/{role_id}", dependencies=[Depends(require_perm("system:role_manage"))])
def delete_role(role_id: int, db: Session = Depends(get_db)):
    """删角色（被引用时拒绝）。"""
    role = db.get(Role, role_id)
    if not role:
        raise HTTPException(404, "角色不存在")

    # 被引用检查
    if db.query(UserRole).filter(UserRole.role_id == role_id).count() > 0:
        raise HTTPException(400, "该角色仍有绑定人员，无法删除")
    if db.query(RolePermission).filter(RolePermission.role_id == role_id).count() > 0:
        db.query(RolePermission).filter(RolePermission.role_id == role_id).delete()

    db.delete(role)
    db.commit()
    return {"ok": True}


# ─────────────── 权限点字典（与前端 store.js 保持一致） ───────────────
# 命名规则：模块:操作。前端路由守卫用 perm 字段校验。
KNOWN_PERMS = [
    "repair:view", "repair:quote", "repair:approve", "repair:manage",
    "repair:report",
    "contract:view", "contract:manage",
    "vehicle:view", "vehicle:manage",
    "daily_check:view", "daily_check:review", "daily_check:manage",
    "system:user_manage", "system:role_manage", "system:department_manage",
    "system:log_view",
    "system:setting_ai", "system:setting_mail", "system:setting_backup",
    "system:setting_datadir", "system:setting_vehicle_use",
    "system:setting_repair_days", "system:setting_network",
    "system:setting_app_update", "system:backup_manage",
    "vehicle_use:view", "vehicle_account:view",
    "fence:view", "fence:manage",
    "data:scope_all",
]


def _all_known_perms() -> set[str]:
    return set(KNOWN_PERMS)


def _validate_side_change(db: Session, role_id: int, new_side: str):
    """归属切换检查：新归属的绑定人员必须合法。"""
    from ..models import User
    # 当前绑定该角色的人员
    user_ids = (db.query(UserRole.user_id).filter(UserRole.role_id == role_id).all())
    for (uid,) in user_ids:
        user = db.get(User, uid)
        if not user:
            continue
        if new_side == "external" and user.supplier_id is None:
            raise HTTPException(400, f"内部人员 {user.username} 不能绑定外部角色")
        if new_side == "internal" and user.supplier_id is not None:
            raise HTTPException(400, f"汽修厂人员 {user.username} 不能绑定内部角色")
