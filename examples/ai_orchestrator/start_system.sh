#!/bin/bash
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
BIN_DIR="$PROJECT_ROOT/build/examples/ai_orchestrator"

# 自动加载 .env 文件（如果存在）
if [ -f "$PROJECT_ROOT/.env" ]; then
    echo "加载配置: $PROJECT_ROOT/.env"
    set -a
    source "$PROJECT_ROOT/.env"
    set +a
fi

if [ ! -f "$BIN_DIR/ai_orchestrator" ]; then
    echo "错误: 找不到可执行文件，请先编译项目"
    exit 1
fi

REGISTRY_PORT=8500
ORCHESTRATOR_PORT=5000
MATH_AGENT_PORT=5001
FWI_THEORY_PORT=5002
FWI_TEACHING_PORT=5003
GENERAL_RESEARCH_PORT=5004
REDIS_HOST="127.0.0.1"
REDIS_PORT=6379

MCP_SERVER="$PROJECT_ROOT/mcp_server_integrated/build/mcp_server"
MCP_PLUGINS="$PROJECT_ROOT/mcp_server_integrated/build/plugins"
ENABLE_MCP="${ENABLE_MCP:-false}"
EMBEDDING_URL="${LOCAL_EMBEDDING_URL:-http://localhost:6000}"
EMBEDDING_MODEL="${LOCAL_EMBEDDING_MODEL:-Qwen/Qwen3-Embedding-0.6B}"

# 自动检测 LLM 提供商和 API Key
LLM_PROVIDER="${LLM_PROVIDER:-qwen}"
API_KEY=""

case "$LLM_PROVIDER" in
    deepseek)
        API_KEY="${DEEPSEEK_API_KEY:-}"
        ;;
    qwen)
        API_KEY="${QWEN_API_KEY:-}"
        ;;
    openai)
        API_KEY="${OPENAI_API_KEY:-}"
        ;;
    local)
        API_KEY="not-needed"
        ;;
    *)
        # 兼容旧配置：检查 QWEN_API_KEY
        API_KEY="${QWEN_API_KEY:-${DEEPSEEK_API_KEY:-${OPENAI_API_KEY:-}}}"
        ;;
esac

if [ -z "$API_KEY" ]; then
    echo "错误: 请设置 API Key"
    echo ""
    echo "方法 1: 使用 .env 文件（推荐）"
    echo "  cp .env.example .env"
    echo "  nano .env  # 填入你的密钥"
    echo "  source .env"
    echo ""
    echo "方法 2: 直接设置环境变量"
    echo "  export LLM_PROVIDER=deepseek"
    echo "  export DEEPSEEK_API_KEY=sk-你的密钥"
    echo ""
    echo "方法 3: 使用通义千问"
    echo "  export QWEN_API_KEY=sk-你的密钥"
    echo ""
    exit 1
fi

echo "使用 LLM: $LLM_PROVIDER"

mkdir -p "$SCRIPT_DIR/logs" "$SCRIPT_DIR/pids"

# 清理旧进程，确保端口可用
echo "清理旧进程..."
pkill -9 ai_registry_server 2>/dev/null || true
pkill -9 ai_math_agent 2>/dev/null || true
pkill -9 ai_fwi_theory_agent 2>/dev/null || true
pkill -9 ai_fwi_teaching_agent 2>/dev/null || true
pkill -9 ai_general_research_agent 2>/dev/null || true
pkill -9 ai_orchestrator 2>/dev/null || true
sleep 1
# 等待端口释放
for port in $REGISTRY_PORT $ORCHESTRATOR_PORT $MATH_AGENT_PORT $FWI_THEORY_PORT $FWI_TEACHING_PORT $GENERAL_RESEARCH_PORT; do
    while ss -tlnp 2>/dev/null | grep -q ":$port "; do
        echo "  等待端口 $port 释放..."
        sleep 1
    done
done
sleep 1

echo "=========================================="
echo "AI Agent 系统启动"
echo "=========================================="

echo "[1/6] 启动 Registry Server..."
"$BIN_DIR/ai_registry_server" $REGISTRY_PORT > "$SCRIPT_DIR/logs/registry.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/registry.pid"
sleep 1
echo "Registry Server 启动完成 (端口: $REGISTRY_PORT)"

MCP_ARGS=""
if [ "$ENABLE_MCP" == "true" ] && [ -f "$MCP_SERVER" ]; then
    MCP_ARGS="--enable-mcp --mcp-server $MCP_SERVER --mcp-args -p,$MCP_PLUGINS"
    echo "MCP 已启用: $MCP_SERVER"
fi

echo "[2/6] 启动 Math Agent..."
"$BIN_DIR/ai_math_agent" math-1 $MATH_AGENT_PORT http://localhost:$REGISTRY_PORT $API_KEY --redis-host $REDIS_HOST --redis-port $REDIS_PORT $MCP_ARGS > "$SCRIPT_DIR/logs/math_agent.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/math_agent.pid"
sleep 1
echo "Math Agent 启动完成 (端口: $MATH_AGENT_PORT)"

echo "[3/6] 启动 FWI Theory Agent..."
"$BIN_DIR/ai_fwi_theory_agent" fwi-theory-1 $FWI_THEORY_PORT http://localhost:$REGISTRY_PORT $API_KEY --redis-host $REDIS_HOST --redis-port $REDIS_PORT > "$SCRIPT_DIR/logs/fwi_theory_agent.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/fwi_theory_agent.pid"
sleep 1
echo "FWI Theory Agent 启动完成 (端口: $FWI_THEORY_PORT)"

echo "[4/6] 启动 FWI Teaching Agent..."
"$BIN_DIR/ai_fwi_teaching_agent" fwi-teaching-1 $FWI_TEACHING_PORT http://localhost:$REGISTRY_PORT $API_KEY --redis-host $REDIS_HOST --redis-port $REDIS_PORT > "$SCRIPT_DIR/logs/fwi_teaching_agent.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/fwi_teaching_agent.pid"
sleep 1
echo "FWI Teaching Agent 启动完成 (端口: $FWI_TEACHING_PORT)"

echo "[5/6] 启动 General Research Agent..."
"$BIN_DIR/ai_general_research_agent" general-research-1 $GENERAL_RESEARCH_PORT http://localhost:$REGISTRY_PORT $API_KEY --redis-host $REDIS_HOST --redis-port $REDIS_PORT > "$SCRIPT_DIR/logs/general_research_agent.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/general_research_agent.pid"
sleep 1
echo "General Research Agent 启动完成 (端口: $GENERAL_RESEARCH_PORT)"

echo "[6/6] 启动 Orchestrator..."
"$BIN_DIR/ai_orchestrator" orch-1 $ORCHESTRATOR_PORT http://localhost:$REGISTRY_PORT $API_KEY --redis-host $REDIS_HOST --redis-port $REDIS_PORT $MCP_ARGS > "$SCRIPT_DIR/logs/orchestrator.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/orchestrator.pid"
sleep 1
echo "Orchestrator 启动完成 (端口: $ORCHESTRATOR_PORT)"

echo ""
echo "=========================================="
echo "系统启动完成!"
echo "=========================================="
echo ""
echo "服务地址:"
echo "  Registry:              http://localhost:$REGISTRY_PORT"
echo "  Orchestrator:          http://localhost:$ORCHESTRATOR_PORT"
echo "  Math Agent:            http://localhost:$MATH_AGENT_PORT"
echo "  FWI Theory Agent:      http://localhost:$FWI_THEORY_PORT"
echo "  FWI Teaching Agent:    http://localhost:$FWI_TEACHING_PORT"
echo "  General Research Agent: http://localhost:$GENERAL_RESEARCH_PORT"
echo ""
echo "停止系统: $SCRIPT_DIR/stop_system.sh"
