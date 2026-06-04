# 🚀 FWI Agent 平台 - 5 分钟快速上手

## 第一次使用（只需做一次）

### 步骤 1: 克隆项目

```bash
git clone git@github.com:kicbed/CPP_FWI-agent.git
cd CPP_FWI-agent
```

### 步骤 2: 一键设置

```bash
./setup.sh
```

### 步骤 3: 填入 API Key

```bash
nano .env
```

修改这两行：
```bash
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-你的密钥
```

### 步骤 4: 一键启动

```bash
./deploy/scripts/start_http.sh
```

启动后自动进入交互式客户端：

```
  ╔═══════════════════════════════════════════════════════════════════╗
  ║            🔬  FWI 全波形反演科研助手平台  🔬                  ║
  ╚═══════════════════════════════════════════════════════════════════╝

  连接: http://localhost:5000

  ┌─────────────────────────────────────────────────────────────────┐
  │  📚 对话历史                                                    │
  └─────────────────────────────────────────────────────────────────┘

  ▶ 什么是FWI全波形反演？  (4 条)
    └─ FWI是全波形反演的缩写...
      计算 123*456  (2 条)
      如何写论文摘要  (2 条)

  ─────────────────────────────────────────────────────────────────
    ↑/↓ 选择   Enter 进入   n 新建   /help 帮助   /quit 退出

  > 
```

### 功能说明

| 模式 | 按键 | 功能 |
|------|------|------|
| **列表** | ↑/↓ | 选择对话 |
| **列表** | Enter | 进入选中的对话 |
| **列表** | n | 新建对话 |
| **列表** | 1-9 | 直接选择对话 |
| **对话** | 直接输入 | 发送消息 |
| **对话** | /list | 返回列表 |
| **对话** | /card | 查看 Agent Card |
| **对话** | /help | 帮助 |
| **对话** | /quit | 退出 |

### 历史记录

退出后再进入，所有对话历史都会保留（存储在 Redis 中）。

---

## 日常使用

```bash
# 启动
./deploy/scripts/start_http.sh

# 停止
./deploy/scripts/stop.sh
```

---

## 切换模型

编辑 `.env` 文件：

```bash
# LLM 配置
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-你的密钥

# Embedding 配置
LOCAL_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
```

---

## gRPC 模式

```bash
./deploy/scripts/start_grpc.sh
```

---

## 常见问题

### Q: Embedding 服务启动失败

```bash
# 查看日志
tail deploy/logs/embedding.log

# 手动启动
python3 deploy/scripts/embedding_server.py &
```

### Q: 如何查看日志

```bash
./examples/ai_orchestrator/view_logs.sh chat    # 对话记录
./examples/ai_orchestrator/view_logs.sh trace   # 调用链路
./examples/ai_orchestrator/view_logs.sh status  # 服务状态
```
