#!/usr/bin/env bash
# ============================================================
# clglxt PostgreSQL 初始化
# 用法：sudo bash init-postgres.sh
# 前置：已安装 PostgreSQL 15+（宝塔软件商店一键装）
# ============================================================
set -euo pipefail

DB_NAME="${CLGLXT_DB_NAME:-clglxt}"
DB_USER="${CLGLXT_DB_USER:-clglxt}"
DB_PASS="${CLGLXT_DB_PASS:-}"

if [[ -z "$DB_PASS" ]]; then
    DB_PASS="$(openssl rand -hex 24)"
    echo "== 自动生成数据库密码（请记录到 .env）=="
fi

# 安全：校验标识符与密码，防止 SQL 注入（值会被拼进 psql heredoc）
if [[ ! "$DB_NAME" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "错误：DB 名只能包含字母/数字/下划线，且不以数字开头：$DB_NAME" >&2
    exit 1
fi
if [[ ! "$DB_USER" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]]; then
    echo "错误：DB 用户名只能包含字母/数字/下划线，且不以数字开头：$DB_USER" >&2
    exit 1
fi
# 密码中的单引号转义（PG 字符串字面量：'' 表示 '）并拒绝控制字符
DB_PASS_SQL="${DB_PASS//\'/\'\'}"
if [[ "$DB_PASS" == *[$'\n\r\t']* ]]; then
    echo "错误：数据库密码不能包含换行/制表符" >&2
    exit 1
fi

echo "== 1. 创建数据库用户 =="
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
DO \$\$
BEGIN
    IF NOT EXISTS (SELECT FROM pg_roles WHERE rolname = '${DB_USER}') THEN
        CREATE ROLE ${DB_USER} LOGIN PASSWORD '${DB_PASS_SQL}';
    ELSE
        ALTER ROLE ${DB_USER} WITH LOGIN PASSWORD '${DB_PASS_SQL}';
    END IF;
END
\$\$;
SQL

echo "== 2. 创建数据库（UTF8，lc_collate 支持中文排序）=="
if ! sudo -u postgres psql -tAc "SELECT 1 FROM pg_database WHERE datname='${DB_NAME}'" | grep -q 1; then
    sudo -u postgres createdb -O "${DB_USER}" -E UTF8 --locale=C.UTF-8 "${DB_NAME}"
else
    echo "   数据库 ${DB_NAME} 已存在，跳过"
fi

echo "== 3. 授权 =="
sudo -u postgres psql -v ON_ERROR_STOP=1 <<SQL
GRANT ALL PRIVILEGES ON DATABASE ${DB_NAME} TO ${DB_USER};
\c ${DB_NAME}
GRANT ALL ON SCHEMA public TO ${DB_USER};
ALTER SCHEMA public OWNER TO ${DB_USER};
SQL

echo "== 4. 性能调优（按 4GB 内存机器；内存更大可等比放大）=="
PG_CONF="$(sudo -u postgres psql -tAc "SHOW config_file" | tr -d ' ')"
PG_VER="$(sudo -u postgres psql -tAc "SHOW server_version" | grep -oE '^[0-9]+' | head -1)"
echo "   检测到 PostgreSQL 主版本：${PG_VER}"

# 并发 100+ 调优：max_connections 200，连接池 + worker 不超过 60
# 若并发更高，建议加 pgbouncer（见 docs/memory-tuning.md）
cat >> "${PG_CONF}" <<'PGTUNE'

# ── clglxt 调优（并发 100+，4GB 内存档）──────────────────────
max_connections = 200
shared_buffers = 1GB
effective_cache_size = 3GB
work_mem = 16MB
maintenance_work_mem = 256MB
wal_buffers = 16MB
checkpoint_completion_target = 0.9
random_page_cost = 1.1
effective_io_concurrency = 200
default_statistics_target = 100
log_min_duration_statement = 1000
log_checkpoints = on
log_lock_waits = on
idle_in_transaction_session_timeout = 60s
statement_timeout = 30s
timezone = 'UTC'
PGTUNE

# 并发 100+ 时建议开启连接池（pgbouncer），这里仅提示
if [[ "${PG_VER:-0}" -ge 13 ]]; then
    echo "   ✓ PostgreSQL ${PG_VER} 支持 prepared 语句缓存（pgbouncer transaction 模式安全）"
fi

echo "== 5. 重启 PostgreSQL 生效 =="
systemctl restart postgresql
sleep 2

echo "== 6. 验证连接 =="
PGPASSWORD="${DB_PASS}" psql -h 127.0.0.1 -U "${DB_USER}" -d "${DB_NAME}" -c "SELECT version();" >/dev/null \
    && echo "   ✓ 连接成功"

cat <<EOF

============================================================
PostgreSQL 初始化完成！请把下面这行写入 server/.env：

CLGLXT_DATABASE_URL=postgresql+psycopg2://${DB_USER}:${DB_PASS}@127.0.0.1:5432/${DB_NAME}

============================================================
EOF
