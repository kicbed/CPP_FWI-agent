#!/usr/bin/env bash
# Compatibility wrapper for the former gRPC/embedding/client launcher.
set -Eeuo pipefail
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
PROJECT_ROOT="$(cd -- "$SCRIPT_DIR/../.." && pwd -P)"

printf '提示: start_grpc.sh 已弃用；转交根 start.sh 启动受控 Agent + Web + gRPC 栈。\n' >&2
exec "$PROJECT_ROOT/start.sh" --grpc "$@"
