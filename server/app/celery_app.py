"""Celery 应用：异步任务（备份、AI 审核、邮件、通知）。

broker 与 result backend 都用 Redis。
任务按队列拆分：backup / ai / mail / notify，方便单独扩容。
"""
import logging

from celery import Celery

from .core.config import (CELERY_BROKER_URL, CELERY_RESULT_BACKEND,
                          CELERY_TASK_TIME_LIMIT, CELERY_WORKER_CONCURRENCY)

log = logging.getLogger("clglxt.celery")

celery_app = Celery(
    "clglxt",
    broker=CELERY_BROKER_URL,
    backend=CELERY_RESULT_BACKEND,
    include=[
        "app.workers.backup_tasks",
        "app.workers.ai_tasks",
        "app.workers.mail_tasks",
        "app.workers.scheduled_tasks",
        "app.workers.import_tasks",
    ],
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    timezone="Asia/Shanghai",
    enable_utc=True,
    worker_concurrency=CELERY_WORKER_CONCURRENCY,
    task_time_limit=CELERY_TASK_TIME_LIMIT,
    task_soft_time_limit=CELERY_TASK_TIME_LIMIT - 60,
    task_acks_late=True,
    worker_prefetch_multiplier=1,
    task_reject_on_worker_lost=True,
    result_expires=3600,
    task_default_queue="default",
    task_routes={
        "app.workers.backup_tasks.*": {"queue": "backup"},
        "app.workers.ai_tasks.*": {"queue": "ai"},
        "app.workers.mail_tasks.*": {"queue": "mail"},
        "app.workers.scheduled_tasks.*": {"queue": "default"},
        "app.workers.import_tasks.*": {"queue": "default"},
    },
    beat_schedule={
        # 每日 03:15 触发所有定时备份任务
        "daily-backup": {
            "task": "app.workers.scheduled_tasks.daily_backup_all",
            "schedule": 86400,
        },
        # 每周一 04:00 清理过期审计日志
        "purge-audit": {
            "task": "app.workers.scheduled_tasks.purge_old_audit_logs",
            "schedule": 86400 * 7,
        },
        # 每日 05:10 归档过期的临时用车记录（end_date 超过 N 天）
        "auto-archive-vehicle-use": {
            "task": "app.workers.scheduled_tasks.auto_archive_expired_vehicle_uses",
            "schedule": 86400,
        },
    },
)
