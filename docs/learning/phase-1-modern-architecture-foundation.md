# Phase 1: 现代 Agent 架构底座 — 学习文档

## 一、目标

引入 RequestContext、TraceLogger、OrchestratorConfig，为后续 Agent-RAG、Tool-RAG 打基础，同时不改变现有行为。

## 二、设计思路

### 2.1 为什么需要 RequestContext

**问题**: 原来的代码中，请求信息（用户输入、上下文ID等）是散落在各个函数的局部变量里。

```cpp
// 原来的代码 — 信息散落在局部变量中
std::string handle_request(const std::string& body) {
    // 解析请求
    std::string user_text = ...;  // 局部变量
    std::string context_id = ...; // 局部变量

    // 处理请求
    std::string intent = analyze_intent(user_text);
    if (intent == "math") {
        response_text = call_math_agent(user_text, context_id);
    }
    // ...
}
```

**痛点分析**:

| 痛点 | 具体问题 | 影响 |
|------|----------|------|
| **无请求追踪** | 没有 request_id，日志中无法区分不同请求 | 生产环境排查问题困难 |
| **信息传递困难** | 后续函数需要什么信息，就要加什么参数 | 函数签名越来越长 |
| **扩展性差** | 新增路由模式、工具调用模式时，要改很多函数 | 代码维护成本高 |
| **调试困难** | 无法追踪一个请求的完整处理链路 | 问题定位耗时 |

**解决方案**: 创建一个 `RequestContext` 结构体，作为整个请求处理流程的"通行证"。

**类比**: 就像医院的病历本
- 患者挂号时创建（请求开始时创建）
- 每个科室都会查看和记录（每个处理阶段都使用）
- 包含患者信息、诊断结果、用药记录（包含请求信息、路由决策、处理结果）

### 2.2 为什么需要 TraceLogger

**问题**: 原来的日志是 `std::cout << "[Orchestrator] ..."` 这样散落各处。

```cpp
// 原来的日志 — 散落各处，格式不统一
std::cout << "[Orchestrator] 收到消息: " << user_text << std::endl;
std::cout << "[Orchestrator] 识别意图: " << intent << std::endl;
std::cerr << "[Orchestrator] 错误: " << e.what() << std::endl;
```

**痛点分析**:

| 痛点 | 具体问题 | 影响 |
|------|----------|------|
| **无 request_id** | 无法追踪一个请求的完整链路 | 多请求并发时日志混乱 |
| **格式不统一** | 有的用 cout，有的用 cerr，格式各异 | 日志分析困难 |
| **无日志级别** | 无法过滤重要日志 | 调试时信息过多 |
| **非线程安全** | 多线程同时输出会混乱 | 生产环境可能出问题 |

**解决方案**: 创建一个 `TraceLogger` 类，统一日志输出格式。

**日志格式设计**:
```
[时间戳][级别][AgentID][req:请求ID][ctx:会话ID][标签] 消息
```

**示例**:
```
[2026-06-03 22:26:54.148][INFO ][orch-1][req:req-6558704837520-1][ctx:trace-ctx-2][REQ] 什么是 FWI
[2026-06-03 22:26:54.709][INFO ][orch-1][req:req-6558704837520-1][ctx:trace-ctx-2][INTENT] fwi
[2026-06-03 22:26:54.709][INFO ][orch-1][req:req-6558704837520-1][ctx:trace-ctx-2][ROUTE] → fwi-handler
[2026-06-03 22:27:32.258][INFO ][orch-1][req:req-6558704837520-1][ctx:trace-ctx-2][RESP] completed in 35420ms
```

**优势**:
- 可以用 `grep "req:req-6558704837520-1"` 过滤某个请求的所有日志
- 可以用 `grep "ERROR"` 过滤所有错误
- 可以用 `grep "orch-1"` 过滤某个 Agent 的日志

### 2.3 为什么需要 OrchestratorConfig

**问题**: 原来的配置（routing_mode、enable_mcp 等）散落在代码和启动脚本中。

```cpp
// 原来的配置 — 散落在代码中
if (intent == "math") {
    response_text = call_math_agent(user_text, context_id);
} else if (intent == "code") {
    response_text = call_code_agent(user_text, context_id);
} else if (intent == "fwi") {
    response_text = handle_fwi_query(user_text, context_id);
}
```

**痛点分析**:

| 痛点 | 具体问题 | 影响 |
|------|----------|------|
| **硬编码** | 路由逻辑写死在代码中 | 无法动态切换 |
| **配置分散** | 有的在代码，有的在脚本，有的在环境变量 | 管理混乱 |
| **无验证** | 配置值没有验证 | 错误配置导致运行时失败 |

**解决方案**: 创建一个 `OrchestratorConfig` 结构体，集中管理配置。

**支持的环境变量**:
```bash
export ROUTING_MODE=fixed        # 或 agent-rag
export TOOL_CALLING_MODE=rule    # 或 llm
export AGENT_API_TOKEN=xxx       # 访问令牌
```

## 三、技术实现详解

### 3.1 RequestContext 实现

```cpp
// orchestrator/include/agent_rpc/orchestrator/request_context.h

struct RequestContext {
    // === 核心字段 ===
    std::string request_id;          // 唯一请求 ID (UUID)
    std::string context_id;          // 会话 ID（同一用户的多轮对话）
    std::string task_id;             // 任务 ID
    std::string user_text;           // 用户输入

    // === 用户标识（多用户支持）===
    std::string user_id;             // 用户标识
    std::string client_id;           // 客户端标识

    // === 路由配置 ===
    std::string routing_mode;        // "fixed" | "agent-rag"
    std::string tool_calling_mode;   // "rule" | "llm"

    // === 扩展字段 ===
    json metadata;                   // 任意扩展数据

    // === 计时 ===
    std::chrono::steady_clock::time_point start_time;

    // === 工厂方法 ===
    static RequestContext create(const std::string& context_id = "default") {
        RequestContext ctx;
        ctx.context_id = context_id;
        ctx.task_id = context_id;
        ctx.request_id = generate_uuid();
        ctx.start_time = std::chrono::steady_clock::now();
        return ctx;
    }

    // === 计算耗时 ===
    int64_t elapsed_ms() const {
        auto now = std::chrono::steady_clock::now();
        return std::chrono::duration_cast<std::chrono::milliseconds>(
            now - start_time).count();
    }

    // === 序列化（用于日志）===
    json to_json() const {
        return {
            {"request_id", request_id},
            {"context_id", context_id},
            {"user_text", user_text.substr(0, 100)},  // 截断，避免日志过长
            {"elapsed_ms", elapsed_ms()}
        };
    }

private:
    // UUID 生成（简化版，生产环境建议用 uuid 库）
    static std::string generate_uuid() {
        static uint64_t counter = 0;
        auto now = std::chrono::steady_clock::now().time_since_epoch().count();
        return "req-" + std::to_string(now) + "-" + std::to_string(++counter);
    }
};
```

**技术要点详解**:

1. **UUID 生成**:
   - 使用时间戳 + 计数器，确保全局唯一
   - 格式：`req-{timestamp}-{counter}`
   - 生产环境建议使用 `uuid` 库

2. **RAII 计时**:
   - 在构造时记录开始时间
   - 随时可调用 `elapsed_ms()` 计算耗时
   - 不需要手动管理计时器

3. **工厂方法**:
   - `create()` 确保每个请求都有唯一 ID
   - 避免忘记初始化的错误

4. **JSON 序列化**:
   - 方便日志输出
   - 截断 user_text 避免日志过长

### 3.2 TraceLogger 实现

```cpp
// orchestrator/include/agent_rpc/orchestrator/trace_logger.h

class TraceLogger {
public:
    explicit TraceLogger(const std::string& agent_id, const std::string& log_file = "")
        : agent_id_(agent_id) {
        if (!log_file.empty()) {
            file_stream_.open(log_file, std::ios::app);  // 追加模式
        }
    }

    // === 请求日志 ===
    void log_request(const RequestContext& ctx, const std::string& message) {
        log(LogLevel::INFO, ctx, "REQ", message);
    }

    void log_routing(const RequestContext& ctx, const std::string& target_agent) {
        log(LogLevel::INFO, ctx, "ROUTE", "→ " + target_agent);
    }

    void log_tool_call(const RequestContext& ctx, const std::string& tool_name,
                       bool success, const std::string& detail = "") {
        std::string status = success ? "OK" : "FAIL";
        std::string msg = "tool=" + tool_name + " status=" + status;
        if (!detail.empty()) msg += " detail=" + detail;
        log(success ? LogLevel::INFO : LogLevel::WARN, ctx, "TOOL", msg);
    }

    void log_response(const RequestContext& ctx, int64_t duration_ms) {
        log(LogLevel::INFO, ctx, "RESP",
            "completed in " + std::to_string(duration_ms) + "ms");
    }

    void log_error(const RequestContext& ctx, const std::string& error) {
        log(LogLevel::ERROR, ctx, "ERROR", error);
    }

    // === 系统日志（无请求上下文）===
    void log_system(LogLevel level, const std::string& message) {
        std::lock_guard<std::mutex> lock(mutex_);
        std::string line = format_line(level, "SYSTEM", "-", "-", message);
        output(line);
    }

private:
    // 格式化日志行
    std::string format_line(LogLevel level, const std::string& tag,
                           const std::string& request_id,
                           const std::string& context_id,
                           const std::string& message) {
        auto now = std::chrono::system_clock::now();
        auto time_t = std::chrono::system_clock::to_time_t(now);
        auto ms = std::chrono::duration_cast<std::chrono::milliseconds>(
            now.time_since_epoch()) % 1000;

        std::ostringstream oss;
        oss << "[" << std::put_time(std::localtime(&time_t), "%Y-%m-%d %H:%M:%S")
            << "." << std::setfill('0') << std::setw(3) << ms.count() << "]"
            << "[" << level_str(level) << "]"
            << "[" << agent_id_ << "]"
            << "[req:" << request_id << "]"
            << "[ctx:" << context_id << "]"
            << "[" << tag << "] "
            << message;
        return oss.str();
    }

    // 线程安全输出
    void output(const std::string& line) {
        std::cout << line << std::endl;  // 输出到 stdout
        if (file_stream_.is_open()) {
            file_stream_ << line << std::endl;  // 输出到文件
            file_stream_.flush();
        }
    }

    std::string agent_id_;
    std::ofstream file_stream_;
    std::mutex mutex_;  // 线程安全
};
```

**技术要点详解**:

1. **线程安全**:
   - 使用 `std::mutex` 保护输出
   - `std::lock_guard` 自动加锁/解锁（RAII）
   - 避免多线程同时输出导致日志混乱

2. **双输出**:
   - 同时输出到 stdout（终端可见）
   - 同时输出到文件（持久化）
   - 文件使用追加模式，不会覆盖旧日志

3. **时间格式化**:
   - 使用 `std::put_time` 格式化时间
   - 精确到毫秒
   - 格式：`2026-06-03 22:26:54.148`

4. **日志级别**:
   - DEBUG: 调试信息
   - INFO: 正常信息
   - WARN: 警告
   - ERROR: 错误

### 3.3 OrchestratorConfig 实现

```cpp
// orchestrator/include/agent_rpc/orchestrator/config.h

enum class RoutingMode {
    FIXED,      // 传统 if-else 路由
    AGENT_RAG   // Agent-RAG 动态路由
};

enum class ToolCallingMode {
    RULE,       // 规则选择工具
    LLM         // LLM 选择工具
};

struct OrchestratorConfig {
    // 路由配置
    RoutingMode routing_mode = RoutingMode::FIXED;
    ToolCallingMode tool_calling_mode = ToolCallingMode::RULE;

    // 访问控制
    std::string api_token;
    std::vector<std::string> allowed_clients;

    // 从环境变量加载
    static OrchestratorConfig from_env() {
        OrchestratorConfig config;

        const char* routing_mode = std::getenv("ROUTING_MODE");
        if (routing_mode) {
            config.routing_mode = routing_mode_from_string(routing_mode);
        }

        const char* tool_calling_mode = std::getenv("TOOL_CALLING_MODE");
        if (tool_calling_mode) {
            config.tool_calling_mode = tool_calling_mode_from_string(tool_calling_mode);
        }

        const char* api_token = std::getenv("AGENT_API_TOKEN");
        if (api_token) {
            config.api_token = api_token;
        }

        return config;
    }
};
```

**技术要点详解**:

1. **枚举类型**:
   - 使用 `enum class` 替代字符串
   - 类型安全，避免拼写错误
   - 编译时检查

2. **环境变量**:
   - 通过 `std::getenv()` 读取
   - 便于部署时配置
   - 不需要重新编译

3. **默认值**:
   - 每个字段都有合理默认值
   - 不设置环境变量也能运行

## 四、集成到 Orchestrator

### 4.1 添加成员变量

```cpp
class AIOrchestrator {
    // 原有成员
    std::string agent_id_;
    std::shared_ptr<RedisTaskStore> task_store_;
    QwenClient qwen_client_;
    RegistryClient registry_client_;
    std::unique_ptr<MCPAgentIntegration> mcp_integration_;

    // 新增成员
    OrchestratorConfig orch_config_;  // 配置
    TraceLogger trace_logger_;        // 日志
};
```

### 4.2 修改构造函数

```cpp
AIOrchestrator(const std::string& agent_id,
               const std::string& listen_address,
               const std::string& registry_url,
               const std::string& api_key,
               const std::string& redis_host,
               int redis_port,
               const MCPAgentConfig& mcp_config = MCPAgentConfig(),
               const OrchestratorConfig& orch_config = OrchestratorConfig())
    : agent_id_(agent_id)
    , listen_address_(listen_address)
    , task_store_(std::make_shared<RedisTaskStore>(redis_host, redis_port))
    , qwen_client_(api_key)
    , registry_client_(registry_url)
    , mcp_integration_(std::make_unique<MCPAgentIntegration>())
    , orch_config_(orch_config)
    , trace_logger_(agent_id)  // 初始化 TraceLogger
{
    // 使用 TraceLogger 输出日志
    trace_logger_.log_system(LogLevel::INFO, "初始化完成 routing_mode=" +
                             to_string(orch_config_.routing_mode));
}
```

### 4.3 修改 handle_request()

```cpp
std::string handle_request(const std::string& body) {
    // 创建请求上下文
    RequestContext ctx = RequestContext::create();
    ctx.routing_mode = to_string(orch_config_.routing_mode);
    ctx.tool_calling_mode = to_string(orch_config_.tool_calling_mode);

    try {
        // ... 解析请求
        ctx.user_text = user_text;
        ctx.context_id = context_id;

        // 使用 TraceLogger 替换 std::cout
        trace_logger_.log_request(ctx, user_text);

        // 识别意图
        std::string intent = analyze_intent(user_text);
        trace_logger_.log_info(ctx, "INTENT", intent);

        // 路由
        if (intent == "math") {
            trace_logger_.log_routing(ctx, "math-agent");
            response_text = call_math_agent(user_text, context_id);
        } else if (intent == "fwi") {
            trace_logger_.log_routing(ctx, "fwi-handler");
            response_text = handle_fwi_query(user_text, context_id);
        } else {
            trace_logger_.log_routing(ctx, "general-handler");
            response_text = handle_general_query(user_text, context_id);
        }

        // 完成
        trace_logger_.log_response(ctx, ctx.elapsed_ms());

    } catch (const std::exception& e) {
        trace_logger_.log_error(ctx, e.what());
    }
}
```

## 五、技术原理总结

### 5.1 请求追踪 (Distributed Tracing)

**原理**: 为每个请求分配唯一 ID，在整个处理链路中传递，日志中包含此 ID。

**数据流**:
```
用户请求
  │
  ├─ request_id = "req-123"
  │
  ▼
Orchestrator
  │ log: [req:req-123] 收到请求
  │
  ├─ intent = "math"
  │ log: [req:req-123] 意图: math
  │
  ▼
MathAgent
  │ log: [req:req-123] 处理数学问题
  │
  ▼
响应
  log: [req:req-123] 完成 (1234ms)
```

**应用场景**:
- 微服务架构中追踪请求链路
- 排查生产环境问题
- 性能分析

**类似技术**:
- OpenTelemetry
- Jaeger
- Zipkin

### 5.2 结构化日志 (Structured Logging)

**原理**: 日志不是自由格式的文本，而是有固定字段的结构化数据。

**对比**:
```
// 自由格式（难以解析）
[Orchestrator] 收到消息: 什么是 FWI

// 结构化格式（易于解析）
[2026-06-03 22:26:54.148][INFO ][orch-1][req:req-123][ctx:ctx-1][REQ] 什么是 FWI
```

**优势**:
- 便于机器解析
- 便于日志聚合和查询
- 便于告警和监控

### 5.3 配置管理 (Configuration Management)

**原理**: 将配置从代码中分离，通过环境变量或配置文件管理。

**对比**:
```cpp
// 硬编码（不灵活）
if (intent == "math") { ... }

// 配置驱动（灵活）
if (orch_config_.routing_mode == RoutingMode::FIXED) {
    // 传统路由
} else {
    // Agent-RAG 路由
}
```

**优势**:
- 同一份代码，不同环境不同配置
- 不需要重新编译
- 安全（密钥不写入代码）

## 六、测试验证

### 编译
```bash
cd build && cmake .. && make -j$(nproc)
```

### 启动
```bash
export ROUTING_MODE=fixed
export TOOL_CALLING_MODE=rule
./examples/ai_orchestrator/start_system.sh
```

### 测试
```bash
curl -X POST http://localhost:5000/ -d '{
  "jsonrpc":"2.0","id":"test","method":"message/send",
  "params":{"message":{"role":"user","contextId":"ctx",
  "parts":[{"kind":"text","text":"你好"}]}}
}'
```

### 验证日志
```bash
grep "req:" examples/ai_orchestrator/logs/orchestrator.log
```

预期输出包含 `[req:req-xxx][ctx:ctx]` 格式的日志。
