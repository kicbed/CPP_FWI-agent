# 🚀 FWI Agent 平台 - 使用手册

## 一、启动

### 方式 1：HTTP 模式（推荐）

一个终端运行：

```bash
cd /root/projects/project/agent-communication-main-v2
./deploy/scripts/start_http.sh
```

启动顺序：Embedding → Agent 系统 → 客户端。完成后直接进入对话界面。

### 方式 2：gRPC 模式

一个终端运行：

```bash
cd /root/projects/project/agent-communication-main-v2
./deploy/scripts/start_grpc.sh
```

> **两种模式的客户端完全相同**，底层都是 HTTP POST。区别只是 UI 标题显示"HTTP 模式"或"gRPC 模式"。

### 方式 3：手动分步启动

需要分别控制服务和客户端时：

```bash
# 终端 1：启动 Agent 系统
./examples/ai_orchestrator/start_system.sh

# 终端 2：启动客户端（二选一）
./build/examples/ai_orchestrator/ai_client http://localhost:5000      # HTTP 版
./build/client/grpc_ai_client http://localhost:5000                    # gRPC 版
```

> **⚠️ 不要同时开两个终端分别运行 `start_system.sh` + `start_grpc.sh`！**
> `start_grpc.sh` 内部已经调用了 `start_system.sh`，重复启动会导致端口冲突。

## 二、停止

```bash
# 停止所有服务（Agent 系统 + Embedding + 看门狗）
./examples/ai_orchestrator/stop_system.sh
```

如果停止脚本无效，手动清理：

```bash
# 按端口杀进程
for p in 8500 5000 5001 5002 5003 5004 50051 6000; do
    fuser -k $p/tcp 2>/dev/null
done
```

### 退出客户端

在客户端界面中输入 `/quit` 退出。服务会在后台继续运行。

## 三、客户端使用

### 列表模式
```
  ┌─────────────────────────────────────────────────────────────────┐
  │  📚 对话历史                                                    │
  └─────────────────────────────────────────────────────────────────┘

  [1] 什么是FWI全波形反演？  (4 条)
  [2] 计算 123*456  (2 条)
  [3] 如何写论文摘要  (2 条)

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
# LLM 提供商（选一个）
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

## 六、服务端口说明

| 端口 | 服务 | 说明 |
|------|------|------|
| 5000 | Orchestrator | 调度中心，客户端连接这个端口 |
| 5001 | Math Agent | 数学计算 |
| 5002 | FWI Theory Agent | FWI 理论 |
| 5003 | FWI Teaching Agent | FWI 教学 |
| 5004 | General Research Agent | 通用研究 |
| 8500 | Registry | 服务注册中心 |
| 6000 | Embedding | 本地向量化服务 |

## 七、常见问题

### "服务无响应 / 连接被拒绝"
Agent 系统没有启动。运行 `./examples/ai_orchestrator/start_system.sh` 或使用一键启动脚本。

### 启动后 Orchestrator 崩溃
看门狗会自动重启（2 秒内）。查看日志：`tail examples/ai_orchestrator/logs/watchdog.log`

### 端口被占用
启动脚本会自动清理旧进程。如果仍然冲突，手动释放：
```bash
for p in 8500 5000 5001 5002 5003 5004 50051 6000; do fuser -k $p/tcp 2>/dev/null; done
```

### Ctrl+C 退出后重新启动失败
启动脚本已添加 `nohup` 和端口清理保护，直接重新运行启动脚本即可。
