"""站内通知：列表（分页）、未读数、已读操作。"""
import logging
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import get_current_user
from ..models import Notification, User

log = logging.getLogger("clglxt.notifications")
router = APIRouter(prefix="/notifications", tags=["notifications"])


@router.get("")
def list_my_notifications(
    unread_only: bool = False,
    page: int = 1,
    page_size: int = 20,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户的通知，分页返回。

    返回：{items, total, page, page_size, has_more}
    """
    page = max(page, 1)
    page_size = min(max(page_size, 1), 100)
    q = db.query(Notification).filter(Notification.user_id == current_user.id)
    if unread_only:
        q = q.filter(Notification.read_at.is_(None))
    total = q.count()
    rows = (q.order_by(Notification.created_at.desc())
            .offset((page - 1) * page_size)
            .limit(page_size).all())
    return {
        "items": [
            {
                "id": n.id,
                "kind": n.kind,
                "title": n.title,
                "body": n.body,
                "target_type": n.target_type,
                "target_id": n.target_id,
                "read_at": n.read_at.isoformat() if n.read_at else None,
                "created_at": n.created_at.isoformat() if n.created_at else None,
            }
            for n in rows
        ],
        "total": total,
        "page": page,
        "page_size": page_size,
        "has_more": page * page_size < total,
    }


@router.get("/unread-count")
def unread_count(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """未读数量，供客户端角标。"""
    cnt = (
        db.query(Notification)
        .filter(Notification.user_id == current_user.id,
                Notification.read_at.is_(None))
        .count()
    )
    return {"unread": cnt}


@router.post("/{nid}/read")
def mark_read(
    nid: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """单条标记已读。"""
    n = db.get(Notification, nid)
    if not n or n.user_id != current_user.id:
        raise HTTPException(404, "通知不存在")
    if n.read_at is None:
        n.read_at = datetime.now(timezone.utc)
        db.commit()
    return {"ok": True}


@router.post("/read-all")
def mark_all_read(
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """当前用户全部通知标记已读。"""
    now = datetime.now(timezone.utc)
    n = (db.query(Notification)
         .filter(Notification.user_id == current_user.id,
                 Notification.read_at.is_(None))
         .update({Notification.read_at: now}, synchronize_session=False))
    db.commit()
    return {"ok": True, "updated": n}
