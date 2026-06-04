/**
 * @file Calculator.cpp
 * @brief MCP Calculator Plugin - 数学计算工具
 */

#include <cmath>
#include <string>
#include <sstream>
#include <stack>
#include <cctype>
#include <stdexcept>
#include "PluginAPI.h"
#include "json.hpp"

using json = nlohmann::json;

// 表达式解析器
class ExpressionParser {
public:
    static double evaluate(const std::string& expr) {
        std::string cleaned = removeSpaces(expr);
        size_t pos = 0;
        double result = parseExpression(cleaned, pos);
        if (pos != cleaned.length()) {
            throw std::runtime_error("Invalid expression");
        }
        return result;
    }

private:
    static std::string removeSpaces(const std::string& s) {
        std::string result;
        for (char c : s) {
            if (!std::isspace(c)) result += c;
        }
        return result;
    }

    static double parseExpression(const std::string& expr, size_t& pos) {
        double result = parseTerm(expr, pos);
        while (pos < expr.length() && (expr[pos] == '+' || expr[pos] == '-')) {
            char op = expr[pos++];
            double term = parseTerm(expr, pos);
            if (op == '+') result += term;
            else result -= term;
        }
        return result;
    }

    static double parseTerm(const std::string& expr, size_t& pos) {
        double result = parsePower(expr, pos);
        while (pos < expr.length() && (expr[pos] == '*' || expr[pos] == '/' || expr[pos] == '%')) {
            char op = expr[pos++];
            double factor = parsePower(expr, pos);
            if (op == '*') result *= factor;
            else if (op == '/') {
                if (factor == 0) throw std::runtime_error("Division by zero");
                result /= factor;
            } else {
                if (factor == 0) throw std::runtime_error("Modulo by zero");
                result = std::fmod(result, factor);
            }
        }
        return result;
    }

    static double parsePower(const std::string& expr, size_t& pos) {
        double base = parseFactor(expr, pos);
        if (pos < expr.length() && expr[pos] == '^') {
            pos++;
            double exponent = parsePower(expr, pos);
            return std::pow(base, exponent);
        }
        return base;
    }

    static double parseFactor(const std::string& expr, size_t& pos) {
        if (pos < expr.length() && expr[pos] == '-') {
            pos++;
            return -parseFactor(expr, pos);
        }
        if (pos < expr.length() && expr[pos] == '+') {
            pos++;
            return parseFactor(expr, pos);
        }
        if (pos < expr.length() && expr[pos] == '(') {
            pos++;
            double result = parseExpression(expr, pos);
            if (pos >= expr.length() || expr[pos] != ')') {
                throw std::runtime_error("Missing closing parenthesis");
            }
            pos++;
            return result;
        }
        if (pos < expr.length() && std::isalpha(expr[pos])) {
            return parseFunction(expr, pos);
        }
        return parseNumber(expr, pos);
    }

    static double parseFunction(const std::string& expr, size_t& pos) {
        std::string funcName;
        while (pos < expr.length() && std::isalpha(expr[pos])) {
            funcName += expr[pos++];
        }
        if (pos >= expr.length() || expr[pos] != '(') {
            throw std::runtime_error("Expected '(' after function");
        }
        pos++;
        double arg = parseExpression(expr, pos);
        if (pos >= expr.length() || expr[pos] != ')') {
            throw std::runtime_error("Missing ')' for function");
        }
        pos++;

        if (funcName == "sin") return std::sin(arg);
        if (funcName == "cos") return std::cos(arg);
        if (funcName == "tan") return std::tan(arg);
        if (funcName == "sqrt") return std::sqrt(arg);
        if (funcName == "abs") return std::abs(arg);
        if (funcName == "log") return std::log(arg);
        if (funcName == "log10") return std::log10(arg);
        if (funcName == "exp") return std::exp(arg);
        if (funcName == "floor") return std::floor(arg);
        if (funcName == "ceil") return std::ceil(arg);
        if (funcName == "round") return std::round(arg);

        throw std::runtime_error("Unknown function: " + funcName);
    }

    static double parseNumber(const std::string& expr, size_t& pos) {
        size_t start = pos;
        while (pos < expr.length() && (std::isdigit(expr[pos]) || expr[pos] == '.')) {
            pos++;
        }
        if (start == pos) {
            throw std::runtime_error("Expected number");
        }
        return std::stod(expr.substr(start, pos - start));
    }
};

static long long factorial(int n) {
    if (n < 0) throw std::runtime_error("Factorial of negative number");
    if (n > 20) throw std::runtime_error("Factorial overflow: n must be <= 20");
    long long result = 1;
    for (int i = 2; i <= n; i++) result *= i;
    return result;
}

// 工具定义
static PluginTool methods[] = {
    {"calculator", "Evaluates a mathematical expression", 
     "{\"type\":\"object\",\"properties\":{\"expression\":{\"type\":\"string\",\"description\":\"Math expression\"}},\"required\":[\"expression\"]}"},
    {"add", "Adds two numbers", 
     "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"number\"},\"b\":{\"type\":\"number\"}},\"required\":[\"a\",\"b\"]}"},
    {"subtract", "Subtracts b from a", 
     "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"number\"},\"b\":{\"type\":\"number\"}},\"required\":[\"a\",\"b\"]}"},
    {"multiply", "Multiplies two numbers", 
     "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"number\"},\"b\":{\"type\":\"number\"}},\"required\":[\"a\",\"b\"]}"},
    {"divide", "Divides a by b", 
     "{\"type\":\"object\",\"properties\":{\"a\":{\"type\":\"number\"},\"b\":{\"type\":\"number\"}},\"required\":[\"a\",\"b\"]}"},
    {"power", "Raises base to exponent", 
     "{\"type\":\"object\",\"properties\":{\"base\":{\"type\":\"number\"},\"exponent\":{\"type\":\"number\"}},\"required\":[\"base\",\"exponent\"]}"},
    {"sqrt", "Square root of a number", 
     "{\"type\":\"object\",\"properties\":{\"number\":{\"type\":\"number\",\"minimum\":0}},\"required\":[\"number\"]}"},
    {"factorial", "Factorial of n (0-20)", 
     "{\"type\":\"object\",\"properties\":{\"n\":{\"type\":\"integer\",\"minimum\":0,\"maximum\":20}},\"required\":[\"n\"]}"}
};

const char* GetNameImpl() { return "calculator-tools"; }
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
        double result = 0;

        if (toolName == "calculator") {
            std::string expr = args["expression"].get<std::string>();
            result = ExpressionParser::evaluate(expr);
            std::ostringstream oss;
            oss.precision(15);
            oss << result;
            resultText = expr + " = " + oss.str();
        }
        else if (toolName == "add") {
            double a = args["a"].get<double>();
            double b = args["b"].get<double>();
            result = a + b;
            resultText = std::to_string(a) + " + " + std::to_string(b) + " = " + std::to_string(result);
        }
        else if (toolName == "subtract") {
            double a = args["a"].get<double>();
            double b = args["b"].get<double>();
            result = a - b;
            resultText = std::to_string(a) + " - " + std::to_string(b) + " = " + std::to_string(result);
        }
        else if (toolName == "multiply") {
            double a = args["a"].get<double>();
            double b = args["b"].get<double>();
            result = a * b;
            resultText = std::to_string(a) + " * " + std::to_string(b) + " = " + std::to_string(result);
        }
        else if (toolName == "divide") {
            double a = args["a"].get<double>();
            double b = args["b"].get<double>();
            if (b == 0) throw std::runtime_error("Division by zero");
            result = a / b;
            resultText = std::to_string(a) + " / " + std::to_string(b) + " = " + std::to_string(result);
        }
        else if (toolName == "power") {
            double base = args["base"].get<double>();
            double exponent = args["exponent"].get<double>();
            result = std::pow(base, exponent);
            resultText = std::to_string(base) + " ^ " + std::to_string(exponent) + " = " + std::to_string(result);
        }
        else if (toolName == "sqrt") {
            double num = args["number"].get<double>();
            if (num < 0) throw std::runtime_error("Cannot sqrt negative number");
            result = std::sqrt(num);
            resultText = "sqrt(" + std::to_string(num) + ") = " + std::to_string(result);
        }
        else if (toolName == "factorial") {
            int n = args["n"].get<int>();
            long long factResult = factorial(n);
            resultText = std::to_string(n) + "! = " + std::to_string(factResult);
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
