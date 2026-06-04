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
#include <nlohmann/json.hpp>
#include <iostream>
#include <thread>
#include <chrono>

using namespace a2a;
using json = nlohmann::json;

class GeneralResearchAgent {
public:
    GeneralResearchAgent(const std::string& agent_id, const std::string& listen_address, const std::string& registry_url,
                         const std::string& api_key, const std::string& redis_host, int redis_port)
        : agent_id_(agent_id), listen_address_(listen_address), task_store_(std::make_shared<RedisTaskStore>(redis_host, redis_port)),
          llm_client_(api_key, LLMProvider::DEEPSEEK), registry_client_(registry_url) {
        std::cout << "[GeneralResearchAgent] 初始化完成" << std::endl;
    }

    void start(int port) {
        HttpServer server(port);
        server.register_handler("/", [this](const std::string& body) { return this->handle_request(body); });
        server.register_handler("/.well-known/agent-card.json", [this](const std::string&) { return this->get_agent_card(); });

        std::cout << "[GeneralResearchAgent] 启动在端口 " << port << std::endl;
        std::thread server_thread([&server]() { server.start(); });
        std::this_thread::sleep_for(std::chrono::seconds(1));

        AgentRegistration registration;
        registration.id = agent_id_;
        registration.name = "General Research Agent";
        registration.address = listen_address_;
        registration.tags = {"research", "general", "academic"};
        registration.description = "通用科研助手，处理文献检索、研究方法、学术写作等问题。";
        registration.capabilities = {false, false, false};
        registration.skills = {{"research_qa", "回答科研相关问题", {"如何写论文摘要"}}};
        registration.agent_card = registration.build_agent_card();

        if (registry_client_.register_agent(registration)) std::cout << "[GeneralResearchAgent] 已注册到服务中心" << std::endl;
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
                if (!message.parts().empty()) { auto tp = dynamic_cast<TextPart*>(message.parts()[0].get()); if (tp) user_text = tp->text(); }
                std::string context_id = message.context_id().value_or("default");
                save_message(context_id, message);
                std::string response_text = answer_research_question(user_text, context_id);
                auto response_msg = AgentMessage::create().with_role(MessageRole::Agent).with_context_id(context_id);
                response_msg.add_text_part(response_text);
                save_message(context_id, response_msg);
                return JsonRpcResponse::create_success(request.id(), response_msg.to_json()).to_json();
            }
            return JsonRpcResponse::create_error(request.id(), ErrorCode::MethodNotFound, "Method not found").to_json();
        } catch (const std::exception& e) { return JsonRpcResponse::create_error("1", ErrorCode::InternalError, e.what()).to_json(); }
    }

    std::string answer_research_question(const std::string& query, const std::string& context_id) {
        auto history = task_store_->get_history(context_id, 5);
        std::string history_text;
        for (const auto& msg : history) {
            std::string role_str = to_string(msg.role());
            std::string text;
            if (!msg.parts().empty()) { auto tp = dynamic_cast<TextPart*>(msg.parts()[0].get()); if (tp) text = tp->text(); }
            history_text += role_str + ": " + text + "\n";
        }
        std::string system_prompt =
            "你是一位经验丰富的科研助手，擅长各类科研问题。\n\n"
            "## 能力范围\n"
            "- 文献检索和综述\n- 研究方法设计\n- 学术写作\n- 数据分析\n\n"
            "历史对话：\n" + history_text;
        return llm_client_.chat(system_prompt, query);
    }

    void save_message(const std::string& context_id, const AgentMessage& message) {
        if (!task_store_->task_exists(context_id)) {
            auto task = AgentTask::create().with_id(context_id).with_context_id(context_id).with_status(TaskState::Running);
            task_store_->set_task(task);
        }
        task_store_->add_history_message(context_id, message);
    }

    std::string get_agent_card() {
        json card = {{"name","General Research Agent"},{"description","通用科研助手"},{"version","1.0.0"},
            {"capabilities",{{"streaming",false},{"push_notifications",false},{"task_management",true}}},
            {"skills",json::array({{{"name","科研问答"},{"description","回答科研相关问题"},{"input_modes",json::array({"text"})},{"output_modes",json::array({"text"})}}})},
            {"provider",{{"name","Agent Communication RPC"},{"organization","A2A Integration"}}}};
        return card.dump();
    }

    std::string agent_id_, listen_address_;
    std::shared_ptr<RedisTaskStore> task_store_;
    LLMClient llm_client_;
    RegistryClient registry_client_;
};

int main(int argc, char* argv[]) {
    if (argc < 5) { std::cerr << "用法: " << argv[0] << " <agent_id> <port> <registry_url> <api_key>" << std::endl; return 1; }
    std::string agent_id = argv[1]; int port = std::stoi(argv[2]);
    std::string registry_url = argv[3]; std::string api_key = argv[4];
    std::string redis_host = "127.0.0.1"; int redis_port = 6379;
    for (int i = 5; i < argc; ++i) { std::string arg = argv[i]; if (arg == "--redis-host" && i + 1 < argc) redis_host = argv[++i]; else if (arg == "--redis-port" && i + 1 < argc) redis_port = std::stoi(argv[++i]); }
    std::string listen_address = "http://localhost:" + std::to_string(port);
    try { GeneralResearchAgent agent(agent_id, listen_address, registry_url, api_key, redis_host, redis_port); agent.start(port); }
    catch (const std::exception& e) { std::cerr << "错误: " << e.what() << std::endl; return 1; }
    return 0;
}
