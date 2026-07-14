#include <gtest/gtest.h>

#include "agent_rpc/orchestrator/tool_calling_engine.h"

namespace {

using agent_rpc::orchestrator::detail::plan_fwi_tool_call;
using agent_rpc::orchestrator::detail::extract_fwi_job_id;
using agent_rpc::orchestrator::ToolCallingEngine;

constexpr const char* kLastJob = "fwi-20260714T120000Z-abcdef123456";

struct NoopLLM {
    std::string chat(const std::string&, const std::string&) { return {}; }
};

TEST(FWIToolRoutingTest, RoutesChineseForwardDemo) {
    const auto call = plan_fwi_tool_call(
        "使用 marmousi_94_288 运行一个二维声学正演演示。");
    EXPECT_EQ(call.tool_name, "fwi_submit_demo");
    EXPECT_EQ(call.arguments.at("model_id"), "marmousi_94_288");
    EXPECT_EQ(call.arguments.at("preset"), "forward");
    EXPECT_EQ(call.arguments.at("device"), "cuda");
}

TEST(FWIToolRoutingTest, RoutesChineseTwoIterationSmoke) {
    const auto call = plan_fwi_tool_call(
        "使用 marmousi_94_288 运行两次迭代的 FWI smoke test。");
    EXPECT_EQ(call.tool_name, "fwi_submit_demo");
    EXPECT_EQ(call.arguments.at("preset"), "fwi_smoke");
}

TEST(FWIToolRoutingTest, RoutesPreviousJobStatus) {
    const auto call = plan_fwi_tool_call("查看刚才 FWI 任务的状态。", kLastJob);
    EXPECT_EQ(call.tool_name, "fwi_get_status");
    EXPECT_EQ(call.arguments.at("job_id"), kLastJob);
}

TEST(FWIToolRoutingTest, RoutesPreviousJobResult) {
    const auto call = plan_fwi_tool_call("显示刚才的反演结果和损失曲线。", kLastJob);
    EXPECT_EQ(call.tool_name, "fwi_get_result");
    EXPECT_EQ(call.arguments.at("job_id"), kLastJob);
}

TEST(FWIToolRoutingTest, TheoryQuestionDoesNotLaunchComputation) {
    const auto call = plan_fwi_tool_call("什么是 FWI？", kLastJob);
    EXPECT_TRUE(call.tool_name.empty());
    EXPECT_TRUE(call.arguments.empty());

    const auto how_to = plan_fwi_tool_call(
        "解释如何使用 marmousi_94_288 运行 FWI smoke test。", kLastJob);
    EXPECT_TRUE(how_to.tool_name.empty());
}

TEST(FWIToolRoutingTest, LiveRouterBypassRecognizesActionButNotTheory) {
    agent_rpc::mcp::MCPAgentIntegration integration;
    NoopLLM llm;
    ToolCallingEngine engine(&integration, llm);

    EXPECT_TRUE(engine.has_explicit_fwi_action(
        "使用 marmousi_94_288 运行两次迭代的 FWI smoke test。"));
    EXPECT_FALSE(engine.has_explicit_fwi_action("什么是 FWI？"));
}

TEST(FWIToolRoutingTest, ExplicitStrictJobIdOverridesPreviousJob) {
    const auto call = plan_fwi_tool_call(
        "查看 fwi-20260714T130000Z-012345abcdef 的状态。", kLastJob);
    EXPECT_EQ(call.tool_name, "fwi_get_status");
    EXPECT_EQ(call.arguments.at("job_id"), "fwi-20260714T130000Z-012345abcdef");
}

TEST(FWIToolRoutingTest, ExtractsSubmittedJobFromNestedMCPText) {
    const std::string response =
        R"({"content":[{"type":"text","text":"{\"job_id\":\"fwi-20260714T120000Z-abcdef123456\",\"status\":\"queued\"}"}],"isError":false})";
    EXPECT_EQ(extract_fwi_job_id(response), kLastJob);
}

}  // namespace
