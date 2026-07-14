/**
 * @file ai_client.cpp
 * @brief FWI Agent 交互式客户端 - 稳定版
 *
 * 功能：
 * - 数字选择对话 + Enter 进入
 * - 对话历史显示
 * - 删除对话
 * - 从 Redis 加载历史
 */

#include <a2a/models/agent_message.hpp>
#include <a2a/models/message_part.hpp>
#include <a2a/core/jsonrpc_request.hpp>
#include <a2a/core/jsonrpc_response.hpp>
#include "agent_rpc/common/redis_cli.h"
#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <string>
#include <vector>
#include <algorithm>
#include <cstdlib>
#include <cstdio>
#include <atomic>
#include <chrono>
#include <cstdint>

using namespace a2a;
using json = nlohmann::json;

static std::string new_context_id() {
    static std::atomic<std::uint64_t> sequence{0};
    const auto now = std::chrono::system_clock::now().time_since_epoch().count();
    return "ctx-cli-" + std::to_string(now) + "-" +
           std::to_string(sequence.fetch_add(1, std::memory_order_relaxed) + 1);
}

// ============================================================
// 颜色
// ============================================================
namespace C {
    const std::string R = "\033[0m";
    const std::string B = "\033[1m";
    const std::string D = "\033[2m";
    const std::string R_ = "\033[31m";
    const std::string G = "\033[32m";
    const std::string Y = "\033[33m";
    const std::string BL = "\033[34m";
    const std::string CY = "\033[36m";
    const std::string W = "\033[37m";
    const std::string GR = "\033[90m";
    const std::string CL = "\033[2J\033[H";
}

static size_t WriteCallback(void* c, size_t s, size_t n, std::string* p) {
    p->append((char*)c, s*n);
    return s*n;
}

// ============================================================
// 对话
// ============================================================
struct Conv {
    std::string id, title, last_msg;
    int count = 0;
};

// ============================================================
// Redis
// ============================================================
std::string redis(const std::vector<std::string>& arguments) {
    return agent_rpc::common::run_redis_cli(arguments);
}

std::vector<Conv> load_convos() {
    std::vector<Conv> out;
    std::string ids = redis({"keys", "a2a:session:*"});
    std::istringstream iss(ids);
    std::string line;
    while (std::getline(iss, line)) {
        while (!line.empty() && (line.back()=='\n'||line.back()=='\r')) line.pop_back();
        if (line.empty() || line.find("a2a:session:") != 0) continue;
        Conv c;
        c.id = line.substr(12);
        c.count = std::atoi(redis({"llen", "a2a:session:" + c.id}).c_str());
        // 标题：第一条用户消息
        std::string title_raw = redis({"lindex", "a2a:session:" + c.id, "0"});
        try {
            auto j = json::parse(title_raw);
            if (j.contains("parts") && !j["parts"].empty())
                c.title = j["parts"][0].value("text", "");
        } catch(...) {}
        if (c.title.empty()) c.title = c.id;
        std::replace(c.title.begin(), c.title.end(), '\n', ' ');
        if (c.title.length() > 30) c.title = c.title.substr(0, 30) + "...";
        // 最后一条消息
        std::string last = redis({"lindex", "a2a:session:" + c.id, "-1"});
        try {
            auto j = json::parse(last);
            if (j.contains("parts") && !j["parts"].empty()) {
                c.last_msg = j["parts"][0].value("text", "");
                if (c.last_msg.length() > 60) c.last_msg = c.last_msg.substr(0, 60) + "...";
            }
        } catch(...) {}
        out.push_back(c);
    }
    std::sort(out.begin(), out.end(), [](const Conv& a, const Conv& b){ return a.count > b.count; });
    return out;
}

void show_history(const std::string& ctx_id) {
    std::string all = redis({"lrange", "a2a:session:" + ctx_id, "0", "-1"});
    std::istringstream iss(all);
    std::string line;
    int shown = 0;
    while (std::getline(iss, line)) {
        while (!line.empty() && (line.back()=='\n'||line.back()=='\r')) line.pop_back();
        if (line.empty()) continue;
        try {
            auto j = json::parse(line);
            std::string role = j.value("role", "");
            std::string text;
            if (j.contains("parts") && !j["parts"].empty())
                text = j["parts"][0].value("text", "");
            if (text.empty()) continue;
            if (role == "user")
                std::cout << C::B << C::BL << "  👤 " << C::R << text << "\n\n";
            else {
                if (text.length() > 400) text = text.substr(0, 400) + "...";
                std::cout << C::B << C::G << "  🤖 " << C::R << text << "\n\n";
            }
            shown++;
        } catch(...) {}
    }
    if (shown == 0)
        std::cout << C::D << "  (暂无消息)" << C::R << "\n\n";
}

// ============================================================
// HTTP
// ============================================================
std::string http_post(const std::string& url, const std::string& body) {
    CURL* curl = curl_easy_init();
    if (!curl) return "";
    std::string r;
    struct curl_slist* h = nullptr;
    h = curl_slist_append(h, "Content-Type: application/json");
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_POSTFIELDS, body.c_str());
    curl_easy_setopt(curl, CURLOPT_HTTPHEADER, h);
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &r);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 120L);
    curl_easy_perform(curl);
    curl_slist_free_all(h);
    curl_easy_cleanup(curl);
    return r;
}

std::string http_get(const std::string& url) {
    CURL* curl = curl_easy_init();
    if (!curl) return "";
    std::string r;
    curl_easy_setopt(curl, CURLOPT_URL, url.c_str());
    curl_easy_setopt(curl, CURLOPT_WRITEFUNCTION, WriteCallback);
    curl_easy_setopt(curl, CURLOPT_WRITEDATA, &r);
    curl_easy_setopt(curl, CURLOPT_TIMEOUT, 10L);
    curl_easy_perform(curl);
    curl_easy_cleanup(curl);
    return r;
}

std::string send_msg(const std::string& url, const std::string& txt, const std::string& ctx) {
    static int rid = 0;
    json req = {
        {"jsonrpc","2.0"}, {"id",std::to_string(++rid)}, {"method","message/send"},
        {"params",{{"message",{{"role","user"},{"contextId",ctx},{"parts",{{{{"kind","text"},{"text",txt}}}}}}},{"historyLength",20}}}
    };
    std::string resp = http_post(url, req.dump());
    try {
        auto j = json::parse(resp);
        if (j.contains("error")) return C::R_ + "错误: " + j["error"]["message"].get<std::string>() + C::R;
        if (j.contains("result") && j["result"].contains("parts") && !j["result"]["parts"].empty())
            return j["result"]["parts"][0]["text"].get<std::string>();
        return C::R_ + "无法解析" + C::R;
    } catch(...) {
        return C::R_ + "解析失败" + C::R;
    }
}

// ============================================================
// UI
// ============================================================
void logo() {
    std::cout << C::CL;
    std::cout << "\n" << C::CY << "  ╔═══════════════════════════════════════════════════════════════════╗" << C::R << "\n";
    std::cout << C::CY << "  ║" << C::R << C::B << C::W << "            🔬  FWI 全波形反演科研助手平台  🔬                  " << C::R << C::CY << "║" << C::R << "\n";
    std::cout << C::CY << "  ╚═══════════════════════════════════════════════════════════════════╝" << C::R << "\n\n";
}

void print_card(const std::string& raw) {
    try {
        auto c = json::parse(raw);
        std::cout << "\n" << C::B << C::CY << "  ┌─ Agent Card ──────────────────────────────────────────────┐" << C::R << "\n";
        std::cout << "  │ " << C::B << "名称:" << C::R << " " << c.value("name","") << "\n";
        std::cout << "  │ " << C::B << "描述:" << C::R << " " << c.value("description","") << "\n";
        if (c.contains("skills") && !c["skills"].empty()) {
            std::cout << "  │ " << C::B << "技能:" << C::R << "\n";
            for (auto& s : c["skills"])
                std::cout << "  │   • " << C::Y << s.value("name","") << C::R << ": " << s.value("description","") << "\n";
        }
        std::cout << C::B << C::CY << "  └────────────────────────────────────────────────────────────┘" << C::R << "\n\n";
    } catch(...) {}
}

void print_list(const std::vector<Conv>& convs) {
    std::cout << C::CY << "  ┌─────────────────────────────────────────────────────────────────┐" << C::R << "\n";
    std::cout << C::CY << "  │" << C::R << C::B << C::W << "  📚 对话历史" << C::R;
    for (int i = 0; i < 52; i++) std::cout << " ";
    std::cout << C::CY << "│" << C::R << "\n";
    std::cout << C::CY << "  └─────────────────────────────────────────────────────────────────┘" << C::R << "\n\n";

    if (convs.empty()) {
        std::cout << C::D << "    暂无历史对话，直接输入消息开始新对话" << C::R << "\n\n";
    } else {
        for (size_t i = 0; i < convs.size(); ++i) {
            std::cout << C::B << C::W << "  [" << (i+1) << "] " << C::R << convs[i].title;
            std::cout << " " << C::GR << "(" << convs[i].count << " 条)" << C::R << "\n";
        }
        std::cout << "\n";
    }

    std::cout << C::GR << "  ─────────────────────────────────────────────────────────────────" << C::R << "\n";
    std::cout << C::D << "  输入数字" << C::R << " 进入对话  " << C::D << "n" << C::R << " 新建  " << C::D << "d 数字" << C::R << " 删除  " << C::D << "/help" << C::R << " 帮助  " << C::D << "/quit" << C::R << " 退出\n\n";
}

// ============================================================
// Main
// ============================================================
int main(int argc, char* argv[]) {
    std::string url = "http://localhost:5000";
    if (argc > 1) url = argv[1];

    auto convs = load_convos();
    std::string ctx;
    bool chat_mode = false;

    logo();

    while (true) {
        if (!chat_mode) {
            print_list(convs);
            std::cout << C::B << C::CY << "  > " << C::R;

            std::string input;
            if (!std::getline(std::cin, input)) break;
            if (input.empty()) continue;

            // /quit
            if (input == "/quit" || input == "/exit") {
                std::cout << "\n" << C::G << "  👋 再见！" << C::R << "\n\n";
                break;
            }

            // /help
            if (input == "/help") {
                std::cout << "\n" << C::B << "  命令:" << C::R << "\n";
                std::cout << "    " << C::G << "数字" << C::R << "    选择对话\n";
                std::cout << "    " << C::G << "n" << C::R << "       新建对话\n";
                std::cout << "    " << C::G << "d 数字" << C::R << "  删除对话\n";
                std::cout << "    " << C::G << "/card" << C::R << "    查看 Agent Card\n";
                std::cout << "    " << C::G << "/list" << C::R << "    返回列表\n";
                std::cout << "    " << C::G << "/quit" << C::R << "   退出\n\n";
                continue;
            }

            // /card
            if (input == "/card") {
                print_card(http_get(url + "/.well-known/agent-card.json"));
                continue;
            }

            // n = 新建
            if (input == "n" || input == "N") {
                ctx = new_context_id();
                chat_mode = true;
                std::cout << C::CL;
                std::cout << "\n" << C::CY << "  ┌─────────────────────────────────────────────────────────────────┐" << C::R << "\n";
                std::cout << C::CY << "  │" << C::R << C::B << C::W << "  💬 新对话";
                for (int i = 0; i < 54; i++) std::cout << " ";
                std::cout << C::CY << "│" << C::R << "\n";
                std::cout << C::CY << "  └─────────────────────────────────────────────────────────────────┘" << C::R << "\n\n";
                continue;
            }

            // d 数字 = 删除
            if (input.length() >= 2 && (input[0] == 'd' || input[0] == 'D')) {
                std::string num_str = input.substr(1);
                try {
                    int num = std::stoi(num_str);
                    if (num >= 1 && num <= static_cast<int>(convs.size())) {
                        std::string to_del = convs[num-1].id;
                        std::string to_del_name = convs[num-1].title;
                        redis({"del", "a2a:session:" + to_del,
                               "a2a:history:" + to_del, "a2a:task:" + to_del});
                        convs = load_convos();
                        std::cout << C::G << "  ✓ 已删除: " << to_del_name << C::R << "\n\n";
                    }
                } catch(...) {}
                continue;
            }

            // 数字 = 进入对话
            try {
                int num = std::stoi(input);
                if (num >= 1 && num <= static_cast<int>(convs.size())) {
                    ctx = convs[num-1].id;
                    chat_mode = true;
                    std::cout << C::CL;
                    std::cout << "\n" << C::CY << "  ┌─────────────────────────────────────────────────────────────────┐" << C::R << "\n";
                    std::cout << C::CY << "  │" << C::R << C::B << C::W << "  💬 " << convs[num-1].title;
                    int pad = 58 - convs[num-1].title.length();
                    for (int i = 0; i < pad; i++) std::cout << " ";
                    std::cout << C::CY << "│" << C::R << "\n";
                    std::cout << C::CY << "  └─────────────────────────────────────────────────────────────────┘" << C::R << "\n\n";
                    // 显示历史
                    show_history(ctx);
                    std::cout << C::GR << "  ─────────────────────────────────────────────────────────────────" << C::R << "\n\n";
                    continue;
                }
            } catch(...) {}

            // 默认：新建对话
            ctx = new_context_id();
            chat_mode = true;
            std::cout << C::CL;
            std::cout << "\n" << C::G << "  ✓ 开始新对话" << C::R << "\n\n";
        } else {
            // ===== 对话模式 =====
            std::cout << C::B << C::BL << "  [" << ctx << "] > " << C::R;
            std::string input;
            if (!std::getline(std::cin, input)) break;
            if (input.empty()) continue;

            if (input == "/quit" || input == "/exit") break;
            if (input == "/list" || input == "/back") {
                chat_mode = false;
                convs = load_convos();
                std::cout << C::CL;
                logo();
                continue;
            }
            if (input == "/help") {
                std::cout << "\n  " << C::G << "/list" << C::R << " 返回列表, " << C::G << "/quit" << C::R << " 退出\n\n";
                continue;
            }
            if (input == "/card") {
                print_card(http_get(url + "/.well-known/agent-card.json"));
                continue;
            }

            // 发送消息
            std::cout << C::D << "  ⏳ 思考中..." << C::R << "\r" << std::flush;
            std::string resp = send_msg(url, input, ctx);
            std::cout << "\n";
            std::cout << C::B << C::G << "  🤖 AI: " << C::R << resp << "\n\n";
            std::cout << C::GR << "  ─────────────────────────────────────────────────────────────────" << C::R << "\n\n";
        }
    }

    return 0;
}
