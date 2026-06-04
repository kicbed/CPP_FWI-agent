# Phase 4: Agent-RAG 动态路由 — 学习文档

## 一、目标

实现 AgentRetriever + LLMAgentSelector，支持基于 AgentCard 描述的动态路由，替代硬编码的 if-else 路由。

## 二、设计思路

### 2.1 问题分析

**原来的路由方式**:

```cpp
// 原来的代码 — 硬编码 if-else 路由
std::string intent = analyze_intent(user_text);  // 调用 LLM 识别意图

if (intent == "math") {
    response_text = call_math_agent(user_text, context_id);
} else if (intent == "code") {
    response_text = call_code_agent(user_text, context_id);
} else if (intent == "fwi") {
    response_text = handle_fwi_query(user_text, context_id);
} else {
    response_text = handle_general_query(user_text, context_id);
}
```

**痛点分析**:

| 痛点 | 具体问题 | 影响 |
|------|----------|------|
| **硬编码** | 每增加一个 Agent 都要改 Orchestrator 代码 | 维护成本高 |
| **无法扩展** | 新增 Agent 需要修改路由逻辑 | 扩展性差 |
| **不智能** | 只能按固定类别路由 | 无法处理复杂场景 |
| **意图识别粗糙** | 只能识别 4 个类别 | 无法区分细分场景 |

**解决方案**: 实现 Agent-RAG 动态路由。

### 2.2 什么是 Agent-RAG

**Agent-RAG** 是一种基于检索增强生成（RAG）的 Agent 路由方法。

**类比**: 就像医院的分诊台

| 分诊台 | Agent-RAG |
|--------|-----------|
| 患者描述症状 | 用户输入问题 |
| 护士查看各科室介绍 | AgentRetriever 获取 AgentCard |
| 护士推荐科室 | 计算相似度 |
| 患者选择科室 | LLMAgentSelector 最终选择 |
| 前往科室就诊 | 调用选中的 Agent |

### 2.3 Agent-RAG 路由流程

```
用户问题: "计算 1+1"
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 1: AgentRetriever.retrieve(query, topK=5)              │
│                                                             │
│ 1. 从 Registry 获取所有 AgentCard                           │
│    - math-1: "数学计算助手..."                               │
│    - orch-1: "智能协调器..."                                 │
│                                                             │
│ 2. 计算问题与每个 AgentCard 的相似度                         │
│    - "计算 1+1" vs "数学计算助手" → 相似度: 0.35             │
│    - "计算 1+1" vs "智能协调器" → 相似度: 0.25              │
│                                                             │
│ 3. 排序取 topK                                              │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 2: LLMAgentSelector.select(query, candidates)          │
│                                                             │
│ 1. 构造 prompt:                                             │
│    "以下是可用 Agent:                                        │
│     1. math-1: 数学计算助手...                               │
│     2. orch-1: 智能协调器...                                 │
│     用户问题: 计算 1+1                                       │
│     请选择最合适的 Agent"                                    │
│                                                             │
│ 2. 调用 LLM 选择                                            │
│                                                             │
│ 3. 解析输出: {"agent_id": "math-1"}                         │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 3: 调用选中的 Agent                                    │
│                                                             │
│ POST http://localhost:5001/                                 │
│ {"method": "message/send", "params": {"message": {...}}}    │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
响应: "1 + 1 = 2"
```

## 三、技术实现详解

### 3.1 AgentRetriever — 候选 Agent 召回

```cpp
// orchestrator/include/agent_rpc/orchestrator/agent_retriever.h

/**
 * @brief Agent Retrieval Result
 */
struct AgentRetrievalResult {
    AgentRegistration agent;     // Agent 注册信息
    float relevance_score;       // 相关度分数 (0-1)
    std::string match_reason;    // 匹配原因
};

/**
 * @brief Agent Retriever - 候选 Agent 召回
 *
 * 实现 Agent-RAG 中的 "R" (Retrieval)。
 * 从 Registry 获取候选 Agent，基于查询相似度排序。
 */
class AgentRetriever {
public:
    explicit AgentRetriever(RegistryClient& registry_client)
        : registry_client_(registry_client) {}

    /**
     * @brief 检索候选 Agent
     * @param query 用户查询
     * @param topK 最大候选数
     * @return 排序后的候选 Agent 列表
     */
    std::vector<AgentRetrievalResult> retrieve(const std::string& query, int topK = 5) {
        // 1. 从 Registry 获取所有 Agent
        auto agents = registry_client_.get_all_agents();

        // 2. 计算每个 Agent 的相关度
        std::vector<AgentRetrievalResult> results;
        for (const auto& agent : agents) {
            float score = compute_relevance(query, agent);
            std::string reason = compute_match_reason(query, agent);
            results.push_back({agent, score, reason});
        }

        // 3. 按分数降序排序
        std::sort(results.begin(), results.end(),
                  [](const AgentRetrievalResult& a, const AgentRetrievalResult& b) {
                      return a.relevance_score > b.relevance_score;
                  });

        // 4. 返回 topK
        if (results.size() > static_cast<size_t>(topK)) {
            results.resize(topK);
        }

        return results;
    }

private:
    /**
     * @brief 计算相关度分数
     *
     * 评分因素:
     * - 标签匹配: +0.3 per matching tag
     * - 描述关键词匹配: +0.05 per keyword
     * - 技能名称匹配: +0.1 per match
     * - 技能描述匹配: +0.05 per match
     * - 输入示例匹配: +0.2 per match
     */
    float compute_relevance(const std::string& query, const AgentRegistration& agent) {
        float score = 0.0f;
        std::string lower_query = to_lowercase(query);
        auto keywords = split_keywords(lower_query);

        // 1. 标签匹配
        for (const auto& tag : agent.tags) {
            std::string lower_tag = to_lowercase(tag);
            if (lower_query.find(lower_tag) != std::string::npos) {
                score += 0.3f;
            }
            for (const auto& kw : keywords) {
                if (lower_tag.find(kw) != std::string::npos) {
                    score += 0.1f;
                }
            }
        }

        // 2. 描述匹配
        std::string lower_desc = to_lowercase(agent.description);
        for (const auto& kw : keywords) {
            if (lower_desc.find(kw) != std::string::npos) {
                score += 0.05f;
            }
        }

        // 3. 技能匹配
        for (const auto& skill : agent.skills) {
            std::string lower_skill_name = to_lowercase(skill.name);
            std::string lower_skill_desc = to_lowercase(skill.description);

            for (const auto& kw : keywords) {
                if (lower_skill_name.find(kw) != std::string::npos) {
                    score += 0.1f;
                }
                if (lower_skill_desc.find(kw) != std::string::npos) {
                    score += 0.05f;
                }
            }

            // 输入示例匹配
            for (const auto& example : skill.input_examples) {
                std::string lower_example = to_lowercase(example);
                if (compute_keyword_overlap(lower_query, lower_example) > 0.3f) {
                    score += 0.2f;
                }
            }
        }

        return std::min(1.0f, std::max(0.0f, score));
    }

    RegistryClient& registry_client_;
};
```

**相似度计算详解**:

| 匹配类型 | 权重 | 示例 |
|----------|------|------|
| 标签完全匹配 | +0.3 | 查询"数学" ↔ 标签"math" |
| 标签关键词匹配 | +0.1 | 查询"计算" ↔ 标签"calculator" |
| 描述关键词匹配 | +0.05 | 查询"计算" ↔ 描述"擅长计算..." |
| 技能名称匹配 | +0.1 | 查询"计算" ↔ 技能"math_calculation" |
| 技能描述匹配 | +0.05 | 查询"计算" ↔ 技能描述"执行计算..." |
| 输入示例匹配 | +0.2 | 查询"计算1+1" ↔ 示例"计算1+1" |

### 3.2 LLMAgentSelector — LLM 最终选择

```cpp
// orchestrator/include/agent_rpc/orchestrator/llm_agent_selector.h

/**
 * @brief LLM Agent Selector
 *
 * 实现 Agent-RAG 中的 "A" (Agent selection)。
 * 使用 LLM 从候选 Agent 中选择最合适的。
 */
class LLMAgentSelector {
public:
    explicit LLMAgentSelector(QwenClient& llm_client)
        : llm_client_(llm_client) {}

    /**
     * @brief 选择最合适的 Agent
     * @param query 用户查询
     * @param candidates 候选 Agent 列表
     * @return 选中的 Agent ID
     */
    std::string select(const std::string& query,
                      const std::vector<AgentRetrievalResult>& candidates) {
        if (candidates.empty()) {
            return "";
        }

        // 如果只有一个候选，直接返回
        if (candidates.size() == 1) {
            return candidates[0].agent.id;
        }

        // 构造 prompt
        std::string prompt = build_prompt(query, candidates);

        try {
            // 调用 LLM
            std::string response = llm_client_.chat(
                "你是一个 Agent 选择器。根据用户问题，从候选 Agent 中选择最合适的。"
                "只返回 JSON，不要其他内容。",
                prompt
            );

            // 解析响应
            return parse_selection(response, candidates);

        } catch (const std::exception& e) {
            // Fallback: 返回第一个候选
            return candidates[0].agent.id;
        }
    }

private:
    /**
     * @brief 构造 prompt
     */
    std::string build_prompt(const std::string& query,
                            const std::vector<AgentRetrievalResult>& candidates) {
        std::ostringstream oss;

        oss << "以下是可用的 Agent 列表：\n\n";

        for (size_t i = 0; i < candidates.size(); ++i) {
            const auto& agent = candidates[i].agent;
            oss << (i + 1) << ". Agent ID: " << agent.id << "\n";
            oss << "   名称: " << agent.name << "\n";
            oss << "   描述: " << agent.description << "\n";
            oss << "   标签: ";
            for (size_t j = 0; j < agent.tags.size(); ++j) {
                if (j > 0) oss << ", ";
                oss << agent.tags[j];
            }
            oss << "\n";

            if (!agent.skills.empty()) {
                oss << "   技能:\n";
                for (const auto& skill : agent.skills) {
                    oss << "     - " << skill.name << ": " << skill.description << "\n";
                    if (!skill.input_examples.empty()) {
                        oss << "       示例: ";
                        for (size_t j = 0; j < skill.input_examples.size(); ++j) {
                            if (j > 0) oss << ", ";
                            oss << skill.input_examples[j];
                        }
                        oss << "\n";
                    }
                }
            }

            oss << "   相关度: " << candidates[i].relevance_score << "\n";
            oss << "   匹配原因: " << candidates[i].match_reason << "\n\n";
        }

        oss << "用户问题: " << query << "\n\n";
        oss << "请选择最合适的 Agent。返回 JSON 格式:\n";
        oss << "{\"agent_id\": \"选中的Agent ID\", \"reason\": \"选择原因\"}\n";

        return oss.str();
    }

    /**
     * @brief 解析 LLM 响应
     */
    std::string parse_selection(const std::string& response,
                               const std::vector<AgentRetrievalResult>& candidates) {
        try {
            auto j = json::parse(response);

            if (j.contains("agent_id")) {
                std::string agent_id = j["agent_id"].get<std::string>();

                // 验证选中的 Agent 是否在候选列表中
                for (const auto& candidate : candidates) {
                    if (candidate.agent.id == agent_id) {
                        return agent_id;
                    }
                }
            }

            // 验证失败，返回第一个候选
            return candidates[0].agent.id;

        } catch (const json::exception& e) {
            // JSON 解析失败，尝试从文本中提取
            // ... 省略错误处理
            return candidates[0].agent.id;
        }
    }

    QwenClient& llm_client_;
};
```

**Prompt 构造示例**:

```
以下是可用的 Agent 列表：

1. Agent ID: math-1
   名称: Math Agent
   描述: 专业数学计算助手，擅长各类数学问题求解
   标签: math, calculator, computation
   技能:
     - math_calculation: 执行各类数学计算和方程求解
       示例: 计算 1+1, 求解 x^2=4, 123*456 是多少
     - expression_evaluation: 计算数学表达式
       示例: sin(3.14), log(100), sqrt(16)
   相关度: 0.35
   匹配原因: skill:math_calculation

2. Agent ID: orch-1
   名称: AI Orchestrator
   描述: 智能协调器，负责意图识别和任务分发
   标签: orchestrator, coordinator
   技能:
     - intent_recognition: 识别用户意图并路由到相应的专业 Agent
       示例: 数学计算, FWI 理论, 通用问答
   相关度: 0.25
   匹配原因: skill:intent_recognition

用户问题: 计算 10+20

请选择最合适的 Agent。返回 JSON 格式:
{"agent_id": "选中的Agent ID", "reason": "选择原因"}
```

**LLM 输出**:
```json
{"agent_id": "math-1", "reason": "用户问题涉及数学计算，Math Agent 专门处理此类问题"}
```

### 3.3 集成到 Orchestrator

```cpp
class AIOrchestrator {
    // 新增成员
    AgentRetriever agent_retriever_;
    LLMAgentSelector llm_agent_selector_;

    /**
     * @brief Agent-RAG 动态路由
     */
    std::string route_with_agent_rag(const std::string& query,
                                     const std::string& context_id,
                                     const RequestContext& ctx) {
        // Step 1: Retrieve candidate Agents
        auto candidates = agent_retriever_.retrieve(query, 5);

        if (candidates.empty()) {
            return handle_general_query(query, context_id);
        }

        // Step 2: LLM selects the best Agent
        std::string selected_agent_id = llm_agent_selector_.select(query, candidates);

        if (selected_agent_id.empty()) {
            return handle_general_query(query, context_id);
        }

        // Step 3: Call the selected Agent
        std::string agent_url;
        for (const auto& candidate : candidates) {
            if (candidate.agent.id == selected_agent_id) {
                agent_url = candidate.agent.address;
                break;
            }
        }

        // Call the Agent
        return call_agent_by_url(agent_url, query, context_id);
    }
};
```

## 四、测试验证

### 4.1 启动 Agent-RAG 模式

```bash
export ROUTING_MODE=agent-rag
./examples/ai_orchestrator/start_system.sh
```

### 4.2 测试数学查询

```bash
curl -X POST http://localhost:5000/ -d '{
  "jsonrpc":"2.0","id":"test","method":"message/send",
  "params":{"message":{"role":"user","contextId":"ctx",
  "parts":[{"kind":"text","text":"计算 10+20"}]}}
}'
```

### 4.3 查看日志

```bash
tail -20 examples/ai_orchestrator/logs/orchestrator.log
```

**预期日志**:
```
[INFO ][orch-1][req:req-xxx][ctx:ctx][ROUTING] agent-rag mode
[INFO ][orch-1][req:req-xxx][ctx:ctx][RETRIEVE] found 2 candidates
[INFO ][orch-1][req:req-xxx][ctx:ctx][CANDIDATE] math-1 (score=0.350000, reason=skill:math_calculation)
[INFO ][orch-1][req:req-xxx][ctx:ctx][CANDIDATE] orch-1 (score=0.250000, reason=skill:intent_recognition)
[INFO ][orch-1][req:req-xxx][ctx:ctx][ROUTE] → math-1
[INFO ][orch-1][req:req-xxx][ctx:ctx][CALL] math-1 at http://localhost:5001
[INFO ][orch-1][req:req-xxx][ctx:ctx][RESP] completed in 2396ms
```

## 五、技术原理总结

### 5.1 RAG (Retrieval-Augmented Generation)

**原理**: 先检索相关信息，再用 LLM 生成答案。

**传统 RAG 流程**:
```
用户问题 → 检索文档 → LLM 生成答案
```

**Agent-RAG 流程**:
```
用户问题 → 检索 Agent → LLM 选择 Agent → 调用 Agent
```

**优势**:
- 不需要硬编码路由逻辑
- 新增 Agent 只需注册 AgentCard
- LLM 可以理解复杂语义

### 5.2 相似度计算

**关键词匹配**:
- 简单快速
- 不需要额外模型
- 准确率一般

**向量相似度**:
- 需要 Embedding 模型
- 语义理解更好
- 成本较高

**LLM 选择**:
- 最智能
- 成本最高
- 延迟最大

**本实现使用**: 关键词匹配（召回）+ LLM 选择（精排）

### 5.3 两阶段路由

**阶段 1: 召回 (Retrieval)**
- 快速筛选候选
- 使用关键词匹配
- 目标：不漏掉正确答案

**阶段 2: 精排 (Selection)**
- LLM 从候选中选择
- 理解复杂语义
- 目标：选出最佳答案

**优势**:
- 减少 LLM 调用次数（只对候选调用）
- 提高准确率（LLM 只需从少量候选中选择）

## 六、后续扩展

### 6.1 向量相似度

```cpp
// 使用 Embedding 模型计算相似度
float compute_vector_similarity(const std::string& query,
                                const AgentCard& card) {
    auto query_embedding = embedding_service.embed(query);
    auto card_embedding = embedding_service.embed(card.description);
    return cosine_similarity(query_embedding, card_embedding);
}
```

### 6.2 缓存

```cpp
// 缓存 AgentCard 向量
std::map<std::string, std::vector<float>> card_embeddings;

// 只在 Agent 注册时计算一次
void on_agent_register(const AgentCard& card) {
    card_embeddings[card.agent_id] = embedding_service.embed(card.description);
}
```

### 6.3 动态 topK

```cpp
// 根据候选质量动态调整 topK
int compute_topK(const std::vector<AgentRetrievalResult>& results) {
    // 如果最高分很高，只返回 1 个
    if (results[0].relevance_score > 0.8) {
        return 1;
    }
    // 否则返回更多候选
    return 5;
}
```

## 七、配置说明

### 7.1 环境变量

```bash
# 路由模式
export ROUTING_MODE=fixed        # 传统 if-else 路由
export ROUTING_MODE=agent-rag    # Agent-RAG 动态路由
```

### 7.2 启动命令

```bash
# 固定路由模式
export ROUTING_MODE=fixed
./examples/ai_orchestrator/start_system.sh

# Agent-RAG 路由模式
export ROUTING_MODE=agent-rag
./examples/ai_orchestrator/start_system.sh
```

## 八、对比总结

| 特性 | Fixed 路由 | Agent-RAG 路由 |
|------|-----------|---------------|
| 路由方式 | if-else 硬编码 | 动态检索 + LLM 选择 |
| 扩展性 | 差（需改代码） | 好（只需注册 AgentCard） |
| 智能程度 | 一般 | 高（LLM 理解语义） |
| 延迟 | 低 | 较高（需调用 LLM） |
| 成本 | 低 | 较高（LLM 调用） |
| 适用场景 | Agent 数量少 | Agent 数量多 |
