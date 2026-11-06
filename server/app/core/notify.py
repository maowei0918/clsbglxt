"""通知模块：站内通知 + 邮件兜底。

事务边界约定（重要）：
  notify() **不调用 db.commit()**。调用方决定何时提交——这样在业务事务中
  调用 notify 时，通知与业务数据要么一起成功、要么一起回滚，避免"业务
  失败但通知已落库"的部分提交。仅在 Celery / 独立任务等无外层事务的场景，
  由调用方自行 commit。
"""
import logging
from datetime import timedelta

from sqlalchemy.orm import Session

from ..core.config import SMTP_HOST
from ..models import Notification, User, now
from ..workers.mail_tasks import send_mail

log = logging.getLogger("clglxt.notify")


def notify(db: Session, user_id: int, title: str, body: str,
           kind: str = "info", target_type: str = "", target_id: int = 0,
           actor_id: int = 0, email_body: str | None = None,
           dedup_minutes: int = 10) -> Notification | None:
    """写站内通知（不提交事务）；配了 SMTP 且用户有邮箱时异步发邮件。

    dedup_minutes：同一 user+kind+target 在该时间窗内只发一次，避免
    围栏判定/异常日检被并发重复触发时形成通知轰炸。
    """
    if not user_id or int(user_id) <= 0:
        return None
    uid = int(user_id)

    # 查重（窗口内同 target 已通知则跳过）
    cutoff = now() - timedelta(minutes=dedup_minutes)
    dup = (db.query(Notification)
           .filter(Notification.user_id == uid,
                   Notification.kind == kind,
                   Notification.target_type == target_type,
                   Notification.target_id == target_id,
                   Notification.created_at >= cutoff)
           .first())
    if dup:
        return None

    n = Notification(user_id=uid, title=title, body=body, kind=kind,
                     target_type=target_type, target_id=target_id,
                     actor_id=actor_id)
    db.add(n)
    # 注意：此处不 commit。由调用方统一提交，保持事务边界完整。

    if email_body is not None and SMTP_HOST:
        u = db.get(User, uid)
        if u and u.email:
            try:
                send_mail.delay(u.email, title, email_body, False)
            except Exception:
                # broker 不可用时不影响主流程
                log.warning("通知邮件入队失败 user=%s", uid)
    return n
