/**
 * @file embedding_service.cpp
 * @brief EmbeddingService 实现
 * 
 * Requirements: 1.2, 2.1, 2.2, 2.3, 2.4
 * Task 2: 实现 EmbeddingService
 */

#include "agent_rpc/mcp/rag/embedding_service.h"
#include "agent_rpc/common/logger.h"

#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <cstdlib>
#include <cmath>
#include <thread>
#include <sstream>
#include <stdexcept>

using json = nlohmann::json;

namespace agent_rpc {
namespace mcp {
namespace rag {

// ============================================================================
// EmbeddingConfig 实现
// ============================================================================

bool EmbeddingConfig::loadApiKeyFromEnv() {
    const char* key = std::getenv("DASHSCOPE_API_KEY");
    if (key && key[0] != '\0') {
        api_key = key;
        return true;
    }
    return false;
}

bool EmbeddingConfig::validate() const {
    if (api_key.empty()) {
        return false;
    }
    if (model.empty()) {
        return false;
    }
    if (dimension <= 0) {
        return false;
    }
    if (max_retries < 0) {
        return false;
    }
    if (timeout_ms <= 0) {
        return false;
    }
    return true;
}

// ============================================================================
// EmbeddingService 实现
// ============================================================================

EmbeddingService::EmbeddingService(const EmbeddingConfig& config)
    : config_(config) {
    // 如果没有提供 API Key，尝试从环境变量加载
    if (config_.api_key.empty()) {
        config_.loadApiKeyFromEnv();
    }
    
    curl_global_init(CURL_GLOBAL_DEFAULT);
}

EmbeddingService::~EmbeddingService() {
    curl_global_cleanup();
}

std::vector<float> EmbeddingService::embed(const std::string& text) {
    auto results = embedBatch({text});
    if (results.empty()) {
        throw std::runtime_error("Empty embedding result");
    }
    return results[0];
}

std::vector<std::vector<float>> EmbeddingService::embedBatch(
    const std::vector<std::string>& texts) {
    
    if (texts.empty()) {
        return {};
    }
    
    if (!config_.validate()) {
        throw std::runtime_error("Invalid EmbeddingConfig: API key may be missing");
    }
    
    // 构造请求 JSON
    // DashScope text-embedding API 格式: {"input": {"texts": ["text1", "text2", ...]}}
    json texts_array = json::array();
    for (const auto& text : texts) {
        // 确保文本不为空
        if (!text.empty()) {
            texts_array.push_back(text);
        } else {
            texts_array.push_back(" ");  // 空文本用空格替代
        }
    }
    
    json request_body = {
        {"model", config_.model},
        {"input", {
            {"texts", texts_array}
        }},
        {"parameters", {
            {"text_type", "query"}
        }}
    };
    
    std::string request_str = request_body.dump();
    
    // 调用 API（带重试）
    std::string response = callApiWithRetry(request_str);
    
    // 解析响应
    return parseEmbeddingResponse(response);
}

float EmbeddingService::cosineSimilarity(
    const std::vector<float>& a, 
    const std::vector<float>& b) {
    
    if (a.size() != b.size() || a.empty()) {
        return 0.0f;
    }
    
    float dot_product = 0.0f;
    float norm_a = 0.0f;
    float norm_b = 0.0f;
    
    for (size_t i = 0; i < a.size(); ++i) {
        dot_product += a[i] * b[i];
        norm_a += a[i] * a[i];
        norm_b += b[i] * b[i];
    }
    
    if (norm_a == 0.0f || norm_b == 0.0f) {
        return 0.0f;
    }
    
    return dot_product / (std::sqrt(norm_a) * std::sqrt(norm_b));
}

// CURL 回调函数
static size_t writeCallback(void* contents, size_t size, size_t nmemb, void* userp) {
    ((std::string*)userp)->append((char*)contents, size * nmemb);
    return size * nmemb;
}

std::string EmbeddingService::sendPostRequest(const std::string& data) {
    CURL* curl = curl_easy_init();
    if (!curl) {
        throw std::runtime_error("Failed to initialize CURL");
    }
    
    std::string response_data;
    
    // 设置请求头
    struct curl_slist* headers = nullptr;
    headers = curl_slist_append(headers, "Content-Type: application/json");
    std::string auth_header = "Authorization: Bearer " + config_.api_key;
    headers = curl_slist_append(headers, auth_header.c_str());
    
    // 配置 CURL
    curl_easy_setopt(curl, CURLOPT_URL, config_.api_url.c_str());
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, data.c_str());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, writeCallback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_data);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT_MS, config_.timeout_ms);
    
    // 执行请求
    CURLcode res = curl_easy_perform(curl);
    
    // 清理
    curl_slist_free_all(headers);
    curl_easy_cleanup(curl);
    
    if (res != CURLE_OK) {
        throw std::runtime_error(std::string("CURL error: ") + curl_easy_strerror(res));
    }
    
    return response_data;
}

std::string EmbeddingService::callApiWithRetry(const std::string& request_body) {
    last_retry_stats_ = RetryStats{};
    
    std::exception_ptr last_exception;
    
    for (int attempt = 0; attempt <= config_.max_retries; ++attempt) {
        last_retry_stats_.total_attempts++;
        
        if (attempt > 0) {
            // 计算退避延迟
            int delay_ms = calculateBackoffDelay(attempt);
            last_retry_stats_.retry_delays_ms.push_back(delay_ms);
            
            LOG_WARN("Embedding API retry " + std::to_string(attempt) + 
                    "/" + std::to_string(config_.max_retries) + 
                    ", delay: " + std::to_string(delay_ms) + "ms");
            
            // 调用回调（用于测试）
            if (retry_callback_) {
                retry_callback_(attempt, delay_ms);
            }
            
            std::this_thread::sleep_for(std::chrono::milliseconds(delay_ms));
        }
        
        try {
            std::string response = sendPostRequest(request_body);
            last_retry_stats_.successful_attempts++;
            return response;
        } catch (const std::exception& e) {
            last_retry_stats_.failed_attempts++;
            last_exception = std::current_exception();
            LOG_ERROR("Embedding API call failed: " + std::string(e.what()));
        }
    }
    
    // 所有重试都失败
    if (last_exception) {
        std::rethrow_exception(last_exception);
    }
    
    throw std::runtime_error("Embedding API call failed after all retries");
}

int EmbeddingService::calculateBackoffDelay(int attempt) const {
    // 指数退避: delay = initial_delay * 2^(attempt-1)
    // 加上一些随机抖动
    int base_delay = config_.initial_retry_delay_ms * (1 << (attempt - 1));
    
    // 添加 0-25% 的随机抖动
    int jitter = (std::rand() % (base_delay / 4 + 1));
    
    return base_delay + jitter;
}

std::vector<std::vector<float>> EmbeddingService::parseEmbeddingResponse(
    const std::string& response) {
    
    try {
        json response_json = json::parse(response);
        
        // 检查错误
        if (response_json.contains("code")) {
            std::string error_msg = "DashScope API Error: " + 
                response_json.value("message", "Unknown error");
            throw std::runtime_error(error_msg);
        }
        
        // 提取 embeddings
        std::vector<std::vector<float>> results;
        
        if (response_json.contains("output") && 
            response_json["output"].contains("embeddings")) {
            
            for (const auto& item : response_json["output"]["embeddings"]) {
                if (item.contains("embedding")) {
                    std::vector<float> embedding;
                    for (const auto& val : item["embedding"]) {
                        embedding.push_back(val.get<float>());
                    }
                    results.push_back(embedding);
                }
            }
        }
        
        if (results.empty()) {
            throw std::runtime_error("No embeddings in response");
        }
        
        return results;
        
    } catch (const json::exception& e) {
        throw std::runtime_error(std::string("JSON parse error: ") + e.what());
    }
}

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
