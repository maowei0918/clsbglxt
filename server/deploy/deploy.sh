#!/usr/bin/env bash
# ============================================================
# clglxt 宝塔一键部署脚本 v2
#   · 自动探测安装目录（上传到任意路径都能跑）
#   · 自动建 Python 虚拟环境 + 装依赖
#   · 自动渲染 systemd unit（路径/用户均为实际值）
#   · 自动配 nginx（可指定域名）
# 用法（宝塔终端，root 执行）：
#   cd /你上传的目录/server && bash deploy/deploy.sh
# ============================================================
set -euo pipefail

# ── 1. 定位项目根（脚本所在目录的上一级 = server/）──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
VENV="$PROJECT_ROOT/.venv"
LOG_DIR="/var/log/clglxt"
DATA_DIR="$PROJECT_ROOT/data"

# 运行用户：默认 www（宝塔标配），不存在则回退 root
WEB_USER="${CLGLXT_USER:-www}"
if ! id "$WEB_USER" >/dev/null 2>&1; then
    WEB_USER="root"
fi

echo "============================================"
echo " clglxt 部署"
echo " 安装目录 : $PROJECT_ROOT"
echo " 运行用户 : $WEB_USER"
echo " 日志目录 : $LOG_DIR"
echo "============================================"

[[ $EUID -eq 0 ]] || { echo "请用 root 运行：sudo bash deploy/deploy.sh"; exit 1; }

# ── 0. 环境预检（缺组件则停下，提醒手动安装）──
echo
echo "== 第一步：环境检测 =="
if ! bash "$SCRIPT_DIR/check-env.sh"; then
    echo
    echo "============================================"
    echo " 环境检测未通过，部署已中止。"
    echo " 请按上面的提示手动安装缺失组件，"
    echo " 装好后重新运行：bash deploy/deploy.sh"
    echo "============================================"
    exit 1
fi
echo "== ✓ 环境检测通过"

# ── 2. 目录 ──（权限放到最后统一处理，避免前端构建后再 chown 错乱）
mkdir -p "$LOG_DIR" "$DATA_DIR" "$DATA_DIR/uploads" "$DATA_DIR/backups"
echo "== ✓ 目录就绪"

# ── 2.5 目录与权限（在构建完成后统一处理，避免 node_modules 被 chown 成 root）──
chown -R "$WEB_USER":"$WEB_USER" "$PROJECT_ROOT" "$LOG_DIR" 2>/dev/null || true
chmod -R u+rwX,go+rX "$PROJECT_ROOT" 2>/dev/null || true
chmod 600 "$PROJECT_ROOT/.env" 2>/dev/null || true
echo "== ✓ 目录权限就绪"

# ── 3. Python 虚拟环境 ──
if [[ ! -x "$VENV/bin/python" ]]; then
    echo "== 创建虚拟环境 =="
    python3 -m venv "$VENV" || { echo "缺 python3-venv，请先：apt install -y python3-venv"; exit 1; }
fi
echo "== 安装依赖（首次约 2-5 分钟）=="
"$VENV/bin/pip" install -q --upgrade pip
"$VENV/bin/pip" install -q -r "$PROJECT_ROOT/requirements.txt"
echo "== ✓ 依赖安装完成"

# ── 3.5 前端构建（把 Vue 打包进 data/web/）──
# 注意：必须在权限处理之前，否则 node_modules 会被 chown 成 root 导致 www 跑不起来
FRONTEND_DIR="$(cd "$PROJECT_ROOT/.." && pwd)/frontend"
if [[ -d "$FRONTEND_DIR" ]]; then
    echo "== 构建前端（首次约 1-3 分钟）=="
    if ! command -v node >/dev/null 2>&1; then
        echo "!! 未找到 node，跳过前端构建。"
        echo "   若 data/web/ 已有打包产物可忽略；否则请先装 Node 18+ 再重跑。"
    else
        pushd "$FRONTEND_DIR" >/dev/null
        if [[ ! -d node_modules ]]; then
            npm install --silent --no-audit --no-fund
        fi
        npm run build
        popd >/dev/null
        echo "== ✓ 前端构建完成 → $PROJECT_ROOT/data/web"
    fi
else
    echo "== 未找到 frontend/ 目录，跳过前端构建"
fi

# ── 4. 生成 .env（若不存在）──
if [[ ! -f "$PROJECT_ROOT/.env" ]]; then
    JWT_SECRET="$(openssl rand -hex 32)"
    DB_PASS="$(openssl rand -hex 16)"
    cat > "$PROJECT_ROOT/.env" <<EOF
# 由 deploy.sh 自动生成 $(date '+%F %T')
CLGLXT_JWT_SECRET=$JWT_SECRET
CLGLXT_DATABASE_URL=postgresql+psycopg2://clglxt:${DB_PASS}@127.0.0.1:5432/clglxt
CLGLXT_REDIS_URL=redis://127.0.0.1:6379/0
CLGLXT_REDIS_CELERY_URL=redis://127.0.0.1:6379/1
CLGLXT_ENABLE_DOCS=false
EOF
    chmod 600 "$PROJECT_ROOT/.env"
    echo "== ✓ 已生成 .env（数据库密码：$DB_PASS）"
    echo "   ↑ 请记录，稍后创建 PG 用户时要用"
fi

# ── 5. 渲染 systemd unit（路径替换为实际值）──
echo "== 渲染 systemd 服务"
for svc in clglxt-web clglxt-worker clglxt-beat; do
    sed -e "s|__PROJECT_ROOT__|$PROJECT_ROOT|g" \
        -e "s|__WEB_USER__|$WEB_USER|g" \
        -e "s|__LOG_DIR__|$LOG_DIR|g" \
        "$PROJECT_ROOT/deploy/$svc.service" > "/etc/systemd/system/$svc.service"
    echo "   · $svc.service"
done
systemctl daemon-reload

# ── 6. nginx（可选：传域名则自动配）──
if [[ -n "${CLGLXT_DOMAIN:-}" ]]; then
    echo "== 配置 nginx：$CLGLXT_DOMAIN"

    # 后端 502 兜底引导页目录：优先用 nginx 自己的 html 目录（与后端进程解耦）
    # 兼容宝塔 / 手动装 nginx / 自定义路径
    NGINX_HTML="$(nginx -V 2>&1 | grep -oP '(?<=--prefix=)[^\s]+' || echo /usr/share/nginx)"
    if [[ -d "$NGINX_HTML/html" ]]; then
        FALLBACK_DIR="$NGINX_HTML/html/clglxt-fallback"
    elif [[ -d /usr/share/nginx/html ]]; then
        FALLBACK_DIR="/usr/share/nginx/html/clglxt-fallback"
    else
        FALLBACK_DIR="$PROJECT_ROOT/data/fallback"
        echo "   ! 未找到 nginx html 目录，fallback 放入 $FALLBACK_DIR"
    fi
    mkdir -p "$FALLBACK_DIR"
    if [[ -f "$PROJECT_ROOT/data/web/guide.html" ]]; then
        cp "$PROJECT_ROOT/data/web/guide.html" "$FALLBACK_DIR/guide.html"
        echo "   · 引导页 → $FALLBACK_DIR/guide.html"
    else
        echo "   ! 未找到 guide.html，502 时将显示 nginx 默认页"
    fi

    sed -e "s|clglxt.example.com|$CLGLXT_DOMAIN|g" \
        -e "s|/var/www/clglxt/data|$DATA_DIR|g" \
        -e "s|/var/log/clglxt|$LOG_DIR|g" \
        -e "s|/www/server/nginx/html/clglxt-fallback|$FALLBACK_DIR|g" \
        "$PROJECT_ROOT/deploy/nginx-clglxt.conf" > "/etc/nginx/sites-available/clglxt.conf"
    ln -sf /etc/nginx/sites-available/clglxt.conf /etc/nginx/sites-enabled/clglxt.conf
    sed -e "s|/var/www/clglxt/data|$DATA_DIR|g" \
        "$PROJECT_ROOT/deploy/nginx-proxy-params.conf" > /etc/nginx/clglxt_proxy_params.conf
    nginx -t && systemctl reload nginx
    echo "== ✓ nginx 已配置"
else
    echo "== 跳过 nginx（未设置 CLGLXT_DOMAIN）"
    echo "   宝塔里手动配置反向代理到 127.0.0.1:8000 即可"
fi

# ── 7. 启动服务 ──
echo "== 启动 clglxt 服务"
systemctl enable --now clglxt-web clglxt-worker clglxt-beat
sleep 3
systemctl --no-pager --lines=0 status clglxt-web || true

echo
echo "============================================"
echo " ✓ 部署完成"
echo "============================================"
echo " 下一步："
echo "  1. 先在宝塔创建 PostgreSQL 数据库："
echo "     数据库名 clglxt / 用户 clglxt / 密码见 .env"
echo "  2. 浏览器打开你的域名 → 自动进入安装向导"
echo "  3. 安装向导里填数据库信息 + 管理员账号"
echo
echo " 常用命令："
echo "   日志：journalctl -u clglxt-web -f"
echo "   重启：systemctl restart clglxt-web clglxt-worker clglxt-beat"
