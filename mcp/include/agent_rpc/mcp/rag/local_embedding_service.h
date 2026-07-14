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
#include <stdexcept>
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
        if (!isLoopbackBaseUrl(config_.api_url)) {
            throw std::invalid_argument(
                "Local embedding URL must be loopback HTTP with an explicit port");
        }
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
    static constexpr std::size_t kMaxResponseBytes = 4U * 1024U * 1024U;

    struct ResponseBuffer {
        std::string body;
        bool too_large = false;
    };

    static bool isLoopbackBaseUrl(const std::string& url) {
        std::string port;
        if (url.rfind("http://127.0.0.1:", 0) == 0) {
            port = url.substr(std::string("http://127.0.0.1:").size());
        } else if (url.rfind("http://localhost:", 0) == 0) {
            port = url.substr(std::string("http://localhost:").size());
        } else {
            return false;
        }
        if (port.empty() || port.find_first_not_of("0123456789") != std::string::npos) {
            return false;
        }
        try {
            const unsigned long parsed = std::stoul(port);
            return parsed > 0 && parsed <= 65535;
        } catch (const std::exception&) {
            return false;
        }
    }

    static size_t WriteCallback(void* contents, size_t size, size_t nmemb,
                                ResponseBuffer* output) {
        const std::size_t bytes = size * nmemb;
        if (bytes > kMaxResponseBytes - output->body.size()) {
            output->too_large = true;
            return 0;
        }
        output->body.append(static_cast<const char*>(contents), bytes);
        return bytes;
    }

    std::string sendPostRequest(const std::string& url, const std::string& data) {
        CURL* curl = curl_easy_init();
        if (!curl) throw std::runtime_error("Failed to initialize CURL");

        ResponseBuffer response;
        struct curl_slist* headers = nullptr;
        headers = curl_slist_append(headers, "Content-Type: application/json");

        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, data.c_str());
        curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE_LARGE,
                         static_cast<curl_off_t>(data.size()));
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, config_.timeout_ms);
        curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS, 2000L);
        curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
        curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);
        curl_easy_setopt(curl, CURLOPT_PROTOCOLS, CURLPROTO_HTTP);
        curl_easy_setopt(curl, CURLOPT_NOPROXY, "*");

        CURLcode res = curl_easy_perform(curl);
        long status = 0;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);

        if (response.too_large) {
            throw std::runtime_error("Local embedding response exceeded 4 MiB");
        }
        if (res != CURLE_OK) {
            throw std::runtime_error(std::string("CURL error: ") + curl_easy_strerror(res));
        }
        if (status != 200) {
            throw std::runtime_error(
                "Local embedding HTTP status: " + std::to_string(status));
        }

        return response.body;
    }

    std::string sendGetRequest(const std::string& url) {
        CURL* curl = curl_easy_init();
        if (!curl) throw std::runtime_error("Failed to initialize CURL");

        ResponseBuffer response;
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, 5000);
        curl_easy_setopt(curl, CURLOPT_CONNECTTIMEOUT_MS, 2000L);
        curl_easy_setopt(curl, CURLOPT_NOSIGNAL, 1L);
        curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 0L);
        curl_easy_setopt(curl, CURLOPT_PROTOCOLS, CURLPROTO_HTTP);
        curl_easy_setopt(curl, CURLOPT_NOPROXY, "*");

        CURLcode res = curl_easy_perform(curl);
        long status = 0;
        curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status);
        curl_easy_cleanup(curl);

        if (response.too_large) {
            throw std::runtime_error("Local embedding response exceeded 4 MiB");
        }
        if (res != CURLE_OK) {
            throw std::runtime_error(std::string("CURL error: ") + curl_easy_strerror(res));
        }
        if (status != 200) {
            throw std::runtime_error(
                "Local embedding HTTP status: " + std::to_string(status));
        }

        return response.body;
    }

    LocalEmbeddingConfig config_;
};

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
