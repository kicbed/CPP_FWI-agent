#!/usr/bin/env bash
# Internal watchdog. Configuration and credentials are inherited via the
# environment; secrets are never accepted as positional arguments.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="${PROJECT_ROOT:-$(cd -- "$SCRIPT_DIR/../.." && pwd -P)}"
BIN_DIR="${BIN_DIR:-$PROJECT_ROOT/build/examples/ai_orchestrator}"
PID_DIR="$SCRIPT_DIR/pids"
ORCH_LOG="$SCRIPT_DIR/logs/orchestrator.log"

ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-5000}"
REGISTRY_PORT="${REGISTRY_PORT:-8500}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"
ENABLE_MCP="${ENABLE_MCP:-false}"
MCP_SERVER="${MCP_SERVER:-$PROJECT_ROOT/mcp_server_integrated/build/mcp_server}"
MCP_PLUGINS="${MCP_PLUGINS:-$PROJECT_ROOT/mcp_server_integrated/build/plugins}"

mkdir -p -- "$PID_DIR"

log() {
    printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

port_in_use() {
    ss -H -ltn "sport = :$1" 2>/dev/null | grep -q .
}

write_pid() {
    local name="$1" pid="$2" tmp
    tmp="$PID_DIR/.${name}.pid.$$"
    printf '%s\n' "$pid" > "$tmp"
    chmod 600 "$tmp"
    mv -f -- "$tmp" "$PID_DIR/$name.pid"
}

tracked_process_is_running() {
    local name="$1" expected="$2" pid_file pid actual
    pid_file="$PID_DIR/$name.pid"
    [[ -f "$pid_file" && ! -L "$pid_file" ]] || return 1
    pid="$(tr -d '[:space:]' < "$pid_file")"
    [[ "$pid" =~ ^[1-9][0-9]*$ ]] || return 1
    kill -0 "$pid" 2>/dev/null || return 1
    actual="$(readlink -f -- "/proc/$pid/exe" 2>/dev/null)" || return 1
    expected="$(readlink -f -- "$expected" 2>/dev/null)" || return 1
    [[ "$actual" == "$expected" ]]
}

restart_orchestrator() {
    if port_in_use "$ORCHESTRATOR_PORT"; then
        log "端口 $ORCHESTRATOR_PORT 已由未跟踪进程占用，拒绝重启 Orchestrator"
        return
    fi
    local -a mcp_args=()
    if [[ "$ENABLE_MCP" == true ]]; then
        mcp_args=(--enable-mcp --mcp-server "$MCP_SERVER" --mcp-args "-p,$MCP_PLUGINS")
    fi
    log "Orchestrator 已退出，使用 @env 凭据哨兵重启"
    nohup "$BIN_DIR/ai_orchestrator" orch-1 "$ORCHESTRATOR_PORT" \
        "http://127.0.0.1:$REGISTRY_PORT" '@env' \
        --redis-host "$REDIS_HOST" --redis-port "$REDIS_PORT" "${mcp_args[@]}" \
        >> "$ORCH_LOG" 2>&1 &
    write_pid orchestrator "$!"
}

while true; do
    sleep 2
    tracked_process_is_running orchestrator "$BIN_DIR/ai_orchestrator" || restart_orchestrator
done
