"""Celery beat 定时任务：每日自动备份 + 审计清理 + 备份记录清理 + 临时用车归档。"""
import logging
from datetime import datetime, timedelta

from app.celery_app import celery_app
from app.core.config import AUDIT_RETENTION_DAYS, VEHICLE_USE_AUTO_ARCHIVE_DAYS

log = logging.getLogger("clglxt.tasks")


@celery_app.task(queue="backup")
def daily_backup_all():
    """每日触发所有 enabled 的定时备份任务。"""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from app.core.config import DATABASE_URL
    from app.models import BackupJob
    from app.workers.backup_tasks import run_backup_job

    engine = create_engine(DATABASE_URL, future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    n = 0
    try:
        jobs = (db.query(BackupJob)
                .filter(BackupJob.enabled.is_(True),
                        BackupJob.schedule_type != "manual")
                .all())
        for j in jobs:
            run_backup_job.delay(j.id, trigger="schedule")
            n += 1
        log.info("已入队 %s 个定时备份任务", n)
    finally:
        db.close()
    return {"queued": n}


@celery_app.task(queue="default")
def purge_old_audit_logs():
    """清理过期审计日志（保留 AUDIT_RETENTION_DAYS 天）。"""
    from sqlalchemy import create_engine, delete
    from sqlalchemy.orm import sessionmaker
    from app.core.config import DATABASE_URL
    from app.models import AuditLog

    engine = create_engine(DATABASE_URL, future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    try:
        cutoff = datetime.now() - timedelta(days=AUDIT_RETENTION_DAYS)
        r = db.query(AuditLog).filter(AuditLog.created_at < cutoff).delete()
        db.commit()
        log.info("清理审计日志 %s 条", r)
        return {"deleted": r}
    finally:
        db.close()


@celery_app.task(queue="default")
def auto_archive_expired_vehicle_uses():
    """临时用车自动归档：end_date 超过 VEHICLE_USE_AUTO_ARCHIVE_DAYS 天即归档。

    只标记 archived=True，不删数据；days<=0 视为关闭自动归档。
    返回受影响条数，便于排查 beat 是否真的跑了。
    """
    from datetime import date as _date

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from app.core.config import DATABASE_URL
    from app.models import TempVehicleUse, now as db_now

    days = VEHICLE_USE_AUTO_ARCHIVE_DAYS
    if days <= 0:
        log.info("临时用车自动归档已关闭（VEHICLE_USE_AUTO_ARCHIVE_DAYS=%s）", days)
        return {"archived": 0, "disabled": True}

    cutoff = _date.today() - timedelta(days=days)

    engine = create_engine(DATABASE_URL, future=True)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    db = Session()
    try:
        r = (db.query(TempVehicleUse)
             .filter(TempVehicleUse.archived.is_(False),
                     TempVehicleUse.end_date < cutoff)
             .update({"archived": True, "archived_at": db_now()},
                     synchronize_session=False))
        db.commit()
        log.info("临时用车自动归档 %s 条（end_date < %s）", r, cutoff)
        return {"archived": r, "cutoff": cutoff.isoformat()}
    finally:
        db.close()
