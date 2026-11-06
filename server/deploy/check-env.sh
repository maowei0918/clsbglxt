#!/usr/bin/env bash
# ============================================================
# clglxt 环境预检脚本
# 用途：部署前检测环境，只提醒，不自动安装
# 用法：bash deploy/check-env.sh
# ============================================================
set -uo pipefail

GRN=$'\033[32m'; YEL=$'\033[33m'; RST=$'\033[0m'
RED=$'\033[31m'
OK=0; WARN=0; FAIL=0
MISSING=()

pass() { echo "  ${GRN}✓${RST} $1"; ((OK++)); }
warn() { echo "  ${YEL}!${RST} $1"; ((WARN++)); }
fail() { echo "  ${RED}✗${RST} $1"; ((FAIL++)); MISSING+=("$2"); }

need() { command -v "$1" >/dev/null 2>&1; }

echo "============================================"
echo " clglxt 环境预检"
echo "============================================"

# ── 系统 ──
echo
echo "[系统]"
if [[ -f /etc/debian_version ]]; then
    pass "Debian $(cat /etc/debian_version)"
else
    warn "非 Debian 系（$(uname -s)），apt 命令可能不适用"
fi
if [[ $EUID -eq 0 ]]; then pass "root 权限"
else
    if command -v sudo >/dev/null 2>&1; then fail "非 root 运行（有 sudo 可用）" ""
    else fail "需要 root 权限，且 sudo 不可用" "sudo"; fi
fi
if pidof systemd >/dev/null 2>&1; then pass "systemd 运行中"
else warn "无 systemd（容器环境？）—— 服务需手动前台启动"; fi

# ── Python ──
echo
echo "[Python]"
if need python3; then
    pass "python3 $(python3 --version 2>&1 | awk '{print $2}')"
    if python3 -c 'import venv' 2>/dev/null; then pass "venv 模块"
    else fail "python3-venv 缺失" "python3-venv"; fi
    if python3 -m pip --version >/dev/null 2>&1; then pass "pip"
    else fail "python3-pip 缺失" "python3-pip"; fi
    PV=$(python3 -c 'import sys;print(sys.version_info.minor)')
    [[ "$PV" -ge 9 ]] && pass "Python 版本满足 (>=3.9)" || fail "Python 需 3.9+，当前 3.${PV}" ""
else
    fail "python3 缺失" "python3"
fi

# ── PostgreSQL ──
echo
echo "[PostgreSQL]"
if need psql; then
    pass "psql $(psql --version 2>/dev/null | awk '{print $3}')"
    if pg_isready -q 2>/dev/null; then pass "PostgreSQL 服务运行中"
    else fail "PostgreSQL 未运行" "postgresql"; fi
else
    fail "PostgreSQL 未安装" "postgresql"
fi

# ── Redis ──
echo
echo "[Redis]"
if need redis-cli; then
    pass "redis-cli 已安装"
    if redis-cli ping 2>/dev/null | grep -q PONG; then pass "Redis 运行中"
    else fail "Redis 未运行" "redis-server"; fi
else
    fail "Redis 未安装" "redis-server"
fi

# ── Nginx ──
echo
echo "[Nginx]"
if need nginx; then
    pass "nginx $(nginx -v 2>&1 | awk -F/ '{print $2}')"
    if pgrep -x nginx >/dev/null 2>&1; then pass "Nginx 运行中"
    else warn "Nginx 已装但未运行（宝塔面板里启动即可）"; fi
else
    warn "Nginx 未检测到（宝塔面板通常自带，或用面板配置反代）"
fi

# ── 工具 ──
echo
echo "[工具]"
need openssl && pass "openssl $(openssl version 2>/dev/null | awk '{print $2}')" || fail "openssl 缺失" "openssl"
need curl && pass "curl" || warn "curl 未安装（非必需）"
need tar && pass "tar" || fail "tar 缺失" "tar"

# ── 端口占用 ──
echo
echo "[端口]"
for p in 5432 6379; do
    if (echo >/dev/tcp/127.0.0.1/$p) 2>/dev/null; then pass "端口 $p 在监听"
    else warn "端口 $p 未监听（对应服务可能没启动）"; fi
done
if (echo >/dev/tcp/127.0.0.1/8000) 2>/dev/null; then
    warn "端口 8000 已被占用（clglxt 默认用 8000，请确认是否旧进程）"
else pass "端口 8000 空闲"; fi

# ── 汇总 ──
echo
echo "============================================"
echo " 通过 $OK / 警告 $WARN / 失败 $FAIL"
echo "============================================"

if [[ $FAIL -gt 0 ]]; then
    echo
    echo "缺少以下组件："
    declare -A seen=()
    for p in "${MISSING[@]}"; do
        [[ -n "$p" && -z "${seen[$p]:-}" ]] && { seen[$p]=1; echo "  - $p"; }
    done
    echo
    echo "请手动安装（宝塔终端 root 执行）："
    declare -A seen=()
    for p in "${MISSING[@]}"; do
        [[ -n "$p" && -z "${seen[$p]:-}" ]] && { seen[$p]=1; echo "  apt install -y $p"; }
    done
    echo
    echo "安装完成后，重新运行：bash deploy/check-env.sh"
    exit 1
fi

echo "环境检查通过，可以执行：bash deploy/deploy.sh"
