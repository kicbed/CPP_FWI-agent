# 🚀 FWI Agent 平台 - 使用手册

## 一、启动方式

本系统支持 **三种客户端**，后端服务相同，区别在于前端交互方式：

| 客户端 | 说明 | 适合场景 |
|--------|------|----------|
| **Web UI** | 浏览器网页界面 | 日常使用、演示展示 |
| **ai_client** | 终端 HTTP 客户端 | 服务器环境、快速测试 |
| **grpc_ai_client** | 终端 gRPC 客户端 | 测试 gRPC 通信链路 |

---

### 方式 1：Web UI（推荐）

```
浏览器 ──HTTP──> Web UI (:8080) ──JSON-RPC──> Orchestrator (:5000)
浏览器 ──HTTP──> Web UI (:8080) ──HTTP──> Bridge (:50052) ──A2A──> Orchestrator (:5000)
```

**步骤 1：启动后端（终端 1）**

```bash
cd /root/projects/project/agent-communication-main-v2

# HTTP 模式（推荐，简单直接）
./deploy/scripts/start_http.sh

# 或 gRPC 模式（测试 gRPC 链路）
./deploy/scripts/start_grpc.sh
```

**步骤 2：启动 Web UI（终端 2）**

```bash
./deploy/scripts/start_web.sh
# 或
python3 web/serve.py
```

浏览器自动打开 `http://localhost:8080`。

**Web UI 功能一览：**

| 功能 | 说明 |
|------|------|
| 模式切换 | 侧边栏 HTTP / gRPC 一键切换 |
| 对话聊天 | 输入消息，Enter 发送，Shift+Enter 换行 |
| 流式响应 | AI 回复逐字显示（HTTP 模式） |
| 快捷入口 | 首页 4 个预设问题，点击即发送 |
| 会话管理 | 左侧栏显示历史对话，点击切换 |
| 新建对话 | 侧边栏「新对话」按钮 |
| Markdown | AI 回复自动渲染代码块、表格、列表 |
| 代码复制 | 代码块右上角一键复制 |
| 系统状态 | 侧边栏底部实时显示服务端口状态 |
| 响应式 | 手机端自适应布局 |

**Web UI 界面说明：**

```
┌──────────────┬──────────────────────────────────────────┐
│              │  Header: 标题 + 模式标签 + 清空按钮       │
│   Sidebar    ├──────────────────────────────────────────┤
│              │                                          │
│  ● HTTP      │   Chat Area                              │
│  ○ gRPC      │                                          │
│              │   🤖 AI 回复气泡（Markdown 渲染）          │
│  [新对话]    │                                          │
│              │         👤 用户消息气泡                    │
│  历史对话    │                                          │
│  ├ 对话 1    │   ⏳ 打字指示器（3-dot 动画）              │
│  └ 对话 2    │                                          │
│              ├──────────────────────────────────────────┤
│  系统状态    │  [输入消息...]              [发送] [停止]  │
│  🟢 运行中   │                                          │
└──────────────┴──────────────────────────────────────────┘
```

> **gRPC 模式说明**
> 切换到 gRPC 模式后，Web UI 通过 HTTP 桥接端口 (50052) 与 gRPC Server 通信。
> `rpc_server` 启动时会自动开启此桥接服务，无需额外配置。

---

### 方式 2：终端 HTTP 客户端

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

---

### 方式 3：终端 gRPC 客户端

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
4. 启动 gRPC Server（`:50051` + HTTP 桥接 `:50052`）
5. 启动 gRPC 客户端（`grpc_ai_client`，前台交互）

---

### 三种方式的区别

| | Web UI | ai_client | grpc_ai_client |
|---|---|---|---|
| 脚本 | `start_http.sh` + `serve.py` | `start_http.sh` | `start_grpc.sh` |
| 交互方式 | 浏览器 | 终端 | 终端 |
| 通信协议 | HTTP + JSON-RPC | HTTP + JSON-RPC | gRPC + Protobuf |
| 连接地址 | `http://localhost:5000` | `http://localhost:5000` | `localhost:50051` |
| 流式响应 | SSE 逐字显示 | SSE 逐字显示 | gRPC Streaming |
| 会话管理 | 浏览器 localStorage | Redis | Redis |
| 适合场景 | 日常使用、演示 | 服务器、快速测试 | 测试 gRPC 链路 |

---

### 手动分步启动

```bash
# 终端 1：启动 Agent 系统
./examples/ai_orchestrator/start_system.sh

# 终端 2（仅 gRPC 模式需要）：启动 gRPC Server
./build/server/rpc_server

# 终端 3：启动客户端（三选一）
python3 web/serve.py                                        # Web UI
./build/examples/ai_orchestrator/ai_client http://localhost:5000  # 终端 HTTP
./build/client/grpc_ai_client localhost:50051               # 终端 gRPC
```

> **⚠️ 不要同时运行 `start_system.sh` + `start_http.sh` 或 `start_grpc.sh`！**
> 一键脚本内部已调用 `start_system.sh`，重复启动会导致端口冲突。

## 二、停止

```bash
./examples/ai_orchestrator/stop_system.sh
```

如果停止脚本无效，手动清理：

```bash
for p in 8500 5000 5001 5002 5003 5004 50051 50052 6000; do fuser -k $p/tcp 2>/dev/null; done
```

退出终端客户端输入 `/quit`，服务在后台继续运行。
Web UI 直接关闭浏览器即可，按 Ctrl+C 停止 Web 服务器。

## 三、终端客户端使用

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
| 5000 | Orchestrator | HTTP + JSON-RPC | 调度中心，所有模式的最终入口 |
| 5001 | Math Agent | HTTP + JSON-RPC | 数学计算 |
| 5002 | FWI Theory Agent | HTTP + JSON-RPC | FWI 理论 |
| 5003 | FWI Teaching Agent | HTTP + JSON-RPC | FWI 教学 |
| 5004 | General Research Agent | HTTP + JSON-RPC | 通用研究 |
| 8500 | Registry Server | HTTP | 服务注册中心 |
| 50051 | gRPC Server | gRPC (Protobuf) | gRPC 客户端连接此端口 |
| 50052 | HTTP Bridge | HTTP | Web 前端 → gRPC Server 的桥接 |
| 6000 | Embedding Server | HTTP | 本地向量化服务 |
| 6379 | Redis | Redis | 会话/任务持久化 |
| 8080 | Web UI | HTTP | 前端界面（需手动启动） |

## 七、常见问题

### "连接被拒绝" / "服务无响应"
Agent 系统没有启动。运行 `./examples/ai_orchestrator/start_system.sh` 或使用一键启动脚本。

### gRPC 模式 "gRPC 错误: Connection refused"
gRPC Server 没有启动。确认 `./deploy/scripts/start_grpc.sh` 完整运行，或手动启动 `./build/server/rpc_server`。

### Web UI gRPC 模式无响应
HTTP 桥接端口 (50052) 没有启动。确认 `rpc_server` 正在运行，查看日志：
```bash
tail deploy/logs/grpc_server.log
```

### 启动后 Orchestrator 崩溃
看门狗会自动重启（2 秒内）。查看日志：`tail examples/ai_orchestrator/logs/watchdog.log`

### 端口被占用
启动脚本会自动清理旧进程。如果仍然冲突，手动释放：
```bash
for p in 8500 5000 5001 5002 5003 5004 50051 50052 6000; do fuser -k $p/tcp 2>/dev/null; done
```
