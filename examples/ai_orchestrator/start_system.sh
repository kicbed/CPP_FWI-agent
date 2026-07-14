#!/usr/bin/env bash
# Internal launcher for the C++ Agent processes. Prefer repository-root start.sh.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
BIN_DIR="$PROJECT_ROOT/build/examples/ai_orchestrator"
LOG_DIR="$SCRIPT_DIR/logs"
PID_DIR="$SCRIPT_DIR/pids"

REGISTRY_PORT="${REGISTRY_PORT:-8500}"
ORCHESTRATOR_PORT="${ORCHESTRATOR_PORT:-5000}"
MATH_AGENT_PORT="${MATH_AGENT_PORT:-5001}"
FWI_THEORY_PORT="${FWI_THEORY_PORT:-5002}"
FWI_TEACHING_PORT="${FWI_TEACHING_PORT:-5003}"
GENERAL_RESEARCH_PORT="${GENERAL_RESEARCH_PORT:-5004}"
CODE_AGENT_PORT="${CODE_AGENT_PORT:-5010}"
EXPERIMENT_PLANNER_AGENT_PORT="${EXPERIMENT_PLANNER_AGENT_PORT:-5011}"
REDIS_HOST="${REDIS_HOST:-127.0.0.1}"
REDIS_PORT="${REDIS_PORT:-6379}"

MCP_SERVER="${MCP_SERVER:-$PROJECT_ROOT/mcp_server_integrated/build/mcp_server}"
MCP_PLUGINS="${MCP_PLUGINS:-$PROJECT_ROOT/mcp_server_integrated/build/plugins}"
ENABLE_MCP="${ENABLE_MCP:-false}"

die() {
    printf '错误: %s\n' "$*" >&2
    return 1
}

if [[ "${FWI_ENV_LOADED:-false}" != true && -e "$PROJECT_ROOT/.env" ]]; then
    [[ -f "$PROJECT_ROOT/.env" && ! -L "$PROJECT_ROOT/.env" ]] || die ".env 必须是普通文件，不能是符号链接"
    env_mode="$(stat -c '%a' "$PROJECT_ROOT/.env")"
    if (( (8#$env_mode & 077) != 0 )); then
        chmod 600 "$PROJECT_ROOT/.env"
    fi
    printf '加载本地配置: %s（不会输出配置内容）\n' "$PROJECT_ROOT/.env"
    set -a
    # .env is trusted local shell input and is excluded from Git.
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.env"
    set +a
fi

LLM_PROVIDER="${LLM_PROVIDER:-qwen}"
export LLM_PROVIDER

api_key_is_configured() {
    case "$LLM_PROVIDER" in
        local) return 0 ;;
        deepseek) [[ -n "${DEEPSEEK_API_KEY:-}" ]] ;;
        qwen) [[ -n "${QWEN_API_KEY:-}" ]] ;;
        openai) [[ -n "${OPENAI_API_KEY:-}" ]] ;;
        *) [[ -n "${QWEN_API_KEY:-${DEEPSEEK_API_KEY:-${OPENAI_API_KEY:-}}}" ]] ;;
    esac
}

api_key_is_configured || die "未为 LLM_PROVIDER=$LLM_PROVIDER 配置 API Key；请复制 .env.example 为 .env 后填写。"
printf '使用 LLM provider: %s（密钥仅通过继承环境传递）\n' "$LLM_PROVIDER"

required_binaries=(
    ai_registry_server ai_math_agent ai_fwi_theory_agent
    ai_fwi_teaching_agent ai_general_research_agent ai_code_agent
    ai_experiment_planner_agent ai_orchestrator
)
for binary in "${required_binaries[@]}"; do
    [[ -x "$BIN_DIR/$binary" ]] || die "缺少可执行文件 $BIN_DIR/$binary，请先运行根目录 ./start.sh 构建。"
done
if [[ "$ENABLE_MCP" == true ]]; then
    [[ -x "$MCP_SERVER" ]] || die "ENABLE_MCP=true，但 MCP Server 不存在或不可执行: $MCP_SERVER"
    [[ -d "$MCP_PLUGINS" ]] || die "MCP 插件目录不存在: $MCP_PLUGINS"
elif [[ "$ENABLE_MCP" != false ]]; then
    die "ENABLE_MCP 只能是 true 或 false"
fi

mkdir -p -- "$LOG_DIR" "$PID_DIR"
chmod 700 "$LOG_DIR" "$PID_DIR"

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

wait_for_port() {
    local name="$1" port="$2" pid="$3" attempt
    for ((attempt = 0; attempt < 100; ++attempt)); do
        if ! kill -0 "$pid" 2>/dev/null; then
            die "$name 启动失败；请查看 $LOG_DIR/$name.log"
        fi
        if port_in_use "$port"; then
            return 0
        fi
        sleep 0.1
    done
    die "$name 未在期限内监听端口 $port；请查看 $LOG_DIR/$name.log"
}

launch_service() {
    local name="$1" port="$2"
    shift 2
    nohup "$@" > "$LOG_DIR/$name.log" 2>&1 &
    local pid=$!
    write_pid "$name" "$pid"
    wait_for_port "$name" "$port" "$pid"
}

# Refuse to overwrite a live PID file or take over a port. The stop scripts
# validate process identity before sending signals, so an unrelated process is
# never killed to make room.
for pid_file in "$PID_DIR"/*.pid; do
    [[ -e "$pid_file" ]] || continue
    if [[ -L "$pid_file" || ! -f "$pid_file" ]]; then
        die "不可信 PID 文件: $pid_file"
    fi
    pid="$(tr -d '[:space:]' < "$pid_file")"
    if [[ "$pid" =~ ^[1-9][0-9]*$ ]] && kill -0 "$pid" 2>/dev/null; then
        die "检测到仍在运行的项目 PID $pid；请先执行 $PROJECT_ROOT/stop.sh"
    fi
    rm -f -- "$pid_file"
done

service_ports=(
    "$REGISTRY_PORT" "$ORCHESTRATOR_PORT" "$MATH_AGENT_PORT"
    "$FWI_THEORY_PORT" "$FWI_TEACHING_PORT" "$GENERAL_RESEARCH_PORT"
    "$CODE_AGENT_PORT" "$EXPERIMENT_PLANNER_AGENT_PORT"
)
for port in "${service_ports[@]}"; do
    [[ "$port" =~ ^[0-9]+$ ]] && ((port >= 1 && port <= 65535)) || die "非法端口: $port"
    port_in_use "$port" && die "端口 $port 已被占用；不会自动终止占用者。"
done

startup_complete=false
rollback() {
    local rc="${1:-1}"
    trap - ERR INT TERM
    if [[ "$startup_complete" != true ]]; then
        printf '启动未完成，正在回滚本次项目进程……\n' >&2
        "$SCRIPT_DIR/stop_system.sh" --quiet || true
    fi
    exit "$rc"
}
trap 'rollback "$?"' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

if command -v redis-cli >/dev/null 2>&1 && redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null | grep -qx PONG; then
    printf '使用现有 Redis: %s:%s\n' "$REDIS_HOST" "$REDIS_PORT"
else
    [[ "$REDIS_HOST" == 127.0.0.1 || "$REDIS_HOST" == localhost ]] || die "无法连接远程 Redis $REDIS_HOST:$REDIS_PORT"
    command -v redis-server >/dev/null 2>&1 || die "Redis 未运行且找不到 redis-server"
    command -v redis-cli >/dev/null 2>&1 || die "找不到 redis-cli，无法进行 Redis 健康检查"
    port_in_use "$REDIS_PORT" && die "Redis 端口 $REDIS_PORT 已被非预期服务占用"
    nohup redis-server --bind 127.0.0.1 --port "$REDIS_PORT" --daemonize no --save '' --appendonly no \
        > "$LOG_DIR/redis.log" 2>&1 &
    redis_pid=$!
    write_pid redis "$redis_pid"
    for _ in {1..50}; do
        kill -0 "$redis_pid" 2>/dev/null || die "Redis 启动失败；请查看 $LOG_DIR/redis.log"
        redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null | grep -qx PONG && break
        sleep 0.1
    done
    redis-cli -h "$REDIS_HOST" -p "$REDIS_PORT" ping 2>/dev/null | grep -qx PONG || die "Redis 健康检查超时"
fi

registry_url="http://127.0.0.1:$REGISTRY_PORT"
key_sentinel='@env'
mcp_args=()
if [[ "$ENABLE_MCP" == true ]]; then
    mcp_args=(--enable-mcp --mcp-server "$MCP_SERVER" --mcp-args "-p,$MCP_PLUGINS")
fi

printf '启动 Registry 和 Agent 服务……\n'
launch_service registry "$REGISTRY_PORT" "$BIN_DIR/ai_registry_server" "$REGISTRY_PORT"
launch_service math_agent "$MATH_AGENT_PORT" \
    "$BIN_DIR/ai_math_agent" math-1 "$MATH_AGENT_PORT" "$registry_url" "$key_sentinel" \
    --redis-host "$REDIS_HOST" --redis-port "$REDIS_PORT" "${mcp_args[@]}"
launch_service fwi_theory_agent "$FWI_THEORY_PORT" \
    "$BIN_DIR/ai_fwi_theory_agent" fwi-theory-1 "$FWI_THEORY_PORT" "$registry_url" "$key_sentinel" \
    --redis-host "$REDIS_HOST" --redis-port "$REDIS_PORT"
launch_service fwi_teaching_agent "$FWI_TEACHING_PORT" \
    "$BIN_DIR/ai_fwi_teaching_agent" fwi-teaching-1 "$FWI_TEACHING_PORT" "$registry_url" "$key_sentinel" \
    --redis-host "$REDIS_HOST" --redis-port "$REDIS_PORT"
launch_service general_research_agent "$GENERAL_RESEARCH_PORT" \
    "$BIN_DIR/ai_general_research_agent" general-research-1 "$GENERAL_RESEARCH_PORT" "$registry_url" "$key_sentinel" \
    --redis-host "$REDIS_HOST" --redis-port "$REDIS_PORT"
launch_service code_agent "$CODE_AGENT_PORT" \
    "$BIN_DIR/ai_code_agent" code-agent-1 "$CODE_AGENT_PORT" "$registry_url" "$key_sentinel" \
    --redis-host "$REDIS_HOST" --redis-port "$REDIS_PORT" --project-root "$PROJECT_ROOT"
launch_service experiment_planner_agent "$EXPERIMENT_PLANNER_AGENT_PORT" \
    "$BIN_DIR/ai_experiment_planner_agent" experiment-planner-1 "$EXPERIMENT_PLANNER_AGENT_PORT" "$registry_url" "$key_sentinel" \
    --redis-host "$REDIS_HOST" --redis-port "$REDIS_PORT" --algorithm-dir "$PROJECT_ROOT/resources/algorithms"
launch_service orchestrator "$ORCHESTRATOR_PORT" \
    "$BIN_DIR/ai_orchestrator" orch-1 "$ORCHESTRATOR_PORT" "$registry_url" "$key_sentinel" \
    --redis-host "$REDIS_HOST" --redis-port "$REDIS_PORT" "${mcp_args[@]}"

export ORCHESTRATOR_PORT REGISTRY_PORT REDIS_HOST REDIS_PORT BIN_DIR PROJECT_ROOT
export ENABLE_MCP MCP_SERVER MCP_PLUGINS
nohup "$SCRIPT_DIR/watchdog.sh" > "$LOG_DIR/watchdog.log" 2>&1 &
write_pid watchdog "$!"

startup_complete=true
trap - ERR INT TERM
printf 'Agent 系统已启动：Orchestrator http://127.0.0.1:%s\n' "$ORCHESTRATOR_PORT"
