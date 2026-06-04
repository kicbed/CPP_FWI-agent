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
#include <agent_rpc/orchestrator/knowledge_base.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <thread>
#include <chrono>

using namespace a2a;
using json = nlohmann::json;
using namespace agent_rpc::orchestrator;

class FWITheoryAgent {
public:
    FWITheoryAgent(const std::string& agent_id, const std::string& listen_address, const std::string& registry_url,
                   const std::string& api_key, const std::string& redis_host, int redis_port)
        : agent_id_(agent_id), listen_address_(listen_address), task_store_(std::make_shared<RedisTaskStore>(redis_host, redis_port)),
          llm_client_(api_key, LLMProvider::DEEPSEEK), registry_client_(registry_url) {
        std::string resource_dir = "resources";
        if (knowledge_base_.load(resource_dir)) {
            std::cout << "[FWITheoryAgent] 知识库加载成功，文档数: " << knowledge_base_.get_document_count() << std::endl;
        }
        std::cout << "[FWITheoryAgent] 初始化完成" << std::endl;
    }

    void start(int port) {
        HttpServer server(port);
        server.register_handler("/", [this](const std::string& body) { return this->handle_request(body); });
        server.register_handler("/.well-known/agent-card.json", [this](const std::string&) { return this->get_agent_card(); });

        std::cout << "[FWITheoryAgent] 启动在端口 " << port << std::endl;
        std::thread server_thread([&server]() { server.start(); });
        std::this_thread::sleep_for(std::chrono::seconds(1));

        AgentRegistration registration;
        registration.id = agent_id_;
        registration.name = "FWI Theory Agent";
        registration.address = listen_address_;
        registration.tags = {"fwi", "theory", "geophysics", "inversion"};
        registration.description = "全波形反演(FWI)理论专家，解释 FWI/AWI/cycle skipping/伴随状态法等概念。";
        registration.capabilities = {false, false, false};
        registration.skills = {{"fwi_theory", "解释 FWI 理论基础", {"什么是 FWI?", "解释伴随状态法"}}, {"cycle_skipping", "解释 cycle skipping", {"什么是 cycle skipping?"}}};
        registration.agent_card = registration.build_agent_card();

        if (registry_client_.register_agent(registration)) std::cout << "[FWITheoryAgent] 已注册到服务中心" << std::endl;
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
                std::string response_text = answer_fwi_question(user_text, context_id);
                auto response_msg = AgentMessage::create().with_role(MessageRole::Agent).with_context_id(context_id);
                response_msg.add_text_part(response_text);
                save_message(context_id, response_msg);
                return JsonRpcResponse::create_success(request.id(), response_msg.to_json()).to_json();
            }
            return JsonRpcResponse::create_error(request.id(), ErrorCode::MethodNotFound, "Method not found").to_json();
        } catch (const std::exception& e) { return JsonRpcResponse::create_error("1", ErrorCode::InternalError, e.what()).to_json(); }
    }

    std::string answer_fwi_question(const std::string& query, const std::string& context_id) {
        auto history = task_store_->get_history(context_id, 20);
        std::string history_text;
        for (const auto& msg : history) {
            std::string role_str = to_string(msg.role());
            std::string text;
            if (!msg.parts().empty()) { auto tp = dynamic_cast<TextPart*>(msg.parts()[0].get()); if (tp) text = tp->text(); }
            history_text += role_str + ": " + text + "\n";
        }

        // 搜索知识库
        std::cout << "[FWITheoryAgent] 搜索知识库: " << query << std::endl;
        auto relevant_docs = knowledge_base_.search(query, 5);
        std::cout << "[FWITheoryAgent] 找到 " << relevant_docs.size() << " 个相关文档" << std::endl;
        for (const auto& doc : relevant_docs) {
            std::cout << "  - " << doc.title << " (分数: " << doc.relevance_score << ")" << std::endl;
        }

        // 调试：输出知识库内容长度
        if (!relevant_docs.empty()) {
            std::cout << "[FWITheoryAgent] 第一个文档内容长度: " << relevant_docs[0].content.length() << std::endl;
            std::cout << "[FWITheoryAgent] 第一个文档内容前200字: " << relevant_docs[0].content.substr(0, 200) << std::endl;
        }
        std::string knowledge_context;
        if (!relevant_docs.empty()) {
            knowledge_context = "\n\n## ⚠️ 重要：本地知识库资料（必须使用）\n"
                               "以下是本地知识库中与用户问题直接相关的资料。\n"
                               "**你必须使用这些资料来回答问题，不要自己编造。**\n"
                               "**回答时必须引用知识库内容。**\n\n";
            for (const auto& doc : relevant_docs) {
                knowledge_context += "### 📚 " + doc.title + " (相关度: " +
                                    std::to_string(doc.relevance_score) + ")\n";
                // 提供完整内容
                std::string content = doc.content;
                if (content.length() > 3000) {
                    content = content.substr(0, 3000) + "\n... (更多内容省略)";
                }
                knowledge_context += content + "\n\n";
            }
        } else {
            knowledge_context = "\n\n## 本地知识库\n"
                               "本地知识库中没有找到与用户问题直接相关的资料。\n"
                               "请基于你的专业知识回答，但要说明这是你的推测。\n\n";
        }

        std::string system_prompt =
            "你是一位全波形反演(FWI)领域的资深科研助手。\n\n"

            "## 🔴 最重要的规则\n"
            "你必须使用下方提供的「本地知识库资料」来回答问题。\n"
            "如果知识库有相关内容，你必须基于知识库内容回答，不要说「未找到」。\n"
            "如果知识库没有相关内容，你才能用自己的知识回答。\n\n"

            "## 专业知识范围\n"
            "- FWI 理论基础\n"
            "- 高级反演策略：多尺度反演、AWI、包络反演\n"
            "- 正则化技术\n\n"

            + knowledge_context +
            "\n## 历史对话\n" + history_text;

        // 调试：输出 prompt 长度
        std::cout << "[FWITheoryAgent] Prompt 长度: " << system_prompt.length() << std::endl;
        std::cout << "[FWITheoryAgent] 知识库内容长度: " << knowledge_context.length() << std::endl;

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
        json card = {{"name","FWI Theory Agent"},{"description","FWI 理论专家"},{"version","1.0.0"},
            {"capabilities",{{"streaming",false},{"push_notifications",false},{"task_management",true}}},
            {"skills",json::array({{{"name","FWI 理论"},{"description","解释 FWI 理论基础"},{"input_modes",json::array({"text"})},{"output_modes",json::array({"text"})}}})},
            {"provider",{{"name","Agent Communication RPC"},{"organization","A2A Integration"}}}};
        return card.dump();
    }

    std::string agent_id_, listen_address_;
    std::shared_ptr<RedisTaskStore> task_store_;
    LLMClient llm_client_;
    RegistryClient registry_client_;
    KnowledgeBase knowledge_base_;
};

int main(int argc, char* argv[]) {
    if (argc < 5) { std::cerr << "用法: " << argv[0] << " <agent_id> <port> <registry_url> <api_key> [--redis-host <host>] [--redis-port <port>]" << std::endl; return 1; }
    std::string agent_id = argv[1]; int port = std::stoi(argv[2]);
    std::string registry_url = argv[3]; std::string api_key = argv[4];
    std::string redis_host = "127.0.0.1"; int redis_port = 6379;
    for (int i = 5; i < argc; ++i) { std::string arg = argv[i]; if (arg == "--redis-host" && i + 1 < argc) redis_host = argv[++i]; else if (arg == "--redis-port" && i + 1 < argc) redis_port = std::stoi(argv[++i]); }
    std::string listen_address = "http://localhost:" + std::to_string(port);
    try { FWITheoryAgent agent(agent_id, listen_address, registry_url, api_key, redis_host, redis_port); agent.start(port); }
    catch (const std::exception& e) { std::cerr << "错误: " << e.what() << std::endl; return 1; }
    return 0;
}
