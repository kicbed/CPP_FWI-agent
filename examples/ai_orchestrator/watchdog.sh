#!/bin/bash
#
# 看门狗脚本 - 监控并自动重启关键服务
# 由 start_system.sh 启动，不应手动运行
#

ORCH_PORT="${1:-5000}"
REGISTRY_PORT="${2:-8500}"
API_KEY="${3:-}"
REDIS_HOST="${4:-127.0.0.1}"
REDIS_PORT="${5:-6379}"
MCP_ARGS="${6:-}"
BIN_DIR="${7:-/root/projects/project/agent-communication-main-v2/build/examples/ai_orchestrator}"
PROJECT_ROOT="${8:-/root/projects/project/agent-communication-main-v2}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

WATCH_LOG="$SCRIPT_DIR/logs/watchdog.log"
ORCH_LOG="$SCRIPT_DIR/logs/orchestrator.log"
GRPC_LOG="$PROJECT_ROOT/deploy/logs/grpc_server.log"
PID_DIR="$SCRIPT_DIR/pids"

mkdir -p "$PID_DIR" "$(dirname "$GRPC_LOG")"

log() {
    echo "[$(date '+%H:%M:%S')] $1" >> "$WATCH_LOG"
}

restart_orchestrator() {
    log "Orchestrator (port $ORCH_PORT) 崩溃，自动重启..."
    nohup "$BIN_DIR/ai_orchestrator" orch-1 "$ORCH_PORT" "http://localhost:$REGISTRY_PORT" "$API_KEY" \
        --redis-host "$REDIS_HOST" --redis-port "$REDIS_PORT" $MCP_ARGS \
        >> "$ORCH_LOG" 2>&1 &
    echo $! >> "$PID_DIR/orchestrator.pid"
}

restart_grpc_server() {
    log "gRPC Server (port 50051) 崩溃，自动重启..."
    nohup "$PROJECT_ROOT/build/server/rpc_server" \
        >> "$GRPC_LOG" 2>&1 &
    echo $! >> "$PID_DIR/grpc_server.pid"
}

while true; do
    sleep 2

    # 监控 Orchestrator
    if ! ss -tlnp 2>/dev/null | grep -q ":$ORCH_PORT "; then
        restart_orchestrator
    fi

    # 监控 gRPC Server
    if [ -f "$PROJECT_ROOT/build/server/rpc_server" ]; then
        if ! ss -tlnp 2>/dev/null | grep -q ":50051 "; then
            restart_grpc_server
        fi
    fi
done
