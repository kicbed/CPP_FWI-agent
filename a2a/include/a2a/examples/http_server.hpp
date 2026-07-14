#pragma once

#include <string>
#include <functional>
#include <map>
#include <thread>
#include <iostream>
#include <sys/socket.h>
#include <sys/time.h>
#include <netinet/in.h>
#include <arpa/inet.h>
#include <unistd.h>
#include <cstdlib>
#include <cstring>
#include <sstream>
#include <stdexcept>
#include <algorithm>
#include <cctype>
#include <cerrno>
#include <limits>
#include <vector>

/**
 * @brief 简单的 HTTP 服务器
 * 用于接收 A2A 协议的 HTTP 请求
 * 支持普通请求和 SSE 流式响应
 */
class HttpServer {
public:
    using RequestHandler = std::function<std::string(const std::string&)>;
    // 流式处理器: 接收请求体和写入回调函数
    using StreamHandler = std::function<void(const std::string&, std::function<bool(const std::string&)>)>;
    
    explicit HttpServer(int port)
        : port_(port),
          running_(false),
          bind_address_(environment_or("AGENT_BIND_HOST", "127.0.0.1")),
          cors_origin_(environment_or("AGENT_CORS_ORIGIN", "http://127.0.0.1:8080")) {
        if (bind_address_ != "127.0.0.1" && bind_address_ != "0.0.0.0") {
            throw std::invalid_argument(
                "AGENT_BIND_HOST must be 127.0.0.1 or 0.0.0.0");
        }
        if (cors_origin_.find_first_of("\r\n") != std::string::npos) {
            throw std::invalid_argument("AGENT_CORS_ORIGIN contains invalid characters");
        }
    }
    
    ~HttpServer() {
        stop();
    }
    
    void register_handler(const std::string& path, RequestHandler handler) {
        handlers_[path] = handler;
    }
    
    /**
     * @brief 注册流式处理器
     * @param path 请求路径
     * @param handler 流式处理函数，接收请求体和写入回调
     */
    void register_stream_handler(const std::string& path, StreamHandler handler) {
        stream_handlers_[path] = handler;
    }

    void start() {
        running_ = true;
        
        // 创建 socket
        int server_fd = socket(AF_INET, SOCK_STREAM, 0);
        if (server_fd < 0) {
            throw std::runtime_error("Failed to create socket");
        }
        
        // 设置 socket 选项
        int opt = 1;
        setsockopt(server_fd, SOL_SOCKET, SO_REUSEADDR, &opt, sizeof(opt));
        
        // 绑定地址
        struct sockaddr_in address;
        address.sin_family = AF_INET;
        if (inet_pton(AF_INET, bind_address_.c_str(), &address.sin_addr) != 1) {
            close(server_fd);
            throw std::runtime_error("Invalid HTTP bind address");
        }
        address.sin_port = htons(port_);
        
        if (bind(server_fd, (struct sockaddr*)&address, sizeof(address)) < 0) {
            close(server_fd);
            throw std::runtime_error("Failed to bind to port " + std::to_string(port_));
        }
        
        // 监听
        if (listen(server_fd, 10) < 0) {
            close(server_fd);
            throw std::runtime_error("Failed to listen on port " + std::to_string(port_));
        }
        
        std::cout << "HTTP Server listening on " << bind_address_ << ':' << port_ << std::endl;
        
        // 接受连接
        while (running_) {
            struct sockaddr_in client_addr;
            socklen_t client_len = sizeof(client_addr);
            
            int client_fd = accept(server_fd, (struct sockaddr*)&client_addr, &client_len);
            if (client_fd < 0) {
                continue;
            }
            
            // 处理请求（在新线程中）
            std::thread([this, client_fd]() {
                this->handle_client(client_fd);
            }).detach();
        }
        
        close(server_fd);
    }
    
    void stop() {
        running_ = false;
    }

private:
    static constexpr std::size_t kMaxHeaderBytes = 16 * 1024;
    static constexpr std::size_t kMaxBodyBytes = 32 * 1024;

    enum class ReadResult { Ok, BadRequest, TooLarge, Disconnected };

    static bool send_all(int fd, const std::string& data) {
        std::size_t offset = 0;
        while (offset < data.size()) {
            const ssize_t written = send(
                fd, data.data() + offset, data.size() - offset, MSG_NOSIGNAL);
            if (written > 0) {
                offset += static_cast<std::size_t>(written);
                continue;
            }
            if (written < 0 && errno == EINTR) continue;
            return false;
        }
        return true;
    }

    static std::string trim_ascii(std::string value) {
        const auto not_space = [](unsigned char c) {
            return std::isspace(c) == 0;
        };
        value.erase(value.begin(), std::find_if(
            value.begin(), value.end(), [&](char c) {
                return not_space(static_cast<unsigned char>(c));
            }));
        value.erase(std::find_if(
            value.rbegin(), value.rend(), [&](char c) {
                return not_space(static_cast<unsigned char>(c));
            }).base(), value.end());
        return value;
    }

    static std::vector<std::string> header_values(
        const std::string& request, const std::string& requested_name) {
        std::vector<std::string> values;
        const auto header_end = request.find("\r\n\r\n");
        if (header_end == std::string::npos) return values;
        std::istringstream headers(request.substr(0, header_end));
        std::string line;
        std::getline(headers, line);  // request line
        while (std::getline(headers, line)) {
            if (!line.empty() && line.back() == '\r') line.pop_back();
            const auto colon = line.find(':');
            if (colon == std::string::npos) continue;
            std::string name = trim_ascii(line.substr(0, colon));
            std::transform(name.begin(), name.end(), name.begin(),
                           [](unsigned char value) {
                               return static_cast<char>(std::tolower(value));
                           });
            if (name == requested_name) {
                values.push_back(trim_ascii(line.substr(colon + 1)));
            }
        }
        return values;
    }

    static bool is_json_content_type(const std::string& value) {
        std::string media_type = value.substr(0, value.find(';'));
        media_type = trim_ascii(std::move(media_type));
        std::transform(media_type.begin(), media_type.end(), media_type.begin(),
                       [](unsigned char character) {
                           return static_cast<char>(std::tolower(character));
                       });
        return media_type == "application/json";
    }

    static ReadResult read_request(int fd, std::string* request) {
        struct timeval timeout {};
        timeout.tv_sec = 10;
        setsockopt(fd, SOL_SOCKET, SO_RCVTIMEO, &timeout, sizeof(timeout));

        std::size_t expected_size = 0;
        bool parsed_headers = false;
        char buffer[4096];
        while (true) {
            const ssize_t count = recv(fd, buffer, sizeof(buffer), 0);
            if (count == 0) return ReadResult::Disconnected;
            if (count < 0) {
                if (errno == EINTR) continue;
                return ReadResult::Disconnected;
            }
            request->append(buffer, static_cast<std::size_t>(count));

            const std::size_t header_end = request->find("\r\n\r\n");
            if (!parsed_headers) {
                if (header_end == std::string::npos) {
                    if (request->size() > kMaxHeaderBytes) {
                        return ReadResult::TooLarge;
                    }
                    continue;
                }
                if (header_end + 4 > kMaxHeaderBytes) {
                    return ReadResult::TooLarge;
                }

                std::size_t content_length = 0;
                bool saw_content_length = false;
                bool unsupported_transfer_encoding = false;
                std::istringstream headers(request->substr(0, header_end));
                std::string line;
                std::getline(headers, line);  // request line
                while (std::getline(headers, line)) {
                    if (!line.empty() && line.back() == '\r') line.pop_back();
                    const auto colon = line.find(':');
                    if (colon == std::string::npos) continue;
                    std::string name = trim_ascii(line.substr(0, colon));
                    std::transform(name.begin(), name.end(), name.begin(),
                                   [](unsigned char c) {
                                       return static_cast<char>(std::tolower(c));
                                   });
                    const std::string value = trim_ascii(line.substr(colon + 1));
                    if (name == "transfer-encoding" && !value.empty() &&
                        value != "identity") {
                        unsupported_transfer_encoding = true;
                    }
                    if (name != "content-length") continue;
                    if (value.empty() || !std::all_of(
                            value.begin(), value.end(), [](unsigned char c) {
                                return std::isdigit(c) != 0;
                            })) {
                        return ReadResult::BadRequest;
                    }
                    unsigned long long parsed = 0;
                    try {
                        parsed = std::stoull(value);
                    } catch (const std::exception&) {
                        return ReadResult::BadRequest;
                    }
                    if (parsed > kMaxBodyBytes) return ReadResult::TooLarge;
                    if (saw_content_length && content_length != parsed) {
                        return ReadResult::BadRequest;
                    }
                    content_length = static_cast<std::size_t>(parsed);
                    saw_content_length = true;
                }
                if (unsupported_transfer_encoding) return ReadResult::BadRequest;
                expected_size = header_end + 4 + content_length;
                parsed_headers = true;
            }

            if (request->size() >= expected_size) {
                request->resize(expected_size);
                return ReadResult::Ok;
            }
            if (request->size() > kMaxHeaderBytes + kMaxBodyBytes) {
                return ReadResult::TooLarge;
            }
        }
    }

    static void send_simple_error(int fd, int status, const char* reason) {
        const std::string body = std::string("{\"error\":\"") + reason + "\"}";
        std::ostringstream response;
        response << "HTTP/1.1 " << status << ' ' << reason << "\r\n"
                 << "Content-Type: application/json\r\n"
                 << "Connection: close\r\n"
                 << "Content-Length: " << body.size() << "\r\n\r\n"
                 << body;
        send_all(fd, response.str());
    }

    void handle_client(int client_fd) {
        std::string request;
        const ReadResult read_result = read_request(client_fd, &request);
        if (read_result != ReadResult::Ok) {
            if (read_result == ReadResult::TooLarge) {
                send_simple_error(client_fd, 413, "Payload Too Large");
            } else if (read_result == ReadResult::BadRequest) {
                send_simple_error(client_fd, 400, "Bad Request");
            }
            close(client_fd);
            return;
        }
        
        // 解析 HTTP 请求
        std::istringstream request_stream(request);
        std::string method, path, version;
        request_stream >> method >> path >> version;
        
        // 提取请求体
        std::string body;
        size_t body_pos = request.find("\r\n\r\n");
        if (body_pos != std::string::npos) {
            body = request.substr(body_pos + 4);
        }

        const auto origins = header_values(request, "origin");
        if (origins.size() > 1 ||
            (!origins.empty() &&
             (cors_origin_.empty() || origins.front() != cors_origin_))) {
            send_simple_error(client_fd, 403, "Forbidden Origin");
            close(client_fd);
            return;
        }

        if (method == "POST") {
            const auto content_types = header_values(request, "content-type");
            if (content_types.size() != 1 ||
                !is_json_content_type(content_types.front())) {
                send_simple_error(client_fd, 415, "Unsupported Media Type");
                close(client_fd);
                return;
            }
        } else if (method != "GET" && method != "OPTIONS") {
            send_simple_error(client_fd, 405, "Method Not Allowed");
            close(client_fd);
            return;
        }

        // CORS 预检请求 (OPTIONS)
        if (method == "OPTIONS") {
            std::ostringstream response;
            response << "HTTP/1.1 204 No Content\r\n";
            append_cors_headers(response);
            response << "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n";
            response << "Access-Control-Allow-Headers: Content-Type, Accept\r\n";
            response << "Access-Control-Max-Age: 86400\r\n";
            response << "Content-Length: 0\r\n";
            response << "\r\n";
            std::string response_str = response.str();
            send_all(client_fd, response_str);
            close(client_fd);
            return;
        }

        // 健康检查端点 (GET /health)
        if (method == "GET" && (path == "/health" || path == "/")) {
            std::string health_body = "{\"status\":\"ok\"}";
            std::ostringstream response;
            response << "HTTP/1.1 200 OK\r\n";
            response << "Content-Type: application/json\r\n";
            response << "Content-Length: " << health_body.length() << "\r\n";
            append_cors_headers(response);
            response << "\r\n";
            response << health_body;
            std::string response_str = response.str();
            send_all(client_fd, response_str);
            close(client_fd);
            return;
        }

        // 检查是否需要流式响应（通过检查请求体中的 method）
        bool is_stream_request = false;
        if (!body.empty()) {
            // 简单检查是否包含 message/stream 方法
            is_stream_request = (body.find("\"message/stream\"") != std::string::npos);
        }

        // 优先检查流式处理器
        auto stream_it = stream_handlers_.find(path);
        if (is_stream_request && stream_it != stream_handlers_.end()) {
            handle_stream_request(client_fd, body, stream_it->second);
            return;
        }

        // 查找普通处理器
        std::string response_body;
        int status_code = 200;

        auto it = handlers_.find(path);
        if (it != handlers_.end()) {
            try {
                response_body = it->second(body);
            } catch (const std::exception& e) {
                status_code = 500;
                response_body = std::string("{\"error\":\"") + e.what() + "\"}";
            }
        } else {
            status_code = 404;
            response_body = "{\"error\":\"Not Found\"}";
        }

        // 构造 HTTP 响应
        std::ostringstream response;
        response << "HTTP/1.1 " << status_code << " OK\r\n";
        response << "Content-Type: application/json\r\n";
        response << "Content-Length: " << response_body.length() << "\r\n";
        append_cors_headers(response);
        response << "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n";
        response << "Access-Control-Allow-Headers: Content-Type, Accept\r\n";
        response << "\r\n";
        response << response_body;

        std::string response_str = response.str();
        send_all(client_fd, response_str);

        close(client_fd);
    }
    
    /**
     * @brief 处理流式请求 (SSE - Server-Sent Events)
     */
    void handle_stream_request(int client_fd, const std::string& body, StreamHandler& handler) {
        // 发送 SSE 响应头
        std::ostringstream header;
        header << "HTTP/1.1 200 OK\r\n";
        header << "Content-Type: text/event-stream\r\n";
        header << "Cache-Control: no-cache\r\n";
        header << "Connection: keep-alive\r\n";
        append_cors_headers(header);
        header << "Access-Control-Allow-Methods: GET, POST, OPTIONS\r\n";
        header << "Access-Control-Allow-Headers: Content-Type, Accept\r\n";
        header << "\r\n";
        
        std::string header_str = header.str();
        if (!send_all(client_fd, header_str)) {
            close(client_fd);
            return;
        }
        
        // 调用流式处理器，传入写入回调
        try {
            handler(body, [client_fd](const std::string& event_data) -> bool {
                // 格式化为 SSE 事件
                std::string sse_event = "data: " + event_data + "\n\n";
                return send_all(client_fd, sse_event);
            });
        } catch (const std::exception& e) {
            // 发送错误事件
            std::string error_event = "data: {\"error\":\"" + std::string(e.what()) + "\"}\n\n";
            send_all(client_fd, error_event);
        }
        
        close(client_fd);
    }

    static std::string environment_or(const char* name, const char* fallback) {
        const char* value = std::getenv(name);
        return value != nullptr && *value != '\0' ? value : fallback;
    }

    void append_cors_headers(std::ostringstream& response) const {
        if (!cors_origin_.empty()) {
            response << "Access-Control-Allow-Origin: " << cors_origin_ << "\r\n";
            response << "Vary: Origin\r\n";
        }
    }

    int port_;
    bool running_;
    std::string bind_address_;
    std::string cors_origin_;
    std::map<std::string, RequestHandler> handlers_;
    std::map<std::string, StreamHandler> stream_handlers_;
};
