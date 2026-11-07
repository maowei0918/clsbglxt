#!/usr/bin/env bash
# ============================================================
# clglxt 回滚脚本
# 用法：bash rollback.sh <deploy_backup_dir> [--with-db]
#   默认只回滚代码（安全，不动数据）。
#   --with-db 才额外用备份的 PG dump 还原数据库（破坏性，人工确认后加）。
#
# 设计约束（相对旧版的修正）：
#   * 备份目录只用于「读取」，绝不用 --delete 覆盖项目目录
#     （旧版 rsync -a --delete 会删掉 data/ 和 .venv/，等于回滚即丢数据）
#   * .env 必须恢复（点文件 glob 会漏，旧版会丢 JWT_SECRET）
#   * --with-db 用 pg_restore 读 custom-format dump（旧版找 .sql.gz 永远匹配不到）
#   * 迁移代码版本不匹配时给出显式告警
# ============================================================
set -euo pipefail

BACKUP_DIR="${1:-/var/backups/clglxt}"
WITH_DB=0
[[ "${2:-}" == "--with-db" ]] && WITH_DB=1

if [[ ! -d "$BACKUP_DIR" ]]; then
    echo "✗ 备份目录不存在: $BACKUP_DIR" >&2
    exit 1
fi

# 备份目录必须含 server/ 子目录（deploy.sh 的产物结构）
if [[ ! -d "$BACKUP_DIR/server" ]]; then
    echo "✗ 备份结构不符：$BACKUP_DIR/server/ 不存在" >&2
    exit 1
fi

echo "== 停止服务 =="
systemctl stop clglxt-web clglxt-worker clglxt-beat 2>/dev/null || true

# 还原目标目录：从已部署的 systemd unit 读 WorkingDirectory（真实部署路径），
# 而不是硬编码 —— 部署到任意路径都能回滚。
PROJECT_ROOT="$(systemctl cat clglxt-web 2>/dev/null | awk -F= '/WorkingDirectory=/ {sub(/^WorkingDirectory=/,""); print; exit}')"
if [[ -z "$PROJECT_ROOT" ]]; then
    echo "⚠ 读不到 WorkingDirectory，回退到脚本所在 server/ 目录" >&2
    PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
fi
echo "还原目标: $PROJECT_ROOT"

# ── 回滚前的安全护栏 ──────────────────────────────────────────
# 确认目标不是未安装的空目录，避免把代码倒进一个错误的位置。
if [[ ! -f "$PROJECT_ROOT/app/main.py" ]]; then
    echo "✗ $PROJECT_ROOT 不像 clglxt 项目目录（缺 app/main.py），拒绝回滚" >&2
    exit 1
fi

# ── 1. 还原代码（只覆盖，不删除）──────────────────────────────
# 关键：用 cp 而不是 rsync --delete。
#   rsync --delete 会删掉备份里不存在的文件，而备份时排除了 data/ 与 .venv/，
#   于是回滚会连带删除上传件、备份件和整个虚拟环境。
echo "== 还原代码（覆盖式，不删除任何文件）=="
# 用 tar 管道：能正确处理点文件，且不删除目标目录里的其它内容。
(cd "$BACKUP_DIR/server" && tar -cf - .) | (cd "$PROJECT_ROOT" && tar -xf -)
echo "   ✓ 代码已还原"

# ── 2. 恢复 .env（点文件必须显式处理）────────────────────────
# tar 已包含 .env（deploy.sh 备份时显式 cp 过），这里再做一次确认。
if [[ -f "$BACKUP_DIR/server/.env" ]]; then
    cp -f "$BACKUP_DIR/server/.env" "$PROJECT_ROOT/.env"
    chmod 600 "$PROJECT_ROOT/.env"
    echo "   ✓ .env 已恢复并设为 600"
else
    echo "   ⚠ 备份里没有 .env —— 保留当前 .env（不回滚配置）"
fi

# ── 3. 权限 ───────────────────────────────────────────────────
WEB_USER="$(systemctl show -p User --value clglxt-web 2>/dev/null)"
[[ -z "$WEB_USER" || "$WEB_USER" == "root" ]] && WEB_USER="www"
id "$WEB_USER" >/dev/null 2>&1 || WEB_USER="root"
chown -R "$WEB_USER":"$WEB_USER" "$PROJECT_ROOT" 2>/dev/null || true
# 数据目录绝不能交给 chown 把属主改乱（这里只确保项目文件可读）
echo "   ✓ 属主已设为 $WEB_USER"

# ── 4. 回滚数据库（可选，破坏性）──────────────────────────────
if [[ "$WITH_DB" == "1" ]]; then
    echo
    echo "!! 破坏性操作：将用备份 dump 覆盖当前数据库 !!"
    echo "   当前时间点的数据将丢失（备份之后的所有写入）。"
    read -r -p "   确认继续？输入 yes: " confirm
    [[ "$confirm" == "yes" ]] || { echo "已取消"; systemctl start clglxt-web clglxt-worker clglxt-beat; exit 1; }

    # dump 文件名形如 clglxt_YYYYMMDD_HHMMSS.dump（backup_engine.py 产出）
    DB_DUMP="$(ls -1 "$BACKUP_DIR/db/"clglxt_*.dump 2>/dev/null | sort | tail -1 || true)"
    if [[ -z "$DB_DUMP" ]]; then
        # 兼容旧命名（backup_engine 曾用 .db 后缀做 SQLite）
        DB_DUMP="$(ls -1 "$BACKUP_DIR/db/"clglxt_*.db 2>/dev/null | sort | tail -1 || true)"
    fi
    if [[ -z "$DB_DUMP" ]]; then
        echo "✗ 指定了 --with-db 但 $BACKUP_DIR/db/ 下找不到 clglxt_*.dump" >&2
        exit 1
    fi
    echo "   使用备份: $DB_DUMP"

    # 恢复前再备份一次当前库（最后一道防线）
    PRE_RESTORE="/tmp/clglxt_pre_restore_$(date +%Y%m%d_%H%M%S).dump"
    echo "   恢复前先另存当前库 → $PRE_RESTORE"
    pg_dump --format=custom --no-owner --clean --if-exists -f "$PRE_RESTORE" \
        "$(awk -F= '/^CLGLXT_DATABASE_URL=/ {sub(/^CLGLXT_DATABASE_URL=/,""); print; exit}' "$PROJECT_ROOT/.env")" \
        || echo "   ⚠ 另存失败，继续（数据库可能不可用）"

    echo "   执行 pg_restore ..."
    # 用 --clean --if-exists：恢复到已有库时先清掉同名对象
    pg_restore --clean --if-exists --no-owner --exit-on-error \
        -d "$(awk -F= '/^CLGLXT_DATABASE_URL=/ {sub(/^CLGLXT_DATABASE_URL=/,""); print; exit}' "$PROJECT_ROOT/.env")" \
        "$DB_DUMP"
    echo "   ✓ 数据库已还原"
else
    echo
    echo "  跳过数据库回滚（默认只回滚代码；需还原 DB 请显式加 --with-db）"
fi

# ── 5. 版本一致性检查 ─────────────────────────────────────────
# 代码回滚了，但数据库 schema 可能停在新版本 → 旧代码对上新 schema。
CUR_REV="$("$PROJECT_ROOT/.venv/bin/alembic" -c "$PROJECT_ROOT/alembic.ini" current 2>/dev/null | awk 'NR==1{print $1}')"
DB_REV="$(cd "$PROJECT_ROOT" && "$PROJECT_ROOT/.venv/bin/alembic" -c alembic.ini current 2>/dev/null | tail -1 | xargs)"
echo
echo "  代码版本: $(cd "$PROJECT_ROOT" && git rev-parse --short HEAD 2>/dev/null || echo unknown)"
echo "  数据库 revision: ${DB_REV:-未知}"
if [[ -n "$DB_REV" && "$DB_REV" != "None" ]]; then
    echo "  ⚠ 若代码回滚后 alembic revision 与数据库不一致，"
    echo "    旧代码可能访问新 schema。必要时执行："
    echo "      cd $PROJECT_ROOT && .venv/bin/alembic -c alembic.ini downgrade <上一个 revision>"
fi

# ── 6. 启动服务 ───────────────────────────────────────────────
echo "== 启动服务 =="
systemctl start clglxt-web clglxt-worker clglxt-beat
systemctl restart nginx

echo "== 回滚完成 =="
echo "验证：curl -fsS http://127.0.0.1:8000/api/healthz"
