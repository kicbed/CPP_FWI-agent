#!/bin/bash

# AI Agent 系统启动脚本
# 启动顺序: Registry -> Math Agent -> Orchestrator

set -e

# 获取脚本所在目录
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
# 项目根目录
PROJECT_ROOT="$(cd "$SCRIPT_DIR/../.." && pwd)"
# 可执行文件目录
BIN_DIR="$PROJECT_ROOT/build/examples/ai_orchestrator"

# 检查可执行文件是否存在
if [ ! -f "$BIN_DIR/ai_registry_server" ]; then
    echo "错误: 找不到可执行文件，请先编译项目"
    echo "  cd $PROJECT_ROOT && mkdir -p build && cd build && cmake .. && make -j"
    exit 1
fi

# 配置
REGISTRY_PORT=8500
ORCHESTRATOR_PORT=5000
MATH_AGENT_PORT=5001
FWI_THEORY_AGENT_PORT=5002
FWI_TEACHING_AGENT_PORT=5003
GENERAL_RESEARCH_AGENT_PORT=5004
REDIS_HOST="127.0.0.1"
REDIS_PORT=6379

# MCP Server 配置
MCP_SERVER_PATH="$PROJECT_ROOT/mcp_server_integrated/build/mcp_server"
MCP_PLUGINS_PATH="$PROJECT_ROOT/mcp_server_integrated/build/plugins"
MCP_LOGS_PATH="$PROJECT_ROOT/mcp_server_integrated/build/logs"
ENABLE_MCP="${ENABLE_MCP:-false}"

# RAG-MCP 配置 (智能工具选择)
ENABLE_RAG="${ENABLE_RAG:-false}"
RAG_TOP_K="${RAG_TOP_K:-5}"
RAG_THRESHOLD="${RAG_THRESHOLD:-0.3}"
# 升级开关配置：默认保持旧逻辑不变
ROUTING_MODE="${ROUTING_MODE:-fixed}"                  # fixed | agent-rag
TOOL_CALLING_MODE="${TOOL_CALLING_MODE:-rule}"         # rule | llm
ENABLE_AGENT_RAG="${ENABLE_AGENT_RAG:-false}"
ENABLE_LLM_TOOL_CALLING="${ENABLE_LLM_TOOL_CALLING:-false}"
DASHSCOPE_API_KEY="${DASHSCOPE_API_KEY:-}"

# API Key (请替换为你的 API Key)
API_KEY="${QWEN_API_KEY:-sk-your-api-key}"

# 检查 API Key
if [ "$API_KEY" == "sk-your-api-key" ]; then
    echo "警告: 请设置 QWEN_API_KEY 环境变量"
    echo "export QWEN_API_KEY=sk-xxx"
    exit 1
fi

# 创建日志和 PID 目录
mkdir -p "$SCRIPT_DIR/logs" "$SCRIPT_DIR/pids"

echo "=========================================="
echo "AI Agent 系统启动"
echo "=========================================="

# 1. 启动 Registry Server
echo "[1/3] 启动 Registry Server..."
"$BIN_DIR/ai_registry_server" $REGISTRY_PORT > "$SCRIPT_DIR/logs/registry.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/registry.pid"
sleep 1
echo "Registry Server 启动完成 (端口: $REGISTRY_PORT)"

# MCP 参数
MCP_ARGS=""
if [ "$ENABLE_MCP" == "true" ] && [ -f "$MCP_SERVER_PATH" ]; then
    # 创建 MCP 日志目录
    mkdir -p "$MCP_LOGS_PATH"
    MCP_ARGS="--enable-mcp --mcp-server $MCP_SERVER_PATH --mcp-args -l,$MCP_LOGS_PATH,-p,$MCP_PLUGINS_PATH"
    echo "MCP 已启用: $MCP_SERVER_PATH"
    echo "MCP 插件目录: $MCP_PLUGINS_PATH"
    echo "MCP 日志目录: $MCP_LOGS_PATH"
fi

# RAG-MCP 参数 (智能工具选择)
RAG_ARGS=""
if [ "$ENABLE_RAG" == "true" ] && [ -n "$DASHSCOPE_API_KEY" ]; then
    RAG_ARGS="--enable-rag --rag-top-k $RAG_TOP_K --rag-threshold $RAG_THRESHOLD"
    echo "RAG-MCP 已启用: 智能工具选择"
    echo "  Top-K: $RAG_TOP_K"
    echo "  相似度阈值: $RAG_THRESHOLD"
elif [ "$ENABLE_RAG" == "true" ] && [ -z "$DASHSCOPE_API_KEY" ]; then
    echo "警告: ENABLE_RAG=true 但未设置 DASHSCOPE_API_KEY，RAG 功能将被禁用"
fi

# 2. 启动 Math Agent
echo "[2/4] 启动 Math Agent..."
"$BIN_DIR/ai_math_agent" math-1 $MATH_AGENT_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST \
  --redis-port $REDIS_PORT \
  --tool-calling-mode $TOOL_CALLING_MODE \
  --enable-llm-tool-calling $ENABLE_LLM_TOOL_CALLING \
  $MCP_ARGS $RAG_ARGS > "$SCRIPT_DIR/logs/math_agent.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/math_agent.pid"
sleep 1
echo "Math Agent 启动完成 (端口: $MATH_AGENT_PORT)"

# 3. 启动 FWI Theory Agent
echo "[3/4] 启动 FWI Theory Agent..."
"$BIN_DIR/ai_fwi_theory_agent" fwi-theory-1 $FWI_THEORY_AGENT_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST \
  --redis-port $REDIS_PORT \
  > "$SCRIPT_DIR/logs/fwi_theory_agent.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/fwi_theory_agent.pid"
sleep 1
echo "FWI Theory Agent 启动完成 (端口: $FWI_THEORY_AGENT_PORT)"

# 4. 启动 FWI Teaching Agent
echo "[4/6] 启动 FWI Teaching Agent..."
"$BIN_DIR/ai_fwi_teaching_agent" fwi-teaching-1 $FWI_TEACHING_AGENT_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT \
  > "$SCRIPT_DIR/logs/fwi_teaching_agent.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/fwi_teaching_agent.pid"
sleep 1
echo "FWI Teaching Agent 启动完成 (端口: $FWI_TEACHING_AGENT_PORT)"

# 5. 启动 General Research Agent
echo "[5/6] 启动 General Research Agent..."
"$BIN_DIR/ai_general_research_agent" general-research-1 $GENERAL_RESEARCH_AGENT_PORT http://localhost:$REGISTRY_PORT $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT \
  > "$SCRIPT_DIR/logs/general_research_agent.log" 2>&1 &
echo $! > "$SCRIPT_DIR/pids/general_research_agent.pid"
sleep 1
echo "General Research Agent 启动完成 (端口: $GENERAL_RESEARCH_AGENT_PORT)"

# 6. 启动 Orchestrator
echo "[6/6] 启动 Orchestrator..."
"$BIN_DIR/ai_orchestrator" orch-1 $ORCHESTRATOR_PORT http://localhost:$REGISTRY_PORT $API_KEY --redis-host $REDIS_HOST --redis-port $REDIS_PORT $MCP_ARGS $RAG_ARGS > "$SCRIPT_DIR/logs/orchestrator.log" 2>&1 &
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
echo "  FWI Theory Agent:      http://localhost:$FWI_THEORY_AGENT_PORT"
echo "  FWI Teaching Agent:    http://localhost:$FWI_TEACHING_AGENT_PORT"
echo "  General Research Agent: http://localhost:$GENERAL_RESEARCH_AGENT_PORT"
echo ""
echo "使用客户端连接:"
echo "  $BIN_DIR/ai_client http://localhost:$ORCHESTRATOR_PORT"
echo ""
echo "查看日志:"
echo "  tail -f $SCRIPT_DIR/logs/orchestrator.log"
echo ""
echo "停止系统:"
echo "  $SCRIPT_DIR/stop_system.sh"
