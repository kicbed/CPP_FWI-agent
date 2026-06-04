# Phase 9: 其他 FWI Agent — 学习文档

## 一、目标

创建 FWITeachingAgent、GeneralResearchAgent，形成完整的 Agent 矩阵。

## 二、Agent 矩阵

| Agent | 端口 | 专长 | 标签 |
|-------|------|------|------|
| MathAgent | 5001 | 数学计算 | math, calculator |
| FWITheoryAgent | 5002 | FWI 理论 | fwi, theory |
| FWITeachingAgent | 5003 | FWI 教学 | fwi, teaching |
| GeneralResearchAgent | 5004 | 通用科研 | research, general |

## 三、各 Agent 设计

### 3.1 FWITeachingAgent

**定位**: FWI 教学助手

**教学风格**: 概念 → 直觉 → 数学 → 代码思路 → 汇报表达

**System Prompt**:
```
你是一位 FWI 领域的资深教师和科研汇报专家。

## 教学风格
你的回答必须遵循以下结构：
1. **概念** (1-2 句话): 用最简单的语言解释是什么
2. **直觉/类比**: 用生活中的例子帮助理解
3. **数学**: 给出核心公式（LaTeX 格式）
4. **代码思路**: 给出 Python/C++ 伪代码
5. **汇报表达**: 如何在论文/汇报中描述这个概念

## 特殊能力
- 帮助准备学术汇报
- 帮助撰写论文相关章节
- 对比不同方法的优缺点
```

**AgentCard**:
```json
{
  "agent_id": "fwi-teaching-1",
  "name": "FWI Teaching Agent",
  "description": "FWI 教学助手，用类比+数学+代码+汇报的方式教学",
  "tags": ["fwi", "teaching", "education"],
  "skills": [
    {"name": "fwi_teaching", "description": "用类比和图示解释 FWI 概念"},
    {"name": "paper_presentation", "description": "帮助准备 FWI 相关汇报"}
  ]
}
```

### 3.2 GeneralResearchAgent

**定位**: 通用科研助手

**能力范围**:
- 文献检索和综述
- 研究方法设计
- 学术写作
- 数据分析
- 科研项目管理

**System Prompt**:
```
你是一位经验丰富的科研助手，擅长各类科研问题。

## 能力范围
- 文献检索和综述
- 研究方法设计
- 学术写作（摘要、引言、方法、结果、讨论）
- 数据分析和可视化
- 科研项目管理
- 学术汇报准备
```

**AgentCard**:
```json
{
  "agent_id": "general-research-1",
  "name": "General Research Agent",
  "description": "通用科研助手，处理文献检索、研究方法、学术写作等问题",
  "tags": ["research", "general", "academic"],
  "skills": [
    {"name": "research_qa", "description": "回答科研相关问题"},
    {"name": "academic_writing", "description": "帮助学术写作"}
  ]
}
```

## 四、技术实现

### 4.1 Agent 模板

每个 Agent 都遵循相同的结构：

```cpp
class XxxAgent {
public:
    XxxAgent(agent_id, listen_address, registry_url, api_key, redis_host, redis_port);

    void start(int port) {
        // 1. 注册 HTTP 处理器
        server.register_handler("/", handle_request);
        server.register_stream_handler("/", handle_stream_request);
        server.register_handler("/.well-known/agent-card.json", get_agent_card);

        // 2. 注册到 Registry
        AgentRegistration registration;
        registration.id = agent_id_;
        registration.tags = {...};
        registration.description = "...";
        registration.skills = {...};
        registration.agent_card = registration.build_agent_card();
        registry_client_.register_agent(registration);

        // 3. 启动服务器
        server.start();
    }

private:
    std::string handle_request(const std::string& body);
    void handle_stream_request(const std::string& body, write_callback);
    std::string answer_xxx_question(const std::string& query, const std::string& context_id);
    std::string get_agent_card();
};
```

### 4.2 CMakeLists.txt

```cmake
add_executable(ai_fwi_teaching_agent
    fwi_teaching_agent_main.cpp
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/src/examples/redis_task_store.cpp
)

target_include_directories(ai_fwi_teaching_agent PRIVATE
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/include/a2a/examples
    ${CMAKE_CURRENT_SOURCE_DIR}/../../a2a/third_party
    ${CMAKE_CURRENT_SOURCE_DIR}/../../common/include
    ${CMAKE_BINARY_DIR}/proto
    ${HIREDIS_INCLUDE_DIRS}
)

target_link_libraries(ai_fwi_teaching_agent
    a2a
    proto_lib
    ${GRPC_LIBRARIES}
    ${PROTOBUF_LIBRARIES}
    CURL::libcurl
    Threads::Threads
    ${HIREDIS_LIBRARIES}
)
```

### 4.3 启动脚本

```bash
# 启动 FWI Teaching Agent
"$BIN_DIR/ai_fwi_teaching_agent" fwi-teaching-1 5003 http://localhost:8500 $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT

# 启动 General Research Agent
"$BIN_DIR/ai_general_research_agent" general-research-1 5004 http://localhost:8500 $API_KEY \
  --redis-host $REDIS_HOST --redis-port $REDIS_PORT
```

## 五、测试验证

### 5.1 启动系统

```bash
export ROUTING_MODE=agent-rag
./examples/ai_orchestrator/start_system.sh
```

### 5.2 检查注册

```bash
curl http://localhost:8500/v1/agent/cards | python3 -c "..."
```

预期输出:
```
Total agents: 5
  - math-1: Math Agent
  - fwi-theory-1: FWI Theory Agent
  - fwi-teaching-1: FWI Teaching Agent
  - general-research-1: General Research Agent
  - orch-1: AI Orchestrator
```

## 六、技术原理

### 6.1 单一职责原则

每个 Agent 只负责一个领域：
- MathAgent: 数学计算
- FWITheoryAgent: FWI 理论
- FWITeachingAgent: FWI 教学
- GeneralResearchAgent: 通用科研

**优势**:
- 代码清晰
- 易于维护
- 可独立部署

### 6.2 Agent-RAG 路由

Orchestrator 根据用户问题自动选择最合适的 Agent：
- "计算 1+1" → MathAgent
- "什么是 FWI" → FWITheoryAgent
- "用类比解释 FWI" → FWITeachingAgent
- "如何写论文摘要" → GeneralResearchAgent

## 七、后续扩展

### 7.1 FWIModelAgent

**功能**: 查看和管理速度模型

**工具调用**:
- `list_models`: 列出模型
- `inspect_model`: 查看模型详情

### 7.2 FWIDataAgent

**功能**: 查看和管理数据集

**工具调用**:
- `list_datasets`: 列出数据集
- `inspect_dataset`: 查看数据集详情

### 7.3 更多专业 Agent

- **SeismicProcessingAgent**: 地震数据处理
- **ImagingAgent**: 地震成像
- **InterpretationAgent**: 地质解释
