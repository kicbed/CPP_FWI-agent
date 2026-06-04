#!/bin/bash
#
# FWI Agent 平台停止脚本
#

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/.."
PID_DIR="$DEPLOY_DIR/pids"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

echo -e "${YELLOW}停止 FWI Agent 平台...${NC}"

# 停止所有服务
for pid_file in "$PID_DIR"/*.pid; do
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        name=$(basename "$pid_file" .pid)
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "${YELLOW}停止 $name (PID: $pid)${NC}"
            kill "$pid"
        fi
        rm -f "$pid_file"
    fi
done

# 也尝试通过进程名停止 Embedding 服务
if pkill -f "embedding_server.py" 2>/dev/null; then
    echo -e "${YELLOW}停止 Embedding 服务${NC}"
fi

echo -e "${GREEN}系统已停止${NC}"
