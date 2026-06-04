#!/usr/bin/env bash
set -euo pipefail

# Usage: place this file in the project root and run: bash build_agent_project.sh
# Or run: PROJECT_DIR=/path/to/-agent-communication-main bash build_agent_project.sh

PROJECT_DIR="${PROJECT_DIR:-$(pwd)}"
JOBS="${JOBS:-2}"
cd "$PROJECT_DIR"

export PATH=/usr/local/bin:$PATH
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:/usr/local/lib64/pkgconfig:${PKG_CONFIG_PATH:-}
export LD_LIBRARY_PATH=/usr/local/lib:/usr/local/lib64:${LD_LIBRARY_PATH:-}

# This project hardcodes /usr/bin/grpc_cpp_plugin. Ensure it exists.
if [[ ! -x /usr/bin/grpc_cpp_plugin && -x /usr/local/bin/grpc_cpp_plugin ]]; then
  sudo ln -sf /usr/local/bin/grpc_cpp_plugin /usr/bin/grpc_cpp_plugin
fi

echo "[Check] grpc++ version: $(pkg-config --modversion grpc++ || true)"
echo "[Check] protoc version: $(protoc --version || true)"
echo "[Check] cmake version:  $(cmake --version | head -1)"

# Build main project
rm -rf build
mkdir -p build
cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
cmake --build . -j"$JOBS"
cd ..

# Build MCP server
cd mcp_server_integrated
rm -rf build
mkdir -p build
cd build
cmake -DCMAKE_BUILD_TYPE=Release ..
cmake --build . -j"$JOBS"
cd ../..

echo ""
echo "========== Build result check =========="
ls -la build/server/rpc_server
ls -la build/client/rpc_client
ls -la build/examples/ai_orchestrator/ai_orchestrator
ls -la build/examples/ai_orchestrator/ai_math_agent
ls -la build/examples/ai_orchestrator/ai_registry_server
ls -la mcp_server_integrated/build/mcp_server
echo "========================================"
