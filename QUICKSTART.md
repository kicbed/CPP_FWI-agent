# 🚀 FWI Agent 平台 - 快速上手

## 一键启动

```bash
# HTTP 模式（推荐，直接启动客户端）
./deploy/scripts/start_http.sh

# gRPC 模式（同 HTTP 模式，客户端底层也是 HTTP POST）
./deploy/scripts/start_grpc.sh
```

> **注意：两种模式都是同一个客户端，底层用 HTTP POST 连接 Orchestrator (port 5000)。**
> 
> **不要同时在两个终端分别运行 `start_system.sh` 和 `start_grpc.sh`** —— `start_grpc.sh` 已经内部调用了 `start_system.sh`，重复启动会导致端口冲突。

## 手动分步启动

如果需要单独控制：

```bash
# 终端 1：启动 Agent 系统（Orchestrator + 4 个 Agent + 看门狗）
./examples/ai_orchestrator/start_system.sh

# 终端 2：启动客户端（直连 Orchestrator :5000）
./build/client/grpc_ai_client http://localhost:5000
# 或
./build/examples/ai_orchestrator/ai_client http://localhost:5000
```

## 停止系统

```bash
# 停止所有服务（Agent 系统 + Embedding 服务 + 看门狗）
./examples/ai_orchestrator/stop_system.sh
```

## 使用方法

启动后进入交互式客户端：

### 列表模式
```
  ┌─────────────────────────────────────────────────────────────────┐
  │  📚 对话历史                                                    │
  └─────────────────────────────────────────────────────────────────┘

  [1] 什么是FWI全波形反演？  (4 条)
  [2] 计算 123*456  (2 条)
  [3] 如何写论文摘要  (2 条)
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
```

| 输入 | 功能 |
|------|------|
| 直接输入 | 发送消息 |
| `/list` | 返回列表 |
| `/quit` | 退出 |

## 配置

编辑 `.env` 文件：

```bash
# LLM 提供商（选一个）
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx

# Embedding 模型（本地，免费）
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_URL=http://localhost:6000
LOCAL_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

## 查看日志

```bash
./examples/ai_orchestrator/view_logs.sh chat    # 对话记录
./examples/ai_orchestrator/view_logs.sh trace   # 调用链路
./examples/ai_orchestrator/view_logs.sh status  # 服务状态
```

## 常见问题

### "服务无响应 / 连接被拒绝"
Agent 系统没有启动。运行 `./examples/ai_orchestrator/start_system.sh` 或使用一键启动脚本。

### 启动后 Orchestrator 崩溃
看门狗会自动重启（2 秒内）。查看日志：`tail examples/ai_orchestrator/logs/watchdog.log`

### 端口被占用
启动脚本会自动清理旧进程。如果仍然冲突，手动释放：
```bash
for p in 8500 5000 5001 5002 5003 5004 6000; do fuser -k $p/tcp 2>/dev/null; done
```

### Ctrl+C 退出后重新启动失败
启动脚本已添加 `nohup` 和端口清理保护，直接重新运行启动脚本即可。
