# syntax=docker/dockerfile:1.7

ARG UBUNTU_VERSION=22.04

FROM ubuntu:${UBUNTU_VERSION} AS builder

ARG DEBIAN_FRONTEND=noninteractive
ARG BUILD_JOBS=2
ARG TORCH_VERSION=2.12.0
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cpu

RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential \
        ca-certificates \
        cmake \
        libcurl4-openssl-dev \
        libgrpc++-dev \
        libgtest-dev \
        libhiredis-dev \
        libjsoncpp-dev \
        libprotobuf-dev \
        librapidcheck-dev \
        nlohmann-json3-dev \
        pkg-config \
        protobuf-compiler \
        protobuf-compiler-grpc \
        python3 \
        python3-pip \
        python3-venv \
        uuid-dev \
    && rm -rf /var/lib/apt/lists/*

# The MCP runner intentionally uses this fixed interpreter path. No API key or
# other secret is accepted as a build argument or persisted in this layer.
RUN python3 -m venv /root/.venvs/cpp-fwi-agent \
    && /root/.venvs/cpp-fwi-agent/bin/python -m pip install --no-cache-dir --upgrade pip \
    && /root/.venvs/cpp-fwi-agent/bin/python -m pip install --no-cache-dir \
        "torch==${TORCH_VERSION}" --index-url "${TORCH_INDEX_URL}" \
    && /root/.venvs/cpp-fwi-agent/bin/python -m pip install --no-cache-dir \
        "deepwave==0.0.27" \
        "matplotlib>=3.8,<4" \
        "numpy>=1.26,<3" \
        "pillow>=10,<13" \
        "pydantic>=2,<3" \
        "pyyaml>=6,<7" \
        "scipy>=1.11,<2"

WORKDIR /opt/fwi-agent
COPY . .

# Compile from the clean source context. Host build trees are excluded by
# .dockerignore and are never copied into the image.
RUN cmake -S . -B /tmp/fwi-main-build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/fwi-main-build --parallel "${BUILD_JOBS}" --target \
        ai_client \
        ai_code_agent \
        ai_experiment_planner_agent \
        ai_fwi_teaching_agent \
        ai_fwi_theory_agent \
        ai_general_research_agent \
        ai_math_agent \
        ai_orchestrator \
        ai_registry_server \
        test_fwi_tool_routing \
    && ctest --test-dir /tmp/fwi-main-build -R '^FWIToolRoutingTest$' --output-on-failure \
    && cmake -S mcp_server_integrated -B /tmp/fwi-mcp-build -DCMAKE_BUILD_TYPE=Release \
    && cmake --build /tmp/fwi-mcp-build --parallel "${BUILD_JOBS}" \
    && ctest --test-dir /tmp/fwi-mcp-build -R '^FWIRunnerPluginTest$' --output-on-failure

RUN install -d \
        /opt/fwi-runtime/build/examples/ai_orchestrator \
        /opt/fwi-runtime/mcp_server_integrated/build/plugins \
    && for binary in \
        ai_client \
        ai_code_agent \
        ai_experiment_planner_agent \
        ai_fwi_teaching_agent \
        ai_fwi_theory_agent \
        ai_general_research_agent \
        ai_math_agent \
        ai_orchestrator \
        ai_registry_server; do \
            install -m 0755 "/tmp/fwi-main-build/examples/ai_orchestrator/${binary}" \
                "/opt/fwi-runtime/build/examples/ai_orchestrator/${binary}"; \
        done \
    && install -m 0755 /tmp/fwi-mcp-build/mcp_server \
        /opt/fwi-runtime/mcp_server_integrated/build/mcp_server \
    && find /tmp/fwi-mcp-build/plugins -type f -name '*.so' \
        -exec install -m 0755 '{}' \
            /opt/fwi-runtime/mcp_server_integrated/build/plugins/ ';'


FROM ubuntu:${UBUNTU_VERSION} AS runtime

ARG DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates \
        curl \
        iproute2 \
        libcurl4 \
        libgomp1 \
        libgrpc++1 \
        libhiredis0.14 \
        libjsoncpp25 \
        libprotobuf23 \
        libstdc++6 \
        libuuid1 \
        python3 \
        redis-server \
        redis-tools \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/fwi-agent

# This second source copy is still governed by .dockerignore, so it contains no
# host build tree, model, run artifact, virtualenv, or .env file.
COPY . .
COPY --from=builder /root/.venvs/cpp-fwi-agent /root/.venvs/cpp-fwi-agent
COPY --from=builder /opt/fwi-runtime/build /opt/fwi-agent/build
COPY --from=builder /opt/fwi-runtime/mcp_server_integrated/build \
    /opt/fwi-agent/mcp_server_integrated/build

# fwi-metadata currently resolves its read-only resource directory relative to
# the process. This compatibility link keeps it functional without retaining a
# CMake build tree in the runtime image.
RUN mkdir -p \
        /root/fwi-data/models \
        /root/fwi-runs \
        /opt/fwi-agent/examples/ai_orchestrator/logs \
        /opt/fwi-agent/examples/ai_orchestrator/pids \
    && ln -s /opt/fwi-agent/resources /resources

ENV PATH=/root/.venvs/cpp-fwi-agent/bin:/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin \
    PYTHONPATH=/opt/fwi-agent \
    FWI_RUN_ROOT=/root/fwi-runs \
    MPLBACKEND=Agg \
    MPLCONFIGDIR=/tmp/matplotlib \
    XDG_CACHE_HOME=/tmp/cache \
    TORCH_HOME=/tmp/torch \
    TORCH_EXTENSIONS_DIR=/tmp/torch-extensions \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

EXPOSE 5000 8080

# compose.yaml supplies the process supervisor command and runtime-only env_file.
CMD ["/bin/bash"]
