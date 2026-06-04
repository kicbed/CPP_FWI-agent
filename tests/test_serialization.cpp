#include <gtest/gtest.h>
#include "agent_rpc/common/serializer.h"
#include "agent_rpc/common/message_converter.h"
#include "agent_rpc/common/types.h"
#include <google/protobuf/util/message_differencer.h>

using namespace agent_rpc::common;

class SerializationTest : public ::testing::Test {
protected:
    void SetUp() override {
        MessageSerializer::getInstance().initialize(SerializerFactory::PROTOBUF_BINARY);
    }
};

TEST_F(SerializationTest, BinarySerialization) {
    // 创建测试消息
    auto message = MessageConverter::toProtobufMessage("Test message", "test_001", "test");
    (*message.mutable_headers())["test_header"] = "test_value";
    
    // 序列化
    std::string serialized = MessageSerializer::getInstance().serializeMessage(message);
    EXPECT_FALSE(serialized.empty());
    
    // 反序列化
    agent_communication::Message deserialized;
    EXPECT_TRUE(MessageSerializer::getInstance().deserializeMessage(serialized, deserialized));
    
    // 验证内容
    EXPECT_EQ(message.content(), deserialized.content());
    EXPECT_EQ(message.id(), deserialized.id());
    EXPECT_EQ(message.type(), deserialized.type());
    EXPECT_EQ(message.headers().at("test_header"), deserialized.headers().at("test_header"));
}

TEST_F(SerializationTest, JsonSerialization) {
    // 创建测试消息
    auto message = MessageConverter::toProtobufMessage("JSON test message", "json_001", "json");
    (*message.mutable_headers())["json_header"] = "json_value";
    
    // JSON序列化
    std::string json = MessageSerializer::getInstance().serializeToJson(message);
    EXPECT_FALSE(json.empty());
    EXPECT_TRUE(json.find("JSON test message") != std::string::npos);
    
    // JSON反序列化
    agent_communication::Message deserialized;
    EXPECT_TRUE(MessageSerializer::getInstance().deserializeFromJson(json, deserialized));
    
    // 验证内容
    EXPECT_EQ(message.content(), deserialized.content());
    EXPECT_EQ(message.id(), deserialized.id());
    EXPECT_EQ(message.type(), deserialized.type());
}

TEST_F(SerializationTest, MessageConversion) {
    // 创建ServiceEndpoint
    ServiceEndpoint endpoint;
    endpoint.host = "192.168.1.100";
    endpoint.port = 8080;
    endpoint.service_name = "test_service";
    endpoint.version = "1.0.0";
    endpoint.metadata["env"] = "test";
    
    // 转换为protobuf
    auto service_info = MessageConverter::toProtobuf(endpoint);
    EXPECT_EQ(endpoint.host, service_info.host());
    EXPECT_EQ(endpoint.port, service_info.port());
    EXPECT_EQ(endpoint.service_name, service_info.service_name());
    EXPECT_EQ(endpoint.version, service_info.version());
    
    // 转换回内部类型
    auto converted_endpoint = MessageConverter::fromProtobuf(service_info);
    EXPECT_EQ(endpoint.host, converted_endpoint.host);
    EXPECT_EQ(endpoint.port, converted_endpoint.port);
    EXPECT_EQ(endpoint.service_name, converted_endpoint.service_name);
    EXPECT_EQ(endpoint.version, converted_endpoint.version);
}

TEST_F(SerializationTest, RequestCreation) {
    // 测试发送消息请求创建
    auto send_request = MessageConverter::createSendMessageRequest("Test message", "target_agent", 30);
    EXPECT_EQ("Test message", send_request.message().content());
    EXPECT_EQ("target_agent", send_request.target_agent());
    EXPECT_EQ(30, send_request.timeout_seconds());
    
    // 测试接收消息请求创建
    auto receive_request = MessageConverter::createReceiveMessageRequest("agent_001", 10, 30);
    EXPECT_EQ("agent_001", receive_request.agent_id());
    EXPECT_EQ(10, receive_request.max_messages());
    EXPECT_EQ(30, receive_request.timeout_seconds());
    
    // 测试广播消息请求创建
    std::vector<std::string> targets = {"agent1", "agent2"};
    auto broadcast_request = MessageConverter::createBroadcastMessageRequest("Broadcast", targets, true);
    EXPECT_EQ("Broadcast", broadcast_request.message().content());
    EXPECT_EQ(2, broadcast_request.target_agents_size());
    EXPECT_TRUE(broadcast_request.exclude_sender());
}

TEST_F(SerializationTest, ResponseParsing) {
    // 创建成功响应
    auto success_response = MessageConverter::createSuccessStatus("Success");
    EXPECT_TRUE(MessageConverter::isStatusSuccess(success_response));
    EXPECT_EQ("Success", MessageConverter::getStatusMessage(success_response));
    
    // 创建错误响应
    auto error_response = MessageConverter::createErrorStatus(500, "Internal Error", "Details");
    EXPECT_FALSE(MessageConverter::isStatusSuccess(error_response));
    EXPECT_EQ("Internal Error", MessageConverter::getStatusMessage(error_response));
}

TEST_F(SerializationTest, MessageBuilder) {
    // 测试消息构建
    std::map<std::string, std::string> headers = {{"key1", "value1"}, {"key2", "value2"}};
    auto message = MessageBuilder::buildMessage("Test content", "msg_001", "test", headers, "payload");
    
    EXPECT_EQ("Test content", message.content());
    EXPECT_EQ("msg_001", message.id());
    EXPECT_EQ("test", message.type());
    EXPECT_EQ(2, message.headers_size());
    EXPECT_EQ("payload", message.payload());
    
    // 测试服务信息构建
    std::vector<std::string> tags = {"tag1", "tag2"};
    std::map<std::string, std::string> metadata = {{"meta1", "value1"}};
    auto service_info = MessageBuilder::buildServiceInfo("test_service", "1.0", "localhost", 8080, tags, metadata);
    
    EXPECT_EQ("test_service", service_info.service_name());
    EXPECT_EQ("1.0", service_info.version());
    EXPECT_EQ("localhost", service_info.host());
    EXPECT_EQ(8080, service_info.port());
    EXPECT_EQ(2, service_info.tags_size());
    EXPECT_EQ(1, service_info.metadata_size());
}

TEST_F(SerializationTest, MessageValidation) {
    // 测试消息验证
    auto valid_message = MessageBuilder::buildMessage("Valid content", "valid_001", "test");
    EXPECT_TRUE(MessageValidator::validateMessage(valid_message));
    
    // 测试无效消息
    agent_communication::Message invalid_message;
    EXPECT_FALSE(MessageValidator::validateMessage(invalid_message));
    
    // 测试服务信息验证
    auto valid_service = MessageBuilder::buildServiceInfo("test_service", "1.0", "localhost", 8080);
    EXPECT_TRUE(MessageValidator::validateServiceInfo(valid_service));
    
    // 测试无效服务信息
    agent_communication::common::ServiceInfo invalid_service;
    EXPECT_FALSE(MessageValidator::validateServiceInfo(invalid_service));
}

TEST_F(SerializationTest, SerializerFactory) {
    // 测试序列化器工厂
    auto binary_serializer = SerializerFactory::createSerializer(SerializerFactory::PROTOBUF_BINARY);
    EXPECT_NE(nullptr, binary_serializer);
    EXPECT_EQ("ProtobufBinary", binary_serializer->getName());
    
    auto json_serializer = SerializerFactory::createSerializer(SerializerFactory::PROTOBUF_JSON);
    EXPECT_NE(nullptr, json_serializer);
    EXPECT_EQ("ProtobufJson", json_serializer->getName());
    
    // 测试可用序列化器列表
    auto available = SerializerFactory::getAvailableSerializers();
    EXPECT_EQ(2, available.size());
    EXPECT_TRUE(std::find(available.begin(), available.end(), "ProtobufBinary") != available.end());
    EXPECT_TRUE(std::find(available.begin(), available.end(), "ProtobufJson") != available.end());
}

TEST_F(SerializationTest, MessageWrapper) {
    // 测试消息包装器
    MessageWrapper wrapper;
    
    // 包装消息
    auto message = MessageBuilder::buildMessage("Wrapped content", "wrap_001", "test");
    wrapper.wrap(message);
    
    // 解包消息
    agent_communication::Message unwrapped;
    EXPECT_TRUE(wrapper.unwrap(unwrapped));
    EXPECT_EQ(message.content(), unwrapped.content());
    EXPECT_EQ(message.id(), unwrapped.id());
    
    // 测试序列化
    std::string serialized = wrapper.serialize(*MessageSerializer::getInstance().getSerializer());
    EXPECT_FALSE(serialized.empty());
    
    // 测试反序列化
    MessageWrapper deserialized_wrapper;
    EXPECT_TRUE(deserialized_wrapper.deserialize(serialized, *MessageSerializer::getInstance().getSerializer()));
    
    agent_communication::Message final_message;
    EXPECT_TRUE(deserialized_wrapper.unwrap(final_message));
    EXPECT_EQ(message.content(), final_message.content());
}

TEST_F(SerializationTest, PerformanceTest) {
    // 性能测试
    auto message = MessageBuilder::buildMessage("Performance test message", "perf_001", "performance");
    
    const int iterations = 1000;
    auto start_time = std::chrono::high_resolution_clock::now();
    
    for (int i = 0; i < iterations; ++i) {
        std::string serialized = MessageSerializer::getInstance().serializeMessage(message);
        agent_communication::Message deserialized;
        MessageSerializer::getInstance().deserializeMessage(serialized, deserialized);
    }
    
    auto end_time = std::chrono::high_resolution_clock::now();
    auto duration = std::chrono::duration_cast<std::chrono::microseconds>(end_time - start_time);
    
    // 验证性能（应该小于1秒）
    EXPECT_LT(duration.count(), 1000000);
    
    std::cout << "序列化/反序列化 " << iterations << " 次耗时: " << duration.count() << " 微秒" << std::endl;
}

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
