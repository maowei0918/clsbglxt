"""日检照片上传与服务端压缩。

POST /api/daily-check/photos
  Content-Type: multipart/form-data
  字段: files (多文件，每张 ≤20 MB 原图，支持 jpg/png/webp/heic)
  返回: {items:[{id,url,thumb_url,width,height,size}], errors:[...]}

POST /api/daily-check/photos/raw
  原生 body 直传（APP 端 APP 已压缩的 JPEG）
  返回: {id,url,thumb_url,width,height,size}

GET /api/daily-check/photos/{photo_id}
  需 Bearer；返回压缩后字节流；?size=thumb 返回 400px 小图。

DELETE /api/daily-check/photos/{photo_id}
  撤销上传（仅本人、未关联 record 的照片可撤）。
"""
from __future__ import annotations

import io
import logging
import os
import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import (APIRouter, Depends, File, HTTPException, Query, Request,
                     Response, UploadFile, status)
from fastapi.responses import StreamingResponse
from sqlalchemy.orm import Session

from ..core.config import UPLOAD_DIR
from ..core.database import get_db
from ..core.deps import DeptChecker, get_current_user
from ..core.image_utils import (compress_image, compress_image_async,
                                is_allowed_image, make_thumb_async)
from ..models import DailyCheckPhoto, DailyCheckRecord, User, Vehicle

log = logging.getLogger("clglxt.daily_check.photos")
router = APIRouter(prefix="/daily-check/photos", tags=["daily-check-photos"])

# 子目录（按年月归档，避免单目录爆炸）
_PHOTO_DIR = UPLOAD_DIR / "daily_check"
_PHOTO_DIR.mkdir(parents=True, exist_ok=True)

# 上传上限（可被环境变量覆盖）
_env_max_upload = int(os.getenv("CLGLXT_MAX_UPLOAD_MB", "20"))
_MAX_UPLOAD_BYTES = _env_max_upload * 1024 * 1024
_THUMB_DIM = 400
_DEFAULT_DIM = int(os.getenv("CLGLXT_IMAGE_MAX_DIM", "1920"))
_DEFAULT_QUALITY = int(os.getenv("CLGLXT_IMAGE_QUALITY", "78"))


def _store_blob(blob: bytes, mime: str) -> Path:
    """按 YYYY/MM 归档写入 UPLOAD_DIR，返回相对路径字符串。"""
    now = datetime.now(timezone.utc)
    sub = _PHOTO_DIR / f"{now.year}" / f"{now.month:02d}"
    sub.mkdir(parents=True, exist_ok=True)
    suffixes = {"image/jpeg": ".jpg", "image/png": ".png",
                "image/webp": ".webp"}
    suffix = suffixes.get(mime, ".jpg")
    fn = f"{uuid.uuid4().hex}{suffix}"
    p = sub / fn
    p.write_bytes(blob)
    return p.relative_to(UPLOAD_DIR)


def _photo_url(photo: DailyCheckPhoto, thumb: bool = False) -> str:
    suffix = "?size=thumb" if thumb else ""
    return f"/api/daily-check/photos/{photo.id}{suffix}"


def _load_photo_or_404(db: Session, photo_id: int) -> DailyCheckPhoto:
    p = db.get(DailyCheckPhoto, photo_id)
    if not p:
        raise HTTPException(404, "照片不存在")
    return p


# ─────────────── 上传 ───────────────

@router.post("", status_code=201)
async def upload_photos(
    request: Request,
    files: list[UploadFile] | None = File(None),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """多文件 multipart（前端 Element Plus 上传组件直传）。"""
    if not files or all(f.filename == "" for f in files if f):
        raise HTTPException(400, "未检测到上传文件")

    results, errors = [], []
    for f in files:
        if not f.filename:
            continue
        try:
            raw = await f.read()
            if not raw:
                errors.append({"filename": f.filename, "error": "空文件"})
                continue
            if len(raw) > _MAX_UPLOAD_BYTES:
                errors.append({"filename": f.filename,
                               "error": f"单张不能超过 {_env_max_upload} MB"})
                continue

            mime = (f.content_type or "").lower()
            if not is_allowed_image(mime, f.filename):
                errors.append({"filename": f.filename, "error": "不支持的图片格式"})
                continue

            blob, out_mime, w, h = await compress_image_async(
                raw, max_dim=_DEFAULT_DIM, quality=_DEFAULT_QUALITY)
            rel_path = _store_blob(blob, out_mime)

            photo = DailyCheckPhoto(
                user_id=current_user.id,
                saved_path=str(rel_path),
                orig_name=f.filename,
                mime=out_mime,
                size=len(blob),
            )
            db.add(photo)
            db.flush()
            results.append({
                "id": photo.id,
                "url": _photo_url(photo),
                "thumb_url": _photo_url(photo, thumb=True),
                "width": w, "height": h, "size": len(blob),
            })
        except HTTPException:
            raise
        except Exception as e:
            log.exception("上传失败: %s", f.filename)
            errors.append({"filename": f.filename, "error": str(e)})
    db.commit()
    # 全部失败 → 400（单张上传时语义清晰）；部分成功 → 201 + errors
    if not results:
        detail = errors[0]["error"] if len(errors) == 1 else "全部文件上传失败"
        raise HTTPException(400, detail)
    return {"items": results, "errors": errors}


@router.post("/raw", status_code=201)
async def upload_photo_raw(
    request: Request,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """原生 body 上传（APP 端 APP 原生压缩后的 JPEG 直接 POST）。"""
    body = await request.body()
    if not body:
        raise HTTPException(400, "body 为空")
    if len(body) > _MAX_UPLOAD_BYTES:
        raise HTTPException(413, f"body 超过 {_env_max_upload} MB")

    content_type = request.headers.get("content-type", "image/jpeg").lower()
    if not is_allowed_image(content_type):
        raise HTTPException(415, "不支持的图片格式")

    orig_name = request.headers.get("x-filename", "photo.jpg")

    try:
        blob, out_mime, w, h = await compress_image_async(
            body, max_dim=_DEFAULT_DIM, quality=_DEFAULT_QUALITY)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e

    rel_path = _store_blob(blob, out_mime)
    photo = DailyCheckPhoto(
        user_id=current_user.id,
        saved_path=str(rel_path),
        orig_name=orig_name,
        mime=out_mime,
        size=len(blob),
    )
    db.add(photo)
    db.commit()
    return {
        "id": photo.id,
        "url": _photo_url(photo),
        "thumb_url": _photo_url(photo, thumb=True),
        "width": w, "height": h, "size": len(blob),
    }


# ─────────────── 读取 ───────────────

@router.get("/{photo_id}")
async def get_photo(
    photo_id: int,
    size: str = Query(""),
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """返回图片字节流。?size=thumb 返回 400px 缩略图（实时生成）。

    归属校验（防 IDOR）：
      * 未关联日检记录的照片：仅上传者本人或超管可读；
      * 已关联记录的照片：需对记录的车辆有部门可见权。
    """
    photo = _load_photo_or_404(db, photo_id)

    if current_user.is_superuser:
        pass
    elif photo.user_id == current_user.id:
        pass
    else:
        # 已关联记录：按记录车辆的部门隔离；未关联：一律拒绝
        if photo.record_id is None:
            raise HTTPException(403, "无权访问该照片")
        rec = db.get(DailyCheckRecord, photo.record_id)
        if not rec:
            raise HTTPException(403, "无权访问该照片")
        veh = db.get(Vehicle, rec.vehicle_id) if rec.vehicle_id else None
        if not veh or veh.department_id is None:
            raise HTTPException(403, "无权访问该照片")
        DeptChecker(current_user, db).check_dept(
            veh.department_id, "照片")

    disk_path = UPLOAD_DIR / photo.saved_path
    if not disk_path.exists():
        raise HTTPException(404, "照片文件丢失")

    if size == "thumb":
        try:
            blob, mime, _, _ = await make_thumb_async(disk_path, _THUMB_DIM)
        except Exception:
            blob = disk_path.read_bytes()
            mime = photo.mime
    else:
        blob = disk_path.read_bytes()
        mime = photo.mime

    resp = Response(blob, media_type=mime)
    resp.headers["Cache-Control"] = "private, max-age=86400"
    return resp


@router.delete("/{photo_id}", status_code=204)
def delete_photo(
    photo_id: int,
    db: Session = Depends(get_db),
    current_user: User = Depends(get_current_user),
):
    """撤销上传（仅本人、未关联 record 的照片可撤）。"""
    photo = _load_photo_or_404(db, photo_id)
    if photo.user_id != current_user.id and not current_user.is_superuser:
        raise HTTPException(403, "无权删除")
    if photo.record_id is not None:
        raise HTTPException(400, "该照片已关联日检记录，无法删除")

    disk_path = UPLOAD_DIR / photo.saved_path
    try:
        if disk_path.exists():
            disk_path.unlink()
            for p in [disk_path.parent, disk_path.parent.parent]:
                if p != _PHOTO_DIR and p.exists() and not any(p.iterdir()):
                    p.rmdir()
    except Exception:
        log.exception("删除照片文件失败: %s", disk_path)

    db.delete(photo)
    db.commit()
    return Response(status_code=204)
