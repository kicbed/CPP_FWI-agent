#!/bin/bash
#
# FWI Agent HTTP 服务启动脚本
#
# 功能：
# 1. 启动 Embedding 服务
# 2. 启动 Agent 系统
# 3. 启动 HTTP 客户端
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════════════════╗"
echo "  ║           FWI Agent HTTP 服务启动                           ║"
echo "  ╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# 加载 .env
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${GREEN}[1/4] 加载配置...${NC}"
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

# 检查 API Key
if [ -z "$DEEPSEEK_API_KEY" ] && [ -z "$QWEN_API_KEY" ]; then
    echo -e "${RED}错误: 请在 .env 中设置 API Key${NC}"
    exit 1
fi

# 启动 Embedding
echo -e "${YELLOW}[2/4] 启动 Embedding 服务...${NC}"
EMBEDDING_MODEL="${LOCAL_EMBEDDING_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
pkill -f embedding_server 2>/dev/null || true
sleep 1
nohup python3 "$PROJECT_ROOT/deploy/scripts/embedding_server.py" \
    --model "$EMBEDDING_MODEL" \
    --port 6000 \
    > "$PROJECT_ROOT/deploy/logs/embedding.log" 2>&1 &
echo $! > "$PROJECT_ROOT/deploy/pids/embedding.pid"
sleep 8

if curl -s http://localhost:6000/health > /dev/null 2>&1; then
    echo -e "${GREEN}  ✓ Embedding 服务启动成功${NC}"
else
    echo -e "${RED}  ✗ Embedding 服务启动失败${NC}"
    exit 1
fi

# 启动 Agent 系统
echo -e "${YELLOW}[3/4] 启动 Agent 系统...${NC}"
"$PROJECT_ROOT/examples/ai_orchestrator/start_system.sh" 2>&1 | tail -3

echo ""
echo -e "${CYAN}=========================================="
echo "系统启动完成!"
echo -e "==========================================${NC}"
echo ""
echo "服务地址:"
echo "  Orchestrator:  http://localhost:5000"
echo ""
echo "停止系统:       ./deploy/scripts/stop.sh"
echo ""

# 启动 HTTP Client（前台）
echo -e "${YELLOW}[4/4] 启动 HTTP Client...${NC}"
echo ""
"$PROJECT_ROOT/build/examples/ai_orchestrator/ai_client" http://localhost:5000
