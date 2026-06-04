/**
 * @file grpc_ai_client.cpp
 * @brief FWI Agent gRPC 客户端 - 与 HTTP 版本相同的 UI
 *
 * 通过 HTTP 连接 Orchestrator（与 ai_client 相同的 UI）
 */

#pragma GCC diagnostic ignored "-Wunused-result"

#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <string>
#include <vector>
#include <ctime>
#include <iomanip>
#include <sstream>
#include <algorithm>
#include <cstdlib>
#include <csignal>

using json = nlohmann::json;

// ============================================================
// 颜色主题
// ============================================================
namespace UI {
    const std::string RESET   = "\033[0m";
    const std::string BOLD    = "\033[1m";
    const std::string DIM     = "\033[2m";
    const std::string RED     = "\033[31m";
    const std::string GREEN   = "\033[32m";
    const std::string YELLOW  = "\033[33m";
    const std::string BLUE    = "\033[34m";
    const std::string CYAN    = "\033[36m";
    const std::string WHITE   = "\033[37m";
    const std::string GRAY    = "\033[90m";
    const std::string CLEAR   = "\033[2J\033[H";
}

static size_t WriteCallback(void* contents, size_t size, size_t nmemb, std::string* userp) {
    userp->append((char*)contents, size * nmemb);
    return size * nmemb;
}

// ============================================================
// 对话信息
// ============================================================
struct Conversation {
    std::string context_id;
    std::string title;
    std::string last_message;
    int message_count = 0;
};

// ============================================================
// HTTP 客户端
// ============================================================
class HttpClient {
public:
    static std::string post(const std::string& url, const std::string& body) {
        CURL* curl = curl_easy_init();
        if (!curl) return "";
        std::string response;
        struct curl_slist* headers = nullptr;
        headers = curl_slist_append(headers, "Content-Type: application/json");
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
        curl_easy_setopt(curl, CURLOPT_HTTPHEADER, headers);
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 120L);
        CURLcode res = curl_easy_perform(curl);
        curl_slist_free_all(headers);
        curl_easy_cleanup(curl);
        if (res != CURLE_OK) {
            return "{\"error\":{\"message\":\"" + std::string(curl_easy_strerror(res)) + "\"}}";
        }
        return response;
    }
};

// ============================================================
// Redis 辅助
// ============================================================
class RedisHelper {
public:
    static std::vector<std::string> get_all_session_ids() {
        std::vector<std::string> ids;
        FILE* pipe = popen("redis-cli keys 'a2a:session:*' 2>/dev/null", "r");
        if (!pipe) return ids;
        char buffer[256];
        while (fgets(buffer, sizeof(buffer), pipe)) {
            std::string line(buffer);
            while (!line.empty() && (line.back() == '\n' || line.back() == '\r'))
                line.pop_back();
            if (line.find("a2a:session:") == 0) {
                ids.push_back(line.substr(12));
            }
        }
        pclose(pipe);
        return ids;
    }

    static int get_session_count(const std::string& context_id) {
        std::string cmd = "redis-cli llen 'a2a:session:" + context_id + "' 2>/dev/null";
        FILE* pipe = popen(cmd.c_str(), "r");
        if (!pipe) return 0;
        char buffer[32];
        int count = 0;
        if (fgets(buffer, sizeof(buffer), pipe)) {
            count = std::atoi(buffer);
        }
        pclose(pipe);
        return count;
    }

    static std::string get_last_user_message(const std::string& context_id) {
        std::string cmd = "redis-cli lindex 'a2a:session:" + context_id + "' -2 2>/dev/null";
        FILE* pipe = popen(cmd.c_str(), "r");
        if (!pipe) return "";
        std::string result;
        char buffer[4096];
        while (fgets(buffer, sizeof(buffer), pipe)) {
            result += buffer;
        }
        pclose(pipe);
        while (!result.empty() && (result.back() == '\n' || result.back() == '\r'))
            result.pop_back();
        try {
            auto msg = json::parse(result);
            if (msg.contains("parts") && !msg["parts"].empty()) {
                return msg["parts"][0].value("text", "");
            }
        } catch (...) {}
        return "";
    }
};

// ============================================================
// Agent 客户端
// ============================================================
class AgentClient {
public:
    explicit AgentClient(const std::string& url) : server_url_(url) {
        curl_global_init(CURL_GLOBAL_DEFAULT);
    }
    ~AgentClient() { curl_global_cleanup(); }

    std::string send_message(const std::string& text, const std::string& context_id) {
        json request = {
            {"jsonrpc", "2.0"},
            {"id", std::to_string(++req_id_)},
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
        std::string resp = HttpClient::post(server_url_, request.dump());
        try {
            auto j = json::parse(resp);
            if (j.contains("error"))
                return UI::RED + "错误: " + j["error"]["message"].get<std::string>() + UI::RESET;
            if (j.contains("result") && j["result"].contains("parts") &&
                !j["result"]["parts"].empty())
                return j["result"]["parts"][0]["text"].get<std::string>();
            return UI::RED + "无法解析响应" + UI::RESET;
        } catch (const std::exception& e) {
            return UI::RED + "解析错误: " + e.what() + UI::RESET;
        }
    }

private:
    std::string server_url_;
    int req_id_ = 0;
};

// ============================================================
// UI 组件
// ============================================================

void clear_screen() { std::cout << UI::CLEAR << std::flush; }

void print_separator() {
    std::cout << UI::GRAY << "  ─────────────────────────────────────────────────────────────────" << UI::RESET << "\n";
}

std::string generate_title(const std::string& text) {
    if (text.empty()) return "新对话";
    std::string title = text;
    std::replace(title.begin(), title.end(), '\n', ' ');
    if (title.length() > 35) title = title.substr(0, 35) + "...";
    return title;
}

void print_welcome(const std::string& server_url) {
    clear_screen();
    std::cout << "\n";
    std::cout << UI::CYAN << "  ╔═══════════════════════════════════════════════════════════════════╗" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  ║" << UI::RESET;
    std::cout << UI::BOLD << UI::WHITE << "            🔬  FWI 全波形反演科研助手平台  🔬                  ";
    std::cout << UI::RESET << UI::CYAN << "║" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  ║" << UI::RESET;
    std::cout << UI::DIM << "                       gRPC 模式                                  ";
    std::cout << UI::RESET << UI::CYAN << "║" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  ╚═══════════════════════════════════════════════════════════════════╝" << UI::RESET << "\n";
    std::cout << "\n";
    std::cout << UI::DIM << "    连接: " << server_url << UI::RESET << "\n\n";
}

std::vector<Conversation> load_conversations() {
    std::vector<Conversation> convs;
    auto session_ids = RedisHelper::get_all_session_ids();
    for (const auto& id : session_ids) {
        Conversation conv;
        conv.context_id = id;
        conv.message_count = RedisHelper::get_session_count(id);
        std::string last_user_msg = RedisHelper::get_last_user_message(id);
        conv.title = generate_title(last_user_msg);
        conv.last_message = last_user_msg.length() > 50 ? last_user_msg.substr(0, 50) + "..." : last_user_msg;
        convs.push_back(conv);
    }
    std::sort(convs.begin(), convs.end(),
        [](const Conversation& a, const Conversation& b) { return a.message_count > b.message_count; });
    return convs;
}

/**
 * @brief 获取会话的完整历史消息
 */
std::vector<std::pair<std::string, std::string>> get_session_messages(const std::string& context_id, int limit = 20) {
    std::vector<std::pair<std::string, std::string>> messages;
    std::string cmd = "redis-cli lrange 'a2a:session:" + context_id + "' 0 -1 2>/dev/null";
    FILE* pipe = popen(cmd.c_str(), "r");
    if (!pipe) return messages;

    char buffer[8192];
    while (fgets(buffer, sizeof(buffer), pipe)) {
        std::string line(buffer);
        while (!line.empty() && (line.back() == '\n' || line.back() == '\r'))
            line.pop_back();
        if (line.empty()) continue;

        try {
            auto msg = nlohmann::json::parse(line);
            std::string role = msg.value("role", "unknown");
            std::string text;
            if (msg.contains("parts") && !msg["parts"].empty()) {
                text = msg["parts"][0].value("text", "");
            }
            if (!text.empty()) {
                messages.push_back({role, text});
            }
        } catch (...) {}
    }
    pclose(pipe);

    if (limit > 0 && messages.size() > static_cast<size_t>(limit)) {
        messages.erase(messages.begin(), messages.end() - limit);
    }
    return messages;
}

/**
 * @brief 打印历史对话
 */
void print_conversation_history(const std::string& context_id) {
    auto messages = get_session_messages(context_id, 20);

    if (messages.empty()) {
        std::cout << UI::DIM << "    暂无历史消息" << UI::RESET << "\n\n";
        return;
    }

    for (const auto& [role, text] : messages) {
        if (role == "user") {
            std::cout << UI::BOLD << UI::BLUE << "    👤 你: " << UI::RESET << text << "\n\n";
        } else {
            std::string display_text = text;
            if (display_text.length() > 300) {
                display_text = display_text.substr(0, 300) + "...";
            }
            std::cout << UI::BOLD << UI::GREEN << "    🤖 AI: " << UI::RESET << display_text << "\n\n";
        }
    }
    print_separator();
}

void print_conversation_list(const std::vector<Conversation>& convs, int selected) {
    std::cout << "\n";
    std::cout << UI::CYAN << "  ┌─────────────────────────────────────────────────────────────────┐" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  📚 对话历史";
    for (int i = 0; i < 52; i++) std::cout << " ";
    std::cout << UI::CYAN << "│" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  └─────────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";

    if (convs.empty()) {
        std::cout << UI::DIM << "    暂无历史对话" << UI::RESET << "\n\n";
    } else {
        for (size_t i = 0; i < convs.size(); ++i) {
            bool is_selected = (static_cast<int>(i) == selected);
            std::string indicator = is_selected ? UI::BOLD + UI::CYAN + "  ▶ " : "    ";
            std::string title_color = is_selected ? UI::BOLD + UI::WHITE : UI::DIM;
            std::string meta_color = is_selected ? UI::GREEN : UI::GRAY;

            std::cout << indicator << title_color << convs[i].title << UI::RESET;
            std::cout << "  " << meta_color << "(" << convs[i].message_count << " 条)" << UI::RESET << "\n";

            if (is_selected && !convs[i].last_message.empty()) {
                std::cout << "      " << UI::DIM << "└─ " << convs[i].last_message << UI::RESET << "\n";
            }
        }
        std::cout << "\n";
    }

    print_separator();
    std::cout << UI::DIM << "    ↑/↓" << UI::RESET << " 选择   ";
    std::cout << UI::DIM << "Enter" << UI::RESET << " 进入   ";
    std::cout << UI::DIM << "n" << UI::RESET << " 新建   ";
    std::cout << UI::DIM << "/help" << UI::RESET << " 帮助   ";
    std::cout << UI::DIM << "/quit" << UI::RESET << " 退出\n\n";
}

/**
 * @brief 读取一个按键（支持方向键）
 */
#include <termios.h>
#include <unistd.h>

int read_key() {
    struct termios old_termios, new_termios;
    tcgetattr(STDIN_FILENO, &old_termios);
    new_termios = old_termios;
    new_termios.c_lflag &= ~(ICANON | ECHO);
    new_termios.c_cc[VMIN] = 1;
    new_termios.c_cc[VTIME] = 0;
    tcsetattr(STDIN_FILENO, TCSANOW, &new_termios);

    int c = getchar();

    if (c == 27) {
        int next = getchar();
        if (next == 91) {
            int arrow = getchar();
            tcsetattr(STDIN_FILENO, TCSANOW, &old_termios);
            switch (arrow) {
                case 65: return 1000;
                case 66: return 1001;
            }
        }
        tcsetattr(STDIN_FILENO, TCSANOW, &old_termios);
        return 27;
    }

    tcsetattr(STDIN_FILENO, TCSANOW, &old_termios);
    return c;
}

// ============================================================
// 主程序
// ============================================================

int main(int argc, char* argv[]) {
    std::string server_url = "http://localhost:5000";
    if (argc > 1) {
        // 如果传入的是 host:port 格式，转换为 http://host:port
        std::string addr = argv[1];
        if (addr.find("http") == std::string::npos) {
            server_url = "http://" + addr;
        } else {
            server_url = addr;
        }
    }

    AgentClient client(server_url);
    auto conversations = load_conversations();
    int selected = 0;
    bool in_conversation = false;
    std::string current_ctx;

    print_welcome(server_url);

    while (true) {
        if (!in_conversation) {
            print_conversation_list(conversations, selected);
            std::cout << UI::BOLD << UI::CYAN << "  > " << UI::RESET << std::flush;

            int key = read_key();

            if (key == 1000) {  // 上
                if (selected > 0) selected--;
                clear_screen();
                print_welcome(server_url);
                continue;
            }

            if (key == 1001) {  // 下
                if (selected < static_cast<int>(conversations.size()) - 1) selected++;
                clear_screen();
                print_welcome(server_url);
                continue;
            }

            if (key == 13) {  // Enter
                if (!conversations.empty()) {
                    current_ctx = conversations[selected].context_id;
                    in_conversation = true;
                    clear_screen();
                    std::cout << "\n";
                    std::cout << UI::CYAN << "  ┌─────────────────────────────────────────────────────────────────┐" << UI::RESET << "\n";
                    std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  💬 " << conversations[selected].title;
                    int pad = 58 - conversations[selected].title.length();
                    for (int i = 0; i < pad; i++) std::cout << " ";
                    std::cout << UI::CYAN << "│" << UI::RESET << "\n";
                    std::cout << UI::CYAN << "  └─────────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";

                    // 显示历史对话
                    print_conversation_history(current_ctx);
                }
                continue;
            }

            if (key == 'n' || key == 'N') {
                current_ctx = "ctx-" + std::to_string(std::time(nullptr));
                in_conversation = true;
                clear_screen();
                std::cout << "\n";
                std::cout << UI::CYAN << "  ┌─────────────────────────────────────────────────────────────────┐" << UI::RESET << "\n";
                std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  💬 新对话";
                for (int i = 0; i < 54; i++) std::cout << " ";
                std::cout << UI::CYAN << "│" << UI::RESET << "\n";
                std::cout << UI::CYAN << "  └─────────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";
                continue;
            }

            if (key == 'q' || key == 'Q') {
                std::cout << "\n" << UI::GREEN << "  👋 再见！" << UI::RESET << "\n\n";
                break;
            }

            if (key >= '1' && key <= '9') {
                int num = key - '0';
                if (num >= 1 && num <= static_cast<int>(conversations.size())) {
                    selected = num - 1;
                    current_ctx = conversations[selected].context_id;
                    in_conversation = true;
                    clear_screen();
                    std::cout << "\n";
                    std::cout << UI::CYAN << "  ┌─────────────────────────────────────────────────────────────────┐" << UI::RESET << "\n";
                    std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  💬 " << conversations[selected].title;
                    int pad = 58 - conversations[selected].title.length();
                    for (int i = 0; i < pad; i++) std::cout << " ";
                    std::cout << UI::CYAN << "│" << UI::RESET << "\n";
                    std::cout << UI::CYAN << "  └─────────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";

                    // 显示历史对话
                    print_conversation_history(current_ctx);
                }
                continue;
            }

            // 其他按键
            if(system("stty cooked echo 2>/dev/null")){}
            std::string input;
            if (key >= 32 && key < 127) input = static_cast<char>(key);
            std::string rest;
            std::getline(std::cin, rest);
            input += rest;

            if (input.empty()) continue;
            if (input == "/quit" || input == "/exit") {
                std::cout << "\n" << UI::GREEN << "  👋 再见！" << UI::RESET << "\n\n";
                break;
            }

            // 默认新建对话
            current_ctx = "ctx-" + std::to_string(std::time(nullptr));
            in_conversation = true;
            clear_screen();
            print_welcome(server_url);

        } else {
            // ========== 对话模式 ==========
            std::cout << UI::BOLD << UI::BLUE << "  [" << current_ctx << "] > " << UI::RESET;
            std::string input;
            if (!std::getline(std::cin, input)) break;
            if (input.empty()) continue;

            if (input == "/quit" || input == "/exit") {
                std::cout << "\n" << UI::GREEN << "  👋 再见！" << UI::RESET << "\n\n";
                break;
            }

            if (input == "/list" || input == "/back") {
                in_conversation = false;
                conversations = load_conversations();
                clear_screen();
                print_welcome(server_url);
                continue;
            }

            if (input == "/clear") { clear_screen(); continue; }

            // 发送消息
            std::cout << "\n" << UI::DIM << "    ⏳ 思考中..." << UI::RESET << "\r" << std::flush;
            std::string response = client.send_message(input, current_ctx);
            std::cout << "\033[2K\n";
            std::cout << UI::BOLD << UI::GREEN << "    🤖 AI: " << UI::RESET << response << "\n\n";
            print_separator();
        }
    }

    return 0;
}
