#include "redis_task_store.hpp"
#include "llm_client.hpp"
#include "http_server.hpp"
#include "registry_client.hpp"
#include "api_key_env.hpp"
#include <a2a/models/agent_message.hpp>
#include <a2a/models/agent_task.hpp>
#include <a2a/models/task_status.hpp>
#include <a2a/models/message_part.hpp>
#include <a2a/core/jsonrpc_request.hpp>
#include <a2a/core/jsonrpc_response.hpp>
#include <a2a/core/error_code.hpp>
#include <agent_rpc/mcp/mcp_agent_integration.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <memory>
#include <thread>
#include <chrono>

using namespace a2a;
using json = nlohmann::json;
using namespace agent_rpc::mcp;

class MathAgent {
public:
    MathAgent(const std::string& agent_id, const std::string& listen_address, const std::string& registry_url,
              const std::string& api_key, const std::string& redis_host, int redis_port, const MCPAgentConfig& mcp_config = MCPAgentConfig())
        : agent_id_(agent_id), listen_address_(listen_address), task_store_(std::make_shared<RedisTaskStore>(redis_host, redis_port)),
          llm_client_(api_key, LLMProvider::DEEPSEEK), registry_client_(registry_url), mcp_integration_(std::make_unique<MCPAgentIntegration>()) {
        if (!mcp_integration_->initialize(mcp_config)) {
            std::cerr << "[MathAgent] MCP 初始化失败" << std::endl;
        } else if (mcp_integration_->isAvailable()) {
            auto tools = mcp_integration_->getToolNames();
            std::cout << "[MathAgent] MCP 已启用，可用工具: ";
            for (const auto& tool : tools) std::cout << tool << " ";
            std::cout << std::endl;
        }
        std::cout << "[MathAgent] 初始化完成" << std::endl;
    }

    void start(int port) {
        HttpServer server(port);
        server.register_handler("/", [this](const std::string& body) { return this->handle_request(body); });
        server.register_stream_handler("/", [this](const std::string& body, std::function<bool(const std::string&)> cb) { this->handle_stream_request(body, cb); });
        server.register_handler("/.well-known/agent-card.json", [this](const std::string&) { return this->get_agent_card(); });

        std::cout << "[MathAgent] 启动在端口 " << port << std::endl;
        std::thread server_thread([&server]() { server.start(); });
        std::this_thread::sleep_for(std::chrono::seconds(1));

        AgentRegistration registration;
        registration.id = agent_id_;
        registration.name = "Math Agent";
        registration.address = listen_address_;
        registration.tags = {"math", "calculator", "computation"};
        registration.description = "专业数学计算助手，擅长各类数学问题求解。支持 MCP 工具调用（calculator 等）。";
        registration.capabilities = {false, true, false};
        registration.skills = {{"math_calculation", "执行各类数学计算和方程求解", {"计算 1+1", "求解 x^2=4"}}, {"expression_evaluation", "计算数学表达式", {"sin(3.14)", "sqrt(16)"}}};
        registration.agent_card = registration.build_agent_card();

        if (registry_client_.register_agent(registration)) std::cout << "[MathAgent] 已注册到服务中心" << std::endl;
        server_thread.join();
    }

private:
    std::string handle_request(const std::string& body) {
        try {
            auto request_json = json::parse(body);
            auto request = JsonRpcRequest::from_json(body);
            if (request.method() == "message/send") {
                auto params_json = request_json["params"];
                auto message = AgentMessage::from_json(params_json["message"].dump());
                std::string user_text;
                if (!message.parts().empty()) {
                    auto text_part = dynamic_cast<TextPart*>(message.parts()[0].get());
                    if (text_part) user_text = text_part->text();
                }
                std::string context_id = message.context_id().value_or("default");
                std::cout << "[MathAgent] 收到数学问题: " << user_text << std::endl;
                save_message(context_id, message);
                std::string response_text = solve_math(user_text, context_id);
                auto response_msg = AgentMessage::create().with_role(MessageRole::Agent).with_context_id(context_id);
                response_msg.add_text_part(response_text);
                save_message(context_id, response_msg);
                return JsonRpcResponse::create_success(request.id(), response_msg.to_json()).to_json();
            }
            return JsonRpcResponse::create_error(request.id(), ErrorCode::MethodNotFound, "Method not found").to_json();
        } catch (const std::exception& e) {
            return JsonRpcResponse::create_error("1", ErrorCode::InternalError, e.what()).to_json();
        }
    }

    void handle_stream_request(const std::string& body, std::function<bool(const std::string&)> write_callback) {
        try {
            auto request_json = json::parse(body);
            auto request = JsonRpcRequest::from_json(body);
            if (request.method() != "message/stream") {
                json err = {{"jsonrpc","2.0"},{"id",request.id()},{"error",{{"code",-32601},{"message","Method not found"}}}};
                write_callback(err.dump()); return;
            }
            auto params_json = request_json["params"];
            auto message = AgentMessage::from_json(params_json["message"].dump());
            std::string user_text;
            if (!message.parts().empty()) { auto tp = dynamic_cast<TextPart*>(message.parts()[0].get()); if (tp) user_text = tp->text(); }
            std::string context_id = message.context_id().value_or("default");
            save_message(context_id, message);
            json start_ev = {{"jsonrpc","2.0"},{"id",request.id()},{"result",{{"type","stream_start"},{"contextId",context_id}}}};
            write_callback(start_ev.dump());
            std::string response_text = solve_math(user_text, context_id);
            const size_t chunk_size = 50;
            for (size_t i = 0; i < response_text.length(); i += chunk_size) {
                std::string chunk = response_text.substr(i, chunk_size);
                json chunk_ev = {{"jsonrpc","2.0"},{"id",request.id()},{"result",{{"type","chunk"},{"content",chunk}}}};
                if (!write_callback(chunk_ev.dump())) return;
                std::this_thread::sleep_for(std::chrono::milliseconds(10));
            }
            auto response_msg = AgentMessage::create().with_role(MessageRole::Agent).with_context_id(context_id);
            response_msg.add_text_part(response_text);
            save_message(context_id, response_msg);
            json complete_ev = {{"jsonrpc","2.0"},{"id",request.id()},{"result",{{"type","stream_end"},{"message",response_msg.to_json()}}}};
            write_callback(complete_ev.dump());
        } catch (const std::exception& e) {
            json err = {{"jsonrpc","2.0"},{"id","1"},{"error",{{"code",-32603},{"message",e.what()}}}};
            write_callback(err.dump());
        }
    }

    std::string solve_math(const std::string& question, const std::string& context_id) {
        auto history = task_store_->get_history(context_id, 20);
        std::string history_text;
        for (const auto& msg : history) {
            std::string role_str = to_string(msg.role());
            std::string text;
            if (!msg.parts().empty()) { auto tp = dynamic_cast<TextPart*>(msg.parts()[0].get()); if (tp) text = tp->text(); }
            history_text += role_str + ": " + text + "\n";
        }
        std::string tool_result;
        if (mcp_integration_ && mcp_integration_->isAvailable()) tool_result = tryMCPCalculation(question);
        std::string system_prompt = "你是一个专业的数学助手。请解答用户的数学问题，给出详细的解题步骤。如果是计算题，请给出准确的计算结果。";
        if (!tool_result.empty()) system_prompt += "\n\n工具计算结果参考:\n" + tool_result;
        return llm_client_.chat(system_prompt + "\n\n历史对话:\n" + history_text, question);
    }

    std::string tryMCPCalculation(const std::string& question) {
        if (!mcp_integration_ || !mcp_integration_->isAvailable()) return "";
        std::vector<ToolInfo> relevant_tools;
        if (mcp_integration_->isRAGEnabled()) {
            relevant_tools = mcp_integration_->getRelevantTools(question, 5);
        } else {
            if (mcp_integration_->hasToolAvailable("calculator")) { ToolInfo t; t.name = "calculator"; relevant_tools.push_back(t); }
        }
        for (const auto& tool : relevant_tools) {
            if (tool.name == "calculator" || tool.name == "calculate" || tool.name == "math") {
                json args; args["expression"] = question;
                auto result = mcp_integration_->callTool(tool.name, args.dump());
                if (result.success) return result.result;
            }
        }
        if (mcp_integration_->hasToolAvailable("calculator")) {
            json args; args["expression"] = question;
            auto result = mcp_integration_->callTool("calculator", args.dump());
            if (result.success) return result.result;
        }
        return "";
    }

    void save_message(const std::string& context_id, const AgentMessage& message) {
        if (!task_store_->task_exists(context_id)) {
            auto task = AgentTask::create().with_id(context_id).with_context_id(context_id).with_status(TaskState::Running);
            task_store_->set_task(task);
        }
        task_store_->add_history_message(context_id, message);
    }

    std::string get_agent_card() {
        json card = {{"name","Math Agent"},{"description","专业数学计算助手，擅长各类数学问题求解"},{"version","1.0.0"},
            {"capabilities",{{"streaming",false},{"push_notifications",false},{"task_management",true}}},
            {"skills",json::array({{{"name","数学计算"},{"description","执行各类数学计算和方程求解"},{"input_modes",json::array({"text"})},{"output_modes",json::array({"text"})}}})},
            {"provider",{{"name","Agent Communication RPC"},{"organization","A2A Integration"}}}};
        return card.dump();
    }

    std::string agent_id_, listen_address_;
    std::shared_ptr<RedisTaskStore> task_store_;
    LLMClient llm_client_;
    RegistryClient registry_client_;
    std::unique_ptr<MCPAgentIntegration> mcp_integration_;
};

void print_usage(const char* program) {
    std::cerr << "用法: " << program << " <agent_id> <port> <registry_url> @env [--redis-host <host>] [--redis-port <port>] [--enable-mcp] [--mcp-server <path>]" << std::endl;
}

int main(int argc, char* argv[]) {
    if (argc < 5) { print_usage(argv[0]); return 1; }
    std::string agent_id = argv[1]; int port = std::stoi(argv[2]);
    std::string registry_url = argv[3];
    std::string api_key = agent_rpc::examples::resolve_api_key_argument(argv[4]);
    if (api_key.empty()) { std::cerr << "错误: 未为所选 LLM_PROVIDER 配置 API Key" << std::endl; return 1; }
    std::string redis_host = "127.0.0.1"; int redis_port = 6379;
    MCPAgentConfig mcp_config = parseMCPConfigFromArgs(argc, argv);
    if (!mcp_config.enable_mcp) { MCPAgentConfig env_config = parseMCPConfigFromEnv(); if (env_config.enable_mcp) mcp_config = env_config; }
    for (int i = 5; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--redis-host" && i + 1 < argc) redis_host = argv[++i];
        else if (arg == "--redis-port" && i + 1 < argc) redis_port = std::stoi(argv[++i]);
    }
    std::string listen_address = "http://localhost:" + std::to_string(port);
    try {
        MathAgent agent(agent_id, listen_address, registry_url, api_key, redis_host, redis_port, mcp_config);
        agent.start(port);
    } catch (const std::exception& e) { std::cerr << "错误: " << e.what() << std::endl; return 1; }
    return 0;
}
