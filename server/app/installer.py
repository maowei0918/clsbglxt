"""安装向导：未初始化的 clglxt 站点，访问根路径自动进入本模块。

设计原则（对齐宝塔/WordPress 体验）：
  * 上传源码 → 访问域名 → 自动跳 /install
  * 表单填数据库/Redis/管理员 → 服务端测试连接 → 写 .env → 建表 → seed
  * 安装完成 → 写 .install_done 标记 → 页面提示重启站点
  * 已安装的站点访问 /install 自动 302 到登录页

安全：
  * 已安装后 /install 页面与安装 API 全部关闭
  * 管理员密码走 validate_password 强度校验

路径隔离（重要）：
  安装标记与 .env 路径集中在 _paths 字典，通过 configure() 注入。
  生产环境不调用，走 DATA_DIR / BASE_DIR 默认值；测试必须调用
  configure(data_dir=..., env_file=...) 一次性同步全部路径——不要
  直接赋值模块属性，那会漏掉新增的派生路径（ENV_FILE 由 BASE_DIR
  推导，不受 CLGLXT_DATA_DIR 影响，漏绑会改写生产 .env）。
"""
import json
import logging
import os
import secrets
import stat
import time

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from pathlib import Path
from urllib.parse import quote_plus

from .core.config import BASE_DIR, DATA_DIR as _DEFAULT_DATA_DIR
from .core.database import sync_sequences
from .core.security import hash_password, validate_password
from .models import Base, Department, Role, RolePermission, User

log = logging.getLogger("clglxt.install")
router = APIRouter(tags=["install"], include_in_schema=False)

# ── 可注入路径配置 ──
# 这些路径曾是模块级常量（ENV_FILE / INSTALL_LOCK / ...），测试靠事后
# 逐个重绑 inst.XXX 来隔离，极易遗漏：ENV_FILE 由 BASE_DIR 推导，不受
# CLGLXT_DATA_DIR 影响，曾导致 /api/install/save 改写生产 server/.env
# （JWT_SECRET 被轮换，在线 token 全失效）。改为集中配置 + 官方注入入口
# configure()，新增路径只需登记一次即被隔离覆盖，生产不调用则走默认值。
_paths: dict = {
    "data_dir": _DEFAULT_DATA_DIR,
    "install_lock": _DEFAULT_DATA_DIR / ".install_done",
    "install_running": _DEFAULT_DATA_DIR / ".install.running",
    "install_rl": _DEFAULT_DATA_DIR / ".install_rl.json",
    "env_file": BASE_DIR / ".env",
}

# 兼容名映射：保持 inst.INSTALL_LOCK / inst.ENV_FILE 等旧访问方式可用
_EXPORTED_PATHS = {
    "DATA_DIR": "data_dir",
    "INSTALL_LOCK": "install_lock",
    "INSTALL_RUNNING": "install_running",
    "INSTALL_RL": "install_rl",
    "ENV_FILE": "env_file",
}


def configure(data_dir=None, env_file=None) -> dict:
    """注入路径配置（测试/多实例用）。生产环境不调用，走默认值。

    data_dir 改变时，派生自它的三个安装标记路径一并重算——
    这是隔离的关键：只改一个入口，三个派生路径同步生效，不会再漏。
    """
    if data_dir is not None:
        dp = Path(data_dir)
        _paths["data_dir"] = dp
        _paths["install_lock"] = dp / ".install_done"
        _paths["install_running"] = dp / ".install.running"
        _paths["install_rl"] = dp / ".install_rl.json"
    if env_file is not None:
        _paths["env_file"] = Path(env_file)
    return dict(_paths)


def __getattr__(name):
    """PEP 562：模块属性不存在时动态返回路径，保持旧访问方式兼容。"""
    key = _EXPORTED_PATHS.get(name)
    if key is not None:
        return _paths[key]
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

_INSTALL_RL_LIMIT = 20       # 每 IP 20 次
_INSTALL_RL_WINDOW = 300     # / 5 分钟
_INSTALL_STALE_LOCK = 300    # 崩溃残留锁过期秒数


def is_installed() -> bool:
    """安装完成标记：.install_done 文件存在。"""
    return _paths["install_lock"].exists()


def _stamp_alembic_head(engine) -> None:
    """把 create_all 建出的库标记为当前 Alembic head。

    create_all 不会写 alembic_version 表；而 deploy/update.sh 对已安装
    站点会执行 `alembic upgrade head`。缺版本表时 upgrade 会尝试从零
    建全部表，撞上已存在的表直接失败并触发回滚 —— 每次更新都中断服务。

    做法：在 alembic_version 表中写入 head revision。用脚本目录的
    alembic.ini + ScriptDirectory 读 head，避免依赖当前工作目录。
    任何异常都只告警不阻断安装：建表本身已成功，stamp 失败最多导致
    下次更新时手动 stamp 一次，不应让整个安装流程回滚。
    """
    try:
        from alembic import command
        from alembic.config import Config
        from alembic.script import ScriptDirectory

        ini = BASE_DIR / "alembic.ini"
        if not ini.exists():
            log.warning("未找到 alembic.ini，跳过版本标记（%s）", ini)
            return
        cfg = Config(str(ini))
        cfg.set_main_option("script_location", str(BASE_DIR / "alembic"))
        head = ScriptDirectory.from_config(cfg).get_current_head()
        if not head:
            log.warning("Alembic 无 head revision，跳过版本标记")
            return
        # 用现有 engine 连接执行 stamp，避免 alembic 再读一次 .env/URL
        with engine.begin() as conn:
            cfg.attributes["connection"] = conn
            command.stamp(cfg, head)
        log.info("已标记 Alembic 版本为 head: %s", head)
    except Exception:
        log.warning("Alembic 版本标记失败（不影响安装，更新前需手动 stamp）",
                    exc_info=True)


# ───────────── 未认证安装接口守卫：限频 + 原子锁 ─────────────

def _install_rate_limit(request: Request) -> None:
    """按客户端 IP 的滑动窗口限频。

    安装发生在 Redis 配置生效之前，故用文件 + flock 实现（多 worker 安全），
    不复用 redis_client.rate_limit（后者在 Redis 故障时 fail-open，不适合此处）。
    """
    ip = request.client.host if request.client else "unknown"
    now = time.time()
    _paths["data_dir"].mkdir(parents=True, exist_ok=True)
    try:
        import fcntl  # Linux/Unix（生产部署环境）
    except ImportError:  # Windows 开发机降级：仅本地调试，并发无所谓
        fcntl = None
    try:
        if not _paths["install_rl"].exists():
            _paths["install_rl"].touch()
        with _paths["install_rl"].open("r+", encoding="utf-8") as f:
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_EX)
            try:
                f.seek(0)
                raw = f.read()
                data = json.loads(raw) if raw.strip() else {}
            except Exception:
                data = {}
            # 清理过期条目，避免文件无限增长
            data = {k: [t for t in v if now - t < _INSTALL_RL_WINDOW]
                    for k, v in data.items()}
            data = {k: v for k, v in data.items() if v}
            hits = list(data.get(ip, []))
            if len(hits) >= _INSTALL_RL_LIMIT:
                raise HTTPException(429, "操作过于频繁，请 5 分钟后再试")
            hits.append(now)
            data[ip] = hits
            f.seek(0)
            f.truncate()
            f.write(json.dumps(data))
            f.flush()
            if fcntl:
                fcntl.flock(f.fileno(), fcntl.LOCK_UN)
    except HTTPException:
        raise
    except OSError as e:
        # 目录不可写不应阻断安装；限频降级但记日志
        log.warning("安装限频计数写入失败：%s", e)


def _acquire_install_lock() -> None:
    """原子抢占安装锁：O_CREAT|O_EXCL，多 worker 并发只有一个能进入。"""
    _paths["data_dir"].mkdir(parents=True, exist_ok=True)
    if _paths["install_running"].exists():
        age = time.time() - _paths["install_running"].stat().st_mtime
        if age > _INSTALL_STALE_LOCK:  # 进程崩溃残留
            try:
                _paths["install_running"].unlink()
            except OSError:
                pass
    try:
        fd = os.open(_paths["install_running"], os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        raise HTTPException(409, "安装正在进行中，请稍后刷新页面确认结果")
    try:
        os.write(fd, str(os.getpid()).encode())
    finally:
        os.close(fd)


def _release_install_lock() -> None:
    try:
        _paths["install_running"].unlink(missing_ok=True)
    except OSError:
        pass


def _install_guard(request: Request) -> None:
    """未认证安装接口统一守卫：已安装关闭 + 限频。"""
    if is_installed():
        raise HTTPException(403, "系统已安装，安装流程已关闭")
    _install_rate_limit(request)


def _safe_port(value) -> int:
    """端口转换：非法输入归一到 5432，避免 ValueError 导致 500。"""
    try:
        p = int(value or 5432)
        return p if 1 <= p <= 65535 else 5432
    except (TypeError, ValueError):
        return 5432


def _db_fail(e: Exception) -> HTTPException:
    """DB 连接异常脱敏：客户端只拿通用提示，全文仅写服务端日志。

    原实现把 str(e)[:200] 回显，会泄露主机/端口/用户名等内网信息。
    """
    log.warning("数据库连接失败：%s", e)
    return HTTPException(
        400, "数据库连接失败，请检查主机、端口、库名、用户名、密码及网络连通性"
    )


# ─────────────────────────── 页面 ───────────────────────────

_PAGE = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>clglxt · 安装向导</title>
<style>
* { margin:0; padding:0; box-sizing:border-box; }
body { font-family:"PingFang SC","Microsoft YaHei",sans-serif;
       background:linear-gradient(135deg,#0f172a 0%,#1e293b 55%,#0c4a6e 100%);
       min-height:100vh; display:flex; align-items:center; justify-content:center;
       padding:24px; }
.card { background:#fff; border-radius:16px; width:100%; max-width:560px;
        padding:36px 40px; box-shadow:0 25px 60px rgba(0,0,0,.35); }
.brand { text-align:center; margin-bottom:28px; }
.brand .logo { font-size:34px; font-weight:800; letter-spacing:1px;
               background:linear-gradient(90deg,#0ea5e9,#22d3ee);
               -webkit-background-clip:text; -webkit-text-fill-color:transparent; }
.brand p { color:#64748b; font-size:14px; margin-top:6px; }
.section { margin-bottom:22px; }
.section h3 { font-size:13px; color:#475569; text-transform:uppercase;
              letter-spacing:1px; margin-bottom:10px; font-weight:600; }
.grid { display:grid; grid-template-columns:1fr 1fr; gap:12px; }
.field { display:flex; flex-direction:column; gap:5px; }
.field.full { grid-column:1/-1; }
.field label { font-size:12px; color:#64748b; }
.field input { border:1px solid #e2e8f0; border-radius:8px; padding:10px 12px;
               font-size:14px; transition:border .15s; }
.field input:focus { outline:none; border-color:#0ea5e9; box-shadow:0 0 0 3px rgba(14,165,233,.15); }
button { width:100%; border:none; border-radius:8px; padding:12px;
         background:linear-gradient(90deg,#0ea5e9,#0284c7); color:#fff;
         font-size:15px; font-weight:600; cursor:pointer; transition:opacity .15s; }
button:hover { opacity:.88; } button:disabled { opacity:.5; cursor:not-allowed; }
.msg { border-radius:8px; padding:10px 14px; font-size:13px; margin-top:14px; display:none; }
.msg.err { background:#fef2f2; color:#b91c1c; border:1px solid #fecaca; }
.msg.ok { background:#f0fdf4; color:#15803d; border:1px solid #bbf7d0; }
.msg.info { background:#eff6ff; color:#1d4ed8; border:1px solid #bfdbfe; }
.hint { font-size:12px; color:#94a3b8; margin-top:16px; text-align:center; }
</style>
</head>
<body>
<div class="card">
  <div class="brand">
    <div class="logo">clglxt</div>
    <p>车辆管理系统 · 安装向导</p>
  </div>
  <form id="form" onsubmit="return false;">
    <div class="section">
      <h3>数据库</h3>
      <div class="grid">
        <div class="field"><label>主机</label>
          <input name="db_host" value="127.0.0.1" required></div>
        <div class="field"><label>端口</label>
          <input name="db_port" value="5432" required></div>
        <div class="field"><label>数据库名</label>
          <input name="db_name" value="clglxt" required></div>
        <div class="field"><label>用户名</label>
          <input name="db_user" value="postgres" required></div>
      </div>
      <div class="field full" style="margin-top:12px"><label>密码</label>
        <input name="db_pass" type="password" required></div>
    </div>

    <div class="section">
      <h3>Redis（可选，先留空）</h3>
      <div class="field full">
        <input name="redis_url" value="redis://127.0.0.1:6379/0">
        <label style="margin-top:4px">不填则使用本地默认，后续可在系统设置里改</label>
      </div>
    </div>

    <div class="section">
      <h3>管理员</h3>
      <div class="grid">
        <div class="field"><label>用户名</label>
          <input name="admin_user" value="admin" required></div>
        <div class="field"><label>姓名</label>
          <input name="admin_name" value="系统管理员"></div>
      </div>
      <div class="field full" style="margin-top:12px"><label>初始密码（至少 8 位，含大小写字母与数字）</label>
        <input name="admin_pass" type="password" autocomplete="new-password" required>
        <label style="margin-top:4px">请自行设置强密码；登录后系统会再次强制修改</label></div>
    </div>

    <div class="section">
      <h3>站点</h3>
      <div class="field full"><label>系统标题</label>
        <input name="site_title" value="车辆管理系统"></div>
    </div>

    <button id="btn" type="button">安装系统</button>
    <div class="msg" id="msg"></div>
  </form>
  <p class="hint">安装完成请重启站点以使配置生效</p>
</div>
<script>
const $ = s => document.querySelector(s);
const f = s => new FormData($('form')).get(s) || '';
function show(cls, txt) {
  const m = $('#msg'); m.className = 'msg ' + cls; m.textContent = txt; m.style.display = 'block';
}
$('#btn').onclick = async () => {
  const btn = $('#btn'); btn.disabled = true; btn.textContent = '正在安装…';
  show('info', '正在连接数据库…');
  try {
    const data = {
      db_host: f('db_host'), db_port: f('db_port'), db_name: f('db_name'),
      db_user: f('db_user'), db_pass: f('db_pass'),
      redis_url: f('redis_url'),
      admin_user: f('admin_user'), admin_name: f('admin_name'), admin_pass: f('admin_pass'),
      site_title: f('site_title'),
    };
    const r = await fetch('/api/install/save', {
      method:'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify(data),
    });
    const j = await r.json().catch(() => ({}));
    if (r.ok && j.ok) {
      show('ok', '安装成功！正在进入系统…');
      setTimeout(() => location.href = '/login', 1500);
    } else {
      show('err', j.detail || ('安装失败（HTTP ' + r.status + '），请检查数据库连接信息'));
      btn.disabled = false; btn.textContent = '安装系统';
    }
  } catch (e) {
    show('err', '网络错误：' + e); btn.disabled = false; btn.textContent = '安装系统';
  }
};
</script>
</body>
</html>"""


@router.get("/install", response_class=HTMLResponse)
def install_page():
    if is_installed():
        return RedirectResponse("/login", status_code=302)
    return HTMLResponse(_PAGE)


@router.get("/login")
def login_page():
    """安装完成后的登录页（前端未部署时的兜底）。"""
    if not is_installed():
        return RedirectResponse("/install", status_code=302)
    return HTMLResponse("""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>登录 · clglxt</title>
<style>
*{margin:0;padding:0;box-sizing:border-box}
body{font-family:"PingFang SC","Microsoft YaHei",sans-serif;min-height:100vh;
     display:flex;align-items:center;justify-content:center;
     background:linear-gradient(135deg,#0f172a,#1e293b 60%,#0c4a6e)}
.card{background:#fff;border-radius:16px;padding:40px;width:380px;
      box-shadow:0 25px 60px rgba(0,0,0,.35)}
.logo{text-align:center;font-size:30px;font-weight:800;
      background:linear-gradient(90deg,#0ea5e9,#22d3ee);
      -webkit-background-clip:text;-webkit-text-fill-color:transparent;margin-bottom:6px}
p.sub{text-align:center;color:#64748b;font-size:13px;margin-bottom:24px}
label{display:block;font-size:12px;color:#64748b;margin-bottom:5px}
input{width:100%;border:1px solid #e2e8f0;border-radius:8px;padding:11px 12px;
      font-size:14px;margin-bottom:14px;box-sizing:border-box}
input:focus{outline:none;border-color:#0ea5e9;box-shadow:0 0 0 3px rgba(14,165,233,.15)}
button{width:100%;border:none;border-radius:8px;padding:12px;
       background:linear-gradient(90deg,#0ea5e9,#0284c7);color:#fff;
       font-size:15px;font-weight:600;cursor:pointer;margin-top:4px}
.msg{border-radius:8px;padding:10px;font-size:13px;margin-top:12px;display:none}
.msg.err{background:#fef2f2;color:#b91c1c;border:1px solid #fecaca}
</style></head><body>
<div class="card">
  <div class="logo">clglxt</div>
  <p class="sub">车辆管理系统 · 登录</p>
  <form id="f" onsubmit="return false">
    <label>用户名</label><input id="u" value="admin">
    <label>密码</label><input id="p" type="password" value="" autocomplete="current-password">
    <button id="b" type="button">登 录</button>
    <div class="msg" id="m"></div>
  </form>
</div>
<script>
const $=s=>document.querySelector(s);
$('#b').onclick=async()=>{
  const r=await fetch('/api/auth/login?username='+encodeURIComponent($('#u').value)+
    '&password='+encodeURIComponent($('#p').value),{method:'POST'});
  const j=await r.json().catch(()=>({});
  if(r.ok&&j.token){
    sessionStorage.setItem('clglxt_token',j.token);
    show('ok','登录成功，正在进入…'); setTimeout(()=>location.href='/',1000);
  } else show('err',j.detail||'登录失败');
};
function show(c,t){const m=$('#m');m.className='msg '+c;m.textContent=t;m.style.display='block'}
</script></body></html>""")


# ─────────────────────────── API ───────────────────────────

@router.get("/api/install/status")
def install_status():
    """仅返回是否已完成安装（供前端判断跳转），不含任何连接信息。"""
    return {
        "installed": is_installed(),
        "service": "clglxt",
    }


def _pg_url(host, port, name, user, password) -> str:
    pw = quote_plus(password) if password else ""
    return f"postgresql+psycopg2://{user}:{pw}@{host}:{port}/{name}"


@router.post("/api/install/test")
def test_connection(request: Request, payload: dict):
    """测试数据库连接（安装向导用）。

    该接口未认证即可访问，本质是一个 DB 探测工具，故必须：
      * 已安装后关闭
      * 限频，防止被当作内网 DB 端口/凭据爆破器
      * 异常脱敏，不回显内网主机/端口/用户名
    """
    _install_guard(request)
    host = str(payload.get("db_host") or "127.0.0.1").strip()
    port = _safe_port(payload.get("db_port"))
    name = str(payload.get("db_name") or "").strip()
    user = str(payload.get("db_user") or "").strip()
    password = str(payload.get("db_pass") or "")
    if not name or not user:
        raise HTTPException(400, "数据库名与用户名不能为空")
    engine = None
    try:
        engine = create_engine(_pg_url(host, port, name, user, password),
                               pool_pre_ping=True, connect_args={"connect_timeout": 5})
        with engine.connect() as conn:
            conn.execute(text("SELECT 1"))
        return {"ok": True, "message": "数据库连接成功"}
    except Exception as e:
        raise _db_fail(e)
    finally:
        if engine is not None:
            engine.dispose()


def _write_env(data: dict) -> None:
    """写生产 .env（安装完成一次性生成）。

    安全要点：
      * 权限 0o600（仅属主可读写）——.env 含 DB 密码与 JWT 密钥，
        泄露 JWT_SECRET 即可伪造任意用户 token（含超管）。
      * 原子写入（同目录临时文件 + os.replace），避免半截文件被并发读取。
      * CLGLXT_ENABLE_DOCS=false：生产不暴露 /docs 与 /openapi.json，
        否则泄露全部路由与权限点命名。
    """
    jwt_secret = secrets.token_hex(32)
    redis_url = str(data.get("redis_url") or "redis://127.0.0.1:6379/0").strip()
    host = str(data.get("db_host") or "127.0.0.1").strip()
    port = _safe_port(data.get("db_port"))
    name = str(data.get("db_name") or "clglxt").strip()
    user = str(data.get("db_user") or "postgres").strip()
    password = str(data.get("db_pass") or "")
    site_title = str(data.get("site_title") or "车辆管理系统").strip()

    lines = [
        f"CLGLXT_DATABASE_URL=postgresql+psycopg2://{user}:{quote_plus(password)}@{host}:{port}/{name}",
        f"CLGLXT_REDIS_URL={redis_url}",
        f"CLGLXT_CELERY_BROKER_URL={redis_url.rsplit('/', 1)[0]}/1",
        f"CLGLXT_CELERY_RESULT_BACKEND={redis_url.rsplit('/', 1)[0]}/2",
        f"CLGLXT_JWT_SECRET={jwt_secret}",
        f"CLGLXT_APP_TITLE={site_title}",
        "CLGLXT_DEBUG=false",
        "CLGLXT_ENABLE_DOCS=false",
        "",
    ]
    content = "\n".join(lines)
    tmp = _paths["env_file"].with_suffix(".env.tmp")
    try:
        tmp.write_text(content, encoding="utf-8")
        os.chmod(tmp, stat.S_IRUSR | stat.S_IWUSR)  # 0o600
        os.replace(tmp, _paths["env_file"])
    except OSError as e:
        try:
            tmp.unlink(missing_ok=True)
        except OSError:
            pass
        log.error("写入 .env 失败：%s", e)
        raise HTTPException(500, "写入 .env 失败（请检查站点目录权限）")


@router.post("/api/install/save")
def save_install(request: Request, payload: dict):
    """保存配置 → 建表 → seed 管理员 → 写标记。

    安全：
      * 未认证接口，统一走 _install_guard（已安装关闭 + 限频）
      * 原子锁防并发重复安装（流程开始即创建，非最后写）
      * DB 连接异常脱敏，不回显内网信息
      * seed 幂等：任一并发/重试命中唯一约束时回滚，不产生脏数据
      * 失败时释放锁并回滚 DB 会话，避免半安装状态卡死后续重试
    """
    _install_guard(request)
    if is_installed():
        raise HTTPException(403, "系统已安装，安装流程已关闭")

    # 0. 原子锁：开始即占位，多 worker 并发只有一个能进入
    _acquire_install_lock()

    # 1. 校验输入
    admin_user = str(payload.get("admin_user") or "").strip().lower()
    admin_pass = str(payload.get("admin_pass") or "")
    admin_name = str(payload.get("admin_name") or "系统管理员").strip()
    site_title = str(payload.get("site_title") or "车辆管理系统").strip()
    if not admin_user:
        raise HTTPException(400, "管理员用户名不能为空")
    err = validate_password(admin_pass, admin_user)
    if err:
        raise HTTPException(400, f"管理员密码不符合要求：{err}")

    host = str(payload.get("db_host") or "127.0.0.1").strip()
    port = _safe_port(payload.get("db_port"))
    name = str(payload.get("db_name") or "").strip()
    db_user = str(payload.get("db_user") or "").strip()
    db_pass = str(payload.get("db_pass") or "")
    if not name or not db_user:
        raise HTTPException(400, "数据库名与用户名不能为空")

    # 2. 连接测试 + 建表 + seed，全程失败释放锁
    engine = None
    db = None
    try:
        try:
            engine = create_engine(
                _pg_url(host, port, name, db_user, db_pass),
                pool_pre_ping=True, connect_args={"connect_timeout": 5},
            )
            with engine.connect() as conn:
                conn.execute(text("SELECT 1"))
        except Exception as e:
            raise _db_fail(e)

        # 3. 建表（幂等）
        Base.metadata.create_all(bind=engine)

        # 3b. 标记 Alembic 版本为当前 head。
        # 不做这一步，create_all 建出的库没有 alembic_version 表，
        # 后续 deploy/update.sh 的 `alembic upgrade head` 会因
        # "relation already exists" 直接失败并触发回滚 ——
        # 等于每次更新都中断服务。
        _stamp_alembic_head(engine)

        # 4. seed 管理员 + 内置角色（+ 可选初始部门）
        Session = sessionmaker(bind=engine, autoflush=False, expire_on_commit=False)
        db = Session()
        dept_name = str(payload.get("dept_name") or "").strip()
        if dept_name and not db.query(Department).first():
            dept = Department(name=dept_name)
            db.add(dept)
            db.flush()
        else:
            dept = None

        admin = User(
            username=admin_user,
            password_hash=hash_password(admin_pass),
            name=admin_name,
            is_superuser=True,
            is_active=True,
            token_version=0,
            must_change_password=True,
            department_id=dept.id if dept else None,
        )
        db.add(admin)
        db.flush()

        # 内置角色 + 超管通配（幂等，重试不会撞唯一约束）
        for rid, rname, side in ((1, "内部管理员", "internal"),
                                 (2, "汽修厂管理员", "external"),
                                 (3, "超管", "internal")):
            if not db.get(Role, rid):
                db.add(Role(id=rid, name=rname, side=side, is_system=True))
        exists = (db.query(RolePermission)
                  .filter(RolePermission.role_id == 3,
                          RolePermission.perm_code == "*")
                  .first())
        if not exists:
            db.add(RolePermission(role_id=3, perm_code="*"))
        db.commit()

        # 4.1 对齐自增序列（必须在 seed 提交之后！）
        # 内置角色/超管用显式 id 插入，不推进 PG sequence；若在建表后
        # 就同步，表还是空的，setval 到 1，首个自增建号照样撞主键。
        sync_sequences(engine)

        # 5. 写 .env（安装后重启站点生效）
        _write_env(payload)

        # 6. 标记完成
        _paths["install_lock"].write_text("done", encoding="utf-8")
    except Exception:
        if db is not None:
            db.rollback()
        _release_install_lock()
        raise
    finally:
        if db is not None:
            db.close()
        if engine is not None:
            engine.dispose()
        _release_install_lock()

    return {
        "ok": True,
        "message": "安装成功。请重启站点（宝塔：站点 → 重启），然后访问 /login",
        "admin_username": admin_user,
        "site_title": site_title,
    }
