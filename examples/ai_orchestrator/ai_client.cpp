/**
 * @file ai_client.cpp
 * @brief FWI Agent 科研助手 - 交互式客户端 v2
 *
 * 功能：
 * - 上下键选择对话
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
#include <termios.h>
#include <unistd.h>

using namespace a2a;
using json = nlohmann::json;

// ============================================================
// 颜色主题
// ============================================================
namespace UI {
    const std::string RESET   = "\033[0m";
    const std::string BOLD    = "\033[1m";
    const std::string DIM     = "\033[2m";
    const std::string ITALIC  = "\033[3m";

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
// 终端原始模式（用于读取方向键）
// ============================================================
class Terminal {
public:
    static void enable_raw_mode() {
        struct termios raw;
        tcgetattr(STDIN_FILENO, &raw);
        raw.c_lflag &= ~(ICANON | ECHO);
        tcsetattr(STDIN_FILENO, TCSAFLUSH, &raw);
    }

    static void disable_raw_mode() {
        struct termios raw;
        tcgetattr(STDIN_FILENO, &raw);
        raw.c_lflag |= (ICANON | ECHO);
        tcsetattr(STDIN_FILENO, TCSAFLUSH, &raw);
    }

    /**
     * @brief 读取一个按键（支持方向键）
     * @return 按键代码
     *
     * 普通按键: 返回 ASCII 值
     * 上箭头:   返回 1000
     * 下箭头:   返回 1001
     * Enter:    返回 13
     * Escape:   返回 27
     */
    static int read_key() {
        int c = getchar();

        // 检查是否是转义序列（方向键）
        if (c == 27) {
            int next = getchar();
            if (next == 91) {
                int arrow = getchar();
                switch (arrow) {
                    case 65: return 1000;  // 上箭头
                    case 66: return 1001;  // 下箭头
                    case 67: return 1002;  // 右箭头
                    case 68: return 1003;  // 左箭头
                }
            }
            return 27;
        }

        return c;
    }
};

// ============================================================
// 对话信息
// ============================================================
struct Conversation {
    std::string context_id;
    std::string title;            // 摘要标题
    std::string last_message;     // 最后一条消息摘要
    std::string timestamp;        // 最后更新时间
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
        // 获取倒数第二条消息（最后一条用户消息）
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
    std::cout << UI::CLEAR;
}

void print_separator() {
    std::cout << UI::GRAY << "  ─────────────────────────────────────────────────────────────────" << UI::RESET << "\n";
}

/**
 * @brief 生成对话标题摘要
 */
std::string generate_title(const std::string& text) {
    if (text.empty()) return "新对话";

    // 去掉换行
    std::string title = text;
    std::replace(title.begin(), title.end(), '\n', ' ');

    // 截断
    if (title.length() > 35) {
        title = title.substr(0, 35) + "...";
    }
    return title;
}

/**
 * @brief 打印欢迎界面
 */
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

/**
 * @brief 格式化 Agent Card
 */
void print_agent_card(const std::string& card_json) {
    try {
        auto card = json::parse(card_json);
        std::cout << "\n";
        std::cout << UI::CYAN << "  ╔═══════════════════════════════════════════════════════════════════╗" << UI::RESET << "\n";
        std::cout << UI::CYAN << "  ║" << UI::RESET << UI::BOLD << UI::WHITE << "  📋 Agent Card" << UI::RESET;
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

/**
 * @brief 加载历史对话
 */
std::vector<Conversation> load_conversations() {
    std::vector<Conversation> convs;
    auto session_ids = RedisHelper::get_all_session_ids();

    for (const auto& id : session_ids) {
        Conversation conv;
        conv.context_id = id;
        conv.message_count = RedisHelper::get_session_count(id);

        // 获取最后一条用户消息作为标题
        std::string last_user_msg = RedisHelper::get_last_user_message(id);
        conv.title = generate_title(last_user_msg);
        conv.last_message = last_user_msg.length() > 50
            ? last_user_msg.substr(0, 50) + "..."
            : last_user_msg;

        conv.timestamp = "";
        convs.push_back(conv);
    }

    // 按消息数量排序
    std::sort(convs.begin(), convs.end(),
        [](const Conversation& a, const Conversation& b) {
            return a.message_count > b.message_count;
        });

    return convs;
}

/**
 * @brief 打印对话列表（支持选中高亮）
 */
void print_conversation_list(const std::vector<Conversation>& convs, int selected) {
    std::cout << "\n";
    std::cout << UI::CYAN << "  ┌─────────────────────────────────────────────────────────────────┐" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  📚 对话历史" << UI::RESET;
    for (int i = 0; i < 52; i++) std::cout << " ";
    std::cout << UI::CYAN << "│" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  └─────────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";

    if (convs.empty()) {
        std::cout << UI::DIM << "    暂无历史对话" << UI::RESET << "\n";
        std::cout << UI::DIM << "    按 Enter 新建对话开始聊天" << UI::RESET << "\n\n";
    } else {
        for (size_t i = 0; i < convs.size(); ++i) {
            bool is_selected = (static_cast<int>(i) == selected);

            // 选中指示器
            std::string indicator = is_selected ? UI::BOLD + UI::CYAN + "  ▶ " : "    ";
            std::string title_color = is_selected ? UI::BOLD + UI::WHITE : UI::DIM;
            std::string meta_color = is_selected ? UI::GREEN : UI::GRAY;

            // 标题
            std::cout << indicator << title_color << convs[i].title << UI::RESET;

            // 消息数量
            std::cout << "  " << meta_color << "(" << convs[i].message_count << " 条)" << UI::RESET;
            std::cout << "\n";

            // 选中时显示摘要
            if (is_selected && !convs[i].last_message.empty()) {
                std::cout << "      " << UI::DIM << "└─ " << convs[i].last_message << UI::RESET << "\n";
            }
        }
        std::cout << "\n";
    }

    // 操作提示
    print_separator();
    std::cout << UI::DIM << "    ↑/↓" << UI::RESET << " 选择   ";
    std::cout << UI::DIM << "Enter" << UI::RESET << " 进入   ";
    std::cout << UI::DIM << "n" << UI::RESET << " 新建   ";
    std::cout << UI::DIM << "/help" << UI::RESET << " 帮助   ";
    std::cout << UI::DIM << "/quit" << UI::RESET << " 退出\n\n";
}

/**
 * @brief 打印帮助
 */
void print_help() {
    std::cout << "\n";
    std::cout << UI::CYAN << "  ┌─────────────────────────────────────────────────────────────────┐" << UI::RESET << "\n";
    std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  📖 帮助" << UI::RESET;
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

// ============================================================
// 主程序
// ============================================================
int main(int argc, char* argv[]) {
    std::string server_url = "http://localhost:5000";
    if (argc > 1) server_url = argv[1];

    AgentClient client(server_url);

    // 加载历史对话
    auto conversations = load_conversations();
    int selected = 0;
    bool in_conversation = false;
    std::string current_ctx;

    // 显示欢迎界面
    print_welcome(server_url);

    // 主循环
    while (true) {
        if (!in_conversation) {
            // ========== 列表模式（支持上下键）==========
            print_conversation_list(conversations, selected);

            // 启用原始模式读取按键
            Terminal::enable_raw_mode();
            int key = Terminal::read_key();
            Terminal::disable_raw_mode();

            // 上箭头
            if (key == 1000) {
                if (selected > 0) {
                    selected--;
                    // 重绘列表
                    std::cout << "\033[" << (conversations.size() + 10) << "A";
                    print_conversation_list(conversations, selected);
                }
                continue;
            }

            // 下箭头
            if (key == 1001) {
                if (selected < static_cast<int>(conversations.size()) - 1) {
                    selected++;
                    std::cout << "\033[" << (conversations.size() + 10) << "A";
                    print_conversation_list(conversations, selected);
                }
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
                    std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  💬 " << conversations[selected].title << UI::RESET;
                    int pad = 58 - conversations[selected].title.length();
                    for (int i = 0; i < pad; i++) std::cout << " ";
                    std::cout << UI::CYAN << "│" << UI::RESET << "\n";
                    std::cout << UI::CYAN << "  └─────────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";
                }
                continue;
            }

            // 普通字符（回退到行模式）
            Terminal::disable_raw_mode();

            // 重新读取输入（行模式）
            std::string input;
            // 如果已经读取了字符，需要处理
            if (key == 'n' || key == 'N') {
                current_ctx = "ctx-" + std::to_string(std::time(nullptr));
                in_conversation = true;
                clear_screen();
                std::cout << "\n";
                std::cout << UI::CYAN << "  ┌─────────────────────────────────────────────────────────────────┐" << UI::RESET << "\n";
                std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  💬 新对话" << UI::RESET;
                for (int i = 0; i < 54; i++) std::cout << " ";
                std::cout << UI::CYAN << "│" << UI::RESET << "\n";
                std::cout << UI::CYAN << "  └─────────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";
                continue;
            }

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
                    std::cout << UI::CYAN << "  │" << UI::RESET << UI::BOLD << UI::WHITE << "  💬 " << conversations[selected].title << UI::RESET;
                    int pad = 58 - conversations[selected].title.length();
                    for (int i = 0; i < pad; i++) std::cout << " ";
                    std::cout << UI::CYAN << "│" << UI::RESET << "\n";
                    std::cout << UI::CYAN << "  └─────────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";
                }
                continue;
            }

            // 其他字符 - 读取完整行
            std::getline(std::cin, input);
            if (key != 27 && key != 13) {
                input = static_cast<char>(key) + input;
            }

            if (input.empty()) continue;

            if (input == "/quit" || input == "/exit") {
                std::cout << "\n" << UI::GREEN << "  👋 再见！" << UI::RESET << "\n\n";
                break;
            }

            if (input == "/help") {
                print_help();
                continue;
            }

            if (input == "/card") {
                print_agent_card(client.get_agent_card());
                continue;
            }

            // 默认：新建对话
            current_ctx = "ctx-" + std::to_string(std::time(nullptr));
            in_conversation = true;
            clear_screen();
            print_conversation_list(conversations, selected);

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

            if (input == "/help") {
                print_help();
                continue;
            }

            if (input == "/card") {
                print_agent_card(client.get_agent_card());
                continue;
            }

            if (input == "/clear") {
                clear_screen();
                continue;
            }

            // 发送消息
            std::cout << "\n" << UI::DIM << "    ⏳ 思考中..." << UI::RESET << "\r";
            std::string response = client.send_message(input, current_ctx);
            std::cout << "\033[2K";
            std::cout << "\n";
            std::cout << UI::BOLD << UI::GREEN << "    🤖 AI: " << UI::RESET << response << "\n\n";
            print_separator();
        }
    }

    return 0;
}
