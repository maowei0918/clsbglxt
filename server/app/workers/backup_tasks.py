"""备份任务：调用 core/backup_engine 真实执行。"""
import logging

from app.celery_app import celery_app
from app.core.backup_engine import run_backup as _run_backup

log = logging.getLogger("clglxt.backup.task")


@celery_app.task(bind=True, queue="backup", acks_late=True, time_limit=3600)
def run_backup_job(self, job_id: int, operator_id: int = 0, trigger: str = "manual"):
    """执行一个备份任务。"""
    log.info("开始备份 job=%s trigger=%s", job_id, trigger)
    result = _run_backup(job_id, operator_id=operator_id, trigger=trigger)
    log.info("备份完成 job=%s result=%s", job_id, result)
    return result


@celery_app.task(bind=True, queue="backup")
def run_restore(self, run_id: int):
    """恢复：从备份快照还原数据库。

    生产建议由人工确认后执行（破坏性操作），此处只做前置校验与提示。
    """
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.config import DATABASE_URL
    from app.models import BackupRun

    engine = create_engine(DATABASE_URL, future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    try:
        run = db.get(BackupRun, run_id)
        if not run:
            return {"ok": False, "reason": "备份记录不存在"}
        if run.status != "success":
            return {"ok": False, "reason": "该备份未成功完成，无法恢复"}
        snapshot = run.snapshot_dir or ""
        return {
            "ok": True,
            "manual_required": True,
            "message": "恢复为破坏性操作，请人工执行："
                       f"pg_restore -d <db> {snapshot}",
        }
    finally:
        db.close()
