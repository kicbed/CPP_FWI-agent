#include <a2a/core/http_client.hpp>
#include <a2a/core/exception.hpp>
#include <curl/curl.h>
#include <sstream>
#include <cstring>

namespace a2a {

// Callback for writing response data
static size_t write_callback(void* contents, size_t size, size_t nmemb, void* userp) {
    size_t total_size = size * nmemb;
    std::string* response = static_cast<std::string*>(userp);
    response->append(static_cast<char*>(contents), total_size);
    return total_size;
}

/**
 * @brief SSE流式数据处理上下文
 * 
 * 解决问题：CURL按网络包边界切分数据，可能在UTF-8多字节字符中间切断
 * 解决方案：按SSE事件边界（双换行符）切分，确保完整的JSON事件
 */
struct StreamContext {
    std::function<void(const std::string&)>* callback;
    std::string buffer;  // 累积缓冲区，存储不完整的事件数据
    std::string last_error;  // 存储最后一个错误
    
    /**
     * @brief 检查字符串是否是有效的 UTF-8
     * 如果字符串末尾有不完整的 UTF-8 序列，返回有效部分的长度
     */
    static size_t find_valid_utf8_end(const std::string& str) {
        if (str.empty()) return 0;
        
        size_t len = str.length();
        size_t i = len;
        
        // 从末尾向前查找，检查是否有不完整的 UTF-8 序列
        // UTF-8 编码规则：
        // - 单字节: 0xxxxxxx (0x00-0x7F)
        // - 双字节起始: 110xxxxx (0xC0-0xDF)
        // - 三字节起始: 1110xxxx (0xE0-0xEF)
        // - 四字节起始: 11110xxx (0xF0-0xF7)
        // - 后续字节: 10xxxxxx (0x80-0xBF)
        
        // 向前查找最多 4 个字节（UTF-8 最大长度）
        size_t check_start = (len > 4) ? len - 4 : 0;
        
        for (i = len; i > check_start; ) {
            i--;
            unsigned char c = static_cast<unsigned char>(str[i]);
            
            if ((c & 0x80) == 0) {
                // 单字节字符 (ASCII)，之后的都是有效的
                return len;
            } else if ((c & 0xC0) == 0x80) {
                // 后续字节，继续向前查找起始字节
                continue;
            } else if ((c & 0xE0) == 0xC0) {
                // 双字节起始，检查是否完整
                size_t expected_len = 2;
                size_t actual_len = len - i;
                if (actual_len >= expected_len) {
                    return len;  // 完整
                } else {
                    return i;  // 不完整，返回起始位置之前
                }
            } else if ((c & 0xF0) == 0xE0) {
                // 三字节起始，检查是否完整
                size_t expected_len = 3;
                size_t actual_len = len - i;
                if (actual_len >= expected_len) {
                    return len;  // 完整
                } else {
                    return i;  // 不完整
                }
            } else if ((c & 0xF8) == 0xF0) {
                // 四字节起始，检查是否完整
                size_t expected_len = 4;
                size_t actual_len = len - i;
                if (actual_len >= expected_len) {
                    return len;  // 完整
                } else {
                    return i;  // 不完整
                }
            }
        }
        
        return len;  // 默认返回全部
    }
    
    /**
     * @brief 安全调用回调，捕获所有异常
     */
    void safe_callback(const std::string& data) {
        try {
            (*callback)(data);
        } catch (const std::exception& e) {
            // 记录错误但不传播异常
            last_error = e.what();
        } catch (...) {
            last_error = "Unknown exception in callback";
        }
    }
    
    /**
     * @brief 处理接收到的数据块
     * 
     * SSE格式: "data: {...}\n\n"
     * 按双换行符切分，确保每次回调传递完整的事件
     */
    void process_chunk(const char* data, size_t size) {
        buffer.append(data, size);
        
        // 按双换行符 (\n\n) 切分，处理完整的 SSE 事件
        size_t pos = 0;
        while (pos < buffer.size()) {
            // 查找双换行符（SSE 事件分隔符）
            size_t event_end = buffer.find("\n\n", pos);
            if (event_end == std::string::npos) {
                // 没有找到完整的事件，保留剩余数据
                break;
            }
            
            // 提取完整的事件（包含第一个换行符）
            std::string event = buffer.substr(pos, event_end - pos + 1);
            
            // 验证 UTF-8 完整性
            size_t valid_end = find_valid_utf8_end(event);
            if (valid_end == event.length()) {
                // UTF-8 完整，传递给回调
                safe_callback(event);
            }
            // 如果 UTF-8 不完整，跳过这个事件（不应该发生，因为我们按事件边界切分）
            
            pos = event_end + 2;  // 跳过双换行符
        }
        
        // 保留未处理的不完整数据
        if (pos < buffer.size()) {
            buffer = buffer.substr(pos);
        } else {
            buffer.clear();
        }
    }
    
    /**
     * @brief 刷新剩余缓冲区（流结束时调用）
     */
    void flush() {
        if (!buffer.empty()) {
            // 验证 UTF-8 完整性
            size_t valid_end = find_valid_utf8_end(buffer);
            if (valid_end > 0) {
                std::string valid_data = buffer.substr(0, valid_end);
                if (!valid_data.empty() && valid_data != "\n") {
                    safe_callback(valid_data);
                }
            }
            buffer.clear();
        }
    }
};

// Callback for streaming data with UTF-8 safe handling
static size_t stream_callback(void* contents, size_t size, size_t nmemb, void* userp) {
    size_t total_size = size * nmemb;
    auto* ctx = static_cast<StreamContext*>(userp);
    ctx->process_chunk(static_cast<const char*>(contents), total_size);
    return total_size;
}

// CURL 全局初始化管理器 (单例模式，确保只初始化一次)
class CurlGlobalInit {
public:
    static CurlGlobalInit& getInstance() {
        static CurlGlobalInit instance;
        return instance;
    }
    
    CurlGlobalInit(const CurlGlobalInit&) = delete;
    CurlGlobalInit& operator=(const CurlGlobalInit&) = delete;
    
private:
    CurlGlobalInit() {
        curl_global_init(CURL_GLOBAL_DEFAULT);
    }
    
    ~CurlGlobalInit() {
        curl_global_cleanup();
    }
};

// PIMPL implementation
class HttpClient::Impl {
public:
    Impl() : timeout_(120L) {  // 流式AI响应需要更长超时（120秒）
        // 确保 CURL 全局初始化（单例，只会初始化一次）
        CurlGlobalInit::getInstance();
    }
    
    ~Impl() {
        // 不在这里调用 curl_global_cleanup()
        // 由 CurlGlobalInit 单例在程序结束时处理
    }
    
    long timeout_;
    std::map<std::string, std::string> headers_;
};

HttpClient::HttpClient() : impl_(std::make_unique<Impl>()) {}

HttpClient::~HttpClient() = default;

HttpClient::HttpClient(HttpClient&&) noexcept = default;
HttpClient& HttpClient::operator=(HttpClient&&) noexcept = default;

HttpResponse HttpClient::get(const std::string& url) {
    CURL* curl = curl_easy_init();
    if (!curl) {
        throw A2AException("Failed to initialize CURL", ErrorCode::InternalError);
    }
    
    std::string response_body;
    HttpResponse response;
    
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_callback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_body);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, impl_->timeout_);
    curl_easy_setopt(curl, CURLOPT_FOLLOWLOCATION, 1L);
    
    // Add custom headers
    struct curl_slist* header_list = nullptr;
    for (const auto& [key, value] : impl_->headers_) {
        std::string header = key + ": " + value;
        header_list = curl_slist_append(header_list, header.c_str());
    }
    if (header_list) {
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
    }
    
    CURLcode res = curl_easy_perform(curl);
    
    if (res != CURLE_OK) {
        curl_slist_free_all(header_list);
        curl_easy_cleanup(curl);
        throw A2AException(
            std::string("CURL error: ") + curl_easy_strerror(res),
            ErrorCode::InternalError
        );
    }
    
    long status_code;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status_code);
    
    response.status_code = static_cast<int>(status_code);
    response.body = response_body;
    
    curl_slist_free_all(header_list);
    curl_easy_cleanup(curl);
    
    return response;
}

HttpResponse HttpClient::post(const std::string& url,
                              const std::string& body,
                              const std::string& content_type) {
    CURL* curl = curl_easy_init();
    if (!curl) {
        throw A2AException("Failed to initialize CURL", ErrorCode::InternalError);
    }
    
    std::string response_body;
    HttpResponse response;
    
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_POST, 1L);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, body.length());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, write_callback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response_body);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, impl_->timeout_);
    
    // Set headers
    struct curl_slist* header_list = nullptr;
    header_list = curl_slist_append(header_list, ("Content-Type: " + content_type).c_str());
    
    for (const auto& [key, value] : impl_->headers_) {
        std::string header = key + ": " + value;
        header_list = curl_slist_append(header_list, header.c_str());
    }
    
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
    
    CURLcode res = curl_easy_perform(curl);
    
    if (res != CURLE_OK) {
        curl_slist_free_all(header_list);
        curl_easy_cleanup(curl);
        throw A2AException(
            std::string("CURL error: ") + curl_easy_strerror(res),
            ErrorCode::InternalError
        );
    }
    
    long status_code;
    curl_easy_getinfo(curl, CURLINFO_RESPONSE_CODE, &status_code);
    
    response.status_code = static_cast<int>(status_code);
    response.body = response_body;
    
    curl_slist_free_all(header_list);
    curl_easy_cleanup(curl);
    
    return response;
}

void HttpClient::post_stream(const std::string& url,
                             const std::string& body,
                             const std::string& content_type,
                             std::function<void(const std::string&)> callback) {
    CURL* curl = curl_easy_init();
    if (!curl) {
        throw A2AException("Failed to initialize CURL", ErrorCode::InternalError);
    }
    
    // 创建流式处理上下文，处理UTF-8边界问题
    StreamContext ctx;
    ctx.callback = &callback;
    
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_POST, 1L);
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDSIZE, body.length());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, stream_callback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &ctx);  // 传递上下文而非直接传递callback
    
    // 流式请求：禁用总超时，使用低速超时
    // 如果60秒内没有收到任何数据才超时
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 0L);  // 禁用总超时
    curl_easy_setopt(curl, CURLOPT_LOW_SPEED_LIMIT, 1L);  // 最低速度 1 byte/s
    curl_easy_setopt(curl, CURLOPT_LOW_SPEED_TIME, 60L);  // 60秒内低于最低速度则超时
    
    // Set headers
    struct curl_slist* header_list = nullptr;
    header_list = curl_slist_append(header_list, ("Content-Type: " + content_type).c_str());
    header_list = curl_slist_append(header_list, "Accept: text/event-stream");
    
    for (const auto& [key, value] : impl_->headers_) {
        std::string header = key + ": " + value;
        header_list = curl_slist_append(header_list, header.c_str());
    }
    
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, header_list);
    
    CURLcode res = curl_easy_perform(curl);
    
    // 刷新剩余缓冲区
    ctx.flush();
    
    curl_slist_free_all(header_list);
    curl_easy_cleanup(curl);
    
    if (res != CURLE_OK) {
        throw A2AException(
            std::string("CURL error: ") + curl_easy_strerror(res),
            ErrorCode::InternalError
        );
    }
}

void HttpClient::set_timeout(long seconds) {
    impl_->timeout_ = seconds;
}

void HttpClient::add_header(const std::string& key, const std::string& value) {
    impl_->headers_[key] = value;
}

void HttpClient::clear_headers() {
    impl_->headers_.clear();
}

} // namespace a2a
