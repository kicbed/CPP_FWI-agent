#!/bin/bash

# AI Agent 系统停止脚本

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "停止 AI Agent 系统..."

# 停止所有服务
for pid_file in "$SCRIPT_DIR/pids"/*.pid; do
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo "停止进程 $pid ($(basename $pid_file .pid))"
            kill "$pid"
        fi
        rm -f "$pid_file"
    fi
done

echo "系统已停止"
