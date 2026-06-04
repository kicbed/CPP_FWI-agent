#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "停止 AI Agent 系统..."

# 停止 Agent 服务
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

# 停止 Embedding 服务（检查多个可能的 PID 文件位置）
for embedding_pid_file in \
    "$SCRIPT_DIR/pids/embedding.pid" \
    "$PROJECT_ROOT/deploy/pids/embedding.pid"; do
    if [ -f "$embedding_pid_file" ]; then
        pid=$(cat "$embedding_pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo "停止 Embedding 服务 (PID: $pid)"
            kill "$pid"
        fi
        rm -f "$embedding_pid_file"
    fi
done

# 也尝试通过进程名停止
pkill -f "embedding_server.py" 2>/dev/null && echo "停止 Embedding 服务 (pkill)"

echo "系统已停止"
