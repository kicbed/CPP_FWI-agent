#!/bin/bash
#
# FWI Agent 平台启动脚本
#
# 用法:
#   ./deploy/scripts/start.sh                    # 使用默认配置
#   ./deploy/scripts/start.sh --config config.json  # 使用指定配置
#

set -e

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
DEPLOY_DIR="$SCRIPT_DIR/.."
CONFIG_FILE="${1:-$DEPLOY_DIR/config/config.json}"

# 颜色输出
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

echo -e "${BLUE}=========================================="
echo "FWI Agent 平台启动"
echo -e "==========================================${NC}"

# 检查可执行文件
BIN_DIR="$PROJECT_ROOT/build/examples/ai_orchestrator"
if [ ! -f "$BIN_DIR/ai_orchestrator" ]; then
    echo -e "${RED}错误: 找不到可执行文件，请先编译项目${NC}"
    echo "  cd $PROJECT_ROOT && mkdir -p build && cd build && cmake .. && make -j"
    exit 1
fi

# 检查 Redis
echo -e "${YELLOW}[1/9] 检查 Redis...${NC}"
if ! redis-cli ping > /dev/null 2>&1; then
    echo -e "${YELLOW}Redis 未运行，尝试启动...${NC}"
    redis-server --daemonize yes
    sleep 1
    if ! redis-cli ping > /dev/null 2>&1; then
        echo -e "${RED}错误: 无法启动 Redis${NC}"
        exit 1
    fi
fi
echo -e "${GREEN}Redis 运行正常${NC}"

# 自动加载 .env 文件（如果存在）
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo -e "${GREEN}加载配置: $PROJECT_ROOT/.env${NC}"
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

# 检查环境变量 - 支持多种 LLM
LLM_PROVIDER="${LLM_PROVIDER:-qwen}"
API_KEY=""

case "$LLM_PROVIDER" in
    deepseek) API_KEY="${DEEPSEEK_API_KEY:-}" ;;
    qwen) API_KEY="${QWEN_API_KEY:-}" ;;
    openai) API_KEY="${OPENAI_API_KEY:-}" ;;
    local) API_KEY="not-needed" ;;
    *) API_KEY="${QWEN_API_KEY:-${DEEPSEEK_API_KEY:-${OPENAI_API_KEY:-}}}" ;;
esac

if [ -z "$API_KEY" ]; then
    echo -e "${RED}错误: 请设置 API Key${NC}"
    echo ""
    echo "方法 1: 使用 .env 文件（推荐）"
    echo "  cp .env.example .env && nano .env && source .env"
    echo ""
    echo "方法 2: 直接设置"
    echo "  export LLM_PROVIDER=deepseek"
    echo "  export DEEPSEEK_API_KEY=sk-你的密钥"
    exit 1
fi

echo -e "${GREEN}使用 LLM: $LLM_PROVIDER${NC}"

# 创建日志目录
LOG_DIR="$DEPLOY_DIR/logs"
mkdir -p "$LOG_DIR"
PID_DIR="$DEPLOY_DIR/pids"
mkdir -p "$PID_DIR"

# 配置
REGISTRY_PORT=8500
ORCHESTRATOR_PORT=5000
MATH_AGENT_PORT=5001
FWI_THEORY_PORT=5002
FWI_TEACHING_PORT=5003
GENERAL_RESEARCH_PORT=5004
CODE_AGENT_PORT=5010
EXPERIMENT_PLANNER_AGENT_PORT=5011
REDIS_HOST="127.0.0.1"
REDIS_PORT=6379

# MCP 配置
MCP_SERVER="$PROJECT_ROOT/mcp_server_integrated/build/mcp_server"
MCP_PLUGINS="$PROJECT_ROOT/mcp_server_integrated/build/plugins"
ENABLE_MCP="${ENABLE_MCP:-true}"
ENABLE_RAG="${ENABLE_RAG:-false}"
ROUTING_MODE="${ROUTING_MODE:-agent-rag}"
TOOL_CALLING_MODE="${TOOL_CALLING_MODE:-llm}"

# 启动服务
echo ""
echo -e "${BLUE}=========================================="
echo "启动服务"
echo -e "==========================================${NC}"

# 1. Registry Server
echo -e "${YELLOW}[2/9] 启动 Registry Server (port $REGISTRY_PORT)...${NC}"
"$BIN_DIR/ai_registry_server" $REGISTRY_PORT > "$LOG_DIR/registry.log" 2>&1 &
echo $! > "$PID_DIR/registry.pid"
sleep 1
echo -e "${GREEN}Registry Server 启动完成${NC}"

# MCP 参数
MCP_ARGS=""
if [ "$ENABLE_MCP" = "true" ] && [ -f "$MCP_SERVER" ]; then
    MCP_ARGS="--enable-mcp --mcp-server $MCP_SERVER --mcp-args -p,$MCP_PLUGINS"
    echo -e "${GREEN}MCP 已启用${NC}"
fi

# RAG 参数
RAG_ARGS=""
if [ "$ENABLE_RAG" = "true" ] && [ -n "$DASHSCOPE_API_KEY" ]; then
    RAG_ARGS="--enable-rag --rag-top-k ${RAG_TOP_K:-5} --rag-threshold ${RAG_THRESHOLD:-0.3}"
    echo -e "${GREEN}RAG 已启用${NC}"
fi

# 2. Math Agent
echo -e "${YELLOW}[3/9] 启动 Math Agent (port $MATH_AGENT_PORT)...${NC}"
"$BIN_DIR/ai_math_agent" math-1 $MATH_AGENT_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT $MCP_ARGS $RAG_ARGS \
  > "$LOG_DIR/math_agent.log" 2>&1 &
echo $! > "$PID_DIR/math_agent.pid"
sleep 1
echo -e "${GREEN}Math Agent 启动完成${NC}"

# 3. FWI Theory Agent
echo -e "${YELLOW}[4/9] 启动 FWI Theory Agent (port $FWI_THEORY_PORT)...${NC}"
"$BIN_DIR/ai_fwi_theory_agent" fwi-theory-1 $FWI_THEORY_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT \
  > "$LOG_DIR/fwi_theory_agent.log" 2>&1 &
echo $! > "$PID_DIR/fwi_theory_agent.pid"
sleep 1
echo -e "${GREEN}FWI Theory Agent 启动完成${NC}"

# 4. FWI Teaching Agent
echo -e "${YELLOW}[5/9] 启动 FWI Teaching Agent (port $FWI_TEACHING_PORT)...${NC}"
"$BIN_DIR/ai_fwi_teaching_agent" fwi-teaching-1 $FWI_TEACHING_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT \
  > "$LOG_DIR/fwi_teaching_agent.log" 2>&1 &
echo $! > "$PID_DIR/fwi_teaching_agent.pid"
sleep 1
echo -e "${GREEN}FWI Teaching Agent 启动完成${NC}"

# 5. General Research Agent
echo -e "${YELLOW}[6/9] 启动 General Research Agent (port $GENERAL_RESEARCH_PORT)...${NC}"
"$BIN_DIR/ai_general_research_agent" general-research-1 $GENERAL_RESEARCH_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT \
  > "$LOG_DIR/general_research_agent.log" 2>&1 &
echo $! > "$PID_DIR/general_research_agent.pid"
sleep 1
echo -e "${GREEN}General Research Agent 启动完成${NC}"

# 6. Code Agent
echo -e "${YELLOW}[7/9] 启动 Code Agent (port $CODE_AGENT_PORT)...${NC}"
"$BIN_DIR/ai_code_agent" code-agent-1 $CODE_AGENT_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT --project-root "$PROJECT_ROOT" \
  > "$LOG_DIR/code_agent.log" 2>&1 &
echo $! > "$PID_DIR/code_agent.pid"
sleep 1
echo -e "${GREEN}Code Agent 启动完成${NC}"

# 7. Experiment Planner Agent
echo -e "${YELLOW}[8/9] 启动 Experiment Planner Agent (port $EXPERIMENT_PLANNER_AGENT_PORT)...${NC}"
"$BIN_DIR/ai_experiment_planner_agent" experiment-planner-1 $EXPERIMENT_PLANNER_AGENT_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT --algorithm-dir "$PROJECT_ROOT/resources/algorithms" \
  > "$LOG_DIR/experiment_planner_agent.log" 2>&1 &
echo $! > "$PID_DIR/experiment_planner_agent.pid"
sleep 1
echo -e "${GREEN}Experiment Planner Agent 启动完成${NC}"

# 8. Orchestrator
echo -e "${YELLOW}[9/9] 启动 Orchestrator (port $ORCHESTRATOR_PORT)...${NC}"
"$BIN_DIR/ai_orchestrator" orch-1 $ORCHESTRATOR_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT $MCP_ARGS $RAG_ARGS \
  > "$LOG_DIR/orchestrator.log" 2>&1 &
echo $! > "$PID_DIR/orchestrator.pid"
sleep 1
echo -e "${GREEN}Orchestrator 启动完成${NC}"

# 完成
echo ""
echo -e "${BLUE}=========================================="
echo "系统启动完成!"
echo -e "==========================================${NC}"
echo ""
echo "服务地址:"
echo "  Registry:              http://localhost:$REGISTRY_PORT"
echo "  Orchestrator:          http://localhost:$ORCHESTRATOR_PORT"
echo "  Math Agent:            http://localhost:$MATH_AGENT_PORT"
echo "  FWI Theory Agent:      http://localhost:$FWI_THEORY_PORT"
echo "  FWI Teaching Agent:    http://localhost:$FWI_TEACHING_PORT"
echo "  General Research Agent: http://localhost:$GENERAL_RESEARCH_PORT"
echo "  Code Agent:            http://localhost:$CODE_AGENT_PORT"
echo "  Experiment Planner:    http://localhost:$EXPERIMENT_PLANNER_AGENT_PORT"
echo ""
echo "使用方式:"
echo "  # 交互式客户端"
echo "  $BIN_DIR/ai_client http://localhost:$ORCHESTRATOR_PORT"
echo ""
echo "  # curl 测试"
echo "  curl -X POST http://localhost:$ORCHESTRATOR_PORT/ -H 'Content-Type: application/json' -d '{...}'"
echo ""
echo "查看日志:"
echo "  tail -f $LOG_DIR/orchestrator.log"
echo ""
echo "停止系统:"
echo "  $SCRIPT_DIR/stop.sh"
