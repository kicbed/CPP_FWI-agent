#!/usr/bin/env bash
# One-command, idempotent shutdown for processes started by ./start.sh.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"

case "${1:-}" in
    '') exec "$SCRIPT_DIR/examples/ai_orchestrator/stop_system.sh" ;;
    --quiet) exec "$SCRIPT_DIR/examples/ai_orchestrator/stop_system.sh" --quiet ;;
    -h|--help)
        printf '用法: ./stop.sh [--quiet]\n仅停止由本项目可信 PID 文件记录且身份匹配的进程。\n'
        ;;
    *) printf '错误: 未知参数 %s\n' "$1" >&2; exit 2 ;;
esac
