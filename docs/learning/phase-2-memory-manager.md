# Phase 2: MemoryManager 记忆管理 — 学习文档

## 一、目标

梳理 Redis 存储结构，实现 Session Memory / Agent Memory / Task State 三层记忆分离。

## 二、设计思路

### 2.1 问题分析

**原来的问题**:

```cpp
// 原来的代码 — 所有 Agent 共用同一个 history key
void save_message(const std::string& context_id, const AgentMessage& message) {
    task_store_->add_history_message(context_id, message);
    // 写入 a2a:history:{context_id}
}
```

**数据流**:
```
用户问 Orchestrator: "什么是 FWI"
    │
    ▼
Orchestrator 识别意图为 "fwi"
    │
    ├─ 写入 a2a:history:ctx-123 ← 用户消息
    │
    ▼
Orchestrator 直接处理（用 FWI prompt）
    │
    ├─ 写入 a2a:history:ctx-123 ← Agent 响应
    │
    ▼
用户问 Orchestrator: "计算 1+1"
    │
    ▼
Orchestrator 识别意图为 "math"
    │
    ├─ 转发给 MathAgent
    │
    ▼
MathAgent 处理
    │
    ├─ 写入 a2a:history:ctx-123 ← MathAgent 的处理过程
    │
    ▼
问题：MathAgent 的处理过程和用户的对话混在一起！
```

**痛点分析**:

| 痛点 | 具体问题 | 影响 |
|------|----------|------|
| **记忆混杂** | 用户对话和 Agent 处理过程混在同一个 key | 无法区分哪些是用户可见的 |
| **无法追踪** | 不知道每个 Agent 做了什么 | 调试困难 |
| **扩展困难** | 后续多 Agent 协作时，无法追踪每个 Agent | 无法做 Agent Memory |

### 2.2 解决方案：三层记忆

**设计思路**: 将记忆分为三层，各司其职。

| 层级 | Redis Key | 用途 | 谁写入 | 谁读取 | 类比 |
|------|-----------|------|--------|--------|------|
| **Session Memory** | `a2a:session:{context_id}` | 用户可见的对话 | Orchestrator | 所有 Agent | 聊天记录 |
| **Agent Memory** | `a2a:agent:{agent_id}:{context_id}` | Agent 内部处理 | 各 Agent 自己 | 各 Agent 自己 | 工作笔记 |
| **Task State** | `a2a:task:{task_id}` | 任务生命周期 | Orchestrator | Orchestrator | 任务单 |

**类比理解**:

想象一个医院的场景：
- **Session Memory** = 病历本（患者能看到的内容）
- **Agent Memory** = 医生的工作笔记（患者看不到）
- **Task State** = 挂号单（任务状态）

### 2.3 为什么需要 Agent Memory

**场景**: 用户问 "计算 1+1"

**Session Memory** 只记录用户可见的内容:
```
用户: 计算 1+1
Agent: 1 + 1 = 2
```

**Agent Memory** 记录 Agent 的完整处理过程:
```json
[
  {"step": "intent", "value": "math", "timestamp": "..."},
  {"step": "route", "target": "math-agent", "timestamp": "..."},
  {"step": "tool_call", "tool": "calculator", "args": {"expression": "1+1"}, "success": true, "result": "2"},
  {"step": "llm_call", "prompt": "...", "response": "1 + 1 = 2"},
  {"step": "complete", "duration_ms": 1234}
]
```

**Agent Memory 的价值**:
- **调试**: 看到 Agent 的完整处理过程
- **优化**: 分析哪些步骤耗时最长
- **审计**: 追踪 Agent 做了什么决策

## 三、技术实现详解

### 3.1 MemoryManager 类设计

```cpp
// orchestrator/include/agent_rpc/orchestrator/memory_manager.h

class MemoryManager {
public:
    /**
     * @brief 构造函数
     * @param redis_host Redis 主机
     * @param redis_port Redis 端口
     *
     * 直接使用 hiredis 连接 Redis，不依赖 RedisTaskStore。
     * 这样可以独立管理记忆，不影响原有的任务状态存储。
     */
    explicit MemoryManager(const std::string& redis_host, int redis_port);

    // ========================================================================
    // Session Memory（用户可见的对话）
    // ========================================================================

    /**
     * @brief 保存消息到会话记忆
     * @param context_id 会话 ID
     * @param message 消息
     *
     * 使用 Redis List 存储，RPUSH 追加，LTRIM 限制长度。
     * Key: a2a:session:{context_id}
     */
    void save_session_message(const std::string& context_id,
                             const a2a::AgentMessage& message);

    /**
     * @brief 获取会话历史
     * @param context_id 会话 ID
     * @param limit 最大消息数（0 = 全部）
     * @return 消息列表（按时间顺序）
     *
     * 使用 LRANGE 获取，支持获取最近 N 条。
     */
    std::vector<a2a::AgentMessage> get_session_history(const std::string& context_id,
                                                       int limit = 0);

    // ========================================================================
    // Agent Memory（Agent 内部处理）
    // ========================================================================

    /**
     * @brief 保存 Agent 处理步骤
     * @param agent_id Agent 标识（如 "orch-1", "math-1"）
     * @param context_id 会话 ID
     * @param step 处理步骤（JSON 格式）
     *
     * Key: a2a:agent:{agent_id}:{context_id}
     */
    void save_agent_memory(const std::string& agent_id,
                          const std::string& context_id,
                          const json& step);

    /**
     * @brief 获取 Agent 处理历史
     * @param agent_id Agent 标识
     * @param context_id 会话 ID
     * @param limit 最大步骤数（0 = 全部）
     * @return 处理步骤列表
     */
    std::vector<json> get_agent_memory(const std::string& agent_id,
                                       const std::string& context_id,
                                       int limit = 0);

    // ========================================================================
    // Task State（任务状态）
    // ========================================================================

    /**
     * @brief 保存任务状态
     * @param task_id 任务 ID
     * @param state 任务状态（JSON 格式）
     *
     * Key: a2a:task:{task_id}
     * 使用 SET 命令，覆盖写入。
     */
    void save_task_state(const std::string& task_id, const json& state);

    /**
     * @brief 获取任务状态
     * @param task_id 任务 ID
     * @return 任务状态（JSON 格式）
     */
    json get_task_state(const std::string& task_id);

    // ========================================================================
    // Legacy 兼容
    // ========================================================================

    /**
     * @brief 获取旧格式历史
     * @param context_id 会话 ID
     * @param limit 最大消息数
     * @return 消息列表
     *
     * 读取 a2a:history:{context_id}，用于向后兼容。
     */
    std::vector<a2a::AgentMessage> get_legacy_history(const std::string& context_id,
                                                      int limit = 0);

    /**
     * @brief 迁移旧格式 key
     * @param context_id 会话 ID
     *
     * 将 a2a:history:{context_id} 的数据复制到 a2a:session:{context_id}。
     */
    void migrate_legacy_keys(const std::string& context_id);

private:
    // Redis key 构建器
    std::string session_key(const std::string& context_id) const {
        return "a2a:session:" + context_id;
    }

    std::string agent_key(const std::string& agent_id,
                         const std::string& context_id) const {
        return "a2a:agent:" + agent_id + ":" + context_id;
    }

    std::string task_key(const std::string& task_id) const {
        return "a2a:task:" + task_id;
    }

    std::string legacy_history_key(const std::string& context_id) const {
        return "a2a:history:" + context_id;
    }

    // Redis 操作
    redisReply* execute_command(const char* format, ...);
    void ensure_connection();

    // 连接
    redisContext* context_;
    std::string host_;
    int port_;
    std::mutex mutex_;  // 线程安全
};
```

### 3.2 Redis 操作详解

#### 3.2.1 保存消息（RPUSH + LTRIM）

```cpp
void MemoryManager::save_session_message(const std::string& context_id,
                                         const a2a::AgentMessage& message) {
    std::string json_str = message.to_json();

    // RPUSH: 在列表末尾追加元素
    // 时间复杂度: O(1)
    auto reply = execute_command("RPUSH %s %s",
                                session_key(context_id).c_str(),
                                json_str.c_str());
    freeReplyObject(reply);

    // LTRIM: 修剪列表，只保留最后 1000 个元素
    // 时间复杂度: O(N)，N 为被删除的元素数
    reply = execute_command("LTRIM %s -1000 -1",
                           session_key(context_id).c_str());
    freeReplyObject(reply);
}
```

**Redis List 操作图解**:
```
初始状态: [msg1, msg2, msg3]

RPUSH msg4: [msg1, msg2, msg3, msg4]

LTRIM -3 -1: [msg2, msg3, msg4]  (只保留最后 3 个)
```

**为什么用 List**:
- 消息是有序的（时间顺序）
- 需要快速追加新消息（RPUSH O(1)）
- 需要获取最近 N 条消息（LRANGE O(N)）
- LTRIM 可以自动限制长度

#### 3.2.2 获取历史（LRANGE）

```cpp
std::vector<a2a::AgentMessage> MemoryManager::get_session_history(
    const std::string& context_id, int limit) {

    redisReply* reply;

    if (limit <= 0) {
        // 获取所有元素
        // LRANGE key 0 -1 表示从 0 到最后一个
        reply = execute_command("LRANGE %s 0 -1",
                               session_key(context_id).c_str());
    } else {
        // 获取最后 N 个元素
        // LRANGE key -N -1 表示从倒数第 N 个到最后一个
        reply = execute_command("LRANGE %s -%d -1",
                               session_key(context_id).c_str(),
                               limit);
    }

    // 解析结果
    std::vector<a2a::AgentMessage> history;
    if (reply->type == REDIS_REPLY_ARRAY) {
        for (size_t i = 0; i < reply->elements; ++i) {
            if (reply->element[i]->type == REDIS_REPLY_STRING) {
                std::string json_str(reply->element[i]->str, reply->element[i]->len);
                history.push_back(a2a::AgentMessage::from_json(json_str));
            }
        }
    }

    freeReplyObject(reply);
    return history;
}
```

**LRANGE 参数详解**:
```
LRANGE key start stop

start = 0, stop = -1  → 获取所有元素
start = -5, stop = -1 → 获取最后 5 个元素
start = 0, stop = 4   → 获取前 5 个元素
```

#### 3.2.3 线程安全

```cpp
redisReply* MemoryManager::execute_command(const char* format, ...) {
    // std::lock_guard: RAII 风格的锁管理
    // 构造时加锁，析构时自动解锁
    std::lock_guard<std::mutex> lock(mutex_);

    // 确保连接有效
    ensure_connection();

    // 执行 Redis 命令
    va_list args;
    va_start(args, format);
    redisReply* reply = static_cast<redisReply*>(
        redisvCommand(context_, format, args));
    va_end(args);

    return reply;
}
```

**为什么需要 mutex**:
```
线程 1: RPUSH session:ctx msg1
线程 2: RPUSH session:ctx msg2

如果没有 mutex，可能出现：
线程 1: RPUSH session:ctx msg1  ← 开始
线程 2: RPUSH session:ctx msg2  ← 插入
线程 1: 完成                    ← 完成
结果: 顺序可能错乱

有 mutex：
线程 1: 加锁 → RPUSH → 解锁
线程 2: 等待 → 加锁 → RPUSH → 解锁
结果: 顺序保证正确
```

### 3.3 向后兼容策略

```cpp
void save_message(const std::string& context_id, const AgentMessage& message) {
    // 1. 写入新格式（Session Memory）
    memory_manager_.save_session_message(context_id, message);

    // 2. 同时写入旧格式（Legacy Key）
    // 确保旧代码（如 MathAgent）仍能读取
    if (!task_store_->task_exists(context_id)) {
        auto task = AgentTask::create()
            .with_id(context_id)
            .with_context_id(context_id)
            .with_status(TaskState::Running);
        task_store_->set_task(task);
    }
    task_store_->add_history_message(context_id, message);
}
```

**双写策略图解**:
```
新消息
  │
  ├─→ memory_manager_.save_session_message()
  │     └─→ RPUSH a2a:session:ctx-123 msg
  │
  └─→ task_store_->add_history_message()
        └─→ RPUSH a2a:history:ctx-123 msg

读取时：
  新代码 → memory_manager_.get_session_history() → a2a:session:*
  旧代码 → task_store_->get_history() → a2a:history:*
```

## 四、Redis Key 设计

### 4.1 Key 命名规范

**格式**: `{namespace}:{类型}:{标识符}`

**示例**:
```
a2a:session:ctx-123              → 会话记忆
a2a:agent:math-1:ctx-123         → MathAgent 的处理记忆
a2a:agent:orch-1:ctx-123         → Orchestrator 的处理记忆
a2a:task:task-456                → 任务状态
a2a:history:ctx-123              → 旧格式历史（兼容）
```

**为什么这样命名**:
- 清晰区分不同类型的内存
- 便于使用 `KEYS a2a:session:*` 查询
- 避免 key 冲突

### 4.2 数据结构选择

| Key 类型 | Redis 数据结构 | 原因 |
|----------|---------------|------|
| Session Memory | List | 有序、可追加、可限制长度 |
| Agent Memory | List | 有序、可追加、可限制长度 |
| Task State | String (JSON) | 简单、覆盖写入 |

### 4.3 容量限制

| Key 类型 | 限制 | 原因 |
|----------|------|------|
| Session Memory | 1000 条消息 | 避免内存溢出 |
| Agent Memory | 500 条步骤 | 避免内存溢出 |
| Task State | 无限制 | 通常只有一条 |

## 五、集成到 Orchestrator

### 5.1 添加成员变量

```cpp
class AIOrchestrator {
    // 原有成员
    std::string agent_id_;
    std::shared_ptr<RedisTaskStore> task_store_;
    QwenClient qwen_client_;
    RegistryClient registry_client_;
    std::unique_ptr<MCPAgentIntegration> mcp_integration_;

    // Phase 1 新增
    OrchestratorConfig orch_config_;
    TraceLogger trace_logger_;

    // Phase 2 新增
    MemoryManager memory_manager_;
};
```

### 5.2 修改构造函数

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
    , trace_logger_(agent_id)
    , memory_manager_(redis_host, redis_port)  // 初始化 MemoryManager
{
    // ...
}
```

### 5.3 修改 save_message()

```cpp
void save_message(const std::string& context_id, const AgentMessage& message) {
    // 写入 Session Memory（新格式）
    memory_manager_.save_session_message(context_id, message);

    // 写入 Legacy Key（旧格式，兼容）
    if (!task_store_->task_exists(context_id)) {
        auto task = AgentTask::create()
            .with_id(context_id)
            .with_context_id(context_id)
            .with_status(TaskState::Running);
        task_store_->set_task(task);
    }
    task_store_->add_history_message(context_id, message);
}
```

### 5.4 修改 get_history()

```cpp
std::string handle_general_query(const std::string& query, const std::string& context_id) {
    // 使用 MemoryManager 获取历史（优先新格式）
    auto history = memory_manager_.get_session_history(context_id, 5);

    // 构建 history_text
    std::string history_text;
    for (const auto& msg : history) {
        std::string role_str = to_string(msg.role());
        std::string text;
        if (!msg.parts().empty()) {
            auto text_part = dynamic_cast<TextPart*>(msg.parts()[0].get());
            if (text_part) {
                text = text_part->text();
            }
        }
        history_text += role_str + ": " + text + "\n";
    }

    // 调用 LLM
    std::string system_prompt = "你是一个智能助手...\n\n历史对话：\n" + history_text;
    return qwen_client_.chat(system_prompt, query);
}
```

## 六、技术原理总结

### 6.1 Redis List 数据结构

**原理**: Redis List 是一个双向链表，支持 O(1) 的头部/尾部插入。

**操作详解**:
```
RPUSH key value     → 在列表末尾追加元素，O(1)
LPUSH key value     → 在列表头部插入元素，O(1)
LRANGE key start stop → 获取列表范围，O(N)
LTRIM key start stop  → 修剪列表，O(N)
LLEN key            → 获取列表长度，O(1)
```

**为什么用 List 而不是其他结构**:
- **List vs Set**: List 有序，Set 无序
- **List vs Sorted Set**: List 简单，Sorted Set 需要 score
- **List vs String**: List 可以存储多条，String 只能存储一条

### 6.2 线程安全

**问题**: 多个请求可能同时访问 Redis。

**解决方案**: 使用 `std::mutex` 保护共享资源。

```cpp
class MemoryManager {
    std::mutex mutex_;  // 互斥锁

    redisReply* execute_command(const char* format, ...) {
        std::lock_guard<std::mutex> lock(mutex_);  // 加锁
        // ... 执行 Redis 命令
        // lock_guard 析构时自动解锁
    }
};
```

**RAII 风格**:
- 构造时加锁
- 析构时自动解锁
- 即使发生异常也能正确解锁

### 6.3 向后兼容策略

**双写**: 同时写入新旧两种格式

**优势**:
- 新代码使用新格式
- 旧代码仍能读取旧格式
- 不需要一次性修改所有代码

**劣势**:
- 写入两次，性能略有影响
- 存储空间翻倍

**权衡**: 在渐进式迁移中，双写是常见的策略。

## 七、测试验证

### 编译
```bash
cd build && cmake .. && make -j$(nproc)
```

### 启动
```bash
./examples/ai_orchestrator/start_system.sh
```

### 测试
```bash
curl -X POST http://localhost:5000/ -d '{
  "jsonrpc":"2.0","id":"test","method":"message/send",
  "params":{"message":{"role":"user","contextId":"mem-ctx",
  "parts":[{"kind":"text","text":"你好"}]}}
}'
```

### 验证 Redis Key
```bash
redis-cli keys "a2a:*"
```

预期输出:
```
a2a:session:mem-ctx      ← 新格式
a2a:history:mem-ctx      ← 旧格式（兼容）
a2a:task:mem-ctx         ← 任务状态
```

### 验证 Session Memory 内容
```bash
redis-cli lrange "a2a:session:mem-ctx" 0 -1
```

预期输出:
```json
[
  {"role":"user", "contextId":"mem-ctx", "parts":[{"kind":"text","text":"你好"}]},
  {"role":"agent", "contextId":"mem-ctx", "parts":[{"kind":"text","text":"你好！..."}]}
]
```

## 八、后续扩展

### Agent Memory 使用示例

```cpp
// 在 MathAgent 中记录处理步骤
json step = {
    {"step", "tool_call"},
    {"tool", "calculator"},
    {"args", {{"expression", "1+1"}}},
    {"success", true},
    {"result", "2"},
    {"timestamp", std::chrono::system_clock::now().time_since_epoch().count()}
};
memory_manager_.save_agent_memory("math-1", context_id, step);
```

### 查询 Agent 处理历史

```cpp
// 调试时查询 MathAgent 的处理过程
auto steps = memory_manager_.get_agent_memory("math-1", context_id);
for (const auto& step : steps) {
    std::cout << step["step"] << ": " << step.dump() << std::endl;
}
```

### 输出示例

```json
[
  {"step": "intent", "value": "math"},
  {"step": "route", "target": "math-agent"},
  {"step": "tool_call", "tool": "calculator", "success": true},
  {"step": "complete", "duration_ms": 1234}
]
```
