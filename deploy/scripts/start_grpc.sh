#!/bin/bash
#
# FWI Agent gRPC 服务启动脚本
#
# 架构:
#   gRPC Client ──gRPC──> gRPC Server (:50051) ──A2A/HTTP──> Orchestrator (:5000) ──> Agents
#
# 功能：
# 1. 启动 Embedding 服务
# 2. 启动 Agent 系统（Orchestrator :5000）
# 3. 启动 gRPC Server（:50051，代理 Orchestrator）
# 4. 启动 gRPC Client（连接 :50051）
#

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
echo -e "${YELLOW}[2/5] 启动 Embedding 服务（首次加载模型约需 15 秒）...${NC}"
EMBEDDING_MODEL="${LOCAL_EMBEDDING_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
pkill -f embedding_server 2>/dev/null || true
sleep 1
mkdir -p "$PROJECT_ROOT/deploy/logs" "$PROJECT_ROOT/deploy/pids"
nohup python3 "$PROJECT_ROOT/deploy/scripts/embedding_server.py" \
    --model "$EMBEDDING_MODEL" \
    --port 6000 \
    > "$PROJECT_ROOT/deploy/logs/embedding.log" 2>&1 &
echo $! > "$PROJECT_ROOT/deploy/pids/embedding.pid"

# 等待服务启动（最多 60 秒）
echo -ne "  等待模型加载"
for i in $(seq 1 30); do
    sleep 2
    if curl -s http://localhost:6000/health > /dev/null 2>&1; then
        echo ""
        echo -e "${GREEN}  ✓ Embedding 服务启动成功${NC}"
        break
    fi
    echo -ne "."
done

# 最终检查
if ! curl -s http://localhost:6000/health > /dev/null 2>&1; then
    echo ""
    echo -e "${RED}  ✗ Embedding 服务启动失败${NC}"
    echo -e "${RED}  查看日志: tail deploy/logs/embedding.log${NC}"
    exit 1
fi

# 启动 Agent 系统（Orchestrator :5000）
echo -e "${YELLOW}[3/5] 启动 Agent 系统...${NC}"
bash "$PROJECT_ROOT/examples/ai_orchestrator/start_system.sh" 2>&1

# 启动 gRPC Server（:50051）
echo -e "${YELLOW}[4/5] 启动 gRPC Server...${NC}"

# 清理旧 gRPC server 进程
fuser -k 50051/tcp 2>/dev/null || true
sleep 1

# 启动 gRPC Server（trap 忽略 SIGINT，防止 Ctrl+C 杀死 server）
nohup bash -c "trap '' INT TERM HUP; exec '$PROJECT_ROOT/build/server/rpc_server'" \
    > "$PROJECT_ROOT/deploy/logs/grpc_server.log" 2>&1 &
echo $! > "$PROJECT_ROOT/deploy/pids/grpc_server.pid"

# 等待 gRPC server 就绪（最多 10 秒）
echo -ne "  等待 gRPC Server 启动"
for i in $(seq 1 10); do
    sleep 1
    if ss -tlnp 2>/dev/null | grep -q ":50051 "; then
        echo ""
        echo -e "${GREEN}  ✓ gRPC Server 启动成功 (端口 50051)${NC}"
        break
    fi
    echo -ne "."
done

# 最终检查
if ! ss -tlnp 2>/dev/null | grep -q ":50051 "; then
    echo ""
    echo -e "${RED}  ✗ gRPC Server 启动失败！${NC}"
    echo -e "${RED}  查看日志: tail deploy/logs/grpc_server.log${NC}"
    exit 1
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
echo "停止系统:       ./examples/ai_orchestrator/stop_system.sh"
echo ""

# 启动 gRPC Client（前台，通过 gRPC 协议连接 :50051）
echo -e "${YELLOW}[5/5] 启动 gRPC Client...${NC}"
echo ""
"$PROJECT_ROOT/build/client/grpc_ai_client" localhost:50051
