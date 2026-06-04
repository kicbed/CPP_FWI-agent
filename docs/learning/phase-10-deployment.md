# Phase 10: 部署配置 — 学习文档

## 一、目标

创建部署脚本、配置文件、日志管理，让课题组成员可以方便地部署和使用 FWI Agent 平台。

## 二、设计思路

### 2.1 为什么需要部署配置

**原来的问题**:
- 启动命令长且复杂
- 配置散落在各处
- 没有统一的管理工具

**解决方案**: 创建标准化的部署工具。

### 2.2 部署工具设计

| 工具 | 功能 | 位置 |
|------|------|------|
| `start.sh` | 启动所有服务 | `deploy/scripts/` |
| `stop.sh` | 停止所有服务 | `deploy/scripts/` |
| `status.sh` | 检查服务状态 | `deploy/scripts/` |
| `config.json` | 配置文件 | `deploy/config/` |

## 三、文件结构

```
deploy/
├── config/
│   └── config.json        # 配置文件
├── scripts/
│   ├── start.sh           # 启动脚本
│   ├── stop.sh            # 停止脚本
│   └── status.sh          # 状态检查脚本
├── logs/                  # 日志目录
├── pids/                  # PID 文件目录
└── README.md              # 部署文档
```

## 四、使用说明

### 4.1 快速启动

```bash
# 设置环境变量
export QWEN_API_KEY=sk-your-api-key

# 启动系统
./deploy/scripts/start.sh
```

### 4.2 检查状态

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

### 4.3 停止系统

```bash
./deploy/scripts/stop.sh
```

### 4.4 查看日志

```bash
# 实时查看 Orchestrator 日志
tail -f deploy/logs/orchestrator.log

# 搜索错误
grep "ERROR" deploy/logs/*.log
```

## 五、配置说明

### 5.1 环境变量

| 变量 | 必需 | 说明 |
|------|------|------|
| `QWEN_API_KEY` | 是 | 通义千问 API Key |
| `DASHSCOPE_API_KEY` | 否 | DashScope API Key |
| `AGENT_API_TOKEN` | 否 | 访问令牌 |
| `ROUTING_MODE` | 否 | 路由模式 |
| `TOOL_CALLING_MODE` | 否 | 工具调用模式 |

### 5.2 配置文件

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
  },
  "access": {
    "enable_auth": false,
    "api_token": "${AGENT_API_TOKEN}"
  }
}
```

## 六、访问控制

### 6.1 Token 认证

```bash
export AGENT_API_TOKEN=your-secret-token

curl -X POST http://localhost:5000/ \
  -H "Authorization: Bearer your-secret-token" \
  -d '{...}'
```

### 6.2 日志审计

每个请求都有唯一标识：
- `request_id`: 请求唯一 ID
- `context_id`: 会话 ID

日志格式：
```
[时间戳][级别][AgentID][req:请求ID][ctx:会话ID][标签] 消息
```

## 七、故障排查

### 7.1 常见问题

| 问题 | 解决方案 |
|------|----------|
| 找不到可执行文件 | 先编译：`cd build && cmake .. && make -j` |
| Redis 连接失败 | 启动 Redis：`redis-server --daemonize yes` |
| API Key 无效 | 检查环境变量：`echo $QWEN_API_KEY` |
| Agent 不可用 | 检查日志：`tail -f deploy/logs/*.log` |

### 7.2 日志位置

```
deploy/logs/
├── registry.log
├── orchestrator.log
├── math_agent.log
├── fwi_theory_agent.log
├── fwi_teaching_agent.log
└── general_research_agent.log
```
