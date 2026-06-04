/**
 * @file ai_client.cpp
 * @brief FWI Agent 交互式客户端
 *
 * 功能：
 * - 对话列表（时间 + 摘要）
 * - 键盘上下选择
 * - 回车进入对话
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
#include <map>
#include <ctime>
#include <iomanip>
#include <sstream>
#include <cstdlib>

using namespace a2a;
using json = nlohmann::json;

// 颜色定义
const std::string RESET = "\033[0m";
const std::string BOLD = "\033[1m";
const std::string DIM = "\033[2m";
const std::string RED = "\033[31m";
const std::string GREEN = "\033[32m";
const std::string YELLOW = "\033[33m";
const std::string BLUE = "\033[34m";
const std::string MAGENTA = "\033[35m";
const std::string CYAN = "\033[36m";
const std::string WHITE = "\033[37m";
const std::string BG_BLUE = "\033[44m";

static size_t WriteCallback(void* contents, size_t size, size_t nmemb, std::string* userp) {
    userp->append((char*)contents, size * nmemb);
    return size * nmemb;
}

/**
 * @brief 对话上下文信息
 */
struct Conversation {
    std::string context_id;
    std::string title;
    std::string last_message;
    std::string timestamp;
    int message_count;
};

/**
 * @brief FWI Agent 客户端
 */
class AgentClient {
public:
    explicit AgentClient(const std::string& server_url) : server_url_(server_url) {
        curl_global_init(CURL_GLOBAL_DEFAULT);
    }

    ~AgentClient() {
        curl_global_cleanup();
    }

    /**
     * @brief 发送消息
     */
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
                return RED + "错误: " + response_json["error"]["message"].get<std::string>() + RESET;
            }
            if (response_json.contains("result") &&
                response_json["result"].contains("parts") &&
                !response_json["result"]["parts"].empty()) {
                return response_json["result"]["parts"][0]["text"].get<std::string>();
            }
            return RED + "无法解析响应" + RESET;
        } catch (const std::exception& e) {
            return RED + "解析错误: " + std::string(e.what()) + RESET;
        }
    }

    /**
     * @brief 获取 Agent Card（格式化）
     */
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

// 全局变量
std::vector<Conversation> conversations;
std::string current_context = "default";
int selected_index = 0;

/**
 * @brief 获取当前时间字符串
 */
std::string get_current_time() {
    auto now = std::chrono::system_clock::now();
    auto time = std::chrono::system_clock::to_time_t(now);
    std::ostringstream oss;
    oss << std::put_time(std::localtime(&time), "%H:%M:%S");
    return oss.str();
}

/**
 * @brief 格式化 Agent Card
 */
void print_agent_card(const std::string& card_json) {
    try {
        auto card = json::parse(card_json);

        std::cout << "\n" << BOLD << CYAN << "╔══════════════════════════════════════════════════════════════╗" << RESET << "\n";
        std::cout << BOLD << CYAN << "║" << RESET << BOLD << "                    Agent Card                                " << RESET << BOLD << CYAN << "║" << RESET << "\n";
        std::cout << BOLD << CYAN << "╚══════════════════════════════════════════════════════════════╝" << RESET << "\n\n";

        // 名称
        std::cout << BOLD << "  📛 名称: " << RESET << card.value("name", "Unknown") << "\n";

        // 描述
        std::cout << BOLD << "  📝 描述: " << RESET << card.value("description", "No description") << "\n";

        // 版本
        std::cout << BOLD << "  🔢 版本: " << RESET << card.value("version", "Unknown") << "\n";

        // 能力
        if (card.contains("capabilities")) {
            auto caps = card["capabilities"];
            std::cout << BOLD << "  ⚡ 能力: " << RESET;
            std::vector<std::string> cap_list;
            if (caps.value("streaming", false)) cap_list.push_back("流式输出");
            if (caps.value("push_notifications", false)) cap_list.push_back("推送通知");
            if (caps.value("task_management", false)) cap_list.push_back("任务管理");
            for (size_t i = 0; i < cap_list.size(); ++i) {
                if (i > 0) std::cout << ", ";
                std::cout << GREEN << cap_list[i] << RESET;
            }
            std::cout << "\n";
        }

        // 技能
        if (card.contains("skills") && !card["skills"].empty()) {
            std::cout << BOLD << "  🎯 技能:" << RESET << "\n";
            for (const auto& skill : card["skills"]) {
                std::cout << "     • " << YELLOW << skill.value("name", "Unknown") << RESET
                          << ": " << skill.value("description", "") << "\n";
            }
        }

        // 提供商
        if (card.contains("provider")) {
            auto provider = card["provider"];
            std::cout << BOLD << "  🏢 提供商: " << RESET << provider.value("name", "Unknown") << "\n";
        }

        std::cout << "\n" << BOLD << CYAN << "════════════════════════════════════════════════════════════════" << RESET << "\n\n";

    } catch (const std::exception& e) {
        std::cout << RED << "解析 Agent Card 失败: " << e.what() << RESET << "\n";
    }
}

/**
 * @brief 打印对话列表
 */
void print_conversation_list() {
    std::cout << "\n" << BOLD << CYAN << "╔══════════════════════════════════════════════════════════════╗" << RESET << "\n";
    std::cout << BOLD << CYAN << "║" << RESET << BOLD << "                    对话列表                                  " << RESET << BOLD << CYAN << "║" << RESET << "\n";
    std::cout << BOLD << CYAN << "╚══════════════════════════════════════════════════════════════╝" << RESET << "\n\n";

    if (conversations.empty()) {
        std::cout << DIM << "  暂无对话记录" << RESET << "\n";
        std::cout << DIM << "  输入消息开始新对话" << RESET << "\n\n";
    } else {
        for (size_t i = 0; i < conversations.size(); ++i) {
            bool selected = (static_cast<int>(i) == selected_index);
            std::string prefix = selected ? BOLD + WHITE + "  ▶ " : "    ";
            std::string suffix = selected ? RESET : "";

            std::cout << prefix
                      << (selected ? BOLD + WHITE : DIM)
                      << "[" << (i + 1) << "] "
                      << RESET
                      << (selected ? BOLD : "")
                      << conversations[i].title
                      << RESET
                      << DIM << " (" << conversations[i].message_count << " 条消息)"
                      << " " << conversations[i].timestamp
                      << RESET << "\n";

            if (selected) {
                std::cout << "      " << DIM << "└─ " << conversations[i].last_message.substr(0, 50);
                if (conversations[i].last_message.length() > 50) std::cout << "...";
                std::cout << RESET << "\n";
            }
        }
        std::cout << "\n";
    }

    std::cout << BOLD << "  命令:" << RESET << "\n";
    std::cout << "    " << GREEN << "↑/↓" << RESET << "     选择对话\n";
    std::cout << "    " << GREEN << "Enter" << RESET << "   进入选中的对话\n";
    std::cout << "    " << GREEN << "n" << RESET << "       新建对话\n";
    std::cout << "    " << GREEN << "d" << RESET << "       删除选中的对话\n";
    std::cout << "    " << GREEN << "/help" << RESET << "   显示帮助\n";
    std::cout << "    " << GREEN << "/quit" << RESET << "   退出\n\n";
}

/**
 * @brief 打印帮助
 */
void print_help() {
    std::cout << "\n" << BOLD << CYAN << "╔══════════════════════════════════════════════════════════════╗" << RESET << "\n";
    std::cout << BOLD << CYAN << "║" << RESET << BOLD << "                    帮助信息                                  " << RESET << BOLD << CYAN << "║" << RESET << "\n";
    std::cout << BOLD << CYAN << "╚══════════════════════════════════════════════════════════════╝" << RESET << "\n\n";

    std::cout << BOLD << "  对话模式:" << RESET << "\n";
    std::cout << "    直接输入文字发送消息\n";
    std::cout << "    " << GREEN << "/help" << RESET << "     显示此帮助\n";
    std::cout << "    " << GREEN << "/card" << RESET << "     查看 Agent Card\n";
    std::cout << "    " << GREEN << "/list" << RESET << "     返回对话列表\n";
    std::cout << "    " << GREEN << "/clear" << RESET << "    清屏\n";
    std::cout << "    " << GREEN << "/quit" << RESET << "     退出\n\n";

    std::cout << BOLD << "  列表模式:" << RESET << "\n";
    std::cout << "    " << GREEN << "↑/↓" << RESET << "     选择对话\n";
    std::cout << "    " << GREEN << "Enter" << RESET << "   进入对话\n";
    std::cout << "    " << GREEN << "n" << RESET << "       新建对话\n";
    std::cout << "    " << GREEN << "d" << RESET << "       删除对话\n\n";

    std::cout << BOLD << "  快捷键:" << RESET << "\n";
    std::cout << "    " << GREEN << "Ctrl+C" << RESET << "  退出\n";
    std::cout << "    " << GREEN << "Ctrl+L" << RESET << "  清屏\n\n";
}

/**
 * @brief 更新对话列表
 */
void update_conversations(const std::string& context_id, const std::string& user_msg, const std::string& ai_msg) {
    // 查找现有对话
    for (auto& conv : conversations) {
        if (conv.context_id == context_id) {
            conv.last_message = ai_msg.substr(0, 100);
            conv.message_count += 2;
            conv.timestamp = get_current_time();
            return;
        }
    }

    // 创建新对话
    Conversation conv;
    conv.context_id = context_id;
    conv.title = user_msg.substr(0, 30);
    if (user_msg.length() > 30) conv.title += "...";
    conv.last_message = ai_msg.substr(0, 100);
    conv.timestamp = get_current_time();
    conv.message_count = 2;
    conversations.push_back(conv);
}

/**
 * @brief 主函数
 */
int main(int argc, char* argv[]) {
    std::string server_url = "http://localhost:5000";
    if (argc > 1) server_url = argv[1];

    AgentClient client(server_url);

    // 清屏
    std::cout << "\033[2J\033[H";

    // 显示欢迎信息
    std::cout << "\n" << BOLD << CYAN << "╔══════════════════════════════════════════════════════════════╗" << RESET << "\n";
    std::cout << BOLD << CYAN << "║" << RESET << BOLD << "           FWI Agent 科研助手平台                              " << RESET << BOLD << CYAN << "║" << RESET << "\n";
    std::cout << BOLD << CYAN << "╚══════════════════════════════════════════════════════════════╝" << RESET << "\n\n";
    std::cout << DIM << "  连接到: " << server_url << RESET << "\n\n";

    bool in_conversation = false;
    std::string current_context_id = "default";

    // 主循环
    while (true) {
        if (!in_conversation) {
            // 列表模式
            print_conversation_list();

            std::cout << BOLD << "  > " << RESET;
            std::string input;
            std::getline(std::cin, input);

            if (input.empty()) continue;

            if (input == "/quit" || input == "/exit") {
                std::cout << "\n" << GREEN << "  再见！" << RESET << "\n\n";
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

            if (input == "n" || input == "N") {
                // 新建对话
                current_context_id = "ctx-" + std::to_string(std::time(nullptr));
                in_conversation = true;
                std::cout << "\n" << GREEN << "  ✓ 新建对话: " << current_context_id << RESET << "\n\n";
                continue;
            }

            if (input == "d" || input == "D") {
                // 删除对话
                if (!conversations.empty() && selected_index >= 0 && selected_index < static_cast<int>(conversations.size())) {
                    std::cout << "\n" << YELLOW << "  已删除对话: " << conversations[selected_index].title << RESET << "\n";
                    conversations.erase(conversations.begin() + selected_index);
                    if (selected_index >= static_cast<int>(conversations.size())) {
                        selected_index = conversations.size() - 1;
                    }
                }
                continue;
            }

            // 数字选择
            try {
                int num = std::stoi(input);
                if (num >= 1 && num <= static_cast<int>(conversations.size())) {
                    selected_index = num - 1;
                    current_context_id = conversations[selected_index].context_id;
                    in_conversation = true;
                    std::cout << "\n" << GREEN << "  ✓ 进入对话: " << conversations[selected_index].title << RESET << "\n\n";
                    continue;
                }
            } catch (...) {}

            // Enter 进入选中的对话
            if (!conversations.empty()) {
                current_context_id = conversations[selected_index].context_id;
                in_conversation = true;
                std::cout << "\n" << GREEN << "  ✓ 进入对话: " << conversations[selected_index].title << RESET << "\n\n";
            }

        } else {
            // 对话模式
            std::cout << BOLD << BLUE << "  [" << current_context_id << "] > " << RESET;
            std::string input;
            std::getline(std::cin, input);

            if (input.empty()) continue;

            if (input == "/quit" || input == "/exit") {
                std::cout << "\n" << GREEN << "  再见！" << RESET << "\n\n";
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

            if (input == "/list") {
                in_conversation = false;
                continue;
            }

            if (input == "/clear") {
                std::cout << "\033[2J\033[H";
                continue;
            }

            // 发送消息
            std::cout << "\n" << DIM << "  思考中..." << RESET << "\n\n";
            std::string response = client.send_message(input, current_context_id);

            // 更新对话列表
            update_conversations(current_context_id, input, response);

            // 显示响应
            std::cout << BOLD << GREEN << "  AI: " << RESET << response << "\n\n";
        }
    }

    return 0;
}
