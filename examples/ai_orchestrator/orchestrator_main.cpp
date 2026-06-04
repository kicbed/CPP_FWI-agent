/**
 * @file orchestrator_main.cpp
 * @brief AI Orchestrator - 调度 AI Agent
 *
 * 基于 a2a-cpp/examples/multi_agent_demo/dynamic_orchestrator.cpp
 * 集成到 agent-communication RPC 框架
 * 集成 MCP 工具支持
 *
 * Requirements: 12.2, 12.3, 12.4
 * Task 19.4: 集成 MCP 到 Orchestrator Agent
 */

#include "redis_task_store.hpp"
#include "llm_client.hpp"
#include "http_server.hpp"
#include "registry_client.hpp"

#include <a2a/models/agent_message.hpp>
#include <a2a/models/agent_task.hpp>
#include <a2a/models/task_status.hpp>
#include <a2a/models/message_part.hpp>
#include <a2a/core/jsonrpc_request.hpp>
#include <a2a/core/jsonrpc_response.hpp>
#include <a2a/core/error_code.hpp>

// MCP 集成
#include <agent_rpc/mcp/mcp_agent_integration.h>

// 现代 Agent 架构
#include <agent_rpc/orchestrator/request_context.h>
#include <agent_rpc/orchestrator/trace_logger.h>
#include <agent_rpc/orchestrator/config.h>
#include <agent_rpc/orchestrator/memory_manager.h>
#include <agent_rpc/orchestrator/agent_retriever.h>
#include <agent_rpc/orchestrator/llm_agent_selector.h>
#include <agent_rpc/orchestrator/tool_calling_engine.h>

#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <memory>
#include <thread>
#include <chrono>

using namespace a2a;
using namespace agent_rpc::orchestrator;
using json = nlohmann::json;
using namespace agent_rpc::mcp;

// agent_rag开关
struct OrchestratorRuntimeConfig {
    std::string routing_mode = "fixed";   // fixed | agent-rag
    bool enable_agent_rag = false;
};

//bool参数解析
static bool parse_bool_arg(const std::string& value) {
    return value == "true" || value == "1" || value == "yes" || value == "on";
}

// 简单的 HTTP 客户端
class SimpleHttpClient {
public:
    static size_t WriteCallback(void* contents, size_t size, size_t nmemb, std::string* userp) {
        userp->append((char*)contents, size * nmemb);
        return size * nmemb;
    }

    static std::string post(const std::string& url, const std::string& body) {
        CURL* curl = curl_easy_init();
        if (!curl) return "";

        std::string response;
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());

        struct curl_slist* headers = nullptr;
        headers = curl_slist_append(headers, "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);

        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);

        curl_easy_perform(curl);
        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);

        return response;
    }
};

/**
 * @brief AI Orchestrator - 智能调度器
 *
 * 功能:
 * - 接收用户问题
 * - 使用 AI 模型分析意图
 * - 路由到合适的专业 Agent
 * - 使用 MCP 工具增强能力
 * - 返回处理结果
 */
class AIOrchestrator {
public:
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
        , llm_client_(api_key, orch_config.llm_provider, orch_config.llm_model, orch_config.llm_api_url)
        , registry_client_(registry_url)
        , mcp_integration_(std::make_unique<MCPAgentIntegration>())
        , orch_config_(orch_config)
        , trace_logger_(agent_id)
        , memory_manager_(redis_host, redis_port)
        , agent_retriever_(registry_client_, orch_config.embedding_provider, orch_config.dashscope_api_key, orch_config.local_embedding_url)
        , llm_agent_selector_(llm_client_)
        , tool_calling_engine_(mcp_integration_.get(), llm_client_) {

        // 初始化 MCP 集成
        if (!mcp_integration_->initialize(mcp_config)) {
            trace_logger_.log_system(LogLevel::WARN, "MCP 初始化失败，将在无 MCP 模式下运行");
        } else if (mcp_integration_->isAvailable()) {
            auto tools = mcp_integration_->getToolNames();
            std::string tool_list;
            for (const auto& tool : tools) {
                tool_list += tool + " ";
            }
            trace_logger_.log_system(LogLevel::INFO, "MCP 已启用，可用工具: " + tool_list);
        }

        trace_logger_.log_system(LogLevel::INFO, "初始化完成 routing_mode=" +
                                 to_string(orch_config_.routing_mode));
    }

    ~AIOrchestrator() {
        if (mcp_integration_) {
            mcp_integration_->shutdown();
        }
    }

    void start(int port) {
        // 启动 HTTP 服务器
        HttpServer server(port);

        // A2A 协议端点 - 普通请求
        server.register_handler("/", [this](const std::string& body) {
            return this->handle_request(body);
        });

        // A2A 协议端点 - 流式请求
        server.register_stream_handler("/", [this](const std::string& body,
            std::function<bool(const std::string&)> write_callback) {
            this->handle_stream_request(body, write_callback);
        });

        // Agent Card 端点 (A2A 协议标准)
        server.register_handler("/.well-known/agent-card.json", [this](const std::string&) {
            return this->get_agent_card();
        });

        trace_logger_.log_system(LogLevel::INFO, "启动在端口 " + std::to_string(port));

        // 在后台线程中启动服务器
        std::thread server_thread([&server]() {
            server.start();
        });

        // 等待服务器启动
        std::this_thread::sleep_for(std::chrono::seconds(1));

        // 注册到注册中心（带完整 AgentCard）
        AgentRegistration registration;
        registration.id = agent_id_;
        registration.name = "AI Orchestrator";
        registration.address = listen_address_;
        registration.tags = {"orchestrator", "coordinator"};
        registration.description = "智能协调器，负责意图识别和任务分发。支持数学计算、FWI 科研问答、通用问答等多种场景。";
        registration.capabilities = {true, true, false};  // streaming, tool_calling, knowledge_base
        registration.skills = {
            {"intent_recognition", "识别用户意图并路由到相应的专业 Agent", {"数学计算", "FWI 理论", "通用问答"}},
            {"task_coordination", "协调多个 Agent 完成复杂任务", {"多 Agent 协作"}}
        };
        registration.agent_card = registration.build_agent_card();

        if (registry_client_.register_agent(registration)) {
            trace_logger_.log_system(LogLevel::INFO, "已注册到服务中心");
        } else {
            trace_logger_.log_system(LogLevel::ERROR, "注册失败");
        }

        server_thread.join();
    }

private:
    std::string handle_request(const std::string& body) {
        // Create request context for tracing
        RequestContext ctx = RequestContext::create();
        ctx.routing_mode = to_string(orch_config_.routing_mode);
        ctx.tool_calling_mode = to_string(orch_config_.tool_calling_mode);

        try {
            auto request_json = json::parse(body);
            auto request = JsonRpcRequest::from_json(body);

            if (request.method() == "message/send") {
                auto params_json = request_json["params"];
                auto message = AgentMessage::from_json(params_json["message"].dump());

                // 获取文本内容
                std::string user_text;
                if (!message.parts().empty()) {
                    auto text_part = dynamic_cast<TextPart*>(message.parts()[0].get());
                    if (text_part) {
                        user_text = text_part->text();
                    }
                }

                std::string context_id = message.context_id().value_or("default");

                // Populate request context
                ctx.context_id = context_id;
                ctx.task_id = context_id;
                ctx.user_text = user_text;

                trace_logger_.log_request(ctx, user_text);

                // 保存用户消息
                save_message(context_id, message);

                std::string response_text;

                // 根据路由模式选择路由策略
                if (orch_config_.routing_mode == RoutingMode::AGENT_RAG) {
                    // Agent-RAG 动态路由
                    trace_logger_.log_info(ctx, "ROUTING", "agent-rag mode");
                    response_text = route_with_agent_rag(user_text, context_id, ctx);
                } else {
                    // 传统固定路由 (fixed mode)
                    trace_logger_.log_info(ctx, "ROUTING", "fixed mode");

                    // 识别意图
                    std::string intent = analyze_intent(user_text);
                    trace_logger_.log_info(ctx, "INTENT", intent);

                    if (intent == "math") {
                        trace_logger_.log_routing(ctx, "math-agent");
                        response_text = call_math_agent(user_text, context_id);

                        // 如果 Math Agent 不可用，回退到 general
                        if (response_text.find("服务暂时不可用") != std::string::npos ||
                            response_text.find("无法解析响应") != std::string::npos) {
                            trace_logger_.log_info(ctx, "FALLBACK", "math→general");
                            response_text = handle_general_query(user_text, context_id);
                        }

                    } else if (intent == "code") {
                        trace_logger_.log_routing(ctx, "code-agent");
                        response_text = call_code_agent(user_text, context_id);

                        // 当前没有 Code Agent 时，回退到 general
                        if (response_text.find("服务暂时不可用") != std::string::npos ||
                            response_text.find("无法解析响应") != std::string::npos) {
                            trace_logger_.log_info(ctx, "FALLBACK", "code→general");
                            response_text = handle_general_query(user_text, context_id);
                        }

                    } else if (intent == "fwi") {
                        trace_logger_.log_routing(ctx, "fwi-handler");
                        response_text = handle_fwi_query(user_text, context_id);

                    } else {
                        trace_logger_.log_routing(ctx, "general-handler");
                        response_text = handle_general_query(user_text, context_id);
                    }
                }

                // 保存 Agent 响应
                auto response_msg = AgentMessage::create()
                    .with_role(MessageRole::Agent)
                    .with_context_id(context_id);
                response_msg.add_text_part(response_text);
                save_message(context_id, response_msg);

                // Log completion
                trace_logger_.log_response(ctx, ctx.elapsed_ms());

                // 返回响应
                auto response = JsonRpcResponse::create_success(request.id(), response_msg.to_json());
                return response.to_json();
            }

            return JsonRpcResponse::create_error(request.id(), ErrorCode::MethodNotFound, "Method not found").to_json();

        } catch (const std::exception& e) {
            trace_logger_.log_error(ctx, e.what());
            return JsonRpcResponse::create_error("1", ErrorCode::InternalError, e.what()).to_json();
        }
    }

    /**
     * @brief 处理流式请求 (message/stream)
     *
     * 支持 A2A 协议的流式消息传输
    */
    void handle_stream_request(const std::string& body,
                               std::function<bool(const std::string&)> write_callback) {
        try {
            auto request_json = json::parse(body);
            auto request = JsonRpcRequest::from_json(body);

            if (request.method() != "message/stream") {
                // 非流式方法，返回错误
                json error_response = {
                    {"jsonrpc", "2.0"},
                    {"id", request.id()},
                    {"error", {
                        {"code", -32601},
                        {"message", "Method not found for streaming"}
                    }}
                };
                write_callback(error_response.dump());
                return;
            }

            auto params_json = request_json["params"];
            auto message = AgentMessage::from_json(params_json["message"].dump());

            // 获取文本内容
            std::string user_text;
            if (!message.parts().empty()) {
                auto text_part = dynamic_cast<TextPart*>(message.parts()[0].get());
                if (text_part) {
                    user_text = text_part->text();
                }
            }

            std::string context_id = message.context_id().value_or("default");

            trace_logger_.log_system(LogLevel::INFO, "收到流式消息: " + user_text);

            // 保存用户消息
            save_message(context_id, message);

            // 发送开始事件
            json start_event = {
                {"jsonrpc", "2.0"},
                {"id", request.id()},
                {"result", {
                    {"type", "stream_start"},
                    {"contextId", context_id}
                }}
            };
            write_callback(start_event.dump());

            // 识别意图
            std::string intent = analyze_intent(user_text);
            trace_logger_.log_system(LogLevel::INFO, "识别意图: " + intent);

            // 发送意图识别事件
            json intent_event = {
                {"jsonrpc", "2.0"},
                {"id", request.id()},
                {"result", {
                    {"type", "intent"},
                    {"intent", intent}
                }}
            };
            write_callback(intent_event.dump());

            // 处理查询并流式返回
            std::string response_text;

            if (intent == "math") {
                response_text = call_math_agent(user_text, context_id);
            } else if (intent == "code") {
                response_text = call_code_agent(user_text, context_id);
            } else if (intent == "fwi") {
                response_text = handle_fwi_query(user_text, context_id);
            } else {
                response_text = handle_general_query(user_text, context_id);
            }

            // UTF-8 安全的分块函数
            auto utf8_safe_chunk = [](const std::string& text, size_t start, size_t max_len) -> std::string {
                if (start >= text.length()) return "";

                size_t end = std::min(start + max_len, text.length());

                // 确保不在 UTF-8 多字节字符中间切断
                while (end > start && end < text.length()) {
                    unsigned char c = static_cast<unsigned char>(text[end]);
                    // 如果是 UTF-8 后续字节 (10xxxxxx)，向前移动
                    if ((c & 0xC0) == 0x80) {
                        end--;
                    } else {
                        break;
                    }
                }

                return text.substr(start, end - start);
            };

            // 流式输出：UTF-8 安全分块
            const size_t chunk_size = 50;
            size_t pos = 0;
            while (pos < response_text.length()) {
                std::string chunk = utf8_safe_chunk(response_text, pos, chunk_size);
                if (chunk.empty()) break;

                pos += chunk.length();

                json chunk_event = {
                    {"jsonrpc", "2.0"},
                    {"id", request.id()},
                    {"result", {
                        {"type", "chunk"},
                        {"content", chunk}
                    }}
                };

                if (!write_callback(chunk_event.dump())) {
                    trace_logger_.log_system(LogLevel::ERROR, "流式写入失败");
                    return;
                }

                // 小延迟模拟流式效果
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }

            // 保存 Agent 响应
            auto response_msg = AgentMessage::create()
                .with_role(MessageRole::Agent)
                .with_context_id(context_id);
            response_msg.add_text_part(response_text);
            save_message(context_id, response_msg);

            // 发送完成事件
            json complete_event = {
                {"jsonrpc", "2.0"},
                {"id", request.id()},
                {"result", {
                    {"type", "stream_end"},
                    {"message", response_msg.to_json()}
                }}
            };
            write_callback(complete_event.dump());

            trace_logger_.log_system(LogLevel::INFO, "流式响应完成");

        } catch (const std::exception& e) {
            trace_logger_.log_system(LogLevel::ERROR, std::string("流式处理错误: ") + e.what());
            json error_event = {
                {"jsonrpc", "2.0"},
                {"id", "1"},
                {"error", {
                    {"code", -32603},
                    {"message", e.what()}
                }}
            };
            write_callback(error_event.dump());
        }
    }

    /**
     * @brief Agent-RAG 动态路由
     *
     * 1. AgentRetriever: 从 Registry 获取候选 Agent
     * 2. LLMAgentSelector: 让 LLM 从候选中选择
     * 3. 调用选中的 Agent
     */
    std::string route_with_agent_rag(const std::string& query,
                                     const std::string& context_id,
                                     const RequestContext& ctx) {
        try {
            // Step 1: Retrieve candidate Agents
            auto candidates = agent_retriever_.retrieve(query, 5);
            trace_logger_.log_info(ctx, "RETRIEVE",
                "found " + std::to_string(candidates.size()) + " candidates");

            if (candidates.empty()) {
                trace_logger_.log_info(ctx, "FALLBACK", "no candidates → general");
                return handle_general_query(query, context_id);
            }

            // Log candidates
            for (const auto& candidate : candidates) {
                trace_logger_.log_info(ctx, "CANDIDATE",
                    candidate.agent.id + " (score=" +
                    std::to_string(candidate.relevance_score) +
                    ", reason=" + candidate.match_reason + ")");
            }

            // Step 2: LLM selects the best Agent
            std::string selected_agent_id = llm_agent_selector_.select(query, candidates);
            trace_logger_.log_routing(ctx, selected_agent_id);

            if (selected_agent_id.empty()) {
                trace_logger_.log_info(ctx, "FALLBACK", "LLM selection failed → general");
                return handle_general_query(query, context_id);
            }

            // Step 3: Call the selected Agent
            // Find the Agent's address
            std::string agent_url;
            for (const auto& candidate : candidates) {
                if (candidate.agent.id == selected_agent_id) {
                    agent_url = candidate.agent.address;
                    break;
                }
            }

            if (agent_url.empty()) {
                trace_logger_.log_info(ctx, "FALLBACK", "Agent not found → general");
                return handle_general_query(query, context_id);
            }

            // Check if it's the Orchestrator itself (general handler)
            if (selected_agent_id == agent_id_) {
                trace_logger_.log_info(ctx, "SELF", "handling locally");
                return handle_general_query(query, context_id);
            }

            // Call the Agent
            trace_logger_.log_info(ctx, "CALL", selected_agent_id + " at " + agent_url);
            std::string response = call_agent_by_url(agent_url, query, context_id);

            // Check if Agent is unavailable
            if (response.find("服务暂时不可用") != std::string::npos ||
                response.find("无法解析响应") != std::string::npos) {
                trace_logger_.log_info(ctx, "FALLBACK", selected_agent_id + " unavailable → general");
                return handle_general_query(query, context_id);
            }

            return response;

        } catch (const std::exception& e) {
            trace_logger_.log_error(ctx, std::string("Agent-RAG error: ") + e.what());
            return handle_general_query(query, context_id);
        }
    }

    /**
     * @brief 调用指定 URL 的 Agent
     */
    std::string call_agent_by_url(const std::string& agent_url,
                                  const std::string& query,
                                  const std::string& context_id) {
        try {
            // 构造请求
            json request = {
                {"jsonrpc", "2.0"},
                {"id", "1"},
                {"method", "message/send"},
                {"params", {
                    {"message", {
                        {"role", "user"},
                        {"contextId", context_id},
                        {"parts", {{{"kind", "text"}, {"text", query}}}}
                    }},
                    {"historyLength", 5}
                }}
            };

            // 发送请求
            std::string response_body = SimpleHttpClient::post(agent_url, request.dump());
            auto response_json = json::parse(response_body);

            if (response_json.contains("result") &&
                response_json["result"].contains("parts") &&
                !response_json["result"]["parts"].empty()) {
                return response_json["result"]["parts"][0]["text"].get<std::string>();
            }

            return "无法解析响应";

        } catch (const std::exception& e) {
            std::cerr << "[Orchestrator] 调用 Agent 失败: " << e.what() << std::endl;
            return "服务暂时不可用";
        }
    }

std::string analyze_intent(const std::string& text) {
    std::string system_prompt =
        "你是一个智能体系统的意图路由器。"
        "你的任务不是回答用户问题，而是判断用户问题应该交给哪个模块处理。\n"
        "只能从以下四个类别中选择一个：\n"
        "1. math：数学计算、方程求解、公式推导、数值计算。\n"
        "2. code：编程、代码解释、代码报错、软件开发相关问题。\n"
        "3. fwi：全波形反演(FWI)、AWI、cycle skipping、伴随状态法、速度模型、"
        "地震数据、地球物理反演、正演模拟、梯度计算、多尺度反演、"
        "目标函数、震源子波、观测系统、炮集数据等地球物理科研相关问题。\n"
        "4. general：其他所有普通问答、专业概念解释、翻译、科研知识、背景介绍。\n\n"
        "注意：如果用户问的是 FWI、全波形反演、地球物理反演、速度模型、cycle skipping 等概念，"
        "这属于 fwi，不属于 general，也不属于 math。\n\n"
        "你必须只返回 JSON，不要输出任何解释。格式如下：\n"
        "{\"intent\":\"general\"}";

    std::string user_prompt =
        "请判断下面用户输入的意图类别：\n"
        + text;

    try {
        std::string result = llm_client_.chat(system_prompt, user_prompt);

        trace_logger_.log_system(LogLevel::INFO, "原始意图识别结果: " + result);

        // 尝试按 JSON 解析
        auto j = json::parse(result);

        if (j.contains("intent") && j["intent"].is_string()) {
            std::string intent = j["intent"].get<std::string>();

            // 转小写
            for (auto& c : intent) {
                c = static_cast<char>(std::tolower(static_cast<unsigned char>(c)));
            }

            if (intent == "math" || intent == "code" || intent == "general" || intent == "fwi") {
                return intent;
            }
        }

        // JSON 正常但内容不合法，兜底 general
        return "general";

    } catch (const std::exception& e) {
        trace_logger_.log_system(LogLevel::WARN, std::string("意图识别失败，使用 general 兜底: ") + e.what());
        return "general";
    }
}

    std::string call_math_agent(const std::string& query, const std::string& context_id) {
        return call_agent_by_tag("math", query, context_id);
    }

    std::string call_code_agent(const std::string& query, const std::string& context_id) {
        return call_agent_by_tag("code", query, context_id);
    }

    std::string call_agent_by_tag(const std::string& tag,
                                   const std::string& query,
                                   const std::string& context_id) {
        try {
            // 从注册中心查找 Agent
            std::string agent_url = registry_client_.select_agent_by_tag(tag);

            trace_logger_.log_system(LogLevel::INFO, "调用 " + tag + " Agent: " + agent_url);

            // 构造请求
            json request = {
                {"jsonrpc", "2.0"},
                {"id", "1"},
                {"method", "message/send"},
                {"params", {
                    {"message", {
                        {"role", "user"},
                        {"contextId", context_id},
                        {"parts", {{{"kind", "text"}, {"text", query}}}}
                    }},
                    {"historyLength", 5}
                }}
            };

            // 发送请求
            std::string response_body = SimpleHttpClient::post(agent_url, request.dump());
            auto response_json = json::parse(response_body);

            if (response_json.contains("result") &&
                response_json["result"].contains("parts") &&
                !response_json["result"]["parts"].empty()) {
                return response_json["result"]["parts"][0]["text"].get<std::string>();
            }

            return "无法解析响应";

        } catch (const std::exception& e) {
            trace_logger_.log_system(LogLevel::ERROR, "调用 " + tag + " Agent 失败: " + e.what());
            return "抱歉，" + tag + " 服务暂时不可用，使用通用模型回答";
        }
    }

    std::string handle_general_query(const std::string& query, const std::string& context_id) {
        try {
            // Use MemoryManager to get session history
            auto history = memory_manager_.get_session_history(context_id, 5);

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

            // Tool-RAG: Try to use tools if enabled
            std::string tool_context;
            if (orch_config_.tool_calling_mode == ToolCallingMode::LLM) {
                tool_context = tool_calling_engine_.process(query);
            }

            std::string system_prompt =
                "你是一个广泛意义上工程领域的智能助手。"
                "你需要根据用户要求回答问题。"
                "如果用户要求用中文、英文、日文等语言回答，你必须遵守。\n\n";

            // Add tool context if available
            if (!tool_context.empty()) {
                system_prompt += "工具查询结果:\n" + tool_context + "\n\n";
            }

            system_prompt += "历史对话：\n" + history_text;

            return llm_client_.chat(system_prompt, query);

        } catch (const std::exception& e) {
            trace_logger_.log_system(LogLevel::ERROR, std::string("general 问答失败: ") + e.what());

            return "抱歉，通用问答模块处理失败。错误信息: " + std::string(e.what());
        }
    }

    /**
     * @brief 处理 FWI（全波形反演）科研问题
     *
     * 使用专业 FWI 知识增强的 system prompt 进行回答。
     * 当前阶段：LLM + 专业知识 prompt（不接真实反演模块）。
     * 后续阶段：可接入 FWITheoryAgent 独立 Agent 或本地知识库。
     */
    std::string handle_fwi_query(const std::string& query, const std::string& context_id) {
        try {
            // Use MemoryManager to get session history
            auto history = memory_manager_.get_session_history(context_id, 5);

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
                "3. 可以用生活类比帮助理解抽象概念（例如：FWI 像闭着眼睛摸大象来推断形状）\n"
                "4. 如果用户问的是教学类问题，用\"概念 → 直觉 → 数学 → 代码思路\"的结构回答\n"
                "5. 如果用户要求用特定语言回答，必须遵守\n\n"
                "## 当前限制\n"
                "当前版本尚未接入真实反演计算模块和速度模型数据。"
                "如果用户要求执行实际 FWI 计算，请说明这是理论指导，建议用户提供模型参数后可扩展。\n\n"
                "历史对话：\n" + history_text;

            return llm_client_.chat(system_prompt, query);

        } catch (const std::exception& e) {
            trace_logger_.log_system(LogLevel::ERROR, std::string("FWI 问答失败: ") + e.what());

            return "抱歉，FWI 科研助手模块处理失败。错误信息: " + std::string(e.what());
        }
    }

    /**
     * @brief 尝试使用 MCP 工具获取辅助信息
     */
    std::string tryMCPTools(const std::string& query) {
        if (!mcp_integration_ || !mcp_integration_->isAvailable()) {
            return "";
        }

        std::string result;
        auto tools = mcp_integration_->getAvailableTools();

        // 根据查询内容选择合适的工具
        for (const auto& tool : tools) {
            // 简单的关键词匹配来决定是否使用工具
            bool should_use = false;

            if (tool.name.find("search") != std::string::npos ||
                tool.name.find("query") != std::string::npos) {
                // 搜索类工具
                should_use = true;
            } else if (tool.name.find("time") != std::string::npos ||
                       tool.name.find("date") != std::string::npos) {
                // 时间类工具
                if (query.find("时间") != std::string::npos ||
                    query.find("日期") != std::string::npos ||
                    query.find("time") != std::string::npos) {
                    should_use = true;
                }
            }

            if (should_use) {
                json args;
                args["query"] = query;

                trace_logger_.log_system(LogLevel::INFO, "调用 MCP 工具: " + tool.name);

                auto tool_result = mcp_integration_->callTool(tool.name, args.dump());
                if (tool_result.success) {
                    result += "[" + tool.name + "]: " + tool_result.result + "\n";
                }
            }
        }

        return result;
    }

    void save_message(const std::string& context_id, const AgentMessage& message) {
        // Save to session memory (user-visible conversation)
        memory_manager_.save_session_message(context_id, message);

        // Also save to legacy key for backward compatibility
        if (!task_store_->task_exists(context_id)) {
            auto task = AgentTask::create()
                .with_id(context_id)
                .with_context_id(context_id)
                .with_status(TaskState::Running);
            task_store_->set_task(task);
        }
        task_store_->add_history_message(context_id, message);
    }

    std::string get_agent_card() {
        json card = {
            {"name", "AI Orchestrator Agent"},
            {"description", "智能协调器，负责意图识别和任务分发"},
            {"version", "1.0.0"},
            {"capabilities", {
                {"streaming", true},
                {"push_notifications", false},
                {"task_management", true}
            }},
            {"skills", json::array({
                {
                    {"name", "意图识别"},
                    {"description", "识别用户意图并路由到相应的专业 Agent"},
                    {"input_modes", json::array({"text"})},
                    {"output_modes", json::array({"text"})}
                },
                {
                    {"name", "任务协调"},
                    {"description", "协调多个 Agent 完成复杂任务"},
                    {"input_modes", json::array({"text"})},
                    {"output_modes", json::array({"text"})}
                }
            })},
            {"provider", {
                {"name", "Agent Communication RPC"},
                {"organization", "A2A Integration"}
            }}
        };
        return card.dump();
    }

    std::string agent_id_;
    std::string listen_address_;
    std::shared_ptr<RedisTaskStore> task_store_;
    LLMClient llm_client_;
    RegistryClient registry_client_;
    std::unique_ptr<MCPAgentIntegration> mcp_integration_;
    OrchestratorConfig orch_config_;
    TraceLogger trace_logger_;
    MemoryManager memory_manager_;
    AgentRetriever agent_retriever_;
    LLMAgentSelector llm_agent_selector_;
    ToolCallingEngine tool_calling_engine_;
};

void print_usage(const char* program) {
    std::cerr << "用法: " << program << " <agent_id> <port> <registry_url> <api_key> [options]" << std::endl;
    std::cerr << "选项:" << std::endl;
    std::cerr << "  --redis-host <host>     Redis 主机 (默认: 127.0.0.1)" << std::endl;
    std::cerr << "  --redis-port <port>     Redis 端口 (默认: 6379)" << std::endl;
    std::cerr << "  --mcp-server <path>     MCP Server 可执行文件路径" << std::endl;
    std::cerr << "  --mcp-args <args>       MCP Server 启动参数 (逗号分隔)" << std::endl;
    std::cerr << "  --enable-mcp            启用 MCP" << std::endl;
    std::cerr << std::endl;
    std::cerr << "示例: " << program << " orch-1 5000 http://localhost:8500 sk-xxx --enable-mcp --mcp-server /path/to/mcp_server" << std::endl;
}

int main(int argc, char* argv[]) {
    if (argc < 5) {
        print_usage(argv[0]);
        return 1;
    }

    std::string agent_id = argv[1];
    int port = std::stoi(argv[2]);
    std::string registry_url = argv[3];
    std::string api_key = argv[4];

    // 默认值
    std::string redis_host = "127.0.0.1";
    int redis_port = 6379;

    // 解析 MCP 配置
    MCPAgentConfig mcp_config = parseMCPConfigFromArgs(argc, argv);

    // 也尝试从环境变量获取 MCP 配置
    if (!mcp_config.enable_mcp) {
        MCPAgentConfig env_config = parseMCPConfigFromEnv();
        if (env_config.enable_mcp) {
            mcp_config = env_config;
        }
    }

    // 解析其他命令行参数
    for (int i = 5; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--redis-host" && i + 1 < argc) {
            redis_host = argv[++i];
        } else if (arg == "--redis-port" && i + 1 < argc) {
            redis_port = std::stoi(argv[++i]);
        }
    }

    std::string listen_address = "http://localhost:" + std::to_string(port);

    // Load orchestrator config from environment
    OrchestratorConfig orch_config = OrchestratorConfig::from_env();
    orch_config.agent_id = agent_id;
    orch_config.port = port;
    orch_config.registry_url = registry_url;
    orch_config.api_key = api_key;
    orch_config.redis_host = redis_host;
    orch_config.redis_port = redis_port;

    try {
        AIOrchestrator orchestrator(agent_id, listen_address, registry_url, api_key,
                                   redis_host, redis_port, mcp_config, orch_config);
        orchestrator.start(port);
    } catch (const std::exception& e) {
        std::cerr << "错误: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
