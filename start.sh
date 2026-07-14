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
grpc_cli_override=""

usage() {
    cat <<'USAGE'
用法: ./start.sh [选项]

一键检查环境、按需编译并在后台启动 Agent 与 Web UI。

选项:
  --rebuild    强制重新运行 CMake 配置和构建
  --no-build   明确跳过构建；缺少二进制会报错
  --grpc       同时启动本机 gRPC Server（50051）和 Web bridge（50052）
  -h, --help   显示帮助

常用环境变量:
  FWI_RUN_ROOT        运行结果目录，默认 /root/fwi-runs
  WEB_HOST/WEB_PORT   Web 监听地址和端口，默认 127.0.0.1:8080
  AGENT_BIND_HOST     Agent 监听地址，默认 127.0.0.1
  AGENT_CORS_ORIGIN   允许的 Web origin，默认 http://127.0.0.1:8080
  ENABLE_MCP          是否启用 MCP，默认 true
  ENABLE_GRPC         是否启动固定 gRPC/HTTP bridge，默认 false；推荐使用 --grpc
  ENABLE_LOCAL_EMBEDDING  auto|true|false；Agent-RAG+local 时 auto 自动启动
  LOCAL_EMBEDDING_MODEL/DEVICE  本地模型，设备默认 cpu
  REDIS_PERSISTENCE   是否持久化对话状态，默认 true
  REDIS_DATA_DIR      Redis 数据目录，默认 ~/.local/state/cpp-fwi-agent/redis
USAGE
}

while (($#)); do
    case "$1" in
        --rebuild) build_mode=rebuild ;;
        --no-build) build_mode=never ;;
        --grpc) grpc_cli_override=true ;;
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

clear_provider_api_keys() {
    unset DEEPSEEK_API_KEY QWEN_API_KEY OPENAI_API_KEY DASHSCOPE_API_KEY
}
provider_secret_free_env=(
    env
    -u DEEPSEEK_API_KEY
    -u QWEN_API_KEY
    -u OPENAI_API_KEY
    -u DASHSCOPE_API_KEY
)

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
web_origin_host="$WEB_HOST"
[[ "$web_origin_host" == 0.0.0.0 ]] && web_origin_host=127.0.0.1
AGENT_CORS_ORIGIN="${AGENT_CORS_ORIGIN:-http://$web_origin_host:$WEB_PORT}"
GRPC_BRIDGE_CORS_ORIGIN="${GRPC_BRIDGE_CORS_ORIGIN:-http://$web_origin_host:$WEB_PORT}"
ENABLE_MCP="${ENABLE_MCP:-true}"
ENABLE_GRPC="${ENABLE_GRPC:-false}"
REDIS_PERSISTENCE="${REDIS_PERSISTENCE:-true}"
REDIS_DATA_DIR="${REDIS_DATA_DIR:-${XDG_STATE_HOME:-$HOME/.local/state}/cpp-fwi-agent/redis}"
ROUTING_MODE="${ROUTING_MODE:-fixed}"
EMBEDDING_PROVIDER="${EMBEDDING_PROVIDER:-local}"
ENABLE_LOCAL_EMBEDDING="${ENABLE_LOCAL_EMBEDDING:-auto}"
LOCAL_EMBEDDING_URL="${LOCAL_EMBEDDING_URL:-http://127.0.0.1:6000}"
LOCAL_EMBEDDING_MODEL="${LOCAL_EMBEDDING_MODEL:-Qwen/Qwen3-Embedding-0.6B}"
LOCAL_EMBEDDING_DEVICE="${LOCAL_EMBEDDING_DEVICE:-cpu}"
CONTEXT_MAX_MESSAGES="${CONTEXT_MAX_MESSAGES:-10}"
CONTEXT_MAX_CHARS="${CONTEXT_MAX_CHARS:-12000}"
CONTEXT_MAX_MESSAGE_CHARS="${CONTEXT_MAX_MESSAGE_CHARS:-4000}"
CONVERSATION_MAX_STORED_MESSAGES="${CONVERSATION_MAX_STORED_MESSAGES:-200}"
CONVERSATION_TTL_SECONDS="${CONVERSATION_TTL_SECONDS:-2592000}"
if [[ -n "$grpc_cli_override" ]]; then
    ENABLE_GRPC="$grpc_cli_override"
fi

die() {
    printf '错误: %s\n' "$*" >&2
    return 1
}

validate_redis_data_dir() {
    local requested="$1" resolved critical entry basename
    [[ "$requested" == /* && "$requested" != *$'\r'* && "$requested" != *$'\n'* ]] || \
        die "REDIS_DATA_DIR 必须是无换行的绝对路径" || return
    [[ ! -L "$requested" ]] || die "REDIS_DATA_DIR 不能是符号链接" || return
    resolved="$(realpath -m -- "$requested")"

    for critical in / /etc /usr /bin /sbin /lib /lib32 /lib64 /boot /proc /sys /dev /run; do
        if [[ "$resolved" == "$critical" || "$resolved" == "$critical/"* ]]; then
            die "REDIS_DATA_DIR 不能位于系统敏感目录: $critical" || return
        fi
    done
    [[ "$resolved" != /var ]] || die "REDIS_DATA_DIR 不能直接指向 /var" || return
    if [[ "$resolved" == "$PROJECT_ROOT" || "$resolved" == "$PROJECT_ROOT/"* ||
          "$PROJECT_ROOT" == "$resolved/"* || "$resolved" == "$HOME" ||
          "$HOME" == "$resolved/"* ]]; then
        die "REDIS_DATA_DIR 不能是仓库/HOME 本身、其上级或仓库内目录" || return
    fi
    if [[ -e "$resolved" ]]; then
        [[ -d "$resolved" && ! -L "$resolved" ]] || \
            die "REDIS_DATA_DIR 必须是非符号链接目录" || return
        [[ ! -L "$resolved/.cpp-fwi-agent-redis-dir" ]] || \
            die "REDIS_DATA_DIR 管理标记不能是符号链接" || return
        if [[ ! -f "$resolved/.cpp-fwi-agent-redis-dir" ]]; then
            # Safely adopt only an empty directory or a legacy directory that
            # contains Redis's own well-known persistence artifacts.
            while IFS= read -r -d '' entry; do
                [[ ! -L "$entry" ]] || die "REDIS_DATA_DIR 含有符号链接" || return
                basename="${entry##*/}"
                case "$basename" in
                    appendonlydir|appendonly.aof|dump.rdb|redis.log) ;;
                    *) die "拒绝接管非空的非专用 REDIS_DATA_DIR: $resolved" || return ;;
                esac
            done < <(find "$resolved" -mindepth 1 -maxdepth 1 -print0)
        fi
    fi
    printf '%s\n' "$resolved"
}

validate_fwi_run_root() {
    local requested="$1" resolved critical
    [[ "$requested" == /* && "$requested" != *$'\r'* && "$requested" != *$'\n'* ]] || \
        die "FWI_RUN_ROOT 必须是无换行的绝对路径" || return
    [[ ! -L "$requested" ]] || die "FWI_RUN_ROOT 不能是符号链接" || return
    resolved="$(realpath -m -- "$requested")"
    for critical in / /etc /usr /bin /sbin /lib /lib32 /lib64 /boot /proc /sys /dev /run; do
        if [[ "$resolved" == "$critical" || "$resolved" == "$critical/"* ]]; then
            die "FWI_RUN_ROOT 不能位于系统敏感目录: $critical" || return
        fi
    done
    [[ "$resolved" != /var && "$(dirname -- "$resolved")" != / ]] || \
        die "FWI_RUN_ROOT 必须是专用的二级或更深目录" || return
    if [[ "$resolved" == "$PROJECT_ROOT" || "$resolved" == "$PROJECT_ROOT/"* ||
          "$PROJECT_ROOT" == "$resolved/"* || "$resolved" == "$HOME" ||
          "$HOME" == "$resolved/"* ]]; then
        die "FWI_RUN_ROOT 不能是仓库/HOME 本身、其上级或仓库内目录" || return
    fi
    if [[ -e "$resolved" ]]; then
        [[ -d "$resolved" && ! -L "$resolved" ]] || \
            die "FWI_RUN_ROOT 必须是非符号链接目录" || return
    fi
    printf '%s\n' "$resolved"
}

[[ "$configured_fwi_venv" == "$FIXED_FWI_VENV" ]] || \
    die "FWI_VENV 必须使用固定隔离环境: $FIXED_FWI_VENV"
[[ "$configured_fwi_python" == "$FIXED_FWI_WORKER_PYTHON" ]] || \
    die "FWI_WORKER_PYTHON 必须使用固定解释器: $FIXED_FWI_WORKER_PYTHON"

for command_name in curl ss realpath; do
    command -v "$command_name" >/dev/null 2>&1 || die "缺少命令: $command_name"
done
if [[ "$build_mode" != never ]]; then
    for command_name in cmake g++; do
        command -v "$command_name" >/dev/null 2>&1 || die "构建模式缺少命令: $command_name"
    done
fi
[[ -x "$FWI_WORKER_PYTHON" ]] || die "Python 环境不可用: $FWI_WORKER_PYTHON"
FWI_RUN_ROOT="$(validate_fwi_run_root "$FWI_RUN_ROOT")"
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
[[ "$REDIS_PERSISTENCE" == true || "$REDIS_PERSISTENCE" == false ]] || \
    die "REDIS_PERSISTENCE 只能是 true 或 false"
[[ "$ROUTING_MODE" == fixed || "$ROUTING_MODE" == agent-rag ]] || \
    die "ROUTING_MODE 只能是 fixed 或 agent-rag"
[[ "$EMBEDDING_PROVIDER" == local || "$EMBEDDING_PROVIDER" == dashscope ]] || \
    die "EMBEDDING_PROVIDER 只能是 local 或 dashscope"
[[ "$ENABLE_LOCAL_EMBEDDING" == auto || "$ENABLE_LOCAL_EMBEDDING" == true || \
   "$ENABLE_LOCAL_EMBEDDING" == false ]] || \
    die "ENABLE_LOCAL_EMBEDDING 只能是 auto、true 或 false"
if [[ "$ENABLE_LOCAL_EMBEDDING" == auto ]]; then
    if [[ "$ROUTING_MODE" == agent-rag && "$EMBEDDING_PROVIDER" == local ]]; then
        ENABLE_LOCAL_EMBEDDING=true
    else
        ENABLE_LOCAL_EMBEDDING=false
    fi
fi
[[ "$ENABLE_LOCAL_EMBEDDING" != true || "$EMBEDDING_PROVIDER" == local ]] || \
    die "ENABLE_LOCAL_EMBEDDING=true 要求 EMBEDDING_PROVIDER=local"
[[ "$LOCAL_EMBEDDING_DEVICE" == cpu || "$LOCAL_EMBEDDING_DEVICE" == cuda ]] || \
    die "LOCAL_EMBEDDING_DEVICE 只能是 cpu 或 cuda"
[[ -n "$LOCAL_EMBEDDING_MODEL" && ${#LOCAL_EMBEDDING_MODEL} -le 200 && \
   "$LOCAL_EMBEDDING_MODEL" != *$'\r'* && "$LOCAL_EMBEDDING_MODEL" != *$'\n'* ]] || \
    die "LOCAL_EMBEDDING_MODEL 必须是 1～200 字节且不含换行"
embedding_port=""
if [[ "$LOCAL_EMBEDDING_URL" =~ ^http://(127\.0\.0\.1|localhost):([0-9]+)$ ]]; then
    embedding_port="${BASH_REMATCH[2]}"
fi
[[ -n "$embedding_port" ]] && ((embedding_port >= 1 && embedding_port <= 65535)) || \
    die "LOCAL_EMBEDDING_URL 必须是带显式端口的 loopback HTTP 地址"
REDIS_DATA_DIR="$(validate_redis_data_dir "$REDIS_DATA_DIR")"

total_steps=5
[[ "$ENABLE_LOCAL_EMBEDDING" == true ]] && ((total_steps += 1))
[[ "$ENABLE_GRPC" == true ]] && ((total_steps += 1))

mkdir -p -- "$FWI_RUN_ROOT" "$LOG_DIR" "$PID_DIR"
chmod 700 "$LOG_DIR" "$PID_DIR"
find "$LOG_DIR" -maxdepth 1 -type f -exec chmod 600 -- {} +
[[ -d "$FWI_RUN_ROOT" ]] || die "无法创建 FWI_RUN_ROOT: $FWI_RUN_ROOT"

printf '[1/%s] 检查隔离的 Python/Deepwave 环境……\n' "$total_steps"
PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}" \
    "${provider_secret_free_env[@]}" "$FWI_WORKER_PYTHON" - <<'PY'
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
if [[ "$ENABLE_LOCAL_EMBEDDING" == true ]]; then
    "${provider_secret_free_env[@]}" "$FWI_WORKER_PYTHON" - <<'PY'
import importlib.util

missing = [name for name in ("flask", "sentence_transformers")
           if importlib.util.find_spec(name) is None]
if missing:
    raise SystemExit(
        "本地 Embedding 缺少依赖: " + ", ".join(missing) +
        "；请先运行 deploy/scripts/setup_embedding.sh"
    )
print("Local Embedding Python dependencies OK")
PY
fi

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
    printf '[2/%s] 配置并增量构建 C++ 项目……\n' "$total_steps"
    "${provider_secret_free_env[@]}" cmake -S "$PROJECT_ROOT" -B "$ROOT_BUILD"
    "${provider_secret_free_env[@]}" cmake --build "$ROOT_BUILD" "${cmake_build_args[@]}"
else
    printf '[2/%s] C++ 构建产物已就绪。\n' "$total_steps"
fi
if [[ "$ENABLE_MCP" == true && "$need_mcp_build" == true ]]; then
    printf '[3/%s] 配置并增量构建 MCP Server/插件……\n' "$total_steps"
    "${provider_secret_free_env[@]}" cmake -S "$PROJECT_ROOT/mcp_server_integrated" -B "$MCP_BUILD"
    "${provider_secret_free_env[@]}" cmake --build "$MCP_BUILD" "${cmake_build_args[@]}"
else
    printf '[3/%s] MCP 构建产物已就绪或已禁用。\n' "$total_steps"
fi

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

process_matches_binary() {
    local pid="$1" expected="$2" actual
    [[ -r "/proc/$pid/exe" ]] || return 1
    actual="$(readlink -f -- "/proc/$pid/exe" 2>/dev/null)" || return 1
    expected="$(readlink -f -- "$expected" 2>/dev/null)" || return 1
    [[ "$actual" == "$expected" ]]
}

process_matches_python_script() {
    local pid="$1" python="$2" script="$3" actual expected
    local -a command_line=()
    [[ -r "/proc/$pid/cmdline" ]] || return 1
    mapfile -d '' -t command_line < "/proc/$pid/cmdline" || true
    ((${#command_line[@]} >= 2)) || return 1
    actual="$(readlink -f -- "/proc/$pid/exe" 2>/dev/null)" || return 1
    expected="$(readlink -f -- "$python" 2>/dev/null)" || return 1
    [[ "$actual" == "$expected" && "${command_line[1]}" == "$script" ]]
}

port_in_use "$WEB_PORT" && die "Web 端口 $WEB_PORT 已被占用；不会自动终止占用者。"
if [[ "$ENABLE_LOCAL_EMBEDDING" == true ]]; then
    port_in_use "$embedding_port" && \
        die "Embedding 端口 $embedding_port 已被占用；不会自动终止占用者。"
fi
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
export REDIS_PERSISTENCE REDIS_DATA_DIR CONTEXT_MAX_MESSAGES CONTEXT_MAX_CHARS
export CONTEXT_MAX_MESSAGE_CHARS CONVERSATION_MAX_STORED_MESSAGES
export CONVERSATION_TTL_SECONDS
export ROUTING_MODE EMBEDDING_PROVIDER ENABLE_LOCAL_EMBEDDING LOCAL_EMBEDDING_URL
export LOCAL_EMBEDDING_MODEL LOCAL_EMBEDDING_DEVICE
export GRPC_BIND_HOST=127.0.0.1
export HTTP_BRIDGE_BIND_HOST=127.0.0.1
export PYTHONPATH="$PROJECT_ROOT${PYTHONPATH:+:$PYTHONPATH}"
# A background/headless launcher should not invoke xdg-open. Users can still
# override BROWSER explicitly or open the printed URL themselves.
export BROWSER="${BROWSER:-true}"

current_step=4

printf '[%s/%s] 启动 Agent 系统（API Key 不进入进程参数）……\n' \
    "$current_step" "$total_steps"
"$INTERNAL_DIR/start_system.sh"
# The long-lived Agent processes already have only the credentials they need.
# Remove provider secrets from this parent before starting Embedding, gRPC,
# Web, and any subsequent health-check subprocesses.
clear_provider_api_keys
((current_step += 1))

if [[ "$ENABLE_LOCAL_EMBEDDING" == true ]]; then
    printf '[%s/%s] 启动本地 Embedding（仅使用已缓存模型，device=%s）……\n' \
        "$current_step" "$total_steps" "$LOCAL_EMBEDDING_DEVICE"
    embedding_script="$PROJECT_ROOT/deploy/scripts/embedding_server.py"
    [[ -f "$embedding_script" && ! -L "$embedding_script" ]] || \
        die "Embedding 服务脚本不存在或不可信: $embedding_script"
    nohup "$FWI_WORKER_PYTHON" "$embedding_script" \
        --model "$LOCAL_EMBEDDING_MODEL" \
        --device "$LOCAL_EMBEDDING_DEVICE" \
        --host 127.0.0.1 --port "$embedding_port" --local-files-only \
        > "$LOG_DIR/embedding.log" 2>&1 &
    embedding_pid=$!
    write_pid embedding "$embedding_pid"

    embedding_ready=false
    for ((attempt = 0; attempt < 900; ++attempt)); do
        kill -0 "$embedding_pid" 2>/dev/null || \
            die "Embedding 服务提前退出；请查看 $LOG_DIR/embedding.log"
        process_matches_python_script "$embedding_pid" "$FWI_WORKER_PYTHON" "$embedding_script" || \
            die "Embedding 服务 PID 身份校验失败"
        if curl --noproxy '*' --fail --silent --show-error --max-time 2 \
            "$LOCAL_EMBEDDING_URL/health" >/dev/null 2>&1; then
            embedding_ready=true
            break
        fi
        sleep 0.2
    done
    [[ "$embedding_ready" == true ]] || \
        die "Embedding 健康检查超时；请查看 $LOG_DIR/embedding.log"
    ((current_step += 1))
fi

port_owned_by_pid() {
    local port="$1" pid="$2"
    ss -H -ltnp "sport = :$port" 2>/dev/null | grep -Eq "pid=$pid([,)])"
}

if [[ "$ENABLE_GRPC" == true ]]; then
    printf '[%s/%s] 启动固定 gRPC Server 与 HTTP bridge……\n' \
        "$current_step" "$total_steps"
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
    ((current_step += 1))
fi

printf '[%s/%s] 启动 Web UI……\n' "$current_step" "$total_steps"
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
if [[ "$ENABLE_LOCAL_EMBEDDING" == true ]]; then
    printf '  Embedding:    %s (%s, %s)\n' "$LOCAL_EMBEDDING_URL" \
        "$LOCAL_EMBEDDING_MODEL" "$LOCAL_EMBEDDING_DEVICE"
else
    printf '  Embedding:    未启用（fixed 路由和 FWI 本地知识库不依赖它）\n'
fi
if [[ "$ENABLE_GRPC" == true ]]; then
    printf '  gRPC Server:  127.0.0.1:50051\n'
    printf '  Web bridge:   http://127.0.0.1:50052 (HTTP → gRPC)\n'
else
    printf '  gRPC 模式:    未启动；需要时先停止，再运行 ./start.sh --grpc\n'
fi
printf '  运行结果:     %s\n' "$FWI_RUN_ROOT"
printf '  日志目录:     %s\n' "$LOG_DIR"
printf '  停止命令:     %s/stop.sh\n' "$PROJECT_ROOT"
