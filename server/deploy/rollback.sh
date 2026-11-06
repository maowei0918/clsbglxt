#!/usr/bin/env bash
# ============================================================
# clglxt 回滚脚本
# 用法：bash rollback.sh <deploy_backup_dir>
# ============================================================
set -euo pipefail

BACKUP_DIR="${1:-/var/backups/clglxt}"

if [[ ! -d "$BACKUP_DIR" ]]; then
    echo "✗ 备份目录不存在: $BACKUP_DIR" >&2
    exit 1
fi

echo "== 停止服务 =="
systemctl stop clglxt-web clglxt-worker clglxt-beat 2>/dev/null || true

echo "== 还原代码 =="
cp -a "$BACKUP_DIR/server/"* "/www/wwwroot/clglxt/server/" 2>/dev/null || true
chown -R www:www /www/wwwroot/clglxt/server

echo "== 回滚数据库（PG 逻辑备份）=="
if [[ -f "$BACKUP_DIR/db/clglxt_$(date +%Y%m%d).sql.gz" ]]; then
    gunzip -c "$BACKUP_DIR/db/clglxt_$(date +%Y%m%d).sql.gz" | \
        psql -h 127.0.0.1 -U clglxt -d clglxt -f -
fi

echo "== 启动服务 =="
systemctl start clglxt-web clglxt-worker clglxt-beat
systemctl restart nginx

echo "== 回滚完成 =="
echo "验证：curl -fsS https://clglxt.example.com/api/healthz"
