#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

echo "提示：setup.sh 已合并到根目录一键启动流程。"
echo "现在将执行: ./start.sh --rebuild"
exec "$ROOT_DIR/start.sh" --rebuild "$@"
