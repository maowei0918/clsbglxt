"""备份引擎：根据 DATABASE_URL 选择 pg_dump 或 SQLite 文件复制。

设计：
  * PostgreSQL → pg_dump --format=custom -Z9 > <target>/clglxt_<ts>.dump
  * SQLite     → shutil.copy2(clglxt.db, <target>/clglxt_<ts>.db)
  * 同时打包 data/uploads/ 到 <target>/uploads_<ts>.tar.gz
  * 写入 BackupRun 执行情况，并按 job.keep_count 清理旧备份
"""
import logging
import os
import shutil
import subprocess
import tarfile
from datetime import datetime
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from ..core.config import DATABASE_URL, DATA_DIR, UPLOAD_DIR, BACKUP_DIR
from ..models import BackupJob, BackupRun, BackupTarget

log = logging.getLogger("clglxt.backup")

_TS = lambda: datetime.now().strftime("%Y%m%d_%H%M%S")
_PG_PROTO = ("postgresql", "postgres")


def _parse_db_url(url: str):
    """返回 (dialect, components)。"""
    p = urlparse(url)
    return p.scheme.split("+")[0], p


def _dump_postgres(db_url: str, out_path: Path) -> None:
    # 用 SQLAlchemy URL 对象解析，避免手写 urlparse 的边界问题
    from sqlalchemy.engine.url import make_url
    u = make_url(db_url)
    env = dict(os.environ)
    if u.password:
        env["PGPASSWORD"] = u.password
    cmd = [
        "pg_dump",
        "-h", u.host or "localhost",
        "-p", str(u.port or 5432),
        "-U", u.username or "postgres",
        "-d", (u.database or "").lstrip("/"),
        "--format=custom",
        "--no-owner",
        "--clean",
        "--if-exists",
        "-Z9",
        "-f", str(out_path),
    ]
    r = subprocess.run(cmd, env=env, capture_output=True, text=True, timeout=1800)
    if r.returncode != 0:
        raise RuntimeError(f"pg_dump 失败: {r.stderr[:300]}")


def _dump_sqlite(db_url: str, out_path: Path) -> None:
    from sqlalchemy.engine.url import make_url
    u = make_url(db_url)
    src = u.database or ""
    if not src:
        raise RuntimeError("SQLite 数据库路径为空")
    if not os.path.isabs(src):
        src = str(Path(DATA_DIR) / src)
    if not os.path.exists(src):
        raise RuntimeError(f"SQLite 文件不存在: {src}")
    shutil.copy2(src, str(out_path))


def _pack_uploads(out_path: Path) -> bool:
    if not UPLOAD_DIR.exists():
        return False
    files = list(UPLOAD_DIR.rglob("*"))
    files = [f for f in files if f.is_file()]
    if not files:
        return False
    with tarfile.open(str(out_path), "w:gz") as tar:
        for f in files:
            tar.add(f, arcname=str(f.relative_to(UPLOAD_DIR)))
    return True


def run_backup(job_id: int, operator_id: int = 0, trigger: str = "manual") -> dict:
    """执行一次备份。供 celery 任务调用。返回结果摘要。"""
    # 复用应用引擎：dialect 判断与连接参数都从引擎取，保证同源
    from .database import SessionLocal, engine as app_engine
    db = SessionLocal()
    run = None
    job = None
    try:
        job = db.get(BackupJob, job_id)
        if not job:
            raise RuntimeError(f"备份任务不存在: {job_id}")
        target = db.get(BackupTarget, job.target_id)
        if not target:
            raise RuntimeError(f"备份目标不存在: {job.target_id}")

        dest_dir = Path(target.base_path or str(BACKUP_DIR))
        dest_dir.mkdir(parents=True, exist_ok=True)

        ts = _TS()
        run = BackupRun(
            job_id=job.id, job_name=job.name, trigger=trigger,
            mode=job.mode, status="running",
        )
        db.add(run)
        db.commit()
        run_id = run.id

        # 数据库（从应用引擎取的 URL，同源）
        db_url = str(app_engine.url)
        db_out = dest_dir / f"clglxt_{ts}.dump"
        dialect, _ = _parse_db_url(db_url)
        if dialect in _PG_PROTO:
            _dump_postgres(db_url, db_out)
        else:
            _dump_sqlite(db_url, db_out)

        run.files_done += 1
        run.files_total += 1

        # 2. 附件
        up_out = dest_dir / f"uploads_{ts}.tar.gz"
        if _pack_uploads(up_out):
            run.files_done += 1
            run.files_total += 1

        # 3. 清理旧备份（按 keep_count）
        _cleanup_old(db, job, dest_dir, str(db_out.name), str(up_out.name))

        run.status = "success"
        run.finished_at = datetime.now()
        job.last_run_at = datetime.now()
        job.last_status = "success"
        db.commit()
        return {"ok": True, "run_id": run_id, "db_file": str(db_out),
                "uploads_file": str(up_out) if up_out.exists() else None}
    except Exception as e:
        log.exception("备份失败 job=%s", job_id)
        if run is not None:
            run.status = "failed"
            run.finished_at = datetime.now()
            run.message = str(e)[:500]
            job.last_status = "failed"
            db.commit()
        _notify_backup_failed(db, job, str(e))
        return {"ok": False, "error": str(e)[:300]}
    finally:
        db.close()


def _cleanup_old(db, job, dest_dir: Path, db_name: str, up_name: str):
    """保留 keep_count 份本任务备份，删除多余的（含 db 文件与 uploads 包）。"""
    if not job.keep_count or job.keep_count <= 0:
        return
    files = []
    for pat in ("clglxt_*.dump", "clglxt_*.db"):
        files.extend(dest_dir.glob(pat))
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    for f in files[job.keep_count:]:
        try:
            f.unlink()
        except OSError:
            continue
        # 删除配对的 uploads 包
        stem = f.name[len("clglxt_"):]
        stem = stem.rsplit(".", 1)[0]
        up = dest_dir / f"uploads_{stem}.tar.gz"
        if up.exists():
            try:
                up.unlink()
            except OSError:
                pass


def _notify_backup_failed(db, job, err: str) -> None:
    """备份失败：通知所有启用的超管。"""
    if job is None:
        return
    try:
        from .notify import notify
        from ..models import User

        admins = (db.query(User)
                  .filter(User.is_superuser == 1, User.is_active == 1).all())
        for u in admins:
            notify(db, u.id, f"备份失败：{job.name}",
                   f"备份任务「{job.name}」执行失败：{err[:300]}",
                   kind="backup", target_type="backup_job", target_id=job.id)
        db.commit()  # notify() 不再内部提交，此处显式落库
    except Exception:
        log.warning("备份失败通知写入异常", exc_info=True)
