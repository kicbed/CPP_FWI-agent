/**
 * @file http_bridge.cpp
 * @brief HTTP 桥接服务 - 为 Web 前端提供 HTTP API
 *
 * 在 gRPC Server 同进程中运行一个轻量 HTTP 服务器，
 * 接收浏览器的 JSON 请求，转发到 Orchestrator。
 *
 * 架构:
 *   浏览器 ──HTTP──> http_bridge(:50052) ──A2A/HTTP──> Orchestrator(:5000)
 */

#include "agent_rpc/server/http_bridge.h"
#include "agent_rpc/common/logger.h"
#include <sys/socket.h>
#include <netinet/in.h>
#include <unistd.h>
#include <cstring>
#include <sstream>
#include <thread>
#include <atomic>
#include <regex>

namespace agent_rpc::server {

class HttpBridge::Impl {
public:
    std::atomic<bool> running{false};
    int server_fd_ = -1;
    int port_ = 50052;
    std::string orchestrator_url_ = "http://localhost:5000";
    std::thread server_thread_;

    ~Impl() { stop(); }

    bool start(int port, const std::string& orchestrator_url) {
        port_ = port;
        orchestrator_url_ = orchestrator_url;

        server_fd_ = socket(AF_INET, SOCK_STREAM, 0);
        if (server_fd_ < 0) {
            LOG_ERROR("HTTP Bridge: 无法创建 socket");
            return false;
        }

        int opt = 1;
        setsockopt(server_fd_, SOL_SOCKET, SO_REUSEADDR | SO_REUSEPORT, &opt, sizeof(opt));

        struct sockaddr_in addr;
        addr.sin_family = AF_INET;
        addr.sin_addr.s_addr = INADDR_ANY;
        addr.sin_port = htons(port_);

        if (bind(server_fd_, (struct sockaddr*)&addr, sizeof(addr)) < 0) {
            LOG_ERROR("HTTP Bridge: 无法绑定端口 " + std::to_string(port_));
            close(server_fd_);
            server_fd_ = -1;
            return false;
        }

        if (listen(server_fd_, 10) < 0) {
            LOG_ERROR("HTTP Bridge: 监听失败");
            close(server_fd_);
            server_fd_ = -1;
            return false;
        }

        running = true;
        server_thread_ = std::thread([this]() { acceptLoop(); });

        LOG_INFO("HTTP Bridge 已启动: 0.0.0.0:" + std::to_string(port_));
        return true;
    }

    void stop() {
        running = false;
        if (server_fd_ >= 0) {
            shutdown(server_fd_, SHUT_RDWR);
            close(server_fd_);
            server_fd_ = -1;
        }
        if (server_thread_.joinable()) {
            server_thread_.join();
        }
    }

private:
    void acceptLoop() {
        while (running) {
            struct sockaddr_in client_addr;
            socklen_t client_len = sizeof(client_addr);
            int client_fd = accept(server_fd_, (struct sockaddr*)&client_addr, &client_len);
            if (client_fd < 0) {
                if (running) continue;
                break;
            }

            // Handle each connection in a detached thread
            std::thread([this, client_fd]() {
                handleConnection(client_fd);
                close(client_fd);
            }).detach();
        }
    }

    void handleConnection(int client_fd) {
        char buffer[8192];
        int n = recv(client_fd, buffer, sizeof(buffer) - 1, 0);
        if (n <= 0) return;
        buffer[n] = '\0';

        std::string request(buffer, n);

        // Parse HTTP method and path
        std::string method, path, body;
        parseHttpRequest(request, method, path, body);

        // CORS preflight
        if (method == "OPTIONS") {
            sendHttpResponse(client_fd, 200, "{}", "application/json");
            return;
        }

        // Route: POST /api/query
        if (method == "POST" && path == "/api/query") {
            handleQuery(client_fd, body);
            return;
        }

        // Route: GET /health
        if (method == "GET" && (path == "/health" || path == "/")) {
            sendHttpResponse(client_fd, 200, R"({"status":"ok","service":"grpc-bridge"})", "application/json");
            return;
        }

        sendHttpResponse(client_fd, 404, R"({"error":"not found"})", "application/json");
    }

    void handleQuery(int client_fd, const std::string& body) {
        // Parse JSON: {"question": "...", "context_id": "..."}
        std::string question = extractJsonString(body, "question");
        std::string context_id = extractJsonString(body, "context_id");

        if (question.empty()) {
            sendHttpResponse(client_fd, 400, R"({"error":"missing 'question' field"})", "application/json");
            return;
        }

        // Build JSON-RPC request for Orchestrator
        std::string req_id = "bridge-" + std::to_string(std::chrono::system_clock::now().time_since_epoch().count());

        std::ostringstream params;
        params << R"({"message":{"role":"user","contextId":")" << escapeJson(context_id)
               << R"(","parts":[{"kind":"text","text":")" << escapeJson(question) << R"("}]},"historyLength":5})";

        std::ostringstream jsonrpc;
        jsonrpc << R"({"jsonrpc":"2.0","id":")" << req_id
                << R"(","method":"message/send","params":)" << params.str() << "}";

        // Use system curl for simplicity (avoids linking libcurl here)
        std::string curl_cmd = "curl -s -X POST " + orchestrator_url_
            + " -H 'Content-Type: application/json'"
            + " -d '" + jsonrpc.str() + "'"
            + " --max-time 120";

        FILE* pipe = popen(curl_cmd.c_str(), "r");
        if (!pipe) {
            sendHttpResponse(client_fd, 500, R"({"error":"failed to call orchestrator"})", "application/json");
            return;
        }

        std::string response;
        char buf[4096];
        while (fgets(buf, sizeof(buf), pipe)) {
            response += buf;
        }
        pclose(pipe);

        if (response.empty()) {
            sendHttpResponse(client_fd, 502, R"({"error":"orchestrator returned empty response"})", "application/json");
            return;
        }

        // Extract answer from JSON-RPC response
        std::string answer = extractAnswerFromJsonRpc(response);
        std::string agent_name = extractJsonStringNested(response, "agentName");

        // Build simple JSON response for frontend
        std::ostringstream result;
        result << R"({"answer":")" << escapeJson(answer)
               << R"(","agent_name":")" << escapeJson(agent_name)
               << R"(","context_id":")" << escapeJson(context_id)
               << R"(","status":0})";

        sendHttpResponse(client_fd, 200, result.str(), "application/json");
    }

    void parseHttpRequest(const std::string& req, std::string& method, std::string& path, std::string& body) {
        // Parse request line
        auto first_line_end = req.find("\r\n");
        if (first_line_end == std::string::npos) first_line_end = req.find("\n");
        std::string first_line = req.substr(0, first_line_end);

        auto sp1 = first_line.find(' ');
        auto sp2 = first_line.find(' ', sp1 + 1);
        if (sp1 != std::string::npos && sp2 != std::string::npos) {
            method = first_line.substr(0, sp1);
            path = first_line.substr(sp1 + 1, sp2 - sp1 - 1);
        }

        // Extract body (after blank line)
        auto body_start = req.find("\r\n\r\n");
        if (body_start != std::string::npos) {
            body = req.substr(body_start + 4);
        } else {
            body_start = req.find("\n\n");
            if (body_start != std::string::npos) {
                body = req.substr(body_start + 2);
            }
        }
    }

    void sendHttpResponse(int client_fd, int code, const std::string& body, const std::string& content_type) {
        std::string status_text;
        switch (code) {
            case 200: status_text = "OK"; break;
            case 400: status_text = "Bad Request"; break;
            case 404: status_text = "Not Found"; break;
            case 500: status_text = "Internal Server Error"; break;
            case 502: status_text = "Bad Gateway"; break;
            default: status_text = "Unknown"; break;
        }

        std::ostringstream resp;
        resp << "HTTP/1.1 " << code << " " << status_text << "\r\n"
             << "Content-Type: " << content_type << "\r\n"
             << "Content-Length: " << body.size() << "\r\n"
             << "Access-Control-Allow-Origin: *\r\n"
             << "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
             << "Access-Control-Allow-Headers: Content-Type\r\n"
             << "Connection: close\r\n"
             << "\r\n"
             << body;

        std::string resp_str = resp.str();
        send(client_fd, resp_str.c_str(), resp_str.size(), 0);
    }

    std::string extractJsonString(const std::string& json, const std::string& key) {
        std::string pattern = "\"" + key + "\"";
        auto pos = json.find(pattern);
        if (pos == std::string::npos) return "";

        auto colon = json.find(':', pos + pattern.size());
        if (colon == std::string::npos) return "";

        auto start = json.find('"', colon + 1);
        if (start == std::string::npos) return "";
        start++;

        auto end = json.find('"', start);
        if (end == std::string::npos) return "";

        return json.substr(start, end - start);
    }

    std::string extractJsonStringNested(const std::string& json, const std::string& key) {
        return extractJsonString(json, key);
    }

    std::string extractAnswerFromJsonRpc(const std::string& jsonrpc_response) {
        // Try to extract from result.artifacts[0].parts[0].text
        auto artifacts_pos = jsonrpc_response.find("\"artifacts\"");
        if (artifacts_pos != std::string::npos) {
            auto text_pos = jsonrpc_response.find("\"text\"", artifacts_pos);
            if (text_pos != std::string::npos) {
                auto colon = jsonrpc_response.find(':', text_pos);
                auto start = jsonrpc_response.find('"', colon + 1);
                if (start != std::string::npos) {
                    start++;
                    auto end = findJsonStringEnd(jsonrpc_response, start);
                    if (end != std::string::npos) {
                        return unescapeJson(jsonrpc_response.substr(start, end - start));
                    }
                }
            }
        }

        // Try result.status.message.parts[0].text
        auto status_pos = jsonrpc_response.find("\"status\"");
        if (status_pos != std::string::npos) {
            auto msg_pos = jsonrpc_response.find("\"message\"", status_pos);
            if (msg_pos != std::string::npos) {
                auto text_pos = jsonrpc_response.find("\"text\"", msg_pos);
                if (text_pos != std::string::npos) {
                    auto colon = jsonrpc_response.find(':', text_pos);
                    auto start = jsonrpc_response.find('"', colon + 1);
                    if (start != std::string::npos) {
                        start++;
                        auto end = findJsonStringEnd(jsonrpc_response, start);
                        if (end != std::string::npos) {
                            return unescapeJson(jsonrpc_response.substr(start, end - start));
                        }
                    }
                }
            }
        }

        // Try result.parts[0].text
        auto parts_pos = jsonrpc_response.find("\"parts\"");
        if (parts_pos != std::string::npos) {
            auto text_pos = jsonrpc_response.find("\"text\"", parts_pos);
            if (text_pos != std::string::npos) {
                auto colon = jsonrpc_response.find(':', text_pos);
                auto start = jsonrpc_response.find('"', colon + 1);
                if (start != std::string::npos) {
                    start++;
                    auto end = findJsonStringEnd(jsonrpc_response, start);
                    if (end != std::string::npos) {
                        return unescapeJson(jsonrpc_response.substr(start, end - start));
                    }
                }
            }
        }

        return "无法解析响应";
    }

    size_t findJsonStringEnd(const std::string& str, size_t start) {
        for (size_t i = start; i < str.size(); i++) {
            if (str[i] == '\\') { i++; continue; }
            if (str[i] == '"') return i;
        }
        return std::string::npos;
    }

    std::string escapeJson(const std::string& s) {
        std::string result;
        for (char c : s) {
            switch (c) {
                case '"':  result += "\\\""; break;
                case '\\': result += "\\\\"; break;
                case '\n': result += "\\n"; break;
                case '\r': result += "\\r"; break;
                case '\t': result += "\\t"; break;
                default:   result += c;
            }
        }
        return result;
    }

    std::string unescapeJson(const std::string& s) {
        std::string result;
        for (size_t i = 0; i < s.size(); i++) {
            if (s[i] == '\\' && i + 1 < s.size()) {
                switch (s[i + 1]) {
                    case '"':  result += '"'; i++; break;
                    case '\\': result += '\\'; i++; break;
                    case 'n':  result += '\n'; i++; break;
                    case 'r':  result += '\r'; i++; break;
                    case 't':  result += '\t'; i++; break;
                    default:   result += s[i];
                }
            } else {
                result += s[i];
            }
        }
        return result;
    }
};

// Public API
HttpBridge::HttpBridge() : impl_(std::make_unique<Impl>()) {}
HttpBridge::~HttpBridge() = default;

bool HttpBridge::start(int port, const std::string& orchestrator_url) {
    return impl_->start(port, orchestrator_url);
}

void HttpBridge::stop() {
    impl_->stop();
}

bool HttpBridge::isRunning() const {
    return impl_->running;
}

} // namespace agent_rpc::server
