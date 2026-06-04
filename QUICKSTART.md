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

脚本会自动：
- ✅ 创建配置文件 `.env`
- ✅ 检查 Redis
- ✅ 编译项目

### 步骤 3: 填入 API Key

```bash
nano .env
```

找到这一行，填入你的 DeepSeek API Key：
```
DEEPSEEK_API_KEY=sk-你的密钥
```

**没有 API Key？** 去这里申请（免费额度）：
- DeepSeek: https://platform.deepseek.com/
- 通义千问: https://dashscope.aliyun.com/

### 步骤 4: 启动本地 Embedding（可选，推荐）

```bash
# 安装 Python 依赖（只需一次）
pip3 install sentence-transformers flask

# 启动 Embedding 服务（后台运行，默认使用 Qwen3-Embedding-0.6B）
nohup python3 deploy/scripts/embedding_server.py > deploy/logs/embedding.log 2>&1 &

# 验证服务启动
curl http://localhost:6000/health
```

**Embedding 服务端口**: 6000

**默认模型**: Qwen/Qwen3-Embedding-0.6B（可在 .env 中修改 `LOCAL_EMBEDDING_MODEL`）

**查看日志**:
```bash
tail -f deploy/logs/embedding.log
```

**停止服务**:
```bash
pkill -f embedding_server
```

### 步骤 5: 启动系统

```bash
source .env
./examples/ai_orchestrator/start_system.sh
```

### 步骤 6: 使用系统

**方式 1: 交互式客户端（推荐）**

```bash
./build/examples/ai_orchestrator/ai_client http://localhost:5000
```

进入后可以直接输入文字对话：
```
[default] > 什么是 FWI
AI: FWI（全波形反演）是...

[default] > 计算 123 * 456
AI: 123 × 456 = 56088

[default] > /quit
```

**方式 2: curl 测试**

```bash
curl -X POST http://localhost:5000/ -H 'Content-Type: application/json' -d '{
  "jsonrpc":"2.0","id":"test","method":"message/send",
  "params":{"message":{"role":"user","contextId":"ctx",
  "parts":[{"kind":"text","text":"什么是 FWI"}]}}
}'
```

**方式 3: gRPC 客户端**

```bash
./build/client/rpc_client
```

---

## 配置说明

### 路由模式

编辑 `.env` 文件：

```bash
# 固定路由（简单，if-else 逻辑）
ROUTING_MODE=fixed

# Agent-RAG 动态路由（智能，基于 Embedding 语义匹配）（推荐）
ROUTING_MODE=agent-rag
```

### 工具调用模式

```bash
# 规则选择（简单，关键词匹配）
TOOL_CALLING_MODE=rule

# LLM 选择（智能，让 LLM 选择工具）（推荐）
TOOL_CALLING_MODE=llm
```

### MCP 工具开关

```bash
# 启用 MCP 工具（推荐）
ENABLE_MCP=true

# 禁用 MCP 工具
ENABLE_MCP=false
```

### Embedding 配置

```bash
# 本地 Embedding（推荐，免费，使用 GPU）
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_URL=http://localhost:6000

# DashScope API（阿里云，需要付费）
EMBEDDING_PROVIDER=dashscope
DASHSCOPE_API_KEY=sk-你的密钥
```

### LLM 配置

```bash
# DeepSeek（推荐，便宜）
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-你的密钥

# 通义千问（阿里）
LLM_PROVIDER=qwen
QWEN_API_KEY=sk-你的密钥

# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-你的密钥

# 本地模型（Ollama，完全免费）
LLM_PROVIDER=local
LOCAL_LLM_URL=http://localhost:11434
```

### 完整配置示例

```bash
# .env 文件示例

# LLM 配置
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-你的deepseek密钥

# Embedding 配置
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_URL=http://localhost:6000

# 功能开关
ENABLE_MCP=true
ROUTING_MODE=agent-rag
TOOL_CALLING_MODE=llm

# Redis
REDIS_HOST=127.0.0.1
REDIS_PORT=6379
```

---

## 日常使用

### 启动系统

```bash
cd CPP_FWI-agent
source .env
./examples/ai_orchestrator/start_system.sh
```

### 停止系统

```bash
./examples/ai_orchestrator/stop_system.sh
```

### 查看日志

```bash
tail -f examples/ai_orchestrator/logs/orchestrator.log
```

---

## 切换模型（超简单）

### 切换 LLM

编辑 `.env` 文件，修改这两行：

```bash
# 用 DeepSeek（便宜）
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx

# 用通义千问
LLM_PROVIDER=qwen
QWEN_API_KEY=sk-xxx

# 用 OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk-xxx

# 用本地模型（完全免费，需要 Ollama）
LLM_PROVIDER=local
```

### 切换 Embedding

```bash
# 用本地模型（推荐，免费）
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_URL=http://localhost:6000

# 用 DashScope API
EMBEDDING_PROVIDER=dashscope
DASHSCOPE_API_KEY=sk-xxx
```

**改完后重启系统即可！**

---

## 推荐配置

### 最便宜方案（推荐）

```bash
# .env 配置
LLM_PROVIDER=deepseek
DEEPSEEK_API_KEY=sk-xxx
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_URL=http://localhost:6000
```

**费用**: DeepSeek 极便宜 + 本地 Embedding 免费

### 最简单方案

```bash
# .env 配置
LLM_PROVIDER=qwen
QWEN_API_KEY=sk-xxx
EMBEDDING_PROVIDER=dashscope
DASHSCOPE_API_KEY=sk-xxx
```

**费用**: 全用阿里云，一个账号搞定

### 完全免费方案

```bash
# .env 配置
LLM_PROVIDER=local
EMBEDDING_PROVIDER=local
LOCAL_EMBEDDING_URL=http://localhost:6000
```

**费用**: 0 元（需要本地跑 Ollama）

---

## 推荐 Embedding 模型（2025 最新）

| 模型 | 大小 | 中文效果 | 速度 | 推荐 |
|------|------|----------|------|------|
| **BAAI/bge-small-zh-v1.5** | 93M | 好 | 极快 | ⭐⭐⭐⭐⭐ |
| Alibaba-NLP/gte-multilingual-base | 305M | 很好 | 中等 | ⭐⭐⭐⭐ |
| Qwen/Qwen3-Embedding-0.6B | 600M | 很好 | 较慢 | ⭐⭐⭐⭐ |

**推荐**: `BAAI/bge-small-zh-v1.5` — 又快又好，93M 参数

切换 Embedding 模型：
```bash
# 编辑 deploy/scripts/embedding_server.py
# 修改 --model 参数
python3 deploy/scripts/embedding_server.py --model BAAI/bge-small-zh-v1.5 --port 6000 &
```

---

## 常见问题

### Q: .env 文件会泄露到 GitHub 吗？

**不会！** `.env` 已经在 `.gitignore` 中，不会被上传。

### Q: 怎么更新代码？

```bash
git pull
```

### Q: 怎么保存我的修改？

```bash
git add .
git commit -m "描述你改了什么"
git push
```

### Q: Embedding 服务挂了怎么办？

```bash
# 重启
pkill -f embedding_server
python3 deploy/scripts/embedding_server.py &
```

### Q: 怎么查看系统状态？

```bash
./deploy/scripts/status.sh
```

---

## 文件说明

| 文件 | 用途 |
|------|------|
| `.env` | **你的配置文件**（API Key 在这里） |
| `.env.example` | 配置文件模板 |
| `setup.sh` | 一键设置脚本 |
| `GIT_QUICKSTART.md` | Git 使用教程 |
| `docs/learning/` | 详细技术文档 |
