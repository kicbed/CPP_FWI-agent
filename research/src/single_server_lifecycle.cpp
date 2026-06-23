#include "agent_rpc/research/single_server_lifecycle.h"

#include <sstream>

namespace agent_rpc::research {
namespace {

bool is_valid_transition(
    SingleServerLifecycleState from,
    SingleServerLifecycleState to) {
    using State = SingleServerLifecycleState;
    if (from == State::Requested) {
        return to == State::Reviewed || to == State::Rejected;
    }
    if (from == State::Reviewed) {
        return to == State::Approved || to == State::Rejected;
    }
    if (from == State::Approved) {
        return to == State::Queued || to == State::Cancelled;
    }
    if (from == State::Queued) {
        return to == State::Running || to == State::Cancelled;
    }
    if (from == State::Running) {
        return to == State::Succeeded ||
               to == State::Failed ||
               to == State::Cancelled;
    }
    return false;
}

std::vector<SingleServerLifecycleState> allowed_next_states(
    SingleServerLifecycleState state) {
    using State = SingleServerLifecycleState;
    if (state == State::Requested) {
        return {State::Reviewed, State::Rejected};
    }
    if (state == State::Reviewed) {
        return {State::Approved, State::Rejected};
    }
    if (state == State::Approved) {
        return {State::Queued, State::Cancelled};
    }
    if (state == State::Queued) {
        return {State::Running, State::Cancelled};
    }
    if (state == State::Running) {
        return {State::Succeeded, State::Failed, State::Cancelled};
    }
    return {};
}

}  // namespace

std::string to_string(SingleServerLifecycleState state) {
    switch (state) {
        case SingleServerLifecycleState::Requested:
            return "requested";
        case SingleServerLifecycleState::Reviewed:
            return "reviewed";
        case SingleServerLifecycleState::Approved:
            return "approved";
        case SingleServerLifecycleState::Rejected:
            return "rejected";
        case SingleServerLifecycleState::Queued:
            return "queued";
        case SingleServerLifecycleState::Running:
            return "running";
        case SingleServerLifecycleState::Succeeded:
            return "succeeded";
        case SingleServerLifecycleState::Failed:
            return "failed";
        case SingleServerLifecycleState::Cancelled:
            return "cancelled";
        case SingleServerLifecycleState::Unknown:
            return "unknown";
    }
    return "unknown";
}

SingleServerLifecycleState parse_single_server_lifecycle_state(
    const std::string& value) {
    if (value == "requested") return SingleServerLifecycleState::Requested;
    if (value == "reviewed") return SingleServerLifecycleState::Reviewed;
    if (value == "approved") return SingleServerLifecycleState::Approved;
    if (value == "rejected") return SingleServerLifecycleState::Rejected;
    if (value == "queued") return SingleServerLifecycleState::Queued;
    if (value == "running") return SingleServerLifecycleState::Running;
    if (value == "succeeded") return SingleServerLifecycleState::Succeeded;
    if (value == "failed") return SingleServerLifecycleState::Failed;
    if (value == "cancelled") return SingleServerLifecycleState::Cancelled;
    return SingleServerLifecycleState::Unknown;
}

SingleServerLifecycleRecord make_single_server_lifecycle_record(
    const std::string& job_id,
    const std::string& request_id,
    const std::string& user_id,
    const std::string& template_id) {
    SingleServerLifecycleRecord record;
    record.job_id = job_id;
    record.request_id = request_id;
    record.user_id = user_id;
    record.template_id = template_id;
    record.state = SingleServerLifecycleState::Requested;
    record.server_connected = false;
    record.command_executed = false;
    record.workspace_created = false;
    return record;
}

std::vector<std::string> append_single_server_lifecycle_event(
    SingleServerLifecycleRecord& record,
    SingleServerLifecycleState next_state,
    const std::string& message,
    const std::string& timestamp) {
    if (!is_valid_transition(record.state, next_state)) {
        return {"invalid lifecycle transition"};
    }
    record.state = next_state;
    record.events.push_back({next_state, message, timestamp});
    record.server_connected = false;
    record.command_executed = false;
    record.workspace_created = false;
    return {};
}

std::string render_single_server_lifecycle_preview(
    const SingleServerLifecycleRecord& record) {
    std::ostringstream out;
    out << "Single Server Fake Lifecycle Preview\n";
    out << "job_id: " << record.job_id << "\n";
    out << "request_id: " << record.request_id << "\n";
    out << "user_id: " << record.user_id << "\n";
    out << "template_id: " << record.template_id << "\n";
    out << "state: " << to_string(record.state) << "\n";
    out << "server_connected: false\n";
    out << "command_executed: false\n";
    out << "workspace_created: false\n";
    out << "allowed_next_states:\n";
    const auto next_states = allowed_next_states(record.state);
    if (next_states.empty()) {
        out << "- none\n";
    } else {
        for (const auto next_state : next_states) {
            out << "- " << to_string(next_state) << "\n";
        }
    }
    out << "events:\n";
    if (record.events.empty()) {
        out << "- none\n";
    } else {
        for (const auto& event : record.events) {
            out << "- " << event.timestamp << " "
                << to_string(event.state) << " "
                << event.message << "\n";
        }
    }
    out << "safety_boundary: fake lifecycle only; no server connection, "
        << "command execution, or workspace creation\n";
    return out.str();
}

}  // namespace agent_rpc::research
