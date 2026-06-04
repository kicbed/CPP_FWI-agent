# Phase 3: AgentCard + Registry 增强 — 学习文档

## 一、目标

所有 Agent 注册时携带完整的 AgentCard 信息，Registry 支持 AgentCard 查询，为后续 Agent-RAG 路由做准备。

## 二、设计思路

### 2.1 什么是 AgentCard

**AgentCard** 是 A2A 协议中描述 Agent 能力的标准格式。

**类比**: 就像求职时投递的简历

| 简历字段 | AgentCard 字段 | 说明 |
|----------|---------------|------|
| 姓名 | agent_id, name | Agent 标识 |
| 自我介绍 | description | Agent 描述 |
| 技能 | skills | Agent 擅长什么 |
| 工作能力 | capabilities | 支持什么功能 |
| 联系方式 | endpoint | Agent 地址 |

### 2.2 为什么需要 AgentCard

**原来的问题**:

```cpp
// 原来的注册信息 — 信息不足
AgentRegistration registration;
registration.id = agent_id_;
registration.name = "Math Agent";
registration.tags = {"math", "calculator"};
```

**痛点分析**:

| 痛点 | 具体问题 | 影响 |
|------|----------|------|
| **信息不足** | 只有 tags，没有描述和技能详情 | 无法做语义匹配 |
| **无法语义匹配** | 后续 Agent-RAG 需要基于描述做匹配 | 路由不智能 |
| **能力不明确** | 不知道 Agent 是否支持流式、工具调用等 | 无法按能力选择 |

**解决方案**: 在 AgentRegistration 中新增 description、capabilities、skills 字段。

### 2.3 Agent-RAG 如何使用 AgentCard

**Agent-RAG 路由流程**:

```
用户问题: "什么是 cycle skipping"
    │
    ▼
AgentRetriever.retrieve()
    │
    │ 1. 从 Registry 获取所有 AgentCard
    │
    │ 2. 计算问题与每个 AgentCard 的相似度
    │
    │    "什么是 cycle skipping"
    │    vs
    │    "FWI 理论专家，解释 cycle skipping..." → 相似度: 0.85
    │    vs
    │    "数学计算助手，擅长方程求解..." → 相似度: 0.15
    │
    │ 3. 排序取 topK
    ▼
候选 Agent 列表
    │
    ▼
LLMAgentSelector.select()
    │
    │ 构造 prompt:
    │ "以下是可用 Agent:
    │  - FWITheoryAgent: FWI 理论专家...
    │  - MathAgent: 数学计算助手...
    │  用户问题: 什么是 cycle skipping
    │  请选择最合适的 Agent"
    │
    │ LLM 输出: {"agent_id": "fwi-theory-1"}
    ▼
FWITheoryAgent
```

## 三、技术实现详解

### 3.1 AgentSkill 结构

```cpp
/**
 * @brief Agent 技能定义
 *
 * 描述 Agent 的一个具体技能。
 * 用于 Agent-RAG 路由时的语义匹配。
 */
struct AgentSkill {
    std::string name;                    // 技能名称（如 "math_calculation"）
    std::string description;             // 技能描述（如 "执行各类数学计算"）
    std::vector<std::string> input_examples;  // 输入示例（如 ["计算 1+1", "求解 x^2=4"]）

    /**
     * @brief 序列化为 JSON
     *
     * 输出格式:
     * {
     *   "name": "math_calculation",
     *   "description": "执行各类数学计算",
     *   "input_examples": ["计算 1+1", "求解 x^2=4"]
     * }
     */
    json to_json() const {
        json j = {
            {"name", name},
            {"description", description}
        };
        if (!input_examples.empty()) {
            j["input_examples"] = input_examples;
        }
        return j;
    }

    /**
     * @brief 从 JSON 反序列化
     */
    static AgentSkill from_json(const json& j) {
        AgentSkill skill;
        skill.name = j.value("name", "");
        skill.description = j.value("description", "");
        if (j.contains("input_examples")) {
            skill.input_examples = j["input_examples"].get<std::vector<std::string>>();
        }
        return skill;
    }
};
```

**为什么需要 input_examples**:
- 帮助 LLM 理解技能用途
- 用于 few-shot 学习
- 例如：用户问 "计算 1+1"，可以匹配到 input_examples 中有类似示例的技能

### 3.2 AgentCapabilities 结构

```cpp
/**
 * @brief Agent 能力定义
 *
 * 描述 Agent 支持的功能特性。
 * 用于 Orchestrator 按能力选择 Agent。
 */
struct AgentCapabilities {
    bool streaming = false;      // 是否支持流式输出
    bool tool_calling = false;   // 是否支持工具调用
    bool knowledge_base = false; // 是否有知识库

    json to_json() const {
        return {
            {"streaming", streaming},
            {"tool_calling", tool_calling},
            {"knowledge_base", knowledge_base}
        };
    }

    static AgentCapabilities from_json(const json& j) {
        AgentCapabilities caps;
        caps.streaming = j.value("streaming", false);
        caps.tool_calling = j.value("tool_calling", false);
        caps.knowledge_base = j.value("knowledge_base", false);
        return caps;
    }
};
```

**能力字段用途**:

| 能力 | 用途 | 示例 |
|------|------|------|
| streaming | 需要流式输出时选择 | 用户要求"逐字输出" |
| tool_calling | 需要工具调用时选择 | 需要计算器 |
| knowledge_base | 需要知识库时选择 | 需要查询文档 |

### 3.3 增强的 AgentRegistration

```cpp
/**
 * @brief Agent 注册信息（增强版）
 *
 * 包含完整的 AgentCard 信息，支持 Agent-RAG 路由。
 */
struct AgentRegistration {
    // === 原有字段（保持兼容）===
    std::string id;              // Agent 唯一 ID
    std::string name;            // Agent 名称
    std::string address;         // Agent 地址 (http://host:port)
    std::vector<std::string> tags;  // Agent 标签
    std::chrono::system_clock::time_point last_heartbeat;  // 最后心跳时间
    json agent_card;             // Agent Card (A2A 协议标准)

    // === 新增字段（Agent-RAG 支持）===
    std::string description;     // Agent 描述（用于语义匹配）
    AgentCapabilities capabilities;  // Agent 能力
    std::vector<AgentSkill> skills;  // Agent 技能列表

    /**
     * @brief 构建完整的 AgentCard JSON
     *
     * 用于 Agent-RAG 路由时的语义匹配。
     * 返回格式:
     * {
     *   "agent_id": "math-1",
     *   "name": "Math Agent",
     *   "description": "专业数学计算助手...",
     *   "tags": ["math", "calculator"],
     *   "capabilities": {"streaming": false, "tool_calling": true},
     *   "endpoint": "http://localhost:5001",
     *   "skills": [...]
     * }
     */
    json build_agent_card() const {
        json card = {
            {"agent_id", id},
            {"name", name},
            {"description", description},
            {"tags", tags},
            {"capabilities", capabilities.to_json()},
            {"endpoint", address}
        };

        if (!skills.empty()) {
            json skills_json = json::array();
            for (const auto& skill : skills) {
                skills_json.push_back(skill.to_json());
            }
            card["skills"] = skills_json;
        }

        return card;
    }

    // === 序列化 ===
    json to_json() const {
        json j = {
            {"id", id},
            {"name", name},
            {"address", address},
            {"tags", tags},
            {"last_heartbeat", std::chrono::system_clock::to_time_t(last_heartbeat)}
        };

        // 新增字段
        if (!description.empty()) {
            j["description"] = description;
        }
        j["capabilities"] = capabilities.to_json();

        if (!skills.empty()) {
            json skills_json = json::array();
            for (const auto& skill : skills) {
                skills_json.push_back(skill.to_json());
            }
            j["skills"] = skills_json;
        }

        if (!agent_card.empty()) {
            j["agent_card"] = agent_card;
        }

        return j;
    }

    // === 反序列化 ===
    static AgentRegistration from_json(const json& j) {
        AgentRegistration reg;
        reg.id = j.at("id").get<std::string>();
        reg.name = j.at("name").get<std::string>();
        reg.address = j.at("address").get<std::string>();
        reg.tags = j.at("tags").get<std::vector<std::string>>();
        reg.last_heartbeat = std::chrono::system_clock::now();

        // 新增字段
        if (j.contains("description")) {
            reg.description = j["description"].get<std::string>();
        }
        if (j.contains("capabilities")) {
            reg.capabilities = AgentCapabilities::from_json(j["capabilities"]);
        }
        if (j.contains("skills")) {
            for (const auto& skill_json : j["skills"]) {
                reg.skills.push_back(AgentSkill::from_json(skill_json));
            }
        }

        if (j.contains("agent_card")) {
            reg.agent_card = j["agent_card"];
        }

        return reg;
    }
};
```

### 3.4 Registry 新增端点

```cpp
// Registry Server 新增端点

/**
 * @brief 获取所有 Agent 的 AgentCard
 *
 * 端点: GET /v1/agent/cards
 * 用途: Agent-RAG 路由时获取所有候选 Agent
 *
 * 响应格式:
 * {
 *   "cards": [
 *     {
 *       "agent_id": "math-1",
 *       "name": "Math Agent",
 *       "description": "...",
 *       "tags": ["math"],
 *       "capabilities": {...},
 *       "skills": [...]
 *     },
 *     ...
 *   ]
 * }
 */
server.register_handler("/v1/agent/cards", [this](const std::string&) {
    return handle_get_cards();
});

/**
 * @brief 根据标签查找 AgentCard
 *
 * 端点: POST /v1/agent/cards/find
 * 请求: {"tag": "math"}
 * 用途: 按标签筛选 Agent
 *
 * 响应格式:
 * {
 *   "cards": [
 *     {
 *       "agent_id": "math-1",
 *       ...
 *     }
 *   ]
 * }
 */
server.register_handler("/v1/agent/cards/find", [this](const std::string& body) {
    return handle_find_cards(body);
});
```

### 3.5 Agent 注册示例

**Orchestrator 注册**:
```cpp
AgentRegistration registration;
registration.id = agent_id_;
registration.name = "AI Orchestrator";
registration.address = listen_address_;
registration.tags = {"orchestrator", "coordinator"};

// 新增字段
registration.description = "智能协调器，负责意图识别和任务分发。"
                           "支持数学计算、FWI 科研问答、通用问答等多种场景。";
registration.capabilities = {
    true,   // streaming: 支持流式
    true,   // tool_calling: 支持工具调用
    false   // knowledge_base: 无知识库
};
registration.skills = {
    {
        "intent_recognition",           // name
        "识别用户意图并路由到相应的专业 Agent",  // description
        {"数学计算", "FWI 理论", "通用问答"}   // input_examples
    },
    {
        "task_coordination",
        "协调多个 Agent 完成复杂任务",
        {"多 Agent 协作"}
    }
};

// 构建完整 AgentCard
registration.agent_card = registration.build_agent_card();
```

**MathAgent 注册**:
```cpp
AgentRegistration registration;
registration.id = agent_id_;
registration.name = "Math Agent";
registration.address = listen_address_;
registration.tags = {"math", "calculator", "computation"};

// 新增字段
registration.description = "专业数学计算助手，擅长各类数学问题求解、方程求解、数值计算。"
                           "支持 MCP 工具调用（calculator 等）。";
registration.capabilities = {
    false,  // streaming: 不支持流式
    true,   // tool_calling: 支持工具调用
    false   // knowledge_base: 无知识库
};
registration.skills = {
    {
        "math_calculation",
        "执行各类数学计算和方程求解",
        {"计算 1+1", "求解 x^2=4", "123*456 是多少"}
    },
    {
        "expression_evaluation",
        "计算数学表达式",
        {"sin(3.14)", "log(100)", "sqrt(16)"}
    }
};

// 构建完整 AgentCard
registration.agent_card = registration.build_agent_card();
```

## 四、测试验证

### 4.1 获取所有 AgentCard

```bash
curl http://localhost:8500/v1/agent/cards
```

**响应**:
```json
{
  "cards": [
    {
      "agent_id": "math-1",
      "name": "Math Agent",
      "description": "专业数学计算助手...",
      "tags": ["math", "calculator", "computation"],
      "capabilities": {
        "streaming": false,
        "tool_calling": true,
        "knowledge_base": false
      },
      "endpoint": "http://localhost:5001",
      "skills": [
        {
          "name": "math_calculation",
          "description": "执行各类数学计算和方程求解",
          "input_examples": ["计算 1+1", "求解 x^2=4", "123*456 是多少"]
        },
        {
          "name": "expression_evaluation",
          "description": "计算数学表达式",
          "input_examples": ["sin(3.14)", "log(100)", "sqrt(16)"]
        }
      ]
    },
    {
      "agent_id": "orch-1",
      "name": "AI Orchestrator",
      "description": "智能协调器...",
      "tags": ["orchestrator", "coordinator"],
      "capabilities": {
        "streaming": true,
        "tool_calling": true,
        "knowledge_base": false
      },
      "endpoint": "http://localhost:5000",
      "skills": [
        {
          "name": "intent_recognition",
          "description": "识别用户意图并路由到相应的专业 Agent",
          "input_examples": ["数学计算", "FWI 理论", "通用问答"]
        }
      ]
    }
  ]
}
```

### 4.2 根据标签查找

```bash
curl -X POST http://localhost:8500/v1/agent/cards/find \
  -H "Content-Type: application/json" \
  -d '{"tag":"math"}'
```

**响应**: 只返回 Math Agent 的 AgentCard。

## 五、技术原理总结

### 5.1 A2A 协议中的 AgentCard

**A2A (Agent-to-Agent)** 协议是 Google 提出的 Agent 通信标准。

**AgentCard 作用**:
- 描述 Agent 的能力
- 支持服务发现
- 支持能力匹配

**标准字段**:
- `name`: Agent 名称
- `description`: 描述
- `url`: 端点地址
- `capabilities`: 能力
- `skills`: 技能列表

### 5.2 服务注册与发现

**服务注册流程**:
```
Agent 启动
    │
    ▼
构造 AgentRegistration
    │ 包含: id, name, address, tags, description, capabilities, skills
    │
    ▼
发送 POST /v1/agent/register 到 Registry
    │
    ▼
Registry 存储 AgentRegistration
    │ 内存中维护 agents_ map 和 tags_index_ map
    │
    ▼
启动心跳线程
    │ 每 10 秒发送一次心跳
    ▼
注册完成
```

**服务发现流程**:
```
Orchestrator 需要 Agent
    │
    ▼
发送 POST /v1/agent/cards/find 到 Registry
    │ 请求: {"tag": "math"}
    │
    ▼
Registry 查找匹配的 Agent
    │ 从 tags_index_ 中查找
    │
    ▼
返回 AgentCard 列表
    │
    ▼
Orchestrator 选择 Agent
```

**健康检查流程**:
```
Registry 定期检查
    │ 每 10 秒检查一次
    │
    ▼
遍历所有 Agent
    │ 检查 last_heartbeat
    │
    ▼
如果超时（30 秒）
    │ 移除不健康的 Agent
    ▼
更新 tags_index_
```

### 5.3 为 Agent-RAG 做准备

**Agent-RAG 路由的核心**:
1. ✅ 获取所有 AgentCard（本阶段实现）
2. ⏳ 计算问题与 AgentCard 的相似度（下一阶段）
3. ⏳ 选择最合适的 Agent（下一阶段）

**相似度计算方法**:

| 方法 | 优点 | 缺点 |
|------|------|------|
| 关键词匹配 | 简单快速 | 不够智能 |
| 向量相似度 | 语义理解 | 需要 Embedding 模型 |
| LLM 选择 | 最智能 | 成本高、延迟大 |

## 六、后续扩展

### 6.1 AgentRetriever（下一阶段实现）

```cpp
class AgentRetriever {
public:
    /**
     * @brief 检索相关 Agent
     * @param query 用户问题
     * @param topK 返回数量
     * @return 候选 Agent 列表
     *
     * 1. 从 Registry 获取所有 AgentCard
     * 2. 计算 query 与每个 AgentCard 的相似度
     * 3. 返回 topK 个最相关的
     */
    std::vector<AgentCard> retrieve(const std::string& query, int topK = 5);

private:
    /**
     * @brief 计算相似度
     * @param query 用户问题
     * @param card AgentCard
     * @return 相似度分数 (0-1)
     *
     * 简单实现: 关键词匹配
     * 进阶实现: 向量相似度
     */
    float compute_similarity(const std::string& query, const AgentCard& card);
};
```

### 6.2 LLMAgentSelector（下一阶段实现）

```cpp
class LLMAgentSelector {
public:
    /**
     * @brief 选择最合适的 Agent
     * @param query 用户问题
     * @param candidates 候选 Agent 列表
     * @return 选中的 Agent ID
     *
     * 1. 构造 prompt，列出所有候选 Agent
     * 2. 让 LLM 选择最合适的
     * 3. 解析 LLM 输出的 JSON
     */
    std::string select(const std::string& query,
                      const std::vector<AgentCard>& candidates);

private:
    QwenClient llm_client_;
};
```

## 七、API 参考

### 7.1 获取所有 AgentCard

**请求**:
```
GET /v1/agent/cards
```

**响应**:
```json
{
  "cards": [
    {
      "agent_id": "string",
      "name": "string",
      "description": "string",
      "tags": ["string"],
      "capabilities": {
        "streaming": boolean,
        "tool_calling": boolean,
        "knowledge_base": boolean
      },
      "endpoint": "string",
      "skills": [
        {
          "name": "string",
          "description": "string",
          "input_examples": ["string"]
        }
      ]
    }
  ]
}
```

### 7.2 根据标签查找 AgentCard

**请求**:
```
POST /v1/agent/cards/find
Content-Type: application/json

{
  "tag": "string"
}
```

**响应**:
```json
{
  "cards": [
    {
      "agent_id": "string",
      "name": "string",
      "description": "string",
      "tags": ["string"],
      "capabilities": {...},
      "endpoint": "string",
      "skills": [...]
    }
  ]
}
```
