"""认证：登录、改密、我、登出。

与 fms 的关键差异：
  * 限频走 Redis（滑动窗口），不再落库
  * 不再兼容 fnOS 子路径，API 前缀固定 /api
"""
import logging

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from ..core.config import ACCESS_TOKEN_EXPIRE_MINUTES
from ..core.database import get_db
from ..core.deps import get_current_user
from ..core.redis_client import rate_limit, rate_limit_reset
from ..core.security import (create_access_token, hash_password,
                             validate_password, verify_dummy,
                             verify_password)
from ..models import (EPOCH, Department, RolePermission, Supplier, User,
                      UserRole)

log = logging.getLogger("clglxt.auth")
router = APIRouter(prefix="/auth", tags=["auth"])

_LIMIT_ACCOUNT = (5, 300)      # 5 次 / 5 分钟（单账号）
_LIMIT_IP_ACCOUNT = (20, 300)  # 20 次 / 5 分钟（同 IP + 同账号）
_LIMIT_GLOBAL = (200, 60)      # 200 次 / 1 分钟


async def _read_credentials(request: Request, keys: tuple = ("username", "password")) -> tuple[str, str]:
    """从请求体读取凭据：JSON body → 表单。

    刻意不支持 query 参数：密码若走 URL，会被 Nginx 与 Gunicorn 的
    access log 以明文记录（包含完整 $request），也会进入 Referer、
    浏览器历史与代理日志。前端与 Android 客户端均使用 JSON body。
    """
    k1, k2 = keys
    try:
        body = await request.json()
        if isinstance(body, dict) and (k1 in body or k2 in body):
            return str(body.get(k1, "")), str(body.get(k2, ""))
    except Exception:
        pass
    form = await request.form()
    return str(form.get(k1, "")), str(form.get(k2, ""))


def client_ip(request: Request) -> str:
    """取真实客户端 IP（信任代理头，仅当直连方是可信代理时）。"""
    from ..core.config import TRUSTED_PROXIES
    direct = request.client.host if request.client else ""
    if direct in TRUSTED_PROXIES:
        xff = request.headers.get("x-forwarded-for", "")
        if xff:
            return xff.split(",")[0].strip()
    return direct


@router.post("/login")
async def login(
    request: Request,
    db: Session = Depends(get_db),
):
    """登录。账号 / IP+账号 / 全局三维限频。

    凭据读取优先级：JSON body → 表单 → query（兼容旧客户端）。
    生产建议走 JSON body，避免密码出现在 URL / access log / Referer。

    防账号枚举：账号不存在时也走一次 bcrypt（verify_dummy），
    让响应时间与「密码错误」一致，攻击者无法据此判断账号是否存在。
    """
    username, password = await _read_credentials(request)
    ip = client_ip(request)
    uname = (username or "").strip().lower()

    if not uname or not password:
        raise HTTPException(401, "用户名或密码错误")

    for key, (limit, window) in (
        (f"login:acct:{uname}", _LIMIT_ACCOUNT),
        (f"login:ipacct:{ip}:{uname}", _LIMIT_IP_ACCOUNT),
        ("login:global", _LIMIT_GLOBAL),
    ):
        ok, _ = rate_limit(key, limit, window)
        if not ok:
            raise HTTPException(429, "登录尝试过于频繁，请稍后再试")

    user = (
        db.query(User)
        .filter(User.username == uname,
                User.deleted_at == EPOCH,
                User.is_active.is_(True))
        .first()
    )

    if user is None:
        verify_dummy()          # 等时化，防枚举
        raise HTTPException(401, "用户名或密码错误")

    if not verify_password(password, user.password_hash):
        raise HTTPException(401, "用户名或密码错误")

    # 登录成功：清掉该账号的失败计数
    rate_limit_reset(f"login:acct:{uname}")

    token = create_access_token(user.id, user.token_version, user.is_superuser,
                                username=user.username)
    return {
        "token": token,
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": _out_user(user, db),
    }


@router.get("/me")
def me(current_user: User = Depends(get_current_user), db: Session = Depends(get_db)):
    """当前用户信息 + 权限点。"""
    return _out_user(current_user, db)


@router.post("/logout")
def logout():
    """登出（无状态 JWT，前端丢弃 token 即可）。"""
    return {"ok": True}


@router.post("/change-password")
async def change_password(
    request: Request,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """改密：校验旧密码 → 强度校验 → token_version 自增（旧 token 全部失效）→ 换发。

    凭据优先从 JSON body 读，兼容旧客户端的 query 传参。
    """
    old_password, new_password = await _read_credentials(
        request, keys=("old_password", "new_password"))
    if not verify_password(old_password, current_user.password_hash):
        raise HTTPException(400, "原密码错误")
    if old_password == new_password:
        raise HTTPException(400, "新密码不能与原密码相同")

    err = validate_password(new_password, current_user.username)
    if err:
        raise HTTPException(400, err)

    current_user.password_hash = hash_password(new_password)
    current_user.token_version += 1
    current_user.must_change_password = False
    db.commit()
    db.refresh(current_user)

    token = create_access_token(current_user.id, current_user.token_version,
                                current_user.is_superuser,
                                username=current_user.username)
    return {
        "token": token,
        "expires_in": ACCESS_TOKEN_EXPIRE_MINUTES * 60,
        "user": _out_user(current_user, db),
    }


# ─────────────────────────── 内部工具 ───────────────────────────

def _perm_codes(db: Session, user: User) -> list[str]:
    """汇总用户权限点：超管 → ["*"]；否则并集其所有角色。"""
    if user.is_superuser:
        return ["*"]
    rows = (
        db.query(RolePermission.perm_code)
        .join(UserRole, UserRole.role_id == RolePermission.role_id)
        .filter(UserRole.user_id == user.id)
        .distinct()
        .all()
    )
    return sorted({r[0] for r in rows})


def _out_user(user: User, db: Session) -> dict:
    """用户信息出参（含权限点与归属）。"""
    dept_name = ""
    if user.department_id:
        d = db.get(Department, user.department_id)
        dept_name = d.name if d else ""

    supplier_name = ""
    if user.supplier_id:
        s = db.get(Supplier, user.supplier_id)
        supplier_name = s.name if s else ""

    return {
        "id": user.id,
        "username": user.username,
        "name": user.name,
        "phone": user.phone,
        "email": user.email or "",
        "department_id": user.department_id,
        "department_name": dept_name,
        "supplier_id": user.supplier_id,
        "supplier_name": supplier_name,
        "is_supplier": bool(user.supplier_id),
        "is_superuser": user.is_superuser,
        "must_change_password": user.must_change_password,
        "perms": _perm_codes(db, user),
    }
