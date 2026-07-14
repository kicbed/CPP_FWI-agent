#!/usr/bin/env bash
# Compatibility wrapper for the former Web-only launcher.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"

if (($# > 0)) && [[ "$1" =~ ^[0-9]+$ ]]; then
    export WEB_PORT="$1"
    shift
fi
printf '提示: start_web.sh 已弃用；根 start.sh 现在会启动完整 Agent + Web 栈。\n' >&2
exec "$PROJECT_ROOT/start.sh" "$@"
