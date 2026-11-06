"""文件校验与压缩辅助：图片 MIME/扩展名校验、JPEG 压缩、缩略图生成。

线程池并发说明：
  压缩是 CPU 密集操作。FastAPI 的 async 端点里直接调用会阻塞事件循环，
  并发 100+ 上传时其他请求全被卡死。统一走 run_in_executor + 信号量限流。
"""
import asyncio
import io
import logging
import os as _os
from functools import partial

log = logging.getLogger("clglxt.image")

# 压缩并发上限：避免 100 个上传各起一线程把 CPU/内存打爆
_MAX_WORKERS = min(8, max(2, (_os.cpu_count() or 4)))
_SEM = asyncio.Semaphore(_MAX_WORKERS)

_ALLOWED_MIME = {
    "image/jpeg", "image/jpg", "image/png", "image/webp", "image/heic",
    "image/heif", "image/gif", "image/bmp", "image/tiff",
}
_ALLOWED_EXT = {
    ".jpg", ".jpeg", ".png", ".webp", ".heic", ".heif", ".gif", ".bmp",
    ".tif", ".tiff",
}

# 输出格式映射
_FMT_SUFFIX = {"JPEG": "image/jpeg", "PNG": "image/png", "WEBP": "image/webp"}


def is_allowed_image(mime: str, filename: str = "") -> bool:
    """校验 MIME 和扩展名（二选一通过即放行，但两者都明显不对则拒）。"""
    mime = (mime or "").lower().split(";")[0].strip()
    ext_ok = False
    if filename and "." in filename:
        ext_ok = ("." + filename.rsplit(".", 1)[-1].lower()) in _ALLOWED_EXT
    if mime in _ALLOWED_MIME:
        return True
    if mime in ("", "application/octet-stream"):
        return ext_ok
    return False


# ─────────────── 压缩 ───────────────

def compress_image(
    src,
    max_dim: int = 1920,
    quality: int = 78,
    out_format: str = "JPEG",
) -> tuple[bytes, str, int, int]:
    """压缩图片。

    src: bytes 或 Path/str（磁盘文件）
    返回: (bytes, mime, width, height)

    规则：
      * 长边超过 max_dim 时等比缩小
      * 缩小后按 out_format 重编码（默认 JPEG，quality 可调）
      * 小图不放大（仅重编码 + 剥离 EXIF，避免元数据泄露手机型号/定位）
      * 若压缩后反而更大（已是极限压缩图），回退原字节
    """
    from PIL import Image, ImageOps

    if isinstance(src, (bytes, bytearray)):
        fp = io.BytesIO(src)
    else:
        fp = str(src)

    Image.MAX_IMAGE_PIXELS = 600_000_000  # 防御解压炸弹

    with Image.open(fp) as im:
        im = ImageOps.exif_transpose(im)  # 按 EXIF 方向校正
        w0, h0 = im.size

        scale = min(1.0, max_dim / max(w0, h0)) if max(w0, h0) > 0 else 1.0
        new_w = max(1, int(round(w0 * scale)))
        new_h = max(1, int(round(h0 * scale)))
        if (new_w, new_h) != (w0, h0):
            im = im.resize((new_w, new_h), Image.LANCZOS)

        if out_format == "JPEG":
            if im.mode in ("RGBA", "LA", "P"):
                bg = Image.new("RGB", im.size, (255, 255, 255))
                if im.mode == "P":
                    im = im.convert("RGBA")
                if im.mode in ("RGBA", "LA"):
                    bg.paste(im, mask=im.split()[-1])
                im = bg
            elif im.mode not in ("RGB", "L"):
                im = im.convert("RGB")

        out = io.BytesIO()
        if out_format == "JPEG":
            im.save(out, format=out_format, quality=quality,
                    optimize=True, progressive=True)
        elif out_format == "WEBP":
            im.save(out, format=out_format, quality=quality, method=4)
        else:
            im.save(out, format=out_format)

    blob = out.getvalue()
    mime = _FMT_SUFFIX.get(out_format, "image/jpeg")

    # 压缩后反而更大时回退原字节（极少，例如原始就是低分辨率高质量图）
    if isinstance(src, (bytes, bytearray)) and len(blob) >= len(src) * 1.02:
        try:
            with Image.open(io.BytesIO(src)) as im2:
                fmt = (im2.format or "").lower()
                m = f"image/{fmt}" if fmt else mime
                return bytes(src), m, im2.size[0], im2.size[1]
        except Exception:
            pass

    return blob, mime, new_w, new_h


# ─────────────── 异步包装（并发 100+ 关键） ───────────────

async def compress_image_async(src, **kwargs) -> tuple[bytes, str, int, int]:
    """在线程池里跑压缩，信号量限制同时压缩数。"""
    loop = asyncio.get_running_loop()
    async with _SEM:
        return await loop.run_in_executor(
            None, partial(compress_image, src, **kwargs))


async def make_thumb_async(src, max_dim: int = 400) -> tuple[bytes, str, int, int]:
    """异步生成缩略图（quality=65，列表预览用）。"""
    return await compress_image_async(src, max_dim=max_dim, quality=65)
