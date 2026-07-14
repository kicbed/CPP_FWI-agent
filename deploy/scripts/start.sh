#!/usr/bin/env bash
# Compatibility wrapper. The repository-root launcher is the canonical entry.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"
printf '提示: deploy/scripts/start.sh 已弃用，请改用 %s/start.sh\n' "$PROJECT_ROOT" >&2
exec "$PROJECT_ROOT/start.sh" "$@"
