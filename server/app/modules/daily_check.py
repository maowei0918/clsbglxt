"""日检管理：模板 CRUD + 记录提交 + AI 审核触发。"""
import logging
from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from ..core.database import get_db
from ..core.deps import get_current_user
from ..core.parsing import parse_date
from ..models import (DailyCheckPhoto, DailyCheckRecord, DailyCheckTemplate,
                      EPOCH, User, Vehicle, alive, utcnow_naive)
from ..core.deps import require_perm
from ..workers.ai_tasks import ai_review_daily_check

log = logging.getLogger("clglxt.daily_check")
router = APIRouter(prefix="/daily-check", tags=["daily-check"])


# ─────────────── 模板 ───────────────

@router.get("/templates")
def list_templates(db: Session = Depends(get_db),
                   current_user: User = Depends(get_current_user)):
    rows = db.query(DailyCheckTemplate).order_by(DailyCheckTemplate.id).all()
    return [{"id": t.id, "name": t.name, "items": t.items,
             "is_default": t.is_default, "min_photos": t.min_photos,
             "max_photos": t.max_photos,
             "ai_review": t.ai_review, "ai_prompt": t.ai_prompt,
             "ai_fail_mode": t.ai_fail_mode} for t in rows]


@router.post("/templates")
def create_template(
    name: str, items: list[str], is_default: bool = False,
    min_photos: int = 0, max_photos: int = 3,
    ai_review: bool = False, ai_prompt: str = "",
    ai_fail_mode: str = "remind",
    db: Session = Depends(get_db),
    current_user: User = Depends(require_perm("daily_check:manage")),
):
    if db.query(DailyCheckTemplate).filter(DailyCheckTemplate.name == name).first():
        raise HTTPException(400, "模板名称已存在")
    t = DailyCheckTemplate(name=name, items=str(items), is_default=is_default,
                           min_photos=min_photos, max_photos=max_photos,
                           ai_review=ai_review, ai_prompt=ai_prompt,
                           ai_fail_mode=ai_fail_mode)
    db.add(t)
    db.commit()
    return {"id": t.id}


@router.put("/templates/{tid}")
def update_template(tid: int, name: str = "", items: list[str] | None = None,
                    min_photos: int | None = None, max_photos: int | None = None,
                    ai_review: bool | None = None, ai_prompt: str | None = None,
                    ai_fail_mode: str | None = None,
                    db: Session = Depends(get_db),
                    current_user: User = Depends(require_perm("daily_check:manage"))):
    t = db.get(DailyCheckTemplate, tid)
    if not t:
        raise HTTPException(404, "模板不存在")
    if name:
        t.name = name
    if items is not None:
        t.items = str(items)
    if min_photos is not None:
        t.min_photos = min_photos
    if max_photos is not None:
        t.max_photos = max_photos
    if ai_review is not None:
        t.ai_review = ai_review
    if ai_prompt is not None:
        t.ai_prompt = ai_prompt
    if ai_fail_mode:
        t.ai_fail_mode = ai_fail_mode
    db.commit()
    return {"ok": True}


@router.delete("/templates/{tid}")
def delete_template(tid: int, db: Session = Depends(get_db),
                    current_user: User = Depends(require_perm("daily_check:manage"))):
    t = db.get(DailyCheckTemplate, tid)
    if not t:
        raise HTTPException(404, "模板不存在")
    if t.is_default:
        raise HTTPException(400, "默认模板不可删除，请先取消默认标记")
    db.delete(t)
    db.commit()
    return {"ok": True}


# ─────────────── 日检记录 ───────────────

@router.get("/records")
def list_records(
    vehicle_id: int | None = None,
    keyword: str = "",
    date_from: str = "", date_to: str = "",
    has_abnormal: bool | None = None,
    limit: int = Query(20, le=100), offset: int = 0,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    q = db.query(DailyCheckRecord).filter(alive(DailyCheckRecord.deleted_at))

    # 可见性隔离：汽修厂只看服务范围；内部账号看本部门（除非 data:scope_all）
    if not current_user.is_superuser:
        from ..core.deps import _load_perms
        if current_user.supplier_id:
            from ..models import Supplier
            shop = db.get(Supplier, current_user.supplier_id)
            if shop and not shop.serves_all_depts:
                import json as _json
                dept_ids = _json.loads(shop.dept_ids or "[]")
                q = q.join(Vehicle, DailyCheckRecord.vehicle_id == Vehicle.id) \
                     .filter(Vehicle.department_id.in_(dept_ids or [-1]))
        elif "data:scope_all" not in _load_perms(db, current_user.id):
            q = q.join(Vehicle, DailyCheckRecord.vehicle_id == Vehicle.id) \
                 .filter(Vehicle.department_id == current_user.department_id)

    if vehicle_id:
        q = q.filter(DailyCheckRecord.vehicle_id == vehicle_id)
    d_from = parse_date(date_from, "date_from")
    d_to = parse_date(date_to, "date_to")
    if d_from:
        q = q.filter(DailyCheckRecord.check_date >= d_from)
    if d_to:
        q = q.filter(DailyCheckRecord.check_date <= d_to)
    if has_abnormal is not None:
        q = q.filter(DailyCheckRecord.has_abnormal == has_abnormal)

    total = q.count()
    rows = q.order_by(DailyCheckRecord.check_date.desc()).offset(offset).limit(limit).all()
    return {"total": total, "items": [{"id": r.id, "vehicle_id": r.vehicle_id,
                                       "user_id": r.user_id,
                                       "check_date": r.check_date.isoformat(),
                                       "results": r.results,
                                       "has_abnormal": r.has_abnormal,
                                       "photos": r.photos}
                                      for r in rows]}


@router.post("/records")
def submit_record(
    vehicle_id: int,
    template_id: int,
    check_date: str,
    results: list[dict],
    photo_ids: list[int] | None = None,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """提交日检记录（同车同日不可重复提报）。"""
    # 日期格式与范围校验：避免非法字符串直接进 Date 列导致 500
    cd = parse_date(check_date, "检查日期")
    if cd is None:
        raise HTTPException(400, "检查日期不能为空")
    if cd > date.today():
        raise HTTPException(400, "检查日期不能晚于今天")
    if (date.today() - cd).days > 31:
        raise HTTPException(400, "检查日期不能早于 31 天前")

    v = db.get(Vehicle, vehicle_id)
    if not v:
        raise HTTPException(404, "车辆不存在")
    if v.department_id and v.department_id != current_user.department_id \
        and not current_user.is_superuser:
        raise HTTPException(403, "无权为该车辆提交日检")

    # 模板 items 长度校验：results 必须与模板条目数量一致
    tpl = db.get(DailyCheckTemplate, template_id)
    if tpl:
        import json as _json_tpl
        try:
            tpl_items = _json_tpl.loads(tpl.items or "[]")
        except Exception:
            tpl_items = []
        if tpl_items and len(results) != len(tpl_items):
            raise HTTPException(400, f"检查项数量应与模板一致（{len(tpl_items)} 项）")

    # 唯一约束兜底：同车同日未删除记录
    old = (db.query(DailyCheckRecord)
           .filter(DailyCheckRecord.vehicle_id == vehicle_id,
                   DailyCheckRecord.check_date == cd,
                   alive(DailyCheckRecord.deleted_at))
           .first())
    if old:
        raise HTTPException(400, "该车辆当日已提交日检")

    has_abnormal = any(not it.get("ok", True) for it in results)

    import json as _json
    r = DailyCheckRecord(vehicle_id=vehicle_id, user_id=current_user.id,
                         check_date=cd,
                         results=_json.dumps(results, ensure_ascii=False),
                         has_abnormal=has_abnormal)
    db.add(r)
    from sqlalchemy.exc import IntegrityError
    try:
        db.flush()
    except IntegrityError:
        # 并发提交时唯一约束 uq_vehicle_date_alive 兜底
        db.rollback()
        raise HTTPException(400, "该车辆当日已提交日检")

    # 绑定已上传的照片
    for pid in (photo_ids or []):
        p = db.get(DailyCheckPhoto, pid)
        if p and p.record_id is None and p.user_id == current_user.id:
            p.record_id = r.id

    db.commit()

    # 触发 AI 审核（broker 不可用时降级为日志告警，不影响主流程）
    if r.has_abnormal and v:
        _notify_abnormal_check(db, r, v, current_user.id, check_date)
        template = db.get(DailyCheckTemplate, template_id)
        if template and template.ai_review:
            try:
                ai_review_daily_check.delay(r.id)
            except Exception:
                log.warning("AI 审核任务投递失败 record_id=%s（已降级）", r.id, exc_info=True)

    return {"id": r.id, "has_abnormal": has_abnormal}


def _notify_abnormal_check(db: Session, r: DailyCheckRecord, v: Vehicle, actor_id: int, check_date: str) -> None:
    """日检异常：通知车辆所属部门的管理员 / 超管。"""
    from ..core.notify import notify
    from ..models import User

    targets = []
    if v.department_id:
        targets = (db.query(User)
                   .filter(User.department_id == v.department_id,
                           User.is_active == 1, User.id != actor_id).all())
    if not targets:
        targets = (db.query(User)
                   .filter(User.is_superuser == 1, User.id != actor_id).all())
    for u in targets:
        notify(db, u.id, f"日检异常 {v.plate_no}",
               f"{check_date} 提交日检存在异常项，请复核 record_id={r.id}",
               kind="daily_check", target_type="daily_check_record", target_id=r.id,
               actor_id=actor_id)
    # notify() 不再内部 commit（保持事务边界），此处为独立后置流程，需显式提交
    if targets:
        db.commit()


@router.delete("/records/{rid}")
def delete_record(rid: int, reason: str = "",
                  db: Session = Depends(get_db),
                  current_user: User = Depends(require_perm("daily_check:manage"))):
    """软删日检记录（CAS 并发控制）。"""
    updated = (db.query(DailyCheckRecord)
               .filter(DailyCheckRecord.id == rid,
                       alive(DailyCheckRecord.deleted_at))
               .update({"deleted_at": utcnow_naive()}, synchronize_session=False))
    if not updated:
        raise HTTPException(400, "该日检记录已删除")
    db.commit()
    return {"ok": True}
