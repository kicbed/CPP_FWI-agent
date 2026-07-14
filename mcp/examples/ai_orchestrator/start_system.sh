#!/usr/bin/env bash
# Deprecated compatibility entry. Use the repository-root launcher so API keys
# stay out of argv and all services share one PID/safety implementation.

set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../../.." && pwd -P)"

printf '提示: mcp/examples/ai_orchestrator/start_system.sh 已弃用，请改用 %s/start.sh\n' \
    "$PROJECT_ROOT" >&2
exec "$PROJECT_ROOT/start.sh" "$@"
