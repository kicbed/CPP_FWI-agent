#!/usr/bin/env bash
# Deprecated compatibility entry for the canonical PID-safe shutdown.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)"

printf '提示: mcp/examples/ai_orchestrator/stop_system.sh 已弃用，请改用 %s/stop.sh\n' \
    "$PROJECT_ROOT" >&2
exec "$PROJECT_ROOT/stop.sh" "$@"
