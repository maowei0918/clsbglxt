#!/usr/bin/env python3
"""批次1 安装向导安全专项测试。

验证：.env 600 / docs off / DB 异常脱敏 / 限频 / 原子锁 / 已安装关闭 /
失败清理（不残留 running 锁、不残留 .env）。
"""
import os
import shutil
import sys
import tempfile
import uuid
import atexit
from pathlib import Path

# 用独立临时目录，避免污染 server/data
TMP = Path(tempfile.mkdtemp(prefix="clglxt_install_sec_"))
os.environ["CLGLXT_DATA_DIR"] = str(TMP)
atexit.register(shutil.rmtree, TMP, True)  # 异常退出也回收
DB = str(TMP / "sec.db")
ENV = str(TMP / ".env")

# 按脚本自身位置解析，避免硬编码绝对路径（便于复刻部署）
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import create_engine as _ce0  # noqa: E402


def _ce(url, **kw):
    if str(url).startswith("sqlite"):
        kw.pop("connect_args", None)
        kw.pop("pool_pre_ping", None)
    return _ce0(url, **kw)


import app.installer as inst  # noqa: E402

# 官方注入入口：一次调用同步所有路径（含三个派生安装标记），
# 避免逐个重绑 inst.XXX 时漏掉新增路径。
inst.configure(data_dir=TMP, env_file=Path(ENV))
inst._pg_url = lambda *a, **k: f"sqlite:///{DB}"
inst.create_engine = _ce

from fastapi.testclient import TestClient  # noqa: E402
import app.core.database as dbmod  # noqa: E402
from sqlalchemy.orm import sessionmaker as _sm  # noqa: E402
import importlib  # noqa: E402
import app.main as M  # noqa: E402

_e = _ce0(f"sqlite:///{DB}")
dbmod.engine = _e
dbmod.SessionLocal = _sm(bind=_e, autoflush=False, expire_on_commit=False)
importlib.reload(M)
c = TestClient(M.app)

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


BASE = {
    "admin_user": "admin", "admin_pass": "MyPass123", "admin_name": "运维",
    "site_title": "青岛车队", "db_host": "h", "db_port": "5432",
    "db_name": "clglxt", "db_user": "u", "db_pass": "p",
    "dept_name": "车队一部", "redis_url": "redis://127.0.0.1:6379/0",
}

print("\n=== A. DB 异常脱敏（伪造连接失败）===")
_orig_ce = inst.create_engine


def _bad_ce(url, **kw):
    raise RuntimeError("connection to server at '10.0.0.5', port 5432 failed: FATAL: password authentication failed for user 'secretuser'")


inst.create_engine = _bad_ce
r = c.post("/api/install/save", json=BASE)
check("save 400", r.status_code == 400, r.text[:120])
body = r.text
check("不泄露主机/端口/用户", "10.0.0.5" not in body and "secretuser" not in body and "5432" not in body, body[:120])
check("失败不留 running 锁", not inst.INSTALL_RUNNING.exists())
check("失败不留 .env", not Path(ENV).exists())
inst.create_engine = _ce

print("\n=== B. 非法端口不 500 ===")
bad = dict(BASE); bad["db_port"] = "abc"
r = c.post("/api/install/save", json=bad)
check("非法端口归一（200）", r.status_code == 200, r.text[:120])
check(".env 权限 600", oct(Path(ENV).stat().st_mode & 0o777) == "0o600", oct(Path(ENV).stat().st_mode & 0o777))
envtxt = Path(ENV).read_text()
check("ENABLE_DOCS=false", "CLGLXT_ENABLE_DOCS=false" in envtxt)
check("JWT_SECRET 非空", "CLGLXT_JWT_SECRET=" in envtxt and len(envtxt.split("CLGLXT_JWT_SECRET=")[1].split("\n")[0]) >= 64)

print("\n=== C. 已安装后接口关闭 ===")
r2 = c.post("/api/install/save", json=BASE)
check("重复安装 403", r2.status_code == 403, r2.text[:80])
rt = c.post("/api/install/test", json={"db_host": "h", "db_port": 5432, "db_name": "x", "db_user": "u", "db_pass": "p"})
check("test 已装关闭 403", rt.status_code == 403, rt.text[:80])
st = c.get("/api/install/status").json()
check("status installed=True", st.get("installed") is True)

print("\n=== D. 限频（新目录重测）===")
TMP2 = Path(tempfile.mkdtemp(prefix="clglxt_install_rl_"))
atexit.register(shutil.rmtree, TMP2, True)  # 异常退出也回收
inst.configure(data_dir=TMP2, env_file=TMP2 / ".env")
inst._pg_url = lambda *a, **k: "sqlite:///__bad_db_path__/" + uuid.uuid4().hex + ".db"
# 让连接必定失败（目录不存在）
codes = []
for i in range(inst._INSTALL_RL_LIMIT + 3):
    rr = c.post("/api/install/test", json={"db_host": "h", "db_port": 5432, "db_name": "x", "db_user": "u", "db_pass": "p"})
    codes.append(rr.status_code)
check("达到阈值后 429", 429 in codes, str(codes[-5:]))
check("前若干次非 429", all(x != 429 for x in codes[:inst._INSTALL_RL_LIMIT]), str(codes[:3]))

print(f"\n=== 结果: {ok} 通过 / {fail} 失败 ===")
shutil.rmtree(TMP, ignore_errors=True)
shutil.rmtree(TMP2, ignore_errors=True)
sys.exit(1 if fail else 0)
