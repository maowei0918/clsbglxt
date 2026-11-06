#!/usr/bin/env bash
# ============================================================
# clglxt 站点启动脚本（部署版）
# 宝塔「站点 → Python 项目」或 systemd 可调用本脚本
# ============================================================
set -euo pipefail

cd "$(dirname "$0")"

# 站点运行用户（宝塔默认 www；systemd 里也配 www）
RUN_USER="${RUN_USER:-www}"

# 虚拟环境（宝塔「一键部署」若未用 venv，改指到全局 Python）
VENV="$(pwd)/.venv"
if [ -d "$VENV" ]; then
    source "$VENV/bin/activate"
fi

# 依赖检查（首次运行自动装）
if ! python -c "import fastapi, uvicorn, sqlalchemy, psycopg2" >/dev/null 2>&1; then
    pip install -r requirements.txt
fi

# 启动前迁移（幂等，已装才执行）
if [ -f data/.install_done ]; then
    alembic upgrade head 2>/dev/null || echo "[warn] 迁移跳过（可能未装 alembic）"
fi

# 以 www 身份运行（宝塔站点目录已属 www，直接前台跑）
exec gunicorn app.main:app \
    --bind 127.0.0.1:8000 \
    --workers 6 \
    --worker-class uvicorn.workers.UvicornWorker \
    --timeout 90 \
    --access-logfile /dev/stdout \
    --error-logfile /dev/stderr
