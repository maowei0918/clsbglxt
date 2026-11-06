# clglxt 静态安全审计报告

> 审计范围：`/vol1/1000/AI/octop/clglxt` — 前端(Vue 3 + Vite)、安卓(WebView 壳 + Kotlin)、部署脚本(gunicorn + Nginx + PostgreSQL)
> 严重度：**P0** = 立即修复 | **P1** = 本迭代内修复 | **P2** = 下一迭代改进

---

## 一、前端 (frontend)

| 文件:行号 | 严重度 | 问题 | 修复建议 |
|---|---|---|---|
| `src/api/http.js:11,55` + `src/views/Login.vue:55` | **P1** | **Token 存于 localStorage，可被 XSS 窃取**：`localStorage.setItem('clglxt_token', ...)` 任何前端脚本都能读取，也应存在 Referer/History 泄露路径。 | 改用 **HttpOnly + Secure + SameSite=Lax** 的 Cookie 持久化 token。若必须 localStorage，至少确保全站强制 CSP `script-src 'self'` 且所有输入已转义/过滤，禁止内联脚本与危险源。 |
| `src/views/Login.vue:54-55` | **P1** | **登录 open redirect**：`const redirect = route.query.redirect || '/dashboard'` 经 `router.replace(redirect)` 后，攻击者可构造 `?redirect=//evil.com/x.js` 劫持 SPA 跳转；即使 Vue Router 视作 path，SPA 仍可能被注入恶意第三方 domain。 | 白名单校验 `redirect` 必须以 `/` 开头且不含 `//` 与域名。示例：`if (!/^\/[a-zA-Z0-9/_-]+$/.test(redirect)) redirect = '/dashboard'`。 |
| `src/api/http.js:4-10` | **P2** | **Axios 实例无 CSRF 防护**：虽然用 Bearer token（天然抗 CSRF），但未设置 `withCredentials`/`xsrfCookieName`，一旦后端新增 Cookie 认证即失效，且缺少 CSRF 同步令牌。 | 若后续引入 Cookie-based 认证，must 使用 `withCredentials: true` 并配置 `X-XSRF-TOKEN` 双提交。 |
| `frontend/src/views/RepairDetail.vue:70` | **P2** | **读取未消毒的 localStorage 后直接 JSON.parse**：`const auth = JSON.parse(localStorage.getItem('clglxt_user') || '{}')` 如果前端不写此 key 说明它是 spooky，且 `clglxt_user` 也存 localStorage，形成双份凭证副本，泄露面翻倍。 | 移除 `localStorage` 读取权限的 `clglxt_user`；权限改由 `useAuthStore().can()` 统一计算，避免多处读取。 |
| `vite.config.js:26-32` | **P2** | **dev server 未配置 CSP / trustedHost**：默认 `port: 5173` 无 host 限制，默认允许所有来源连接，公网暴露时可能导致 DNS rebinding 访问内网。 | 生产构建后 `dist` 由 Nginx 托管，禁止外露 dev server。若确需外网调试，至少 `server.host: '127.0.0.1'` 并启动 `vueDevtools: false`。 |
| `frontend/src/components/PhotoUpload.vue:5` | **P1** | **缩略图 URL 直接 `:src` 信任服务端响应**：`:src="p.thumb_url \|\| p.url"` 虽已是绑定（Vue 不转义 src 值），但若后端未校验 `thumb_url` 协议，可能注入 `javascript:` Data URI | 后端返回 URL 前强制 `https://yourcdn/` 白名单校验；前端额外 `URL.canParse()` + `protocol === 'https:' \|\| 'http:'` 前置过滤。 |
| `frontend/src/api/auth.js:4-6` + `frontend/src/api/auth.js:16-19` | **P1** | **login / change-password 使用 GET 风格传参**（`{ params: ... }` 拼接 query string）：用户名/密码以明文在 URL、access log、Referer Header 中留存。完整密码出现在 Nginx/ELB/代理/浏览器 history 里。 | 后端同时支持 `application/json` 或 `application/x-www-form-urlencoded` POST body；前端改传 `data` 替代 `params`。 |
| `frontend/public/sw.js:65-67` + 其它 | **P2** | **Service Worker 缓存了 `/api/daily-check/photos/`（含授权头）**：攻击者若已窃 token 可从 cache API 二次提取旧照片。 | SW 仅缓存静态资源，所有带 `Authorization` 或敏感数据的响应 **不进入 cache**（已在第 38 行 skip 了通用 API，但 photo 通路绕过了）。 |
| `frontend/src/main.js:20-26` | **P2** | **SW 注册失败仅 console.warn**：SW 生命周期未控制旧版本，若更新 Service Worker 可能继续拦截事件；未在 server 侧设 `Clear-Site-Data`。 | SW 未签名（无 integrity），生产环境建议加 `Service-Worker-Allowed` / 版本化更新，或在 Nginx 返回 `Service-Worker-Allowed: /`。 |
| `frontend/vite.config.js:22-27` | **P2** | **proxy 仅 127.0.0.1:8000**：开发环境对外层网络无身份校验，若调试时误绑 `0.0.0.0` 会把本地起的服务暴露到内网。 | 生产打包后不走此代理，但可加注释警告或加白名单 origin 检查。 |

### 未发现的问题（正面）
- 全站无 `v-html` / `innerHTML` / `dangerouslyUseHTMLString` / `location.href=` 动态赋值（XSS 面控制良好）
- 未发现硬编码 Token / 密钥
- 路由守卫 `router.beforeEach` 已实现"未登录跳登录 + must_change_password 拦截 + meta.perm 校验"三层，但仅作为"体验层"（后端兜底正确）
- API 错误拦截 401/403/429/500 分层做了，且不会把完整 500 细节回显到用户（安全失败正确）

---

## 二、安卓 (android)

| 文件:行号 | 严重度 | 问题 | 修复建议 |
|---|---|---|---|
| `MainActivity.kt:45` `mixedContentMode = MIXED_CONTENT_ALWAYS_ALLOW` | **P0** | **WebView 允许任意混合内容**：在已启用 JS + 文件访问 + 全局 HTTP 信任情况下，攻击者通过中间人注入的 `<script src="http://evil/x.js">` 直接攻击页面取得 cookies / token。 | 生产构建 `release` 时强制 `MIXED_CONTENT_NEVER_ALLOW`；仅对 debug build `MIXED_CONTENT_COMPATIBILITY_MODE`。 |
| `MainActivity.kt:43` `allowFileAccess = true` + `:44` `allowContentAccess = true` | **P1** | **允许 file:// 协议范围加载 + ContentProvider 访问**：FileProvider 始终开启，且 file_paths.xml 的 `<cache-path path="." />` 与 `<files-path path="." />` 暴露整个 cache 与 files 目录，恶意网页通过 XHR 读 `file:///data/data/.../shared_prefs/clglxt_prefs.xml` 可窃取 ServerConfig 中 URL 与登录态残留。 | 3.x 默认应 `allowFileAccess=false`、`allowContentAccess=false`（拍照通过 WebChromeClient.onShowFileChooser 拦截，已经不需要 file://）；file_paths.xml 缩小为仅 `cache-path name="camera" path="camera/"` 与 `files-path name="photos" path="photos/"` 子目录。 |
| `MainActivity.kt:137` `addJavascriptInterface(WebAppInterface(), "Android")` | **P1** | **暴露 JS 接口给所有加载域**：WebView 加载任意 URL（允许用户自定义 server address），所有网页可直接调用 `Android.setServerUrl("http://evil.com")` 劫持 WebView 跳转到钓鱼站点。另外 `getServerUrl()` 也可读。 |（1）限制仅白名单域名可调接口：注入前 `if (url.host.endsWith(".clglxt.example.com"))`，否则 `removeJavascriptInterface("Android")`；（2）若只需内部调用，用 `WebView.evaluateJavascript()` 替代桥接；（3）改为通过 postMessage / prompt 拦截实现通信。 |
| `MainActivity.kt:39` `@SuppressLint("SetJavaScriptEnabled")` + `settings.mediaPlaybackRequiresUserGesture = false` | **P2** | **媒体自动播放**：可被利用做 ultrasonic cross-site tracking / 消耗用户流量。 | `mediaPlaybackRequiresUserGesture = true`，已是现代 WebView 默认值。 |
| `MainActivity.kt:57-60` `onReceivedError` 注入 HTML | **P2** | **loadDataWithBaseURL(null, html)`**：离线/失败页使用 null baseURL，但 WebView 仍能跑 JS（未在此处 disable JS），且错误提示没有 `noopener` 同类。 | 改用独立的本地静态失败页 `file:///android_asset/offline.html`，并禁用失败页 JS；或调用 `settings.javaScriptEnabled = false` 后再注入。 |
| `MainActivity.kt:68-73` `shouldOverrideUrlLoading` 放行所有非 `intent://` 和 `market://` 的 URL | **P2** | **Tel/sms/maps 等 scheme 无拦截**，可被用作拨打电话或发短信到付费号码。白名单处理。 | 至少允许 `http/https/intent/market`，其余全部拦截并 `return true`。 |
| `MainActivity.kt:229` `Log.d` 尺寸/KB 值 + `:233` `Log.e` 打印压缩异常 | **P2** | **生产日志含部分敏感元信息**：文件尺寸、宽高、时间戳。Android release 包通常需 strip（R8/Proguard 默认不开 debug 日志；但实际配置见下文 proguard）。 | 使用 Timber 或 `#if DEBUG` 包；main处置顶 `if (!BuildConfig.DEBUG) Log.setLoggable(...)`。 |
| `app/proguard-rules.pro:1` `-dontobfuscate` + `:5-6` `-keep class com.clglxt.app.** { *; }` | **P1** | **release 包未混淆**：攻击者反编译后可直接看到 `WebAppInterface`、`setServerUrl`、`getServerUrl`、文件路径逻辑，辅以jadx可秒级还原业务流。 | 删除 `-dontobfuscate`；保留 `-keep @android.webkit.JavascriptInterface *` 即可，其余类交由 R8 混淆。 |
| `app/src/main/AndroidManifest.xml:30` `android:usesCleartextTraffic="true"` + network_security_config `cleartextTrafficPermitted="true"` | **P1** | **全量放行明文传输**：配合 MIXED_CONTENT_ALWAYS_ALLOW 与 no `onReceivedSslError`，中间人可轻易窃取所有 API 请求与 token，即使部署时后端配了 HTTPS。 | 改为 `<base-config cleartextTrafficPermitted="false">` + 在 debug manifest (src/debug/) 覆盖为 true 仅开发期使用；同时实现 `network_securityConfig` 仅对内部测试网段允许。 |
| `network_security_config.xml:1-8` 未配置 `<domain-config>` | **P2** | **仅有一个 base-config**，无 pin，无域名限定，也无证书固定（certificate pinning）。 | 生产 at minimum 对自家域名配置 `<trust-anchors><certificates src="system"/><certificates src="user"/></trust-anchors>`，并考虑 OkHttp / volley 侧做 SPKI pin。 |
| `file_paths.xml:4-5` `<cache-path path="." />` / `<files-path path="." />` | **P1** | **暴露整个 cache + files 目录给 FileProvider**：content:// 访问权限可被授权Intent传递，也可通过被劫持的 WebView 间接读取。 | 如前 1 条，缩小到 `cache-path path="camera/"`、`files-path path="photos/"`。 |
| `build.gradle.kts:20` `isMinifyEnabled = true` 但未引用 R8/shrinkResources | **P2** | `shrinkResources` 未开启，`isMinifyEnabled` 仅靠 proguard-rules.pro，而后者已全部 `-dontobfuscate`。 | 启用 `isShrinkResources = true` + `debuggableVariants []`，默认模板同段。 |
| `MainActivity.kt` 未重建 `onReceivedSslError` | **P2** | 无显式 handler（当前是默认行为，WebView 继续加载导致 accepted）；但配合前述全 HTTP 放行，等同于允许 SSL stripping。 | 显式覆写: `if (request.url.host.endsWith("my-domain")) handler.proceed() else handler.cancel()`，并在生产 manifest 禁用 cleartext。 |
| `MainActivity.kt` 无 `launchMode="singleTask"` deep-link 验证 | **P2** | scheme://deep link 未在 manifest 显式限制，任意三方 App 可发动 intent。 | 如无需 deep-link 提供支持，移除所有 `android.intent.action.VIEW` intent-filter；如需，加 `android:scheme="https"` + `android:host="clglxt.example.com"`。 |
| `ServerConfig.kt` 无 URL 认证校验 | **P2** | `normalize()` 仅在格式层面，允许用户任意指定 `http://evil.com` 作为 server，目前只是 UX 便利但有工程上钓鱼风险。 | 至少保留历史最近 3 个合法 URL 白名单，或要求预配置里内置公钥指纹校验；无必要则 `setServerUrl` 触发 `confirm("确认切换？")` 提示。 |

### 未发现的问题（正面）
- 无硬编码 API Key / 密码（`ServerConfig.kt` 仅存储 URL，且 URL 由用户输入）
- 未发现 `setAllowUniversalAccessFromFileURLs` / `setSavePassword` 等危险 default。正确禁用了 `setGeolocationEnabled`、`setDisplayingWebContentUsingNewRendererByDefault`等未调用。
- `allowBackup="true"` 是默认值（非新增暴露）
- `App.kt` 空实现（未泄漏）

---

## 三、部署 (server/deploy + root)

| 文件:行号 | 严重度 | 问题 | 修复建议 |
|---|---|---|---|
| `deploy/start.sh:21` `workers = 6` 与 `start.sh:15` `workers = 2` 和 `gunicorn.conf.py:12` `workers = 2` 不一致 | **P2** | 代码库存在两份 worker 数不一致配置，在高配机器用 6 worker 但 pool_size=15、max_overflow=20 + PG max_connections=200 ≈ (6+1)* (15+20) = 245 > 200 PG 上限。 | 统一为一份权威配置。base：worker=2 (或 2/4 档)，pool_size = worker*4，PG max_connections=200 → 总连接需求 <120。开启 PgBouncer。 |
| `start.sh:30` `exec ... --access-logfile /dev/stdout` | **P2** | `exec` 前台模式，systemd 通过 `Type=notify` 已捕获，但若直接后台 `nohup &` 会丢失控制权；与 `clglxt-web.service` 中 `Type=notify` 重复功能。 | 业务稳定执行原样，确保 `exec` 不被 `>>log &` 替代。 |
| `deploy/init-postgres.sh:73-90` PostgreSQL 内存参数 `shared_buffers = 1GB` 在 4G 机器上合理，但 `max_connections=200` 高峰 + 业务 6gunicorn worker × 25 conn = 150，加上 PgBouncer/随机其它应用可能枯竭。 | **P2** | 并发估算狭窄，缺 PgBouncer 情境下直接 passthrough。默认不安装 pgbouncer，仅 echo 提示。 | ① 部署脚本 by default 装 pgbouncer；② `max_connections` 降至 120-150，靠 pgbouncer pool 到 1000+ 客户连接。 |
| `deploy/init-postgres.sh:26` `DB_PASS=${CLGLXT_DB_PASS:-}` 为空时 openssl 生成，但未强制密码策略 | **P2** | 若部署者手动导出 `CLGLXT_DB_PASS=123`，弱密码不拒绝。 | 增加 `if [[ "${#DB_PASS}" -lt 12 ]]; then error "min 12 chars"` 前置校验。 |
| `deploy/nginx-clglxt.conf:57` 缺少 `Content-Security-Policy`、`Permissions-Policy`、`Cross-Origin-*` 头 | **P1** | 已设 HSTS/X-Content-Type/X-Frame/Referrer-Policy，XSS 仍可能通过未严 CSP 注入内联脚本；跨源请求可读取 /api/ 任何内容（CORS 默认未启用但缺 COOP/COEP）。 | 添加完整 CSP：`Content-Security-Policy: default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data: blob:; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'`。删除 style 的 `'unsafe-inline'` 若可接受）。 |
| `deploy/nginx-clglxt.conf:40` `ssl_ciphers` 未禁 TLS1.2 弱套件（保留 `AES128-GCM-SH256`） | **P2** | 未排除 `CBC` 与 `RSA` 无前向保密套件，且 `ssl_prefer_server_ciphers off`。 | 改为 `ssl_protocols TLSv1.2 TLSv1.3; ssl_ciphers 'ECDHE-ECDSA-AES128-GCM-SHA256:ECDHE-RSA-AES128-GCM-SHA256:...'; ssl_prefer_server_ciphers on;`。 |
| `deploy/nginx-clglxt.conf:94-97` `valid_referers` 反盗链含 `clglxt.example.com` 硬编码域名 | **P2** | 部署时若不修改域名，防盗链仅拦自家站，真实域名访问也会被挡。 | 把域名抽为变量 `set $server_name 'clglxt.example.com';`，让 `valid_referers` 用 `$server_name`。 |
| `deploy/nginx-clglxt.conf:88-92` `/data/` 文件目录反代 alias 未校验 path traversal | **P1** | `alias /var/www/clglxt/data/;` 若后端允许 `%2e%2e/`，alias 固定 root 后 path 拼接可能能读系统；且 `location ~* \.(php|py|sh|cgi|pl)$ { deny all; }` blacklist 防御薄弱。 | 改为 `location /data/ { alias ...; location ~* \.(?:php[345]?|p?html?|s?html?|cgi|pl|jsp|asp|aspx|json|xml)$ { deny all; try_files /dev/null =403; } }`，即 whitelist allowed extensions (jpg/png/pdf...)。 |
| `deploy/init-postgres.sh:9` `if [[ -z "$DB_PASS" ]]`... openssl 生成 + 提示"请记录"但未写入 stdout | **P2** | 密码若不留盘（stdout 是临时）下个容器即丢失。 | stdout 也 tee 到 `~/.clglxt/.pgpass`，并 `chmod 600`；或在脚本退出前 `echo "export CLGLXT_DB_PASS='${DB_PASS}'" >> ~/.bashrc`。 |
| `deploy/init-postgres.sh:69-72` 使用 heredoc SQL 注入 `${DB_USER}` `${DB_PASS}` / `${DB_NAME}` | **P1** | 变量直接拼入 SQL 源码；`${DB_USER}` 若含单引号可闭合注入（虽然脚本入口做 opensql 随机 24 字节 hex，路径可控，但 `CLGLXT_DB_USER` 可环境传入）。 | 用 `quote_ident()` / 函数参数替代；或使用 psql 的 `-v` 参数赋值：`psql -v user="$DB_USER" -c "CREATE ROLE :'user' ..."`。 |
| `deploy/deploy.sh:39-44` `alembic revision --autogenerate` 跳过失败时部署挂起 | **P2** | `2>/dev/null || true` 隐藏失败，后续 `upgrade head` 也会失败，但脚本 continue。 | 把 `|| true` 改成专门的 abort path；或至少把 stderr 输出到 `\e[31m` |
| `deploy/deploy.sh:57-59` systemctl restart nginx / redis 失败后无 rollback | **P2** | 任一服务 start/restart 错误导致脚本继续，用户对 502 莫名。 | 加 `|| { echo "restart failed"; exit 1; }` 或提供 healthcheck 后退出 1。 |
| `deploy/rollback.sh:2` 未做任何身份鉴权谁都能执行；`psql -h 127.0.0.1 -U clglxt -d clglxt -f -` 使用 pipe 但未设 `PGPASSWORD` | **P1** | **任何本地用户** 拿到备份文件都能直接执行 DB 还原；若未在服务容器中 `PGPASSWORD` 环境变量则命令失败；或须先导入。 | （1）脚本开头 `if [[ $EUID -ne 0 ]]; then sudo "$0" "$@"; exit $?; fi`；（2）导出 `PGPASSWORD` 从 `.env` 读取。 |
| `server/.env.example:38` `CLGLXT_DATABASE_URL=postgresql+psycopg2://clglxt:changeme@...` | **P2** | 示例中 `changeme` 密码占位仍被 deploy 中的 `init-postgres.sh` 空默认逻辑跳过；但新手以此提交 repo。 | `.gitignore` 没在 workspace 内看到所列 `.env` 条目；检查是否误提交。 |
| `scripts/smoke.sh:17` `df ... > 1024` 单位是 KB 非 MB，剩余 1MB 也算充足 | **P2** | 脚本比较 `[ "$FREE" -gt 1024 ]` 实际 > 1MB 即 OK，意义不明。 | 把 1024 改 `1024*1024`（即 1GB）。 |
| `server/start.sh:13` `RUN_USER=${RUN_USER:-www}` 设置后未 `exec gunicorn` 切换用户 | **P2** | 变量定义后仍 `exec` 当前用户（root/实际用户）。 | 要么 `RUN_USER` 启动脚本前设用户若 root 使用 `sudo -u $RUN_USER ...`，要么删掉该变量。 |
| `server/gunicorn.conf.py` 未配置 `limit_request_line` / `limit_request_fields` | **P2** | 长 URI / 多 header 攻击无节流。 | 添加 `limit_request_line 4094`、`limit_request_fields 100`、`limit_request_field_size 8190`。 |

### 未发现的问题（正面）
- systemd unit `clglxt-web.service` 已开启 `NoNewPrivileges=true`、`PrivateTmp=true`、`ProtectSystem=full`、`ProtectHome=true`，纵深防御做得好。
- nginx 反对 proxy 参数完整；隐藏文件 `/\\. ` 拦截；Gzip 开启；SSL 路径规范；`proxy_intercept_errors off` 避免暴露错误页。
- start.sh 已 `set -euo pipefail` 严格模式。
- `init-redis.sh` 极简单（与 Redis 安全无关，但有 `daemonize yes` 可能需配合 `rename-command`）；无认证。
- `init-postgres.sh` 密码用 `openssl rand -hex 24`（强）。

---

## 四、横向关注

| 关注点 | 现状 | 建议 |
|---|---|---|
| **CORS 依赖配置** | 前端 `vite.config.js` 反向代理已配置 `/api` 转发，生产 Nginx 直接反代，默认同源，缺 `Access-Control-Allow-Origin`。 | 后端 `CLGLXT_CORS_ORIGINS` 留空默认（同源），若子域分离则填前端 origin 白名单。 |
| **依赖版本** | `requirements.txt` 版本均为 lower bound（`>=`），不锁版，生产可能意外升级。 | 生成 `requirements-freeze.txt` 或用 poetry / `pip-tools` lock；Pin 关键（fastapi, sqlalchemy, pyjwt, celery）。 |
| **SSRF** | 后端有 `CLGLXT_SSRF_ALLOWED_HOSTS`，但前端未看到外链出站描述；部署 nginx 只反代本机，出口少。 | 保证后端 AI/爬虫类出站请求走该白名单 + 出口禁 169.254.0/16 和 10.0.0/8 内部 IP。 |
| **备份与权限** | `server/app/core/backup_engine.py` 与 DB schema 有 backup_* 表；前端 `system:setting_backup` 权限受控。 | 备份文件存储 `/var/backups/clglxt`（参考 rollback.sh）应加密＋自动清理，权限 600。 |
| **Mobile 加固** | 缺 root / emulator / debug 检测；proguard 未启用混淆。 | 开启 R8 混淆、做 SafetyNet 校验、在 `MainActivity.onCreate` 检测 isDebuggerConnected / android.os.Debug.isDebuggerConnected()。 |

---

## 五、风险评级汇总

| 维度 | P0 | P1 | P2 |
|---|---|---|---|
| 前端 | 0 | 4 (token storage、open redirect、:src authz、login password in URL) | 6 (CSRF、clglxt_user chrome、PhotoUpload data size、500 detail、SW non-precache、SW integrity) |
| 安卓 | 1 (`MIXED_CONTENT_ALWAYS_ALLOW`) | 4 (file allow、js-bridge、proguard -dontobfuscate、hardcoded URI scope / cleartext) | 10 |
| 部署/运维 | 0 | 2 (SQL injection via env vars、rollback.sh auth/pipe) | 9 |

---

## 六、Top-5 快速行动项（建议本周内完成）

1. **`android:usesCleartextTraffic="true"` + `MixedContentMode.ALWAYS_ALWAYS_ALLOW`** → 修复为仅 test build 开启，完全消除明文流量可被中间人注入 JavaScript 的可能。
2. **登录/改密接口 GET 传参** → 迁到 JSON body 提交，全面清理 Nginx、Chrome history 与代理 log 中的密码明文。
3. **`router.replace(redirect)` 白名单** → 2 行代码，阻止盗号/跳转钓鱼。
4. **`MIXED_CONTENT_ALWAYS_ALLOW` + `addJavascriptInterface("Android")` 限制加载域** → 2 行 WebViewClient 覆写。
5. **`frontend nginx` 补齐 CSP 与关闭 PHP/Python 执行** → 数千行前端 XSS 攻击面收拢到 1 层。

---

*审计方法：静态源码审计（未运行 / 未动态 Fuzz）。报告基于当前输入；对于未覆盖的后端路径、Celery 任务/Docker/K8s 配置、CI/CD pipeline secrets 建议同样接受下一轮审计。*
