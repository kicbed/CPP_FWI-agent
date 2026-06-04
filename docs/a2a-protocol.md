# A2A 协议详细说明

## 概述

A2A (Agent-to-Agent) 协议是一种用于 AI Agent 之间通信的标准协议。本框架实现了 A2A 协议的核心功能，支持多 Agent 协作场景。

## 协议架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         A2A Protocol                            │
├─────────────────────────────────────────────────────────────────┤
│                                                                 │
│  ┌─────────────┐    HTTP/JSON-RPC    ┌─────────────┐           │
│  │   Client    │ ◄─────────────────► │   Server    │           │
│  │  (A2AClient)│                     │(TaskManager)│           │
│  └─────────────┘                     └─────────────┘           │
│                                                                 │
│  消息格式:                                                       │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │ {                                                        │   │
│  │   "jsonrpc": "2.0",                                     │   │
│  │   "method": "message/send",                             │   │
│  │   "params": { ... },                                    │   │
│  │   "id": "request-id"                                    │   │
│  │ }                                                        │   │
│  └─────────────────────────────────────────────────────────┘   │
│                                                                 │
└─────────────────────────────────────────────────────────────────┘
```

## 核心概念

### Agent Card

Agent 的元数据描述，包含 Agent 的能力和配置信息。

```cpp
struct AgentCard {
    std::string name;           // Agent 名称
    std::string description;    // Agent 描述
    std::string url;            // Agent URL
    std::string version;        // 版本号
    std::vector<std::string> skills;  // 技能列表
    
    // 可选字段
    std::string provider;       // 提供者
    std::string documentation_url;  // 文档 URL
    AuthenticationInfo authentication;  // 认证信息
};
```

### Agent Message

Agent 之间传递的消息。

```cpp
struct AgentMessage {
    std::string role;           // "user" 或 "agent"
    std::vector<MessagePart> parts;  // 消息部分
    std::string context_id;     // 上下文 ID
    std::string message_id;     // 消息 ID
    std::string timestamp;      // 时间戳
};

struct MessagePart {
    std::string type;           // "text", "image", "file" 等
    std::string content;        // 内容
    std::string mime_type;      // MIME 类型
};
```

### Agent Task

任务表示一次完整的交互。

```cpp
struct AgentTask {
    std::string id;             // 任务 ID
    std::string context_id;     // 上下文 ID
    TaskStatus status;          // 任务状态
    std::vector<AgentMessage> messages;  // 消息历史
    std::vector<Artifact> artifacts;     // 产出物
    std::string created_at;     // 创建时间
    std::string updated_at;     // 更新时间
};
```

### Task Status

任务状态枚举。

```cpp
enum class TaskState {
    SUBMITTED,   // 已提交
    RUNNING,     // 运行中
    COMPLETED,   // 已完成
    FAILED,      // 失败
    CANCELED     // 已取消
};

struct TaskStatus {
    TaskState state;
    std::string message;        // 状态消息
    int progress;               // 进度 (0-100)
};
```

## 状态机

```
                    ┌──────────────┐
                    │  SUBMITTED   │
                    └──────┬───────┘
                           │
                           ▼
                    ┌──────────────┐
                    │   RUNNING    │
                    └──────┬───────┘
                           │
           ┌───────────────┼───────────────┐
           ▼               ▼               ▼
    ┌──────────────┐ ┌──────────────┐ ┌──────────────┐
    │  COMPLETED   │ │    FAILED    │ │   CANCELED   │
    └──────────────┘ └──────────────┘ └──────────────┘
```

### 状态转换规则

| 当前状态 | 允许转换到 |
|----------|------------|
| SUBMITTED | RUNNING, CANCELED |
| RUNNING | COMPLETED, FAILED, CANCELED |
| COMPLETED | (终态) |
| FAILED | (终态) |
| CANCELED | (终态) |

## API 方法

### message/send

发送消息给 Agent。

**请求:**
```json
{
    "jsonrpc": "2.0",
    "method": "message/send",
    "params": {
        "message": {
            "role": "user",
            "parts": [
                {
                    "type": "text",
                    "content": "计算 123 + 456"
                }
            ]
        },
        "context_id": "ctx-001"
    },
    "id": "req-001"
}
```

**响应:**
```json
{
    "jsonrpc": "2.0",
    "result": {
        "task": {
            "id": "task-001",
            "context_id": "ctx-001",
            "status": {
                "state": "completed",
                "message": "计算完成"
            },
            "messages": [
                {
                    "role": "agent",
                    "parts": [
                        {
                            "type": "text",
                            "content": "123 + 456 = 579"
                        }
                    ]
                }
            ]
        }
    },
    "id": "req-001"
}
```

### message/stream

流式发送消息。

**请求:** 同 message/send

**响应:** Server-Sent Events (SSE)
```
event: thinking
data: {"content": "正在分析问题..."}

event: content
data: {"content": "123 + 456 = "}

event: content
data: {"content": "579"}

event: done
data: {"task_id": "task-001"}
```

### task/get

获取任务状态。

**请求:**
```json
{
    "jsonrpc": "2.0",
    "method": "task/get",
    "params": {
        "task_id": "task-001"
    },
    "id": "req-002"
}
```

**响应:**
```json
{
    "jsonrpc": "2.0",
    "result": {
        "task": {
            "id": "task-001",
            "status": {
                "state": "completed"
            }
        }
    },
    "id": "req-002"
}
```

### task/cancel

取消任务。

**请求:**
```json
{
    "jsonrpc": "2.0",
    "method": "task/cancel",
    "params": {
        "task_id": "task-001"
    },
    "id": "req-003"
}
```

## 适配层

### A2AAdapter

将 gRPC 请求转换为 A2A 协议。

```cpp
#include "agent_rpc/a2a_adapter/a2a_adapter.h"

A2AConfig config;
config.orchestrator_url = "http://localhost:5000";
config.request_timeout_seconds = 30;

A2AAdapter adapter(config);
adapter.initialize();

// 同步查询
AIQueryRequest request;
request.set_question("计算 123 + 456");

AIQueryResponse response = adapter.processQuery(request);

// 流式查询
adapter.processQueryStreaming(request, [](const AIStreamEvent& event) {
    std::cout << event.content() << std::flush;
});
```

### RequestAdapter

请求转换器。

```cpp
#include "agent_rpc/a2a_adapter/request_adapter.h"

RequestAdapter adapter;

// 转换 RPC 请求为 A2A 消息
AIQueryRequest rpc_request;
rpc_request.set_question("Hello");
rpc_request.set_context_id("ctx-001");

a2a::MessageSendParams a2a_params = adapter.convertToA2A(rpc_request);
```

### ResponseAdapter

响应转换器。

```cpp
#include "agent_rpc/a2a_adapter/response_adapter.h"

ResponseAdapter adapter;

// 转换 A2A 响应为 RPC 响应
a2a::AgentTask a2a_task;
// ... 填充 a2a_task

AIQueryResponse rpc_response = adapter.convertFromA2A(a2a_task);
```

### ErrorMapper

错误码映射。

```cpp
#include "agent_rpc/a2a_adapter/error_mapper.h"

ErrorMapper mapper;

// A2A 错误码 -> gRPC 状态码
a2a::ErrorCode a2a_error = a2a::ErrorCode::TASK_NOT_FOUND;
grpc::StatusCode grpc_status = mapper.mapToGrpc(a2a_error);
// 结果: grpc::StatusCode::NOT_FOUND
```

| A2A 错误码 | gRPC 状态码 |
|------------|-------------|
| INVALID_REQUEST | INVALID_ARGUMENT |
| METHOD_NOT_FOUND | UNIMPLEMENTED |
| TASK_NOT_FOUND | NOT_FOUND |
| AGENT_UNAVAILABLE | UNAVAILABLE |
| TIMEOUT | DEADLINE_EXCEEDED |
| INTERNAL_ERROR | INTERNAL |

## 任务管理

### TaskManagerWrapper

任务管理器封装。

```cpp
#include "agent_rpc/a2a_adapter/task_manager_wrapper.h"

TaskManagerWrapper manager;

// 创建任务
std::string task_id = manager.createTask("ctx-001");

// 更新状态
manager.updateStatus(task_id, TaskState::RUNNING, "处理中");

// 添加消息
AgentMessage message;
message.role = "agent";
message.parts.push_back({"text", "Hello"});
manager.addMessage(task_id, message);

// 获取任务
auto task = manager.getTask(task_id);

// 查询历史
auto history = manager.getHistory("ctx-001");
```

### 存储后端

支持多种存储后端：

| 后端 | 描述 | 适用场景 |
|------|------|----------|
| MemoryTaskStore | 内存存储 | 开发测试 |
| RedisTaskStore | Redis 存储 | 生产环境 |

```cpp
// 使用内存存储
A2AConfig config;
config.enable_redis_store = false;

// 使用 Redis 存储
config.enable_redis_store = true;
config.redis_url = "localhost:6379";
```

## Agent 注册中心

### RegistryClient

注册中心客户端。

```cpp
#include "agent_rpc/orchestrator/registry_client.h"

RegistryClient client("http://localhost:8500");
client.connect();

// 注册 Agent
AgentCard card;
card.name = "math-agent";
card.skills = {"math", "calculation"};
client.registerAgent(card);

// 发现 Agent
auto agents = client.discoverAgents("math");

// 心跳
client.startHeartbeatLoop(30); // 30秒间隔
```

### 心跳机制

```
┌─────────────┐                    ┌─────────────┐
│    Agent    │                    │  Registry   │
└──────┬──────┘                    └──────┬──────┘
       │                                  │
       │  ──── register ────────────────► │
       │                                  │
       │  ◄─── registered ────────────── │
       │                                  │
       │  ──── heartbeat ───────────────► │ (每30秒)
       │                                  │
       │  ◄─── ack ─────────────────────  │
       │                                  │
       │  ──── heartbeat ───────────────► │
       │                                  │
       │  ◄─── ack ─────────────────────  │
       │                                  │
       │         ...                      │
       │                                  │
       │  (超时未收到心跳)                  │
       │                                  │
       │                    Agent 标记为不健康
       │                                  │
```

## Agent 路由

### AgentRouter

Agent 路由器。

```cpp
#include "agent_rpc/orchestrator/agent_router.h"

AgentRouter router;
router.initialize(RoutingStrategy::SKILL_MATCH);

// 更新 Agent 列表
std::vector<AgentInfo> agents = registry.discoverAgents();
router.updateAgentList(agents);

// 选择 Agent
auto selected = router.selectAgent("计算 123 + 456", {"math"});
if (selected.has_value()) {
    std::cout << "选择: " << selected->name << std::endl;
}

// 健康状态管理
router.markAgentUnhealthy("agent-001");
router.markAgentHealthy("agent-001");
```

### 路由策略

| 策略 | 描述 |
|------|------|
| ROUND_ROBIN | 轮询选择 |
| RANDOM | 随机选择 |
| SKILL_MATCH | 技能匹配 |
| LEAST_LOAD | 最少负载 |

## 配置

### A2AConfig

```cpp
struct A2AConfig {
    // Orchestrator 配置
    std::string orchestrator_url = "http://localhost:5000";
    int orchestrator_port = 5000;
    
    // Registry 配置
    std::string registry_url = "http://localhost:8500";
    
    // 存储配置
    bool enable_redis_store = false;
    std::string redis_url = "localhost:6379";
    
    // 超时配置
    int request_timeout_seconds = 30;
    
    // 历史配置
    int history_length = 10;
};
```

## 错误处理

```cpp
try {
    auto response = adapter.processQuery(request);
} catch (const a2a::A2AException& e) {
    switch (e.error_code()) {
        case a2a::ErrorCode::AGENT_UNAVAILABLE:
            // 重试或降级
            break;
        case a2a::ErrorCode::TIMEOUT:
            // 超时处理
            break;
        default:
            // 其他错误
            break;
    }
}
```

## 监控指标

| 指标 | 描述 |
|------|------|
| a2a_requests_total | A2A 请求总数 |
| a2a_request_latency_ms | 请求延迟 |
| a2a_errors_total | 错误总数 |
| a2a_tasks_active | 活跃任务数 |
| a2a_agents_healthy | 健康 Agent 数 |

## 最佳实践

1. **设置合理的超时**: 根据任务复杂度设置超时时间
2. **启用心跳**: 保持 Agent 健康状态更新
3. **使用 Redis 存储**: 生产环境使用持久化存储
4. **监控错误率**: 及时发现问题
5. **实现降级逻辑**: Agent 不可用时有备选方案
