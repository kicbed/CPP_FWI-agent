# Phase 0: 基线运行验证 — 学习文档

## 一、目标

确认当前系统可以正常编译、启动、运行，建立一个可工作的基线。

## 二、为什么要先做基线验证

在任何架构升级之前，必须先确认：
1. **当前代码能编译** — 否则后续修改无法验证
2. **当前系统能运行** — 否则不知道是新引入的 bug 还是原有的
3. **当前功能正常** — 作为回归测试的基准

这是软件工程中的**"先让它跑起来"原则**：不要在不确定代码状态的情况下做修改。

## 三、做了什么

### 3.1 编译

```bash
mkdir -p build && cd build
cmake ..
make -j$(nproc)
```

**技术原理**:
- **CMake**: 跨平台构建系统生成器，读取 `CMakeLists.txt` 生成 Makefile
- **Make**: 并行编译 (`-j$(nproc)`) 利用多核 CPU 加速

**编译结果**:
- `build/server/rpc_server` — gRPC 服务端
- `build/client/rpc_client` — gRPC 客户端
- `build/examples/ai_orchestrator/ai_orchestrator` — Orchestrator
- `build/examples/ai_orchestrator/ai_math_agent` — MathAgent
- `build/examples/ai_orchestrator/ai_registry_server` — Registry

### 3.2 启动 Redis

```bash
redis-server --daemonize yes
redis-cli ping  # 应返回 PONG
```

**为什么需要 Redis**:
- 存储任务状态 (`a2a:task:*`)
- 存储对话历史 (`a2a:history:*`)
- 支持多进程/多机器共享状态

### 3.3 启动完整系统

```bash
export QWEN_API_KEY="sk-xxx"
export DASHSCOPE_API_KEY="sk-xxx"
export ENABLE_MCP=true
export ENABLE_RAG=false
./examples/ai_orchestrator/start_system.sh
```

**启动顺序**:
1. Registry Server (端口 8500) — Agent 注册中心
2. MathAgent (端口 5001) — 数学计算 Agent
3. Orchestrator (端口 5000) — 协调器

**为什么这个顺序**:
- Agent 启动时需要向 Registry 注册自己
- Orchestrator 启动时需要从 Registry 发现可用 Agent
- 所以 Registry 必须先启动

### 3.4 测试三种查询

#### 数学查询
```bash
curl -X POST http://localhost:5000/ -d '{
  "jsonrpc":"2.0","id":"test-1","method":"message/send",
  "params":{"message":{"role":"user","contextId":"test-ctx",
  "parts":[{"kind":"text","text":"计算 1+1"}]}}
}'
```
**结果**: `1 + 1 = 2`

#### 通用查询
```bash
curl -X POST http://localhost:5000/ -d '{
  "jsonrpc":"2.0","id":"test-2","method":"message/send",
  "params":{"message":{"role":"user","contextId":"test-ctx-2",
  "parts":[{"kind":"text","text":"什么是人工智能"}]}}
}'
```
**结果**: 详细的 AI 介绍

#### FWI 查询
```bash
curl -X POST http://localhost:5000/ -d '{
  "jsonrpc":"2.0","id":"test-3","method":"message/send",
  "params":{"message":{"role":"user","contextId":"test-ctx-3",
  "parts":[{"kind":"text","text":"什么是 cycle skipping"}]}}
}'
```
**结果**: 详细的 FWI 理论解释

### 3.5 检查 Redis Key

```bash
redis-cli keys "*"
```

**结果**:
```
a2a:task:default
a2a:task:test-ctx
a2a:task:test-ctx-2
a2a:task:test-ctx-3
a2a:history:default
a2a:history:test-ctx
a2a:history:test-ctx-2
a2a:history:test-ctx-3
```

## 四、当前请求链路

```
用户输入
  │
  ▼
rpc_client (gRPC 客户端)
  │ gRPC/Protobuf
  ▼
rpc_server (端口 50051)
  │ AIQueryService::Query()
  ▼
A2AAdapter
  │ 转换为 A2A JSON-RPC
  ▼
Orchestrator (端口 5000)
  │ analyze_intent() → 调用 QwenClient 识别意图
  │ if intent == "math" → call_math_agent()
  │ if intent == "code" → call_code_agent()
  │ if intent == "fwi"  → handle_fwi_query()
  │ else                → handle_general_query()
  ▼
MathAgent (端口 5001) 或 直接处理
  │ solve_math() → tryMCPCalculation()
  ▼
响应原路返回
```

## 五、关键发现

### 已实现功能
- ✅ gRPC 通信
- ✅ A2A 协议
- ✅ Agent 注册/发现
- ✅ 意图识别 (LLM)
- ✅ MCP 工具调用
- ✅ RAG-MCP 工具检索
- ✅ Redis 状态存储

### 需要改进
- ❌ 无请求追踪 (无法区分不同请求)
- ❌ 所有 Agent 共用同一个 history key
- ❌ 路由硬编码 (if-else)
- ❌ 无配置管理
