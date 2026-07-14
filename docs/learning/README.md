# FWI Agent 平台升级 — 学习文档索引

> **历史学习资料，不是当前运行手册。** 本目录按早期 Phase 保留架构演进记录，部分
> DashScope、三层记忆、启动脚本和“尚未接入真实 FWI”的描述已经被后续实现替代。
> 当前能力与命令以仓库根目录 `README.md`、`docs/DEPLOYMENT.md` 和
> `docs/PROJECT_CONTINUITY.md` 为准，不要直接照抄本目录中的旧部署或密钥示例。

## 项目概述

本项目是一个基于 C++ 和 gRPC 的多智能体科研助手平台，首个落地场景为 FWI（全波形反演）科研助手。

**核心特性**:
- Agent-RAG 动态路由（基于 Embedding 语义匹配）
- Tool-RAG + LLM Tool Calling（智能工具选择）
- 三层记忆管理（Session/Agent/Task）
- MCP 工具扩展
- 本地知识库
- 多 Agent 协作

## 升级阶段

| 阶段 | 文档 | 状态 | 核心内容 |
|------|------|------|----------|
| Phase 0 | [phase-0-baseline-verification.md](phase-0-baseline-verification.md) | ✅ | 基线验证 |
| Phase 1 | [phase-1-modern-architecture-foundation.md](phase-1-modern-architecture-foundation.md) | ✅ | RequestContext, TraceLogger, Config |
| Phase 2 | [phase-2-memory-manager.md](phase-2-memory-manager.md) | ✅ | MemoryManager 三层记忆 |
| Phase 3 | [phase-3-agent-card-registry.md](phase-3-agent-card-registry.md) | ✅ | AgentCard + Registry 增强 |
| Phase 4 | [phase-4-agent-rag-routing.md](phase-4-agent-rag-routing.md) | ✅ | Agent-RAG 动态路由 |
| Phase 5 | [phase-5-fwi-theory-agent.md](phase-5-fwi-theory-agent.md) | ✅ | FWITheoryAgent |
| Phase 6 | [phase-6-fwi-knowledge-base.md](phase-6-fwi-knowledge-base.md) | ✅ | FWI 本地知识库 |
| Phase 7 | [phase-7-fwi-mcp-tools.md](phase-7-fwi-mcp-tools.md) | ✅ | FWI MCP 工具 |
| Phase 8 | [phase-8-tool-rag-llm-tool-calling.md](phase-8-tool-rag-llm-tool-calling.md) | ✅ | Tool-RAG + LLM Tool Calling |
| Phase 9 | [phase-9-additional-agents.md](phase-9-additional-agents.md) | ✅ | 其他 FWI Agent |
| Phase 10 | [phase-10-deployment.md](phase-10-deployment.md) | ✅ | 部署配置 |

## 补充文档

| 文档 | 内容 |
|------|------|
| [upgrade-roadmap.md](upgrade-roadmap.md) | 升级路线图、2026 技术趋势 |
| [cpp-api-integration.md](cpp-api-integration.md) | C++ API 接入详解 |
| [embedding-upgrade.md](embedding-upgrade.md) | Embedding 升级、向量缓存、持久化策略 |

## 技术栈

| 技术 | 用途 |
|------|------|
| **C++17** | 主要编程语言 |
| **gRPC** | 远程过程调用 |
| **Protobuf** | 序列化 |
| **Redis** | 状态存储 |
| **hiredis** | Redis C 客户端 |
| **libcurl** | HTTP 客户端 |
| **nlohmann/json** | JSON 处理 |
| **CMake** | 构建系统 |
| **DashScope API** | Embedding 向量化 |

## 架构总览

```
┌─────────────────────────────────────────────────────────────────┐
│                        用户入口                                  │
│                    rpc_client / rpc_server                       │
└───────────────────────────────┬─────────────────────────────────┘
                                │
┌───────────────────────────────▼─────────────────────────────────┐
│                        Orchestrator (port 5000)                  │
│  RequestContext │ MemoryManager │ TraceLogger │ Config           │
│  AgentRetriever (Embedding+Cache) │ LLMAgentSelector │ ToolRAG  │
└───────────────────────────────┬─────────────────────────────────┘
                                │
        ┌───────────────────────┼───────────────────────┐
        │                       │                       │
┌───────▼───────┐ ┌─────────────▼─────────────┐ ┌───────▼───────┐
│  MathAgent    │ │    FWI Agents             │ │ General       │
│  (port 5001)  │ │ FWITheoryAgent (5002)     │ │ Research      │
│               │ │ FWITeachingAgent (5003)    │ │ Agent (5004)  │
└───────────────┘ └───────────────────────────┘ └───────────────┘

┌─────────────────────────────────────────────────────────────────┐
│  MCP Server │ Registry Server │ Redis │ KnowledgeBase           │
│  EmbeddingService │ EmbeddingCache │ VectorStore                │
└─────────────────────────────────────────────────────────────────┘
```

## 核心设计决策

### 1. Agent-RAG 路由

**问题**: 原来是 if-else 硬编码路由，无法扩展。

**方案**: 
- 使用 EmbeddingService 将 AgentCard 向量化
- 计算用户查询与 AgentCard 的余弦相似度
- LLMAgentSelector 从候选中最终选择

**缓存策略**:
- AgentCard 向量：持久化到 JSON 文件，Agent 变化时更新
- 查询向量：LRU 内存缓存，1 小时过期

### 2. Tool-RAG 工具选择

**问题**: 工具数量增多时，规则选择不智能。

**方案**:
- 使用 RAG 检索候选工具
- LLM 从候选中选择并生成参数
- C++ 执行 MCP tools/call

### 3. 三层记忆管理

**问题**: 所有 Agent 共用同一个 history key，记忆混杂。

**方案**:
- Session Memory: 用户可见对话
- Agent Memory: Agent 内部处理
- Task State: 任务生命周期

### 4. 向量持久化

**问题**: 重启服务后向量丢失，需要重新调用 API。

**方案**:
- AgentCard 向量：保存到 `resources/embeddings/agent_cards.json`
- 查询向量：LRU 内存缓存（不持久化）
- 启动时加载，变化时更新

## 学习路径

### 入门

1. 阅读 [Phase 0](phase-0-baseline-verification.md) 了解系统现状
2. 编译运行，测试基本功能

### 进阶

3. 阅读 [Phase 1](phase-1-modern-architecture-foundation.md) 理解 RequestContext 和 TraceLogger
4. 阅读 [Phase 2](phase-2-memory-manager.md) 理解记忆管理
5. 阅读 [Phase 3](phase-3-agent-card-registry.md) 理解 AgentCard

### 高级

6. 阅读 [Phase 4](phase-4-agent-rag-routing.md) 理解 Agent-RAG 路由
7. 阅读 [embedding-upgrade.md](embedding-upgrade.md) 理解 Embedding 和向量缓存
8. 阅读 [Phase 8](phase-8-tool-rag-llm-tool-calling.md) 理解 Tool-RAG

## 关键文件

| 文件 | 作用 |
|------|------|
| `examples/ai_orchestrator/orchestrator_main.cpp` | Orchestrator 主入口 |
| `examples/ai_orchestrator/math_agent_main.cpp` | MathAgent |
| `examples/ai_orchestrator/fwi_theory_agent_main.cpp` | FWITheoryAgent |
| `examples/ai_orchestrator/fwi_teaching_agent_main.cpp` | FWITeachingAgent |
| `examples/ai_orchestrator/general_research_agent_main.cpp` | GeneralResearchAgent |
| `examples/ai_orchestrator/registry_server_main.cpp` | Registry Server |
| `orchestrator/include/agent_rpc/orchestrator/request_context.h` | RequestContext |
| `orchestrator/include/agent_rpc/orchestrator/trace_logger.h` | TraceLogger |
| `orchestrator/include/agent_rpc/orchestrator/config.h` | OrchestratorConfig |
| `orchestrator/include/agent_rpc/orchestrator/memory_manager.h` | MemoryManager |
| `orchestrator/include/agent_rpc/orchestrator/agent_retriever.h` | AgentRetriever (Embedding) |
| `orchestrator/include/agent_rpc/orchestrator/llm_agent_selector.h` | LLMAgentSelector |
| `orchestrator/include/agent_rpc/orchestrator/tool_calling_engine.h` | ToolCallingEngine |
| `orchestrator/include/agent_rpc/orchestrator/knowledge_base.h` | KnowledgeBase |
| `orchestrator/include/agent_rpc/orchestrator/vector_store.h` | VectorStore |
| `mcp/include/agent_rpc/mcp/mcp_agent_integration.h` | MCP 集成 |
| `mcp/include/agent_rpc/mcp/rag/embedding_service.h` | EmbeddingService |
| `mcp/include/agent_rpc/mcp/rag/embedding_cache.h` | EmbeddingCache |
