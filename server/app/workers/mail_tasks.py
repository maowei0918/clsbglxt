"""邮件任务。SMTP 未配置时降级静默跳过。"""
import logging
import smtplib
from email.mime.text import MIMEText
from app.celery_app import celery_app
from app.core.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASS, SMTP_FROM

log = logging.getLogger("clglxt.mail")

@celery_app.task(bind=True, queue="mail", max_retries=3, default_retry_delay=60)
def send_mail(self, to, subject, body, html=False):
    """SMTP 未配置时降级跳过。"""
    if not SMTP_HOST:
        log.warning("SMTP 未配置，邮件未发送：%s → %s", to, subject)
        return {"ok": False, "degraded": True, "reason": "SMTP 未配置"}
    try:
        msg = MIMEText(body, "html" if html else "plain", "utf-8")
        msg["Subject"] = subject
        msg["From"] = SMTP_FROM or SMTP_USER
        msg["To"] = to
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT, timeout=15) as s:
            if SMTP_USER:
                s.starttls()
                s.login(SMTP_USER, SMTP_PASS)
            s.send_message(msg)
        return {"ok": True}
    except Exception as e:
        log.exception("邮件发送失败")
        raise self.retry(exc=e)
