/**
 * @file local_embedding_service.h
 * @brief 本地 Embedding 服务客户端
 *
 * 调用本地 sentence-transformers HTTP 服务生成向量。
 * 替代 DashScope API，节省成本。
 */

#pragma once

#include <string>
#include <vector>
#include <curl/curl.h>
#include <nlohmann/json.hpp>

using json = nlohmann::json;

namespace agent_rpc {
namespace mcp {
namespace rag {

/**
 * @brief 本地 Embedding 服务配置
 */
struct LocalEmbeddingConfig {
    std::string api_url = "http://localhost:6000";  // 服务地址
    int dimension = 1024;                           // 向量维度
    int timeout_ms = 30000;                         // 超时时间
};

/**
 * @brief 本地 Embedding 服务客户端
 *
 * 调用本地 sentence-transformers HTTP 服务。
 */
class LocalEmbeddingService {
public:
    explicit LocalEmbeddingService(const LocalEmbeddingConfig& config = LocalEmbeddingConfig())
        : config_(config) {
        curl_global_init(CURL_GLOBAL_DEFAULT);
    }

    ~LocalEmbeddingService() {
        curl_global_cleanup();
    }

    /**
     * @brief 生成单个文本的向量
     * @param text 输入文本
     * @return 向量
     */
    std::vector<float> embed(const std::string& text) {
        json request_body = {{"text", text}};
        std::string response = sendPostRequest(config_.api_url + "/embed", request_body.dump());

        auto response_json = json::parse(response);
        if (response_json.contains("error")) {
            throw std::runtime_error("Embedding error: " + response_json["error"].get<std::string>());
        }

        auto embedding = response_json["embedding"].get<std::vector<float>>();
        return embedding;
    }

    /**
     * @brief 批量生成向量
     * @param texts 输入文本列表
     * @return 向量列表
     */
    std::vector<std::vector<float>> embedBatch(const std::vector<std::string>& texts) {
        json request_body = {{"texts", texts}};
        std::string response = sendPostRequest(config_.api_url + "/embed_batch", request_body.dump());

        auto response_json = json::parse(response);
        if (response_json.contains("error")) {
            throw std::runtime_error("Embedding error: " + response_json["error"].get<std::string>());
        }

        return response_json["embeddings"].get<std::vector<std::vector<float>>>();
    }

    /**
     * @brief 检查服务是否可用
     */
    bool isAvailable() {
        try {
            std::string response = sendGetRequest(config_.api_url + "/health");
            auto response_json = json::parse(response);
            return response_json.value("status", "") == "ok";
        } catch (...) {
            return false;
        }
    }

    /**
     * @brief 获取向量维度
     */
    int getDimension() {
        try {
            std::string response = sendGetRequest(config_.api_url + "/health");
            auto response_json = json::parse(response);
            return response_json.value("dimension", config_.dimension);
        } catch (...) {
            return config_.dimension;
        }
    }

private:
    static size_t WriteCallback(void* contents, size_t size, size_t nmemb, std::string* userp) {
        userp->append((char*)contents, size * nmemb);
        return size * nmemb;
    }

    std::string sendPostRequest(const std::string& url, const std::string& data) {
        CURL* curl = curl_easy_init();
        if (!curl) throw std::runtime_error("Failed to initialize CURL");

        std::string response;
        struct curl_slist* headers = nullptr;
        headers = curl_slist_append(headers, "Content-Type: application/json");

        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, data.c_str());
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, config_.timeout_ms);

        CURLcode res = curl_easy_perform(curl);
        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);

        if (res != CURLE_OK) {
            throw std::runtime_error(std::string("CURL error: ") + curl_easy_strerror(res));
        }

        return response;
    }

    std::string sendGetRequest(const std::string& url) {
        CURL* curl = curl_easy_init();
        if (!curl) throw std::runtime_error("Failed to initialize CURL");

        std::string response;
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, 5000);

        CURLcode res = curl_easy_perform(curl);
        curl_easy_cleanup(curl);

        if (res != CURLE_OK) {
            throw std::runtime_error(std::string("CURL error: ") + curl_easy_strerror(res));
        }

        return response;
    }

    LocalEmbeddingConfig config_;
};

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
