# 🚀 FWI Agent 平台 - 使用手册

## 一、启动

### HTTP 模式

```
ai_client ──HTTP POST (JSON-RPC)──> Orchestrator (:5000) ──> Agents
```

```bash
cd /root/projects/project/agent-communication-main-v2
./deploy/scripts/start_http.sh
```

启动顺序（4 步）：
1. 加载 `.env` 配置
2. 启动 Embedding 服务 (`:6000`)
3. 启动 Agent 系统（Orchestrator `:5000` + 4 个 Agent）
4. 启动 HTTP 客户端（`ai_client`，前台交互）

### gRPC 模式

```
grpc_ai_client ──gRPC (Protobuf)──> gRPC Server (:50051) ──A2A HTTP──> Orchestrator (:5000) ──> Agents
```

```bash
cd /root/projects/project/agent-communication-main-v2
./deploy/scripts/start_grpc.sh
```

启动顺序（5 步）：
1. 加载 `.env` 配置
2. 启动 Embedding 服务 (`:6000`)
3. 启动 Agent 系统（Orchestrator `:5000` + 4 个 Agent）
4. 启动 gRPC Server（`:50051`，代理 Orchestrator）
5. 启动 gRPC 客户端（`grpc_ai_client`，前台交互）

### 两种模式的区别

| | HTTP 模式 | gRPC 模式 |
|---|---|---|
| 脚本 | `start_http.sh` | `start_grpc.sh` |
| 客户端 | `ai_client` | `grpc_ai_client` |
| 通信协议 | HTTP POST + JSON-RPC 2.0 | gRPC + Protobuf |
| 客户端连接 | `http://localhost:5000` | `localhost:50051` |
| 是否经过 gRPC Server | 否，直连 Orchestrator | 是，gRPC Server 代理转发 |
| 流式支持 | SSE (Server-Sent Events) | gRPC Server Streaming |

### 手动分步启动

```bash
# 终端 1：启动 Agent 系统
./examples/ai_orchestrator/start_system.sh

# 终端 2（仅 gRPC 模式需要）：启动 gRPC Server
./build/server/rpc_server

# 终端 3：启动客户端（二选一）
./build/examples/ai_orchestrator/ai_client http://localhost:5000      # HTTP 模式
./build/client/grpc_ai_client localhost:50051                          # gRPC 模式
```

> **⚠️ 不要同时运行 `start_system.sh` + `start_http.sh` 或 `start_grpc.sh`！**
> 一键脚本内部已调用 `start_system.sh`，重复启动会导致端口冲突。

## 二、停止

```bash
./examples/ai_orchestrator/stop_system.sh
```

如果停止脚本无效，手动清理：

```bash
for p in 8500 5000 5001 5002 5003 5004 50051 6000; do fuser -k $p/tcp 2>/dev/null; done
```

退出客户端输入 `/quit`，服务在后台继续运行。

## 三、客户端使用

### 列表模式
```
  ┌─────────────────────────────────────────────────────────────────┐
  │  📚 对话历史                                                    │
  └─────────────────────────────────────────────────────────────────┘

  [1] 什么是FWI全波形反演？  (4 条)
  [2] 计算 123*456  (2 条)

  输入数字 进入对话  n 新建  d 数字 删除  /help 帮助  /quit 退出
```

| 输入 | 功能 |
|------|------|
| `1-9` | 选择对话（显示历史+继续聊天） |
| `n` | 新建对话 |
| `d 3` | 删除第 3 个对话 |
| `/help` | 帮助 |
| `/quit` | 退出 |

### 对话模式
```
  [ctx-xxx] > 什么是FWI的伴随状态法？

  🤖 AI: 伴随状态法是 FWI 中计算梯度的核心方法...
```

| 输入 | 功能 |
|------|------|
| 直接输入 | 发送消息 |
| `/list` | 返回对话列表 |
| `/quit` | 退出 |

## 四、配置

编辑 `.env` 文件：

```bash
# LLM 提供商
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx

# Embedding 模型（本地，免费）
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_URL=http://localhost:6000
LOCAL_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

## 五、查看日志

```bash
./examples/ai_orchestrator/view_logs.sh chat    # 对话记录
./examples/ai_orchestrator/view_logs.sh trace   # 调用链路
./examples/ai_orchestrator/view_logs.sh status  # 服务状态
```

## 六、服务端口

| 端口 | 服务 | 协议 | 说明 |
|------|------|------|------|
| 5000 | Orchestrator | HTTP + JSON-RPC | 调度中心，HTTP 模式客户端直连此端口 |
| 5001 | Math Agent | HTTP + JSON-RPC | 数学计算 |
| 5002 | FWI Theory Agent | HTTP + JSON-RPC | FWI 理论 |
| 5003 | FWI Teaching Agent | HTTP + JSON-RPC | FWI 教学 |
| 5004 | General Research Agent | HTTP + JSON-RPC | 通用研究 |
| 8500 | Registry Server | HTTP | 服务注册中心 |
| 50051 | gRPC Server | gRPC (Protobuf) | gRPC 模式客户端连接此端口 |
| 6000 | Embedding Server | HTTP | 本地向量化服务 |
| 6379 | Redis | Redis | 会话/任务持久化 |

## 七、常见问题

### "连接被拒绝" / "服务无响应"
Agent 系统没有启动。运行 `./examples/ai_orchestrator/start_system.sh` 或使用一键启动脚本。

### gRPC 模式 "gRPC 错误: Connection refused"
gRPC Server 没有启动。确认 `./deploy/scripts/start_grpc.sh` 完整运行，或手动启动 `./build/server/rpc_server`。

### 启动后 Orchestrator 崩溃
看门狗会自动重启（2 秒内）。查看日志：`tail examples/ai_orchestrator/logs/watchdog.log`

### 端口被占用
启动脚本会自动清理旧进程。如果仍然冲突，手动释放：
```bash
for p in 8500 5000 5001 5002 5003 5004 50051 6000; do fuser -k $p/tcp 2>/dev/null; done
```
