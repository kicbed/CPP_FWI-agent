#include "redis_task_store.hpp"
#include "llm_client.hpp"
#include "http_server.hpp"
#include "registry_client.hpp"

#include <agent_rpc/research/algorithm_listing_tool.h>
#include <agent_rpc/research/algorithm_registry.h>

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
#include <string>
#include <thread>

using namespace a2a;
using json = nlohmann::json;

class ExperimentPlannerAgent {
public:
    ExperimentPlannerAgent(const std::string& agent_id,
                           const std::string& listen_address,
                           const std::string& registry_url,
                           const std::string& api_key,
                           const std::string& redis_host,
                           int redis_port,
                           const std::string& algorithm_dir)
        : agent_id_(agent_id),
          listen_address_(listen_address),
          task_store_(std::make_shared<RedisTaskStore>(redis_host, redis_port)),
          llm_client_(api_key, LLMProvider::DEEPSEEK),
          registry_client_(registry_url),
          algorithm_context_(load_algorithm_context(algorithm_dir)) {
        std::cout << "[ExperimentPlannerAgent] 初始化完成" << std::endl;
    }

    void start(int port) {
        HttpServer server(port);
        server.register_handler("/", [this](const std::string& body) {
            return this->handle_request(body);
        });
        server.register_handler("/.well-known/agent-card.json", [this](const std::string&) {
            return this->get_agent_card();
        });

        std::cout << "[ExperimentPlannerAgent] 启动在端口 " << port << std::endl;
        std::thread server_thread([&server]() { server.start(); });
        std::this_thread::sleep_for(std::chrono::seconds(1));

        AgentRegistration registration;
        registration.id = agent_id_;
        registration.name = "Experiment Planner Agent";
        registration.address = listen_address_;
        registration.tags = {"experiment", "planning", "research-computing", "fwi"};
        registration.description =
            "Plans dry-run research computing experiments using AlgorithmCards and local research knowledge.";
        registration.capabilities = {false, true, true};
        registration.skills = {
            {"experiment_planning", "Create structured experiment plans", {"plan a multi-scale FWI experiment on Marmousi"}},
            {"parameter_advice", "Recommend parameters with risk analysis", {"how should I set frequency bands for missing low frequency data?"}},
            {"dry_run_job", "Render dry-run job specs without execution", {"generate a dry-run command for CUDA-MPI FWI"}}
        };
        registration.agent_card = registration.build_agent_card();

        if (registry_client_.register_agent(registration)) {
            std::cout << "[ExperimentPlannerAgent] 已注册到服务中心" << std::endl;
        } else {
            std::cerr << "[ExperimentPlannerAgent] 注册失败" << std::endl;
        }

        server_thread.join();
    }

private:
    static std::string load_algorithm_context(const std::string& algorithm_dir) {
        agent_rpc::research::AlgorithmRegistry registry;
        std::string error;
        if (!registry.load_from_directory(algorithm_dir, &error)) {
            return "Algorithm registry unavailable: " + error;
        }
        return agent_rpc::research::list_algorithms_for_tool(registry).dump(2);
    }

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

                std::string response_text = answer_experiment_request(user_text, context_id);
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

    std::string answer_experiment_request(const std::string& query,
                                          const std::string& context_id) {
        auto history = task_store_->get_history(context_id, 20);
        std::string history_text;
        for (const auto& msg : history) {
            std::string text;
            if (!msg.parts().empty()) {
                auto text_part = dynamic_cast<TextPart*>(msg.parts()[0].get());
                if (text_part) {
                    text = text_part->text();
                }
            }
            history_text += to_string(msg.role()) + ": " + text + "\n";
        }

        const std::string system_prompt =
            "You are an Experiment Planner Agent for seismic research computing.\n"
            "Return practical experiment plans with parameters, assumptions, risks, and next steps.\n"
            "Do not claim that any CUDA/MPI job was executed.\n"
            "When execution is requested, output a dry-run JobSpec only.\n"
            "All execution plans must clearly state dry_run: true.\n\n"
            "## Available AlgorithmCards\n" + algorithm_context_ + "\n\n"
            "## Conversation history\n" + history_text;

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
            {"name", "Experiment Planner Agent"},
            {"description", "Plans dry-run research computing experiments using AlgorithmCards and local research knowledge."},
            {"version", "1.0.0"},
            {"capabilities", {
                {"streaming", false},
                {"push_notifications", false},
                {"task_management", true},
                {"tool_calling", true},
                {"knowledge_base", true}
            }},
            {"skills", json::array({
                {
                    {"name", "experiment_planning"},
                    {"description", "Create structured experiment plans"},
                    {"input_modes", json::array({"text"})},
                    {"output_modes", json::array({"text"})}
                },
                {
                    {"name", "parameter_advice"},
                    {"description", "Recommend parameters with risk analysis"},
                    {"input_modes", json::array({"text"})},
                    {"output_modes", json::array({"text"})}
                },
                {
                    {"name", "dry_run_job"},
                    {"description", "Render dry-run job specs without execution"},
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
    std::string algorithm_context_;
};

int main(int argc, char* argv[]) {
    if (argc < 5) {
        std::cerr << "用法: " << argv[0]
                  << " <agent_id> <port> <registry_url> <api_key>"
                  << " [--redis-host <host>] [--redis-port <port>]"
                  << " [--algorithm-dir <path>]"
                  << std::endl;
        return 1;
    }

    std::string agent_id = argv[1];
    int port = std::stoi(argv[2]);
    std::string registry_url = argv[3];
    std::string api_key = argv[4];
    std::string redis_host = "127.0.0.1";
    int redis_port = 6379;
    std::string algorithm_dir =
        (std::filesystem::current_path() / "resources" / "algorithms").string();

    for (int i = 5; i < argc; ++i) {
        std::string arg = argv[i];
        if (arg == "--redis-host" && i + 1 < argc) {
            redis_host = argv[++i];
        } else if (arg == "--redis-port" && i + 1 < argc) {
            redis_port = std::stoi(argv[++i]);
        } else if (arg == "--algorithm-dir" && i + 1 < argc) {
            algorithm_dir = argv[++i];
        }
    }

    std::string listen_address = "http://localhost:" + std::to_string(port);
    try {
        ExperimentPlannerAgent agent(agent_id, listen_address, registry_url, api_key,
                                     redis_host, redis_port, algorithm_dir);
        agent.start(port);
    } catch (const std::exception& e) {
        std::cerr << "错误: " << e.what() << std::endl;
        return 1;
    }

    return 0;
}
