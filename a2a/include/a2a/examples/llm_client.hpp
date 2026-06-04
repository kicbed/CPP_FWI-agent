/**
 * @file llm_client.hpp
 * @brief 通用 LLM 客户端 - 支持多种 API 提供商
 */

#pragma once

#include <string>
#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <sstream>
#include <stdexcept>

// 使用 config.h 中定义的 LLMProvider
#include "agent_rpc/orchestrator/config.h"

using json = nlohmann::json;

/**
 * @brief 通用 LLM 客户端
 */
class LLMClient {
public:
    explicit LLMClient(const std::string& api_key,
                       LLMProvider provider = LLMProvider::DEEPSEEK,
                       const std::string& model = "",
                       const std::string& api_url = "")
        : api_key_(api_key), provider_(provider) {

        switch (provider) {
            case LLMProvider::DEEPSEEK:
                model_ = model.empty() ? "deepseek-chat" : model;
                api_url_ = api_url.empty() ? "https://api.deepseek.com/v1/chat/completions" : api_url;
                break;
            case LLMProvider::QWEN:
                model_ = model.empty() ? "qwen-plus" : model;
                api_url_ = api_url.empty() ? "https://dashscope.aliyuncs.com/api/v1/services/aigc/text-generation/generation" : api_url;
                break;
            case LLMProvider::OPENAI:
                model_ = model.empty() ? "gpt-4o-mini" : model;
                api_url_ = api_url.empty() ? "https://api.openai.com/v1/chat/completions" : api_url;
                break;
            case LLMProvider::LOCAL:
                model_ = model.empty() ? "qwen2.5:7b" : model;
                api_url_ = api_url.empty() ? "http://localhost:11434/v1/chat/completions" : api_url;
                break;
        }

        curl_global_init(CURL_GLOBAL_DEFAULT);
    }

    ~LLMClient() {
        curl_global_cleanup();
    }

    std::string chat(const std::string& system_prompt, const std::string& user_message) {
        json request_body;
        std::string auth_header;

        switch (provider_) {
            case LLMProvider::DEEPSEEK:
            case LLMProvider::OPENAI:
            case LLMProvider::LOCAL:
                request_body = {
                    {"model", model_},
                    {"messages", json::array({
                        {{"role", "system"}, {"content", system_prompt}},
                        {{"role", "user"}, {"content", user_message}}
                    })},
                    {"max_tokens", 2000},
                    {"temperature", 0.7}
                };
                auth_header = "Authorization: Bearer " + api_key_;
                break;
            case LLMProvider::QWEN:
                request_body = {
                    {"model", model_},
                    {"input", {
                        {"messages", json::array({
                            {{"role", "system"}, {"content", system_prompt}},
                            {{"role", "user"}, {"content", user_message}}
                        })}
                    }},
                    {"parameters", {{"result_format", "message"}}}
                };
                auth_header = "Authorization: Bearer " + api_key_;
                break;
        }

        std::string response = send_request(request_body.dump(), auth_header);
        return parse_response(response);
    }

private:
    std::string send_request(const std::string& body, const std::string& auth_header) {
        CURL* curl = curl_easy_init();
        if (!curl) throw std::runtime_error("Failed to initialize CURL");

        std::string response;
        struct curl_slist* headers = nullptr;
        headers = curl_slist_append(headers, "Content-Type: application/json");
        headers = curl_slist_append(headers, auth_header.c_str());

        curl_easy_setopt(curl, CURLOPT_URL, api_url_.c_str());
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_callback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 120L);

        CURLcode res = curl_easy_perform(curl);
        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);

        if (res != CURLE_OK) {
            throw std::runtime_error(std::string("CURL error: ") + curl_easy_strerror(res));
        }

        return response;
    }

    std::string parse_response(const std::string& response) {
        auto response_json = json::parse(response);

        if (response_json.contains("error")) {
            std::string error_msg = response_json["error"].value("message", "Unknown error");
            throw std::runtime_error("API Error: " + error_msg);
        }

        // OpenAI 格式
        if (provider_ == LLMProvider::DEEPSEEK || provider_ == LLMProvider::OPENAI || provider_ == LLMProvider::LOCAL) {
            if (response_json.contains("choices") && !response_json["choices"].empty()) {
                auto& choice = response_json["choices"][0];
                if (choice.contains("message") && choice["message"].contains("content")) {
                    return choice["message"]["content"].get<std::string>();
                }
            }
        }

        // 通义千问格式
        if (provider_ == LLMProvider::QWEN) {
            if (response_json.contains("output") && response_json["output"].contains("choices") &&
                !response_json["output"]["choices"].empty()) {
                auto& choice = response_json["output"]["choices"][0];
                if (choice.contains("message") && choice["message"].contains("content")) {
                    return choice["message"]["content"].get<std::string>();
                }
            }
        }

        throw std::runtime_error("Invalid response format");
    }

    static size_t write_callback(void* contents, size_t size, size_t nmemb, void* userp) {
        ((std::string*)userp)->append((char*)contents, size * nmemb);
        return size * nmemb;
    }

    std::string api_key_;
    LLMProvider provider_;
    std::string model_;
    std::string api_url_;
};
