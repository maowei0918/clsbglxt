#!/usr/bin/env bash
# ============================================================
# clglxt 站点启动脚本（轻量版 — 2 worker）
# 宝塔「站点→Python项目」或 systemd 直接调用
# ============================================================
set -euo pipefail
cd "$(dirname "$0")"

RUN_USER="${RUN_USER:-www}"
VENV="$(pwd)/.venv"

# 激活虚拟环境（若有）
[ -d "$VENV" ] && source "$VENV/bin/activate"

# 首次依赖自检
if ! python -c "import fastapi, uvicorn, sqlalchemy, psycopg2" >/dev/null 2>&1; then
    pip install -r requirements.txt -i https://pypi.tuna.tsinghua.edu.cn/simple
fi

# 已装过才跑迁移（幂等）
[ -f data/.install_done ] && { alembic upgrade head 2>/dev/null || echo "[warn] 迁移跳过"; }

# ── 轻量配置：2 worker + DB pool 10 ──
exec gunicorn app.main:app \
    --bind 127.0.0.1:8000 \
    --workers 2 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 90 \
    --worker-connections 100 \
    --max-requests 1000 \
    --max-requests-jitter 50 \
    --access-logfile /dev/stdout \
    --error-logfile /dev/stderr
