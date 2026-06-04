#!/bin/bash
#
# FWI Agent 平台状态检查脚本
#

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/.."
PID_DIR="$DEPLOY_DIR/pids"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m'

echo -e "${BLUE}=========================================="
echo "FWI Agent 平台状态"
echo -e "==========================================${NC}"
echo ""

# 检查 Redis
echo -e "${YELLOW}Redis:${NC}"
if redis-cli ping > /dev/null 2>&1; then
    echo -e "  ${GREEN}运行中${NC}"
else
    echo -e "  ${RED}未运行${NC}"
fi

echo ""

# 检查各服务
echo -e "${YELLOW}服务状态:${NC}"
services=("registry" "math_agent" "fwi_theory_agent" "fwi_teaching_agent" "general_research_agent" "orchestrator")
ports=(8500 5001 5002 5003 5004 5000)

for i in "${!services[@]}"; do
    name="${services[$i]}"
    port="${ports[$i]}"
    pid_file="$PID_DIR/$name.pid"

    if [ -f "$pid_file" ]; then
        pid=$(cat "$pid_file")
        if kill -0 "$pid" 2>/dev/null; then
            echo -e "  $name (port $port): ${GREEN}运行中${NC} (PID: $pid)"
        else
            echo -e "  $name (port $port): ${RED}已停止${NC}"
        fi
    else
        echo -e "  $name (port $port): ${RED}未启动${NC}"
    fi
done

echo ""

# 检查 Registry
echo -e "${YELLOW}注册的 Agent:${NC}"
agents=$(curl -s http://localhost:8500/v1/agent/cards 2>/dev/null | python3 -c "
import json, sys
try:
    data = json.load(sys.stdin)
    for card in data.get('cards', []):
        print(f\"  - {card['agent_id']}: {card['name']}\")
except:
    print('  无法连接到 Registry')
" 2>/dev/null)

if [ -n "$agents" ]; then
    echo "$agents"
else
    echo -e "  ${RED}无法获取${NC}"
fi

echo ""
