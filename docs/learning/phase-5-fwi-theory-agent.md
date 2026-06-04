# Phase 5: FWITheoryAgent — 学习文档

## 一、目标

创建独立的 FWI 理论 Agent，专注于全波形反演理论知识问答，能被 Agent-RAG 路由正确选择。

## 二、设计思路

### 2.1 为什么需要独立的 FWITheoryAgent

**原来的问题**:

```cpp
// 原来的代码 — FWI 处理直接在 Orchestrator 中
if (intent == "fwi") {
    response_text = handle_fwi_query(user_text, context_id);
    // 直接在 Orchestrator 中用 prompt 处理
}
```

**痛点分析**:

| 痛点 | 具体问题 | 影响 |
|------|----------|------|
| **职责不清** | Orchestrator 既负责路由又负责 FWI 回答 | 代码臃肿 |
| **无法扩展** | FWI 相关功能都堆在 Orchestrator 中 | 维护困难 |
| **无法独立部署** | FWI 功能不能单独部署到其他机器 | 灵活性差 |
| **无法被 Agent-RAG 选择** | 不是独立 Agent，没有 AgentCard | 无法动态路由 |

**解决方案**: 创建独立的 FWITheoryAgent。

### 2.2 FWITheoryAgent 的定位

**类比**: 就像医院的专科医生

| 专科医生 | FWITheoryAgent |
|----------|----------------|
| 专攻某一领域 | 专攻 FWI 理论 |
| 有专业资质 | 有专业 AgentCard |
| 接收分诊台转来的患者 | 接收 Orchestrator 转来的请求 |
| 使用专业设备 | 使用专业 FWI prompt |

### 2.3 FWITheoryAgent 的能力

| 能力 | 说明 | 示例 |
|------|------|------|
| FWI 理论 | 解释目标函数、梯度推导 | "什么是 FWI?" |
| Cycle Skipping | 解释周波跳跃问题 | "什么是 cycle skipping?" |
| 反演策略 | 解释多尺度 FWI、AWI 等 | "多尺度 FWI 是什么?" |
| 正则化 | 解释 Tikhonov、TV 等 | "如何选择正则化参数?" |

## 三、技术实现详解

### 3.1 FWITheoryAgent 类结构

```cpp
class FWITheoryAgent {
public:
    FWITheoryAgent(const std::string& agent_id,
                   const std::string& listen_address,
                   const std::string& registry_url,
                   const std::string& api_key,
                   const std::string& redis_host,
                   int redis_port);

    void start(int port);

private:
    // 请求处理
    std::string handle_request(const std::string& body);
    void handle_stream_request(const std::string& body,
                               std::function<bool(const std::string&)> write_callback);

    // 核心能力
    std::string answer_fwi_question(const std::string& query, const std::string& context_id);

    // 辅助函数
    void save_message(const std::string& context_id, const AgentMessage& message);
    std::string get_agent_card();

    // 成员变量
    std::string agent_id_;
    std::string listen_address_;
    std::shared_ptr<RedisTaskStore> task_store_;
    QwenClient qwen_client_;
    RegistryClient registry_client_;
};
```

### 3.2 注册到 Registry（带完整 AgentCard）

```cpp
void start(int port) {
    // ...

    // 注册到注册中心（带完整 AgentCard）
    AgentRegistration registration;
    registration.id = agent_id_;
    registration.name = "FWI Theory Agent";
    registration.address = listen_address_;
    registration.tags = {"fwi", "theory", "geophysics", "inversion"};

    // 描述 — 用于 Agent-RAG 语义匹配
    registration.description = "全波形反演(FWI)理论专家，解释 FWI/AWI/cycle skipping/"
                               "伴随状态法/梯度推导/多尺度策略等概念。"
                               "适合 FWI 理论学习、论文阅读、科研讨论。";

    // 能力
    registration.capabilities = {false, false, false};  // streaming, tool_calling, knowledge_base

    // 技能 — 用于 Agent-RAG 语义匹配
    registration.skills = {
        {
            "fwi_theory",
            "解释 FWI 理论基础、目标函数、梯度推导",
            {"什么是 FWI?", "解释伴随状态法", "FWI 的数学原理"}
        },
        {
            "cycle_skipping",
            "解释 cycle skipping 问题及解决方法",
            {"什么是 cycle skipping?", "如何避免周波跳跃?", "cycle skipping 的原因"}
        },
        {
            "inversion_strategy",
            "解释各种反演策略",
            {"多尺度 FWI", "自适应波形反演 AWI", "包络反演"}
        },
        {
            "regularization",
            "解释正则化技术",
            {"Tikhonov 正则化", "TV 正则化", "如何选择正则化参数"}
        }
    };

    // 构建完整 AgentCard
    registration.agent_card = registration.build_agent_card();

    // 注册
    registry_client_.register_agent(registration);
}
```

**为什么这样设计 AgentCard**:

| 字段 | 用途 | 示例 |
|------|------|------|
| tags | 快速标签匹配 | "fwi", "theory" |
| description | 语义匹配 | "全波形反演理论专家..." |
| skills[].name | 技能名称匹配 | "fwi_theory" |
| skills[].description | 技能描述匹配 | "解释 FWI 理论基础..." |
| skills[].input_examples | 输入示例匹配 | "什么是 FWI?" |

### 3.3 专业 FWI System Prompt

```cpp
std::string answer_fwi_question(const std::string& query, const std::string& context_id) {
    // 专业 FWI system prompt
    std::string system_prompt =
        "你是一位全波形反演(FWI)领域的资深科研助手，同时具备教学能力。\n\n"

        "## 专业知识范围\n"
        "- FWI 理论基础：最小二乘目标函数、Fréchet 梯度推导、伴随状态法(adjoint-state method)\n"
        "- 常见问题诊断：cycle skipping（周波跳跃）、局部极小值陷阱、振幅匹配与相位匹配\n"
        "- 高级反演策略：多尺度反演(multiscale FWI)、自适应波形反演(AWI)、包络反演(envelope inversion)\n"
        "- 正则化技术：Tikhonov 正则化、TV 正则化、总变分、模型平滑约束\n"
        "- 数值方法：有限差分(FD)、有限元(FEM)、谱元法(SEM)、声波/弹性波方程\n"
        "- 数据与模型：炮集数据(gather)、速度模型(velocity model)、观测系统(acquisition geometry)\n"
        "- 工业应用：油气储层成像、地壳结构反演、CO₂ 监测、微震定位\n\n"

        "## 回答要求\n"
        "1. 概念解释要准确严谨，必要时给出数学公式（LaTeX 格式，用 $...$ 包裹）\n"
        "2. 如果涉及算法实现，给出 Python 或 C++ 伪代码思路\n"
        "3. 可以用生活类比帮助理解抽象概念\n"
        "4. 如果用户问的是教学类问题，用\"概念 → 直觉 → 数学 → 代码思路\"的结构回答\n\n"

        "历史对话：\n" + history_text;

    return qwen_client_.chat(system_prompt, query);
}
```

**Prompt 设计要点**:

| 要点 | 说明 |
|------|------|
| 角色设定 | "资深科研助手，同时具备教学能力" |
| 知识范围 | 明确列出所有专业领域 |
| 回答要求 | 格式化要求（公式、代码、类比） |
| 历史对话 | 支持多轮对话 |

### 3.4 请求处理流程

```
用户问题 "什么是 cycle skipping"
    │
    ▼
Orchestrator (Agent-RAG 路由)
    │ AgentRetriever: fwi-theory-1 score=1.0
    │ LLMAgentSelector: 选择 fwi-theory-1
    │
    ▼
POST http://localhost:5002/
    │ {
    │   "method": "message/send",
    │   "params": {
    │     "message": {
    │       "role": "user",
    │       "contextId": "fwi-ctx",
    │       "parts": [{"kind": "text", "text": "什么是 cycle skipping"}]
    │     }
    │   }
    │ }
    ▼
FWITheoryAgent.handle_request()
    │
    ├─ 解析请求
    ├─ 保存用户消息
    ├─ answer_fwi_question()
    │   ├─ 获取历史对话
    │   ├─ 构造 system prompt
    │   └─ 调用 QwenClient.chat()
    ├─ 保存 Agent 响应
    └─ 返回响应
    │
    ▼
响应: "Cycle skipping（周波跳跃）是全波形反演中最经典的非线性问题之一..."
```

## 四、测试验证

### 4.1 启动系统

```bash
export ROUTING_MODE=agent-rag
./examples/ai_orchestrator/start_system.sh
```

### 4.2 检查注册

```bash
curl http://localhost:8500/v1/agent/cards | python3 -m json.tool
```

预期看到 3 个 Agent:
- fwi-theory-1
- math-1
- orch-1

### 4.3 测试 FWI 查询

```bash
curl -X POST http://localhost:5000/ -d '{
  "jsonrpc":"2.0","id":"test","method":"message/send",
  "params":{"message":{"role":"user","contextId":"ctx",
  "parts":[{"kind":"text","text":"什么是 cycle skipping"}]}}
}'
```

### 4.4 查看路由日志

```bash
grep "RETRIEVE\|CANDIDATE\|ROUTE\|CALL" examples/ai_orchestrator/logs/orchestrator.log
```

**预期日志**:
```
[RETRIEVE] found 3 candidates
[CANDIDATE] fwi-theory-1 (score=1.000000, reason=skill:fwi_theory,skill:cycle_skipping)
[CANDIDATE] math-1 (score=0.000000, reason=default)
[CANDIDATE] orch-1 (score=0.000000, reason=default)
[ROUTE] → fwi-theory-1
[CALL] fwi-theory-1 at http://localhost:5002
```

## 五、技术原理总结

### 5.1 专业 Agent 设计原则

**单一职责**: 每个 Agent 只负责一个领域
- FWITheoryAgent: 只做 FWI 理论问答
- MathAgent: 只做数学计算
- 后续: FWITeachingAgent, FWIModelAgent 等

**优势**:
- 代码清晰，易于维护
- 可以独立部署
- 可以独立扩展

### 5.2 System Prompt 设计

**角色设定**: 明确 Agent 的身份和能力

**知识范围**: 列出所有专业领域，帮助 LLM 理解边界

**回答要求**: 格式化要求，确保输出质量

**历史对话**: 支持多轮对话，保持上下文

### 5.3 Agent-RAG 集成

**关键**: AgentCard 的设计直接影响 Agent-RAG 的路由准确性

| AgentCard 字段 | 对路由的影响 |
|----------------|-------------|
| tags | 快速标签匹配，权重 +0.3 |
| description | 语义匹配，权重 +0.05 per keyword |
| skills[].name | 技能名称匹配，权重 +0.1 |
| skills[].description | 技能描述匹配，权重 +0.05 |
| skills[].input_examples | 输入示例匹配，权重 +0.2 |

## 六、后续扩展

### 6.1 接入本地知识库

```cpp
// 在 answer_fwi_question() 中加入知识库检索
auto relevant_docs = knowledge_base_.search(query, 3);
std::string doc_context;
for (const auto& doc : relevant_docs) {
    doc_context += doc.content + "\n\n";
}

system_prompt += "\n## 参考资料\n" + doc_context;
```

### 6.2 接入 MCP 工具

```cpp
// 接入 FWI metadata 工具
if (query.find("速度模型") != std::string::npos) {
    auto models = mcp_client_.callTool("list_models", "{}");
    system_prompt += "\n## 可用速度模型\n" + models.result;
}
```

### 6.3 接入真实 FWI 计算

```cpp
// 接入真实反演模块
if (query.find("执行反演") != std::string::npos) {
    auto result = mcp_client_.callTool("run_fwi", model_params);
    return "反演完成，结果：" + result.result;
}
```

## 七、文件结构

```
examples/ai_orchestrator/
├── orchestrator_main.cpp          # Orchestrator
├── math_agent_main.cpp            # MathAgent
├── fwi_theory_agent_main.cpp      # FWITheoryAgent (新增)
├── registry_server_main.cpp       # Registry
├── client_main.cpp                # 交互式客户端
├── start_system.sh                # 启动脚本
└── stop_system.sh                 # 停止脚本
```
