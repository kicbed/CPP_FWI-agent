#include "agent_rpc/common/serializer.h"
#include "agent_rpc/common/logger.h"
#include <google/protobuf/util/json_util.h>
#include <google/protobuf/util/message_differencer.h>
#include <sstream>

namespace agent_rpc {
namespace common {

// ProtobufBinarySerializer 实现
std::string ProtobufBinarySerializer::serialize(const google::protobuf::Message& message) {
    std::string data;
    if (!message.SerializeToString(&data)) {
        LOG_ERROR("Failed to serialize protobuf message to binary");
        return "";
    }
    return data;
}

bool ProtobufBinarySerializer::deserialize(const std::string& data, google::protobuf::Message& message) {
    if (!message.ParseFromString(data)) {
        LOG_ERROR("Failed to deserialize binary data to protobuf message");
        return false;
    }
    return true;
}

std::string ProtobufBinarySerializer::serializeToJson(const google::protobuf::Message& message) {
    std::string json;
    google::protobuf::util::JsonOptions options;
    options.add_whitespace = true;
    options.preserve_proto_field_names = true;
    
    auto status = google::protobuf::util::MessageToJsonString(message, &json, options);
    if (!status.ok()) {
        LOG_ERROR("Failed to serialize protobuf message to JSON: " + status.message().as_string());
        return "";
    }
    return json;
}

bool ProtobufBinarySerializer::deserializeFromJson(const std::string& json, google::protobuf::Message& message) {
    google::protobuf::util::JsonParseOptions options;
    options.ignore_unknown_fields = true;
    options.case_insensitive_enum_parsing = true;
    
    auto status = google::protobuf::util::JsonStringToMessage(json, &message, options);
    if (!status.ok()) {
        LOG_ERROR("Failed to deserialize JSON to protobuf message: " + status.message().as_string());
        return false;
    }
    return true;
}

// ProtobufJsonSerializer 实现
std::string ProtobufJsonSerializer::serialize(const google::protobuf::Message& message) {
    // JSON序列化器直接输出JSON
    return serializeToJson(message);
}

bool ProtobufJsonSerializer::deserialize(const std::string& data, google::protobuf::Message& message) {
    // JSON序列化器直接从JSON反序列化
    return deserializeFromJson(data, message);
}

std::string ProtobufJsonSerializer::serializeToJson(const google::protobuf::Message& message) {
    std::string json;
    google::protobuf::util::JsonOptions options;
    options.add_whitespace = false;
    options.preserve_proto_field_names = true;
    
    auto status = google::protobuf::util::MessageToJsonString(message, &json, options);
    if (!status.ok()) {
        LOG_ERROR("Failed to serialize protobuf message to JSON: " + status.message().as_string());
        return "";
    }
    return json;
}

bool ProtobufJsonSerializer::deserializeFromJson(const std::string& json, google::protobuf::Message& message) {
    google::protobuf::util::JsonParseOptions options;
    options.ignore_unknown_fields = true;
    options.case_insensitive_enum_parsing = true;
    
    auto status = google::protobuf::util::JsonStringToMessage(json, &message, options);
    if (!status.ok()) {
        LOG_ERROR("Failed to deserialize JSON to protobuf message: " + status.message().as_string());
        return false;
    }
    return true;
}

// SerializerFactory 实现
std::unique_ptr<Serializer> SerializerFactory::createSerializer(SerializerType type) {
    switch (type) {
        case PROTOBUF_BINARY:
            return std::make_unique<ProtobufBinarySerializer>();
        case PROTOBUF_JSON:
            return std::make_unique<ProtobufJsonSerializer>();
        default:
            LOG_WARN("Unknown serializer type, using binary serializer");
            return std::make_unique<ProtobufBinarySerializer>();
    }
}

std::vector<std::string> SerializerFactory::getAvailableSerializers() {
    return {
        "ProtobufBinary",
        "ProtobufJson"
    };
}

// MessageWrapper 实现
std::string MessageWrapper::serialize(Serializer& serializer) const {
    return serializer.serialize(any_);
}

bool MessageWrapper::deserialize(const std::string& data, Serializer& serializer) {
    return serializer.deserialize(data, any_);
}

// MessageSerializer 实现
MessageSerializer& MessageSerializer::getInstance() {
    static MessageSerializer instance;
    return instance;
}

void MessageSerializer::initialize(SerializerFactory::SerializerType type) {
    serializer_ = SerializerFactory::createSerializer(type);
    LOG_INFO("MessageSerializer initialized with " + serializer_->getName());
}

std::string MessageSerializer::serializeMessage(const google::protobuf::Message& message) {
    if (!serializer_) {
        LOG_ERROR("MessageSerializer not initialized");
        return "";
    }
    
    return serializer_->serialize(message);
}

bool MessageSerializer::deserializeMessage(const std::string& data, google::protobuf::Message& message) {
    if (!serializer_) {
        LOG_ERROR("MessageSerializer not initialized");
        return false;
    }
    
    return serializer_->deserialize(data, message);
}

std::string MessageSerializer::serializeToJson(const google::protobuf::Message& message) {
    if (!serializer_) {
        LOG_ERROR("MessageSerializer not initialized");
        return "";
    }
    
    return serializer_->serializeToJson(message);
}

bool MessageSerializer::deserializeFromJson(const std::string& json, google::protobuf::Message& message) {
    if (!serializer_) {
        LOG_ERROR("MessageSerializer not initialized");
        return false;
    }
    
    return serializer_->deserializeFromJson(json, message);
}

} // namespace common
} // namespace agent_rpc
