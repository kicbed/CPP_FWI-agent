#include "redis_task_store.hpp"
#include "llm_client.hpp"
#include "http_server.hpp"
#include "registry_client.hpp"
#include "code_agent_tools.hpp"

#include <a2a/models/agent_message.hpp>
#include <a2a/models/agent_task.hpp>
#include <a2a/models/task_status.hpp>
#include <a2a/models/message_part.hpp>
#include <a2a/core/jsonrpc_request.hpp>
#include <a2a/core/jsonrpc_response.hpp>
#include <a2a/core/error_code.hpp>
#include <nlohmann/json.hpp>

#include <chrono>
#include <filesystem>
#include <iostream>
#include <memory>
#include <stdexcept>
#include <string>
#include <thread>

using namespace a2a;
using json = nlohmann::json;

class CodeAgent {
public:
    CodeAgent(const std::string& agent_id, const std::string& listen_address,
              const std::string& registry_url, const std::string& api_key,
              const std::string& redis_host, int redis_port, const std::string& project_root)
        : agent_id_(agent_id),
          listen_address_(listen_address),
          task_store_(std::make_shared<RedisTaskStore>(redis_host, redis_port)),
          llm_client_(api_key, LLMProvider::DEEPSEEK),
          registry_client_(registry_url),
          inspector_(project_root) {
        std::cout << "[CodeAgent] 初始化完成" << std::endl;
    }

    void start(int port) {
        HttpServer server(port);
        server.register_handler("/", [this](const std::string& body) {
            return this->handle_request(body);
        });
        server.register_handler("/.well-known/agent-card.json", [this](const std::string&) {
            return this->get_agent_card();
        });

        std::cout << "[CodeAgent] 启动在端口 " << port << std::endl;
        std::thread server_thread([&server]() { server.start(); });
        std::this_thread::sleep_for(std::chrono::seconds(1));

        AgentRegistration registration;
        registration.id = agent_id_;
        registration.name = "Code Agent";
        registration.address = listen_address_;
        registration.tags = {"code", "engineering", "debugging"};
        registration.description = "Read-only code analysis agent for repository navigation, error diagnosis, and patch suggestions.";
        registration.capabilities = {false, true, false};
        registration.skills = {
            {"code_navigation", "List files, read repository files, search text, and explain code paths", {"where is orchestrator routing implemented?"}},
            {"error_diagnosis", "Analyze compiler and runtime errors", {"explain this C++ build error"}},
            {"patch_proposal", "Propose safe patches without applying them", {"suggest a fix for this function"}}
        };
        registration.agent_card = registration.build_agent_card();

        if (registry_client_.register_agent(registration)) {
            std::cout << "[CodeAgent] 已注册到服务中心" << std::endl;
        } else {
            std::cerr << "[CodeAgent] 注册失败" << std::endl;
        }

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
                    if (text_part) {
                        user_text = text_part->text();
                    }
                }

                std::string context_id = message.context_id().value_or("default");
                save_message(context_id, message);

                std::string response_text = answer_code_question(user_text, context_id);
                auto response_msg = AgentMessage::create()
                    .with_role(MessageRole::Agent)
                    .with_context_id(context_id);
                response_msg.add_text_part(response_text);
                save_message(context_id, response_msg);

                return JsonRpcResponse::create_success(request.id(), response_msg.to_json()).to_json();
            }

            return JsonRpcResponse::create_error(request.id(), ErrorCode::MethodNotFound, "Method not found").to_json();
        } catch (const std::exception& e) {
            return JsonRpcResponse::create_error("1", ErrorCode::InternalError, e.what()).to_json();
        }
    }

    std::string answer_code_question(const std::string& query, const std::string& context_id) {
        auto history = task_store_->get_history(context_id, 20);
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

        const std::string project_context = inspector_.summarize_for_query(query);
        const std::string system_prompt =
            "You are a read-only Code Agent for this repository.\n"
            "You may explain files, diagnose errors, and propose patches.\n"
            "Do not claim that you changed files.\n"
            "Do not execute commands.\n"
            "When suggesting a patch, explain risk and validation.\n"
            "Prefer file paths and line-level references when available.\n\n"
            "## Read-only project inspection context\n" + project_context + "\n"
            "Conversation history:\n" + history_text;

        return llm_client_.chat(system_prompt, query);
    }

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

    std::string get_agent_card() {
        json card = {
            {"name", "Code Agent"},
            {"description", "Read-only code analysis agent for repository navigation, error diagnosis, and patch suggestions."},
            {"version", "1.0.0"},
            {"capabilities", {
                {"streaming", false},
                {"push_notifications", false},
                {"task_management", true},
                {"tool_calling", true}
            }},
            {"skills", json::array({
                {
                    {"name", "code_navigation"},
                    {"description", "List files, read repository files, search text, and explain code paths"},
                    {"input_modes", json::array({"text"})},
                    {"output_modes", json::array({"text"})}
                },
                {
                    {"name", "error_diagnosis"},
                    {"description", "Analyze compiler and runtime errors"},
                    {"input_modes", json::array({"text"})},
                    {"output_modes", json::array({"text"})}
                },
                {
                    {"name", "patch_proposal"},
                    {"description", "Propose safe patches without applying them"},
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
    code_agent::ProjectInspector inspector_;
};

int main(int argc, char* argv[]) {
    if (argc < 5) {
        std::cerr << "用法: " << argv[0]
                  << " <agent_id> <port> <registry_url> <api_key> [--redis-host <host>] [--redis-port <port>] [--project-root <path>]"
                  << std::endl;
        return 1;
    }

    std::string agent_id = argv[1];
    int port = std::stoi(argv[2]);
    std::string registry_url = argv[3];
    std::string api_key = argv[4];
    std::string redis_host = "127.0.0.1";
    int redis_port = 6379;
    std::string project_root = std::filesystem::current_path().string();

    for (int i = 5; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--redis-host" && i + 1 < argc) {
            redis_host = argv[++i];
        } else if (arg == "--redis-port" && i + 1 < argc) {
            redis_port = std::stoi(argv[++i]);
        } else if (arg == "--project-root" && i + 1 < argc) {
            project_root = argv[++i];
        }
    }

    std::string listen_address = "http://localhost:" + std::to_string(port);
    try {
        CodeAgent agent(agent_id, listen_address, registry_url, api_key, redis_host, redis_port, project_root);
        agent.start(port);
    } catch (const std::exception& e) {
        std::cerr << "错误: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
