#include "agent_rpc/registry/service_registry.h"
#include "agent_rpc/common/logger.h"
#include <curl/curl.h>
#include <json/json.h>
#include <sstream>
#include <thread>
#include <chrono>

namespace agent_rpc {
namespace registry {

// ConsulServiceRegistry 实现
ConsulServiceRegistry::ConsulServiceRegistry() {
    curl_global_init(CURL_GLOBAL_DEFAULT);
}

ConsulServiceRegistry::~ConsulServiceRegistry() {
    stopHealthCheck();
    curl_global_cleanup();
}

bool ConsulServiceRegistry::initialize(const std::string& consul_address) {
    consul_address_ = consul_address;
    LOG_INFO("Consul service registry initialized with address: " + consul_address);
    return true;
}

bool ConsulServiceRegistry::registerService(const common::ServiceEndpoint& endpoint) {
    std::string service_id = getServiceId(endpoint);

    Json::Value service_json;
    service_json["ID"] = service_id;
    service_json["Name"] = endpoint.service_name;
    service_json["Address"] = endpoint.host;
    service_json["Port"] = endpoint.port;
    service_json["Tags"] = Json::Value(Json::arrayValue);
    service_json["Meta"] = Json::Value(Json::objectValue);

    for (const auto& pair : endpoint.metadata) {
        service_json["Meta"][pair.first] = pair.second;
    }

    Json::StreamWriterBuilder builder;
    std::string json_string = Json::writeString(builder, service_json);

    std::string url = "http://" + consul_address_ + "/v1/agent/service/register";
    std::string response = makeHttpRequest("PUT", url, json_string);

    if (response.empty()) {
        LOG_ERROR("Failed to register service: " + service_id);
        return false;
    }

    {
        std::lock_guard<std::mutex> lock(services_mutex_);
        registered_services_[service_id] = endpoint;
    }

    LOG_INFO("Service registered: " + service_id);
    return true;
}

bool ConsulServiceRegistry::unregisterService(const std::string& service_id) {
    std::string url = "http://" + consul_address_ + "/v1/agent/service/deregister/" + service_id;
    std::string response = makeHttpRequest("PUT", url);

    {
        std::lock_guard<std::mutex> lock(services_mutex_);
        registered_services_.erase(service_id);
    }

    LOG_INFO("Service unregistered: " + service_id);
    return true;
}

std::vector<common::ServiceEndpoint> ConsulServiceRegistry::discoverServices(const std::string& service_name) {
    std::string url = "http://" + consul_address_ + "/v1/health/service/" + service_name;
    std::string response = makeHttpRequest("GET", url);

    if (response.empty()) {
        LOG_ERROR("Failed to discover services: " + service_name);
        return {};
    }

    std::vector<common::ServiceEndpoint> services = parseServiceList(response);

    {
        std::lock_guard<std::mutex> lock(services_mutex_);
        discovered_services_[service_name] = services;
    }

    return services;
}

bool ConsulServiceRegistry::isServiceHealthy(const std::string& service_id) {
    std::string url = "http://" + consul_address_ + "/v1/agent/health/service/id/" + service_id;
    std::string response = makeHttpRequest("GET", url);

    if (response.empty()) {
        return false;
    }

    Json::Value root;
    Json::Reader reader;
    if (reader.parse(response, root)) {
        return root["Status"].asString() == "passing";
    }

    return false;
}

bool ConsulServiceRegistry::updateHeartbeat(const std::string& service_id) {
    std::string url = "http://" + consul_address_ + "/v1/agent/check/pass/service:" + service_id;
    std::string response = makeHttpRequest("PUT", url);

    return !response.empty();
}

void ConsulServiceRegistry::watchServices(const std::string& service_name,
                                        std::function<void(const std::vector<common::ServiceEndpoint>&)> callback) {
    std::lock_guard<std::mutex> lock(watchers_mutex_);
    watchers_[service_name] = callback;
}

void ConsulServiceRegistry::startHealthCheck() {
    if (health_check_running_) {
        return;
    }

    health_check_running_ = true;
    health_check_thread_ = std::thread([this]() {
        healthCheckLoop();
    });
}

void ConsulServiceRegistry::stopHealthCheck() {
    if (health_check_running_) {
        health_check_running_ = false;
        if (health_check_thread_.joinable()) {
            health_check_thread_.join();
        }
    }
}

std::string ConsulServiceRegistry::getServiceId(const common::ServiceEndpoint& endpoint) const {
    return endpoint.service_name + "-" + endpoint.host + "-" + std::to_string(endpoint.port);
}

void ConsulServiceRegistry::healthCheckLoop() {
    while (health_check_running_) {
        {
            std::lock_guard<std::mutex> lock(services_mutex_);
            for (const auto& pair : registered_services_) {
                updateHeartbeat(pair.first);
            }
        }

        std::this_thread::sleep_for(std::chrono::seconds(30));
    }
}

std::string ConsulServiceRegistry::makeHttpRequest(const std::string& method,
                                                  const std::string& url,
                                                  const std::string& body) {
    CURL* curl = curl_easy_init();
    if (!curl) {
        return "";
    }

    std::string response_data;

    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, [](void* contents, size_t size, size_t nmemb, std::string* data) {
        data->append((char*)contents, size * nmemb);
        return size * nmemb;
    });
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_data);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);

    if (method == "PUT") {
        curl_easy_setopt(curl, CURLOPT_CUSTOMREQUEST, "PUT");
        if (!body.empty()) {
            curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
        }
    }

    CURLcode res = curl_easy_perform(curl);
    curl_easy_cleanup(curl);

    if (res != CURLE_OK) {
        LOG_ERROR("HTTP request failed: " + std::string(curl_easy_strerror(res)));
        return "";
    }

    return response_data;
}

std::vector<common::ServiceEndpoint> ConsulServiceRegistry::parseServiceList(const std::string& json_response) {
    std::vector<common::ServiceEndpoint> services;

    Json::Value root;
    Json::Reader reader;
    if (!reader.parse(json_response, root)) {
        LOG_ERROR("Failed to parse service list JSON");
        return services;
    }

    for (const auto& service : root) {
        common::ServiceEndpoint endpoint;
        endpoint.host = service["Service"]["Address"].asString();
        endpoint.port = service["Service"]["Port"].asInt();
        endpoint.service_name = service["Service"]["Name"].asString();
        endpoint.is_healthy = service["Checks"][0]["Status"].asString() == "passing";

        const auto& meta = service["Service"]["Meta"];
        for (const auto& key : meta.getMemberNames()) {
            endpoint.metadata[key] = meta[key].asString();
        }

        services.push_back(endpoint);
    }

    return services;
}

common::ServiceEndpoint ConsulServiceRegistry::parseServiceEndpoint(const std::string& json_service) {
    common::ServiceEndpoint endpoint;

    Json::Value root;
    Json::Reader reader;
    if (reader.parse(json_service, root)) {
        endpoint.host = root["Address"].asString();
        endpoint.port = root["Port"].asInt();
        endpoint.service_name = root["Name"].asString();

        const auto& meta = root["Meta"];
        for (const auto& key : meta.getMemberNames()) {
            endpoint.metadata[key] = meta[key].asString();
        }
    }

    return endpoint;
}

// EtcdServiceRegistry 实现
EtcdServiceRegistry::EtcdServiceRegistry() = default;

EtcdServiceRegistry::~EtcdServiceRegistry() {
    if (watch_running_) {
        watch_running_ = false;
        if (watch_thread_.joinable()) {
            watch_thread_.join();
        }
    }
}

bool EtcdServiceRegistry::initialize(const std::string& etcd_address) {
    etcd_address_ = etcd_address;
    LOG_INFO("Etcd service registry initialized with address: " + etcd_address);
    return true;
}

bool EtcdServiceRegistry::registerService(const common::ServiceEndpoint& endpoint) {
    // 简化的etcd注册实现
    std::string service_key = "/services/" + endpoint.service_name + "/" +
                             endpoint.host + ":" + std::to_string(endpoint.port);

    Json::Value service_json;
    service_json["host"] = endpoint.host;
    service_json["port"] = endpoint.port;
    service_json["service_name"] = endpoint.service_name;
    service_json["version"] = endpoint.version;
    service_json["metadata"] = Json::Value(Json::objectValue);

    for (const auto& pair : endpoint.metadata) {
        service_json["metadata"][pair.first] = pair.second;
    }

    Json::StreamWriterBuilder builder;
    std::string json_string = Json::writeString(builder, service_json);

    std::string response = makeEtcdRequest("PUT", service_key, json_string);

    {
        std::lock_guard<std::mutex> lock(services_mutex_);
        registered_services_[service_key] = endpoint;
    }

    LOG_INFO("Service registered in etcd: " + service_key);
    return !response.empty();
}

bool EtcdServiceRegistry::unregisterService(const std::string& service_id) {
    std::string service_key = "/services/" + service_id;
    std::string response = makeEtcdRequest("DELETE", service_key);

    {
        std::lock_guard<std::mutex> lock(services_mutex_);
        registered_services_.erase(service_key);
    }

    LOG_INFO("Service unregistered from etcd: " + service_key);
    return !response.empty();
}

std::vector<common::ServiceEndpoint> EtcdServiceRegistry::discoverServices(const std::string& service_name) {
    std::string service_prefix = "/services/" + service_name + "/";
    std::string response = makeEtcdRequest("GET", service_prefix);

    std::vector<common::ServiceEndpoint> services = parseEtcdResponse(response);

    {
        std::lock_guard<std::mutex> lock(services_mutex_);
        discovered_services_[service_name] = services;
    }

    return services;
}

bool EtcdServiceRegistry::isServiceHealthy(const std::string& service_id) {
    // 简化的健康检查实现
    return true;
}

bool EtcdServiceRegistry::updateHeartbeat(const std::string& service_id) {
    // 简化的心跳实现
    return true;
}

void EtcdServiceRegistry::watchServices(const std::string& service_name,
                                       std::function<void(const std::vector<common::ServiceEndpoint>&)> callback) {
    std::lock_guard<std::mutex> lock(watchers_mutex_);
    watchers_[service_name] = callback;
}

void EtcdServiceRegistry::watchLoop() {
    // 简化的监听实现
    while (watch_running_) {
        std::this_thread::sleep_for(std::chrono::seconds(10));
    }
}

std::string EtcdServiceRegistry::makeEtcdRequest(const std::string& method,
                                                const std::string& key,
                                                const std::string& value) {
    // 简化的etcd请求实现
    return "OK";
}

std::vector<common::ServiceEndpoint> EtcdServiceRegistry::parseEtcdResponse(const std::string& response) {
    // 简化的响应解析实现
    return {};
}

// MemoryServiceRegistry 实现
bool MemoryServiceRegistry::registerService(const common::ServiceEndpoint& endpoint) {
    std::string service_id = endpoint.host + ":" + std::to_string(endpoint.port);
    std::vector<common::ServiceEndpoint> snapshot;
    std::function<void(const std::vector<common::ServiceEndpoint>&)> watcher;

    {
        std::lock_guard<std::mutex> lock(services_mutex_);
        services_[service_id] = endpoint;
        for (const auto& pair : services_) {
            if (pair.second.service_name == endpoint.service_name) {
                snapshot.push_back(pair.second);
            }
        }
    }

    {
        std::lock_guard<std::mutex> lock(watchers_mutex_);
        auto it = watchers_.find(endpoint.service_name);
        if (it != watchers_.end()) {
            watcher = it->second;
        }
    }

    LOG_INFO("Service registered in memory: " + service_id);
    if (watcher) {
        watcher(snapshot);
    }
    return true;
}

bool MemoryServiceRegistry::unregisterService(const std::string& service_id) {
    std::string service_name;
    std::vector<common::ServiceEndpoint> snapshot;
    std::function<void(const std::vector<common::ServiceEndpoint>&)> watcher;

    {
        std::lock_guard<std::mutex> lock(services_mutex_);

        auto it = services_.find(service_id);
        if (it == services_.end()) {
            return false;
        }

        service_name = it->second.service_name;
        services_.erase(it);
        for (const auto& pair : services_) {
            if (pair.second.service_name == service_name) {
                snapshot.push_back(pair.second);
            }
        }
    }

    {
        std::lock_guard<std::mutex> lock(watchers_mutex_);
        auto it = watchers_.find(service_name);
        if (it != watchers_.end()) {
            watcher = it->second;
        }
    }

    LOG_INFO("Service unregistered from memory: " + service_id);
    if (watcher) {
        watcher(snapshot);
    }
    return true;
}

std::vector<common::ServiceEndpoint> MemoryServiceRegistry::discoverServices(const std::string& service_name) {
    std::lock_guard<std::mutex> lock(services_mutex_);

    std::vector<common::ServiceEndpoint> result;
    for (const auto& pair : services_) {
        if (pair.second.service_name == service_name) {
            result.push_back(pair.second);
        }
    }

    return result;
}

bool MemoryServiceRegistry::isServiceHealthy(const std::string& service_id) {
    std::lock_guard<std::mutex> lock(services_mutex_);

    auto it = services_.find(service_id);
    return it != services_.end() && it->second.is_healthy;
}

bool MemoryServiceRegistry::updateHeartbeat(const std::string& service_id) {
    std::lock_guard<std::mutex> lock(services_mutex_);

    auto it = services_.find(service_id);
    if (it != services_.end()) {
        it->second.last_heartbeat = std::chrono::steady_clock::now();
        return true;
    }

    return false;
}

void MemoryServiceRegistry::watchServices(const std::string& service_name,
                                        std::function<void(const std::vector<common::ServiceEndpoint>&)> callback) {
    std::lock_guard<std::mutex> lock(watchers_mutex_);
    watchers_[service_name] = callback;
}

} // namespace registry
} // namespace agent_rpc
