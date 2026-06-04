/**
 * @file http_bridge.h
 * @brief HTTP 桥接服务 - 为 Web 前端提供 HTTP API
 *
 * 在 gRPC Server 同进程中运行，监听 HTTP 端口，
 * 接收浏览器请求并转发到 Orchestrator。
 */

#pragma once

#include <memory>
#include <string>

namespace agent_rpc::server {

class HttpBridge {
public:
    HttpBridge();
    ~HttpBridge();

    /**
     * 启动 HTTP 桥接服务
     * @param port HTTP 监听端口
     * @param orchestrator_url Orchestrator 地址
     * @return 是否启动成功
     */
    bool start(int port, const std::string& orchestrator_url);

    /** 停止服务 */
    void stop();

    /** 是否运行中 */
    bool isRunning() const;

private:
    class Impl;
    std::unique_ptr<Impl> impl_;
};

} // namespace agent_rpc::server
