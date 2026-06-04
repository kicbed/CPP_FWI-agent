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
#include <nlohmann/json.hpp>
#include <iostream>
#include <thread>
#include <chrono>

using namespace a2a;
using json = nlohmann::json;

class FWITeachingAgent {
public:
    FWITeachingAgent(const std::string& agent_id, const std::string& listen_address, const std::string& registry_url,
                     const std::string& api_key, const std::string& redis_host, int redis_port)
        : agent_id_(agent_id), listen_address_(listen_address), task_store_(std::make_shared<RedisTaskStore>(redis_host, redis_port)),
          qwen_client_(api_key), registry_client_(registry_url) {
        std::cout << "[FWITeachingAgent] 初始化完成" << std::endl;
    }

    void start(int port) {
        HttpServer server(port);
        server.register_handler("/", [this](const std::string& body) { return this->handle_request(body); });
        server.register_handler("/.well-known/agent-card.json", [this](const std::string&) { return this->get_agent_card(); });

        std::cout << "[FWITeachingAgent] 启动在端口 " << port << std::endl;
        std::thread server_thread([&server]() { server.start(); });
        std::this_thread::sleep_for(std::chrono::seconds(1));

        AgentRegistration registration;
        registration.id = agent_id_;
        registration.name = "FWI Teaching Agent";
        registration.address = listen_address_;
        registration.tags = {"fwi", "teaching", "education"};
        registration.description = "FWI 教学助手，用类比+数学+代码+汇报的方式教学。";
        registration.capabilities = {false, false, false};
        registration.skills = {{"fwi_teaching", "用类比解释 FWI 概念", {"用简单的话解释 FWI"}}};
        registration.agent_card = registration.build_agent_card();

        if (registry_client_.register_agent(registration)) std::cout << "[FWITeachingAgent] 已注册到服务中心" << std::endl;
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
                std::string response_text = teach_fwi(user_text, context_id);
                auto response_msg = AgentMessage::create().with_role(MessageRole::Agent).with_context_id(context_id);
                response_msg.add_text_part(response_text);
                save_message(context_id, response_msg);
                return JsonRpcResponse::create_success(request.id(), response_msg.to_json()).to_json();
            }
            return JsonRpcResponse::create_error(request.id(), ErrorCode::MethodNotFound, "Method not found").to_json();
        } catch (const std::exception& e) { return JsonRpcResponse::create_error("1", ErrorCode::InternalError, e.what()).to_json(); }
    }

    std::string teach_fwi(const std::string& query, const std::string& context_id) {
        auto history = task_store_->get_history(context_id, 5);
        std::string history_text;
        for (const auto& msg : history) {
            std::string role_str = to_string(msg.role());
            std::string text;
            if (!msg.parts().empty()) { auto tp = dynamic_cast<TextPart*>(msg.parts()[0].get()); if (tp) text = tp->text(); }
            history_text += role_str + ": " + text + "\n";
        }
        std::string system_prompt =
            "你是一位 FWI 领域的资深教师。\n\n"
            "## 教学风格\n"
            "1. **概念** (1-2 句话): 用最简单的语言解释\n"
            "2. **直觉/类比**: 用生活中的例子帮助理解\n"
            "3. **数学**: 给出核心公式（LaTeX 格式）\n"
            "4. **代码思路**: 给出伪代码\n"
            "5. **汇报表达**: 如何在论文/汇报中描述\n\n"
            "历史对话：\n" + history_text;
        return qwen_client_.chat(system_prompt, query);
    }

    void save_message(const std::string& context_id, const AgentMessage& message) {
        if (!task_store_->task_exists(context_id)) {
            auto task = AgentTask::create().with_id(context_id).with_context_id(context_id).with_status(TaskState::Running);
            task_store_->set_task(task);
        }
        task_store_->add_history_message(context_id, message);
    }

    std::string get_agent_card() {
        json card = {{"name","FWI Teaching Agent"},{"description","FWI 教学助手"},{"version","1.0.0"},
            {"capabilities",{{"streaming",false},{"push_notifications",false},{"task_management",true}}},
            {"skills",json::array({{{"name","FWI 教学"},{"description","用类比解释 FWI 概念"},{"input_modes",json::array({"text"})},{"output_modes",json::array({"text"})}}})},
            {"provider",{{"name","Agent Communication RPC"},{"organization","A2A Integration"}}}};
        return card.dump();
    }

    std::string agent_id_, listen_address_;
    std::shared_ptr<RedisTaskStore> task_store_;
    QwenClient qwen_client_;
    RegistryClient registry_client_;
};

int main(int argc, char* argv[]) {
    if (argc < 5) { std::cerr << "用法: " << argv[0] << " <agent_id> <port> <registry_url> <api_key>" << std::endl; return 1; }
    std::string agent_id = argv[1]; int port = std::stoi(argv[2]);
    std::string registry_url = argv[3]; std::string api_key = argv[4];
    std::string redis_host = "127.0.0.1"; int redis_port = 6379;
    for (int i = 5; i < argc; ++i) { std::string arg = argv[i]; if (arg == "--redis-host" && i + 1 < argc) redis_host = argv[++i]; else if (arg == "--redis-port" && i + 1 < argc) redis_port = std::stoi(argv[++i]); }
    std::string listen_address = "http://localhost:" + std::to_string(port);
    try { FWITeachingAgent agent(agent_id, listen_address, registry_url, api_key, redis_host, redis_port); agent.start(port); }
    catch (const std::exception& e) { std::cerr << "错误: " << e.what() << std::endl; return 1; }
    return 0;
}
