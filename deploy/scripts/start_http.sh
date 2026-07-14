#!/usr/bin/env bash
# Compatibility wrapper for the former HTTP launcher.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
printf '提示: start_http.sh 已弃用；根 start.sh 现在会同时启动 Agent HTTP 与 Web UI。\n' >&2
exec "$PROJECT_ROOT/start.sh" "$@"
