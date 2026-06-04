#include "agent_rpc/common/serializer.h"
#include "agent_rpc/common/message_converter.h"
#include "agent_rpc/common/rpc_framework.h"
#include "agent_rpc/server/rpc_server.h"
#include "agent_rpc/client/rpc_client.h"
#include <iostream>
#include <chrono>
#include <thread>

using namespace agent_rpc::common;
using namespace agent_rpc::server;
using namespace agent_rpc::client;

void demonstrateSerialization() {
    std::cout << "=== 序列化演示 ===" << std::endl;
    
    // 初始化序列化器
    MessageSerializer::getInstance().initialize(SerializerFactory::PROTOBUF_BINARY);
    
    // 创建测试消息
    auto message = MessageConverter::toProtobufMessage(
        "Hello, Protobuf Serialization!", 
        "msg_001", 
        "test"
    );
    
    // 添加头部信息
    (*message.mutable_headers())["sender"] = "example_client";
    (*message.mutable_headers())["priority"] = "high";
    
    // 二进制序列化
    std::string binary_data = MessageSerializer::getInstance().serializeMessage(message);
    std::cout << "二进制序列化大小: " << binary_data.size() << " 字节" << std::endl;
    
    // JSON序列化
    std::string json_data = MessageSerializer::getInstance().serializeToJson(message);
    std::cout << "JSON序列化大小: " << json_data.size() << " 字节" << std::endl;
    std::cout << "JSON内容: " << json_data << std::endl;
    
    // 反序列化测试
    agent_communication::Message deserialized_message;
    if (MessageSerializer::getInstance().deserializeMessage(binary_data, deserialized_message)) {
        std::cout << "反序列化成功: " << deserialized_message.content() << std::endl;
    }
    
    // 从JSON反序列化
    agent_communication::Message json_deserialized_message;
    if (MessageSerializer::getInstance().deserializeFromJson(json_data, json_deserialized_message)) {
        std::cout << "JSON反序列化成功: " << json_deserialized_message.content() << std::endl;
    }
    
    std::cout << std::endl;
}

void demonstrateMessageConversion() {
    std::cout << "=== 消息转换演示 ===" << std::endl;
    
    // 创建ServiceEndpoint
    ServiceEndpoint endpoint;
    endpoint.host = "192.168.1.100";
    endpoint.port = 8080;
    endpoint.service_name = "test_service";
    endpoint.version = "1.0.0";
    endpoint.metadata["environment"] = "production";
    endpoint.metadata["region"] = "us-west-2";
    
    // 转换为protobuf
    auto service_info = MessageConverter::toProtobuf(endpoint);
    std::cout << "ServiceInfo: " << service_info.service_name() 
              << " @ " << service_info.host() << ":" << service_info.port() << std::endl;
    
    // 转换回内部类型
    auto converted_endpoint = MessageConverter::fromProtobuf(service_info);
    std::cout << "转换回ServiceEndpoint: " << converted_endpoint.service_name 
              << " @ " << converted_endpoint.host << ":" << converted_endpoint.port << std::endl;
    
    // 创建各种请求
    auto send_request = MessageConverter::createSendMessageRequest("Test message", "target_agent", 30);
    auto receive_request = MessageConverter::createReceiveMessageRequest("agent_001", 10, 30);
    auto broadcast_request = MessageConverter::createBroadcastMessageRequest("Broadcast message", {"agent1", "agent2"});
    
    std::cout << "创建了发送消息请求: " << send_request.target_agent() << std::endl;
    std::cout << "创建了接收消息请求: " << receive_request.agent_id() << std::endl;
    std::cout << "创建了广播消息请求: " << broadcast_request.target_agents_size() << " 个目标" << std::endl;
    
    std::cout << std::endl;
}

void demonstrateRpcCommunication() {
    std::cout << "=== RPC通信演示 ===" << std::endl;
    
    // 配置
    RpcConfig config;
    config.server_address = "127.0.0.1:50051";
    config.log_level = "INFO";
    
    // 初始化框架
    auto& framework = RpcFramework::getInstance();
    if (!framework.initialize(config)) {
        std::cerr << "Failed to initialize RPC framework" << std::endl;
        return;
    }
    
    // 创建服务器
    RpcServer server;
    if (!server.initialize(config)) {
        std::cerr << "Failed to initialize RPC server" << std::endl;
        return;
    }
    
    // 设置消息处理器
    auto service = server.getService();
    service->setMessageHandler([](const std::string& message) {
        std::cout << "服务器收到消息: " << message << std::endl;
    });
    
    // 启动服务器
    if (!server.start()) {
        std::cerr << "Failed to start RPC server" << std::endl;
        return;
    }
    
    std::cout << "服务器已启动，等待客户端连接..." << std::endl;
    
    // 等待一下让服务器完全启动
    std::this_thread::sleep_for(std::chrono::seconds(1));
    
    // 创建客户端
    RpcClient client;
    if (!client.initialize(config)) {
        std::cerr << "Failed to initialize RPC client" << std::endl;
        return;
    }
    
    // 连接到服务器
    if (!client.connect("127.0.0.1:50051")) {
        std::cerr << "Failed to connect to RPC server" << std::endl;
        return;
    }
    
    std::cout << "客户端已连接到服务器" << std::endl;
    
    // 注册代理
    ServiceEndpoint agent_info;
    agent_info.host = "127.0.0.1";
    agent_info.port = 8080;
    agent_info.service_name = "serialization_demo";
    agent_info.version = "1.0.0";
    
    std::string agent_id = client.registerAgent(agent_info);
    if (!agent_id.empty()) {
        std::cout << "代理注册成功: " << agent_id << std::endl;
        
        // 发送消息
        if (client.sendMessage("Hello from serialization demo!", agent_id)) {
            std::cout << "消息发送成功" << std::endl;
        }
        
        // 接收消息
        auto messages = client.receiveMessages(agent_id, 5, 10);
        std::cout << "接收到 " << messages.size() << " 条消息" << std::endl;
        
        // 广播消息
        int success_count = client.broadcastMessage("Broadcast from serialization demo!");
        std::cout << "广播消息到 " << success_count << " 个代理" << std::endl;
        
        // 注销代理
        client.unregisterAgent(agent_id, "Demo completed");
    }
    
    // 断开连接
    client.disconnect();
    server.stop();
    
    std::cout << "RPC通信演示完成" << std::endl;
    std::cout << std::endl;
}

void demonstratePerformance() {
    std::cout << "=== 性能测试 ===" << std::endl;
    
    // 初始化序列化器
    MessageSerializer::getInstance().initialize(SerializerFactory::PROTOBUF_BINARY);
    
    // 创建测试消息
    auto message = MessageConverter::toProtobufMessage(
        "Performance test message with some content to measure serialization overhead",
        "perf_test_001",
        "performance"
    );
    
    // 添加更多头部信息
    for (int i = 0; i < 10; ++i) {
        (*message.mutable_headers())["header_" + std::to_string(i)] = "value_" + std::to_string(i);
    }
    
    const int iterations = 10000;
    
    // 测试二进制序列化性能
    auto start_time = std::chrono::high_resolution_clock::now();
    
    for (int i = 0; i < iterations; ++i) {
        std::string data = MessageSerializer::getInstance().serializeMessage(message);
        agent_communication::Message deserialized;
        MessageSerializer::getInstance().deserializeMessage(data, deserialized);
    }
    
    auto end_time = std::chrono::high_resolution_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time);
    
    std::cout << "二进制序列化/反序列化 " << iterations << " 次耗时: " 
              << duration.count() << " 微秒" << std::endl;
    std::cout << "平均每次: " << (double)duration.count() / iterations << " 微秒" << std::endl;
    
    // 测试JSON序列化性能
    start_time = std::chrono::high_resolution_clock::now();
    
    for (int i = 0; i < iterations; ++i) {
        std::string json = MessageSerializer::getInstance().serializeToJson(message);
        agent_communication::Message deserialized;
        MessageSerializer::getInstance().deserializeFromJson(json, deserialized);
    }
    
    end_time = std::chrono::high_resolution_clock::now();
    duration = std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time);
    
    std::cout << "JSON序列化/反序列化 " << iterations << " 次耗时: " 
              << duration.count() << " 微秒" << std::endl;
    std::cout << "平均每次: " << (double)duration.count() / iterations << " 微秒" << std::endl;
    
    std::cout << std::endl;
}

int main() {
    std::cout << "Agent Communication RPC Framework - Protobuf序列化演示" << std::endl;
    std::cout << "=====================================================" << std::endl;
    std::cout << std::endl;
    
    try {
        // 序列化演示
        demonstrateSerialization();
        
        // 消息转换演示
        demonstrateMessageConversion();
        
        // RPC通信演示
        demonstrateRpcCommunication();
        
        // 性能测试
        demonstratePerformance();
        
        std::cout << "所有演示完成！" << std::endl;
        
    } catch (const std::exception& e) {
        std::cerr << "演示过程中发生错误: " << e.what() << std::endl;
        return 1;
    }
    
    return 0;
}
