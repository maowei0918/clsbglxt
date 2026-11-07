"""备份管理 API。"""
import logging
from pathlib import Path

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from ..core.config import BASE_DIR, DATA_DIR
from ..core.database import get_db
from ..core.deps import get_current_user, require_perm
from ..models import BackupTarget, BackupJob

log = logging.getLogger("clglxt.backup.api")
router = APIRouter(prefix="/backup", tags=["backup"])

# Web 静态目录：dump 若落在这里，等于把整库（含 bcrypt 哈希、供应商
# 凭据）以无鉴权 HTTP 可下载的形式暴露。
_WEB_DIR = DATA_DIR / "web"


class TargetIn(BaseModel):
    """备份目标创建入参（白名单，禁止任意字段注入）。

    旧实现直接 `BackupTarget(**data)`，客户端可写入任意列，
    并把 base_path 指向 Web 静态目录，导致全库 dump 可被匿名下载。
    """
    name: str = Field(min_length=1, max_length=64)
    kind: str = Field(default="local", pattern="^(local|s3|webdav)$")
    base_path: str = Field(default="", max_length=512)
    s3_endpoint: str = Field(default="", max_length=255)
    s3_bucket: str = Field(default="", max_length=128)
    s3_region: str = Field(default="us-east-1", max_length=32)
    s3_access_key: str = Field(default="", max_length=128)
    s3_secret: str = Field(default="", max_length=255)
    s3_prefix: str = Field(default="fms", max_length=255)
    dav_url: str = Field(default="", max_length=255)
    dav_secure: bool = True
    dav_host: str = Field(default="", max_length=255)
    dav_port: int = Field(default=0, ge=0, le=65535)
    dav_path: str = Field(default="", max_length=255)
    dav_user: str = Field(default="", max_length=64)
    dav_pass: str = Field(default="", max_length=255)
    enabled: bool = True


class JobIn(BaseModel):
    """备份任务创建入参（白名单）。"""
    name: str = Field(min_length=1, max_length=64)
    target_id: int = Field(ge=1)
    mode: str = Field(default="full", pattern="^(full|db|uploads)$")
    keep_count: int = Field(default=7, ge=1, le=365)
    enabled: bool = True
    cron: str = Field(default="", max_length=64)


def _check_base_path(raw: str) -> str:
    """校验备份目录：必须绝对路径，且不得落在 Web 静态目录或应用源码目录。

    旧实现不校验，客户端可把 base_path 指向 DATA_DIR/web/，
    使全库 dump 出现在 StaticFiles 挂载的目录下 —— 无鉴权 HTTP 下载。
    """
    if not raw:
        return ""
    p = Path(raw).expanduser()
    if not p.is_absolute():
        raise HTTPException(400, "备份目录必须是绝对路径")
    try:
        p = p.resolve(strict=False)
    except OSError as e:
        raise HTTPException(400, f"备份路径无效：{e}")
    for bad in (_WEB_DIR, Path(BASE_DIR)):
        # 注意：不能先判断 bad.exists() 再决定是否拦截。Web 目录尚未
        # 创建时（新部署、或刚清理过）恰好是最该拦的场景——等它被创建
        # 或挂载为静态目录后，dump 已经躺在里面了。路径比较本身不需要
        # 目标存在，resolve(strict=False) 已处理符号链接。
        if p == bad or bad in p.parents:
            raise HTTPException(400, f"备份目录不得位于 {bad} 之下")
    return str(p)


@router.get("/targets")
def list_targets(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    rows = db.query(BackupTarget).order_by(BackupTarget.id).all()
    return [{"id": r.id, "name": r.name, "kind": r.kind, "enabled": r.enabled}
            for r in rows]


@router.post("/targets", dependencies=[Depends(require_perm("system:backup_manage"))])
def create_target(data: TargetIn, db: Session = Depends(get_db)):
    payload = data.model_dump()
    payload["base_path"] = _check_base_path(payload.get("base_path", ""))
    t = BackupTarget(**payload)
    db.add(t)
    db.commit()
    return {"id": t.id, "name": t.name}


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    rows = db.query(BackupJob).order_by(BackupJob.id).all()
    return [{"id": r.id, "name": r.name, "enabled": r.enabled,
             "last_run_at": r.last_run_at} for r in rows]


@router.post("/jobs", dependencies=[Depends(require_perm("system:backup_manage"))])
def create_job(data: JobIn, db: Session = Depends(get_db)):
    if not db.get(BackupTarget, data.target_id):
        raise HTTPException(404, "备份目标不存在")
    j = BackupJob(**data.model_dump())
    db.add(j)
    db.commit()
    return {"id": j.id, "name": j.name}
