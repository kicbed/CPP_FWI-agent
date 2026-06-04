#!/usr/bin/env bash
set -euo pipefail

# Agent Communication RPC Framework environment setup
# Target: Ubuntu 20.04/22.04+, CMake >=3.20, GCC >=9, Redis >=6, gRPC v1.51.1
# Usage: bash setup_agent_env_grpc151.sh

JOBS="${JOBS:-2}"   # If your server has 4GB+ RAM, you can run: JOBS=4 bash setup_agent_env_grpc151.sh
GRPC_VERSION="v1.51.1"
CMAKE_VERSION="3.28.6"
SRC_DIR="${SRC_DIR:-/opt/src}"

need_sudo() {
  if [[ $EUID -eq 0 ]]; then echo ""; else echo "sudo"; fi
}
SUDO=$(need_sudo)

echo "[1/8] System info"
lsb_release -a || true
uname -a

echo "[2/8] Install base packages"
$SUDO apt-get update
$SUDO env DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential git pkg-config curl wget unzip ca-certificates \
  autoconf automake libtool make ninja-build \
  libssl-dev zlib1g-dev \
  libcurl4-openssl-dev libjsoncpp-dev uuid-dev \
  libgtest-dev libhiredis-dev redis-server nlohmann-json3-dev

# Install newer CMake because mcp_server_integrated requires CMake >= 3.20.
echo "[3/8] Install CMake ${CMAKE_VERSION} under /opt"
if command -v cmake >/dev/null 2>&1; then
  echo "Current cmake: $(cmake --version | head -1)"
fi
if ! cmake --version 2>/dev/null | head -1 | grep -Eq '3\.(2[0-9]|[3-9][0-9])'; then
  cd /tmp
  CMAKE_TGZ="cmake-${CMAKE_VERSION}-linux-x86_64.tar.gz"
  wget -q --show-progress "https://github.com/Kitware/CMake/releases/download/v${CMAKE_VERSION}/${CMAKE_TGZ}"
  $SUDO rm -rf "/opt/cmake-${CMAKE_VERSION}-linux-x86_64"
  $SUDO tar -xzf "$CMAKE_TGZ" -C /opt
  $SUDO ln -sf "/opt/cmake-${CMAKE_VERSION}-linux-x86_64/bin/cmake" /usr/local/bin/cmake
  $SUDO ln -sf "/opt/cmake-${CMAKE_VERSION}-linux-x86_64/bin/ctest" /usr/local/bin/ctest
  $SUDO ln -sf "/opt/cmake-${CMAKE_VERSION}-linux-x86_64/bin/cpack" /usr/local/bin/cpack
fi
cmake --version | head -1

# Prefer g++-11 if available, otherwise use system default g++.
echo "[4/8] Check compiler"
$SUDO apt-get install -y gcc-11 g++-11 || true
if command -v g++-11 >/dev/null 2>&1; then
  $SUDO update-alternatives --install /usr/bin/gcc gcc /usr/bin/gcc-11 110 || true
  $SUDO update-alternatives --install /usr/bin/g++ g++ /usr/bin/g++-11 110 || true
fi
g++ --version | head -1

# Build and install gRPC v1.51.1 from source.
echo "[5/8] Build and install gRPC ${GRPC_VERSION} (this may take a while)"
$SUDO mkdir -p "$SRC_DIR"
$SUDO chown -R "$USER":"$USER" "$SRC_DIR"
cd "$SRC_DIR"
if [[ ! -d grpc ]]; then
  git clone --recurse-submodules -b "$GRPC_VERSION" --depth 1 https://github.com/grpc/grpc.git
else
  cd grpc
  git fetch --tags
  git checkout "$GRPC_VERSION"
  git submodule update --init --recursive
  cd ..
fi
cd grpc
mkdir -p cmake/build
cd cmake/build
cmake ../.. \
  -DCMAKE_BUILD_TYPE=Release \
  -DCMAKE_INSTALL_PREFIX=/usr/local \
  -DgRPC_INSTALL=ON \
  -DgRPC_BUILD_TESTS=OFF \
  -DgRPC_ABSL_PROVIDER=module \
  -DgRPC_CARES_PROVIDER=module \
  -DgRPC_PROTOBUF_PROVIDER=module \
  -DgRPC_RE2_PROVIDER=module \
  -DgRPC_SSL_PROVIDER=package \
  -DgRPC_ZLIB_PROVIDER=package
cmake --build . --target install -j"$JOBS"
$SUDO ldconfig

# The project hardcodes /usr/bin/grpc_cpp_plugin in proto/CMakeLists.txt.
# If gRPC installed it into /usr/local/bin, create a compatibility symlink.
echo "[6/8] Fix grpc_cpp_plugin path expected by this project"
if [[ -x /usr/local/bin/grpc_cpp_plugin ]]; then
  $SUDO ln -sf /usr/local/bin/grpc_cpp_plugin /usr/bin/grpc_cpp_plugin
fi

# Make pkg-config and PATH permanent.
echo "[7/8] Configure PATH and PKG_CONFIG_PATH"
cat <<'PROFILE' | $SUDO tee /etc/profile.d/grpc151.sh >/dev/null
export PATH=/usr/local/bin:$PATH
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:/usr/local/lib64/pkgconfig:$PKG_CONFIG_PATH
export LD_LIBRARY_PATH=/usr/local/lib:/usr/local/lib64:$LD_LIBRARY_PATH
PROFILE
export PATH=/usr/local/bin:$PATH
export PKG_CONFIG_PATH=/usr/local/lib/pkgconfig:/usr/local/lib64/pkgconfig:${PKG_CONFIG_PATH:-}
export LD_LIBRARY_PATH=/usr/local/lib:/usr/local/lib64:${LD_LIBRARY_PATH:-}

# Install RapidCheck, because this project's tests/CMakeLists.txt calls find_package(rapidcheck REQUIRED).
echo "[8/8] Install RapidCheck for project tests"
cd "$SRC_DIR"
if [[ ! -d rapidcheck ]]; then
  git clone https://github.com/emil-e/rapidcheck.git
fi
cd rapidcheck
mkdir -p build
cd build
cmake .. -DCMAKE_BUILD_TYPE=Release -DRC_ENABLE_TESTS=OFF -DRC_ENABLE_EXAMPLES=OFF
cmake --build . -j"$JOBS"
$SUDO cmake --install . || $SUDO make install
$SUDO ldconfig

# Start Redis.
$SUDO systemctl enable redis-server >/dev/null 2>&1 || true
$SUDO systemctl restart redis-server || true

echo ""
echo "========== Environment check =========="
echo "cmake:       $(cmake --version | head -1)"
echo "g++:         $(g++ --version | head -1)"
echo "protoc:      $(protoc --version || true)"
echo "grpc++ pc:   $(pkg-config --modversion grpc++ || true)"
echo "protobuf pc: $(pkg-config --modversion protobuf || true)"
echo "grpc plugin: $(which grpc_cpp_plugin || true)"
echo "redis:       $(redis-cli ping || true)"
echo "======================================="
echo "Done. If you open a new shell, /etc/profile.d/grpc151.sh will set PATH/PKG_CONFIG_PATH automatically."
