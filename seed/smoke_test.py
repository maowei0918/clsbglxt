"""clglxt 真实 API 冒烟测试（对准 openapi 实际路由/参数风格）。

要点：
  * 写接口用 query 参数，不是 JSON body。
  * 数据名带唯一后缀，脚本可重复运行。
  * 兼容首次安装（种子密码）与已改密两种状态。
用法: server/.venv/bin/python ../seed/smoke_test.py
"""
from uuid import uuid4
import requests

B = "http://127.0.0.1:18113"
ok, fail = [], []
TAG = uuid4().hex[:6]
def N(s): return f"{s}-{TAG}"

def chk(name, cond, extra=""):
    (ok if cond else fail).append(name + (f"  <{extra}>" if extra and not cond else ""))
    print(("  ok " if cond else "  XX ") + name + (f"  <{extra}>" if extra and not cond else ""))

# ── 登录（兼容首次安装/已改密）──
INIT_CANDIDATES = ["SmokeTest#2024", "SmokePass456!", "admin123"]
r, PWD0 = None, None
for cand in INIT_CANDIDATES:
    rr = requests.post(f"{B}/api/auth/login", json={"username": "admin", "password": cand})
    if rr.status_code == 200:
        r, PWD0 = rr, cand
        break
if r is None:
    r = requests.post(f"{B}/api/auth/login", json={"username": "admin", "password": INIT_CANDIDATES[0]})
chk("POST /auth/login 200", r.status_code == 200, r.text[:150])
assert r.status_code == 200, "无法登录：请先重建测试库或确认 admin 密码"
PWD0 = PWD0 or INIT_CANDIDATES[0]
H = {"Authorization": f"Bearer {r.json()['token']}"}

me = requests.get(f"{B}/api/auth/me", headers=H)
chk("GET /auth/me 200", me.status_code == 200, me.text[:150])
print(f"     admin.must_change_password={me.json().get('must_change_password', False)}")

# 首次登录须改密，否则业务接口 403
if me.json().get("must_change_password"):
    r2 = requests.post(f"{B}/api/auth/change-password", headers=H,
                       json={"old_password": PWD0, "new_password": "SmokePass456!"})
    chk("POST /auth/change-password 200", r2.status_code == 200, r2.text[:200])
    if r2.status_code == 200:
        H = {"Authorization": f"Bearer {r2.json().get('token')}"}

# ── 读接口（真实路由）──
READS = [
    "/api/healthz", "/api/auth/me", "/api/suppliers", "/api/departments",
    "/api/personnel", "/api/vehicles", "/api/vehicles/types/all", "/api/vehicles/natures/all",
    "/api/contracts", "/api/repairs", "/api/daily-check/records",
    "/api/daily-check/templates", "/api/notifications", "/api/notifications/unread-count",
    "/api/vehicle-use", "/api/my-shop/assignable-perms", "/api/system/roles",
    "/api/fence/alerts", "/api/fence/night-runs", "/api/fence/points",
    "/api/backup/jobs", "/api/backup/targets", "/api/import-fms/config", "/api/import-fms/status",
]
for p in READS:
    rr = requests.get(f"{B}{p}", headers=H)
    chk(f"GET {p} -> 200", rr.status_code == 200, f"{rr.status_code} {rr.text[:100]}")

# vehicle-account 需真实车牌（参数名 plate_no）；未知车牌 404 属预期
for p in ("/api/vehicle-account", "/api/vehicle-account/summary"):
    rr = requests.get(f"{B}{p}", headers=H, params={"plate_no": "冒烟A00000"})
    chk(f"GET {p} 未知车牌 404", rr.status_code == 404, f"{rr.status_code} {rr.text[:100]}")

# my-shop 仅汽修厂账号；admin 是部门账号，403 属预期
rr = requests.get(f"{B}/api/my-shop", headers=H)
chk("GET /my-shop 非汽修厂账号 403", rr.status_code == 403, f"{rr.status_code} {rr.text[:100]}")

# ── 写接口（query 参数）──
# 供应商
r = requests.post(f"{B}/api/suppliers", headers=H,
                  params={"name": N("冒烟汽修厂"), "contact": "张三", "phone": "****0000"})
chk("POST /suppliers 200", r.status_code == 200, r.text[:160])
sid = r.json().get("id") if r.status_code == 200 else None
if sid:
    rr = requests.put(f"{B}/api/suppliers/{sid}", headers=H, params={"name": N("冒烟汽修厂-改")})
    chk("PUT /suppliers/{id} 200", rr.status_code == 200, rr.text[:160])
    rr = requests.get(f"{B}/api/suppliers", headers=H)
    body = rr.json()
    seq = body if isinstance(body, list) else body.get("items", [])
    chk("GET /suppliers 含新增", any(x.get("id") == sid for x in seq), rr.text[:120])
    rr = requests.delete(f"{B}/api/suppliers/{sid}", headers=H)
    chk("DELETE /suppliers/{id} 200", rr.status_code == 200, rr.text[:160])

# 部门
r = requests.post(f"{B}/api/departments", headers=H, params={"name": N("冒烟部门"), "remark": "smoke"})
chk("POST /departments 200", r.status_code == 200, r.text[:160])
did = r.json().get("id") if r.status_code == 200 else None
if did:
    rr = requests.put(f"{B}/api/departments/{did}", headers=H, params={"name": N("冒烟部门-改")})
    chk("PUT /departments/{id} 200", rr.status_code == 200, rr.text[:160])
    rr = requests.delete(f"{B}/api/departments/{did}", headers=H)
    chk("DELETE /departments/{id} 200", rr.status_code == 200, rr.text[:160])

# 人员（归属部门单独新建）+ 改资料 + 重置密码 + 删除
d2 = requests.post(f"{B}/api/departments", headers=H, params={"name": N("冒烟人员归属部")})
chk("POST /departments (人员归属)", d2.status_code == 200, d2.text[:160])
did2 = d2.json().get("id") if d2.status_code == 200 else None
r = requests.post(f"{B}/api/personnel", headers=H,
                  params={"username": f"smokeuser{TAG}", "name": N("冒烟用户"),
                          "password": "InitPass123!", "department_id": did2})
chk("POST /personnel 200", r.status_code == 200, r.text[:200])
uid = r.json().get("id") if r.status_code == 200 else None
if uid:
    rr = requests.put(f"{B}/api/personnel/{uid}", headers=H, params={"name": N("冒烟用户-改")})
    chk("PUT /personnel/{id} 200", rr.status_code == 200, rr.text[:160])
    rr = requests.post(f"{B}/api/personnel/{uid}/reset-password", headers=H)
    chk("POST /personnel/{id}/reset-password 200", rr.status_code == 200, rr.text[:160])
    rr = requests.delete(f"{B}/api/personnel/{uid}", headers=H)
    chk("DELETE /personnel/{id} 200", rr.status_code == 200, rr.text[:160])

# 车辆类型 / 性质 / 角色
r = requests.post(f"{B}/api/vehicles/types", headers=H, params={"name": N("冒烟车型")})
chk("POST /vehicles/types 200", r.status_code == 200, r.text[:160])
r = requests.post(f"{B}/api/vehicles/natures", headers=H, params={"name": N("冒烟性质")})
chk("POST /vehicles/natures 200", r.status_code == 200, r.text[:160])
r = requests.post(f"{B}/api/system/roles", headers=H, params={"name": N("冒烟角色")})
chk("POST /system/roles 200", r.status_code == 200, r.text[:160])
rid = r.json().get("id") if r.status_code == 200 else None
if rid:
    rr = requests.delete(f"{B}/api/system/roles/{rid}", headers=H)
    chk("DELETE /system/roles/{id} 200", rr.status_code == 200, rr.text[:160])

# 登出（无状态 JWT，前端丢弃 token 即可）
rr = requests.post(f"{B}/api/auth/logout", headers=H)
chk("POST /auth/logout 200 (无状态 JWT)", rr.status_code == 200, rr.text[:120])

print(f"\n{'='*50}\nPASS={len(ok)}  FAIL={len(fail)}")
for n in fail:
    print("  FAILED:", n)
raise SystemExit(1 if fail else 0)
