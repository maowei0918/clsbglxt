#!/usr/bin/env bash
# ============================================================
# clglxt 回滚脚本
# 用法：bash rollback.sh <deploy_backup_dir> [--with-db]
#   默认只回滚代码（安全）。
#   --with-db 才额外用 db/ 里的 PG 备份还原数据库（破坏性，人工确认后加）。
# ============================================================
set -euo pipefail

BACKUP_DIR="${1:-/var/backups/clglxt}"
WITH_DB=0
[[ "${2:-}" == "--with-db" ]] && WITH_DB=1

if [[ ! -d "$BACKUP_DIR" ]]; then
    echo "✗ 备份目录不存在: $BACKUP_DIR" >&2
    exit 1
fi

echo "== 停止服务 =="
systemctl stop clglxt-web clglxt-worker clglxt-beat 2>/dev/null || true

# 还原目标目录：从已部署的 systemd unit 读 WorkingDirectory（真实部署路径），
# 而不是硬编码 /www/wwwroot/clglxt —— 部署到任意路径都能回滚。
PROJECT_ROOT="$(systemctl cat clglxt-web 2>/dev/null | awk -F= '/WorkingDirectory=/ {sub(/^WorkingDirectory=/,""); print; exit}')"
if [[ -z "$PROJECT_ROOT" ]]; then
    echo "⚠ 读不到 WorkingDirectory，回退到脚本所在 server/ 目录" >&2
    PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
echo "还原目标: $PROJECT_ROOT"

echo "== 还原代码 =="
cp -a "$BACKUP_DIR/server/"* "$PROJECT_ROOT/" 2>/dev/null || true
# 运行用户与 deploy.sh 保持一致（宝塔默认 www，可能是 root）
WEB_USER="$(systemctl show -p User --value clglxt-web 2>/dev/null)"
[[ -z "$WEB_USER" || "$WEB_USER" == "root" ]] && WEB_USER="www"
id "$WEB_USER" >/dev/null 2>&1 || WEB_USER="root"
chown -R "$WEB_USER":"$WEB_USER" "$PROJECT_ROOT" 2>/dev/null || true

echo "== 回滚数据库（PG 逻辑备份）=="
if [[ "$WITH_DB" == "1" ]]; then
    DB_DUMP="$BACKUP_DIR/db/clglxt_$(date +%Y%m%d).sql.gz"
    if [[ -f "$DB_DUMP" ]]; then
        echo "!! 破坏性操作：用 $DB_DUMP 覆盖当前数据库"
        gunzip -c "$DB_DUMP" | psql -h 127.0.0.1 -U clglxt -d clglxt -f -
    else
        echo "✗ 指定了 --with-db 但找不到 $DB_DUMP" >&2
        exit 1
    fi
else
    echo "  跳过（默认只回滚代码；需还原数据库请显式加 --with-db）"
fi

echo "== 启动服务 =="
systemctl start clglxt-web clglxt-worker clglxt-beat
systemctl restart nginx

echo "== 回滚完成 =="
echo "验证：curl -fsS https://clglxt.example.com/api/healthz"
