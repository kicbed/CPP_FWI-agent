#pragma once

#include <string>
#include <vector>

namespace agent_rpc::research {

enum class SingleServerLifecycleState {
    Requested,
    Reviewed,
    Approved,
    Rejected,
    Queued,
    Running,
    Succeeded,
    Failed,
    Cancelled,
    Unknown,
};

struct SingleServerLifecycleEvent {
    SingleServerLifecycleState state = SingleServerLifecycleState::Unknown;
    std::string message;
    std::string timestamp;
};

struct SingleServerLifecycleRecord {
    std::string job_id;
    std::string request_id;
    std::string user_id;
    std::string template_id;
    SingleServerLifecycleState state = SingleServerLifecycleState::Requested;
    std::vector<SingleServerLifecycleEvent> events;
    bool server_connected = false;
    bool command_executed = false;
    bool workspace_created = false;
};

std::string to_string(SingleServerLifecycleState state);
SingleServerLifecycleState parse_single_server_lifecycle_state(
    const std::string& value);
SingleServerLifecycleRecord make_single_server_lifecycle_record(
    const std::string& job_id,
    const std::string& request_id,
    const std::string& user_id,
    const std::string& template_id);
std::vector<std::string> append_single_server_lifecycle_event(
    SingleServerLifecycleRecord& record,
    SingleServerLifecycleState next_state,
    const std::string& message,
    const std::string& timestamp);
std::string render_single_server_lifecycle_preview(
    const SingleServerLifecycleRecord& record);

}  // namespace agent_rpc::research
