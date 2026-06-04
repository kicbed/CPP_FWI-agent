#pragma once

#include "types.h"
#include <google/protobuf/message.h>
#include <google/protobuf/any.pb.h>
#include <string>
#include <memory>
#include <vector>
#include <map>

namespace agent_rpc {
namespace common {

// 序列化器接口
class Serializer {
public:
    virtual ~Serializer() = default;
    
    // 序列化protobuf消息为字节数组
    virtual std::string serialize(const google::protobuf::Message& message) = 0;
    
    // 反序列化字节数组为protobuf消息
    virtual bool deserialize(const std::string& data, google::protobuf::Message& message) = 0;
    
    // 序列化protobuf消息为JSON字符串
    virtual std::string serializeToJson(const google::protobuf::Message& message) = 0;
    
    // 从JSON字符串反序列化为protobuf消息
    virtual bool deserializeFromJson(const std::string& json, google::protobuf::Message& message) = 0;
    
    // 获取序列化器名称
    virtual std::string getName() const = 0;
};

// Protobuf二进制序列化器
class ProtobufBinarySerializer : public Serializer {
public:
    ProtobufBinarySerializer() = default;
    ~ProtobufBinarySerializer() = default;
    
    std::string serialize(const google::protobuf::Message& message) override;
    bool deserialize(const std::string& data, google::protobuf::Message& message) override;
    std::string serializeToJson(const google::protobuf::Message& message) override;
    bool deserializeFromJson(const std::string& json, google::protobuf::Message& message) override;
    std::string getName() const override { return "ProtobufBinary"; }
};

// Protobuf JSON序列化器
class ProtobufJsonSerializer : public Serializer {
public:
    ProtobufJsonSerializer() = default;
    ~ProtobufJsonSerializer() = default;
    
    std::string serialize(const google::protobuf::Message& message) override;
    bool deserialize(const std::string& data, google::protobuf::Message& message) override;
    std::string serializeToJson(const google::protobuf::Message& message) override;
    bool deserializeFromJson(const std::string& json, google::protobuf::Message& message) override;
    std::string getName() const override { return "ProtobufJson"; }
};

// 序列化器工厂
class SerializerFactory {
public:
    enum SerializerType {
        PROTOBUF_BINARY,
        PROTOBUF_JSON
    };
    
    static std::unique_ptr<Serializer> createSerializer(SerializerType type);
    static std::vector<std::string> getAvailableSerializers();
};

// 消息包装器 - 用于Any类型消息
class MessageWrapper {
public:
    MessageWrapper() = default;
    ~MessageWrapper() = default;
    
    // 包装protobuf消息
    template<typename T>
    void wrap(const T& message) {
        any_.PackFrom(message);
    }
    
    // 解包protobuf消息
    template<typename T>
    bool unwrap(T& message) {
        return any_.UnpackTo(&message);
    }
    
    // 获取消息类型URL
    std::string getTypeUrl() const { return any_.type_url(); }
    
    // 设置消息类型URL
    void setTypeUrl(const std::string& type_url) { any_.set_type_url(type_url); }
    
    // 序列化
    std::string serialize(Serializer& serializer) const;
    
    // 反序列化
    bool deserialize(const std::string& data, Serializer& serializer);
    
    // 获取Any对象
    const google::protobuf::Any& getAny() const { return any_; }
    google::protobuf::Any& getAny() { return any_; }

private:
    google::protobuf::Any any_;
};

// 消息序列化工具类
class MessageSerializer {
public:
    static MessageSerializer& getInstance();
    
    // 初始化序列化器
    void initialize(SerializerFactory::SerializerType type = SerializerFactory::PROTOBUF_BINARY);
    
    // 序列化消息
    std::string serializeMessage(const google::protobuf::Message& message);
    
    // 反序列化消息
    bool deserializeMessage(const std::string& data, google::protobuf::Message& message);
    
    // 序列化为JSON
    std::string serializeToJson(const google::protobuf::Message& message);
    
    // 从JSON反序列化
    bool deserializeFromJson(const std::string& json, google::protobuf::Message& message);
    
    // 包装消息为Any类型
    template<typename T>
    MessageWrapper wrapMessage(const T& message) {
        MessageWrapper wrapper;
        wrapper.wrap(message);
        return wrapper;
    }
    
    // 解包Any类型消息
    template<typename T>
    bool unwrapMessage(const MessageWrapper& wrapper, T& message) {
        return wrapper.unwrap(message);
    }
    
    // 获取当前序列化器
    Serializer* getSerializer() { return serializer_.get(); }

private:
    MessageSerializer() = default;
    ~MessageSerializer() = default;
    MessageSerializer(const MessageSerializer&) = delete;
    MessageSerializer& operator=(const MessageSerializer&) = delete;
    
    std::unique_ptr<Serializer> serializer_;
};

// 便利宏
#define SERIALIZE_MESSAGE(msg) MessageSerializer::getInstance().serializeMessage(msg)
#define DESERIALIZE_MESSAGE(data, msg) MessageSerializer::getInstance().deserializeMessage(data, msg)
#define SERIALIZE_TO_JSON(msg) MessageSerializer::getInstance().serializeToJson(msg)
#define DESERIALIZE_FROM_JSON(json, msg) MessageSerializer::getInstance().deserializeFromJson(json, msg)

} // namespace common
} // namespace agent_rpc
