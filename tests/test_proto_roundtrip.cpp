/**
 * @file test_proto_roundtrip.cpp
 * @brief Property-based tests for Protobuf serialization round-trip
 * 
 * **Feature: a2a-integration, Property 11: Protobuf Serialization Round-Trip**
 * **Validates: Requirements 11.5**
 * 
 * For any AIQueryRequest or AIQueryResponse, serializing to protobuf binary
 * format and deserializing SHALL produce an equivalent message.
 */

#include <gtest/gtest.h>
#include <rapidcheck.h>
#include <rapidcheck/gtest.h>

#include "ai_query.pb.h"
#include "common.pb.h"

namespace {

// Custom generator for valid UTF-8 strings (ASCII subset)
rc::Gen<std::string> genValidString() {
    return rc::gen::container<std::string>(
        rc::gen::inRange<char>('a', 'z')
    );
}

rc::Gen<std::string> genNonEmptyValidString() {
    return rc::gen::nonEmpty(genValidString());
}

/**
 * @brief Test fixture for Protobuf round-trip tests
 */
class ProtoRoundTripTest : public ::testing::Test {
protected:
    void SetUp() override {}
    void TearDown() override {}
};

// ============================================================================
// Property Tests - AIQueryRequest Round-Trip
// ============================================================================

/**
 * **Feature: a2a-integration, Property 11: Protobuf Serialization Round-Trip**
 * **Validates: Requirements 11.5**
 */
RC_GTEST_PROP(ProtoRoundTrip, AIQueryRequest_PreservesRequestId, ()) {
    auto request_id = *genNonEmptyValidString();
    
    agent_communication::AIQueryRequest original;
    original.set_request_id(request_id);
    
    // Serialize to binary
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    // Deserialize
    agent_communication::AIQueryRequest deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    // Verify round-trip
    RC_ASSERT(deserialized.request_id() == original.request_id());
}

RC_GTEST_PROP(ProtoRoundTrip, AIQueryRequest_PreservesQuestion, ()) {
    auto question = *genValidString();
    
    agent_communication::AIQueryRequest original;
    original.set_question(question);
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::AIQueryRequest deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.question() == original.question());
}

RC_GTEST_PROP(ProtoRoundTrip, AIQueryRequest_PreservesContextId, ()) {
    auto context_id = *genValidString();
    
    agent_communication::AIQueryRequest original;
    original.set_context_id(context_id);
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::AIQueryRequest deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.context_id() == original.context_id());
}

RC_GTEST_PROP(ProtoRoundTrip, AIQueryRequest_PreservesHistoryLength, ()) {
    auto history_length = *rc::gen::inRange(0, 1000);
    
    agent_communication::AIQueryRequest original;
    original.set_history_length(history_length);
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::AIQueryRequest deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.history_length() == original.history_length());
}

RC_GTEST_PROP(ProtoRoundTrip, AIQueryRequest_PreservesTimeout, ()) {
    auto timeout = *rc::gen::inRange(1, 3600);
    
    agent_communication::AIQueryRequest original;
    original.set_timeout_seconds(timeout);
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::AIQueryRequest deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.timeout_seconds() == original.timeout_seconds());
}

RC_GTEST_PROP(ProtoRoundTrip, AIQueryRequest_PreservesMetadata, ()) {
    auto key = *genNonEmptyValidString();
    auto value = *genValidString();
    
    agent_communication::AIQueryRequest original;
    (*original.mutable_metadata())[key] = value;
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::AIQueryRequest deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.metadata().count(key) == 1);
    RC_ASSERT(deserialized.metadata().at(key) == value);
}

RC_GTEST_PROP(ProtoRoundTrip, AIQueryRequest_FullMessage, ()) {
    auto request_id = *genNonEmptyValidString();
    auto question = *genValidString();
    auto context_id = *genValidString();
    auto history_length = *rc::gen::inRange(0, 100);
    auto timeout = *rc::gen::inRange(1, 300);
    
    agent_communication::AIQueryRequest original;
    original.set_request_id(request_id);
    original.set_question(question);
    original.set_context_id(context_id);
    original.set_history_length(history_length);
    original.set_timeout_seconds(timeout);
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::AIQueryRequest deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.request_id() == original.request_id());
    RC_ASSERT(deserialized.question() == original.question());
    RC_ASSERT(deserialized.context_id() == original.context_id());
    RC_ASSERT(deserialized.history_length() == original.history_length());
    RC_ASSERT(deserialized.timeout_seconds() == original.timeout_seconds());
}

// ============================================================================
// Property Tests - AIQueryResponse Round-Trip
// ============================================================================

RC_GTEST_PROP(ProtoRoundTrip, AIQueryResponse_PreservesAnswer, ()) {
    auto answer = *genValidString();
    
    agent_communication::AIQueryResponse original;
    original.set_answer(answer);
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::AIQueryResponse deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.answer() == original.answer());
}

RC_GTEST_PROP(ProtoRoundTrip, AIQueryResponse_PreservesAgentInfo, ()) {
    auto agent_id = *genNonEmptyValidString();
    auto agent_name = *genValidString();
    auto task_id = *genNonEmptyValidString();
    
    agent_communication::AIQueryResponse original;
    original.set_agent_id(agent_id);
    original.set_agent_name(agent_name);
    original.set_task_id(task_id);
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::AIQueryResponse deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.agent_id() == original.agent_id());
    RC_ASSERT(deserialized.agent_name() == original.agent_name());
    RC_ASSERT(deserialized.task_id() == original.task_id());
}

RC_GTEST_PROP(ProtoRoundTrip, AIQueryResponse_PreservesProcessingTime, ()) {
    auto processing_time = *rc::gen::inRange(0, 100000);
    
    agent_communication::AIQueryResponse original;
    original.set_processing_time_ms(processing_time);
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::AIQueryResponse deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.processing_time_ms() == original.processing_time_ms());
}

// ============================================================================
// Property Tests - AIStreamEvent Round-Trip
// ============================================================================

RC_GTEST_PROP(ProtoRoundTrip, AIStreamEvent_PreservesContent, ()) {
    auto event_id = *genNonEmptyValidString();
    auto content = *genValidString();
    auto timestamp = *rc::gen::inRange(0, 999999999);
    
    agent_communication::AIStreamEvent original;
    original.set_event_id(event_id);
    original.set_event_type("partial");
    original.set_content(content);
    original.set_task_state("running");
    original.set_timestamp(timestamp);
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::AIStreamEvent deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.event_id() == original.event_id());
    RC_ASSERT(deserialized.event_type() == original.event_type());
    RC_ASSERT(deserialized.content() == original.content());
    RC_ASSERT(deserialized.task_state() == original.task_state());
    RC_ASSERT(deserialized.timestamp() == original.timestamp());
}

// ============================================================================
// Property Tests - Artifact Round-Trip
// ============================================================================

RC_GTEST_PROP(ProtoRoundTrip, Artifact_PreservesData, ()) {
    auto name = *genNonEmptyValidString();
    auto data = *genValidString();
    
    agent_communication::Artifact original;
    original.set_name(name);
    original.set_mime_type("text/plain");
    original.set_data(data);
    
    std::string serialized;
    RC_ASSERT(original.SerializeToString(&serialized));
    
    agent_communication::Artifact deserialized;
    RC_ASSERT(deserialized.ParseFromString(serialized));
    
    RC_ASSERT(deserialized.name() == original.name());
    RC_ASSERT(deserialized.mime_type() == original.mime_type());
    RC_ASSERT(deserialized.data() == original.data());
}

} // namespace

int main(int argc, char** argv) {
    ::testing::InitGoogleTest(&argc, argv);
    return RUN_ALL_TESTS();
}
