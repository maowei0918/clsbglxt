"""种子数据：admin 账号、内置角色。

启动必需项只有 admin + 3 个内置角色。
业务字典（部门/车型/性质/流程模板）由管理员在系统里建，
或通过 import_fms 从老 FMS 拉取，不在此硬编码。
"""
import logging

from sqlalchemy.orm import Session

from .database import init_db, SessionLocal, sync_sequences
from .security import hash_password
from ..models import AppSetting, Department, Role, RolePermission, User

log = logging.getLogger("clglxt.seed")


def seed(db: Session, org_name: str = "", dept_name: str = "") -> None:
    """插入种子数据（幂等：已存在则跳过）。

    org_name/dept_name 来自安装向导，缺省用通用值。
    """
    if db.query(User).filter(User.username == "admin").first():
        log.info("admin 账号已存在，跳过种子")
        return

    # 内置角色（id 固定为 1/2/3，代码里有硬编码引用）
    for rid, name, side in (
        (1, "内部管理员", "internal"),
        (2, "汽修厂管理员", "external"),
        (3, "超管", "internal"),
    ):
        if not db.get(Role, rid):
            db.add(Role(id=rid, name=name, side=side, is_system=True))
    db.flush()

    if org_name.strip():
        db.add(AppSetting(key="org_name", value=org_name.strip()))
    if dept_name.strip():
        d = Department(name=dept_name.strip())
        db.add(d)
        db.flush()

    # 超管账号：首次登录强制改密
    admin = User(
        username="admin",
        password_hash=hash_password("admin123"),
        name="系统管理员",
        is_superuser=True,
        is_active=True,
        token_version=0,
        must_change_password=True,
    )
    db.add(admin)

    # 超管角色通配所有权限点
    db.add(RolePermission(role_id=3, perm_code="*"))

    db.commit()
    log.info("种子数据已插入：admin / admin123（首次登录须改密）")


def run_seed() -> None:
    """独立入口：初始化表并写种子。"""
    init_db()
    db = SessionLocal()
    try:
        seed(db)
    finally:
        db.close()
    # seed() 里内置角色用显式 id 插入，不推进 PG 序列；
    # 不同步的话首个自增建角色/建号会撞主键（与 installer 同一坑）。
    sync_sequences()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    run_seed()
