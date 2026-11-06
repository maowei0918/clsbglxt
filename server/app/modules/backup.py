"""备份管理 API。"""
import logging
from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import get_current_user, require_perm
from ..models import BackupTarget, BackupJob

log = logging.getLogger("clglxt.backup.api")
router = APIRouter(prefix="/backup", tags=["backup"])


@router.get("/targets")
def list_targets(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    rows = db.query(BackupTarget).order_by(BackupTarget.id).all()
    return [{"id": r.id, "name": r.name, "kind": r.kind, "enabled": r.enabled}
            for r in rows]


@router.post("/targets", dependencies=[Depends(require_perm("system:backup_manage"))])
def create_target(data: dict, db: Session = Depends(get_db)):
    t = BackupTarget(**data)
    db.add(t)
    db.commit()
    return {"id": t.id, "name": t.name}


@router.get("/jobs")
def list_jobs(db: Session = Depends(get_db), _: object = Depends(get_current_user)):
    rows = db.query(BackupJob).order_by(BackupJob.id).all()
    return [{"id": r.id, "name": r.name, "enabled": r.enabled,
             "last_run_at": r.last_run_at} for r in rows]


@router.post("/jobs", dependencies=[Depends(require_perm("system:backup_manage"))])
def create_job(data: dict, db: Session = Depends(get_db)):
    j = BackupJob(**data)
    db.add(j)
    db.commit()
    return {"id": j.id, "name": j.name}
