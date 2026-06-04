# MCP 插件开发指南

## 概述

MCP (Model Context Protocol) 插件是扩展 AI Agent 能力的主要方式。本指南介绍如何开发自定义 MCP 插件。

## 插件架构

```
┌─────────────────────────────────────────────────────────────────┐
│                         MCP Server                              │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Plugin Manager                        │   │
│  │  ┌───────────┐  ┌───────────┐  ┌───────────┐           │   │
│  │  │ Plugin 1  │  │ Plugin 2  │  │ Plugin N  │           │   │
│  │  │ (*.so)    │  │ (*.so)    │  │ (*.so)    │           │   │
│  │  └───────────┘  └───────────┘  └───────────┘           │   │
│  └─────────────────────────────────────────────────────────┘   │
│                              │                                  │
│                              ▼                                  │
│  ┌─────────────────────────────────────────────────────────┐   │
│  │                    Plugin API                            │   │
│  │  - GetName()                                             │   │
│  │  - GetVersion()                                          │   │
│  │  - Initialize()                                          │   │
│  │  - HandleRequest()                                       │   │
│  │  - GetToolCount() / GetTool()                           │   │
│  │  - GetResourceCount() / GetResource()                   │   │
│  │  - Shutdown()                                            │   │
│  └─────────────────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────────────────┘
```

## 快速开始

### 1. 创建插件目录

```bash
cd mcp_server_integrated/plugins
mkdir my_plugin
cd my_plugin
```

### 2. 创建插件头文件

```cpp
// MyPlugin.h
#pragma once

#include "../../include/PluginAPI.h"

class MyPlugin {
public:
    static const char* GetName();
    static const char* GetVersion();
    static PluginType GetType();
    static int Initialize();
    static char* HandleRequest(const char* request);
    static void Shutdown();
    
    // 工具相关
    static int GetToolCount();
    static const PluginTool* GetTool(int index);
    
    // 资源相关 (可选)
    static int GetResourceCount();
    static const PluginResource* GetResource(int index);
};
```

### 3. 实现插件

```cpp
// MyPlugin.cpp
#include "MyPlugin.h"
#include <json/json.h>
#include <cstring>
#include <vector>

// 定义工具
static std::vector<PluginTool> g_tools;

const char* MyPlugin::GetName() {
    return "my-plugin";
}

const char* MyPlugin::GetVersion() {
    return "1.0.0";
}

PluginType MyPlugin::GetType() {
    return PluginType::TOOL;  // 或 RESOURCE, BOTH
}

int MyPlugin::Initialize() {
    // 注册工具
    PluginTool tool;
    tool.name = "my_tool";
    tool.description = "我的自定义工具";
    tool.inputSchema = R"({
        "type": "object",
        "properties": {
            "param1": {
                "type": "string",
                "description": "参数1"
            }
        },
        "required": ["param1"]
    })";
    g_tools.push_back(tool);
    
    return 0;  // 成功
}

char* MyPlugin::HandleRequest(const char* request) {
    Json::Value root;
    Json::Reader reader;
    
    if (!reader.parse(request, root)) {
        return strdup(R"({"error": "Invalid JSON"})");
    }
    
    std::string method = root["method"].asString();
    
    if (method == "tools/call") {
        std::string tool_name = root["params"]["name"].asString();
        Json::Value arguments = root["params"]["arguments"];
        
        if (tool_name == "my_tool") {
            std::string param1 = arguments["param1"].asString();
            
            // 处理逻辑
            Json::Value result;
            result["content"][0]["type"] = "text";
            result["content"][0]["text"] = "处理结果: " + param1;
            
            Json::FastWriter writer;
            return strdup(writer.write(result).c_str());
        }
    }
    
    return strdup(R"({"error": "Unknown method"})");
}

void MyPlugin::Shutdown() {
    g_tools.clear();
}

int MyPlugin::GetToolCount() {
    return static_cast<int>(g_tools.size());
}

const PluginTool* MyPlugin::GetTool(int index) {
    if (index >= 0 && index < static_cast<int>(g_tools.size())) {
        return &g_tools[index];
    }
    return nullptr;
}

int MyPlugin::GetResourceCount() {
    return 0;
}

const PluginResource* MyPlugin::GetResource(int index) {
    return nullptr;
}

// 导出函数
extern "C" {
    PLUGIN_API PluginAPI* CreatePlugin() {
        static PluginAPI api;
        api.GetName = MyPlugin::GetName;
        api.GetVersion = MyPlugin::GetVersion;
        api.GetType = MyPlugin::GetType;
        api.Initialize = MyPlugin::Initialize;
        api.HandleRequest = MyPlugin::HandleRequest;
        api.Shutdown = MyPlugin::Shutdown;
        api.GetToolCount = MyPlugin::GetToolCount;
        api.GetTool = MyPlugin::GetTool;
        api.GetResourceCount = MyPlugin::GetResourceCount;
        api.GetResource = MyPlugin::GetResource;
        return &api;
    }
    
    PLUGIN_API void DestroyPlugin(PluginAPI* api) {
        // 清理资源
    }
}
```

### 4. 创建 CMakeLists.txt

```cmake
# CMakeLists.txt
cmake_minimum_required(VERSION 3.10)
project(my_plugin)

set(CMAKE_CXX_STANDARD 17)
set(CMAKE_POSITION_INDEPENDENT_CODE ON)

find_package(PkgConfig REQUIRED)
pkg_check_modules(JSONCPP REQUIRED jsoncpp)

add_library(my_plugin SHARED
    MyPlugin.cpp
)

target_include_directories(my_plugin PRIVATE
    ${JSONCPP_INCLUDE_DIRS}
    ${CMAKE_SOURCE_DIR}/include
)

target_link_libraries(my_plugin PRIVATE
    ${JSONCPP_LIBRARIES}
)

set_target_properties(my_plugin PROPERTIES
    PREFIX ""
    OUTPUT_NAME "my_plugin"
)
```

### 5. 编译插件

```bash
mkdir build && cd build
cmake ..
make
```

### 6. 部署插件

```bash
# 复制到 plugins 目录
cp libmy_plugin.so ../../build/plugins/
```

## 插件 API 详解

### PluginAPI 结构

```cpp
struct PluginAPI {
    // 基本信息
    const char* (*GetName)();
    const char* (*GetVersion)();
    PluginType (*GetType)();
    
    // 生命周期
    int (*Initialize)();
    void (*Shutdown)();
    
    // 请求处理
    char* (*HandleRequest)(const char* request);
    
    // 工具相关
    int (*GetToolCount)();
    const PluginTool* (*GetTool)(int index);
    
    // 资源相关
    int (*GetResourceCount)();
    const PluginResource* (*GetResource)(int index);
};
```

### PluginTool 结构

```cpp
struct PluginTool {
    const char* name;           // 工具名称
    const char* description;    // 工具描述
    const char* inputSchema;    // JSON Schema 格式的输入参数定义
};
```

### PluginResource 结构

```cpp
struct PluginResource {
    const char* uri;            // 资源 URI
    const char* name;           // 资源名称
    const char* description;    // 资源描述
    const char* mimeType;       // MIME 类型
};
```

### PluginType 枚举

```cpp
enum class PluginType {
    TOOL,       // 只提供工具
    RESOURCE,   // 只提供资源
    BOTH        // 同时提供工具和资源
};
```

## 请求/响应格式

### 工具调用请求

```json
{
    "jsonrpc": "2.0",
    "method": "tools/call",
    "params": {
        "name": "my_tool",
        "arguments": {
            "param1": "value1"
        }
    },
    "id": "request-id"
}
```

### 工具调用响应

```json
{
    "content": [
        {
            "type": "text",
            "text": "处理结果"
        }
    ]
}
```

### 资源读取请求

```json
{
    "jsonrpc": "2.0",
    "method": "resources/read",
    "params": {
        "uri": "my-plugin:///resource"
    },
    "id": "request-id"
}
```

### 资源读取响应

```json
{
    "contents": [
        {
            "uri": "my-plugin:///resource",
            "mimeType": "text/plain",
            "text": "资源内容"
        }
    ]
}
```

## 示例插件

### Calculator 插件

提供数学计算功能。

```cpp
// 工具定义
PluginTool calculator_tool = {
    "calculator",
    "计算数学表达式",
    R"({
        "type": "object",
        "properties": {
            "expression": {
                "type": "string",
                "description": "数学表达式，如 2+3*4"
            }
        },
        "required": ["expression"]
    })"
};

// 处理逻辑
if (tool_name == "calculator") {
    std::string expr = arguments["expression"].asString();
    double result = evaluateExpression(expr);
    
    Json::Value response;
    response["content"][0]["type"] = "text";
    response["content"][0]["text"] = expr + " = " + std::to_string(result);
    return response;
}
```

### Weather 插件

提供天气查询功能。

```cpp
// 工具定义
PluginTool weather_tool = {
    "get_weather",
    "查询城市天气",
    R"({
        "type": "object",
        "properties": {
            "city": {
                "type": "string",
                "description": "城市名称"
            }
        },
        "required": ["city"]
    })"
};

// 处理逻辑
if (tool_name == "get_weather") {
    std::string city = arguments["city"].asString();
    std::string weather = fetchWeather(city);  // 调用天气 API
    
    Json::Value response;
    response["content"][0]["type"] = "text";
    response["content"][0]["text"] = city + " 天气: " + weather;
    return response;
}
```

### Bacio-Quote 插件 (资源类型)

提供意大利名言资源。

```cpp
// 资源定义
PluginResource quote_resource = {
    "bacio:///quote",
    "Bacio Quote",
    "意大利 Bacio Perugina 名言",
    "text/plain"
};

// 处理逻辑
if (method == "resources/read") {
    std::string uri = params["uri"].asString();
    
    if (uri == "bacio:///quote") {
        std::string quote = getRandomQuote();
        
        Json::Value response;
        response["contents"][0]["uri"] = uri;
        response["contents"][0]["mimeType"] = "text/plain";
        response["contents"][0]["text"] = quote;
        return response;
    }
}
```

## 错误处理

### 返回错误

```cpp
char* HandleRequest(const char* request) {
    // 参数验证失败
    if (!validateParams(params)) {
        Json::Value error;
        error["error"]["code"] = -32602;
        error["error"]["message"] = "Invalid params";
        return strdup(Json::FastWriter().write(error).c_str());
    }
    
    // 内部错误
    try {
        // 处理逻辑
    } catch (const std::exception& e) {
        Json::Value error;
        error["error"]["code"] = -32603;
        error["error"]["message"] = e.what();
        return strdup(Json::FastWriter().write(error).c_str());
    }
}
```

### 错误码

| 错误码 | 描述 |
|--------|------|
| -32700 | Parse error |
| -32600 | Invalid Request |
| -32601 | Method not found |
| -32602 | Invalid params |
| -32603 | Internal error |

## 最佳实践

### 1. 内存管理

```cpp
// 使用 strdup 分配返回字符串
char* HandleRequest(const char* request) {
    std::string result = processRequest(request);
    return strdup(result.c_str());  // 调用方负责释放
}
```

### 2. 线程安全

```cpp
#include <mutex>

static std::mutex g_mutex;

char* HandleRequest(const char* request) {
    std::lock_guard<std::mutex> lock(g_mutex);
    // 处理逻辑
}
```

### 3. 资源清理

```cpp
void Shutdown() {
    // 关闭连接
    closeConnections();
    
    // 释放内存
    g_tools.clear();
    g_resources.clear();
    
    // 清理缓存
    clearCache();
}
```

### 4. 日志记录

```cpp
#include <iostream>
#include <ctime>

void log(const std::string& level, const std::string& message) {
    time_t now = time(nullptr);
    char timestamp[64];
    strftime(timestamp, sizeof(timestamp), "%Y-%m-%d %H:%M:%S", localtime(&now));
    std::cerr << "[" << timestamp << "] [" << level << "] " << message << std::endl;
}

int Initialize() {
    log("INFO", "Plugin initializing...");
    // ...
    log("INFO", "Plugin initialized successfully");
    return 0;
}
```

### 5. 输入验证

```cpp
bool validateExpression(const std::string& expr) {
    // 检查长度
    if (expr.empty() || expr.length() > 1000) {
        return false;
    }
    
    // 检查字符
    for (char c : expr) {
        if (!isdigit(c) && !strchr("+-*/().^ ", c)) {
            return false;
        }
    }
    
    return true;
}
```

## 调试技巧

### 1. 使用测试客户端

```bash
cd mcp_server_integrated/test
python3 test-client-stdio.py configuration-stdio-linux.json
```

### 2. 查看日志

```bash
tail -f /tmp/mcp_logs/mcp-server_*.log
```

### 3. 单独测试插件

```cpp
// test_my_plugin.cpp
#include "MyPlugin.h"
#include <iostream>

int main() {
    MyPlugin::Initialize();
    
    const char* request = R"({
        "jsonrpc": "2.0",
        "method": "tools/call",
        "params": {
            "name": "my_tool",
            "arguments": {"param1": "test"}
        },
        "id": "1"
    })";
    
    char* response = MyPlugin::HandleRequest(request);
    std::cout << response << std::endl;
    free(response);
    
    MyPlugin::Shutdown();
    return 0;
}
```

## 发布清单

- [ ] 插件名称唯一
- [ ] 版本号正确
- [ ] 所有工具有描述
- [ ] 输入参数有 JSON Schema
- [ ] 错误处理完善
- [ ] 内存无泄漏
- [ ] 线程安全
- [ ] 文档完整
- [ ] 测试通过
