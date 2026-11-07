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
from pathlib import Path

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

# 解码前像素上限：1.5 亿像素（≈ 450MB RGB 原始数据）。
# 常规手机照片（12MP=1200 万）远在此之下；超过的都是构造的
# 解压炸弹。旧值 6 亿像素约需 1.7GB 内存，单请求可打爆 worker。
MAX_DECODE_PIXELS = 150_000_000


def resolve_upload_path(rel: str) -> Path:
    """把库中存的相对路径解析成绝对路径，拒绝越界。

    `UPLOAD_DIR / "../../etc/passwd"` 这种拼接不会自动阻止路径穿越，
    一旦某条代码路径接受了客户端可控的 saved_path，就是任意文件读。
    这里统一做解析 + 前缀校验，把风险收敛在一个地方。
    """
    from .config import UPLOAD_DIR
    base = Path(UPLOAD_DIR).resolve()
    rp = (base / str(rel)).resolve(strict=False)
    if rp != base and base not in rp.parents:
        raise ValueError("非法的存储路径")
    return rp


# ─────────────── 磁盘余量保护 ───────────────

def ensure_free_space(path, need_bytes: int, min_free_mb: int | None = None) -> None:
    """写入前检查目标分区剩余空间，不足则抛 ValueError。

    磁盘写满的后果不是「上传失败」这么简单：PostgreSQL 的 WAL 与
    pg_dump 都写不进去，数据库会直接进入不可用状态，需要人工介入。
    旧实现完全没有这层保护（MIN_FREE_MB 是死配置）。
    """
    from .config import MIN_FREE_MB
    need_mb = (need_bytes // (1024 * 1024)) + 1
    free_mb = min_free_mb if min_free_mb is not None else MIN_FREE_MB
    try:
        st = _os.statvfs(str(path))
        avail_mb = (st.f_bavail * st.f_frsize) // (1024 * 1024)
    except (AttributeError, OSError):
        # 非 POSIX 文件系统（如 Windows 开发机）跳过检查，不阻断上传
        return
    if avail_mb < need_mb + free_mb:
        raise ValueError(
            f"磁盘空间不足：需要 {need_mb}MB，保留 {free_mb}MB，"
            f"当前可用 {avail_mb}MB")

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


def _pixel_guard(im, max_pixels: int = MAX_DECODE_PIXELS) -> None:
    """解码前用文件头声明的尺寸做像素预检，再兜底 Pillow 自身限额。

    DecompressionBombWarning 只在解码时才可能触发；而 600MP 的 RGB
    解码要 ~1.7GB 内存，单请求就能把 worker 打爆。这里在 decode 之前
    用 Image.open() 读到的 size（仅解析头部，不解压像素）先拦。
    """
    w, h = im.size
    if w * h > max_pixels:
        raise ValueError(
            f"图片像素过多（{w}x{h}={w*h}），上限 {max_pixels}")


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
    from PIL import Image

    if isinstance(src, (bytes, bytearray)):
        fp = io.BytesIO(src)
    else:
        fp = str(src)

    # Pillow 自身限额作兜底（超过 2 倍时抛 DecompressionBombError）
    Image.MAX_IMAGE_PIXELS = MAX_DECODE_PIXELS

    try:
        with Image.open(fp) as im:
            # 解码前预检：头部声明的像素数超限直接拒绝，不进入 resize
            _pixel_guard(im)
            return _encode(im, max_dim, quality, out_format, src)
    except Image.DecompressionBombError as e:
        # Pillow 的炸弹异常不是 ValueError，调用方只捕获 ValueError，
        # 不转换会让超限图片变成 500 而不是 400。
        raise ValueError(str(e)) from e


def _encode(im, max_dim, quality, out_format, src):
    from PIL import Image, ImageOps

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
