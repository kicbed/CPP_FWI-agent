# RAG-MCP 使用指南

## 概述

RAG-MCP (Retrieval-Augmented Generation for Model Context Protocol) 是一个基于检索增强生成的智能工具选择框架。它通过语义相似度搜索，从大量 MCP 工具中自动选择与用户查询最相关的工具。

## 核心特性

| 特性 | 描述 |
|------|------|
| 语义搜索 | 基于向量相似度的工具检索 |
| 向量缓存 | LRU 缓存减少 API 调用 |
| 自动降级 | 服务不可用时返回全量工具 |
| 工具验证 | 可选的工具可用性验证 |
| 持久化 | 索引可保存到文件 |

## 架构

```
┌─────────────────────────────────────────────────────────────────┐
│                        ToolRetriever                            │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    retrieve(query)                       │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│              ┌───────────────┼───────────────┐                 │
│              ▼               ▼               ▼                 │
│  ┌───────────────┐  ┌───────────────┐  ┌───────────────┐      │
│  │EmbeddingService│  │  VectorIndex  │  │ToolValidator │      │
│  │               │  │               │  │   (可选)      │      │
│  │  ┌─────────┐  │  │  ┌─────────┐  │  └───────────────┘      │
│  │  │DashScope│  │  │  │ Cosine  │  │                         │
│  │  │  API    │  │  │  │Similarity│  │                         │
│  │  └─────────┘  │  │  └─────────┘  │                         │
│  │       │       │  └───────────────┘                         │
│  │       ▼       │                                             │
│  │  ┌─────────┐  │                                             │
│  │  │Embedding│  │                                             │
│  │  │ Cache   │  │                                             │
│  │  │ (LRU)   │  │                                             │
│  │  └─────────┘  │                                             │
│  └───────────────┘                                             │
└─────────────────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 设置环境变量

```bash
export DASHSCOPE_API_KEY=sk-your-api-key
```

### 2. 配置 RAG-MCP

```cpp
#include "agent_rpc/mcp/mcp_agent_integration.h"

using namespace agent_rpc::mcp;

MCPAgentConfig config;
config.enable_mcp = true;
config.mcp_server_path = "/path/to/mcp_server";

// RAG-MCP 配置
config.rag_config.enabled = true;
config.rag_config.api_key = std::getenv("DASHSCOPE_API_KEY");
config.rag_config.model = "text-embedding-v2";
config.rag_config.dimension = 1536;
config.rag_config.top_k = 5;
config.rag_config.similarity_threshold = 0.3f;

// 缓存配置
config.rag_config.enable_cache = true;
config.rag_config.cache_max_size = 1000;
config.rag_config.cache_ttl_seconds = 3600;

// 可选: 工具验证
config.rag_config.enable_validation = false;
config.rag_config.validation_timeout_ms = 5000;
```

### 3. 初始化并使用

```cpp
MCPAgentIntegration mcp;
mcp.initialize(config);

// 获取与查询相关的工具
std::string query = "计算 123 + 456";
auto tools = mcp.getRelevantTools(query);

for (const auto& tool : tools) {
    std::cout << "工具: " << tool.name 
              << ", 相关度: " << tool.relevance_score 
              << std::endl;
}

// 获取 LLM 函数调用格式
std::string functions_json = mcp.getRelevantToolsAsJson(query);
```

## 配置参数详解

### RAGMCPConfig

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| enabled | bool | false | 是否启用 RAG-MCP |
| api_key | string | "" | DashScope API Key |
| model | string | "text-embedding-v2" | Embedding 模型 |
| dimension | int | 1536 | 向量维度 |
| top_k | int | 5 | 返回工具数量 |
| similarity_threshold | float | 0.3 | 相似度阈值 |
| index_path | string | "" | 索引文件路径 |
| enable_cache | bool | true | 是否启用缓存 |
| cache_max_size | size_t | 1000 | 缓存最大条目 |
| cache_ttl_seconds | int | 3600 | 缓存过期时间 |
| enable_validation | bool | false | 是否启用工具验证 |
| validation_timeout_ms | int | 5000 | 验证超时时间 |

### EmbeddingConfig

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| api_key | string | 环境变量 | DashScope API Key |
| model | string | "text-embedding-v2" | 模型名称 |
| dimension | int | 1536 | 向量维度 |
| max_retries | int | 3 | 最大重试次数 |
| timeout_ms | int | 30000 | 请求超时 (毫秒) |
| base_url | string | DashScope URL | API 基础 URL |

### CacheConfig

| 参数 | 类型 | 默认值 | 描述 |
|------|------|--------|------|
| enabled | bool | true | 是否启用缓存 |
| max_size | size_t | 1000 | 最大缓存条目 |
| ttl_seconds | int | 3600 | 过期时间 (秒) |

## 核心组件

### EmbeddingService

负责调用 DashScope API 进行文本向量化。

```cpp
#include "agent_rpc/mcp/rag/embedding_service.h"

EmbeddingConfig config;
config.api_key = "sk-xxx";

EmbeddingService service(config);

// 单文本向量化
auto result = service.embed("计算两个数的和");
if (result.success) {
    std::vector<float> embedding = result.embedding;
}

// 批量向量化
std::vector<std::string> texts = {"加法", "减法", "乘法"};
auto batch_result = service.embedBatch(texts);
```

### EmbeddingCache

LRU 缓存，减少重复的 API 调用。

```cpp
#include "agent_rpc/mcp/rag/embedding_cache.h"

CacheConfig config;
config.max_size = 1000;
config.ttl_seconds = 3600;

EmbeddingCache cache(config);

// 存入缓存
cache.put("key", embedding_vector);

// 获取缓存
auto cached = cache.get("key");
if (cached.has_value()) {
    std::vector<float> embedding = cached.value();
}

// 缓存统计
auto stats = cache.getStats();
std::cout << "命中率: " << stats.hit_rate << std::endl;
```

### VectorIndex

向量索引，支持相似度搜索。

```cpp
#include "agent_rpc/mcp/rag/vector_index.h"

VectorIndex index;

// 添加工具
IndexedTool tool;
tool.name = "calculator";
tool.description = "计算数学表达式";
tool.embedding = embedding_vector;
index.addTool(tool);

// 搜索
auto results = index.search(query_embedding, top_k);
for (const auto& result : results) {
    std::cout << result.tool_name << ": " << result.similarity << std::endl;
}

// 持久化
index.saveToFile("index.json");
index.loadFromFile("index.json");
```

### ToolRetriever

工具检索器，整合所有组件。

```cpp
#include "agent_rpc/mcp/rag/tool_retriever.h"

RetrieverConfig config;
config.top_k = 5;
config.similarity_threshold = 0.3f;

ToolRetriever retriever(config);
retriever.initialize();

// 索引工具
std::vector<ToolInfo> tools = mcp_client.getAvailableTools();
retriever.indexTools(tools);

// 检索
auto results = retriever.retrieve("计算 123 + 456");
```

### ToolValidator

可选的工具验证器。

```cpp
#include "agent_rpc/mcp/rag/tool_validator.h"

ToolValidator validator(mcp_client, 5000); // 5秒超时

ValidationResult result = validator.validate(tool_info);
if (result.is_valid) {
    // 工具可用
} else {
    std::cout << "验证失败: " << result.error_message << std::endl;
}
```

## 使用场景

### 场景 1: 大量工具的智能选择

当 MCP Server 提供数十甚至上百个工具时，RAG-MCP 可以根据用户查询自动选择最相关的工具。

```cpp
// 假设有 100 个工具
auto all_tools = mcp.getAvailableTools();
std::cout << "总工具数: " << all_tools.size() << std::endl;

// RAG-MCP 只返回最相关的 5 个
auto relevant = mcp.getRelevantTools("发送邮件给张三");
std::cout << "相关工具数: " << relevant.size() << std::endl;
```

### 场景 2: LLM 函数调用

将检索到的工具转换为 LLM 函数调用格式。

```cpp
std::string query = "查询北京天气";
std::string functions = mcp.getRelevantToolsAsJson(query);

// 发送给 LLM
std::string prompt = "用户问题: " + query + "\n可用函数:\n" + functions;
auto response = llm.chat(prompt);
```

### 场景 3: 索引持久化

保存索引到文件，避免重复向量化。

```cpp
// 首次运行: 构建并保存索引
config.rag_config.index_path = "tools_index.json";
mcp.initialize(config);

// 后续运行: 自动加载索引
// 如果索引文件存在，会跳过向量化步骤
```

## 性能优化

### 1. 启用缓存

```cpp
config.rag_config.enable_cache = true;
config.rag_config.cache_max_size = 1000;
```

### 2. 调整 Top-K

```cpp
// 减少返回数量可以提高性能
config.rag_config.top_k = 3;
```

### 3. 提高相似度阈值

```cpp
// 过滤掉低相关度的工具
config.rag_config.similarity_threshold = 0.5f;
```

### 4. 禁用工具验证

```cpp
// 验证会增加延迟
config.rag_config.enable_validation = false;
```

### 5. 使用索引持久化

```cpp
// 避免每次启动都重新向量化
config.rag_config.index_path = "index.json";
```

## 降级机制

RAG-MCP 在以下情况会自动降级：

| 情况 | 降级行为 |
|------|----------|
| API Key 未设置 | 返回所有可用工具 |
| DashScope API 不可用 | 返回所有可用工具 |
| 向量化失败 | 返回所有可用工具 |
| 索引为空 | 返回所有可用工具 |

```cpp
// 检查 RAG-MCP 是否可用
if (mcp.isRAGAvailable()) {
    auto tools = mcp.getRelevantTools(query);
} else {
    // 降级到全量工具
    auto tools = mcp.getAvailableTools();
}
```

## 错误处理

```cpp
try {
    auto tools = mcp.getRelevantTools(query);
} catch (const EmbeddingException& e) {
    // 向量化错误
    std::cerr << "Embedding error: " << e.what() << std::endl;
} catch (const IndexException& e) {
    // 索引错误
    std::cerr << "Index error: " << e.what() << std::endl;
}
```

## 监控指标

RAG-MCP 提供以下监控指标：

| 指标 | 描述 |
|------|------|
| rag_embedding_requests_total | 向量化请求总数 |
| rag_embedding_latency_ms | 向量化延迟 |
| rag_cache_hits_total | 缓存命中次数 |
| rag_cache_misses_total | 缓存未命中次数 |
| rag_search_latency_ms | 搜索延迟 |
| rag_tools_retrieved | 检索到的工具数量 |

## 最佳实践

1. **合理设置 Top-K**: 根据实际需求设置，通常 3-5 个足够
2. **启用缓存**: 减少 API 调用，提高响应速度
3. **使用索引持久化**: 避免重复向量化
4. **监控缓存命中率**: 如果命中率低，考虑增加缓存大小
5. **定期刷新索引**: 工具更新后需要重新索引

## 示例代码

完整示例请参考 `examples/rag_mcp_example.cpp`。
