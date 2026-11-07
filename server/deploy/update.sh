#!/usr/bin/env bash
# ============================================================
# clglxt 代码更新脚本（生产）
# 安全更新：备份 → 更新 → 迁移 → 重启 → 健康检查 → 失败自动回滚
#
# 用法：
#   cd /www/wwwroot/clglxt/server && sudo bash deploy/update.sh
#
# 更新来源二选一：
#   - 默认：从 git remote 拉取（需服务器已 git clone 本仓库）
#   - 或：把新代码覆盖到 server/ 后再跑本脚本（脚本只重启，不拉取）
#
# 环境变量：
#   CLGLXT_SKIP_BACKUP=1   跳过代码备份（不推荐）
# ============================================================
set -euo pipefail

# ── 定位真实部署目录：优先从 systemd 读，回退脚本位置 ──
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(systemctl cat clglxt-web 2>/dev/null | awk -F= '/WorkingDirectory=/ {sub(/^WorkingDirectory=/,""); print; exit}')"
[[ -z "$PROJECT_ROOT" ]] && PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
[[ -d "$PROJECT_ROOT" ]] || { echo "✗ 部署目录不存在: $PROJECT_ROOT" >&2; exit 1; }
cd "$PROJECT_ROOT"

BACKUP_ROOT="${CLGLXT_BACKUP_DIR:-/var/backups/clglxt}"
STAMP="$(date +%Y%m%d_%H%M%S)"
CODE_BACKUP="$BACKUP_ROOT/code_$STAMP"
PREV_HASH="$(git rev-parse HEAD 2>/dev/null || echo 'unknown')"

echo "=============================================="
echo " clglxt 更新"
echo " 部署目录 : $PROJECT_ROOT"
echo " 前一版本 : $PREV_HASH"
echo " 备份目录 : $CODE_BACKUP"
echo "=============================================="

[[ $EUID -eq 0 ]] || { echo "请用 root 运行：sudo bash deploy/update.sh"; exit 1; }

# ── 回滚代码的公共函数 ──────────────────────────────────────────
# 用「覆盖式还原」（tar 管道），绝不用 rsync --delete：
# 备份时排除了 data/ 与 .venv/，--delete 会把上传件、备份件、
# 整个虚拟环境连带删掉 —— 这是旧版最危险的缺陷。
rollback_code() {
    [[ -d "$CODE_BACKUP/server" ]] || { echo "⚠ 无代码备份，跳过代码回滚" >&2; return 1; }
    (cd "$CODE_BACKUP/server" && tar -cf - .) | (cd "$PROJECT_ROOT" && tar -xf -) \
        || { echo "✗ 代码回滚失败" >&2; return 1; }
    if [[ -f "$CODE_BACKUP/server/.env" ]]; then
        cp -f "$CODE_BACKUP/server/.env" "$PROJECT_ROOT/.env"
        chmod 600 "$PROJECT_ROOT/.env"
    fi
    echo "   ✓ 代码与 .env 已还原"
}

# ── 依赖检查：rsync 用于代码备份/回滚，缺失会导致静默失败 ──
if ! command -v rsync >/dev/null 2>&1; then
    echo "✗ 缺少 rsync（备份/回滚依赖）。请先：apt install -y rsync" >&2
    exit 1
fi

# ── 0. 更新前：确认服务在跑（更新前必须健康）──
if ! systemctl is-active --quiet clglxt-web; then
    echo "⚠ 更新前服务未运行，仍继续（可能刚首次部署）"
fi

# ── 0.5 .env 权限守护（每次更新复查，防止手工编辑后放宽）──
if [ -f .env ]; then
    chmod 600 .env
    echo "== ✓ .env 权限已收紧为 600"
fi

# ── 1. 备份当前代码（防止更新失败后无法回滚）──
if [[ "${CLGLXT_SKIP_BACKUP:-0}" != "1" ]]; then
    mkdir -p "$CODE_BACKUP"
    # 备份源码（排除虚拟环境/数据/日志/构建产物）
    rsync -a --exclude='.venv/' --exclude='data/' --exclude='__pycache__/' \
          --exclude='*.pyc' --exclude='.git/' ./ "$CODE_BACKUP/server/" 2>/dev/null \
      || cp -a "$PROJECT_ROOT" "$CODE_BACKUP/server/"
    # 备份 .env（关键配置，单独存）
    [ -f .env ] && cp .env "$CODE_BACKUP/server/.env" && chmod 600 "$CODE_BACKUP/server/.env"
    echo "== ✓ 已备份代码 → $CODE_BACKUP"
else
    echo "== ⚠ 已跳过代码备份（CLGLXT_SKIP_BACKUP=1）"
fi

# ── 2. 拉取/同步新代码 ──
if git rev-parse --git-dir >/dev/null 2>&1; then
    echo "== 从 git 拉取更新..."
    git fetch --prune
    git checkout .        # 丢弃工作区改动
    git pull --ff-only
    NEW_HASH="$(git rev-parse HEAD)"
    echo "   当前版本: $NEW_HASH"
else
    echo "== 未发现 git 仓库，跳过拉取（假设新代码已覆盖到本目录）"
    NEW_HASH="已覆盖"
fi

# ── 3. 装依赖（保证新代码依赖齐全）──
VENV="$PROJECT_ROOT/.venv"
if [[ -x "$VENV/bin/pip" ]]; then
    echo "== 同步依赖..."
    "$VENV/bin/pip" install -q -r requirements.txt
fi

# ── 4. 数据库迁移（增量，幂等）──
if [ -f data/.install_done ]; then
    echo "== 执行数据库迁移..."
    # 迁移前先做一次数据库备份：迁移失败时可以拿它恢复，
    # 而不是面对「代码回滚成功但 schema 已变」的组合故障。
    DB_BACKUP="$BACKUP_ROOT/db_pre_migrate_$STAMP.dump"
    mkdir -p "$BACKUP_ROOT"
    pg_dump --format=custom --no-owner -f "$DB_BACKUP" \
        "$(awk -F= '/^CLGLXT_DATABASE_URL=/ {sub(/^CLGLXT_DATABASE_URL=/,""); print; exit}' .env)" \
        2>/dev/null && echo "   已备份数据库 → $DB_BACKUP" \
        || echo "   ⚠ 数据库预备份失败，继续（迁移失败将无法回滚 DB）"

    # 迁移失败是更新失败的高发点：失败则回滚代码并退出
    if ! "$VENV/bin/alembic" -c alembic.ini upgrade head; then
        echo "✗ 迁移失败，回滚代码并退出"
        echo "  数据库可能已部分迁移。DB 备份在：$DB_BACKUP"
        echo "  恢复命令（详见 rollback.sh --with-db）："
        echo "    pg_restore --clean --if-exists --no-owner -d <DB> $DB_BACKUP"
        rollback_code || true
        exit 1
    fi
fi

# ── 5. 重启服务（优雅重载）──
echo "== 重启服务..."
systemctl restart clglxt-web clglxt-worker clglxt-beat
sleep 3

# ── 6. 健康检查（失败自动回滚）──
if systemctl is-active --quiet clglxt-web && curl -fsS -o /dev/null http://127.0.0.1:8000/api/healthz; then
    echo "== ✓ 更新成功，服务健康"
    echo "================================================"
    echo " 完成。前一版本: $PREV_HASH → 当前: $NEW_HASH"
    echo " 回滚代码（如需）：bash deploy/rollback.sh $CODE_BACKUP"
    echo "   注：回滚默认只还原代码，不动数据库；确需还原 DB 再加 --with-db"
    echo "================================================"
    exit 0
else
    echo "✗ 更新后健康检查失败，自动回滚代码"
    rollback_code || true
    systemctl restart clglxt-web clglxt-worker clglxt-beat
    echo "已回滚到备份。人工排查：journalctl -u clglxt-web -n 50"
    exit 1
fi
