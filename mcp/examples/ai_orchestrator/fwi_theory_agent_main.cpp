/**
 * @file fwi_theory_agent_main.cpp
 * @brief FWI Theory Agent - 全波形反演理论专家
 *
 * 专注于 FWI 理论知识问答，包括：
 * - FWI 基础概念（目标函数、梯度、伴随状态法）
 * - 常见问题诊断（cycle skipping、局部极小值）
 * - 高级反演策略（多尺度 FWI、AWI、包络反演）
 * - 正则化技术（Tikhonov、TV）
 * - 数值方法（有限差分、有限元、谱元法）
 *
 * 不接真实反演程序，只做理论问答。
 * 后续可接入真实 FWI 计算模块（作为 MCP 工具）。
 */

#include "redis_task_store.hpp"
#include "qwen_client.hpp"
#include "http_server.hpp"
#include "registry_client.hpp"

#include <a2a/models/agent_message.hpp>
#include <a2a/models/agent_task.hpp>
#include <a2a/models/task_status.hpp>
#include <a2a/models/message_part.hpp>
#include <a2a/core/jsonrpc_request.hpp>
#include <a2a/core/jsonrpc_response.hpp>
#include <a2a/core/error_code.hpp>

#include <agent_rpc/orchestrator/knowledge_base.h>

#include <nlohmann/json.hpp>
#include <iostream>
#include <memory>
#include <thread>
#include <chrono>

using namespace agent_rpc::orchestrator;

using namespace a2a;
using json = nlohmann::json;

/**
 * @brief FWI Theory Agent - 全波形反演理论专家
 *
 * 功能:
 * - 接收 A2A message/send 请求
 * - 使用专业 FWI prompt 回答理论问题
 * - 支持读取会话历史（上下文记忆）
 * - 注册到 Registry，支持 Agent-RAG 路由
 */
class FWITheoryAgent {
public:
    FWITheoryAgent(const std::string& agent_id,
                   const std::string& listen_address,
                   const std::string& registry_url,
                   const std::string& api_key,
                   const std::string& redis_host,
                   int redis_port)
        : agent_id_(agent_id)
        , listen_address_(listen_address)
        , task_store_(std::make_shared<RedisTaskStore>(redis_host, redis_port))
        , qwen_client_(api_key)
        , registry_client_(registry_url) {

        // 加载本地知识库（使用相对于可执行文件的路径）
        // 可执行文件在 build/examples/ai_orchestrator/
        // 资源在 resources/
        std::string resource_dir = "resources";
        if (knowledge_base_.load(resource_dir)) {
            std::cout << "[FWITheoryAgent] 知识库加载成功，文档数: "
                      << knowledge_base_.get_document_count() << std::endl;
        } else {
            // 尝试使用项目根目录
            resource_dir = "../../resources";
            if (knowledge_base_.load(resource_dir)) {
                std::cout << "[FWITheoryAgent] 知识库加载成功，文档数: "
                          << knowledge_base_.get_document_count() << std::endl;
            } else {
                std::cout << "[FWITheoryAgent] 知识库加载失败或为空" << std::endl;
            }
        }

        std::cout << "[FWITheoryAgent] 初始化完成" << std::endl;
    }

    ~FWITheoryAgent() = default;

    void start(int port) {
        HttpServer server(port);

        // 注册普通请求处理器
        server.register_handler("/", [this](const std::string& body) {
            return this->handle_request(body);
        });

        // 注册流式请求处理器
        server.register_stream_handler("/", [this](const std::string& body,
            std::function<bool(const std::string&)> write_callback) {
            this->handle_stream_request(body, write_callback);
        });

        // 注册 AgentCard 端点
        server.register_handler("/.well-known/agent-card.json", [this](const std::string&) {
            return this->get_agent_card();
        });

        std::cout << "[FWITheoryAgent] 启动在端口 " << port << std::endl;

        // 在后台线程中启动服务器
        std::thread server_thread([&server]() {
            server.start();
        });

        // 等待服务器启动
        std::this_thread::sleep_for(std::chrono::seconds(1));

        // 注册到注册中心（带完整 AgentCard）
        AgentRegistration registration;
        registration.id = agent_id_;
        registration.name = "FWI Theory Agent";
        registration.address = listen_address_;
        registration.tags = {"fwi", "theory", "geophysics", "inversion"};
        registration.description = "全波形反演(FWI)理论专家，解释 FWI/AWI/cycle skipping/"
                                   "伴随状态法/梯度推导/多尺度策略等概念。"
                                   "适合 FWI 理论学习、论文阅读、科研讨论。";
        registration.capabilities = {false, false, false};  // streaming, tool_calling, knowledge_base
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
        registration.agent_card = registration.build_agent_card();

        if (registry_client_.register_agent(registration)) {
            std::cout << "[FWITheoryAgent] 已注册到服务中心" << std::endl;
        } else {
            std::cerr << "[FWITheoryAgent] 注册失败" << std::endl;
        }

        server_thread.join();
    }

private:
    /**
     * @brief 处理普通请求
     */
    std::string handle_request(const std::string& body) {
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

                std::cout << "[FWITheoryAgent] 收到问题: " << user_text << std::endl;

                // 保存用户消息
                save_message(context_id, message);

                // 使用专业 FWI prompt 回答
                std::string response_text = answer_fwi_question(user_text, context_id);

                // 保存响应
                auto response_msg = AgentMessage::create()
                    .with_role(MessageRole::Agent)
                    .with_context_id(context_id);
                response_msg.add_text_part(response_text);
                save_message(context_id, response_msg);

                auto response = JsonRpcResponse::create_success(request.id(), response_msg.to_json());
                return response.to_json();
            }

            return JsonRpcResponse::create_error(request.id(), ErrorCode::MethodNotFound, "Method not found").to_json();

        } catch (const std::exception& e) {
            std::cerr << "[FWITheoryAgent] 错误: " << e.what() << std::endl;
            return JsonRpcResponse::create_error("1", ErrorCode::InternalError, e.what()).to_json();
        }
    }

    /**
     * @brief 处理流式请求
     */
    void handle_stream_request(const std::string& body,
                               std::function<bool(const std::string&)> write_callback) {
        try {
            auto request_json = json::parse(body);
            auto request = JsonRpcRequest::from_json(body);

            if (request.method() != "message/stream") {
                json error_response = {
                    {"jsonrpc", "2.0"},
                    {"id", request.id()},
                    {"error", {{"code", -32601}, {"message", "Method not found for streaming"}}}
                };
                write_callback(error_response.dump());
                return;
            }

            auto params_json = request_json["params"];
            auto message = AgentMessage::from_json(params_json["message"].dump());

            std::string user_text;
            if (!message.parts().empty()) {
                auto text_part = dynamic_cast<TextPart*>(message.parts()[0].get());
                if (text_part) {
                    user_text = text_part->text();
                }
            }

            std::string context_id = message.context_id().value_or("default");

            std::cout << "[FWITheoryAgent] 收到流式问题: " << user_text << std::endl;

            // 保存用户消息
            save_message(context_id, message);

            // 发送开始事件
            json start_event = {
                {"jsonrpc", "2.0"},
                {"id", request.id()},
                {"result", {{"type", "stream_start"}, {"contextId", context_id}}}
            };
            write_callback(start_event.dump());

            // 回答问题
            std::string response_text = answer_fwi_question(user_text, context_id);

            // 流式输出
            const size_t chunk_size = 50;
            for (size_t i = 0; i < response_text.length(); i += chunk_size) {
                std::string chunk = response_text.substr(i, chunk_size);
                json chunk_event = {
                    {"jsonrpc", "2.0"},
                    {"id", request.id()},
                    {"result", {{"type", "chunk"}, {"content", chunk}}}
                };
                if (!write_callback(chunk_event.dump())) return;
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }

            // 保存响应
            auto response_msg = AgentMessage::create()
                .with_role(MessageRole::Agent)
                .with_context_id(context_id);
            response_msg.add_text_part(response_text);
            save_message(context_id, response_msg);

            // 发送完成事件
            json complete_event = {
                {"jsonrpc", "2.0"},
                {"id", request.id()},
                {"result", {{"type", "stream_end"}, {"message", response_msg.to_json()}}}
            };
            write_callback(complete_event.dump());

            std::cout << "[FWITheoryAgent] 流式响应完成" << std::endl;

        } catch (const std::exception& e) {
            std::cerr << "[FWITheoryAgent] 流式处理错误: " << e.what() << std::endl;
            json error_event = {
                {"jsonrpc", "2.0"},
                {"id", "1"},
                {"error", {{"code", -32603}, {"message", e.what()}}}
            };
            write_callback(error_event.dump());
        }
    }

    /**
     * @brief 使用专业 FWI prompt 回答问题
     *
     * 核心能力：
     * 1. 概念解释准确严谨
     * 2. 必要时给出数学公式
     * 3. 用生活类比帮助理解
     * 4. 支持"概念→直觉→数学→代码思路"的教学结构
     */
    std::string answer_fwi_question(const std::string& query, const std::string& context_id) {
        try {
            // 获取历史对话
            auto history = task_store_->get_history(context_id, 5);
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

            // 从知识库检索相关文档
            auto relevant_docs = knowledge_base_.search(query, 3);
            std::string knowledge_context;
            if (!relevant_docs.empty()) {
                knowledge_context = "\n\n## 参考资料\n";
                for (const auto& doc : relevant_docs) {
                    knowledge_context += "### " + doc.title + "\n";
                    // 截取前 500 字符避免 prompt 过长
                    std::string excerpt = doc.content.substr(0, 500);
                    knowledge_context += excerpt + "...\n\n";
                }
            }

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
                "3. 可以用生活类比帮助理解抽象概念（例如：FWI 像闭着眼睛摸大象来推断形状）\n"
                "4. 如果用户问的是教学类问题，用\"概念 → 直觉 → 数学 → 代码思路\"的结构回答\n"
                "5. 如果用户要求用特定语言回答，必须遵守\n\n"

                "## 当前限制\n"
                "当前版本尚未接入真实反演计算模块和速度模型数据。"
                "如果用户要求执行实际 FWI 计算，请说明这是理论指导，建议用户提供模型参数后可扩展。\n\n"

                + knowledge_context +

                "历史对话：\n" + history_text;

            return qwen_client_.chat(system_prompt, query);

        } catch (const std::exception& e) {
            std::cerr << "[FWITheoryAgent] 回答失败: " << e.what() << std::endl;
            return "抱歉，FWI 理论助手处理失败。错误信息: " + std::string(e.what());
        }
    }

    /**
     * @brief 保存消息
     */
    void save_message(const std::string& context_id, const AgentMessage& message) {
        if (!task_store_->task_exists(context_id)) {
            auto task = AgentTask::create()
                .with_id(context_id)
                .with_context_id(context_id)
                .with_status(TaskState::Running);
            task_store_->set_task(task);
        }
        task_store_->add_history_message(context_id, message);
    }

    /**
     * @brief 获取 AgentCard
     */
    std::string get_agent_card() {
        json card = {
            {"name", "FWI Theory Agent"},
            {"description", "全波形反演(FWI)理论专家，解释 FWI/AWI/cycle skipping/伴随状态法等概念"},
            {"version", "1.0.0"},
            {"capabilities", {{"streaming", false}, {"push_notifications", false}, {"task_management", true}}},
            {"skills", json::array({
                {{"name", "FWI 理论"}, {"description", "解释 FWI 理论基础、目标函数、梯度推导"}, {"input_modes", json::array({"text"})}, {"output_modes", json::array({"text"})}},
                {{"name", "Cycle Skipping"}, {"description", "解释 cycle skipping 问题及解决方法"}, {"input_modes", json::array({"text"})}, {"output_modes", json::array({"text"})}},
                {{"name", "反演策略"}, {"description", "解释多尺度 FWI、AWI、包络反演等策略"}, {"input_modes", json::array({"text"})}, {"output_modes", json::array({"text"})}}
            })},
            {"provider", {{"name", "Agent Communication RPC"}, {"organization", "A2A Integration"}}}
        };
        return card.dump();
    }

    std::string agent_id_;
    std::string listen_address_;
    std::shared_ptr<RedisTaskStore> task_store_;
    QwenClient qwen_client_;
    RegistryClient registry_client_;
    KnowledgeBase knowledge_base_;
};

void print_usage(const char* program) {
    std::cerr << "用法: " << program << " <agent_id> <port> <registry_url> <api_key> [options]" << std::endl;
    std::cerr << "选项:" << std::endl;
    std::cerr << "  --redis-host <host>     Redis 主机 (默认: 127.0.0.1)" << std::endl;
    std::cerr << "  --redis-port <port>     Redis 端口 (默认: 6379)" << std::endl;
    std::cerr << std::endl;
    std::cerr << "示例: " << program << " fwi-theory-1 5002 http://localhost:8500 sk-xxx" << std::endl;
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

    try {
        FWITheoryAgent agent(agent_id, listen_address, registry_url, api_key,
                            redis_host, redis_port);
        agent.start(port);
    } catch (const std::exception& e) {
        std::cerr << "错误: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
