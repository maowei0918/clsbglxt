#!/usr/bin/env bash
# 复位 clglxt 本地测试残留（.env / .install_done 为安装向导产物，测试会重新生成）
# 用法：bash /vol1/1000/AI/octop/clglxt/server/tests/reset_install.sh
set -e
cd /vol1/1000/AI/octop/clglxt/server
rm -f .env data/.install_done
echo "reset: .env / data/.install_done removed"
