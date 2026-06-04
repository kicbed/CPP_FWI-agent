/**
 * @file ai_client.cpp
 * @brief FWI Agent 科研助手 - 交互式客户端
 *
 * 功能：
 * - 对话历史持久化（从 Redis 加载）
 * - 对话列表（时间 + 摘要）
 * - 键盘上下选择
 * - 格式化 Agent Card
 * - 流式输出
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
#include <fstream>
#include <algorithm>

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

    // 前景色
    const std::string RED     = "\033[31m";
    const std::string GREEN   = "\033[32m";
    const std::string YELLOW  = "\033[33m";
    const std::string BLUE    = "\033[34m";
    const std::string MAGENTA = "\033[35m";
    const std::string CYAN    = "\033[36m";
    const std::string WHITE   = "\033[37m";
    const std::string GRAY    = "\033[90m";

    // 背景色
    const std::string BG_DARK  = "\033[48;5;235m";
    const std::string BG_BLUE  = "\033[48;5;24m";
    const std::string BG_GREEN = "\033[48;5;22m";

    // 特殊
    const std::string CLEAR = "\033[2J\033[H";
    const std::string LINE_UP = "\033[A";
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
    std::string title;            // 第一条用户消息作为标题
    std::string last_message;     // 最后一条 AI 回复摘要
    std::string timestamp;        // 最后更新时间
    int message_count = 0;        // 消息总数
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
// Redis 客户端（通过 REST 或直接 CLI）
// ============================================================
class RedisHelper {
public:
    /**
     * @brief 获取所有会话 ID
     */
    static std::vector<std::string> get_all_session_ids() {
        std::vector<std::string> ids;
        // 使用 redis-cli 命令
        FILE* pipe = popen("redis-cli keys 'a2a:session:*' 2>/dev/null", "r");
        if (!pipe) return ids;

        char buffer[256];
        while (fgets(buffer, sizeof(buffer), pipe)) {
            std::string line(buffer);
            // 去掉换行
            while (!line.empty() && (line.back() == '\n' || line.back() == '\r'))
                line.pop_back();
            // 提取 context_id: a2a:session:xxx -> xxx
            if (line.find("a2a:session:") == 0) {
                ids.push_back(line.substr(12));
            }
        }
        pclose(pipe);
        return ids;
    }

    /**
     * @brief 获取会话消息数量
     */
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

    /**
     * @brief 获取最后一条消息
     */
    static std::string get_last_message(const std::string& context_id) {
        std::string cmd = "redis-cli lindex 'a2a:session:" + context_id + "' -1 2>/dev/null";
        FILE* pipe = popen(cmd.c_str(), "r");
        if (!pipe) return "";

        std::string result;
        char buffer[4096];
        while (fgets(buffer, sizeof(buffer), pipe)) {
            result += buffer;
        }
        pclose(pipe);

        // 去掉换行
        while (!result.empty() && (result.back() == '\n' || result.back() == '\r'))
            result.pop_back();

        // 解析 JSON 提取文本
        try {
            auto msg = json::parse(result);
            if (msg.contains("parts") && !msg["parts"].empty()) {
                return msg["parts"][0].value("text", "");
            }
        } catch (...) {}
        return result;
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

    std::string get_server_url() const { return server_url_; }

private:
    std::string server_url_;
    int req_id_ = 0;
};

// ============================================================
// UI 组件
// ============================================================

/**
 * @brief 清屏
 */
void clear_screen() {
    std::cout << UI::CLEAR;
}

/**
 * @brief 打印分隔线
 */
void print_separator(const std::string& color = UI::GRAY) {
    std::cout << color << "  ─────────────────────────────────────────────────────────────" << UI::RESET << "\n";
}

/**
 * @brief 打印标题栏
 */
void print_header(const std::string& title) {
    std::cout << "\n";
    std::cout << UI::BOLD << UI::CYAN << "  ┌────────────────────────────────────────────────────────────┐" << UI::RESET << "\n";
    std::cout << UI::BOLD << UI::CYAN << "  │" << UI::RESET;
    std::cout << UI::BOLD << UI::WHITE << "  " << title;
    // 填充空格
    int padding = 58 - title.length();
    for (int i = 0; i < padding; i++) std::cout << " ";
    std::cout << UI::RESET << UI::BOLD << UI::CYAN << "│" << UI::RESET << "\n";
    std::cout << UI::BOLD << UI::CYAN << "  └────────────────────────────────────────────────────────────┘" << UI::RESET << "\n\n";
}

/**
 * @brief 打印欢迎界面
 */
void print_welcome(const std::string& server_url) {
    clear_screen();
    std::cout << "\n";
    std::cout << UI::BOLD << UI::CYAN << "  ╔══════════════════════════════════════════════════════════════╗" << UI::RESET << "\n";
    std::cout << UI::BOLD << UI::CYAN << "  ║                                                              ║" << UI::RESET << "\n";
    std::cout << UI::BOLD << UI::CYAN << "  ║" << UI::RESET;
    std::cout << UI::BOLD << UI::WHITE << "          🔬  FWI 全波形反演科研助手平台  🔬                ";
    std::cout << UI::RESET << UI::BOLD << UI::CYAN << "║" << UI::RESET << "\n";
    std::cout << UI::BOLD << UI::CYAN << "  ║" << UI::RESET;
    std::cout << UI::DIM << "           Full Waveform Inversion Research Assistant           ";
    std::cout << UI::RESET << UI::BOLD << UI::CYAN << "║" << UI::RESET << "\n";
    std::cout << UI::BOLD << UI::CYAN << "  ║                                                              ║" << UI::RESET << "\n";
    std::cout << UI::BOLD << UI::CYAN << "  ╚══════════════════════════════════════════════════════════════╝" << UI::RESET << "\n";
    std::cout << "\n";
    std::cout << UI::DIM << "    连接到: " << server_url << UI::RESET << "\n\n";
}

/**
 * @brief 格式化 Agent Card
 */
void print_agent_card(const std::string& card_json) {
    try {
        auto card = json::parse(card_json);

        std::cout << "\n";
        std::cout << UI::BOLD << UI::CYAN << "  ╔══════════════════════════════════════════════════════════════╗" << UI::RESET << "\n";
        std::cout << UI::BOLD << UI::CYAN << "  ║" << UI::RESET << UI::BOLD << UI::WHITE << "  📋 Agent Card                                              " << UI::RESET << UI::BOLD << UI::CYAN << "║" << UI::RESET << "\n";
        std::cout << UI::BOLD << UI::CYAN << "  ╚══════════════════════════════════════════════════════════════╝" << UI::RESET << "\n\n";

        // 名称
        std::cout << UI::BOLD << "    📛 名称    " << UI::RESET << card.value("name", "Unknown") << "\n";

        // 描述
        std::cout << UI::BOLD << "    📝 描述    " << UI::RESET << card.value("description", "") << "\n";

        // 版本
        std::cout << UI::BOLD << "    🔢 版本    " << UI::RESET << card.value("version", "") << "\n";

        // 能力
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

        // 技能
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
 * @brief 加载历史对话列表
 */
std::vector<Conversation> load_conversations() {
    std::vector<Conversation> convs;
    auto session_ids = RedisHelper::get_all_session_ids();

    for (const auto& id : session_ids) {
        Conversation conv;
        conv.context_id = id;
        conv.message_count = RedisHelper::get_session_count(id);

        // 获取最后一条消息作为摘要
        std::string last_msg = RedisHelper::get_last_message(id);
        if (last_msg.length() > 60) {
            conv.last_message = last_msg.substr(0, 60) + "...";
        } else {
            conv.last_message = last_msg;
        }

        // 标题：使用 context_id，如果太长就截断
        if (id.length() > 30) {
            conv.title = id.substr(0, 30) + "...";
        } else {
            conv.title = id;
        }

        conv.timestamp = "";  // Redis 不存储时间戳，后续可以改进

        convs.push_back(conv);
    }

    // 按消息数量排序（最近活跃的在前）
    std::sort(convs.begin(), convs.end(),
        [](const Conversation& a, const Conversation& b) {
            return a.message_count > b.message_count;
        });

    return convs;
}

/**
 * @brief 打印对话列表
 */
void print_conversation_list(const std::vector<Conversation>& convs, int selected) {
    print_header("📚 对话历史");

    if (convs.empty()) {
        std::cout << UI::DIM << "    暂无历史对话" << UI::RESET << "\n";
        std::cout << UI::DIM << "    输入任意消息开始新对话" << UI::RESET << "\n\n";
    } else {
        for (size_t i = 0; i < convs.size(); ++i) {
            bool is_selected = (static_cast<int>(i) == selected);

            // 选中高亮
            std::string prefix = is_selected
                ? UI::BOLD + UI::WHITE + "  ▶ "
                : "    ";
            std::string id_color = is_selected ? UI::BOLD + UI::CYAN : UI::DIM;
            std::string msg_color = is_selected ? UI::WHITE : UI::GRAY;
            std::string count_color = is_selected ? UI::GREEN : UI::DIM;

            // 对话编号和标题
            std::cout << prefix << id_color << "[" << (i + 1) << "] " << UI::RESET;
            std::cout << (is_selected ? UI::BOLD : UI::DIM);
            std::cout << convs[i].title << UI::RESET;

            // 消息数量
            std::cout << " " << count_color << "(" << convs[i].message_count << " 条)" << UI::RESET;
            std::cout << "\n";

            // 摘要
            if (is_selected && !convs[i].last_message.empty()) {
                std::cout << "      " << UI::DIM << "└─ " << convs[i].last_message << UI::RESET << "\n";
            }
        }
        std::cout << "\n";
    }

    // 操作提示
    print_separator();
    std::cout << UI::DIM << "    n" << UI::RESET << " 新建对话   ";
    std::cout << UI::DIM << "1-9" << UI::RESET << " 选择对话   ";
    std::cout << UI::DIM << "Enter" << UI::RESET << " 进入   ";
    std::cout << UI::DIM << "/help" << UI::RESET << " 帮助   ";
    std::cout << UI::DIM << "/quit" << UI::RESET << " 退出\n\n";
}

/**
 * @brief 打印帮助
 */
void print_help() {
    std::cout << "\n";
    print_header("📖 帮助");

    std::cout << UI::BOLD << "    对话模式:" << UI::RESET << "\n";
    std::cout << "      直接输入文字    发送消息\n";
    std::cout << "      " << UI::GREEN << "/help" << UI::RESET << "           显示帮助\n";
    std::cout << "      " << UI::GREEN << "/card" << UI::RESET << "           查看 Agent Card\n";
    std::cout << "      " << UI::GREEN << "/list" << UI::RESET << "           返回对话列表\n";
    std::cout << "      " << UI::GREEN << "/clear" << UI::RESET << "          清屏\n";
    std::cout << "      " << UI::GREEN << "/quit" << UI::RESET << "           退出\n\n";

    std::cout << UI::BOLD << "    列表模式:" << UI::RESET << "\n";
    std::cout << "      " << UI::GREEN << "↑/↓ 或 数字" << UI::RESET << "  选择对话\n";
    std::cout << "      " << UI::GREEN << "Enter" << UI::RESET << "         进入对话\n";
    std::cout << "      " << UI::GREEN << "n" << UI::RESET << "             新建对话\n\n";
}

/**
 * @brief 获取当前时间
 */
std::string now_time() {
    auto now = std::chrono::system_clock::now();
    auto t = std::chrono::system_clock::to_time_t(now);
    std::ostringstream oss;
    oss << std::put_time(std::localtime(&t), "%H:%M:%S");
    return oss.str();
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
            // ========== 列表模式 ==========
            print_conversation_list(conversations, selected);

            std::cout << UI::BOLD << UI::CYAN << "  > " << UI::RESET;
            std::string input;
            if (!std::getline(std::cin, input)) break;
            if (input.empty()) continue;

            // 退出
            if (input == "/quit" || input == "/exit" || input == "q") {
                std::cout << "\n" << UI::GREEN << "  👋 再见！" << UI::RESET << "\n\n";
                break;
            }

            // 帮助
            if (input == "/help" || input == "h") {
                print_help();
                continue;
            }

            // Agent Card
            if (input == "/card") {
                print_agent_card(client.get_agent_card());
                continue;
            }

            // 新建对话
            if (input == "n" || input == "N") {
                current_ctx = "ctx-" + std::to_string(std::time(nullptr));
                in_conversation = true;
                clear_screen();
                print_header("💬 新对话: " + current_ctx);
                continue;
            }

            // 数字选择
            try {
                int num = std::stoi(input);
                if (num >= 1 && num <= static_cast<int>(conversations.size())) {
                    selected = num - 1;
                    current_ctx = conversations[selected].context_id;
                    in_conversation = true;
                    clear_screen();
                    print_header("💬 " + conversations[selected].title);
                    // 显示历史消息
                    if (!conversations[selected].last_message.empty()) {
                        std::cout << UI::DIM << "    最近: " << conversations[selected].last_message << UI::RESET << "\n\n";
                    }
                    continue;
                }
            } catch (...) {}

            // Enter 进入
            if (!conversations.empty() && selected >= 0 && selected < static_cast<int>(conversations.size())) {
                current_ctx = conversations[selected].context_id;
                in_conversation = true;
                clear_screen();
                print_header("💬 " + conversations[selected].title);
                if (!conversations[selected].last_message.empty()) {
                    std::cout << UI::DIM << "    最近: " << conversations[selected].last_message << UI::RESET << "\n\n";
                }
                continue;
            }

            // 没有对话时，直接新建
            current_ctx = "ctx-" + std::to_string(std::time(nullptr));
            in_conversation = true;
            clear_screen();
            print_header("💬 新对话");

        } else {
            // ========== 对话模式 ==========
            std::cout << UI::BOLD << UI::BLUE << "    [" << current_ctx << "] > " << UI::RESET;
            std::string input;
            if (!std::getline(std::cin, input)) break;
            if (input.empty()) continue;

            // 退出
            if (input == "/quit" || input == "/exit") {
                std::cout << "\n" << UI::GREEN << "  👋 再见！" << UI::RESET << "\n\n";
                break;
            }

            // 返回列表
            if (input == "/list" || input == "/back") {
                in_conversation = false;
                // 重新加载对话列表
                conversations = load_conversations();
                clear_screen();
                print_welcome(server_url);
                continue;
            }

            // 帮助
            if (input == "/help") {
                print_help();
                continue;
            }

            // Agent Card
            if (input == "/card") {
                print_agent_card(client.get_agent_card());
                continue;
            }

            // 清屏
            if (input == "/clear") {
                clear_screen();
                print_header("💬 " + current_ctx);
                continue;
            }

            // 发送消息
            std::cout << "\n" << UI::DIM << "    ⏳ 思考中..." << UI::RESET << "\r";
            std::string response = client.send_message(input, current_ctx);

            // 清除 "思考中" 并显示回复
            std::cout << "\033[2K";  // 清除当前行
            std::cout << "\n";
            std::cout << UI::BOLD << UI::GREEN << "    🤖 AI: " << UI::RESET << response << "\n\n";
            print_separator();
        }
    }

    return 0;
}
