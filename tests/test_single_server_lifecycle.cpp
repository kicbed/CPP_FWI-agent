#include <gtest/gtest.h>

#include <agent_rpc/research/single_server_lifecycle.h>

using namespace agent_rpc::research;

TEST(SingleServerLifecycleTest, ParsesLifecycleStateNames) {
    EXPECT_EQ(parse_single_server_lifecycle_state("requested"),
        SingleServerLifecycleState::Requested);
    EXPECT_EQ(parse_single_server_lifecycle_state("reviewed"),
        SingleServerLifecycleState::Reviewed);
    EXPECT_EQ(parse_single_server_lifecycle_state("approved"),
        SingleServerLifecycleState::Approved);
    EXPECT_EQ(parse_single_server_lifecycle_state("rejected"),
        SingleServerLifecycleState::Rejected);
    EXPECT_EQ(parse_single_server_lifecycle_state("queued"),
        SingleServerLifecycleState::Queued);
    EXPECT_EQ(parse_single_server_lifecycle_state("running"),
        SingleServerLifecycleState::Running);
    EXPECT_EQ(parse_single_server_lifecycle_state("succeeded"),
        SingleServerLifecycleState::Succeeded);
    EXPECT_EQ(parse_single_server_lifecycle_state("failed"),
        SingleServerLifecycleState::Failed);
    EXPECT_EQ(parse_single_server_lifecycle_state("cancelled"),
        SingleServerLifecycleState::Cancelled);
    EXPECT_EQ(parse_single_server_lifecycle_state("other"),
        SingleServerLifecycleState::Unknown);
}

TEST(SingleServerLifecycleTest, CreatesRequestedRecordWithoutExecution) {
    const auto record = make_single_server_lifecycle_record(
        "job-1",
        "req-1",
        "researcher-a",
        "fwi_multiscale_review");

    EXPECT_EQ(record.job_id, "job-1");
    EXPECT_EQ(record.request_id, "req-1");
    EXPECT_EQ(record.user_id, "researcher-a");
    EXPECT_EQ(record.template_id, "fwi_multiscale_review");
    EXPECT_EQ(record.state, SingleServerLifecycleState::Requested);
    EXPECT_FALSE(record.server_connected);
    EXPECT_FALSE(record.command_executed);
    EXPECT_FALSE(record.workspace_created);
}

TEST(SingleServerLifecycleTest, AppendsValidSuccessLifecycleWithoutExecution) {
    auto record = make_single_server_lifecycle_record(
        "job-2",
        "req-2",
        "researcher-a",
        "fwi_multiscale_review");

    EXPECT_TRUE(append_single_server_lifecycle_event(
        record,
        SingleServerLifecycleState::Reviewed,
        "review packet accepted",
        "2026-06-23T12:01:00Z").empty());
    EXPECT_TRUE(append_single_server_lifecycle_event(
        record,
        SingleServerLifecycleState::Approved,
        "operator approved fake lifecycle",
        "2026-06-23T12:02:00Z").empty());
    EXPECT_TRUE(append_single_server_lifecycle_event(
        record,
        SingleServerLifecycleState::Queued,
        "queued in fake lifecycle only",
        "2026-06-23T12:03:00Z").empty());
    EXPECT_TRUE(append_single_server_lifecycle_event(
        record,
        SingleServerLifecycleState::Running,
        "running status simulated",
        "2026-06-23T12:04:00Z").empty());
    EXPECT_TRUE(append_single_server_lifecycle_event(
        record,
        SingleServerLifecycleState::Succeeded,
        "fake run succeeded",
        "2026-06-23T12:05:00Z").empty());

    EXPECT_EQ(record.state, SingleServerLifecycleState::Succeeded);
    ASSERT_EQ(record.events.size(), 5);
    EXPECT_EQ(record.events.front().state, SingleServerLifecycleState::Reviewed);
    EXPECT_EQ(record.events.back().state, SingleServerLifecycleState::Succeeded);
    EXPECT_FALSE(record.server_connected);
    EXPECT_FALSE(record.command_executed);
    EXPECT_FALSE(record.workspace_created);
}

TEST(SingleServerLifecycleTest, AllowsCancellationBeforeTerminalStates) {
    auto record = make_single_server_lifecycle_record(
        "job-cancel",
        "req-cancel",
        "researcher-a",
        "fwi_multiscale_review");

    ASSERT_TRUE(append_single_server_lifecycle_event(
        record,
        SingleServerLifecycleState::Reviewed,
        "reviewed",
        "2026-06-23T12:01:00Z").empty());
    ASSERT_TRUE(append_single_server_lifecycle_event(
        record,
        SingleServerLifecycleState::Approved,
        "approved",
        "2026-06-23T12:02:00Z").empty());

    const auto errors = append_single_server_lifecycle_event(
        record,
        SingleServerLifecycleState::Cancelled,
        "operator cancelled before fake queue",
        "2026-06-23T12:03:00Z");

    EXPECT_TRUE(errors.empty());
    EXPECT_EQ(record.state, SingleServerLifecycleState::Cancelled);
    EXPECT_FALSE(record.server_connected);
    EXPECT_FALSE(record.command_executed);
    EXPECT_FALSE(record.workspace_created);
}

TEST(SingleServerLifecycleTest, RejectsInvalidTransitionAfterRejected) {
    auto record = make_single_server_lifecycle_record(
        "job-3",
        "req-3",
        "researcher-a",
        "fwi_multiscale_review");
    record.state = SingleServerLifecycleState::Rejected;

    const auto errors = append_single_server_lifecycle_event(
        record,
        SingleServerLifecycleState::Running,
        "cannot run rejected job",
        "2026-06-23T12:00:00Z");

    ASSERT_FALSE(errors.empty());
    EXPECT_EQ(errors[0], "invalid lifecycle transition");
    EXPECT_EQ(record.state, SingleServerLifecycleState::Rejected);
    EXPECT_TRUE(record.events.empty());
}

TEST(SingleServerLifecycleTest, RendersLifecyclePreviewWithoutExecution) {
    auto record = make_single_server_lifecycle_record(
        "job-4",
        "req-4",
        "researcher-a",
        "fwi_multiscale_review");
    ASSERT_TRUE(append_single_server_lifecycle_event(
        record,
        SingleServerLifecycleState::Reviewed,
        "review packet accepted",
        "2026-06-23T12:01:00Z").empty());

    const auto rendered = render_single_server_lifecycle_preview(record);

    EXPECT_NE(rendered.find("Single Server Fake Lifecycle Preview"),
        std::string::npos);
    EXPECT_NE(rendered.find("job_id: job-4"), std::string::npos);
    EXPECT_NE(rendered.find("request_id: req-4"), std::string::npos);
    EXPECT_NE(rendered.find("state: reviewed"), std::string::npos);
    EXPECT_NE(rendered.find("allowed_next_states:"), std::string::npos);
    EXPECT_NE(rendered.find("- approved"), std::string::npos);
    EXPECT_NE(rendered.find("- rejected"), std::string::npos);
    EXPECT_NE(rendered.find("server_connected: false"), std::string::npos);
    EXPECT_NE(rendered.find("command_executed: false"), std::string::npos);
    EXPECT_NE(rendered.find("workspace_created: false"), std::string::npos);
    EXPECT_NE(rendered.find("- 2026-06-23T12:01:00Z reviewed review packet accepted"),
        std::string::npos);
    EXPECT_NE(rendered.find(
                  "safety_boundary: fake lifecycle only; no server connection, command execution, or workspace creation"),
        std::string::npos);
}
