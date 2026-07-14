#include <a2a/models/agent_message.hpp>
#include <a2a/models/message_part.hpp>
#include <a2a/core/jsonrpc_request.hpp>
#include <a2a/core/jsonrpc_response.hpp>
#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <string>
#include <atomic>
#include <chrono>
#include <cstdint>

using namespace a2a;
using json = nlohmann::json;

static std::string generate_context_id() {
    static std::atomic<std::uint64_t> sequence{0};
    const auto now = std::chrono::system_clock::now().time_since_epoch().count();
    return "ctx-cli-" + std::to_string(now) + "-" +
           std::to_string(sequence.fetch_add(1, std::memory_order_relaxed) + 1);
}

static size_t WriteCallback(void* contents, size_t size, size_t nmemb, std::string* userp) {
    userp->append((char*)contents, size * nmemb);
    return size * nmemb;
}

class A2AClient {
public:
    explicit A2AClient(const std::string& server_url) : server_url_(server_url) {
        curl_global_init(CURL_GLOBAL_DEFAULT);
    }

    ~A2AClient() {
        curl_global_cleanup();
    }

    std::string send_message(const std::string& text, const std::string& context_id) {
        json request = {
            {"jsonrpc", "2.0"},
            {"id", std::to_string(++request_id_)},
            {"method", "message/send"},
            {"params", {
                {"message", {
                    {"role", "user"},
                    {"contextId", context_id},
                    {"parts", {{{"kind", "text"}, {"text", text}}}}
                }},
                {"historyLength", 10}
            }}
        };

        std::string response_body = post(request.dump());

        try {
            auto response_json = json::parse(response_body);
            if (response_json.contains("error")) {
                return "错误: " + response_json["error"]["message"].get<std::string>();
            }
            if (response_json.contains("result") &&
                response_json["result"].contains("parts") &&
                !response_json["result"]["parts"].empty()) {
                return response_json["result"]["parts"][0]["text"].get<std::string>();
            }
            return "无法解析响应";
        } catch (const std::exception& e) {
            return "解析错误: " + std::string(e.what());
        }
    }

    std::string get_agent_card() {
        CURL* curl = curl_easy_init();
        if (!curl) return "{}";
        std::string url = server_url_ + "/.well-known/agent-card.json";
        std::string response;
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 5L);
        curl_easy_perform(curl);
        curl_easy_cleanup(curl);
        return response;
    }

private:
    std::string post(const std::string& body) {
        CURL* curl = curl_easy_init();
        if (!curl) return "";
        std::string response;
        curl_easy_setopt(curl, CURLOPT_URL, server_url_.c_str());
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
        struct curl_slist* headers = nullptr;
        headers = curl_slist_append(headers, "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 60L);
        CURLcode res = curl_easy_perform(curl);
        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);
        if (res != CURLE_OK) {
            return "{\"error\": {\"message\": \"" + std::string(curl_easy_strerror(res)) + "\"}}";
        }
        return response;
    }

    std::string server_url_;
    int request_id_ = 0;
};

void print_help() {
    std::cout << "\n命令:" << std::endl;
    std::cout << "  /help     - 显示帮助" << std::endl;
    std::cout << "  /card     - 获取 Agent Card" << std::endl;
    std::cout << "  /context <id> - 切换上下文" << std::endl;
    std::cout << "  /quit     - 退出" << std::endl;
    std::cout << "\n直接输入文本发送消息给 AI\n" << std::endl;
}

int main(int argc, char* argv[]) {
    std::string server_url = "http://localhost:5000";
    if (argc > 1) server_url = argv[1];

    std::cout << "AI Agent 交互客户端" << std::endl;
    std::cout << "连接到: " << server_url << std::endl;
    print_help();

    A2AClient client(server_url);
    std::string context_id = generate_context_id();

    std::string line;
    while (true) {
        std::cout << "[" << context_id << "] > ";
        std::getline(std::cin, line);

        if (line.empty()) continue;

        if (line == "/quit" || line == "/exit") {
            std::cout << "再见!" << std::endl;
            break;
        }

        if (line == "/help") {
            print_help();
            continue;
        }

        if (line == "/card") {
            std::cout << "\nAgent Card:\n" << client.get_agent_card() << "\n" << std::endl;
            continue;
        }

        if (line.substr(0, 9) == "/context ") {
            context_id = line.substr(9);
            std::cout << "切换到上下文: " << context_id << std::endl;
            continue;
        }

        std::cout << "\n思考中..." << std::endl;
        std::string response = client.send_message(line, context_id);
        std::cout << "\nAI: " << response << "\n" << std::endl;
    }

    return 0;
}
