#!/bin/bash
#
# FWI Agent gRPC 服务启动脚本
#
# 功能：
# 1. 启动 Embedding 服务
# 2. 启动 Agent 系统
# 3. 启动 gRPC Server
# 4. 启动 gRPC Client
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"

# 颜色
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
RED='\033[0;31m'
CYAN='\033[0;36m'
NC='\033[0m'

echo -e "${CYAN}"
echo "  ╔══════════════════════════════════════════════════════════════╗"
echo "  ║           FWI Agent gRPC 服务启动                           ║"
echo "  ╚══════════════════════════════════════════════════════════════╝"
echo -e "${NC}"

# 加载 .env
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${GREEN}[1/5] 加载配置...${NC}"
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
echo -e "${YELLOW}[2/5] 启动 Embedding 服务...${NC}"
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
echo -e "${YELLOW}[3/5] 启动 Agent 系统...${NC}"
"$PROJECT_ROOT/examples/ai_orchestrator/start_system.sh" 2>&1 | tail -3

# 启动 gRPC Server
echo -e "${YELLOW}[4/5] 启动 gRPC Server...${NC}"
nohup "$PROJECT_ROOT/build/server/rpc_server" \
    > "$PROJECT_ROOT/deploy/logs/grpc_server.log" 2>&1 &
echo $! > "$PROJECT_ROOT/deploy/pids/grpc_server.pid"
sleep 2

if ss -tlnp | grep -q ":50051"; then
    echo -e "${GREEN}  ✓ gRPC Server 启动成功 (端口 50051)${NC}"
else
    echo -e "${RED}  ✗ gRPC Server 启动失败${NC}"
fi

echo ""
echo -e "${CYAN}=========================================="
echo "系统启动完成!"
echo -e "==========================================${NC}"
echo ""
echo "服务地址:"
echo "  Orchestrator:  http://localhost:5000"
echo "  gRPC Server:   localhost:50051"
echo ""
echo "使用方式:"
echo "  HTTP 客户端:   ./build/examples/ai_orchestrator/ai_client http://localhost:5000"
echo "  gRPC 客户端:   ./build/client/rpc_client"
echo ""
echo "停止系统:       ./deploy/scripts/stop.sh"
echo ""

# 启动 gRPC Client（前台）
echo -e "${YELLOW}[5/5] 启动 gRPC Client...${NC}"
echo ""
"$PROJECT_ROOT/build/client/rpc_client"
