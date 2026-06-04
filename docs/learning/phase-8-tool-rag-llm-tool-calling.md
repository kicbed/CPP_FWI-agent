# Phase 8: Tool-RAG + LLM Tool Calling — 学习文档

## 一、目标

实现 Tool-RAG（工具检索）+ LLM Tool Calling（LLM 选择工具），让 Agent 能够智能选择和调用 MCP 工具。

## 二、设计思路

### 2.1 问题分析

**原来的工具调用方式**:

```cpp
// 原来的代码 — 规则选择工具
if (query.find("计算") != std::string::npos) {
    tool = "calculator";
} else if (query.find("模型") != std::string::npos) {
    tool = "list_models";
}
```

**痛点分析**:

| 痛点 | 具体问题 | 影响 |
|------|----------|------|
| **规则硬编码** | 每增加一个工具都要改规则 | 维护成本高 |
| **不够智能** | 无法理解复杂语义 | 工具选择不准确 |
| **无法扩展** | 工具数量增多时规则爆炸 | 扩展性差 |

**解决方案**: 实现 Tool-RAG + LLM Tool Calling。

### 2.2 什么是 Tool-RAG

**Tool-RAG** 是一种基于检索增强生成（RAG）的工具选择方法。

**类比**: 就像医生开处方

| 医生开处方 | Tool-RAG |
|-----------|----------|
| 患者描述症状 | 用户输入问题 |
| 医生查看可用药物 | Tool-RAG 检索候选工具 |
| 医生选择药物 | LLM 选择工具 |
| 开具处方 | 生成工具调用参数 |

### 2.3 Tool-RAG 流程

```
用户问题: "有哪些速度模型？"
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 1: Tool-RAG 检索候选工具                                │
│                                                             │
│ 从 MCP Server 获取所有工具:                                  │
│ - calculator: 计算数学表达式                                 │
│ - list_models: 列出速度模型                                  │
│ - inspect_model: 查看模型详情                                │
│ - formula_helper: 查询公式                                   │
│ - ...                                                       │
│                                                             │
│ 计算相关度:                                                  │
│ - "模型" ↔ "list_models" → 相关度高                          │
│ - "模型" ↔ "calculator" → 相关度低                           │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 2: LLM 选择工具并生成参数                               │
│                                                             │
│ 构造 prompt:                                                │
│ "以下是可用工具:                                              │
│  1. list_models: 列出速度模型                                │
│  2. inspect_model: 查看模型详情                              │
│  用户问题: 有哪些速度模型？                                   │
│  请选择工具并生成参数"                                        │
│                                                             │
│ LLM 输出:                                                   │
│ {"tool_name": "list_models", "arguments": {}}               │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 3: 执行工具调用                                         │
│                                                             │
│ MCP tools/call:                                             │
│ {                                                           │
│   "method": "tools/call",                                   │
│   "params": {                                               │
│     "name": "list_models",                                  │
│     "arguments": {}                                         │
│   }                                                         │
│ }                                                           │
│                                                             │
│ 返回: 模型列表 JSON                                          │
└─────────────────────────────────────────────────────────────┘
    │
    ▼
┌─────────────────────────────────────────────────────────────┐
│ Step 4: LLM 总结工具结果                                     │
│                                                             │
│ 将工具结果加入 prompt，让 LLM 生成最终回答                    │
└─────────────────────────────────────────────────────────────┘
```

## 三、技术实现详解

### 3.1 ToolCallingEngine 类

```cpp
// orchestrator/include/agent_rpc/orchestrator/tool_calling_engine.h

/**
 * @brief Tool Calling Engine
 *
 * 实现 Tool-RAG + LLM Tool Calling。
 */
class ToolCallingEngine {
public:
    ToolCallingEngine(MCPAgentIntegration* mcp_integration,
                     QwenClient& llm_client)
        : mcp_integration_(mcp_integration)
        , llm_client_(llm_client) {}

    /**
     * @brief 处理查询
     * @param query 用户查询
     * @return 工具结果，如果不需要工具则返回空
     *
     * 流程:
     * 1. 检索候选工具 (Tool-RAG)
     * 2. LLM 选择工具并生成参数
     * 3. 执行工具
     * 4. 返回结果
     */
    std::string process(const std::string& query) {
        // Step 1: 检索候选工具
        auto candidates = retrieve_tools(query, 5);
        if (candidates.empty()) return "";

        // Step 2: LLM 选择工具
        auto tool_call = select_tool(query, candidates);
        if (tool_call.tool_name.empty()) return "";

        // Step 3: 执行工具
        auto result = execute_tool(tool_call);

        return result.result;
    }

private:
    /**
     * @brief 检索候选工具 (Tool-RAG)
     */
    std::vector<ToolInfo> retrieve_tools(const std::string& query, int topK) {
        if (mcp_integration_->isRAGEnabled()) {
            // 使用 RAG 检索
            return mcp_integration_->getRelevantTools(query, topK);
        }

        // 回退: 返回所有工具
        return mcp_integration_->getAvailableTools();
    }

    /**
     * @brief LLM 选择工具
     */
    ToolCallResult select_tool(const std::string& query,
                              const std::vector<ToolInfo>& candidates) {
        // 构造 prompt
        std::string prompt = build_tool_selection_prompt(query, candidates);

        // 调用 LLM
        std::string response = llm_client_.chat(
            "你是一个工具选择器。只返回 JSON。",
            prompt
        );

        // 解析响应
        return parse_tool_call(response, candidates);
    }

    /**
     * @brief 执行工具
     */
    ToolCallResult execute_tool(const ToolCallResult& call) {
        auto tool_result = mcp_integration_->callTool(
            call.tool_name, call.arguments.dump());
        // ...
    }

    MCPAgentIntegration* mcp_integration_;
    QwenClient& llm_client_;
};
```

### 3.2 Prompt 构造

```cpp
std::string build_tool_selection_prompt(const std::string& query,
                                       const std::vector<ToolInfo>& candidates) {
    std::ostringstream oss;

    oss << "以下是可用的工具列表：\n\n";

    for (size_t i = 0; i < candidates.size(); ++i) {
        oss << (i + 1) << ". 工具名: " << candidates[i].name << "\n";
        oss << "   描述: " << candidates[i].description << "\n";
        if (!candidates[i].input_schema.empty()) {
            oss << "   参数: " << candidates[i].input_schema << "\n";
        }
        oss << "\n";
    }

    oss << "用户问题: " << query << "\n\n";
    oss << "请选择最合适的工具并生成参数。返回 JSON 格式:\n";
    oss << "{\"tool_name\": \"工具名\", \"arguments\": {参数}}\n";
    oss << "如果不需要工具，返回: {\"tool_name\": \"\"}\n";

    return oss.str();
}
```

**Prompt 示例**:
```
以下是可用的工具列表：

1. 工具名: calculator
   描述: 计算数学表达式
   参数: {"type":"object","properties":{"expression":{"type":"string"}}}

2. 工具名: list_models
   描述: 列出可用速度模型
   参数: {"type":"object","properties":{}}

3. 工具名: inspect_model
   描述: 查看模型详情
   参数: {"type":"object","properties":{"model_id":{"type":"string"}}}

用户问题: 有哪些速度模型？

请选择最合适的工具并生成参数。返回 JSON:
{"tool_name": "工具名", "arguments": {参数}}
```

**LLM 输出**:
```json
{"tool_name": "list_models", "arguments": {}}
```

### 3.3 集成到 Orchestrator

```cpp
class AIOrchestrator {
    // 新增成员
    ToolCallingEngine tool_calling_engine_;

    // 修改 handle_general_query
    std::string handle_general_query(const std::string& query, ...) {
        // Tool-RAG: 尝试使用工具
        std::string tool_context;
        if (orch_config_.tool_calling_mode == ToolCallingMode::LLM) {
            tool_context = tool_calling_engine_.process(query);
        }

        // 构建 prompt
        std::string system_prompt = "你是一个智能助手...\n\n";

        if (!tool_context.empty()) {
            system_prompt += "工具查询结果:\n" + tool_context + "\n\n";
        }

        system_prompt += "历史对话:\n" + history_text;

        return qwen_client_.chat(system_prompt, query);
    }
};
```

## 四、测试验证

### 4.1 启动系统

```bash
export TOOL_CALLING_MODE=llm
export ROUTING_MODE=agent-rag
./examples/ai_orchestrator/start_system.sh
```

### 4.2 测试查询

```bash
curl -X POST http://localhost:5000/ -d '{
  "jsonrpc":"2.0","id":"test","method":"message/send",
  "params":{"message":{"role":"user","contextId":"ctx",
  "parts":[{"kind":"text","text":"有哪些速度模型？"}]}}
}'
```

### 4.3 验证工具调用

检查 Orchestrator 日志中是否有工具调用记录。

## 五、技术原理总结

### 5.1 RAG (Retrieval-Augmented Generation)

**传统 RAG**: 检索文档 → LLM 生成答案

**Tool-RAG**: 检索工具 → LLM 选择工具 → 执行工具 → LLM 总结

### 5.2 LLM Tool Calling

**原理**: 让 LLM 理解工具描述，选择合适的工具并生成参数。

**优势**:
- 理解复杂语义
- 自动生成参数
- 支持多工具组合

### 5.3 两阶段选择

| 阶段 | 方法 | 目标 |
|------|------|------|
| 召回 | RAG/关键词匹配 | 不漏掉正确工具 |
| 精排 | LLM 选择 | 选出最佳工具 |

## 六、配置说明

### 6.1 环境变量

```bash
# 工具调用模式
export TOOL_CALLING_MODE=rule  # 规则选择
export TOOL_CALLING_MODE=llm   # LLM 选择
```

### 6.2 对比

| 模式 | 优点 | 缺点 |
|------|------|------|
| rule | 简单快速 | 不够智能 |
| llm | 智能准确 | 需要 LLM 调用 |

## 七、后续扩展

### 7.1 多工具组合

```cpp
// LLM 可以选择多个工具
std::vector<ToolCallResult> select_multiple_tools(const std::string& query,
                                                   const std::vector<ToolInfo>& candidates) {
    // LLM 输出:
    // {"tool_calls": [
    //   {"tool_name": "list_models", "arguments": {}},
    //   {"tool_name": "formula_helper", "arguments": {"formula_name": "objective"}}
    // ]}
}
```

### 7.2 工具链

```cpp
// 工具 A 的输出作为工具 B 的输入
auto result1 = execute_tool({"tool_a", args1});
auto result2 = execute_tool({"tool_b", {{"input", result1.result}}});
```

### 7.3 工具结果验证

```cpp
// LLM 验证工具结果是否合理
std::string validate_result(const std::string& query,
                           const ToolCallResult& result) {
    std::string prompt = "用户问题: " + query + "\n"
                        "工具结果: " + result.result + "\n"
                        "结果是否合理？返回 JSON: {\"valid\": true/false}";
    return llm_client_.chat(prompt);
}
```
