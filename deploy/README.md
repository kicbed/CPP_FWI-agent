# FWI Agent 平台部署指南

## 一、环境要求

| 要求 | 版本 |
|------|------|
| 操作系统 | Linux (Ubuntu 20.04+) |
| CMake | 3.15+ |
| GCC | 9+ (C++17) |
| Redis | 6.0+ |
| gRPC | 1.51.1 |

## 二、快速部署

### 2.0 设置本地 Embedding 服务（可选，推荐）

使用本地 Embedding 模型可以节省 API 费用：

```bash
# 安装依赖并下载模型
./deploy/scripts/setup_embedding.sh

# 启动 Embedding 服务
python3 deploy/scripts/embedding_server.py --model Qwen/Qwen3-Embedding-0.6B --port 6000 &

# 验证服务
curl http://localhost:6000/health
```

**Embedding 模型选择**:
| 模型 | 大小 | 中文效果 | 推荐度 |
|------|------|----------|--------|
| **Qwen3-Embedding-0.6B** | 0.6B | 很好 | ⭐⭐⭐⭐⭐ |
| BGE-small-zh-v1.5 | 0.3B | 好 | ⭐⭐⭐⭐ |
| M3E-small | 0.3B | 好 | ⭐⭐⭐⭐ |

### 2.1 编译

```bash
cd /root/projects/project/agent-communication-main-v2

# 编译主项目
mkdir -p build && cd build
cmake .. && make -j$(nproc)
cd ..

# 编译 MCP Server
cd mcp_server_integrated
mkdir -p build && cd build
cmake .. && make -j$(nproc)
cd ../..
```

### 2.2 设置环境变量

```bash
# 必需
export QWEN_API_KEY=sk-your-qwen-api-key

# 可选（用于 RAG 功能）
export DASHSCOPE_API_KEY=sk-your-dashscope-api-key

# 可选（用于访问控制）
export AGENT_API_TOKEN=your-secret-token
```

### 2.3 启动系统

```bash
# 使用部署脚本
./deploy/scripts/start.sh

# 或使用原有脚本
./examples/ai_orchestrator/start_system.sh
```

### 2.4 验证

```bash
# 检查状态
./deploy/scripts/status.sh

# 测试查询
curl -X POST http://localhost:5000/ -H 'Content-Type: application/json' -d '{
  "jsonrpc":"2.0","id":"test","method":"message/send",
  "params":{"message":{"role":"user","contextId":"ctx",
  "parts":[{"kind":"text","text":"什么是 FWI"}]}}
}'
```

### 2.5 停止系统

```bash
./deploy/scripts/stop.sh
```

## 三、配置说明

### 3.1 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `QWEN_API_KEY` | 是 | 通义千问 API Key（用于对话） |
| `DASHSCOPE_API_KEY` | 否 | DashScope API Key（用于 Embedding 向量化） |
| `AGENT_API_TOKEN` | 否 | 访问令牌 |
| `ENABLE_MCP` | 否 | 启用 MCP (true/false) |
| `ENABLE_RAG` | 否 | 启用 RAG (true/false) |
| `ROUTING_MODE` | 否 | 路由模式 (fixed/agent-rag) |
| `TOOL_CALLING_MODE` | 否 | 工具调用模式 (rule/llm) |

**说明**:
- `QWEN_API_KEY`: 必需，用于 LLM 对话和意图识别
- `DASHSCOPE_API_KEY`: 可选，用于 Agent-RAG 的 Embedding 向量化
  - 设置后：使用语义相似度匹配 Agent（推荐）
  - 不设置：使用关键词匹配（回退模式）

### 3.2 配置文件

配置文件位于 `deploy/config/config.json`：

```json
{
  "server": {
    "registry_port": 8500,
    "orchestrator_port": 5000,
    "math_agent_port": 5001,
    "fwi_theory_agent_port": 5002,
    "fwi_teaching_agent_port": 5003,
    "general_research_agent_port": 5004
  },
  "features": {
    "enable_mcp": true,
    "routing_mode": "agent-rag",
    "tool_calling_mode": "llm"
  }
}
```

## 四、访问控制

### 4.1 简单 Token 认证

设置环境变量：
```bash
export AGENT_API_TOKEN=your-secret-token
```

在请求中携带 Token：
```bash
curl -X POST http://localhost:5000/ \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer your-secret-token" \
  -d '{...}'
```

### 4.2 客户端白名单

在配置文件中设置：
```json
{
  "access": {
    "allowed_clients": ["client-1", "client-2"]
  }
}
```

## 五、日志管理

### 5.1 日志位置

```
deploy/logs/
├── registry.log
├── orchestrator.log
├── math_agent.log
├── fwi_theory_agent.log
├── fwi_teaching_agent.log
└── general_research_agent.log
```

### 5.2 查看日志

```bash
# 实时查看 Orchestrator 日志
tail -f deploy/logs/orchestrator.log

# 搜索错误
grep "ERROR" deploy/logs/*.log

# 搜索特定请求
grep "req:xxx" deploy/logs/orchestrator.log
```

### 5.3 日志格式

```
[时间戳][级别][AgentID][req:请求ID][ctx:会话ID][标签] 消息
```

示例：
```
[2026-06-04 00:58:33.783][INFO ][orch-1][req:req-123][ctx:ctx-1][REQ] 什么是 FWI
```

## 六、监控

### 6.1 健康检查

```bash
# 检查 Registry
curl http://localhost:8500/v1/agents

# 检查 Orchestrator
curl http://localhost:5000/.well-known/agent-card.json
```

### 6.2 状态脚本

```bash
./deploy/scripts/status.sh
```

输出示例：
```
==========================================
FWI Agent 平台状态
==========================================

Redis:
  运行中

服务状态:
  registry (port 8500): 运行中 (PID: 12345)
  math_agent (port 5001): 运行中 (PID: 12346)
  fwi_theory_agent (port 5002): 运行中 (PID: 12347)
  fwi_teaching_agent (port 5003): 运行中 (PID: 12348)
  general_research_agent (port 5004): 运行中 (PID: 12349)
  orchestrator (port 5000): 运行中 (PID: 12350)

注册的 Agent:
  - math-1: Math Agent
  - fwi-theory-1: FWI Theory Agent
  - fwi-teaching-1: FWI Teaching Agent
  - general-research-1: General Research Agent
  - orch-1: AI Orchestrator
```

## 七、故障排查

### 7.1 启动失败

**问题**: 找不到可执行文件
```bash
# 解决: 先编译
cd build && cmake .. && make -j$(nproc)
```

**问题**: Redis 连接失败
```bash
# 解决: 启动 Redis
sudo systemctl start redis-server
# 或
redis-server --daemonize yes
```

**问题**: API Key 无效
```bash
# 解决: 检查环境变量
echo $QWEN_API_KEY
```

### 7.2 运行时问题

**问题**: Agent 不可用
```bash
# 检查日志
tail -f deploy/logs/orchestrator.log

# 检查 Registry
curl http://localhost:8500/v1/agents
```

**问题**: MCP 工具不可用
```bash
# 检查 MCP Server 是否编译
ls mcp_server_integrated/build/mcp_server

# 检查日志
grep "MCP" deploy/logs/orchestrator.log
```

### 7.3 性能问题

**问题**: 响应慢
- 检查网络延迟
- 检查 API 调用耗时
- 考虑启用缓存

## 八、多用户访问

### 8.1 当前实现

- 简单 Token 认证
- 日志记录 request_id、context_id
- 支持多会话

### 8.2 未来扩展

- 用户管理
- 权限控制
- 配额管理
- 审计日志

## 九、文件结构

```
deploy/
├── config/
│   └── config.json        # 配置文件
├── scripts/
│   ├── start.sh           # 启动脚本
│   ├── stop.sh            # 停止脚本
│   └── status.sh          # 状态检查脚本
├── logs/                  # 日志目录
└── pids/                  # PID 文件目录
```
