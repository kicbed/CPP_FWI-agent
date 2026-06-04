# Embedding 升级说明

## 一、问题

原来 Agent-RAG 使用关键词匹配，没有复用现有的 EmbeddingService，且每次都要重新计算向量。

## 二、升级内容

### 2.1 AgentRetriever 升级

**原来**: 只支持关键词匹配，每次重新计算

**现在**: 支持两种模式 + 向量缓存
1. **Embedding 模式**: 使用 DashScope API 生成向量（推荐）
2. **关键词模式**: 使用关键词匹配（无 API Key 时的回退）

### 2.2 缓存策略

| 缓存对象 | 缓存位置 | 更新时机 | TTL |
|----------|----------|----------|-----|
| AgentCard 向量 | `cached_agent_embeddings_` | Agent 注册/注销时 | 永久（直到变化） |
| 查询向量 | `EmbeddingCache` (LRU) | 首次查询时 | 1 小时 |

### 2.3 复用现有组件

| 组件 | 位置 | 用途 |
|------|------|------|
| `EmbeddingService` | `mcp/rag/embedding_service.h` | 调用 DashScope API |
| `EmbeddingCache` | `mcp/rag/embedding_cache.h` | LRU 缓存 |

## 三、缓存原理

### 3.1 AgentCard 向量缓存

```
Agent 注册 → Agent 列表变化 → 重新计算所有 Agent 向量 → 缓存

用户查询 → 检查 Agent 列表是否变化
  ├─ 没变化 → 使用缓存的 Agent 向量
  └─ 变化了 → 重新计算
```

**优势**: Agent 注册是低频操作，查询是高频操作。缓存后每次查询只需计算 1 个查询向量。

### 3.2 查询向量缓存 (LRU)

```
用户查询 "什么是 FWI"
  ├─ 缓存命中 → 直接返回向量
  └─ 缓存未命中 → 调用 API → 存入缓存 → 返回向量

1 小时后 TTL 过期 → 重新计算
```

**优势**: 相同或相似查询不需要重复调用 API。

### 3.3 API 调用次数对比

| 场景 | 无缓存 | 有缓存 |
|------|--------|--------|
| 首次查询 | 5 次 API（4 Agent + 1 查询） | 5 次 API |
| 相同查询 | 5 次 API | 0 次 API |
| 不同查询 | 5 次 API | 1 次 API |
| Agent 注册后 | 5 次 API | 5 次 API |

## 四、代码实现

```cpp
class AgentRetriever {
public:
    AgentRetriever(RegistryClient& registry_client,
                   const std::string& api_key = "")
        : registry_client_(registry_client)
        , use_embedding_(!api_key.empty()) {

        if (use_embedding_) {
            embedding_service_ = std::make_unique<EmbeddingService>(config);

            // 初始化查询缓存
            CacheConfig cache_config;
            cache_config.max_size = 100;  // 缓存 100 个查询向量
            cache_config.ttl_seconds = 3600;  // 1 小时过期
            query_cache_ = std::make_unique<EmbeddingCache>(cache_config);
        }
    }

    std::vector<AgentRetrievalResult> retrieve_with_embedding(
        const std::string& query,
        const std::vector<AgentRegistration>& agents) {

        // 1. 检查 Agent 列表是否变化
        std::string hash = compute_agent_list_hash(agents);
        if (hash != cached_agent_list_hash_) {
            update_agent_embeddings(agents);  // 重新计算
            cached_agent_list_hash_ = hash;
        }

        // 2. 获取查询向量（带缓存）
        std::vector<float> query_embedding;
        auto cached = query_cache_->get(query);
        if (cached.has_value()) {
            query_embedding = cached.value();  // 缓存命中
        } else {
            query_embedding = embedding_service_->embed(query);
            query_cache_->put(query, query_embedding);  // 存入缓存
        }

        // 3. 计算相似度
        for (size_t i = 0; i < agents.size(); ++i) {
            float score = cosine_similarity(query_embedding, cached_agent_embeddings_[i]);
            results.push_back({agents[i], score, "embedding"});
        }

        return results;
    }

private:
    // Agent 向量缓存
    std::vector<std::vector<float>> cached_agent_embeddings_;
    std::string cached_agent_list_hash_;

    // 查询向量缓存
    std::unique_ptr<EmbeddingCache> query_cache_;
};
```

## 五、向量持久化

### 5.1 问题

重启服务后，缓存的向量丢失，需要重新调用 API。

### 5.2 解决方案：VectorStore

使用 JSON 文件持久化向量：

```
resources/embeddings/
├── agent_cards.json   # AgentCard 向量
├── tools.json         # 工具描述向量
└── knowledge.json     # 知识库向量
```

### 5.3 加载流程

```
服务启动
    │
    ▼
加载 resources/embeddings/agent_cards.json
    ├─ 文件存在 → 加载到内存
    └─ 文件不存在 → 空
    │
    ▼
Agent 注册
    │
    ├─ 向量已存在 → 使用缓存
    └─ 向量不存在 → 调用 API → 保存到内存 + 文件
    │
    ▼
用户查询
    │
    ├─ Agent 列表没变 → 使用内存中的向量
    └─ Agent 列表变化 → 更新向量 + 保存文件
```

### 5.4 对比

| 方案 | 启动速度 | API 调用 | 复杂度 |
|------|----------|----------|--------|
| 每次重算 | 慢 | 多 | 低 |
| JSON 文件持久化 | 快 | 少 | 低 |
| 数据库（FAISS/Milvus） | 快 | 少 | 高 |

### 5.5 为什么不用数据库

| 场景 | 数据量 | 建议方案 |
|------|--------|----------|
| Agent < 100 | 小 | JSON 文件（当前） |
| Agent 100-1000 | 中 | Redis + 向量扩展 |
| Agent > 1000 | 大 | Milvus/FAISS |

当前场景：Agent 数量 < 10，JSON 文件足够。

## 六、配置

```bash
# 启用 Embedding 模式
export DASHSCOPE_API_KEY=sk-your-dashscope-api-key

# 不设置则使用关键词模式（回退）
```

## 七、对比

| 方法 | 优点 | 缺点 |
|------|------|------|
| 关键词匹配 | 简单快速，无需 API | 不够智能 |
| Embedding（无缓存） | 语义理解好 | 每次调用 API |
| Embedding（内存缓存） | 语义理解好，延迟低 | 重启后丢失 |
| Embedding（文件持久化） | 语义理解好，延迟低，重启不丢失 | 需要文件系统 |
