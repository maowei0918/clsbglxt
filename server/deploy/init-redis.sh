#!/bin/bash
# Redis 启动脚本（轻量版）
set -e
cd "$(dirname "$0")"
redis-server /etc/redis/redis.conf --daemonize yes
echo "Redis 启动完成（端口 6379）"