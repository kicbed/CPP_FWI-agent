#include <gtest/gtest.h>

#include "agent_rpc/orchestrator/tool_calling_engine.h"

namespace {

using agent_rpc::orchestrator::detail::plan_fwi_tool_call;
using agent_rpc::orchestrator::detail::extract_fwi_job_id;
using agent_rpc::orchestrator::detail::has_fwi_negative_intent;
using agent_rpc::orchestrator::detail::has_invalid_fwi_iteration_request;
using agent_rpc::orchestrator::detail::is_fwi_capability_query;
using agent_rpc::orchestrator::detail::is_fwi_howto_query;
using agent_rpc::orchestrator::detail::is_fwi_tool_call_allowed;
using agent_rpc::orchestrator::detail::resolve_allow_legacy_fwi_submit;
using agent_rpc::orchestrator::ToolCallingEngine;

constexpr const char* kLastJob = "fwi-20260714T120000Z-abcdef123456";

struct NoopLLM {
    std::string chat(const std::string&, const std::string&) { return {}; }
};

TEST(FWIToolRoutingTest, SourcePolicyDefaultsToLegacyCompatibility) {
    EXPECT_TRUE(resolve_allow_legacy_fwi_submit(nlohmann::json::object()));
    EXPECT_TRUE(resolve_allow_legacy_fwi_submit({
        {"metadata", {{"unrelated", "value"}}}
    }));
}

TEST(FWIToolRoutingTest, SourcePolicyAcceptsOnlyExactFalseString) {
    EXPECT_FALSE(resolve_allow_legacy_fwi_submit({
        {"metadata", {{"allow_legacy_fwi_submit", "false"}}}
    }));

    for (const auto& invalid : {
             nlohmann::json(true),
             nlohmann::json(false),
             nlohmann::json("true"),
             nlohmann::json("False"),
             nlohmann::json(0),
         }) {
        const nlohmann::json params = {
            {"metadata", {{"allow_legacy_fwi_submit", invalid}}}
        };
        EXPECT_THROW(resolve_allow_legacy_fwi_submit(params),
                     std::invalid_argument);
    }
    EXPECT_THROW(resolve_allow_legacy_fwi_submit({{"metadata", "false"}}),
                 std::invalid_argument);
}

TEST(FWIToolRoutingTest, SourcePolicyBlocksOnlyActualLegacySubmissionPlan) {
    const auto submit = plan_fwi_tool_call(
        "使用 marmousi_94_288 运行两次迭代的 FWI smoke test。",
        kLastJob);
    const auto status = plan_fwi_tool_call(
        "查看刚才 FWI 任务的状态。", kLastJob);

    ASSERT_EQ(submit.tool_name, "fwi_submit_demo");
    ASSERT_EQ(status.tool_name, "fwi_get_status");
    EXPECT_FALSE(is_fwi_tool_call_allowed(submit, false));
    EXPECT_TRUE(is_fwi_tool_call_allowed(status, false));
    EXPECT_TRUE(is_fwi_tool_call_allowed(submit, true));
}

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

TEST(FWIToolRoutingTest, RoutesDemoAndCpuOnlyWhenExplicitlyRequested) {
    const auto demo = plan_fwi_tool_call(
        "使用 marmousi_94_288 运行二维声学 FWI demo。");
    EXPECT_EQ(demo.tool_name, "fwi_submit_demo");
    EXPECT_EQ(demo.arguments.at("preset"), "fwi_demo");
    EXPECT_EQ(demo.arguments.at("device"), "cuda");
    EXPECT_EQ(demo.arguments.at("iterations"), 5);

    const auto cpu_smoke = plan_fwi_tool_call(
        "使用 marmousi_94_288 在 CPU 上运行两次迭代的二维声学 FWI smoke test。");
    EXPECT_EQ(cpu_smoke.tool_name, "fwi_submit_demo");
    EXPECT_EQ(cpu_smoke.arguments.at("preset"), "fwi_smoke");
    EXPECT_EQ(cpu_smoke.arguments.at("device"), "cpu");
    EXPECT_EQ(cpu_smoke.arguments.at("iterations"), 2);
}

TEST(FWIToolRoutingTest, CombinedRunAndResultStillSubmitsAsyncJob) {
    // This is the natural-language form used by the Web UI. It deliberately
    // omits the acronym "FWI" and the full model ID, so the deterministic
    // router must recognise the whitelisted Marmousi alias plus the concrete
    // inversion-test action instead of falling through to a code-writing LLM.
    const auto colloquial = plan_fwi_tool_call(
        "做一下marmousi的反演测试，迭代50次，完成后展示结果", kLastJob);
    EXPECT_EQ(colloquial.tool_name, "fwi_submit_demo");
    EXPECT_EQ(colloquial.arguments.at("model_id"), "marmousi_94_288");
    EXPECT_EQ(colloquial.arguments.at("preset"), "fwi_demo");
    EXPECT_EQ(colloquial.arguments.at("device"), "cuda");
    EXPECT_EQ(colloquial.arguments.at("iterations"), 50);

    const auto smoke = plan_fwi_tool_call(
        "使用 marmousi_94_288 在 CUDA 上运行2次迭代的FWI并向我展示结果", kLastJob);
    EXPECT_EQ(smoke.tool_name, "fwi_submit_demo");
    EXPECT_EQ(smoke.arguments.at("preset"), "fwi_smoke");
    EXPECT_EQ(smoke.arguments.at("iterations"), 2);

    const auto demo = plan_fwi_tool_call(
        "使用 marmousi_94_288 在 CUDA 上运行 5 次迭代的 FWI 并向我展示结果", kLastJob);
    EXPECT_EQ(demo.tool_name, "fwi_submit_demo");
    EXPECT_EQ(demo.arguments.at("preset"), "fwi_demo");
    EXPECT_EQ(demo.arguments.at("iterations"), 5);

    const auto execute = plan_fwi_tool_call(
        "执行 marmousi_94_288 的 50 次迭代 FWI，然后展示结果", kLastJob);
    EXPECT_EQ(execute.tool_name, "fwi_submit_demo");
    EXPECT_EQ(execute.arguments.at("iterations"), 50);
}

TEST(FWIToolRoutingTest, ExplicitIterationCountIsPreservedUpToSafetyLimit) {
    const std::string request =
        "使用 marmousi_94_288 在 CUDA 上运行50次迭代的FWI并向我展示结果";
    EXPECT_FALSE(has_invalid_fwi_iteration_request(request));
    const auto call = plan_fwi_tool_call(request, kLastJob);
    EXPECT_EQ(call.tool_name, "fwi_submit_demo");
    EXPECT_EQ(call.arguments.at("preset"), "fwi_demo");
    EXPECT_EQ(call.arguments.at("device"), "cuda");
    EXPECT_EQ(call.arguments.at("iterations"), 50);

    const auto english = plan_fwi_tool_call(
        "Run a marmousi_94_288 FWI demo for 10 iterations.");
    EXPECT_EQ(english.tool_name, "fwi_submit_demo");
    EXPECT_EQ(english.arguments.at("iterations"), 10);

    const auto reversed = plan_fwi_tool_call(
        "使用 marmousi_94_288 运行 FWI，迭代 50 次。");
    EXPECT_EQ(reversed.tool_name, "fwi_submit_demo");
    EXPECT_EQ(reversed.arguments.at("iterations"), 50);

    const auto rounds = plan_fwi_tool_call(
        "使用 marmousi_94_288 运行50轮 FWI。");
    EXPECT_EQ(rounds.tool_name, "fwi_submit_demo");
    EXPECT_EQ(rounds.arguments.at("iterations"), 50);

    const auto upper_bound = plan_fwi_tool_call(
        "使用 marmousi_94_288 在 CUDA 上运行 10000 次迭代的 FWI。");
    EXPECT_EQ(upper_bound.tool_name, "fwi_submit_demo");
    EXPECT_EQ(upper_bound.arguments.at("iterations"), 10000);

    EXPECT_TRUE(has_invalid_fwi_iteration_request(
        "使用 marmousi_94_288 运行 0 次迭代的 FWI。"));
    EXPECT_TRUE(has_invalid_fwi_iteration_request(
        "使用 marmousi_94_288 运行 10001 次迭代的 FWI。"));
    EXPECT_TRUE(plan_fwi_tool_call(
        "使用 marmousi_94_288 运行 10001 次迭代的 FWI。", kLastJob).tool_name.empty());
    EXPECT_TRUE(has_invalid_fwi_iteration_request(
        "使用 marmousi_94_288 运行 2.5 次迭代的 FWI。"));
    EXPECT_TRUE(has_invalid_fwi_iteration_request(
        "使用 marmousi_94_288 运行 -3 次迭代的 FWI。"));
    EXPECT_TRUE(plan_fwi_tool_call(
        "使用 marmousi_94_288 运行 2.5 次迭代的 FWI。", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "使用 marmousi_94_288 运行 -3 次迭代的 FWI。", kLastJob).tool_name.empty());
}

TEST(FWIToolRoutingTest, RoutesPreviousJobStatus) {
    const auto call = plan_fwi_tool_call("查看刚才 FWI 任务的状态。", kLastJob);
    EXPECT_EQ(call.tool_name, "fwi_get_status");
    EXPECT_EQ(call.arguments.at("job_id"), kLastJob);
}

TEST(FWIToolRoutingTest, RunStatusAndRunResultPhrasesDoNotSubmitNewJob) {
    const auto status = plan_fwi_tool_call(
        "查看 marmousi_94_288 FWI 的运行状态。", kLastJob);
    EXPECT_EQ(status.tool_name, "fwi_get_status");
    EXPECT_EQ(status.arguments.at("job_id"), kLastJob);

    const auto result = plan_fwi_tool_call(
        "显示 marmousi_94_288 FWI 的运行结果。", kLastJob);
    EXPECT_EQ(result.tool_name, "fwi_get_result");
    EXPECT_EQ(result.arguments.at("job_id"), kLastJob);

    const auto situation = plan_fwi_tool_call(
        "查看 marmousi_94_288 FWI 的运行情况。", kLastJob);
    EXPECT_EQ(situation.tool_name, "fwi_get_status");

    const auto completed = plan_fwi_tool_call(
        "查看 marmousi_94_288 FWI 运行了吗？", kLastJob);
    EXPECT_EQ(completed.tool_name, "fwi_get_status");

    const auto fail_closed = plan_fwi_tool_call(
        "查看 marmousi_94_288 FWI 运行", kLastJob);
    EXPECT_TRUE(fail_closed.tool_name.empty());
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

    // Nearby wording must remain explanatory: adding an iteration number to
    // a theory/result-analysis question is not authorisation to launch a job.
    EXPECT_TRUE(plan_fwi_tool_call(
        "解释一下 marmousi 的反演测试原理，迭代 50 次意味着什么。",
        kLastJob).tool_name.empty());
    const auto analyse_result = plan_fwi_tool_call(
        "帮我分析一下 marmousi 的反演测试结果。", kLastJob);
    EXPECT_NE(analyse_result.tool_name, "fwi_submit_demo");
    EXPECT_EQ(analyse_result.tool_name, "fwi_get_result");
}

TEST(FWIToolRoutingTest, CapabilityQuestionDoesNotLaunchComputation) {
    EXPECT_TRUE(is_fwi_capability_query("你可以做 FWI 反演吗？"));
    EXPECT_TRUE(is_fwi_capability_query("marmousi_94_288 的 FWI 能运行吗？"));

    EXPECT_TRUE(plan_fwi_tool_call("你可以做 FWI 反演吗？", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "marmousi_94_288 的 FWI 能运行吗？", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "marmousi_94_288 的 FWI 能在 CUDA 上运行吗？", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "Can you run a marmousi_94_288 FWI demo?", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "CAN YOU RUN a marmousi_94_288 FWI demo?", kLastJob).tool_name.empty());
}

TEST(FWIToolRoutingTest, HowToQuestionDoesNotLaunchComputation) {
    EXPECT_TRUE(is_fwi_howto_query("怎么启动一个 FWI 反演呢？"));
    EXPECT_TRUE(plan_fwi_tool_call(
        "怎么启动一个 marmousi_94_288 FWI 反演呢？", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "How to run a marmousi_94_288 FWI demo?", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "HOW TO RUN a marmousi_94_288 FWI demo?", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "how to execute a marmousi_94_288 FWI demo?", kLastJob).tool_name.empty());
}

TEST(FWIToolRoutingTest, NegatedExecutionNeverLaunchesComputation) {
    const std::string request = "不要运行 marmousi_94_288 FWI，只说明怎么启动。";
    EXPECT_TRUE(has_fwi_negative_intent(request));
    EXPECT_TRUE(plan_fwi_tool_call(request, kLastJob).tool_name.empty());

    EXPECT_TRUE(plan_fwi_tool_call(
        "别执行 marmousi_94_288 的 FWI demo。", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "不执行 marmousi_94_288 的 FWI demo。", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "Do not run a marmousi_94_288 FWI demo.", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "DON'T EXECUTE a marmousi_94_288 FWI demo.", kLastJob).tool_name.empty());
}

TEST(FWIToolRoutingTest, EnglishActionsRequireWholeWords) {
    EXPECT_TRUE(plan_fwi_tool_call(
        "Marmousi FWI runtime configuration", kLastJob).tool_name.empty());
    EXPECT_TRUE(plan_fwi_tool_call(
        "Marmousi FWI startup guide", kLastJob).tool_name.empty());

    const auto run = plan_fwi_tool_call(
        "run a marmousi_94_288 FWI demo", kLastJob);
    EXPECT_EQ(run.tool_name, "fwi_submit_demo");
}

TEST(FWIToolRoutingTest, MixedExecutionPhrasesRemainExplicitActions) {
    for (const char* request : {
             "介绍并运行 Marmousi FWI",
             "run Marmousi FWI display model",
             "运行 Marmousi FWI，分析模型",
         }) {
        EXPECT_EQ(plan_fwi_tool_call(request, kLastJob).tool_name,
                  "fwi_submit_demo") << request;
    }
}

TEST(FWIToolRoutingTest, LiveRouterBypassRecognizesActionButNotTheory) {
    agent_rpc::mcp::MCPAgentIntegration integration;
    NoopLLM llm;
    ToolCallingEngine engine(&integration, llm);

    EXPECT_TRUE(engine.has_explicit_fwi_action(
        "使用 marmousi_94_288 运行两次迭代的 FWI smoke test。"));
    EXPECT_TRUE(engine.has_explicit_fwi_action(
        "做一下marmousi的反演测试，迭代50次，完成后展示结果"));
    EXPECT_TRUE(engine.has_explicit_fwi_submission(
        "做一下marmousi的反演测试，迭代50次，完成后展示结果"));
    EXPECT_FALSE(engine.has_explicit_fwi_submission(
        "查看刚才 FWI 任务的状态。"));
    EXPECT_FALSE(engine.has_explicit_fwi_action("什么是 FWI？"));
    EXPECT_FALSE(engine.has_explicit_fwi_action("你可以做 FWI 反演吗？"));
    EXPECT_FALSE(engine.has_explicit_fwi_action("不要运行 marmousi_94_288 FWI。"));

    EXPECT_TRUE(engine.has_fwi_guidance_request("什么是 FWI？"));
    EXPECT_TRUE(engine.has_fwi_guidance_request("你可以做 FWI 反演吗？"));
    EXPECT_TRUE(engine.has_fwi_guidance_request("怎么启动一个 FWI 反演呢？"));
    EXPECT_TRUE(engine.has_fwi_guidance_request("不要运行 marmousi_94_288 FWI。"));
}

TEST(FWIToolRoutingTest, RemembersLatestFwiJobPerConversation) {
    agent_rpc::mcp::MCPAgentIntegration integration;
    NoopLLM llm;
    ToolCallingEngine engine(&integration, llm);

    constexpr const char* kContextA = "ctx-conversation-a";
    constexpr const char* kContextB = "ctx-conversation-b";
    constexpr const char* kJobB = "fwi-20260714T121500Z-012345abcdef";
    const std::string previous_status = "查看刚才 FWI 任务的状态。";

    engine.remember_fwi_job(kContextA, kLastJob);

    EXPECT_TRUE(engine.has_explicit_fwi_action(previous_status, kContextA));
    EXPECT_FALSE(engine.has_explicit_fwi_action(previous_status, kContextB));
    EXPECT_FALSE(engine.has_explicit_fwi_action(previous_status));

    engine.remember_fwi_job(kContextB, kJobB);

    EXPECT_TRUE(engine.has_explicit_fwi_action(previous_status, kContextA));
    EXPECT_TRUE(engine.has_explicit_fwi_action(previous_status, kContextB));
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
