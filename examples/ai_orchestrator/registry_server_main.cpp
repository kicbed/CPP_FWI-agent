#include "agent_registry.hpp"
#include "http_server.hpp"
#include <nlohmann/json.hpp>
#include <iostream>
#include <thread>
#include <chrono>
#include <atomic>

using json = nlohmann::json;

class RegistryServer {
public:
    explicit RegistryServer(int heartbeat_timeout = 30) : registry_(heartbeat_timeout), running_(false) {}

    void start(int port) {
        running_ = true;
        std::thread health_thread([this]() {
            while (running_) {
                registry_.check_health();
                std::this_thread::sleep_for(std::chrono::seconds(10));
            }
        });

        HttpServer server(port);
        server.register_handler("/v1/agent/register", [this](const std::string& body) { return handle_register(body); });
        server.register_handler("/v1/agent/deregister", [this](const std::string& body) { return handle_deregister(body); });
        server.register_handler("/v1/agent/heartbeat", [this](const std::string& body) { return handle_heartbeat(body); });
        server.register_handler("/v1/agent/find", [this](const std::string& body) { return handle_find(body); });
        server.register_handler("/v1/agents", [this](const std::string&) { return handle_list(); });
        server.register_handler("/v1/agent/cards", [this](const std::string&) { return handle_get_cards(); });
        server.register_handler("/v1/agent/cards/find", [this](const std::string& body) { return handle_find_cards(body); });

        std::cout << "[Registry] 启动在端口 " << port << std::endl;
        server.start();

        running_ = false;
        if (health_thread.joinable()) health_thread.join();
    }

private:
    std::string handle_register(const std::string& body) {
        try {
            auto j = json::parse(body);
            auto registration = AgentRegistration::from_json(j);
            if (registry_.register_agent(registration)) {
                std::cout << "[Registry] 注册 Agent: " << registration.id << " (" << registration.name << ")" << std::endl;
                return json({{"success", true}}).dump();
            }
            return json({{"success", false}, {"error", "Registration failed"}}).dump();
        } catch (const std::exception& e) {
            return json({{"success", false}, {"error", e.what()}}).dump();
        }
    }

    std::string handle_deregister(const std::string& body) {
        try {
            auto j = json::parse(body);
            std::string agent_id = j.at("id").get<std::string>();
            if (registry_.deregister_agent(agent_id)) {
                std::cout << "[Registry] 注销 Agent: " << agent_id << std::endl;
                return json({{"success", true}}).dump();
            }
            return json({{"success", false}, {"error", "Agent not found"}}).dump();
        } catch (const std::exception& e) {
            return json({{"success", false}, {"error", e.what()}}).dump();
        }
    }

    std::string handle_heartbeat(const std::string& body) {
        try {
            auto j = json::parse(body);
            std::string agent_id = j.at("id").get<std::string>();
            if (registry_.heartbeat(agent_id)) return json({{"success", true}}).dump();
            return json({{"success", false}, {"error", "Agent not found"}}).dump();
        } catch (const std::exception& e) {
            return json({{"success", false}, {"error", e.what()}}).dump();
        }
    }

    std::string handle_find(const std::string& body) {
        try {
            auto j = json::parse(body);
            std::string tag = j.at("tag").get<std::string>();
            auto agents = registry_.find_agents_by_tag(tag);
            json result = json::array();
            for (const auto& agent : agents) result.push_back(agent.to_json());
            return json({{"agents", result}}).dump();
        } catch (const std::exception& e) {
            return json({{"agents", json::array()}, {"error", e.what()}}).dump();
        }
    }

    std::string handle_list() {
        auto agents = registry_.get_all_agents();
        json result = json::array();
        for (const auto& agent : agents) result.push_back(agent.to_json());
        return json({{"agents", result}}).dump();
    }

    std::string handle_get_cards() {
        auto agents = registry_.get_all_agents();
        json cards = json::array();
        for (const auto& agent : agents) cards.push_back(agent.build_agent_card());
        return json({{"cards", cards}}).dump();
    }

    std::string handle_find_cards(const std::string& body) {
        try {
            auto j = json::parse(body);
            std::string tag = j.at("tag").get<std::string>();
            auto agents = registry_.find_agents_by_tag(tag);
            json cards = json::array();
            for (const auto& agent : agents) cards.push_back(agent.build_agent_card());
            return json({{"cards", cards}}).dump();
        } catch (const std::exception& e) {
            return json({{"cards", json::array()}, {"error", e.what()}}).dump();
        }
    }

    AgentRegistry registry_;
    std::atomic<bool> running_{false};
};

int main(int argc, char* argv[]) {
    int port = 8500;
    int heartbeat_timeout = 30;
    if (argc > 1) port = std::stoi(argv[1]);
    if (argc > 2) heartbeat_timeout = std::stoi(argv[2]);

    std::cout << "Agent Registry Server" << std::endl;
    std::cout << "端口: " << port << std::endl;
    std::cout << "心跳超时: " << heartbeat_timeout << " 秒" << std::endl;

    try {
        RegistryServer server(heartbeat_timeout);
        server.start(port);
    } catch (const std::exception& e) {
        std::cerr << "错误: " << e.what() << std::endl;
        return 1;
    }
    return 0;
}
