#!/bin/bash
#
# FWI Agent 系统停止脚本
#
# 功能：
# 1. 停止所有 Agent 服务
# 2. 停止 Embedding 服务
# 3. 清理 PID 文件
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

echo "=========================================="
echo "停止 FWI Agent 系统"
echo "=========================================="

# 颜色
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m'

# 1. 停止 PID 文件中记录的进程
echo -e "${YELLOW}[1/3] 停止 Agent 服务...${NC}"
for pid_file in "$SCRIPT_DIR/pids"/*.pid; do
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        name=$(basename "$pid_file" .pid)
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "  停止 $name (PID: $pid)"
            kill "$pid" 2>/dev/null
            sleep 1
            # 如果还没停止，强制杀死
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null
            fi
        fi
        rm -f "$pid_file"
    fi
done

# 2. 停止 Embedding 服务
echo -e "${YELLOW}[2/3] 停止 Embedding 服务...${NC}"

# 检查所有可能的 PID 文件位置
for pid_file in \
    "$SCRIPT_DIR/pids/embedding.pid" \
    "$PROJECT_ROOT/deploy/pids/embedding.pid" \
    "$PROJECT_ROOT/deploy/logs/embedding.pid"; do
    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "  停止 Embedding (PID: $pid)"
            kill "$pid" 2>/dev/null
            sleep 1
            if kill -0 "$pid" 2>/dev/null; then
                kill -9 "$pid" 2>/dev/null
            fi
        fi
        rm -f "$pid_file"
    fi
done

# 也通过端口查找并停止
embedding_pid=$(ss -tlnp | grep ":6000 " | grep -oP 'pid=\d+' | grep -oP '\d+')
if [ -n "$embedding_pid" ]; then
    echo -e "  停止 Embedding (端口 6000, PID: $embedding_pid)"
    kill "$embedding_pid" 2>/dev/null
    sleep 1
    if kill -0 "$embedding_pid" 2>/dev/null; then
        kill -9 "$embedding_pid" 2>/dev/null
    fi
fi

# 3. 通过进程名强制停止（兜底）
echo -e "${YELLOW}[3/3] 清理残留进程...${NC}"
for pattern in "ai_orchestrator" "ai_math_agent" "ai_registry_server" "ai_fwi_theory" "ai_fwi_teaching" "ai_general_research" "embedding_server"; do
    pids=$(pgrep -f "$pattern" 2>/dev/null)
    if [ -n "$pids" ]; then
        echo -e "  强制停止 $pattern (PIDs: $pids)"
        echo "$pids" | xargs kill -9 2>/dev/null
    fi
done

# 清理 PID 文件
rm -f "$SCRIPT_DIR/pids"/*.pid
rm -f "$PROJECT_ROOT/deploy/pids"/*.pid

sleep 1

echo ""
echo -e "${GREEN}=========================================="
echo "系统已停止"
echo -e "==========================================${NC}"
