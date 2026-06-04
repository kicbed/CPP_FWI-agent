/**
 * @file grpc_ai_client.cpp
 * @brief FWI Agent gRPC 客户端 - 稳定版（与 HTTP 相同 UI）
 */
#pragma GCC diagnostic ignored "-Wunused-result"

#include <curl/curl.h>
#include <nlohmann/json.hpp>
#include <iostream>
#include <string>
#include <vector>
#include <sstream>
#include <algorithm>
#include <cstdlib>
#include <cstdio>
#include <ctime>

using json = nlohmann::json;

// 颜色
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

struct Conv {
    std::string id, title, last_msg;
    int count = 0;
};

std::string redis(const std::string& cmd) {
    FILE* p = popen(("redis-cli " + cmd + " 2>/dev/null").c_str(), "r");
    if (!p) return "";
    std::string r; char b[8192];
    while (fgets(b, sizeof(b), p)) r += b;
    pclose(p);
    return r;
}

std::vector<Conv> load_convos() {
    std::vector<Conv> out;
    std::string ids = redis("keys 'a2a:session:*'");
    std::istringstream iss(ids);
    std::string line;
    while (std::getline(iss, line)) {
        while (!line.empty() && (line.back()=='\n'||line.back()=='\r')) line.pop_back();
        if (line.empty() || line.find("a2a:session:") != 0) continue;
        Conv c;
        c.id = line.substr(12);
        c.count = std::atoi(redis("llen 'a2a:session:" + c.id + "'").c_str());
        std::string title_raw = redis("lindex 'a2a:session:" + c.id + "' 0");
        try {
            auto j = json::parse(title_raw);
            if (j.contains("parts") && !j["parts"].empty())
                c.title = j["parts"][0].value("text", "");
        } catch(...) {}
        if (c.title.empty()) c.title = c.id;
        std::replace(c.title.begin(), c.title.end(), '\n', ' ');
        if (c.title.length() > 30) c.title = c.title.substr(0, 30) + "...";
        std::string last = redis("lindex 'a2a:session:" + c.id + "' -1");
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
    std::string all = redis("lrange 'a2a:session:" + ctx_id + "' 0 -1");
    std::istringstream iss(all);
    std::string line;
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
        } catch(...) {}
    }
}

std::string g_last_curl_error;

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
    CURLcode res = curl_easy_perform(curl);
    if (res != CURLE_OK) {
        g_last_curl_error = curl_easy_strerror(res);
    } else {
        g_last_curl_error.clear();
    }
    curl_slist_free_all(h);
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
    if (resp.empty()) {
        if (g_last_curl_error.find("Connection refused") != std::string::npos)
            return C::R_ + "连接被拒绝，请确认 Agent 系统已启动 (运行 start_system.sh)" + C::R;
        if (g_last_curl_error.find("timed out") != std::string::npos)
            return C::R_ + "请求超时（120秒），服务可能繁忙" + C::R;
        return C::R_ + "服务无响应: " + g_last_curl_error + C::R;
    }
    try {
        auto j = json::parse(resp);
        if (j.contains("error")) return C::R_ + "错误: " + j["error"]["message"].get<std::string>() + C::R;
        if (j.contains("result") && j["result"].contains("parts") && !j["result"]["parts"].empty())
            return j["result"]["parts"][0]["text"].get<std::string>();
        return C::R_ + "无法解析响应" + C::R;
    } catch(const std::exception& e) {
        return C::R_ + "解析失败: " + e.what() + C::R;
    }
}

void logo() {
    std::cout << C::CL;
    std::cout << "\n" << C::CY << "  ╔═══════════════════════════════════════════════════════════════════╗" << C::R << "\n";
    std::cout << C::CY << "  ║" << C::R << C::B << C::W << "            🔬  FWI 全波形反演科研助手平台  🔬                  " << C::R << C::CY << "║" << C::R << "\n";
    std::cout << C::CY << "  ║" << C::R << C::D << "                       gRPC 模式                                  " << C::R << C::CY << "║" << C::R << "\n";
    std::cout << C::CY << "  ╚═══════════════════════════════════════════════════════════════════╝" << C::R << "\n\n";
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

int main(int argc, char* argv[]) {
    std::string url = "http://localhost:5000";
    if (argc > 1) {
        std::string addr = argv[1];
        if (addr.find("http") == std::string::npos) url = "http://" + addr;
        else url = addr;
    }

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

            if (input == "/quit" || input == "/exit") {
                std::cout << "\n" << C::G << "  👋 再见！" << C::R << "\n\n";
                break;
            }

            if (input == "/help") {
                std::cout << "\n" << C::B << "  命令:" << C::R << "\n";
                std::cout << "    " << C::G << "数字" << C::R << "    选择对话\n";
                std::cout << "    " << C::G << "n" << C::R << "       新建对话\n";
                std::cout << "    " << C::G << "d 数字" << C::R << "  删除对话\n";
                std::cout << "    " << C::G << "/list" << C::R << "    返回列表\n";
                std::cout << "    " << C::G << "/quit" << C::R << "   退出\n\n";
                continue;
            }

            if (input == "n" || input == "N") {
                ctx = "ctx-" + std::to_string(std::time(nullptr));
                chat_mode = true;
                std::cout << C::CL;
                std::cout << "\n" << C::CY << "  ┌─────────────────────────────────────────────────────────────────┐" << C::R << "\n";
                std::cout << C::CY << "  │" << C::R << C::B << C::W << "  💬 新对话";
                for (int i = 0; i < 54; i++) std::cout << " ";
                std::cout << C::CY << "│" << C::R << "\n";
                std::cout << C::CY << "  └─────────────────────────────────────────────────────────────────┘" << C::R << "\n\n";
                continue;
            }

            if (input.length() >= 2 && (input[0] == 'd' || input[0] == 'D')) {
                std::string num_str = input.substr(1);
                try {
                    int num = std::stoi(num_str);
                    if (num >= 1 && num <= static_cast<int>(convs.size())) {
                        std::string to_del = convs[num-1].id;
                        std::string to_del_name = convs[num-1].title;
                        redis("del 'a2a:session:" + to_del + "' 'a2a:history:" + to_del + "' 'a2a:task:" + to_del + "'");
                        convs = load_convos();
                        std::cout << C::G << "  ✓ 已删除: " << to_del_name << C::R << "\n\n";
                    }
                } catch(...) {}
                continue;
            }

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
                    show_history(ctx);
                    std::cout << C::GR << "  ─────────────────────────────────────────────────────────────────" << C::R << "\n\n";
                    continue;
                }
            } catch(...) {}

            ctx = "ctx-" + std::to_string(std::time(nullptr));
            chat_mode = true;
            std::cout << C::CL;
            std::cout << "\n" << C::G << "  ✓ 开始新对话" << C::R << "\n\n";
        } else {
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

            std::cout << C::D << "  ⏳ 思考中..." << C::R << "\r" << std::flush;
            std::string resp = send_msg(url, input, ctx);
            std::cout << "\n";
            std::cout << C::B << C::G << "  🤖 AI: " << C::R << resp << "\n\n";
            std::cout << C::GR << "  ─────────────────────────────────────────────────────────────────" << C::R << "\n\n";
        }
    }

    return 0;
}
