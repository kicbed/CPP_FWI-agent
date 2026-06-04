# Phase 7: FWI MCP 工具 — 学习文档

## 一、目标

创建 FWI 元数据 MCP 工具，通过 `tools/list` 和 `tools/call` 暴露，让 Agent 能够查询速度模型、数据集、公式等信息。

## 二、设计思路

### 2.1 为什么需要 FWI MCP 工具

**原来的问题**:

FWITheoryAgent 只能用 LLM 回答理论问题，无法查询具体数据。

**痛点分析**:

| 痛点 | 具体问题 | 影响 |
|------|----------|------|
| **无法查询模型** | 用户问"Marmousi 模型多大？"时无法回答 | 缺乏数据支持 |
| **无法查询数据集** | 用户问"有哪些测试数据？"时无法回答 | 缺乏数据支持 |
| **无法查询公式** | 用户问"梯度公式是什么？"时只能用 LLM 回答 | 可能不准确 |
| **无法搜索知识库** | 用户问"cycle skipping 的解决方法"时无法搜索本地文档 | 知识库无法利用 |

**解决方案**: 创建 MCP 工具，让 Agent 能够调用这些工具获取信息。

### 2.2 MCP 工具设计

| 工具名 | 功能 | 输入 | 输出 |
|--------|------|------|------|
| `list_models` | 列出可用速度模型 | 无 | 模型列表 |
| `inspect_model` | 查看模型详情 | model_id | 模型 metadata |
| `list_datasets` | 列出可用数据集 | 无 | 数据集列表 |
| `inspect_dataset` | 查看数据集详情 | dataset_id | 数据集 metadata |
| `formula_helper` | 查询 FWI 公式 | formula_name | LaTeX 公式 + 说明 |
| `search_fwi_notes` | 搜索知识库 | query | 相关文档摘要 |

### 2.3 MCP 插件结构

**类比**: MCP 插件就像医院的检验设备

| 检验设备 | MCP 工具 |
|----------|----------|
| 血液分析仪 | list_models |
| CT 机 | inspect_model |
| X 光机 | formula_helper |

每个工具都是独立的，Agent 按需调用。

## 三、技术实现详解

### 3.1 MCP 插件接口

```cpp
// mcp_server_integrated/src/interface/PluginAPI.h

/**
 * @brief 工具定义
 */
struct PluginTool {
    const char* name;           // 工具名称
    const char* description;    // 工具描述
    const char* input_schema;   // 输入参数 JSON Schema
};

/**
 * @brief 插件接口
 */
struct PluginAPI {
    const char* (*GetName)();
    const char* (*GetVersion)();
    PluginType (*GetType)();
    int (*Initialize)();
    char* (*HandleRequest)(const char* req);  // 处理工具调用
    void (*Shutdown)();
    int (*GetToolCount)();
    const PluginTool* (*GetTool)(int index);
    // ...
};
```

### 3.2 工具定义

```cpp
static PluginTool methods[] = {
    {
        "list_models",
        "列出可用的 FWI 速度模型。返回模型 ID、名称、描述、维度等信息。",
        "{\"type\":\"object\",\"properties\":{},\"required\":[]}"
    },
    {
        "inspect_model",
        "查看指定速度模型的详细信息。输入模型 ID，返回完整的模型 metadata。",
        "{\"type\":\"object\",\"properties\":{\"model_id\":{\"type\":\"string\"}},\"required\":[\"model_id\"]}"
    },
    // ...
};
```

**JSON Schema 说明**:
- `type`: 参数类型（string, number, object 等）
- `properties`: 参数属性
- `required`: 必需参数

### 3.3 工具调用处理

```cpp
char* HandleRequestImpl(const char* req) {
    json response;
    response["content"] = json::array();
    response["isError"] = false;

    try {
        auto request = json::parse(req);
        std::string toolName = request["params"]["name"].get<std::string>();
        auto args = request["params"]["arguments"];

        std::string resultText;

        if (toolName == "list_models") {
            // 读取模型 metadata
            auto models_data = read_json_file(RESOURCE_DIR + "/fwi_models/model_metadata.json");

            if (models_data.contains("models")) {
                json result = json::array();
                for (const auto& model : models_data["models"]) {
                    json item;
                    item["id"] = model["id"];
                    item["name"] = model["name"];
                    item["description"] = model["description"];
                    result.push_back(item);
                }
                resultText = "可用速度模型:\n" + result.dump(2);
            }
        }
        else if (toolName == "inspect_model") {
            std::string model_id = args["model_id"].get<std::string>();
            // 查找并返回模型详情
            // ...
        }
        // ... 其他工具

        json content;
        content["type"] = "text";
        content["text"] = resultText;
        response["content"].push_back(content);

    } catch (const std::exception& e) {
        response["isError"] = true;
        // ...
    }

    return buffer;
}
```

### 3.4 构建配置

```cmake
# plugins/fwi-metadata/CMakeLists.txt

add_library(fwi-metadata SHARED
    ${PROJECT_SOURCE_DIR}/plugins/fwi-metadata/FWIMetadata.cpp
)

target_include_directories(fwi-metadata PRIVATE
    ${PROJECT_SOURCE_DIR}/include
    ${PROJECT_SOURCE_DIR}/src/interface
)
```

**共享库 (.so)**:
- 插件编译为共享库
- MCP Server 在运行时动态加载
- 无需重新编译 MCP Server

### 3.5 注册插件

```cmake
# mcp_server_integrated/CMakeLists.txt

# FWI Plugins
add_subdirectory(plugins/fwi-metadata)
```

## 四、工具详解

### 4.1 list_models

**功能**: 列出所有可用速度模型

**输入**: 无

**输出**:
```json
[
  {
    "id": "marmousi2",
    "name": "Marmousi-2 模型",
    "description": "经典 FWI 测试模型...",
    "dimensions": {"nx": 13601, "nz": 2801},
    "velocity_range": {"min": 1500, "max": 4500}
  }
]
```

### 4.2 inspect_model

**功能**: 查看指定模型详情

**输入**:
```json
{"model_id": "marmousi2"}
```

**输出**: 完整的模型 metadata JSON

### 4.3 formula_helper

**功能**: 查询 FWI 相关公式

**输入**:
```json
{"formula_name": "gradient"}
```

**输出**:
```
FWI 梯度（伴随状态法）
公式: $\nabla_m J = -\sum_s \int_0^T u(x,t) \cdot \frac{\partial^2 u^\dagger}{\partial t^2}(x,t) dt$
说明: 通过伴随状态法高效计算梯度
```

**内置公式**:
- `objective`: FWI 目标函数
- `gradient`: 梯度公式
- `adjoint`: 伴随方程
- `update`: 模型更新
- `cycle_skip`: Cycle skipping 判据
- `envelope`: 包络目标函数

### 4.4 search_fwi_notes

**功能**: 搜索本地知识库

**输入**:
```json
{"query": "cycle skipping"}
```

**输出**: 相关文档摘要列表

## 五、测试验证

### 5.1 启动系统

```bash
export ENABLE_MCP=true
export ROUTING_MODE=agent-rag
./examples/ai_orchestrator/start_system.sh
```

### 5.2 检查工具加载

```bash
grep "MCP 已启用" examples/ai_orchestrator/logs/orchestrator.log
```

**预期输出**:
```
MCP 已启用，可用工具: ... list_models inspect_model list_datasets inspect_dataset formula_helper search_fwi_notes
```

### 5.3 测试工具调用

通过 Orchestrator 调用工具（需要 Tool-RAG 支持，Phase 8 实现）。

## 六、技术原理总结

### 6.1 MCP 协议

**MCP (Model Context Protocol)** 是一种标准化的工具调用协议。

**核心概念**:
- **Tool**: 可调用的工具
- **tools/list**: 列出所有可用工具
- **tools/call**: 调用指定工具

**工具定义格式**:
```json
{
  "name": "list_models",
  "description": "列出可用速度模型",
  "inputSchema": {
    "type": "object",
    "properties": {},
    "required": []
  }
}
```

### 6.2 插件架构

**动态加载**: MCP Server 在运行时加载 `.so` 共享库

**优势**:
- 无需重新编译 MCP Server
- 插件可以独立更新
- 支持热插拔

### 6.3 JSON Schema

**用途**: 定义工具输入参数的格式

**示例**:
```json
{
  "type": "object",
  "properties": {
    "model_id": {
      "type": "string",
      "description": "模型 ID"
    }
  },
  "required": ["model_id"]
}
```

## 七、后续扩展

### 7.1 接入真实数据

```cpp
// 读取真实速度模型文件
if (toolName == "inspect_model") {
    std::string model_id = args["model_id"].get<std::string>();
    std::string model_path = RESOURCE_DIR + "/fwi_models/" + model_id + ".bin";
    
    // 读取二进制模型文件
    auto model_data = read_binary_file(model_path);
    
    // 返回模型统计信息
    result["min_velocity"] = min(model_data);
    result["max_velocity"] = max(model_data);
    result["dimensions"] = {nx, nz};
}
```

### 7.2 接入真实反演

```cpp
// 调用 FWI 计算程序
if (toolName == "run_fwi") {
    std::string model_id = args["model_id"].get<std::string>();
    std::string dataset_id = args["dataset_id"].get<std::string>();
    
    // 调用外部 FWI 程序
    std::string cmd = "./fwi_solver --model " + model_id + " --data " + dataset_id;
    std::string result = execute_command(cmd);
    
    resultText = "反演完成:\n" + result;
}
```

### 7.3 向量检索

```cpp
// 使用 Embedding 模型搜索知识库
if (toolName == "search_fwi_notes") {
    std::string query = args["query"].get<std::string>();
    
    // 向量化查询
    auto query_embedding = embedding_service.embed(query);
    
    // 在向量索引中搜索
    auto results = vector_index.search(query_embedding, 5);
    
    resultText = format_results(results);
}
```

## 八、文件结构

```
mcp_server_integrated/
├── plugins/
│   ├── calculator/          # 计算器插件
│   ├── weather/             # 天气插件
│   ├── fwi-metadata/        # FWI 元数据插件 (新增)
│   │   ├── CMakeLists.txt
│   │   └── FWIMetadata.cpp
│   └── ...
└── CMakeLists.txt           # 添加了 fwi-metadata

resources/
├── fwi_models/
│   └── model_metadata.json  # 模型 metadata
├── fwi_datasets/
│   └── dataset_metadata.json # 数据集 metadata
└── fwi_knowledge/
    └── *.md                 # 知识文件
```
