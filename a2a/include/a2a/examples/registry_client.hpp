#pragma once

#include "agent_registry.hpp"
#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <string>
#include <vector>
#include <stdexcept>
#include <thread>
#include <atomic>
#include <algorithm>
#include <cctype>

using json = nlohmann::json;

struct RegistryResponseBuffer {
    static constexpr std::size_t kMaxBytes = 1024U * 1024U;
    std::string body;
    bool too_large = false;
};

// CURL 回调函数
static size_t WriteCallback(void* contents, size_t size, size_t nmemb,
                            RegistryResponseBuffer* output) {
    const std::size_t bytes = size * nmemb;
    if (bytes > RegistryResponseBuffer::kMaxBytes - output->body.size()) {
        output->too_large = true;
        return 0;
    }
    output->body.append(static_cast<const char*>(contents), bytes);
    return bytes;
}

/**
 * @brief 注册中心客户端
 */
class RegistryClient {
public:
    explicit RegistryClient(const std::string& registry_url = "http://localhost:8500")
        : registry_url_(registry_url)
        , heartbeat_running_(false) {
        if (!is_loopback_http_url(registry_url_)) {
            throw std::invalid_argument(
                "Registry URL must be loopback HTTP with an explicit port");
        }
    }
    
    ~RegistryClient() {
        stop_heartbeat();
    }
    
    // 注册 Agent
    bool register_agent(const AgentRegistration& registration) {
        json request = registration.to_json();
        auto response = post("/v1/agent/register", request.dump());
        
        if (response.contains("success") && response["success"].get<bool>()) {
            // 保存注册信息用于心跳
            current_registration_ = registration;
            // 启动心跳线程
            start_heartbeat();
            return true;
        }
        
        return false;
    }
    
    // 注销 Agent
    bool deregister_agent(const std::string& agent_id) {
        stop_heartbeat();
        
        json request = {{"id", agent_id}};
        auto response = post("/v1/agent/deregister", request.dump());
        
        return response.contains("success") && response["success"].get<bool>();
    }
    
    // 根据标签查找 Agent
    std::vector<AgentRegistration> find_agents_by_tag(const std::string& tag) {
        json request = {{"tag", tag}};
        auto response = post("/v1/agent/find", request.dump());
        
        std::vector<AgentRegistration> result;
        
        if (response.contains("agents") && response["agents"].is_array()) {
            for (const auto& agent_json : response["agents"]) {
                result.push_back(AgentRegistration::from_json(agent_json));
            }
        }
        
        return result;
    }
    
    // 获取所有 Agent
    std::vector<AgentRegistration> get_all_agents() {
        auto response = get("/v1/agents");
        
        std::vector<AgentRegistration> result;
        
        if (response.contains("agents") && response["agents"].is_array()) {
            for (const auto& agent_json : response["agents"]) {
                result.push_back(AgentRegistration::from_json(agent_json));
            }
        }
        
        return result;
    }
    
    // 选择一个 Agent（负载均衡：轮询）
    std::string select_agent_by_tag(const std::string& tag) {
        auto agents = find_agents_by_tag(tag);
        
        if (agents.empty()) {
            throw std::runtime_error("No agent found with tag: " + tag);
        }
        
        // 简单轮询
        static std::map<std::string, size_t> round_robin_index;
        size_t& index = round_robin_index[tag];
        
        std::string address = agents[index % agents.size()].address;
        index++;
        
        return address;
    }

private:
    static bool is_loopback_http_url(const std::string& url) {
        std::string remainder;
        if (url.rfind("http://127.0.0.1:", 0) == 0) {
            remainder = url.substr(std::string("http://127.0.0.1:").size());
        } else if (url.rfind("http://localhost:", 0) == 0) {
            remainder = url.substr(std::string("http://localhost:").size());
        } else {
            return false;
        }
        if (remainder.empty() || !std::all_of(
                remainder.begin(), remainder.end(), [](unsigned char value) {
                    return std::isdigit(value) != 0;
                })) {
            return false;
        }
        try {
            const unsigned long port = std::stoul(remainder);
            return port > 0 && port <= 65535;
        } catch (const std::exception&) {
            return false;
        }
    }

    // 发送 POST 请求
    json post(const std::string& path, const std::string& body) {
        CURL* curl = curl_easy_init();
        if (!curl) {
            throw std::runtime_error("Failed to initialize CURL");
        }
        
        std::string url = registry_url_ + path;
        RegistryResponseBuffer response;
        
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_POST, 1L);
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
        
        struct curl_slist* headers = nullptr;
        headers = curl_slist_append(headers, "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 5L);
        curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 2L);
        curl_easy_setopt(curl, CURLOPT_NOPROXY, "*");
        curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);
        curl_easy_setopt(curl, CURLOPT_PROTOCOLS, CURLPROTO_HTTP);

        CURLcode res = curl_easy_perform(curl);
        long status = 0;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
        
        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);
        
        if (response.too_large) {
            throw std::runtime_error("Registry response exceeded 1 MiB");
        }
        if (res != CURLE_OK) {
            throw std::runtime_error("CURL error: " + std::string(curl_easy_strerror(res)));
        }
        if (status != 200) {
            throw std::runtime_error("Registry HTTP status: " + std::to_string(status));
        }
        
        return json::parse(response.body);
    }
    
    // 发送 GET 请求
    json get(const std::string& path) {
        CURL* curl = curl_easy_init();
        if (!curl) {
            throw std::runtime_error("Failed to initialize CURL");
        }
        
        std::string url = registry_url_ + path;
        RegistryResponseBuffer response;
        
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 5L);
        curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT, 2L);
        curl_easy_setopt(curl, CURLOPT_NOPROXY, "*");
        curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);
        curl_easy_setopt(curl, CURLOPT_PROTOCOLS, CURLPROTO_HTTP);

        CURLcode res = curl_easy_perform(curl);
        long status = 0;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
        curl_easy_cleanup(curl);
        
        if (response.too_large) {
            throw std::runtime_error("Registry response exceeded 1 MiB");
        }
        if (res != CURLE_OK) {
            throw std::runtime_error("CURL error: " + std::string(curl_easy_strerror(res)));
        }
        if (status != 200) {
            throw std::runtime_error("Registry HTTP status: " + std::to_string(status));
        }
        
        return json::parse(response.body);
    }
    
    // 启动心跳线程
    void start_heartbeat() {
        if (heartbeat_running_) {
            return;
        }
        
        heartbeat_running_ = true;
        heartbeat_thread_ = std::thread([this]() {
            while (heartbeat_running_) {
                try {
                    json request = {{"id", current_registration_.id}};
                    post("/v1/agent/heartbeat", request.dump());
                } catch (const std::exception& e) {
                    // 忽略心跳错误
                }
                
                std::this_thread::sleep_for(std::chrono::seconds(10));
            }
        });
    }
    
    // 停止心跳线程
    void stop_heartbeat() {
        if (!heartbeat_running_) {
            return;
        }
        
        heartbeat_running_ = false;
        if (heartbeat_thread_.joinable()) {
            heartbeat_thread_.join();
        }
    }
    
    std::string registry_url_;
    AgentRegistration current_registration_;
    std::atomic<bool> heartbeat_running_;
    std::thread heartbeat_thread_;
};
