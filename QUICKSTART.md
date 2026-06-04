# 🚀 FWI Agent 平台 - 快速上手

## 一键启动

```bash
# HTTP 模式
./deploy/scripts/start_http.sh

# gRPC 模式
./deploy/scripts/start_grpc.sh
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
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
LOCAL_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

## 查看日志

```bash
./examples/ai_orchestrator/view_logs.sh chat    # 对话记录
./examples/ai_orchestrator/view_logs.sh trace   # 调用链路
./examples/ai_orchestrator/view_logs.sh status  # 服务状态
```
