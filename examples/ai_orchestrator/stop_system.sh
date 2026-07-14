#!/usr/bin/env bash
# Internal, PID-file-only shutdown. Prefer repository-root stop.sh.

set -Eeuo pipefail
umask 077

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
BIN_DIR="$PROJECT_ROOT/build/examples/ai_orchestrator"
PID_DIR="$SCRIPT_DIR/pids"
QUIET=false

case "${1:-}" in
    '') ;;
    --quiet) QUIET=true ;;
    -h|--help)
        printf '用法: %s [--quiet]\n' "$0"
        exit 0
        ;;
    *) printf '错误: 未知参数 %s\n' "$1" >&2; exit 2 ;;
esac

log() {
    [[ "$QUIET" == true ]] || printf '%s\n' "$*"
}

expected_binary() {
    case "$1" in
        registry) printf '%s\n' "$BIN_DIR/ai_registry_server" ;;
        math_agent) printf '%s\n' "$BIN_DIR/ai_math_agent" ;;
        fwi_theory_agent) printf '%s\n' "$BIN_DIR/ai_fwi_theory_agent" ;;
        fwi_teaching_agent) printf '%s\n' "$BIN_DIR/ai_fwi_teaching_agent" ;;
        general_research_agent) printf '%s\n' "$BIN_DIR/ai_general_research_agent" ;;
        code_agent) printf '%s\n' "$BIN_DIR/ai_code_agent" ;;
        experiment_planner_agent) printf '%s\n' "$BIN_DIR/ai_experiment_planner_agent" ;;
        orchestrator) printf '%s\n' "$BIN_DIR/ai_orchestrator" ;;
        grpc_server) printf '%s\n' "$PROJECT_ROOT/build/server/rpc_server" ;;
        redis) command -v redis-server 2>/dev/null || true ;;
        *) return 1 ;;
    esac
}

process_matches() {
    local name="$1" pid="$2" actual expected
    [[ -r "/proc/$pid/cmdline" ]] || return 1

    if [[ "$name" == watchdog || "$name" == web || "$name" == embedding ]]; then
        local -a command_line=()
        mapfile -d '' -t command_line < "/proc/$pid/cmdline" || true
        ((${#command_line[@]} >= 2)) || return 1
        if [[ "$name" == watchdog ]]; then
            [[ "${command_line[1]}" == "$SCRIPT_DIR/watchdog.sh" ]]
        elif [[ "$name" == web ]]; then
            [[ "${command_line[1]}" == "$PROJECT_ROOT/web/serve.py" ]]
        else
            [[ "${command_line[1]}" == "$PROJECT_ROOT/deploy/scripts/embedding_server.py" ]]
        fi
        return
    fi

    expected="$(expected_binary "$name")" || return 1
    [[ -n "$expected" && -e "$expected" ]] || return 1
    # When an incremental build atomically replaces a running binary, Linux
    # reports the original executable as "<canonical path> (deleted)".  It is
    # still the project process recorded by our private PID file and must
    # remain stoppable; compare that exact form without weakening the path
    # allow-list or falling back to process-name matching.
    actual="$(readlink -- "/proc/$pid/exe" 2>/dev/null)" || return 1
    expected="$(readlink -f -- "$expected" 2>/dev/null)" || return 1
    [[ "$actual" == "$expected" || "$actual" == "$expected (deleted)" ]]
}

stop_one() {
    local name="$1" pid_file pid attempt
    pid_file="$PID_DIR/$name.pid"
    [[ -e "$pid_file" ]] || return 0

    if [[ -L "$pid_file" || ! -f "$pid_file" ]]; then
        log "跳过不可信 PID 文件: $pid_file"
        rm -f -- "$pid_file"
        return 0
    fi

    pid="$(tr -d '[:space:]' < "$pid_file")"
    if [[ ! "$pid" =~ ^[1-9][0-9]*$ ]]; then
        log "移除无效 PID 文件: $pid_file"
        rm -f -- "$pid_file"
        return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
        rm -f -- "$pid_file"
        return 0
    fi
    if ! process_matches "$name" "$pid"; then
        log "拒绝停止身份不匹配的 PID $pid（记录名: $name）"
        rm -f -- "$pid_file"
        return 0
    fi

    log "停止 $name (PID $pid)"
    kill -TERM "$pid" 2>/dev/null || true
    for ((attempt = 0; attempt < 50; ++attempt)); do
        kill -0 "$pid" 2>/dev/null || break
        sleep 0.1
    done
    if kill -0 "$pid" 2>/dev/null && process_matches "$name" "$pid"; then
        log "$name 未及时退出，发送 KILL"
        kill -KILL "$pid" 2>/dev/null || true
    fi
    rm -f -- "$pid_file"
}

log '停止 FWI Agent 项目进程……'

# Stop the watchdog first so it cannot recreate services during shutdown.
stop_one watchdog
stop_one web
for name in orchestrator experiment_planner_agent code_agent general_research_agent \
    fwi_teaching_agent fwi_theory_agent math_agent registry grpc_server embedding redis; do
    stop_one "$name"
done

# Unknown PID files are deliberately left untouched: only the fixed allow-list
# above is authorised to receive signals.
log '项目进程已停止。再次执行本脚本也是安全的。'
