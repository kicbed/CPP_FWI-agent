/**
 * @file http_bridge.cpp
 * @brief HTTP 桥接服务 - 为 Web 前端提供 HTTP API
 *
 * 在 gRPC Server 同进程中运行一个轻量 HTTP 服务器，
 * 接收浏览器的 JSON 请求，转换为 AIQueryService gRPC 调用。
 *
 * 架构:
 *   浏览器 ──HTTP──> http_bridge(:50052) ──gRPC──> AIQueryService(:50051)
 */

#include "agent_rpc/server/http_bridge.h"
#include "agent_rpc/common/logger.h"
#include "ai_query.grpc.pb.h"
#include <grpcpp/grpcpp.h>
#include <nlohmann/json.hpp>
#include <arpa/inet.h>
#include <sys/socket.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <unistd.h>
#include <algorithm>
#include <chrono>
#include <cctype>
#include <cerrno>
#include <cstdlib>
#include <cstring>
#include <limits>
#include <mutex>
#include <sstream>
#include <thread>
#include <atomic>
#include <vector>

namespace agent_rpc::server {

class HttpBridge::Impl {
public:
    static constexpr std::size_t kMaxHttpRequestBytes = 32 * 1024;
    static constexpr std::size_t kMaxHttpHeaderBytes = 16 * 1024;
    static constexpr std::size_t kMaxQuestionBytes = 8 * 1024;
    static constexpr std::size_t kMaxContextIdBytes = 128;
    static constexpr std::size_t kMaxWorkers = 32;

    std::atomic<bool> running{false};
    int server_fd_ = -1;
    int port_ = 50052;
    std::string grpc_target_ = "127.0.0.1:50051";
    std::shared_ptr<grpc::Channel> grpc_channel_;
    std::unique_ptr<agent_communication::AIQueryService::Stub> grpc_stub_;
    std::string bind_host_ = "127.0.0.1";
    std::string cors_origin_ = "http://127.0.0.1:8080";
    std::thread server_thread_;
    struct Worker {
        std::thread thread;
        std::shared_ptr<std::atomic<bool>> done;
    };
    std::mutex workers_mutex_;
    std::vector<Worker> workers_;

    ~Impl() { stop(); }

    bool start(int port, const std::string& grpc_target) {
        port_ = port;
        grpc_target_ = grpc_target;

        if (grpc_target_.empty() || grpc_target_.find_first_of("\r\n") != std::string::npos) {
            LOG_ERROR("HTTP Bridge: invalid gRPC target");
            return false;
        }

        grpc_channel_ = grpc::CreateChannel(
            grpc_target_, grpc::InsecureChannelCredentials());
        if (!grpc_channel_) {
            LOG_ERROR("HTTP Bridge: failed to create gRPC channel to " + grpc_target_);
            return false;
        }
        grpc_stub_ = agent_communication::AIQueryService::NewStub(grpc_channel_);
        if (!grpc_stub_) {
            LOG_ERROR("HTTP Bridge: failed to create AIQueryService stub");
            grpc_channel_.reset();
            return false;
        }

        if (const char* value = std::getenv("HTTP_BRIDGE_BIND_HOST")) {
            if (*value != '\0') bind_host_ = value;
        }
        if (const char* value = std::getenv("GRPC_BRIDGE_CORS_ORIGIN")) {
            if (*value != '\0') cors_origin_ = value;
        }
        if ((bind_host_ != "127.0.0.1" && bind_host_ != "0.0.0.0") ||
            cors_origin_ == "*" ||
            cors_origin_.find_first_of("\r\n") != std::string::npos) {
            LOG_ERROR("HTTP Bridge: invalid bind host or CORS origin");
            return false;
        }

        server_fd_ = socket(AF_INET, SOCK_STREAM, 0);
        if (server_fd_ < 0) {
            LOG_ERROR("HTTP Bridge: 无法创建 socket");
            return false;
        }

        int opt = 1;
        setsockopt(server_fd_, SOL_SOCKET, SO_REUSEADDR | SO_REUSEPORT, &opt, sizeof(opt));

        struct sockaddr_in addr;
        addr.sin_family = AF_INET;
        if (inet_pton(AF_INET, bind_host_.c_str(), &addr.sin_addr) != 1) {
            LOG_ERROR("HTTP Bridge: invalid IPv4 bind address");
            close(server_fd_);
            server_fd_ = -1;
            return false;
        }
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

        LOG_INFO("HTTP-to-gRPC Bridge 已启动: " + bind_host_ + ":" +
                 std::to_string(port_) + " -> " + grpc_target_);
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
        reapWorkers(true);
        grpc_stub_.reset();
        grpc_channel_.reset();
    }

private:
    enum class ReadRequestResult {
        Ok,
        BadRequest,
        TooLarge
    };

    void acceptLoop() {
        while (running) {
            struct sockaddr_in client_addr;
            socklen_t client_len = sizeof(client_addr);
            int client_fd = accept(server_fd_, (struct sockaddr*)&client_addr, &client_len);
            if (client_fd < 0) {
                if (running) continue;
                break;
            }

            reapWorkers(false);

            bool at_capacity = false;
            {
                std::lock_guard<std::mutex> lock(workers_mutex_);
                at_capacity = workers_.size() >= kMaxWorkers;
                if (!at_capacity) {
                    auto done = std::make_shared<std::atomic<bool>>(false);
                    workers_.push_back(Worker{
                        std::thread([this, client_fd, done]() {
                            handleConnection(client_fd);
                            close(client_fd);
                            done->store(true, std::memory_order_release);
                        }),
                        std::move(done)});
                }
            }

            if (at_capacity) {
                const nlohmann::json error = {
                    {"error", {
                        {"type", "bridge_busy"},
                        {"message", "HTTP-to-gRPC bridge is at connection capacity"}
                    }},
                    {"status", 503},
                    {"transport", "grpc"}
                };
                sendHttpResponse(client_fd, 503, error.dump(), "application/json");
                close(client_fd);
            }
        }
    }

    void reapWorkers(bool join_all) {
        std::vector<std::thread> threads_to_join;
        {
            std::lock_guard<std::mutex> lock(workers_mutex_);
            auto worker = workers_.begin();
            while (worker != workers_.end()) {
                const bool done = worker->done->load(std::memory_order_acquire);
                if (join_all || done) {
                    threads_to_join.push_back(std::move(worker->thread));
                    worker = workers_.erase(worker);
                } else {
                    ++worker;
                }
            }
        }
        for (auto& thread : threads_to_join) {
            if (thread.joinable()) thread.join();
        }
    }

    void handleConnection(int client_fd) {
        // Do not let a client that opens a socket without sending data block
        // bridge shutdown forever.
        struct timeval timeout {5, 0};
        setsockopt(client_fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

        std::string request;
        const ReadRequestResult read_result = readHttpRequest(client_fd, request);
        if (read_result == ReadRequestResult::TooLarge) {
            const nlohmann::json error = {
                {"error", {
                    {"type", "request_too_large"},
                    {"message", "HTTP request exceeds the bridge size limit"}
                }},
                {"status", 413},
                {"transport", "grpc"}
            };
            sendHttpResponse(client_fd, 413, error.dump(), "application/json");
            return;
        }
        if (read_result != ReadRequestResult::Ok) {
            const nlohmann::json error = {
                {"error", {
                    {"type", "invalid_http_request"},
                    {"message", "Malformed or incomplete HTTP request"}
                }},
                {"status", 400},
                {"transport", "grpc"}
            };
            sendHttpResponse(client_fd, 400, error.dump(), "application/json");
            return;
        }

        // Parse HTTP method and path
        std::string method, path, body;
        parseHttpRequest(request, method, path, body);

        const auto origins = headerValues(request, "origin");
        if (origins.size() > 1 ||
            (!origins.empty() && origins.front() != cors_origin_)) {
            const nlohmann::json error = {
                {"error", {{"type", "forbidden_origin"},
                           {"message", "Request Origin is not allowed"}}},
                {"status", 403}, {"transport", "grpc"}
            };
            sendHttpResponse(client_fd, 403, error.dump(), "application/json");
            return;
        }

        if (method == "POST") {
            const auto content_types = headerValues(request, "content-type");
            if (content_types.size() != 1 ||
                !isJsonContentType(content_types.front())) {
                const nlohmann::json error = {
                    {"error", {{"type", "unsupported_media_type"},
                               {"message", "POST requires application/json"}}},
                    {"status", 415}, {"transport", "grpc"}
                };
                sendHttpResponse(client_fd, 415, error.dump(), "application/json");
                return;
            }
        }

        // CORS preflight
        if (method == "OPTIONS") {
            sendHttpResponse(client_fd, 200, nlohmann::json::object().dump(),
                             "application/json");
            return;
        }

        // Route: POST /api/query
        if (method == "POST" && path == "/api/query") {
            handleQuery(client_fd, body);
            return;
        }

        // Route: GET /health
        if (method == "GET" && (path == "/health" || path == "/")) {
            const nlohmann::json health = {
                {"status", "ok"},
                {"service", "http-to-grpc-bridge"},
                {"transport", "grpc"},
                {"grpc_target", grpc_target_}
            };
            sendHttpResponse(client_fd, 200, health.dump(), "application/json");
            return;
        }

        const nlohmann::json error = {
            {"error", {
                {"type", "not_found"},
                {"message", "Route not found"}
            }},
            {"status", 404},
            {"transport", "grpc"}
        };
        sendHttpResponse(client_fd, 404, error.dump(), "application/json");
    }

    void handleQuery(int client_fd, const std::string& body) {
        nlohmann::json payload;
        try {
            payload = nlohmann::json::parse(body);
        } catch (const nlohmann::json::parse_error&) {
            sendJsonRequestError(client_fd, "invalid_json",
                                 "Request body must be valid JSON");
            return;
        }

        if (!payload.is_object()) {
            sendJsonRequestError(client_fd, "invalid_payload",
                                 "Request body must be a JSON object");
            return;
        }

        const auto question_it = payload.find("question");
        if (question_it == payload.end() || !question_it->is_string()) {
            sendJsonRequestError(client_fd, "invalid_question",
                                 "'question' must be a string");
            return;
        }
        const std::string question = question_it->get<std::string>();
        if (question.empty() || question.size() > kMaxQuestionBytes) {
            sendJsonRequestError(client_fd, "invalid_question",
                                 "'question' must contain 1 to 8192 bytes");
            return;
        }

        std::string context_id;
        const auto context_it = payload.find("context_id");
        if (context_it != payload.end()) {
            if (!context_it->is_string()) {
                sendJsonRequestError(client_fd, "invalid_context_id",
                                     "'context_id' must be a string when provided");
                return;
            }
            context_id = context_it->get<std::string>();
        }
        const bool valid_context_id = context_id.empty() ||
            (context_id.size() <= kMaxContextIdBytes &&
             std::isalnum(static_cast<unsigned char>(context_id.front())) &&
             std::all_of(context_id.begin(), context_id.end(), [](unsigned char c) {
                 return std::isalnum(c) || c == '-' || c == '_';
             }));
        if (!valid_context_id) {
            sendJsonRequestError(client_fd, "invalid_context_id",
                                 "'context_id' must be empty or match [A-Za-z0-9][A-Za-z0-9_-]{0,127}");
            return;
        }

        bool disable_legacy_fwi_submit = false;
        const auto legacy_submit_it = payload.find("allow_legacy_fwi_submit");
        if (legacy_submit_it != payload.end()) {
            if (!legacy_submit_it->is_boolean()) {
                sendJsonRequestError(
                    client_fd, "invalid_allow_legacy_fwi_submit",
                    "'allow_legacy_fwi_submit' must be a boolean when provided");
                return;
            }
            if (legacy_submit_it->get<bool>()) {
                sendJsonRequestError(
                    client_fd, "invalid_allow_legacy_fwi_submit",
                    "'allow_legacy_fwi_submit' may only be false when provided");
                return;
            }
            disable_legacy_fwi_submit = true;
        }

        if (!grpc_stub_) {
            sendGrpcError(client_fd, grpc::StatusCode::UNAVAILABLE,
                          "AIQueryService gRPC client is not available");
            return;
        }

        std::string req_id = "bridge-" + std::to_string(std::chrono::system_clock::now().time_since_epoch().count());

        agent_communication::AIQueryRequest request;
        request.set_request_id(req_id);
        request.set_question(question);
        request.set_context_id(context_id);
        request.set_history_length(10);
        request.set_timeout_seconds(60);
        if (disable_legacy_fwi_submit) {
            (*request.mutable_metadata())["allow_legacy_fwi_submit"] = "false";
        }

        grpc::ClientContext grpc_context;
        grpc_context.set_deadline(
            std::chrono::system_clock::now() + std::chrono::seconds(60));

        agent_communication::AIQueryResponse response;
        const grpc::Status rpc_status = grpc_stub_->Query(
            &grpc_context, request, &response);
        if (!rpc_status.ok()) {
            LOG_ERROR("HTTP Bridge: AIQueryService gRPC call failed: " +
                      rpc_status.error_message());
            sendGrpcError(client_fd, rpc_status.error_code(),
                          rpc_status.error_message().empty()
                              ? "AIQueryService gRPC call failed"
                              : rpc_status.error_message());
            return;
        }

        if (response.status().code() != 0) {
            const std::string message = response.status().message().empty()
                ? "AIQueryService returned an application error"
                : response.status().message();
            LOG_ERROR("HTTP Bridge: AIQueryService application error: " + message);
            sendApplicationError(client_fd, response.status().code(), message,
                                 response.status().details(), response.request_id());
            return;
        }

        // Build simple JSON response for frontend
        const nlohmann::json result = {
            {"answer", response.answer()},
            {"agent_name", response.agent_name()},
            {"context_id", response.context_id().empty()
                               ? context_id : response.context_id()},
            {"request_id", response.request_id()},
            {"status", 0},
            {"transport", "grpc"}
        };

        sendHttpResponse(client_fd, 200, result.dump(), "application/json");
    }

    static std::string trimAscii(std::string value) {
        const auto is_space = [](unsigned char c) { return std::isspace(c) != 0; };
        value.erase(value.begin(),
                    std::find_if(value.begin(), value.end(),
                                 [&](char c) { return !is_space(static_cast<unsigned char>(c)); }));
        value.erase(std::find_if(value.rbegin(), value.rend(),
                                 [&](char c) { return !is_space(static_cast<unsigned char>(c)); })
                        .base(),
                    value.end());
        return value;
    }

    static std::string lowerAscii(std::string value) {
        std::transform(value.begin(), value.end(), value.begin(), [](unsigned char c) {
            return static_cast<char>(std::tolower(c));
        });
        return value;
    }

    static std::vector<std::string> headerValues(
        const std::string& request, const std::string& requested_name) {
        std::vector<std::string> values;
        std::size_t header_end = request.find("\r\n\r\n");
        if (header_end == std::string::npos) header_end = request.find("\n\n");
        if (header_end == std::string::npos) return values;
        const std::string headers = request.substr(0, header_end);
        std::size_t line_start = headers.find('\n');
        if (line_start != std::string::npos) ++line_start;
        while (line_start != std::string::npos && line_start < headers.size()) {
            std::size_t line_end = headers.find('\n', line_start);
            if (line_end == std::string::npos) line_end = headers.size();
            const std::string line = headers.substr(line_start, line_end - line_start);
            const auto colon = line.find(':');
            if (colon != std::string::npos &&
                lowerAscii(trimAscii(line.substr(0, colon))) == requested_name) {
                values.push_back(trimAscii(line.substr(colon + 1)));
            }
            line_start = line_end == headers.size()
                ? std::string::npos : line_end + 1;
        }
        return values;
    }

    static bool isJsonContentType(const std::string& value) {
        return lowerAscii(trimAscii(value.substr(0, value.find(';')))) ==
               "application/json";
    }

    static bool parseSize(const std::string& text, std::size_t& value) {
        if (text.empty()) return false;
        std::size_t parsed = 0;
        for (const unsigned char c : text) {
            if (!std::isdigit(c)) return false;
            const std::size_t digit = static_cast<std::size_t>(c - '0');
            if (parsed > (std::numeric_limits<std::size_t>::max() - digit) / 10) {
                return false;
            }
            parsed = parsed * 10 + digit;
        }
        value = parsed;
        return true;
    }

    ReadRequestResult readHttpRequest(int client_fd, std::string& request) {
        std::size_t expected_size = 0;
        bool headers_parsed = false;

        while (true) {
            char buffer[4096];
            const ssize_t bytes = recv(client_fd, buffer, sizeof(buffer), 0);
            if (bytes < 0) {
                if (errno == EINTR) continue;
                return ReadRequestResult::BadRequest;
            }
            if (bytes == 0) return ReadRequestResult::BadRequest;

            request.append(buffer, static_cast<std::size_t>(bytes));
            if (request.size() > kMaxHttpRequestBytes) {
                return ReadRequestResult::TooLarge;
            }

            if (!headers_parsed) {
                std::size_t delimiter_size = 4;
                std::size_t header_end = request.find("\r\n\r\n");
                if (header_end == std::string::npos) {
                    delimiter_size = 2;
                    header_end = request.find("\n\n");
                }
                if (header_end == std::string::npos) {
                    if (request.size() > kMaxHttpHeaderBytes) {
                        return ReadRequestResult::TooLarge;
                    }
                    continue;
                }
                if (header_end > kMaxHttpHeaderBytes) {
                    return ReadRequestResult::TooLarge;
                }

                const std::string headers = request.substr(0, header_end);
                std::size_t content_length = 0;
                bool has_content_length = false;
                std::size_t line_start = headers.find('\n');
                if (line_start != std::string::npos) ++line_start;

                while (line_start != std::string::npos && line_start < headers.size()) {
                    std::size_t line_end = headers.find('\n', line_start);
                    if (line_end == std::string::npos) line_end = headers.size();
                    const std::string line = headers.substr(line_start, line_end - line_start);
                    const std::size_t colon = line.find(':');
                    if (colon == std::string::npos) {
                        return ReadRequestResult::BadRequest;
                    }
                    const std::string name = lowerAscii(trimAscii(line.substr(0, colon)));
                    const std::string value = trimAscii(line.substr(colon + 1));
                    if (name == "transfer-encoding") {
                        // Chunked request decoding is intentionally unsupported. Reject it
                        // instead of ambiguously combining it with Content-Length.
                        return ReadRequestResult::BadRequest;
                    }
                    if (name == "content-length") {
                        std::size_t parsed_length = 0;
                        if (!parseSize(value, parsed_length) ||
                            (has_content_length && parsed_length != content_length)) {
                            return ReadRequestResult::BadRequest;
                        }
                        content_length = parsed_length;
                        has_content_length = true;
                    }
                    line_start = line_end == headers.size()
                        ? std::string::npos : line_end + 1;
                }

                const std::size_t body_start = header_end + delimiter_size;
                if (content_length > kMaxHttpRequestBytes - body_start) {
                    return ReadRequestResult::TooLarge;
                }
                expected_size = body_start + content_length;
                headers_parsed = true;
            }

            if (headers_parsed && request.size() >= expected_size) {
                request.resize(expected_size);
                return ReadRequestResult::Ok;
            }
        }
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
            case 403: status_text = "Forbidden"; break;
            case 405: status_text = "Method Not Allowed"; break;
            case 413: status_text = "Payload Too Large"; break;
            case 415: status_text = "Unsupported Media Type"; break;
            case 404: status_text = "Not Found"; break;
            case 500: status_text = "Internal Server Error"; break;
            case 502: status_text = "Bad Gateway"; break;
            case 503: status_text = "Service Unavailable"; break;
            default: status_text = "Unknown"; break;
        }

        std::ostringstream resp;
        resp << "HTTP/1.1 " << code << " " << status_text << "\r\n"
             << "Content-Type: " << content_type << "\r\n"
             << "Content-Length: " << body.size() << "\r\n"
             << "Access-Control-Allow-Origin: " << cors_origin_ << "\r\n"
             << "Vary: Origin\r\n"
             << "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n"
             << "Access-Control-Allow-Headers: Content-Type\r\n"
             << "Connection: close\r\n"
             << "\r\n"
             << body;

        std::string resp_str = resp.str();
        std::size_t sent = 0;
        while (sent < resp_str.size()) {
            const ssize_t bytes = send(client_fd, resp_str.data() + sent,
                                       resp_str.size() - sent, MSG_NOSIGNAL);
            if (bytes < 0) {
                if (errno == EINTR) continue;
                break;
            }
            if (bytes == 0) break;
            sent += static_cast<std::size_t>(bytes);
        }
    }

    void sendGrpcError(int client_fd, grpc::StatusCode code,
                       const std::string& message) {
        const nlohmann::json error = {
            {"error", {
                {"type", "grpc_transport_error"},
                {"code", static_cast<int>(code)},
                {"message", message}
            }},
            {"status", 502},
            {"transport", "grpc"}
        };
        sendHttpResponse(client_fd, 502, error.dump(), "application/json");
    }

    void sendApplicationError(int client_fd, int code,
                              const std::string& message,
                              const std::string& details,
                              const std::string& request_id) {
        const nlohmann::json error = {
            {"error", {
                {"type", "grpc_application_error"},
                {"code", code},
                {"message", message},
                {"details", details}
            }},
            {"request_id", request_id},
            {"status", 502},
            {"transport", "grpc"}
        };
        sendHttpResponse(client_fd, 502, error.dump(), "application/json");
    }

    void sendJsonRequestError(int client_fd, const std::string& type,
                              const std::string& message) {
        const nlohmann::json error = {
            {"error", {
                {"type", type},
                {"message", message}
            }},
            {"status", 400},
            {"transport", "grpc"}
        };
        sendHttpResponse(client_fd, 400, error.dump(), "application/json");
    }

};

// Public API
HttpBridge::HttpBridge() : impl_(std::make_unique<Impl>()) {}
HttpBridge::~HttpBridge() = default;

bool HttpBridge::start(int port, const std::string& grpc_target) {
    return impl_->start(port, grpc_target);
}

void HttpBridge::stop() {
    impl_->stop();
}

bool HttpBridge::isRunning() const {
    return impl_->running;
}

} // namespace agent_rpc::server
