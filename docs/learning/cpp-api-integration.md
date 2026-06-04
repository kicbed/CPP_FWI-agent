# C++ API 接入详解 — 学习文档

## 一、概述

本文档详细解释 FWI Agent 平台如何用 C++ 调用 LLM API（DeepSeek/Qwen/OpenAI）。

## 二、核心文件

| 文件 | 作用 |
|------|------|
| `a2a/include/a2a/examples/llm_client.hpp` | 通用 LLM 客户端 |
| `a2a/include/a2a/examples/qwen_client.hpp` | 通义千问专用客户端（旧版） |
| `orchestrator/include/agent_rpc/orchestrator/config.h` | 配置管理 |

## 三、LLMClient 实现详解

### 3.1 类设计

```cpp
class LLMClient {
public:
    // 构造函数：指定 API Key、提供商、模型、URL
    explicit LLMClient(const std::string& api_key,
                       LLMProvider provider = LLMProvider::DEEPSEEK,
                       const std::string& model = "",
                       const std::string& api_url = "");

    // 核心方法：发送对话请求
    std::string chat(const std::string& system_prompt,
                    const std::string& user_message);

private:
    // 发送 HTTP 请求
    std::string send_request(const std::string& body,
                           const std::string& auth_header);

    // 解析响应
    std::string parse_response(const std::string& response);

    std::string api_key_;
    LLMProvider provider_;
    std::string model_;
    std::string api_url_;
};
```

### 3.2 支持的提供商

```cpp
enum class LLMProvider {
    DEEPSEEK,   // DeepSeek (OpenAI 兼容格式)
    QWEN,       // 通义千问 (DashScope 格式)
    OPENAI,     // OpenAI
    LOCAL       // 本地模型 (Ollama)
};
```

### 3.3 构造函数实现

```cpp
LLMClient(const std::string& api_key,
          LLMProvider provider,
          const std::string& model,
          const std::string& api_url)
    : api_key_(api_key), provider_(provider) {

    // 根据提供商设置默认值
    switch (provider) {
        case LLMProvider::DEEPSEEK:
            model_ = model.empty() ? "deepseek-chat" : model;
            api_url_ = api_url.empty()
                ? "https://api.deepseek.com/v1/chat/completions"
                : api_url;
            break;
        case LLMProvider::QWEN:
            model_ = model.empty() ? "qwen-plus" : model;
            api_url_ = api_url.empty()
                ? "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation"
                : api_url;
            break;
        // ...
    }
}
```

**设计要点**:
- 提供商有默认值，不需要手动指定
- API URL 有默认值，可以覆盖
- 模型有默认值，可以覆盖

### 3.4 chat() 方法实现

```cpp
std::string chat(const std::string& system_prompt,
                const std::string& user_message) {
    // 1. 根据提供商构造请求体
    json request_body;
    std::string auth_header;

    switch (provider_) {
        case LLMProvider::DEEPSEEK:
        case LLMProvider::OPENAI:
        case LLMProvider::LOCAL:
            // OpenAI 兼容格式
            request_body = {
                {"model", model_},
                {"messages", json::array({
                    {{"role", "system"}, {"content", system_prompt}},
                    {{"role", "user"}, {"content", user_message}}
                })},
                {"max_tokens", 2000},
                {"temperature", 0.7}
            };
            auth_header = "Authorization: Bearer " + api_key_;
            break;

        case LLMProvider::QWEN:
            // 通义千问格式（不同！）
            request_body = {
                {"model", model_},
                {"input", {
                    {"messages", json::array({
                        {{"role", "system"}, {"content", system_prompt}},
                        {{"role", "user"}, {"content", user_message}}
                    })}
                }},
                {"parameters", {{"result_format", "message"}}}
            };
            auth_header = "Authorization: Bearer " + api_key_;
            break;
    }

    // 2. 发送 HTTP 请求
    std::string response = send_request(request_body.dump(), auth_header);

    // 3. 解析响应
    return parse_response(response);
}
```

**关键区别**:
- **OpenAI/DeepSeek**: `{"messages": [...]}`
- **通义千问**: `{"input": {"messages": [...]}}`

### 3.5 HTTP 请求实现

```cpp
std::string send_request(const std::string& body,
                        const std::string& auth_header) {
    // 初始化 CURL
    CURL* curl = curl_easy_init();

    // 设置响应缓冲区
    std::string response;

    // 设置请求头
    struct curl_slist* headers = nullptr;
    headers = curl_slist_append(headers, "Content-Type: application/json");
    headers = curl_slist_append(headers, auth_header.c_str());

    // 配置 CURL
    curl_easy_setopt(curl, CURLOPT_URL, api_url_.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_callback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 120L);  // 超时 120 秒

    // 执行请求
    CURLcode res = curl_easy_perform(curl);

    // 清理
    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);

    if (res != CURLE_OK) {
        throw std::runtime_error(
            std::string("CURL error: ") + curl_easy_strerror(res));
    }

    return response;
}
```

**CURL 使用要点**:
- `CURLOPT_URL`: 请求 URL
- `CURLOPT_POSTFIELDS`: POST 请求体
- `CURLOPT_HTTPHEADER`: 请求头
- `CURLOPT_WRITEFUNCTION`: 响应回调函数
- `CURLOPT_TIMEOUT`: 超时时间

### 3.6 响应解析

```cpp
std::string parse_response(const std::string& response) {
    auto response_json = json::parse(response);

    // 检查错误
    if (response_json.contains("error")) {
        std::string error_msg = response_json["error"]
            .value("message", "Unknown error");
        throw std::runtime_error("API Error: " + error_msg);
    }

    // OpenAI 格式 (DeepSeek, OpenAI, Local)
    if (provider_ == LLMProvider::DEEPSEEK ||
        provider_ == LLMProvider::OPENAI ||
        provider_ == LLMProvider::LOCAL) {
        // {"choices": [{"message": {"content": "..."}}]}
        if (response_json.contains("choices") &&
            !response_json["choices"].empty()) {
            auto& choice = response_json["choices"][0];
            if (choice.contains("message") &&
                choice["message"].contains("content")) {
                return choice["message"]["content"].get<std::string>();
            }
        }
    }

    // 通义千问格式
    if (provider_ == LLMProvider::QWEN) {
        // {"output": {"choices": [{"message": {"content": "..."}}]}}
        if (response_json.contains("output") &&
            response_json["output"].contains("choices") &&
            !response_json["output"]["choices"].empty()) {
            auto& choice = response_json["output"]["choices"][0];
            if (choice.contains("message") &&
                choice["message"].contains("content")) {
                return choice["message"]["content"].get<std::string>();
            }
        }
    }

    throw std::runtime_error("Invalid response format");
}
```

**响应格式区别**:

**OpenAI/DeepSeek**:
```json
{
  "choices": [
    {
      "message": {
        "content": "AI 的回复"
      }
    }
  ]
}
```

**通义千问**:
```json
{
  "output": {
    "choices": [
      {
        "message": {
          "content": "AI 的回复"
        }
      }
    ]
  }
}
```

## 四、配置管理

### 4.1 OrchestratorConfig

```cpp
struct OrchestratorConfig {
    // LLM 配置
    LLMProvider llm_provider = LLMProvider::DEEPSEEK;
    std::string llm_model;
    std::string llm_api_url;

    // 从环境变量加载
    static OrchestratorConfig from_env() {
        OrchestratorConfig config;

        const char* llm_provider = std::getenv("LLM_PROVIDER");
        if (llm_provider) {
            std::string provider_str = llm_provider;
            if (provider_str == "deepseek")
                config.llm_provider = LLMProvider::DEEPSEEK;
            else if (provider_str == "qwen")
                config.llm_provider = LLMProvider::QWEN;
            else if (provider_str == "openai")
                config.llm_provider = LLMProvider::OPENAI;
            else if (provider_str == "local")
                config.llm_provider = LLMProvider::LOCAL;
        }

        return config;
    }
};
```

### 4.2 环境变量

```bash
# .env 文件
LLM_PROVIDER=deepseek          # 提供商
DEEPSEEK_API_KEY=sk-xxx        # API Key
LLM_MODEL=deepseek-chat        # 模型（可选）
LLM_API_URL=                   # 自定义 URL（可选）
```

## 五、调用流程

```
用户输入 "什么是 FWI"
    │
    ▼
Orchestrator.handle_request()
    │
    ▼
LLMClient.chat(system_prompt, user_text)
    │
    ├─ 构造请求体 (OpenAI 格式)
    │  {
    │    "model": "deepseek-chat",
    │    "messages": [
    │      {"role": "system", "content": "你是..."},
    │      {"role": "user", "content": "什么是 FWI"}
    │    ]
    │  }
    │
    ├─ 设置请求头
    │  Authorization: Bearer sk-xxx
    │
    ├─ CURL POST 请求
    │  → https://api.deepseek.com/v1/chat/completions
    │
    ├─ 接收响应
    │  {"choices": [{"message": {"content": "FWI 是..."}}]}
    │
    ├─ 解析响应
    │  return "FWI 是..."
    │
    ▼
返回给用户
```

## 六、错误处理

### 6.1 API Key 无效

```cpp
if (response_json.contains("error")) {
    std::string error_msg = response_json["error"]
        .value("message", "Unknown error");
    throw std::runtime_error("API Error: " + error_msg);
}
```

**错误信息**: `API Error: Invalid API-key provided.`

### 6.2 网络超时

```cpp
curl_easy_setopt(curl, CURLOPT_TIMEOUT, 120L);  // 120 秒超时

if (res != CURLE_OK) {
    throw std::runtime_error(
        std::string("CURL error: ") + curl_easy_strerror(res));
}
```

### 6.3 响应格式错误

```cpp
// 如果解析失败
throw std::runtime_error("Invalid response format");
```

## 七、扩展新提供商

添加新的 LLM 提供商只需 3 步：

### 步骤 1: 添加枚举值

```cpp
enum class LLMProvider {
    DEEPSEEK,
    QWEN,
    OPENAI,
    LOCAL,
    NEW_PROVIDER  // 新增
};
```

### 步骤 2: 设置默认值

```cpp
case LLMProvider::NEW_PROVIDER:
    model_ = model.empty() ? "default-model" : model;
    api_url_ = api_url.empty()
        ? "https://api.new-provider.com/v1/chat"
        : api_url;
    break;
```

### 步骤 3: 处理响应格式

```cpp
// 如果格式与 OpenAI 不同
case LLMProvider::NEW_PROVIDER:
    request_body = {
        {"model", model_},
        {"prompt", user_message},  // 不同的字段名
        // ...
    };
    break;
```

## 八、学习要点总结

| 要点 | 说明 |
|------|------|
| **CURL** | C++ 中最常用的 HTTP 客户端库 |
| **JSON** | 使用 nlohmann/json 处理请求和响应 |
| **枚举类** | 使用 `enum class` 替代字符串，类型安全 |
| **策略模式** | 根据提供商选择不同的请求/响应格式 |
| **错误处理** | 使用异常 (`throw std::runtime_error`) |
| **RAII** | CURL 资源在析构时自动清理 |
| **环境变量** | 通过 `std::getenv()` 读取配置 |

## 九、常见问题

### Q: 为什么 DeepSeek 说"无法提供 FWI 相关信息"？

**原因**: DeepSeek 是通用 LLM，没有专门训练 FWI 领域知识。

**解决方案**:
1. 在 system prompt 中提供 FWI 背景知识
2. 使用 RAG 检索本地知识库
3. 接入专业的 FWI 知识库

### Q: 如何切换 LLM 提供商？

只需修改 `.env` 文件：
```bash
LLM_PROVIDER=deepseek  # 或 qwen、openai、local
```

### Q: 如何添加自定义 API URL？

```bash
LLM_API_URL=https://your-custom-api.com/v1/chat
```
