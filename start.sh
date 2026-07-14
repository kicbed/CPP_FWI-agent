#!/usr/bin/env bash
# One-command host launcher for the FWI Agent demo.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$SCRIPT_DIR"
INTERNAL_DIR="$PROJECT_ROOT/examples/ai_orchestrator"
PID_DIR="$INTERNAL_DIR/pids"
LOG_DIR="$INTERNAL_DIR/logs"

build_mode=auto

usage() {
    cat <<'USAGE'
用法: ./start.sh [选项]

一键检查环境、按需编译并在后台启动 Agent 与 Web UI。

选项:
  --rebuild    强制重新运行 CMake 配置和构建
  --no-build   明确跳过构建；缺少二进制会报错
  -h, --help   显示帮助

常用环境变量:
  FWI_RUN_ROOT        运行结果目录，默认 /root/fwi-runs
  WEB_HOST/WEB_PORT   Web 监听地址和端口，默认 127.0.0.1:8080
  AGENT_BIND_HOST     Agent 监听地址，默认 127.0.0.1
  AGENT_CORS_ORIGIN   允许的 Web origin，默认 http://127.0.0.1:8080
  ENABLE_MCP          是否启用 MCP，默认 true
  ENABLE_GRPC         是否启动固定 gRPC/HTTP bridge，默认 false
USAGE
}

while (($#)); do
    case "$1" in
        --rebuild) build_mode=rebuild ;;
        --no-build) build_mode=never ;;
        -h|--help) usage; exit 0 ;;
        *) printf '错误: 未知参数 %s\n\n' "$1" >&2; usage >&2; exit 2 ;;
    esac
    shift
done

# Load local configuration before resolving launcher defaults. The file is
# trusted shell input, but must be a non-symlink regular file with private
# permissions. Its values are never printed.
if [[ -e "$PROJECT_ROOT/.env" ]]; then
    if [[ ! -f "$PROJECT_ROOT/.env" || -L "$PROJECT_ROOT/.env" ]]; then
        printf '错误: .env 必须是普通文件，不能是符号链接\n' >&2
        exit 1
    fi
    env_mode="$(stat -c '%a' "$PROJECT_ROOT/.env")"
    if (( (8#$env_mode & 077) != 0 )); then
        printf '安全提示: 正在把 .env 权限收紧为 600。\n'
        chmod 600 "$PROJECT_ROOT/.env"
    fi
    set -a
    # shellcheck disable=SC1091
    source "$PROJECT_ROOT/.env"
    set +a
    FWI_ENV_LOADED=true
    export FWI_ENV_LOADED
fi

FIXED_FWI_VENV=/root/.venvs/cpp-fwi-agent
FIXED_FWI_WORKER_PYTHON=/root/.venvs/cpp-fwi-agent/bin/python
configured_fwi_venv="${FWI_VENV:-$FIXED_FWI_VENV}"
configured_fwi_python="${FWI_WORKER_PYTHON:-$FIXED_FWI_WORKER_PYTHON}"
FWI_VENV="$FIXED_FWI_VENV"
FWI_WORKER_PYTHON="$FIXED_FWI_WORKER_PYTHON"
FWI_RUN_ROOT="${FWI_RUN_ROOT:-/root/fwi-runs}"
WEB_HOST="${WEB_HOST:-127.0.0.1}"
WEB_PORT="${WEB_PORT:-8080}"
AGENT_BIND_HOST="${AGENT_BIND_HOST:-127.0.0.1}"
AGENT_CORS_ORIGIN="${AGENT_CORS_ORIGIN:-http://127.0.0.1:$WEB_PORT}"
GRPC_BRIDGE_CORS_ORIGIN="${GRPC_BRIDGE_CORS_ORIGIN:-http://127.0.0.1:$WEB_PORT}"
ENABLE_MCP="${ENABLE_MCP:-true}"
ENABLE_GRPC="${ENABLE_GRPC:-false}"

die() {
    printf '错误: %s\n' "$*" >&2
    return 1
}

[[ "$configured_fwi_venv" == "$FIXED_FWI_VENV" ]] || \
    die "FWI_VENV 必须使用固定隔离环境: $FIXED_FWI_VENV"
[[ "$configured_fwi_python" == "$FIXED_FWI_WORKER_PYTHON" ]] || \
    die "FWI_WORKER_PYTHON 必须使用固定解释器: $FIXED_FWI_WORKER_PYTHON"

for command_name in curl ss; do
    command -v "$command_name" >/dev/null 2>&1 || die "缺少命令: $command_name"
done
if [[ "$build_mode" != never ]]; then
    for command_name in cmake g++; do
        command -v "$command_name" >/dev/null 2>&1 || die "构建模式缺少命令: $command_name"
    done
fi
[[ -x "$FWI_WORKER_PYTHON" ]] || die "Python 环境不可用: $FWI_WORKER_PYTHON"
[[ "$FWI_RUN_ROOT" == /* ]] || die "FWI_RUN_ROOT 必须是绝对路径"
[[ "$WEB_PORT" =~ ^[0-9]+$ ]] && ((WEB_PORT >= 1 && WEB_PORT <= 65535)) || die "WEB_PORT 非法: $WEB_PORT"
[[ "$WEB_HOST" == 127.0.0.1 || "$WEB_HOST" == localhost || "$WEB_HOST" == 0.0.0.0 ]] || \
    die "WEB_HOST 仅允许 127.0.0.1、localhost 或 0.0.0.0"
[[ "$AGENT_BIND_HOST" == 127.0.0.1 || "$AGENT_BIND_HOST" == 0.0.0.0 ]] || \
    die "AGENT_BIND_HOST 仅允许 127.0.0.1 或 0.0.0.0"
[[ "$AGENT_CORS_ORIGIN" != *$'\r'* && "$AGENT_CORS_ORIGIN" != *$'\n'* ]] || \
    die "AGENT_CORS_ORIGIN 包含非法换行"
[[ -n "$GRPC_BRIDGE_CORS_ORIGIN" && "$GRPC_BRIDGE_CORS_ORIGIN" != '*' && \
   "$GRPC_BRIDGE_CORS_ORIGIN" != *$'\r'* && "$GRPC_BRIDGE_CORS_ORIGIN" != *$'\n'* ]] || \
    die "GRPC_BRIDGE_CORS_ORIGIN 必须是无换行的精确 origin，不能使用 *"
[[ "$ENABLE_MCP" == true || "$ENABLE_MCP" == false ]] || die "ENABLE_MCP 只能是 true 或 false"
[[ "$ENABLE_GRPC" == true || "$ENABLE_GRPC" == false ]] || die "ENABLE_GRPC 只能是 true 或 false"

mkdir -p -- "$FWI_RUN_ROOT" "$LOG_DIR" "$PID_DIR"
chmod 700 "$LOG_DIR" "$PID_DIR"
[[ -d "$FWI_RUN_ROOT" ]] || die "无法创建 FWI_RUN_ROOT: $FWI_RUN_ROOT"

printf '[1/5] 检查隔离的 Python/Deepwave 环境……\n'
PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" "$FWI_WORKER_PYTHON" - <<'PY'
import importlib.metadata
from fwi_worker.config import resolve_config
from fwi_worker.model_io import load_model
import torch

deepwave_version = importlib.metadata.version("deepwave")
config = resolve_config({"preset": "forward", "device": "cpu"})
loaded = load_model(config)
assert loaded.velocity.shape == (94, 288)
print(f"Python OK | torch={torch.__version__} | deepwave={deepwave_version} | cuda={torch.cuda.is_available()}")
print("Marmousi NPY、sidecar 与原始 MAT 哈希校验通过")
PY

ROOT_BUILD="$PROJECT_ROOT/build"
MCP_BUILD="$PROJECT_ROOT/mcp_server_integrated/build"
required_root_binaries=(
    "$ROOT_BUILD/examples/ai_orchestrator/ai_registry_server"
    "$ROOT_BUILD/examples/ai_orchestrator/ai_math_agent"
    "$ROOT_BUILD/examples/ai_orchestrator/ai_fwi_theory_agent"
    "$ROOT_BUILD/examples/ai_orchestrator/ai_fwi_teaching_agent"
    "$ROOT_BUILD/examples/ai_orchestrator/ai_general_research_agent"
    "$ROOT_BUILD/examples/ai_orchestrator/ai_code_agent"
    "$ROOT_BUILD/examples/ai_orchestrator/ai_experiment_planner_agent"
    "$ROOT_BUILD/examples/ai_orchestrator/ai_orchestrator"
)
RPC_SERVER="$ROOT_BUILD/server/rpc_server"
if [[ "$ENABLE_GRPC" == true ]]; then
    required_root_binaries+=("$RPC_SERVER")
fi

need_root_build=false
for binary in "${required_root_binaries[@]}"; do
    [[ -x "$binary" ]] || need_root_build=true
done
need_mcp_build=false
if [[ "$ENABLE_MCP" == true ]]; then
    mcp_binary="$MCP_BUILD/mcp_server"
    [[ -x "$mcp_binary" ]] || need_mcp_build=true
fi

if [[ "$build_mode" != never ]]; then
    # Always let CMake perform an incremental dependency check. This prevents a
    # source update from silently running stale binaries from an older branch.
    need_root_build=true
    [[ "$ENABLE_MCP" == true ]] && need_mcp_build=true
fi
if [[ "$build_mode" == never && ( "$need_root_build" == true || "$need_mcp_build" == true ) ]]; then
    die "构建产物缺失；请移除 --no-build 或使用 --rebuild"
fi

jobs="${BUILD_JOBS:-$(nproc)}"
[[ "$jobs" =~ ^[1-9][0-9]*$ ]] || die "BUILD_JOBS 必须是正整数"
cmake_build_args=(-j "$jobs")
if [[ "$build_mode" == rebuild ]]; then
    cmake_build_args=(--clean-first -j "$jobs")
fi
if [[ "$need_root_build" == true ]]; then
    printf '[2/5] 配置并增量构建 C++ 项目……\n'
    cmake -S "$PROJECT_ROOT" -B "$ROOT_BUILD"
    cmake --build "$ROOT_BUILD" "${cmake_build_args[@]}"
else
    printf '[2/5] C++ 构建产物已就绪。\n'
fi
if [[ "$ENABLE_MCP" == true && "$need_mcp_build" == true ]]; then
    printf '[3/5] 配置并增量构建 MCP Server/插件……\n'
    cmake -S "$PROJECT_ROOT/mcp_server_integrated" -B "$MCP_BUILD"
    cmake --build "$MCP_BUILD" "${cmake_build_args[@]}"
else
    printf '[3/5] MCP 构建产物已就绪或已禁用。\n'
fi

port_in_use() {
    ss -H -ltn "sport = :$1" 2>/dev/null | grep -q .
}
port_in_use "$WEB_PORT" && die "Web 端口 $WEB_PORT 已被占用；不会自动终止占用者。"
if [[ "$ENABLE_GRPC" == true ]]; then
    for port in 50051 50052; do
        port_in_use "$port" && die "gRPC 端口 $port 已被占用；不会自动终止占用者。"
    done
fi

startup_complete=false
rollback() {
    local rc="${1:-1}"
    trap - ERR INT TERM
    if [[ "$startup_complete" != true ]]; then
        printf '一键启动失败，回滚本项目已启动的进程……\n' >&2
        "$PROJECT_ROOT/stop.sh" --quiet || true
    fi
    exit "$rc"
}
trap 'rollback "$?"' ERR
trap 'rollback 130' INT
trap 'rollback 143' TERM

export FWI_VENV FWI_WORKER_PYTHON FWI_RUN_ROOT WEB_HOST WEB_PORT ENABLE_MCP ENABLE_GRPC
export AGENT_BIND_HOST AGENT_CORS_ORIGIN
export GRPC_BRIDGE_CORS_ORIGIN
export GRPC_BIND_HOST=127.0.0.1
export HTTP_BRIDGE_BIND_HOST=127.0.0.1
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
# A background/headless launcher should not invoke xdg-open. Users can still
# override BROWSER explicitly or open the printed URL themselves.
export BROWSER="${BROWSER:-true}"

total_steps=5
[[ "$ENABLE_GRPC" == true ]] && total_steps=6
printf '[4/%s] 启动 Agent 系统（API Key 不进入进程参数）……\n' "$total_steps"
"$INTERNAL_DIR/start_system.sh"

write_pid() {
    local name="$1" pid="$2" tmp
    tmp="$PID_DIR/.${name}.pid.$$"
    printf '%s\n' "$pid" > "$tmp"
    chmod 600 "$tmp"
    mv -f -- "$tmp" "$PID_DIR/$name.pid"
}

process_matches_binary() {
    local pid="$1" expected="$2" actual
    [[ -r "/proc/$pid/exe" ]] || return 1
    actual="$(readlink -f -- "/proc/$pid/exe" 2>/dev/null)" || return 1
    expected="$(readlink -f -- "$expected" 2>/dev/null)" || return 1
    [[ "$actual" == "$expected" ]]
}

port_owned_by_pid() {
    local port="$1" pid="$2"
    ss -H -ltnp "sport = :$port" 2>/dev/null | grep -Eq "pid=$pid([,)])"
}

if [[ "$ENABLE_GRPC" == true ]]; then
    printf '[5/6] 启动固定 gRPC Server 与 HTTP bridge……\n'
    nohup "$RPC_SERVER" --port 50051 --orchestrator http://127.0.0.1:5000 --http-port 50052 \
        > "$LOG_DIR/grpc_server.log" 2>&1 &
    grpc_pid=$!
    write_pid grpc_server "$grpc_pid"

    grpc_ready=false
    for ((attempt = 0; attempt < 100; ++attempt)); do
        kill -0 "$grpc_pid" 2>/dev/null || die "gRPC Server 提前退出；请查看 $LOG_DIR/grpc_server.log"
        process_matches_binary "$grpc_pid" "$RPC_SERVER" || die "gRPC Server PID 身份校验失败"
        if port_owned_by_pid 50051 "$grpc_pid" && port_owned_by_pid 50052 "$grpc_pid" && \
           curl --fail --silent --show-error --max-time 2 http://127.0.0.1:50052/health >/dev/null 2>&1; then
            grpc_ready=true
            break
        fi
        sleep 0.1
    done
    [[ "$grpc_ready" == true ]] || die "gRPC/HTTP bridge 健康检查超时；请查看 $LOG_DIR/grpc_server.log"
fi

printf '[%s/%s] 启动 Web UI……\n' "$total_steps" "$total_steps"
nohup "$FWI_WORKER_PYTHON" "$PROJECT_ROOT/web/serve.py" "$WEB_PORT" > "$LOG_DIR/web.log" 2>&1 &
web_pid=$!
pid_tmp="$PID_DIR/.web.pid.$$"
printf '%s\n' "$web_pid" > "$pid_tmp"
chmod 600 "$pid_tmp"
mv -f -- "$pid_tmp" "$PID_DIR/web.pid"

wait_http() {
    local name="$1" url="$2" pid="$3" attempt
    for ((attempt = 0; attempt < 150; ++attempt)); do
        kill -0 "$pid" 2>/dev/null || die "$name 进程提前退出；请查看 $LOG_DIR/$name.log"
        curl --fail --silent --show-error --max-time 2 "$url" >/dev/null 2>&1 && return 0
        sleep 0.2
    done
    die "$name 健康检查超时: $url"
}

orchestrator_pid="$(tr -d '[:space:]' < "$PID_DIR/orchestrator.pid")"
wait_http orchestrator 'http://127.0.0.1:5000/.well-known/agent-card.json' "$orchestrator_pid"
wait_http web "http://127.0.0.1:$WEB_PORT/" "$web_pid"

startup_complete=true
trap - ERR INT TERM

printf '\n启动成功。\n'
printf '  Web UI:       http://%s:%s\n' "$WEB_HOST" "$WEB_PORT"
printf '  Orchestrator: http://127.0.0.1:5000\n'
if [[ "$ENABLE_GRPC" == true ]]; then
    printf '  gRPC Server:  127.0.0.1:50051\n'
    printf '  HTTP bridge:  http://127.0.0.1:50052\n'
fi
printf '  运行结果:     %s\n' "$FWI_RUN_ROOT"
printf '  日志目录:     %s\n' "$LOG_DIR"
printf '  停止命令:     %s/stop.sh\n' "$PROJECT_ROOT"
