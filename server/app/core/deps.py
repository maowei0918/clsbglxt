"""依赖注入：当前用户、权限校验、部门隔离。

权限点的语义、部门隔离逻辑与 fms 一致。
服务端是唯一信任边界——前端隐藏菜单只是体验层，这里才是兜底。
"""
import logging
from typing import Optional

from fastapi import Depends, HTTPException, Request
from sqlalchemy.orm import Session

from .database import get_db
from .redis_client import cache_get, cache_set
from .security import decode_token

log = logging.getLogger("clglxt.auth")

# ─────────────────────────── 基础依赖 ───────────────────────────

def get_current_user(request: Request, db: Session = Depends(get_db)):
    """从 Bearer 或 Cookie 解析当前用户。

    优先看 Authorization 头的 access token；没有的话看 HttpOnly Cookie。
    Cookie 名：clglxt_at（access token）、clglxt_mt（media token）。
    """
    auth = request.headers.get("authorization", "")
    token = None
    if auth.lower().startswith("bearer "):
        token = auth[7:].strip()
    else:
        token = request.cookies.get("clglxt_at") or request.cookies.get("clglxt_mt")

    if not token:
        raise HTTPException(401, "未登录或登录已过期")

    payload = decode_token(token)
    if not payload or payload.get("scope") not in ("access", "media"):
        raise HTTPException(401, "令牌无效或已过期")

    # token_version 校验：改密/停用后旧 token 立即失效
    from ..models import User, UserRole, RolePermission
    user = db.get(User, payload.get("uid"))
    if not user or not user.is_active or user.deleted_at.year != 1970:
        raise HTTPException(401, "账号已停用或删除")
    if user.token_version != payload.get("tv", -1):
        raise HTTPException(401, "令牌已失效，请重新登录")

    return user


# ─────────────────────────── 权限 ───────────────────────────

# 缓存：用户权限集合（TTL 60s，权限变更后下次请求生效）
_PERM_CACHE_TTL = 60

def _load_perms(db: Session, user_id: int) -> list[str]:
    """从 user_roles → role_permissions 汇总权限点。"""
    cache_key = f"perms:{user_id}"
    cached = cache_get(cache_key)
    if cached is not None:
        return cached

    from ..models import UserRole, RolePermission
    rows = (
        db.query(RolePermission.perm_code)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .filter(UserRole.user_id == user_id)
        .all()
    )
    perms = sorted({r[0] for r in rows})
    cache_set(cache_key, perms, _PERM_CACHE_TTL)
    return perms


def can(user, perm_code: str, db: Session | None = None) -> bool:
    """判断用户是否拥有权限点。

    注意：原实现引用 user._db（ORM 对象上不存在该属性），是死代码。
    调用方需显式传入 db；未传 db 时退化为仅按超管身份判定。
    """
    if not user:
        return False
    if user.is_superuser:
        return True
    if perm_code == "*":
        return True
    if db is None:
        return False
    perms = _load_perms(db, user.id)
    return perm_code in perms or "*" in perms


def invalidate_perm_cache(user_id: int):
    from .redis_client import cache_del
    cache_del(f"perms:{user_id}")


def require_perm(perm_code: str):
    """权限依赖工厂。用法：Depends(require_perm("repair:view"))"""
    def _dep(user=Depends(get_current_user), db: Session = Depends(get_db)):
        # user 已在 get_current_user 中绑定 db，这里直接复用
        if user.is_superuser:
            return user
        if perm_code == "*":
            return user
        perms = _load_perms(db, user.id)
        if perm_code not in perms and "*" not in perms:
            raise HTTPException(403, "无权限")
        return user
    return _dep


# ─────────────────────────── 部门隔离 ───────────────────────────

def require_internal_dep():
    """内部部门账号才允许（汽修厂账号被拦）。"""
    def _dep(user=Depends(get_current_user)):
        if user.supplier_id is not None:
            raise HTTPException(403, "汽修厂账号无权访问内部功能")
        return user
    return _dep


def require_supplier_dep():
    """汽修厂账号才允许。"""
    def _dep(user=Depends(get_current_user)):
        if user.supplier_id is None:
            raise HTTPException(403, "仅汽修厂账号可访问")
        return user
    return _dep


class DeptChecker:
    """部门范围校验工具。

    规则（与 fms 一致）：
      * 超管：看全部
      * 汽修厂：只看服务范围内的部门（需单独配置）
      * 内部用户：data:scope_all 看全公司，否则只看本部门
    """
    def __init__(self, user, db):
        self.user = user
        self.db = db

    def can_see_dept(self, dept_id: Optional[int]) -> bool:
        if self.user.is_superuser:
            return True
        if self.user.supplier_id is not None:
            from ..models import Supplier
            sup = self.db.get(Supplier, self.user.supplier_id)
            if not sup:
                return False
            if sup.serves_all_depts:
                return True
            import json
            return dept_id in (json.loads(sup.dept_ids or "[]"))
        # 内部用户
        perms = _load_perms(self.db, self.user.id)
        if "data:scope_all" in perms:
            return True
        return dept_id == self.user.department_id

    def has_scope_all(self) -> bool:
        """是否拥有「看全部」视野（超管 / serves_all / data:scope_all）。"""
        if self.user.is_superuser:
            return True
        if self.user.supplier_id is not None:
            from ..models import Supplier
            sup = self.db.get(Supplier, self.user.supplier_id)
            return bool(sup and sup.serves_all_depts)
        return "data:scope_all" in _load_perms(self.db, self.user.id)

    def visible_dept_ids(self) -> list[int] | None:
        """可见部门 ID 列表；None 表示「不限制（看全部）」。

        用于列表查询：返回 list 时调用方应加 `.in_(ids)` 过滤。
        """
        if self.has_scope_all():
            return None
        if self.user.supplier_id is not None:
            import json
            from ..models import Supplier
            sup = self.db.get(Supplier, self.user.supplier_id)
            ids = json.loads(sup.dept_ids or "[]") if sup else []
            return [int(x) for x in ids] or [-1]  # 空范围 → 无可见数据
        return [self.user.department_id] if self.user.department_id is not None else [-1]

    def apply_dept_filter(self, q, dept_column, requested_dept_id: Optional[int] = None):
        """给列表查询统一加部门隔离，返回新 query。

        非超管显式请求了不可见部门时抛 403（防客户端传 department_id 越权）。
        """
        if requested_dept_id is not None:
            self.check_dept(requested_dept_id, "数据")
            return q.filter(dept_column == requested_dept_id)
        ids = self.visible_dept_ids()
        if ids is None:
            return q
        return q.filter(dept_column.in_(ids))

    def check_dept(self, dept_id: Optional[int], res_name: str = "资源"):
        if not self.can_see_dept(dept_id):
            raise HTTPException(403, f"无权访问该{res_name}")


def check_dept(db, user, dept_id: Optional[int], res_name: str = "资源"):
    DeptChecker(user, db).check_dept(dept_id, res_name)
