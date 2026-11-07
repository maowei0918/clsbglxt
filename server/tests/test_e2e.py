"""
clglxt 全链路集成测试
模拟：安装 → 登录 → 建部门/类型/汽修厂/车辆 → 提报维修单 → 抢单 → 报价 → 推进 → 验收 → 改密
"""
import sys, os, uuid, tempfile, shutil, atexit
from pathlib import Path
import warnings; warnings.filterwarnings("ignore")

# 路径按脚本自身位置解析，不硬编码绝对路径（复刻到其它机器/目录才不会失效）
_SERVER_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(_SERVER_DIR))

# ── 隔离：必须在 import app.* 之前设置 ──
# CLGLXT_DATA_DIR 决定上传/备份/安装标记（.install_done、.install.running）的落点；
# 不设置时回落到 server/data，会污染真实工作区。
_TMP = Path(tempfile.mkdtemp(prefix="clglxt_e2e_"))
os.environ["CLGLXT_DATA_DIR"] = str(_TMP)
# 用 atexit 兜底回收：脚本中途异常时不会走到末尾的清理代码，否则 /tmp 会堆积
atexit.register(shutil.rmtree, _TMP, True)
# 自带 JWT 密钥，避免依赖 .env；也让 lifespan 的密钥校验通过。
os.environ["CLGLXT_JWT_SECRET"] = "e2e-test-secret-" + "0" * 48
DB = f"/tmp/e2e_{uuid.uuid4().hex[:8]}.db"
os.environ["CLGLXT_DATABASE_URL"] = f"sqlite:///{DB}"

from sqlalchemy import create_engine as _ce0
from sqlalchemy.orm import sessionmaker as _sm


def _ce(url, **kw):
    if str(url).startswith("sqlite"):
        kw.pop("connect_args", None); kw.pop("pool_pre_ping", None)
    return _ce0(url, **kw)


import app.installer as inst
inst._pg_url = lambda *a, **k: f"sqlite:///{DB}"
inst.create_engine = _ce
# 官方注入入口：一次 configure 同步所有路径（含 ENV_FILE 及三个派生安装标记）。
# 不能只靠 CLGLXT_DATA_DIR 环境变量——ENV_FILE 由 BASE_DIR 推导，必须显式注入。
inst.configure(data_dir=_TMP, env_file=_TMP / "install.env")

from fastapi.testclient import TestClient
from app.main import app
import app.core.database as dbmod
_e = _ce0(f"sqlite:///{DB}")
dbmod.engine = _e
dbmod.SessionLocal = _sm(bind=_e, autoflush=False, expire_on_commit=False)
import importlib, app.main as M
importlib.reload(M)
c = TestClient(M.app)

ok = 0; fail = 0
def check(name, cond, extra=""):
    global ok, fail
    if cond:
        ok += 1; print(f"  ✓ {name}")
    else:
        fail += 1; print(f"  ✗ {name} {extra}")

print("\n=== 1. 安装 ===")
r = c.post("/api/install/save", json={
    "admin_user":"admin","admin_pass":"MyPass123","admin_name":"运维","site_title":"青岛车队",
    "db_host":"h","db_port":5432,"db_name":"clglxt","db_user":"u","db_pass":"p",
    "dept_name":"车队一部","redis_url":"redis://127.0.0.1:6379/0"})
check("save 200", r.status_code == 200, r.text[:80])
check("installed 标记", c.get("/api/install/status").json()["installed"] is True)

print("\n=== 2. 登录 + 改密 ===")
l = c.post("/api/auth/login", json={"username": "admin", "password": "MyPass123"})
check("login 200", l.status_code == 200)
check("must_change=True", l.json().get("user",{}).get("must_change_password") is True)
token = l.json()["token"]
H = {"Authorization": f"Bearer {token}"}

cp = c.post("/api/auth/change-password", json={"old_password": "MyPass123", "new_password": "NewPass456"}, headers=H)
check("change-password 200", cp.status_code == 200, cp.text[:80])
# 旧密码失效
check("旧密码被拒", c.post("/api/auth/login", json={"username": "admin", "password": "MyPass123"}).status_code == 401)
# 新密码可用
l2 = c.post("/api/auth/login", json={"username": "admin", "password": "NewPass456"})
check("新密码登录 200", l2.status_code == 200)
token2 = l2.json()["token"]
H2 = {"Authorization": f"Bearer {token2}"}
check("改密后 must_change=False", l2.json()["user"]["must_change_password"] is False)

print("\n=== 3. 建基础数据 ===")
me = c.get("/api/auth/me", headers=H2).json()
check("me 200", me.get("username") == "admin")

d = c.post("/api/departments", params={"name":"车队二部"}, headers=H2)
check("部门创建", d.status_code == 200, d.text[:80])
dept_id = d.json()["id"] if d.status_code == 200 else None

t = c.post("/api/vehicles/types", params={"name":"货车"}, headers=H2)
check("车辆类型创建", t.status_code == 200, t.text[:80])
type_id = t.json()["id"] if t.status_code == 200 else None

s = c.post("/api/suppliers", params={"name":"青岛汽修厂","contact":"13800000000"}, headers=H2)
check("汽修厂创建", s.status_code == 200, s.text[:80])
sup_id = s.json()["id"] if s.status_code == 200 else None

print("\n=== 4. 车辆档案 ===")
v = c.post("/api/vehicles", params={
    "plate_no":"鲁B12345","vehicle_type_id":type_id,"department_id":dept_id,
    "nature":"货运","brand":"解放"}, headers=H2)
check("车辆创建", v.status_code == 200, v.text[:80])
vid = v.json()["id"] if v.status_code == 200 else None

vl = c.get("/api/vehicles", headers=H2)
check("车辆列表非空", isinstance(vl.json(), list) and len(vl.json()) >= 1)

ta = c.get("/api/vehicles/types/all", headers=H2)
check("类型列表非空", isinstance(ta.json(), list) and len(ta.json()) >= 1)

print("\n=== 5. 维修单全流程 ===")
ro = c.post("/api/repairs", params={
    "vehicle_id":vid,"description":"发动机异响","supplier_id":sup_id,
    "idem_key":f"e2e_{uuid.uuid4().hex[:6]}"}, headers=H2)
check("维修单创建", ro.status_code == 200, ro.text[:80])
oid = ro.json()["id"] if ro.status_code == 200 else None
ono = ro.json().get("order_no") if ro.status_code == 200 else None

od = c.get(f"/api/repairs/{oid}", headers=H2)
check("维修单详情", od.status_code == 200 and od.json().get("order_no") == ono)
seq = od.json().get("current_seq")

cl = c.post(f"/api/repairs/{oid}/quote/claim", headers=H2)
check("抢单 claim", cl.status_code == 200, cl.text[:80])

# 报价（JSON body）
q = c.post(f"/api/repairs/{oid}/quote", json={
    "items":[{"part_name":"机油","qty":2,"part_price":200,"labor_price":50}],
    "note":"更换机油"}, headers=H2)
check("报价提交", q.status_code == 200, q.text[:80])

# 推进：接受
av = c.post(f"/api/repairs/{oid}/advance", params={"action":"accept","note":"已修复","seen_seq":seq}, headers=H2)
check("推进 accept", av.status_code == 200, av.text[:80])

rl = c.get("/api/repairs", headers=H2)
check("维修单列表含本单", isinstance(rl.json(), list) and isinstance(rl.json()[0], list) and rl.json()[0][0]["order_no"] == ono)

print("\n=== 6. 人员/角色 ===")
r0 = c.get("/api/system/roles", headers=H2)
check("角色列表（内置3个）", isinstance(r0.json(), list) and len(r0.json()) >= 3)

u = c.post("/api/personnel", params={
    "username":"driver1","name":"张师傅","department_id":dept_id,
    "role_ids":[1]}, headers=H2)
check("人员创建", u.status_code == 200, u.text[:80])

print("\n=== 7. 无 token 拦截 ===")
check("无 token 访问 401", c.get("/api/vehicles").status_code == 401)

print(f"\n=== 结果: {ok} 通过 / {fail} 失败 ===")
os.remove(DB)
shutil.rmtree(_TMP, ignore_errors=True)
sys.exit(1 if fail else 0)
