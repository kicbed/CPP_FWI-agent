/**
 * @file FWIMetadata.cpp
 * @brief MCP FWI Metadata Plugin - FWI 元数据工具
 *
 * 提供 FWI 相关的元数据查询工具：
 * - list_models: 列出可用速度模型
 * - inspect_model: 查看模型详细信息
 * - list_datasets: 列出可用数据集
 * - inspect_dataset: 查看数据集详细信息
 * - formula_helper: 查询 FWI 相关公式
 * - search_fwi_notes: 搜索 FWI 知识库
 */

#include <string>
#include <fstream>
#include <sstream>
#include <vector>
#include "PluginAPI.h"
#include "json.hpp"

using json = nlohmann::json;

// 资源目录路径（相对于 MCP Server 可执行文件）
static std::string RESOURCE_DIR = "../../resources";

// 读取 JSON 文件
static json read_json_file(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open()) {
        return json::object();
    }
    json data;
    file >> data;
    return data;
}

// 读取 Markdown 文件
static std::string read_markdown_file(const std::string& path) {
    std::ifstream file(path);
    if (!file.is_open()) {
        return "";
    }
    std::ostringstream oss;
    oss << file.rdbuf();
    return oss.str();
}

// 工具定义
static PluginTool methods[] = {
    {
        "list_models",
        "列出可用的 FWI 速度模型。返回模型 ID、名称、描述、维度等信息。",
        "{\"type\":\"object\",\"properties\":{},\"required\":[]}"
    },
    {
        "inspect_model",
        "查看指定速度模型的详细信息。输入模型 ID，返回完整的模型 metadata。",
        "{\"type\":\"object\",\"properties\":{\"model_id\":{\"type\":\"string\",\"description\":\"模型 ID，如 marmousi2\"}},\"required\":[\"model_id\"]}"
    },
    {
        "list_datasets",
        "列出可用的 FWI 数据集。返回数据集 ID、名称、描述、采集参数等信息。",
        "{\"type\":\"object\",\"properties\":{},\"required\":[]}"
    },
    {
        "inspect_dataset",
        "查看指定数据集的详细信息。输入数据集 ID，返回完整的数据集 metadata。",
        "{\"type\":\"object\",\"properties\":{\"dataset_id\":{\"type\":\"string\",\"description\":\"数据集 ID，如 marmousi2_synthetic\"}},\"required\":[\"dataset_id\"]}"
    },
    {
        "formula_helper",
        "查询 FWI 相关公式。输入公式名称，返回 LaTeX 格式的公式和解释。",
        "{\"type\":\"object\",\"properties\":{\"formula_name\":{\"type\":\"string\",\"description\":\"公式名称，如 gradient、adjoint、objective\"}},\"required\":[\"formula_name\"]}"
    },
    {
        "search_fwi_notes",
        "搜索 FWI 知识库。输入关键词，返回相关文档摘要。",
        "{\"type\":\"object\",\"properties\":{\"query\":{\"type\":\"string\",\"description\":\"搜索关键词\"}},\"required\":[\"query\"]}"
    }
};

const char* GetNameImpl() { return "fwi-metadata"; }
const char* GetVersionImpl() { return "1.0.0"; }
PluginType GetTypeImpl() { return PLUGIN_TYPE_TOOLS; }
int InitializeImpl() { return 1; }

char* HandleRequestImpl(const char* req) {
    json response;
    response["content"] = json::array();
    response["isError"] = false;

    try {
        auto request = json::parse(req);
        std::string toolName = request["params"]["name"].get<std::string>();
        auto args = request["params"]["arguments"];

        std::string resultText;

        if (toolName == "list_models") {
            // 列出所有速度模型
            auto models_data = read_json_file(RESOURCE_DIR + "/fwi_models/model_metadata.json");

            if (models_data.contains("models")) {
                json result = json::array();
                for (const auto& model : models_data["models"]) {
                    json item;
                    item["id"] = model["id"];
                    item["name"] = model["name"];
                    item["description"] = model["description"];
                    item["dimensions"] = model["dimensions"];
                    item["velocity_range"] = model["velocity_range"];
                    result.push_back(item);
                }
                resultText = "可用速度模型:\n" + result.dump(2);
            } else {
                resultText = "暂无速度模型数据";
            }
        }
        else if (toolName == "inspect_model") {
            // 查看指定模型详情
            std::string model_id = args["model_id"].get<std::string>();
            auto models_data = read_json_file(RESOURCE_DIR + "/fwi_models/model_metadata.json");

            bool found = false;
            if (models_data.contains("models")) {
                for (const auto& model : models_data["models"]) {
                    if (model["id"] == model_id) {
                        resultText = "模型详情:\n" + model.dump(2);
                        found = true;
                        break;
                    }
                }
            }

            if (!found) {
                resultText = "未找到模型: " + model_id + "\n可用模型: marmousi2, overthrust, salt2d";
            }
        }
        else if (toolName == "list_datasets") {
            // 列出所有数据集
            auto datasets_data = read_json_file(RESOURCE_DIR + "/fwi_datasets/dataset_metadata.json");

            if (datasets_data.contains("datasets")) {
                json result = json::array();
                for (const auto& ds : datasets_data["datasets"]) {
                    json item;
                    item["id"] = ds["id"];
                    item["name"] = ds["name"];
                    item["description"] = ds["description"];
                    item["acquisition_type"] = ds["acquisition"]["type"];
                    result.push_back(item);
                }
                resultText = "可用数据集:\n" + result.dump(2);
            } else {
                resultText = "暂无数据集";
            }
        }
        else if (toolName == "inspect_dataset") {
            // 查看指定数据集详情
            std::string dataset_id = args["dataset_id"].get<std::string>();
            auto datasets_data = read_json_file(RESOURCE_DIR + "/fwi_datasets/dataset_metadata.json");

            bool found = false;
            if (datasets_data.contains("datasets")) {
                for (const auto& ds : datasets_data["datasets"]) {
                    if (ds["id"] == dataset_id) {
                        resultText = "数据集详情:\n" + ds.dump(2);
                        found = true;
                        break;
                    }
                }
            }

            if (!found) {
                resultText = "未找到数据集: " + dataset_id;
            }
        }
        else if (toolName == "formula_helper") {
            // 查询 FWI 公式
            std::string formula_name = args["formula_name"].get<std::string>();

            // 内置公式库
            json formulas;
            formulas["objective"] = {
                {"name", "FWI 目标函数"},
                {"latex", "$J(m) = \\frac{1}{2} \\sum_{s,r} \\| d^{obs}_{s,r}(t) - d^{syn}_{s,r}(t; m) \\|^2$"},
                {"description", "最小二乘目标函数，最小化观测数据与模拟数据的残差"}
            };
            formulas["gradient"] = {
                {"name", "FWI 梯度（伴随状态法）"},
                {"latex", "$\\nabla_m J = -\\sum_s \\int_0^T u(x,t) \\cdot \\frac{\\partial^2 u^\\dagger}{\\partial t^2}(x,t) dt$"},
                {"description", "通过伴随状态法高效计算梯度"}
            };
            formulas["adjoint"] = {
                {"name", "伴随方程"},
                {"latex", "$\\frac{1}{v^2} \\frac{\\partial^2 u^\\dagger}{\\partial t^2} - \\nabla^2 u^\\dagger = r(t)$"},
                {"description", "伴随波场由残差作为源、时间反向传播得到"}
            };
            formulas["update"] = {
                {"name", "模型更新（梯度下降）"},
                {"latex", "$m_{k+1} = m_k - \\alpha_k \\nabla_m J(m_k)$"},
                {"description", "沿梯度方向更新模型"}
            };
            formulas["cycle_skip"] = {
                {"name", "Cycle skipping 判据"},
                {"latex", "$\\max|\\Delta t| > \\frac{1}{2 f_{dom}}$"},
                {"description", "当走时偏差超过半周期时发生 cycle skipping"}
            };
            formulas["envelope"] = {
                {"name", "包络目标函数"},
                {"latex", "$J_{env} = \\frac{1}{2} \\sum \\| |d^{obs}| - |d^{syn}| \\|^2$"},
                {"description", "对相位不敏感，容忍走时差"}
            };

            if (formulas.contains(formula_name)) {
                auto f = formulas[formula_name];
                resultText = f["name"].get<std::string>() + "\n"
                           + "公式: " + f["latex"].get<std::string>() + "\n"
                           + "说明: " + f["description"].get<std::string>();
            } else {
                resultText = "可用公式: objective, gradient, adjoint, update, cycle_skip, envelope";
            }
        }
        else if (toolName == "search_fwi_notes") {
            // 搜索知识库
            std::string query = args["query"].get<std::string>();

            // 读取所有知识文件
            std::vector<std::string> files = {
                RESOURCE_DIR + "/fwi_knowledge/fwi_basics.md",
                RESOURCE_DIR + "/fwi_knowledge/cycle_skipping.md",
                RESOURCE_DIR + "/fwi_knowledge/adjoint_state.md",
                RESOURCE_DIR + "/fwi_knowledge/multiscale_fwi.md",
                RESOURCE_DIR + "/fwi_knowledge/awi.md"
            };

            json results = json::array();
            for (const auto& file_path : files) {
                std::string content = read_markdown_file(file_path);
                if (content.empty()) continue;

                // 简单关键词匹配
                std::string lower_content = content;
                std::string lower_query = query;
                for (auto& c : lower_content) c = std::tolower(c);
                for (auto& c : lower_query) c = std::tolower(c);

                if (lower_content.find(lower_query) != std::string::npos) {
                    // 提取标题
                    size_t pos = content.find("# ");
                    std::string title = (pos != std::string::npos) ?
                        content.substr(pos + 2, content.find("\n", pos) - pos - 2) : file_path;

                    json item;
                    item["title"] = title;
                    item["file"] = file_path;
                    item["excerpt"] = content.substr(0, 300) + "...";
                    results.push_back(item);
                }
            }

            if (results.empty()) {
                resultText = "未找到与 \"" + query + "\" 相关的知识";
            } else {
                resultText = "搜索结果 (" + std::to_string(results.size()) + " 条):\n" + results.dump(2);
            }
        }
        else {
            throw std::runtime_error("Unknown tool: " + toolName);
        }

        json content;
        content["type"] = "text";
        content["text"] = resultText;
        response["content"].push_back(content);

    } catch (const std::exception& e) {
        response["isError"] = true;
        json errorContent;
        errorContent["type"] = "text";
        errorContent["text"] = std::string("Error: ") + e.what();
        response["content"].push_back(errorContent);
    }

    std::string resultStr = response.dump();
    char* buffer = new char[resultStr.length() + 1];
    strcpy(buffer, resultStr.c_str());
    return buffer;
}

void ShutdownImpl() {}
int GetToolCountImpl() { return sizeof(methods) / sizeof(methods[0]); }
const PluginTool* GetToolImpl(int index) {
    if (index < 0 || index >= GetToolCountImpl()) return nullptr;
    return &methods[index];
}

static PluginAPI plugin = {
    GetNameImpl, GetVersionImpl, GetTypeImpl, InitializeImpl,
    HandleRequestImpl, ShutdownImpl, GetToolCountImpl, GetToolImpl,
    nullptr, nullptr, nullptr, nullptr
};

extern "C" PLUGIN_API PluginAPI* CreatePlugin() { return &plugin; }
extern "C" PLUGIN_API void DestroyPlugin(PluginAPI*) {}
