#!/usr/bin/env python3
"""照片上传/压缩 API 集成测试（走 SQLite，需装本轮子依赖 Pillow + python-multipart）。"""
import io
import os
import random
import shutil
import sys
import tempfile
import uuid
import warnings
import atexit
from pathlib import Path

warnings.filterwarnings("ignore")

# ── 临时数据目录 ──
tmp_data = Path(tempfile.mkdtemp(prefix="clglxt_photo_"))
atexit.register(shutil.rmtree, tmp_data, True)  # 异常退出也回收
DB = str(tmp_data / "photo_test.db")
os.environ["CLGLXT_DATA_DIR"] = str(tmp_data)
os.environ["CLGLXT_DATABASE_URL"] = f"sqlite:///{DB}"
os.environ["CLGLXT_JWT_SECRET"] = "test-secret-for-photo-pipeline"
os.environ["CLGLXT_MAX_UPLOAD_MB"] = "50"  # 24MP 噪声图约 27MB，放行以便测压缩

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine as _ce0  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402


def _ce(url, **kw):
    """SQLite 需 pop 掉 PG 专有的 connect_args / pool_pre_ping。"""
    if str(url).startswith("sqlite"):
        kw.pop("connect_args", None)
        kw.pop("pool_pre_ping", None)
    return _ce0(url, **kw)


import app.installer as inst  # noqa: E402

inst._pg_url = lambda *a, **k: f"sqlite:///{DB}"
inst.create_engine = _ce
# 官方注入入口：一次 configure 同步所有路径（含 ENV_FILE 及三个派生安装标记）。
# 不能只靠 CLGLXT_DATA_DIR 环境变量——ENV_FILE 由 BASE_DIR 推导，必须显式注入。
inst.configure(data_dir=tmp_data, env_file=tmp_data / "install.env")

from fastapi.testclient import TestClient  # noqa: E402
from PIL import Image  # noqa: E402

import app.core.database as dbmod  # noqa: E402
from app.main import app  # noqa: E402

# 重新绑定引擎到 SQLite
engine = _ce0(f"sqlite:///{DB}")
dbmod.engine = engine
dbmod.SessionLocal = _sm(bind=engine, autoflush=False, expire_on_commit=False)
import importlib  # noqa: E402
import app.main as main_mod  # noqa: E402
importlib.reload(main_mod)
c = TestClient(main_mod.app)

ok = 0
fail = 0


def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  ✓ {name}")
    else:
        fail += 1
        print(f"  ✗ {name} {extra}")


# ── 生成测试图 ──
def _rnd_jpeg(w, h, q=95):
    img = Image.frombytes("RGB", (w, h),
                          bytes(random.getrandbits(8) for _ in range(w * h * 3)))
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=q)
    return buf.getvalue()


JPEG_BIG = _rnd_jpeg(4000, 3000, q=95)      # 12MP
JPEG_SMALL = _rnd_jpeg(1920, 1080, q=80)   # 2MP
JPEG_LARGE = _rnd_jpeg(6000, 4000, q=95)   # 24MP

print(f"\n测试图片大小: BIG {len(JPEG_BIG) // 1024}KB  "
      f"SMALL {len(JPEG_SMALL) // 1024}KB  LARGE {len(JPEG_LARGE) // 1024}KB")

print("\n=== 1. 安装 ===")
r = c.post("/api/install/save", json={
    "admin_user": "admin", "admin_pass": "MyPass123", "admin_name": "系统管理员",
    "site_title": "测试站", "dept_name": "维修车间",
    "db_host": "h", "db_port": 5432, "db_name": "clglxt",
    "db_user": "u", "db_pass": "p", "redis_url": "redis://127.0.0.1:6379/0",
})
check("install 200", r.status_code == 200, r.text[:120])
check("installed 标记", c.get("/api/install/status").json()["installed"] is True)

print("\n=== 2. 登录 ===")
l = c.post("/api/auth/login?username=admin&password=MyPass123")
check("login 200", l.status_code == 200, l.text[:80])
token = l.json()["token"]
H = {"Authorization": f"Bearer {token}"}

print("\n=== 3. 上传 12MP 大图 ===")
files = [("files", ("big.jpg", JPEG_BIG, "image/jpeg"))]
r = c.post("/api/daily-check/photos", files=files, headers=H)
check("upload 201", r.status_code == 201, r.text[:120])
items = r.json()["items"]
pid = items[0]["id"]
out_sz = items[0]["size"]
print(f"    {items[0]['width']}x{items[0]['height']} "
      f"{out_sz // 1024}KB (原 {len(JPEG_BIG) // 1024}KB, "
      f"{100 * out_sz // len(JPEG_BIG)}%)")
check("尺寸缩减", out_sz < len(JPEG_BIG))

print("\n=== 4. 上传 2MP 小图（不应放大）===")
files = [("files", ("small.jpg", JPEG_SMALL, "image/jpeg"))]
r = c.post("/api/daily-check/photos", files=files, headers=H)
check("upload 201", r.status_code == 201, r.text[:120])
items2 = r.json()["items"]
print(f"    {items2[0]['width']}x{items2[0]['height']} {items2[0]['size'] // 1024}KB")
check("小图不放大", items2[0]["width"] <= 1920)

print("\n=== 5. 上传 24MP 超大图 ===")
files = [("files", ("huge.jpg", JPEG_LARGE, "image/jpeg"))]
r = c.post("/api/daily-check/photos", files=files, headers=H)
check("upload 201", r.status_code == 201, r.text[:120])
if r.status_code == 201:
    h0 = r.json()["items"][0]
    print(f"    {h0['width']}x{h0['height']} {h0['size'] // 1024}KB "
          f"(原 {len(JPEG_LARGE) // 1024}KB)")
    check("24MP 也压缩", h0["width"] <= 1920)

print("\n=== 6. 读取原图 ===")
r = c.get(f"/api/daily-check/photos/{pid}", headers=H)
check("GET 原图 200", r.status_code == 200)
check("大小匹配", len(r.content) == out_sz)

print("\n=== 7. 读取缩略图 ===")
r = c.get(f"/api/daily-check/photos/{pid}?size=thumb", headers=H)
check("GET thumb 200", r.status_code == 200)
check("thumb 是 JPEG", r.content[:2] == b"\xff\xd8")
check("thumb < 原图", len(r.content) < out_sz)

print("\n=== 8. 无 token 访问 ===")
r = c.get(f"/api/daily-check/photos/{pid}")
check("401", r.status_code == 401, str(r.status_code))

print("\n=== 9. 非媒体 token ===")
check("403", r.status_code in (401, 403))

print("\n=== 10. 非法 MIME 拒绝 ===")
files = [("files", ("hack.exe", b"MZ\x90\x00", "application/x-msdownload"))]
r = c.post("/api/daily-check/photos", files=files, headers=H)
check("400 reject exe", r.status_code == 400, str(r.status_code))

print("\n=== 11. RAW body 上传 ===")
r = c.post("/api/daily-check/photos/raw",
           content=JPEG_BIG,
           headers={**H, "content-type": "image/jpeg"})
check("raw 201", r.status_code == 201, r.text[:100])

print("\n=== 12. 删除 ===")
new_pid = r.json()["id"]
r = c.delete(f"/api/daily-check/photos/{new_pid}", headers=H)
check("delete 204", r.status_code == 204, str(r.status_code))
r = c.get(f"/api/daily-check/photos/{new_pid}", headers=H)
check("404 after delete", r.status_code == 404, str(r.status_code))

print("\n=== 13. 多文件批量 ===")
batch = [("files", ("a.jpg", JPEG_SMALL, "image/jpeg")),
         ("files", ("b.jpg", JPEG_SMALL, "image/jpeg"))]
r = c.post("/api/daily-check/photos", files=batch, headers=H)
check("batch 201", r.status_code == 201)
check("2 items", len(r.json()["items"]) == 2)

print(f"\n=== 结果: {ok} 通过 / {fail} 失败 ===")
sys.exit(1 if fail else 0)
