# Lab Research Agent Platform

FWI-first research computing agent workbench built on C++, gRPC, A2A, MCP, RAG,
and multi-agent orchestration.

The current version supports research Q&A, FWI knowledge retrieval, MCP tool
calling, multi-agent routing, and Web/CLI access. Real CUDA/MPI or cluster job
execution is not enabled yet; v0.2 adds dry-run experiment planning and reserves
the backend interface for later Slurm/PBS/server integration.

**首个落地场景**: FWI（全波形反演）科研助手平台

## 产品层次

- **Client**: CLI client, Web UI, gRPC client, and HTTP bridge.
- **Orchestrator**: Routes user requests to specialized agents and tools.
- **Agents**: Math, FWI Theory, FWI Teaching, General Research, Code Agent, and
  Experiment Planner agent.
- **MCP Tools**: Standardized tool discovery and invocation for calculators,
  FWI metadata, and future lab utilities.
- **Knowledge**: Local Markdown/JSON knowledge retrieval plus embedding-based
  routing support.
- **Experiment Planning**: Planned v0.2 AlgorithmCard, ExperimentSpec, JobSpec,
  and DryRunBackend models for safe dry-run planning.

## 当前限制

- 不执行真实 CUDA/MPI 作业。
- 不连接 SSH、Slurm、PBS 或远程服务器。
- 不从用户输入执行任意 shell 命令。
- Code Agent 默认只读；可以生成补丁建议，但不会自动应用补丁。

## 核心特性

- **Agent-RAG 动态路由**: 基于 Embedding 语义匹配，自动选择最合适的 Agent
- **Tool-RAG + LLM Tool Calling**: 智能工具选择，LLM 生成调用参数
- **三层记忆管理**: Session Memory / Agent Memory / Task State
- **MCP 工具扩展**: 标准化工具协议，支持动态加载
- **本地知识库**: Markdown/JSON 知识文件，支持关键词检索
- **多 Agent 协作**: MathAgent, FWITheoryAgent, FWITeachingAgent, GeneralResearchAgent
- **向量持久化**: AgentCard 向量缓存到文件，避免重复 API 调用

## 快速开始

```bash
# 1. 一键设置
./setup.sh

# 2. 编辑配置文件（填入你的 API Key）
nano .env

# 3. 启动本地 Embedding 服务（可选，推荐）
nohup python3 deploy/scripts/embedding_server.py --model BAAI/bge-small-zh-v1.5 --port 6000 > deploy/logs/embedding.log 2>&1 &

# 4. 启动系统
./examples/ai_orchestrator/start_system.sh

# 5. 使用交互式客户端
./build/examples/ai_orchestrator/ai_client http://localhost:5000
```

**交互示例**:
```
[default] > 什么是 FWI
AI: FWI（全波形反演）是...

[default] > 计算 123 * 456
AI: 123 × 456 = 56088

[default] > /quit
```

### 快速 demo 命令

这些命令只启动本机 `localhost` 演示服务；不会执行真实 CUDA/MPI 作业，
也不会连接 SSH、Slurm、PBS 或远程服务器。

**HTTP 终端客户端**

```bash
./deploy/scripts/start_http.sh
```

Expected:

- Starts local Embedding on `:6000`.
- Starts the agent system with Orchestrator on `:5000`.
- Opens `ai_client` against `http://localhost:5000`.

**gRPC bridge**

```bash
# Terminal 1
./deploy/scripts/start_grpc.sh

# Terminal 2
curl http://localhost:50052/health
```

Expected:

- Starts `rpc_server` on gRPC `:50051`.
- Starts the HTTP bridge on `:50052` for browser clients.
- Opens `grpc_ai_client` against `localhost:50051`.

**Web UI**

```bash
# Terminal 1
./deploy/scripts/start_http.sh

# Terminal 2
./deploy/scripts/start_web.sh 8080
```

Open `http://localhost:8080` in a browser. Use HTTP mode for the direct
Orchestrator path, or start `./deploy/scripts/start_grpc.sh` first and switch
the UI to gRPC mode to exercise the bridge path.

**Local embedding only**

```bash
mkdir -p deploy/logs
nohup python3 deploy/scripts/embedding_server.py \
  --model Qwen/Qwen3-Embedding-0.6B \
  --port 6000 \
  > deploy/logs/embedding.log 2>&1 &
curl http://localhost:6000/health
```

Use this when you only want to verify local vector embedding before starting
the full agent system.

### Code Agent 路由 smoke test

启动系统并打开交互式客户端：

```bash
./examples/ai_orchestrator/start_system.sh
./build/examples/ai_orchestrator/ai_client http://localhost:5000
```

在客户端输入：

```text
[default] > 这个项目里 Orchestrator 的 code intent 路由在哪里？请指出文件和逻辑。
```

Expected:

- Routes to the Code Agent when `ai_code_agent` is running instead of falling
  back to the general handler.
- References `examples/ai_orchestrator/orchestrator_main.cpp`.
- Explains that `intent == "code"` calls `call_code_agent(...)`, which then
  finds an agent through `call_agent_by_tag("code", ...)`.
- Does not claim that files were changed or commands were executed.

### v0.2 Demo

Use the scripted v0.2 runbook for a complete local demo:

- [v0.2 demo script](docs/upgrade/demo-script-v0.2.md)
- [v0.2 test report](docs/upgrade/test-report-v0.2.md)

The demo covers FWI knowledge Q&A, Code Agent routing, and direct dry-run
experiment planning through the Experiment Planner Agent. It stays on
`localhost` and does not execute CUDA/MPI jobs or connect to SSH, Slurm, PBS, or
remote servers.

## 模型切换指南

### LLM API 切换

支持多种 LLM API，通过环境变量快速切换：

```bash
# DeepSeek（推荐，性价比高）
export LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=sk-xxx

# 小米 MiMo
export LLM_PROVIDER=mimo
export MIMO_API_KEY=sk-xxx

# 通义千问
export LLM_PROVIDER=qwen
export QWEN_API_KEY=sk-xxx

# OpenAI
export LLM_PROVIDER=openai
export OPENAI_API_KEY=sk-xxx

# 本地模型（Ollama）
export LLM_PROVIDER=local
```

**配置文件**: `deploy/config/llm_config.json`

### Embedding 模型切换

#### 方案 1: 本地 Embedding（推荐，免费）

```bash
# 安装依赖
pip3 install sentence-transformers flask

# 启动本地 Embedding 服务
python3 deploy/scripts/embedding_server.py --model Qwen/Qwen3-Embedding-0.6B --port 6000 &

# 配置使用本地 Embedding
export EMBEDDING_PROVIDER=local
export LOCAL_EMBEDDING_URL=http://localhost:6000
```

**推荐模型**:
| 模型 | 大小 | 中文效果 | 推荐度 |
|------|------|----------|--------|
| **Qwen3-Embedding-0.6B** | 0.6B | 很好 | ⭐⭐⭐⭐⭐ |
| BGE-small-zh-v1.5 | 0.3B | 好 | ⭐⭐⭐⭐ |
| M3E-small | 0.3B | 好 | ⭐⭐⭐⭐ |

#### 方案 2: DashScope API

```bash
export EMBEDDING_PROVIDER=dashscope
export DASHSCOPE_API_KEY=sk-xxx
```

### 完整配置示例

```bash
# 使用 DeepSeek + 本地 Embedding
export LLM_PROVIDER=deepseek
export DEEPSEEK_API_KEY=sk-your-deepseek-key
export EMBEDDING_PROVIDER=local
export LOCAL_EMBEDDING_URL=http://localhost:6000
export ROUTING_MODE=agent-rag
export ENABLE_MCP=true

# 启动系统
./examples/ai_orchestrator/start_system.sh
```

---

## 目录

- [项目概述](#项目概述)
- [系统架构](#系统架构)
- [环境要求](#环境要求)
- [编译构建](#编译构建)
- [快速启动](#快速启动)
- [完整部署指南](#完整部署指南)
- [功能模块](#功能模块)
- [配置说明](#配置说明)
- [API 参考](#api-参考)
- [测试](#测试)
- [常见问题](#常见问题)

---

## 项目概述

Agent Communication RPC Framework 是一个专为多 Agent 协作场景设计的通信框架。

### 核心能力

| 能力 | 说明 |
|------|------|
| gRPC 通信 | 基于 gRPC 的高性能远程过程调用 |
| A2A 协议 | Agent-to-Agent 通信协议支持 |
| MCP 集成 | Model Context Protocol 工具调用 |
| RAG-MCP | 基于检索增强生成的智能工具选择 |
| 服务发现 | 支持内存注册中心 |
| 多 Agent 协作 | Orchestrator 协调多个专业 Agent |

### 技术栈

- **语言**: C++17
- **RPC 框架**: gRPC + Protocol Buffers
- **HTTP 客户端**: libcurl
- **JSON 处理**: nlohmann/json, jsoncpp
- **测试框架**: Google Test + RapidCheck (属性测试)
- **向量化服务**: 阿里百炼 DashScope API
- **AI 模型**: 通义千问 (Qwen)

---

## 系统架构

### 整体架构图

```
┌─────────────────────────────────────────────────────────────────────────────┐
│                              用户应用层                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         RPC Client                                   │   │
│  │                    (build/client/rpc_client)                         │   │
│  └──────────────────────────────┬──────────────────────────────────────┘   │
└─────────────────────────────────┼───────────────────────────────────────────┘
                                  │ gRPC/Protobuf
                                  ▼
┌─────────────────────────────────────────────────────────────────────────────┐
│                              RPC 服务层                                      │
│  ┌─────────────────────────────────────────────────────────────────────┐   │
│  │                         RPC Server                                   │   │
│  │                    (build/server/rpc_server)                         │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │   │
│  │  │AIQueryService│  │ A2A Adapter │  │HealthService│                  │   │
│  │  └──────┬──────┘  └──────┬──────┘  └─────────────┘                  │   │
│  └─────────┼────────────────┼──────────────────────────────────────────┘   │
└────────────┼────────────────┼───────────────────────────────────────────────┘
             │                │ A2A Protocol (HTTP/JSON-RPC)
             │                ▼
┌────────────┼────────────────────────────────────────────────────────────────┐
│            │           Agent 协调层                                          │
│  ┌─────────┴───────────────────────────────────────────────────────────┐   │
│  │                      Orchestrator Agent                              │   │
│  │                 (ai_orchestrator, 端口 5000)                         │   │
│  │  ┌─────────────┐  ┌─────────────┐  ┌─────────────┐                  │   │
│  │  │ 意图识别     │  │ Agent 路由  │  │ MCP 工具    │                  │   │
│  │  │ (Qwen API)  │  │             │  │             │                  │   │
│  │  └─────────────┘  └──────┬──────┘  └─────────────┘                  │   │
│  └──────────────────────────┼──────────────────────────────────────────┘   │
│                             │                                               │
│            ┌────────────────┼────────────────┐                             │
│            ▼                ▼                ▼                             │
│  ┌─────────────────┐ ┌─────────────────┐ ┌─────────────────┐              │
│  │   Math Agent    │ │   Code Agent    │ │   Other Agent   │              │
│  │ (ai_math_agent) │ │    (可扩展)      │ │    (可扩展)      │              │
│  │   端口 5001     │ │                 │ │                 │              │
│  │  + MCP Tools    │ │  + MCP Tools    │ │  + MCP Tools    │              │
│  └─────────────────┘ └─────────────────┘ └─────────────────┘              │
│                                                                            │
│  ┌─────────────────────────────────────────────────────────────────────┐  │
│  │                      Registry Server                                 │  │
│  │                   (ai_registry_server, 端口 8500)                    │  │
│  └─────────────────────────────────────────────────────────────────────┘  │
└────────────────────────────────────────────────────────────────────────────┘
```

### 数据流向

```
1. 用户输入 "1+7"
2. rpc_client → gRPC → rpc_server (端口 50051)
3. rpc_server → A2A Adapter → Orchestrator (端口 5000)
4. Orchestrator 识别意图为 "math"
5. Orchestrator → Math Agent (端口 5001)
6. Math Agent 调用 MCP calculator 工具
7. 返回结果 "1+7 = 8"
8. 响应原路返回给用户
```

---

## 环境要求

### 系统要求

| 要求 | 版本 |
|------|------|
| 操作系统 | Linux (Ubuntu 20.04+) |
| CMake | 3.15+ |
| C++ 编译器 | GCC 9+ (支持 C++17) |
| Redis | 6.0+ (任务状态存储) |
| grpc | 1.51.1 |

### 安装依赖

```bash
# Ubuntu/Debian
sudo apt-get update
sudo apt-get install -y \
    cmake \
    build-essential \
    pkg-config \
    libgrpc++-dev \
    libprotobuf-dev \
    protobuf-compiler \
    protobuf-compiler-grpc \
    libcurl4-openssl-dev \
    libjsoncpp-dev \
    uuid-dev \
    libgtest-dev \
    libhiredis-dev \
    redis-server \
    nlohmann-json3-dev
```

### 必需的 API Key

| API Key | 用途 | 获取方式 |
|---------|------|----------|
| QWEN_API_KEY | 通义千问 AI 模型，用于对话和意图识别 | [阿里云百炼](https://bailian.console.aliyun.com/) |
| DASHSCOPE_API_KEY | RAG 向量化服务，用于智能工具选择 | [阿里云 DashScope](https://dashscope.console.aliyun.com/) |

> **注意**: 两个 API Key 都是完整功能所必需的。DASHSCOPE_API_KEY 用于 RAG-MCP 的工具向量化和检索功能。

---

## 编译构建

### 1. 编译主项目

```bash
# 克隆项目
git clone <repository-url>
cd agent-communication

# 创建构建目录
mkdir -p build && cd build

# 配置并编译
cmake ..
make -j$(nproc)

# 返回项目根目录
cd ..
```

### 2. 编译 MCP Server (可选，用于工具调用)

```bash
cd mcp_server_integrated
mkdir -p build && cd build
cmake ..
make -j$(nproc)
cd ../..
```

### 3. 验证编译结果

```bash
# 检查主要可执行文件
ls -la build/server/rpc_server
ls -la build/client/rpc_client
ls -la build/examples/ai_orchestrator/ai_orchestrator
ls -la build/examples/ai_orchestrator/ai_math_agent
ls -la build/examples/ai_orchestrator/ai_registry_server

# 检查 MCP Server (如果编译了)
ls -la mcp_server_integrated/build/mcp_server
```

---

## 快速启动（完整功能版）

本快速启动将启动项目的**所有功能**，包括：
- ✅ 多 Agent 协作系统 (Registry + Orchestrator + Math Agent)
- ✅ MCP 工具调用 (calculator, add, subtract, multiply, divide, power, sqrt, factorial)
- ✅ RAG-MCP 智能工具选择 (基于向量相似度动态检索相关工具，而非一次性提供所有工具给 LLM)
- ✅ gRPC 通信 (RPC Server + RPC Client)
- ✅ A2A 协议通信
- ✅ Redis 任务状态存储

> **RAG-MCP 核心价值**: 当 MCP Server 提供大量工具时，RAG 会将工具描述存储在向量索引中，在查询时动态检索与用户任务最相关的工具，而不是将所有工具描述一次性提供给 LLM，从而显著减少 Token 消耗并提高工具选择准确性。

### 第一步：编译所有组件

```bash
# 1. 编译主项目
mkdir -p build && cd build
cmake .. && make -j$(nproc)
cd ..

# 2. 编译 MCP Server（提供工具调用能力）
cd mcp_server_integrated
mkdir -p build && cd build
cmake .. && make -j$(nproc)
cd ../..
```

### 第二步：设置环境变量

```bash
# 必需：通义千问 API Key（用于 AI 对话和意图识别）
export QWEN_API_KEY=sk-your-qwen-api-key

# 必需：DashScope API Key（用于 RAG 智能工具选择）
# RAG 会将工具描述向量化存储，查询时动态检索相关工具
export DASHSCOPE_API_KEY=sk-your-dashscope-api-key
```

### 第三步：启动 Redis

```bash
# 启动 Redis（用于任务状态存储）
sudo systemctl start redis-server

# 验证 Redis 运行
redis-cli ping  # 应返回 PONG
```

### 第四步：启动完整系统

```bash
# 启动多 Agent 系统（包含 MCP 工具 + RAG 智能工具选择）
# 这将启动: Registry Server (8500) + Math Agent (5001) + Orchestrator (5000)
# RAG 会自动将工具描述向量化，查询时动态检索最相关的工具
ENABLE_MCP=true ENABLE_RAG=true ./examples/ai_orchestrator/start_system.sh
```

预期输出：
```
==========================================
AI Agent 系统启动
==========================================
[1/3] 启动 Registry Server...
Registry Server 启动完成 (端口: 8500)
MCP 已启用: /path/to/mcp_server
MCP 插件目录: /path/to/plugins
MCP 日志目录: /path/to/logs
RAG-MCP 已启用: 智能工具选择
  Top-K: 5
  相似度阈值: 0.3
[2/3] 启动 Math Agent...
Math Agent 启动完成 (端口: 5001)
[3/3] 启动 Orchestrator...
Orchestrator 启动完成 (端口: 5000)

==========================================
系统启动完成!
==========================================
```

### 第五步：启动 RPC Server

```bash
# 新开终端，启动 gRPC 服务端
./build/server/rpc_server
```

预期输出：
```
==========================================
RPC Server 启动成功
==========================================
gRPC 地址:      0.0.0.0:50051
Orchestrator:   http://localhost:5000
AI 服务状态:    可用
超时时间:       60 秒
==========================================
```

### 第六步：启动 RPC Client 并测试

```bash
# 新开终端，启动客户端
./build/client/rpc_client
```

在客户端中测试各种功能：

```bash
# 数学计算（通过 MCP calculator 工具）
[default] > 1+7
AI: 1+7 = 8

[default] > 计算 123 * 456
AI: 123 * 456 = 56088

[default] > 2的10次方是多少
AI: 2^10 = 1024

# 切换流式模式
[default] > /stream
流式模式: 开启

# 流式输出测试
[default/流式] > 什么是人工智能
AI: 人工智能是...（逐字输出）

# 查看连接状态
[default] > /status
连接状态: 已连接
服务器: localhost:50051
上下文: default

# 退出
[default] > /quit
```

### 第七步：停止系统

```bash
# 停止所有服务
./examples/ai_orchestrator/stop_system.sh

# 或手动停止
pkill -f rpc_server
pkill -f ai_orchestrator
pkill -f ai_math_agent
pkill -f ai_registry_server
```

### 验证 MCP + RAG 是否启用

查看日志确认 MCP 和 RAG 初始化成功：

```bash
# 查看 Math Agent 日志
grep -E "MCP|RAG" examples/ai_orchestrator/logs/math_agent.log

# 预期输出:
# [MathAgent] MCP 已启用，可用工具: calculator add subtract multiply divide power sqrt factorial
# [INFO ] RAG-MCP initialized successfully
# [INFO ] Indexing 12 tools for RAG retrieval

# 查看 Orchestrator 日志
grep -E "MCP|RAG" examples/ai_orchestrator/logs/orchestrator.log

# 预期输出:
# [Orchestrator] MCP 已启用，可用工具: calculator add subtract multiply divide power sqrt factorial
# [INFO ] RAG-MCP initialized successfully
# [INFO ] Indexing 12 tools for RAG retrieval
```

### RAG 智能工具选择工作原理

当用户发送查询时，RAG-MCP 会：
1. 将用户查询向量化（调用 DashScope Embedding API）
2. 在工具向量索引中搜索最相似的工具
3. 只返回 Top-K 个最相关的工具给 LLM，而非全部工具

```
用户查询 "计算 123 + 456"
        │
        ▼
┌───────────────────┐
│  EmbeddingService │  ← 调用 DashScope API 向量化
│  (text-embedding) │
└─────────┬─────────┘
          │ 向量 [0.12, 0.34, ...]
          ▼
┌───────────────────┐
│   VectorIndex     │  ← 余弦相似度搜索
│   (工具向量库)     │
└─────────┬─────────┘
          │ Top-K 相关工具
          ▼
┌───────────────────┐
│  返回相关工具      │  ← 只有 3-5 个工具，而非全部 12 个
│  - calculator     │
│  - add            │
│  - multiply       │
└───────────────────┘
```

### 单独运行 RAG-MCP 示例

```bash
# 运行 RAG-MCP 示例程序，验证智能工具选择
./build/examples/rag_mcp_example \
    --mcp-server ./mcp_server_integrated/build/mcp_server \
    --enable-rag \
    --top-k 5 \
    --threshold 0.3
```

RAG 示例输出：
```
=== RAG-MCP Framework Example ===

Initializing MCPAgentIntegration...
  MCP Enabled: Yes
  RAG Enabled: Yes
  Initialized: Yes
  Available: Yes
  RAG Active: Yes

Available Tools (12):
  - calculator: 计算数学表达式
  - add: 加法运算
  - subtract: 减法运算
  ...

=== Intelligent Tool Selection Demo ===

Query: "计算 123 + 456 的结果"
Relevant Tools (3):
  - calculator (相关度: 0.89)
  - add (相关度: 0.76)
  - subtract (相关度: 0.45)
```

---

## 完整部署指南（手动启动各组件）

如果需要更精细地控制各组件，可以手动启动每个服务。

### 步骤 1: 准备环境

```bash
# 设置环境变量
export QWEN_API_KEY=sk-your-qwen-api-key
export DASHSCOPE_API_KEY=sk-your-dashscope-api-key  # 用于 RAG 智能工具选择

# 启动 Redis
sudo systemctl start redis-server
redis-cli ping  # 应返回 PONG

# 确保 MCP Server 已编译
ls mcp_server_integrated/build/mcp_server
```

### 步骤 2: 启动 Registry Server

```bash
# 终端 1: Registry Server (Agent 注册中心)
./build/examples/ai_orchestrator/ai_registry_server 8500

# 输出:
# [Registry] 启动在端口 8500
# HTTP Server listening on port 8500
```

### 步骤 3: 启动 Math Agent（含 MCP 工具 + RAG）

```bash
# 终端 2: Math Agent (数学计算专业 Agent + MCP 工具 + RAG 智能工具选择)
./build/examples/ai_orchestrator/ai_math_agent \
    math-1 \
    5001 \
    http://localhost:8500 \
    $QWEN_API_KEY \
    --redis-host 127.0.0.1 \
    --redis-port 6379 \
    --enable-mcp \
    --mcp-server $(pwd)/mcp_server_integrated/build/mcp_server \
    --enable-rag \
    --rag-top-k 5 \
    --rag-threshold 0.3

# 输出:
# [RedisTaskStore] 连接到 Redis 127.0.0.1:6379
# [RedisTaskStore] 连接成功
# [INFO ] MCP client connected to server: .../mcp_server
# [INFO ] Refreshed 12 MCP tools
# [MathAgent] MCP 已启用，可用工具: calculator add subtract multiply divide power sqrt factorial sleep ...
# [INFO ] RAG-MCP initialized successfully
# [INFO ] Indexing 12 tools for RAG retrieval
# [MathAgent] 初始化完成
# [MathAgent] 启动在端口 5001
# [MathAgent] 已注册到服务中心
```

### 步骤 4: 启动 Orchestrator（含 MCP 工具 + RAG）

```bash
# 终端 3: Orchestrator (协调器 + MCP 工具 + RAG 智能工具选择)
./build/examples/ai_orchestrator/ai_orchestrator \
    orch-1 \
    5000 \
    http://localhost:8500 \
    $QWEN_API_KEY \
    --redis-host 127.0.0.1 \
    --redis-port 6379 \
    --enable-mcp \
    --mcp-server $(pwd)/mcp_server_integrated/build/mcp_server \
    --enable-rag \
    --rag-top-k 5 \
    --rag-threshold 0.3

# 输出:
# [RedisTaskStore] 连接到 Redis 127.0.0.1:6379
# [RedisTaskStore] 连接成功
# [INFO ] MCP client connected to server: .../mcp_server
# [INFO ] Refreshed 12 MCP tools
# [Orchestrator] MCP 已启用，可用工具: calculator add subtract multiply divide power sqrt factorial sleep ...
# [INFO ] RAG-MCP initialized successfully
# [INFO ] Indexing 12 tools for RAG retrieval
# [Orchestrator] 初始化完成
# [Orchestrator] 启动在端口 5000
# [Orchestrator] 已注册到服务中心
```

### 步骤 5: 启动 RPC Server

```bash
# 终端 4: RPC Server (gRPC 服务端)
./build/server/rpc_server

# 输出:
# [INFO ] 正在初始化 RPC Server...
# [INFO ] MessageSerializer initialized with ProtobufBinary
# [INFO ] AIQueryService initialized successfully
# [INFO ] AI Query Service registered
# [INFO ] RPC server initialized on 0.0.0.0:50051
# [INFO ] RPC server started successfully
# ==========================================
# RPC Server 启动成功
# ==========================================
# gRPC 地址:      0.0.0.0:50051
# Orchestrator:   http://localhost:5000
# AI 服务状态:    可用
# 超时时间:       60 秒
```

### 步骤 6: 启动 RPC Client

```bash
# 终端 5: RPC Client (用户客户端)
./build/client/rpc_client

# 输出:
# ==========================================
# RPC Client - AI 查询客户端
# ==========================================
# 连接到: localhost:50051
# [INFO ] AIQueryClient connected to localhost:50051
# 连接成功!
# 流式模式: 关闭
# 上下文 ID: default
#
# 命令:
#   /help     - 显示帮助
#   /stream   - 切换流式模式
#   /context <id> - 切换上下文
#   /status   - 查看连接状态
#   /quit     - 退出
#
# [default] >
```

### 步骤 7: 测试完整功能

```bash
# 在 RPC Client 中测试

# === 数学计算（通过 MCP calculator 工具）===
[default] > 1+7
思考中...
AI: 1+7 = 8
[Agent: math-1, 耗时: 856ms]

[default] > 计算 123 * 456
思考中...
AI: 123 * 456 = 56088
[Agent: math-1, 耗时: 743ms]

[default] > 2的10次方
思考中...
AI: 2^10 = 1024
[Agent: math-1, 耗时: 812ms]

[default] > 16的平方根
思考中...
AI: √16 = 4
[Agent: math-1, 耗时: 695ms]

# === 切换流式模式 ===
[default] > /stream
流式模式: 开启

# === 流式输出测试 ===
[default/流式] > 什么是人工智能
AI: 人工智能是...（逐字输出）

# === 查看状态 ===
[default] > /status
连接状态: 已连接
服务器: localhost:50051
上下文: default

# === 退出 ===
[default] > /quit
再见!
```

### 步骤 8: 运行 RAG-MCP 示例

```bash
# 单独运行 RAG-MCP 智能工具选择示例
./build/examples/rag_mcp_example \
    --mcp-server ./mcp_server_integrated/build/mcp_server \
    --enable-rag \
    --top-k 5 \
    --threshold 0.3

# 输出:
# === RAG-MCP Framework Example ===
#
# Initializing MCPAgentIntegration...
#   MCP Enabled: Yes
#   RAG Enabled: Yes
#   Initialized: Yes
#   Available: Yes
#   RAG Active: Yes
#
# Available Tools (12):
#   - progress_test: 进度测试工具
#   - logging_test: 日志测试工具
#   - get_weather: 获取天气信息
#   - calculator: 计算数学表达式
#   - add: 加法运算
#   - subtract: 减法运算
#   - multiply: 乘法运算
#   - divide: 除法运算
#   - power: 幂运算
#   - sqrt: 平方根运算
#   - factorial: 阶乘运算
#   - sleep: 延时工具
#
# === Intelligent Tool Selection Demo ===
#
# Query: "计算 123 + 456 的结果"
# Relevant Tools (5):
#   - calculator
#   - add
#   - subtract
#   - multiply
#   - divide
#
# Query: "今天北京的天气怎么样"
# Relevant Tools (5):
#   - get_weather
#   - ...
```

### 步骤 9: 停止系统

```bash
# 方式 1: 使用停止脚本
./examples/ai_orchestrator/stop_system.sh

# 方式 2: 手动停止 (Ctrl+C 各终端)

# 方式 3: 强制停止所有进程
pkill -f ai_orchestrator
pkill -f ai_math_agent
pkill -f ai_registry_server
pkill -f rpc_server
```

---

## 功能模块

### 1. RPC Server/Client

gRPC 服务端和客户端，提供 AI 查询接口。

```bash
# RPC Server 参数
./build/server/rpc_server [选项]
  -p, --port PORT           监听端口 (默认: 50051)
  -o, --orchestrator URL    Orchestrator 地址 (默认: http://localhost:5000)
  -h, --help                显示帮助

# RPC Client 参数
./build/client/rpc_client [选项] [SERVER_ADDRESS]
  -s, --stream              启用流式模式
  -c, --context ID          设置上下文 ID
  -t, --timeout SEC         超时时间 (默认: 60)
  -h, --help                显示帮助
```

### 2. Multi-Agent 系统

| 组件 | 端口 | 功能 |
|------|------|------|
| Registry Server | 8500 | Agent 注册中心，服务发现 |
| Orchestrator | 5000 | 协调器，意图识别，任务分发 |
| Math Agent | 5001 | 数学计算专业 Agent |

### 3. MCP 工具

当启用 MCP 时，可用以下工具：

| 工具 | 功能 | 示例 |
|------|------|------|
| calculator | 计算表达式 | `1+2*3` → `7` |
| add | 加法 | `add(1, 2)` → `3` |
| subtract | 减法 | `subtract(5, 3)` → `2` |
| multiply | 乘法 | `multiply(4, 5)` → `20` |
| divide | 除法 | `divide(10, 2)` → `5` |
| power | 幂运算 | `power(2, 10)` → `1024` |
| sqrt | 平方根 | `sqrt(16)` → `4` |
| factorial | 阶乘 | `factorial(5)` → `120` |

### 4. MCP Client 传输方式

MCP Client 支持两种传输方式，可根据部署场景选择：

| 传输方式 | 适用场景 | 说明 |
|----------|----------|------|
| STDIO | 本地部署 | 通过进程管道通信，MCP Server 作为子进程运行 |
| SSE | 分布式部署 | 通过 HTTP/SSE 连接远程 MCP Server |

#### STDIO 模式（默认）

适用于 MCP Server 和 Agent 在同一台机器上运行的场景。

```cpp
#include "agent_rpc/mcp/mcp_client.h"

// 方式 1: 简单连接
MCPClient client;
client.connect("/path/to/mcp_server", {"-p", "/plugins"});

// 方式 2: 使用配置结构
MCPConnectionConfig config;
config.transport = MCPTransportType::STDIO;
config.server_path = "/path/to/mcp_server";
config.server_args = {"-p", "/plugins", "-l", "/logs"};

MCPClient client;
client.connect(config);
```

#### SSE 模式（远程部署）

适用于 MCP Server 部署在远程服务器的场景，支持跨网络调用。

```cpp
#include "agent_rpc/mcp/mcp_client.h"

MCPConnectionConfig config;
config.transport = MCPTransportType::SSE;
config.sse_url = "http://remote-server:8080/mcp";  // 远程 MCP Server URL
config.api_key = "your-api-key";                    // API Key（可选）
config.connect_timeout_ms = 5000;                   // 连接超时 5 秒
config.request_timeout_ms = 30000;                  // 请求超时 30 秒
config.verify_ssl = true;                           // 验证 SSL 证书

MCPClient client;
if (client.connect(config)) {
    // 连接成功，使用方式与 STDIO 相同
    auto tools = client.listTools();
    auto result = client.callTool("calculator", "{\"expression\": \"1+1\"}");
}
```

#### SSE 配置参数

| 参数 | 类型 | 默认值 | 说明 |
|------|------|--------|------|
| sse_url | string | - | MCP Server SSE 端点 URL |
| api_key | string | - | API Key（可选，用于认证） |
| connect_timeout_ms | int | 5000 | 连接超时（毫秒） |
| request_timeout_ms | int | 30000 | 请求超时（毫秒） |
| verify_ssl | bool | true | 是否验证 SSL 证书 |

#### 检查传输类型

```cpp
MCPClient client;
client.connect(config);

// 检查当前使用的传输方式
if (client.getTransportType() == MCPTransportType::SSE) {
    std::cout << "使用 SSE 远程连接" << std::endl;
} else {
    std::cout << "使用 STDIO 本地连接" << std::endl;
}
```

### 5. RAG-MCP (智能工具选择)

基于向量相似度的智能工具选择，当 MCP Server 提供大量工具时，RAG 可以自动选择最相关的工具。

#### RAG 工作流程

```
用户查询 "计算 123 + 456"
        │
        ▼
┌───────────────────┐
│  EmbeddingService │  ← 调用 DashScope API 向量化
│  (text-embedding) │
└─────────┬─────────┘
          │ 向量 [0.12, 0.34, ...]
          ▼
┌───────────────────┐
│   VectorIndex     │  ← 余弦相似度搜索
│   (工具向量库)     │
└─────────┬─────────┘
          │ Top-K 相关工具
          ▼
┌───────────────────┐
│  返回相关工具      │
│  - calculator     │
│  - add            │
│  - multiply       │
└───────────────────┘
```

#### RAG 配置参数

| 参数 | 默认值 | 说明 |
|------|--------|------|
| api_key | 环境变量 | DashScope API Key |
| model | text-embedding-v2 | Embedding 模型 |
| top_k | 5 | 返回工具数量 |
| similarity_threshold | 0.3 | 相似度阈值 |
| enable_cache | true | 启用向量缓存 |
| cache_max_size | 1000 | 缓存最大条目 |

#### 运行 RAG 示例

```bash
# 设置 API Key
export DASHSCOPE_API_KEY=sk-your-dashscope-api-key

# 运行 RAG 示例
./build/examples/rag_mcp_example \
    --mcp-server ./mcp_server_integrated/build/mcp_server \
    --enable-rag \
    --top-k 5 \
    --threshold 0.3
```

#### RAG 优势

| 场景 | 无 RAG | 有 RAG |
|------|--------|--------|
| 工具数量 | 返回全部 12 个工具 | 返回最相关的 5 个 |
| LLM Token 消耗 | 高 | 低 (减少 60%+) |
| 响应速度 | 慢 | 快 |
| 工具选择准确性 | 可能选错 | 更精准 |

---

## 配置说明

### 环境变量

| 变量 | 必需 | 说明 | 获取方式 |
|------|------|------|----------|
| QWEN_API_KEY | **是** | 通义千问 API Key，用于 AI 对话和意图识别 | [阿里云百炼](https://bailian.console.aliyun.com/) |
| DASHSCOPE_API_KEY | **是** | DashScope API Key，用于 RAG 向量化和智能工具选择 | [阿里云 DashScope](https://dashscope.console.aliyun.com/) |
| ENABLE_MCP | 否 | 是否启用 MCP 工具 (true/false，默认 false) | - |
| ENABLE_RAG | 否 | 是否启用 RAG 智能工具选择 (true/false，默认 false) | - |
| RAG_TOP_K | 否 | RAG 返回工具数量 (默认: 5) | - |
| RAG_THRESHOLD | 否 | RAG 相似度阈值 (默认: 0.3) | - |
| RPC_SERVER_PORT | 否 | RPC Server 端口 (默认: 50051) | - |
| ORCHESTRATOR_URL | 否 | Orchestrator 地址 (默认: http://localhost:5000) | - |

```bash
# 设置所有环境变量（完整功能）
export QWEN_API_KEY=sk-your-qwen-api-key
export DASHSCOPE_API_KEY=sk-your-dashscope-api-key
export ENABLE_MCP=true
export ENABLE_RAG=true
export RAG_TOP_K=5
export RAG_THRESHOLD=0.3
```

### Agent 启动参数

```bash
# Orchestrator / Math Agent 通用参数
./ai_orchestrator <agent_id> <port> <registry_url> <api_key> [选项]

选项:
  --redis-host <host>     Redis 主机 (默认: 127.0.0.1)
  --redis-port <port>     Redis 端口 (默认: 6379)
  --enable-mcp            启用 MCP 工具
  --mcp-server <path>     MCP Server 路径
  --mcp-args <args>       MCP Server 参数 (逗号分隔)
  --enable-rag            启用 RAG 智能工具选择
  --rag-api-key <key>     DashScope API Key (也可通过环境变量设置)
  --rag-top-k <n>         返回工具数量 (默认: 5)
  --rag-threshold <f>     相似度阈值 (默认: 0.3)
  --rag-model <model>     Embedding 模型 (默认: text-embedding-v2)
```

---

## API 参考

### RPC Client 交互命令

| 命令 | 说明 |
|------|------|
| `/help` | 显示帮助 |
| `/stream` | 切换流式模式 |
| `/context <id>` | 切换对话上下文 |
| `/status` | 查看连接状态 |
| `/quit` | 退出 |

### gRPC 服务接口

```protobuf
service AIQueryService {
    // 同步查询
    rpc Query(AIQueryRequest) returns (AIQueryResponse);
    
    // 流式查询
    rpc QueryStream(AIQueryRequest) returns (stream AIStreamEvent);
    
    // 获取查询状态
    rpc GetQueryStatus(QueryStatusRequest) returns (QueryStatusResponse);
}
```

---

## 测试

### 运行所有测试

```bash
cd build
ctest --output-on-failure
```

### 运行特定测试

```bash
# MCP 集成测试
./build/tests/test_mcp_integration

# RAG-MCP 属性测试
./build/tests/test_rag_mcp_properties

# A2A 适配器测试
./build/tests/test_adapter_properties

# Protobuf 往返测试
./build/tests/test_proto_roundtrip
```

---

## 常见问题

### Q: 启动时报错 "找不到可执行文件"

```bash
# 解决: 先编译项目
cd build && cmake .. && make -j$(nproc)
```

### Q: 连接 Orchestrator 失败

```bash
# 检查 Orchestrator 是否运行
curl http://localhost:5000/.well-known/agent-card.json

# 查看日志
tail -f examples/ai_orchestrator/logs/orchestrator.log
```

### Q: Redis 连接失败

```bash
# 启动 Redis
sudo systemctl start redis-server

# 验证
redis-cli ping  # 应返回 PONG
```

### Q: API Key 无效

```bash
# 确保设置了正确的 API Key
echo $QWEN_API_KEY

# 重新设置
export QWEN_API_KEY=sk-your-actual-api-key
```

### Q: MCP 工具不可用

```bash
# 1. 确保编译了 MCP Server
cd mcp_server_integrated/build && cmake .. && make -j$(nproc)

# 2. 使用 ENABLE_MCP=true 启动
ENABLE_MCP=true ./examples/ai_orchestrator/start_system.sh

# 3. 检查日志确认 MCP 初始化
grep "MCP 已启用" examples/ai_orchestrator/logs/orchestrator.log
```

### Q: Ctrl+C 无法退出 Client

这是正常的，因为 `std::getline()` 是阻塞的。按两次 Ctrl+C 或输入 `/quit` 退出。

### Q: RAG 功能不工作

```bash
# 1. 确保设置了 DashScope API Key
echo $DASHSCOPE_API_KEY

# 2. 重新设置
export DASHSCOPE_API_KEY=sk-your-dashscope-api-key

# 3. 确保启动时启用了 RAG
ENABLE_MCP=true ENABLE_RAG=true ./examples/ai_orchestrator/start_system.sh

# 4. 检查日志确认 RAG 初始化
grep "RAG" examples/ai_orchestrator/logs/orchestrator.log

# 预期输出:
# [INFO ] RAG-MCP initialized successfully
# [INFO ] Indexing 12 tools for RAG retrieval

# 5. 运行 RAG 示例验证
./build/examples/rag_mcp_example \
    --mcp-server ./mcp_server_integrated/build/mcp_server \
    --enable-rag

# 6. 检查输出中是否显示 "RAG Active: Yes"
```

### Q: RAG 返回的工具不相关

```bash
# 调整 RAG 参数
# 1. 增加 top_k 返回更多工具
export RAG_TOP_K=10

# 2. 降低相似度阈值，允许更多工具通过
export RAG_THRESHOLD=0.2

# 3. 重启系统
./examples/ai_orchestrator/stop_system.sh
ENABLE_MCP=true ENABLE_RAG=true ./examples/ai_orchestrator/start_system.sh
```

### Q: 查询返回 "错误: Success"

这通常是 A2A 响应解析问题。确保：
1. Orchestrator 正在运行
2. Math Agent 正在运行
3. 查看日志确认请求是否到达

```bash
# 查看 Orchestrator 日志
tail -f examples/ai_orchestrator/logs/orchestrator.log

# 查看 Math Agent 日志
tail -f examples/ai_orchestrator/logs/math_agent.log
```

---

## 项目结构

```
agent-communication/
├── common/                      # 公共组件 (日志、类型、指标)
├── server/                      # RPC 服务端
├── client/                      # RPC 客户端
├── a2a/                         # A2A 协议实现
├── a2a_adapter/                 # A2A 适配层
├── orchestrator/                # Agent 编排
├── mcp/                         # MCP 模块 + RAG-MCP
├── mcp_server_integrated/       # MCP Server 及插件
├── proto/                       # Protobuf 定义
├── examples/                    # 示例代码
│   └── ai_orchestrator/         # 多 Agent 系统示例
├── tests/                       # 测试代码
├── docs/                        # 详细文档
│   ├── architecture.md          # 架构设计
│   ├── a2a-protocol.md          # A2A 协议
│   ├── rag-mcp-guide.md         # RAG-MCP 指南
│   ├── mcp-plugin-development.md # MCP 插件开发
│   └── deployment.md            # 部署指南
└── README.md                    # 本文档
```

---

## 许可证

MIT License
