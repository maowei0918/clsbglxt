"""FMS 数据导入任务（Celery）。"""
import logging

from app.celery_app import celery_app
from app.core.fms_importer import run_import

log = logging.getLogger("clglxt.import.task")


@celery_app.task(bind=True, queue="default", acks_late=True, time_limit=3600)
def run_import_job(self, scopes: list[str] | None = None, batch_size: int = 100):
    """执行 FMS 数据导入。"""
    log.info("开始 FMS 导入 scopes=%s batch=%s", scopes, batch_size)
    try:
        result = run_import(scopes=scopes, batch_size=batch_size)
        log.info("FMS 导入结束 state=%s created=%s updated=%s skipped=%s",
                 result.get("state"), result.get("created"),
                 result.get("updated"), result.get("skipped"))
        return {"ok": True, "state": result.get("state"),
                "created": result.get("created"), "updated": result.get("updated"),
                "skipped": result.get("skipped")}
    except Exception as e:  # noqa: BLE001
        log.exception("FMS 导入失败")
        return {"ok": False, "error": str(e)[:300]}
