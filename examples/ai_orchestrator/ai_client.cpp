/**
 * @file ai_client.cpp
 * @brief FWI Agent 科研助手 - 交互式客户端 v3
 *
 * 功能：
 * - 上下键选择对话（使用 stty 原始模式）
 * - 对话标题自动摘要
 * - 历史记录持久化（从 Redis 加载）
 * - 精美 UI 界面
 */

#include <a2a/models/agent_message.hpp>
#include <a2a/models/message_part.hpp>
#include <a2a/core/jsonrpc_request.hpp>
#include <a2a/core/jsonrpc_response.hpp>
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
#include <cstdio>

using namespace a2a;
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
    const std::string MAGENTA = "\033[35m";
    const std::string CYAN    = "\033[36m";
    const std::string WHITE   = "\033[37m";
    const std::string GRAY    = "\033[90m";
    const std::string CLEAR   = "\033[2J\033[H";
}

// ============================================================
// CURL 回调
// ============================================================
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
    static std::string get(const std::string& url) {
        CURL* curl = curl_easy_init();
        if (!curl) return "";
        std::string response;
        curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
        curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
        curl_easy_setopt(curl, CURLOPT_WRITEDATA, &response);
        curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
        curl_easy_perform(curl);
        curl_easy_cleanup(curl);
        return response;
    }

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

    /**
     * @brief 获取会话的完整历史消息
     * @param context_id 会话 ID
     * @param limit 最大消息数（0 = 全部）
     * @return 消息列表，每个元素包含 role 和 text
     */
    static std::vector<std::pair<std::string, std::string>> get_session_messages(
        const std::string& context_id, int limit = 20) {

        std::vector<std::pair<std::string, std::string>> messages;

        // 使用 redis-cli 获取所有消息
        std::string cmd = "redis-cli lrange 'a2a:session:" + context_id + "' 0 -1";
        FILE* pipe = popen(cmd.c_str(), "r");
        if (!pipe) return messages;

        std::string buffer;
        char line[8192];
        while (fgets(line, sizeof(line), pipe)) {
            std::string str(line);
            // 去掉换行符
            while (!str.empty() && (str.back() == '\n' || str.back() == '\r'))
                str.pop_back();

            if (str.empty()) continue;

            try {
                auto msg = json::parse(str);
                std::string role = msg.value("role", "unknown");
                std::string text;
                if (msg.contains("parts") && !msg["parts"].empty()) {
                    text = msg["parts"][0].value("text", "");
                }
                if (!text.empty()) {
                    messages.push_back({role, text});
                }
            } catch (...) {
                // 忽略解析错误
            }
        }
        pclose(pipe);

        // 限制消息数量（保留最新的）
        if (limit > 0 && messages.size() > static_cast<size_t>(limit)) {
            messages.erase(messages.begin(), messages.end() - limit);
        }

        return messages;
    }

    /**
     * @brief 删除会话
     * @param context_id 会话 ID
     * @return 是否成功
     */
    static bool delete_session(const std::string& context_id) {
        std::string cmd = "redis-cli del 'a2a:session:" + context_id + "' 'a2a:history:" + context_id + "' 'a2a:task:" + context_id + "' 2>/dev/null";
        int ret = system(cmd.c_str());
        return ret == 0;
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
                {"historyLength", 20}
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

    std::string get_agent_card() {
        return HttpClient::get(server_url_ + "/.well-known/agent-card.json");
    }

private:
    std::string server_url_;
    int req_id_ = 0;
};

// ============================================================
// UI 组件
// ============================================================

void clear_screen() {
    std::cout << UI::CLEAR << std::flush;
}

void print_separator() {
    std::cout << UI::GRAY << "  ─────────────────────────────────────────────────────────────────" << UI::RESET << "\n";
}

std::string generate_title(const std::string& text) {
    if (text.empty()) return "新对话";
    std::string title = text;
    std::replace(title.begin(), title.end(), '\n', ' ');
    if (title.length() > 35) {
        title = title.substr(0, 35) + "...";
    }
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
    std::cout << UI::DIM << "             Full Waveform Inversion Research Assistant               ";
    std::cout << UI::RESET << UI::CYAN << "║" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  ╚═══════════════════════════════════════════════════════════════════╝" << UI::RESET << "\n";
    std::cout << "\n";
    std::cout << UI::DIM << "    连接: " << server_url << UI::RESET << "\n\n";
}

void print_agent_card(const std::string& card_json) {
    try {
        auto card = json::parse(card_json);
        std::cout << "\n";
        std::cout << UI::CYAN << "  ╔═══════════════════════════════════════════════════════════════════╗" << UI::RESET << "\n";
        std::cout << UI::CYAN << "  ║" << UI::RESET << UI::BOLD << UI::WHITE << "  📋 Agent Card";
        for (int i = 0; i < 52; i++) std::cout << " ";
        std::cout << UI::CYAN << "║" << UI::RESET << "\n";
        std::cout << UI::CYAN << "  ╚═══════════════════════════════════════════════════════════════════╝" << UI::RESET << "\n\n";
        std::cout << UI::BOLD << "    📛 名称    " << UI::RESET << card.value("name", "Unknown") << "\n";
        std::cout << UI::BOLD << "    📝 描述    " << UI::RESET << card.value("description", "") << "\n";
        std::cout << UI::BOLD << "    🔢 版本    " << UI::RESET << card.value("version", "") << "\n";
        if (card.contains("capabilities")) {
            auto caps = card["capabilities"];
            std::cout << UI::BOLD << "    ⚡ 能力    " << UI::RESET;
            std::vector<std::string> list;
            if (caps.value("streaming", false)) list.push_back("流式");
            if (caps.value("task_management", false)) list.push_back("任务管理");
            for (size_t i = 0; i < list.size(); ++i) {
                if (i > 0) std::cout << " · ";
                std::cout << UI::GREEN << list[i] << UI::RESET;
            }
            std::cout << "\n";
        }
        if (card.contains("skills") && !card["skills"].empty()) {
            std::cout << UI::BOLD << "    🎯 技能    " << UI::RESET << "\n";
            for (const auto& skill : card["skills"]) {
                std::cout << "        • " << UI::YELLOW << skill.value("name", "") << UI::RESET
                          << ": " << skill.value("description", "") << "\n";
            }
        }
        std::cout << "\n";
    } catch (const std::exception& e) {
        std::cout << UI::RED << "  解析失败: " << e.what() << UI::RESET << "\n";
    }
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
        conv.last_message = last_user_msg.length() > 50
            ? last_user_msg.substr(0, 50) + "..."
            : last_user_msg;
        convs.push_back(conv);
    }
    std::sort(convs.begin(), convs.end(),
        [](const Conversation& a, const Conversation& b) {
            return a.message_count > b.message_count;
        });
    return convs;
}

void print_conversation_list(const std::vector<Conversation>& convs, int selected) {
    std::cout << "\n";
    std::cout << UI::CYAN << "  ┌─────────────────────────────────────────────────────────────────┐" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  📚 对话历史";
    for (int i = 0; i < 52; i++) std::cout << " ";
    std::cout << UI::CYAN << "│" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  └─────────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";

    if (convs.empty()) {
        std::cout << UI::DIM << "    暂无历史对话" << UI::RESET << "\n";
        std::cout << UI::DIM << "    按 Enter 新建对话开始聊天" << UI::RESET << "\n\n";
    } else {
        for (size_t i = 0; i < convs.size(); ++i) {
            bool is_selected = (static_cast<int>(i) == selected);
            std::string indicator = is_selected ? UI::BOLD + UI::CYAN + "  ▶ " : "    ";
            std::string title_color = is_selected ? UI::BOLD + UI::WHITE : UI::DIM;
            std::string meta_color = is_selected ? UI::GREEN : UI::GRAY;

            std::cout << indicator << title_color << convs[i].title << UI::RESET;
            std::cout << "  " << meta_color << "(" << convs[i].message_count << " 条)" << UI::RESET;
            std::cout << "\n";

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
    std::cout << UI::DIM << "d" << UI::RESET << " 删除   ";
    std::cout << UI::DIM << "/help" << UI::RESET << " 帮助   ";
    std::cout << UI::DIM << "/quit" << UI::RESET << " 退出\n\n";
}

/**
 * @brief 打印历史对话
 */
void print_conversation_history(const std::string& context_id) {
    auto messages = RedisHelper::get_session_messages(context_id, 20);

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

void print_help() {
    std::cout << "\n";
    std::cout << UI::CYAN << "  ┌─────────────────────────────────────────────────────────────────┐" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  📖 帮助";
    for (int i = 0; i < 55; i++) std::cout << " ";
    std::cout << UI::CYAN << "│" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  └─────────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";
    std::cout << UI::BOLD << "    列表模式:" << UI::RESET << "\n";
    std::cout << "      ↑/↓         选择对话\n";
    std::cout << "      Enter       进入选中的对话\n";
    std::cout << "      n           新建对话\n";
    std::cout << "      1-9         直接选择对应对话\n\n";
    std::cout << UI::BOLD << "    对话模式:" << UI::RESET << "\n";
    std::cout << "      直接输入    发送消息\n";
    std::cout << "      /help       显示帮助\n";
    std::cout << "      /card       查看 Agent Card\n";
    std::cout << "      /list       返回对话列表\n";
    std::cout << "      /clear      清屏\n";
    std::cout << "      /quit       退出\n\n";
}

/**
 * @brief 使用 popen + read 读取单个字符（支持方向键）
 */
/**
 * @brief 读取一个按键（支持方向键）
 *
 * 使用 termios 直接控制终端，避免 stty 的问题
 */
#include <termios.h>
#include <unistd.h>

int read_key() {
    struct termios old_termios, new_termios;

    // 保存当前终端设置
    tcgetattr(STDIN_FILENO, &old_termios);
    new_termios = old_termios;

    // 设置为原始模式：关闭行缓冲和回显
    new_termios.c_lflag &= ~(ICANON | ECHO);
    new_termios.c_cc[VMIN] = 1;   // 最少读取 1 个字符
    new_termios.c_cc[VTIME] = 0;  // 不超时
    tcsetattr(STDIN_FILENO, TCSANOW, &new_termios);

    int c = getchar();

    // 检查是否是转义序列（方向键）
    if (c == 27) {
        int next = getchar();
        if (next == 91) {
            int arrow = getchar();
            // 恢复终端设置
            tcsetattr(STDIN_FILENO, TCSANOW, &old_termios);
            switch (arrow) {
                case 65: return 1000;  // 上
                case 66: return 1001;  // 下
            }
        }
        tcsetattr(STDIN_FILENO, TCSANOW, &old_termios);
        return 27;
    }

    // 恢复终端设置
    tcsetattr(STDIN_FILENO, TCSANOW, &old_termios);
    return c;
}

// ============================================================
// 主程序
// ============================================================
int main(int argc, char* argv[]) {
    std::string server_url = "http://localhost:5000";
    if (argc > 1) server_url = argv[1];

    AgentClient client(server_url);
    auto conversations = load_conversations();
    int selected = 0;
    bool in_conversation = false;
    std::string current_ctx;

    print_welcome(server_url);

    while (true) {
        if (!in_conversation) {
            // ========== 列表模式 ==========
            print_conversation_list(conversations, selected);
            std::cout << UI::BOLD << UI::CYAN << "  > " << UI::RESET << std::flush;

            int key = read_key();

            // 上箭头
            if (key == 1000) {
                if (selected > 0) selected--;
                clear_screen();
                print_welcome(server_url);
                continue;
            }

            // 下箭头
            if (key == 1001) {
                if (selected < static_cast<int>(conversations.size()) - 1) selected++;
                clear_screen();
                print_welcome(server_url);
                continue;
            }

            // Enter
            if (key == 13) {
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

            // n - 新建
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

            // d - 删除
            if ((key == 'd' || key == 'D') && !conversations.empty()) {
                std::string ctx_to_delete = conversations[selected].context_id;
                std::string title_to_delete = conversations[selected].title;

                std::cout << "\n" << UI::YELLOW << "  确认删除 \"" << title_to_delete << "\"? (y/n): " << UI::RESET;

                // 读取确认
                if(system("stty cooked echo 2>/dev/null")){}
                std::string confirm;
                std::getline(std::cin, confirm);

                if (confirm == "y" || confirm == "Y") {
                    RedisHelper::delete_session(ctx_to_delete);
                    conversations = load_conversations();
                    if (selected >= static_cast<int>(conversations.size())) {
                        selected = std::max(0, static_cast<int>(conversations.size()) - 1);
                    }
                    std::cout << UI::GREEN << "  ✓ 已删除" << UI::RESET << "\n";
                } else {
                    std::cout << UI::DIM << "  取消删除" << UI::RESET << "\n";
                }
                continue;
            }

            // q - 退出
            if (key == 'q' || key == 'Q') {
                std::cout << "\n" << UI::GREEN << "  👋 再见！" << UI::RESET << "\n\n";
                break;
            }

            // 数字选择
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

            // 其他按键 - 回退到行模式读取
            (void)system("stty cooked echo 2>/dev/null");
            std::string input;
            if (key >= 32 && key < 127) {
                input = static_cast<char>(key);
            }
            std::string rest;
            std::getline(std::cin, rest);
            input += rest;

            if (input.empty()) continue;

            if (input == "/quit" || input == "/exit") {
                std::cout << "\n" << UI::GREEN << "  👋 再见！" << UI::RESET << "\n\n";
                break;
            }

            if (input == "/help") { print_help(); continue; }
            if (input == "/card") { print_agent_card(client.get_agent_card()); continue; }

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

            if (input == "/help") { print_help(); continue; }
            if (input == "/card") { print_agent_card(client.get_agent_card()); continue; }
            if (input == "/clear") { clear_screen(); continue; }

            // 发送消息
            std::cout << "\n" << UI::DIM << "    ⏳ 思考中..." << UI::RESET << "\r" << std::flush;
            std::string response = client.send_message(input, current_ctx);
            std::cout << "\033[2K";
            std::cout << "\n";
            std::cout << UI::BOLD << UI::GREEN << "    🤖 AI: " << UI::RESET << response << "\n\n";
            print_separator();
        }
    }

    return 0;
}
