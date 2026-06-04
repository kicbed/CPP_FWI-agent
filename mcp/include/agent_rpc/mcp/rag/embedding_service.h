/**
 * @file embedding_service.h
 * @brief 阿里百炼 DashScope 文本向量化服务
 * 
 * Requirements: 1.2, 2.1, 2.2, 2.3, 2.4
 * Task 2: 实现 EmbeddingService
 */

#pragma once

#include <string>
#include <vector>
#include <memory>
#include <functional>
#include <chrono>

namespace agent_rpc {
namespace mcp {
namespace rag {

/**
 * @brief Embedding 服务配置
 */
struct EmbeddingConfig {
    std::string api_key;                          ///< DashScope API Key
    std::string model = "text-embedding-v2";      ///< 模型名称
    int dimension = 1536;                         ///< 向量维度
    int max_retries = 3;                          ///< 最大重试次数
    int timeout_ms = 10000;                       ///< 超时时间 (毫秒)
    int initial_retry_delay_ms = 1000;            ///< 初始重试延迟 (毫秒)
    std::string api_url = "https://dashscope.aliyuncs.com/api/v1/services/embeddings/text-embedding/text-embedding";
    
    /**
     * @brief 从环境变量加载 API Key
     * @return 如果成功加载返回 true
     */
    bool loadApiKeyFromEnv();
    
    /**
     * @brief 验证配置
     * @return 如果配置有效返回 true
     */
    bool validate() const;
};

/**
 * @brief 重试统计信息
 */
struct RetryStats {
    int total_attempts = 0;
    int successful_attempts = 0;
    int failed_attempts = 0;
    std::vector<int> retry_delays_ms;  ///< 每次重试的延迟
};

/**
 * @brief Embedding 服务类
 * 
 * 调用阿里百炼 DashScope API 生成文本向量。
 * 支持：
 * - 单文本向量化
 * - 批量向量化
 * - 指数退避重试
 * - 超时处理
 */
class EmbeddingService {
public:
    explicit EmbeddingService(const EmbeddingConfig& config);
    ~EmbeddingService();
    
    // 禁止拷贝
    EmbeddingService(const EmbeddingService&) = delete;
    EmbeddingService& operator=(const EmbeddingService&) = delete;
    
    /**
     * @brief 生成单个文本的向量
     * @param text 输入文本
     * @return 向量 (维度由配置决定)
     * @throws std::runtime_error 如果 API 调用失败
     */
    std::vector<float> embed(const std::string& text);
    
    /**
     * @brief 批量生成向量
     * @param texts 输入文本列表
     * @return 向量列表
     * @throws std::runtime_error 如果 API 调用失败
     */
    std::vector<std::vector<float>> embedBatch(const std::vector<std::string>& texts);
    
    /**
     * @brief 计算两个向量的余弦相似度
     * @param a 向量 a
     * @param b 向量 b
     * @return 相似度 [-1, 1]
     */
    static float cosineSimilarity(const std::vector<float>& a, const std::vector<float>& b);
    
    /**
     * @brief 获取配置
     */
    const EmbeddingConfig& getConfig() const { return config_; }
    
    /**
     * @brief 获取最近一次调用的重试统计
     */
    const RetryStats& getLastRetryStats() const { return last_retry_stats_; }
    
    /**
     * @brief 设置重试回调（用于测试）
     */
    using RetryCallback = std::function<void(int attempt, int delay_ms)>;
    void setRetryCallback(RetryCallback callback) { retry_callback_ = callback; }

private:
    /**
     * @brief 发送 HTTP POST 请求
     */
    std::string sendPostRequest(const std::string& data);
    
    /**
     * @brief 带重试的 API 调用
     */
    std::string callApiWithRetry(const std::string& request_body);
    
    /**
     * @brief 计算指数退避延迟
     */
    int calculateBackoffDelay(int attempt) const;
    
    /**
     * @brief 解析 API 响应
     */
    std::vector<std::vector<float>> parseEmbeddingResponse(const std::string& response);
    
    EmbeddingConfig config_;
    RetryStats last_retry_stats_;
    RetryCallback retry_callback_;
};

} // namespace rag
} // namespace mcp
} // namespace agent_rpc
