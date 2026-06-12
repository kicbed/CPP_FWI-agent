# v0.4 Experiment Planner Test Report

Date: 2026-06-12

## Scope

v0.4 turns the Experiment Planner from a skeleton agent with static
AlgorithmCard prompt context into a deterministic dry-run experiment planning
component. The completed scope includes:

- request-specific AlgorithmCard and ResearchKnowledge retrieval
- algorithm recommendation
- assumptions
- parameter table
- risk analysis
- next-step plan
- ExperimentSpec JSON
- dry-run JobSpec text
- reproducible experiment record JSON

The implementation remains dry-run only. It does not execute CUDA/MPI jobs,
does not connect SSH, Slurm, PBS, or remote servers, and does not execute shell
commands from user input.

## Verification Commands

```bash
cmake --build build -j2
ctest --test-dir build -R "Planner(Context|Answer)" --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

## Results

- PASS. RED build failed first for the expected missing
  `agent_rpc/research/planner_answer.h`.
- PASS. `cmake --build build -j2` exited 0 after implementation.
- PASS. Planner targeted tests passed 2/2.
- PASS. Full `ctest` passed 23/23 tests.
- PASS. `git diff --check` produced no output.

## Coverage Summary

`PlannerContextTest` protects deterministic grounding. It verifies that a
Marmousi multi-scale FWI request with low-frequency and cycle-skipping language
is converted into method, dataset, failure-mode, and parameter retrieval keys.
It also verifies that local AlgorithmCards and ResearchKnowledge notes are
selected before the LLM path.

`PlannerAnswerTest` protects v0.4 planning output. It verifies that the system
generates a structured algorithm recommendation, parameter plan, risk analysis,
ExperimentSpec, dry-run JobSpec, dry-run rendered text, and versioned
experiment record.

The full `ctest` run protects integration with the rest of the repository:
RPC serialization, A2A integration, Code Agent contracts, AlgorithmCard
registry, ExperimentSpec validation, ResearchKnowledge retrieval, Planner
context and answer generation, MCP integration, RAG properties, service
registry, and agent communication.

## 中文知识点总结

### 1. 解决的问题

v0.2 已经建立了安全边界：Code Agent 默认只读，实验规划只能 dry-run，
AlgorithmCard、ExperimentSpec、JobSpec 和 DryRunBackend 都已经存在。v0.3
进一步把研究知识整理成 JSON-backed 的本地知识库，可以按 method、dataset、
failure mode 和 parameter advice 检索。但是在 v0.3 结束时，Planner 还不够像
一个真正的实验规划器。

上一版的不足主要在三个地方。第一，Planner Agent 只有 skeleton，prompt 里虽然
能放 AlgorithmCard 摘要，但没有确定性地把用户请求和本地知识连接起来。第二，
Planner 的输出形状没有被代码约束，LLM 可能给出一段自然语言建议，却不一定包含
算法推荐、参数表、风险分析、ExperimentSpec、JobSpec 和可复现实验记录。第三，
虽然 DryRunBackend 存在，但 Planner 回答没有稳定地把 dry-run JobSpec 渲染出来，
所以用户还不能把一次计划作为实验记录保存和复盘。

v0.4 要解决的就是这个断点：让 Planner 在调用 LLM 之前，已经由本地 C++ 代码生成
一个结构化、可测试、可复现、明确 dry-run 的规划草案。LLM 可以负责表达和解释，
但核心结构不能完全依赖模型自由发挥。

### 2. 实现方式

v0.4 的数据流分成两层。

第一层是 `PlannerContext`。它从用户请求中推断结构化检索条件，比如算法标签
`fwi`、方法 `multi-scale-fwi`、数据集 `marmousi`、失败模式
`cycle_skipping` 和参数 `frequency_band`。然后它调用 `AlgorithmRegistry` 和
`ResearchKnowledgeBase`，组合出与请求相关的 AlgorithmCard、研究笔记、失败案例
和参数建议。这个层的职责是 grounding，也就是把“用户自然语言问题”落到本地可验证
知识上。

第二层是 `PlannerAnswer`。它接收 `PlannerContext`，生成结构化输出：
算法推荐、假设列表、参数表、风险分析、下一步计划、ExperimentSpec、JobSpec、
dry-run job text 和 experiment record JSON。这个层的职责是 planning scaffold，
也就是给 LLM 和用户一个稳定的实验计划骨架。

API 形状刻意保持小而明确。`PlannerContextRequest` 表示检索意图，
`PlannerContext` 表示检索结果，`PlannerAnswer` 表示规划结果。关键函数是
`infer_planner_context_request(...)`、`build_planner_context(...)` 和
`build_planner_answer(...)`。这样设计的好处是每一步都能单独测试，也方便后续把
PlannerAnswer 渲染到 Web UI、保存到实验历史、或转交给未来的受控后端。

这里没有引入真实 CUDA/MPI，也没有生成真实 Slurm 脚本。`JobSpec.command` 只是
dry-run preview，包含 `--dry-run`，并由 `DryRunBackend` 渲染为带
`dry_run: true` 的文本。这样比直接接真实服务器简单，也比纯 prompt 文本更可靠。
更复杂的后端调度、认证、工作目录隔离、日志采集和 artifact 管理应该留到 v0.8，
因为那些属于真实执行系统，不应该和 Planner MVP 混在一起。

### 3. 关键文件、测试、资源

`research/include/agent_rpc/research/planner_context.h` 和
`research/src/planner_context.cpp` 负责确定性检索。它们保护的风险是：Planner 不应
只靠 LLM 记忆或泛泛 prompt，而应该先从本地 AlgorithmCard 和 ResearchKnowledge
拿证据。

`research/include/agent_rpc/research/planner_answer.h` 和
`research/src/planner_answer.cpp` 负责结构化规划输出。它们把 context 转成
parameter table、risk analysis、ExperimentSpec、JobSpec 和 experiment record。
这部分是 v0.4 的核心能力。

`examples/ai_orchestrator/experiment_planner_agent_main.cpp` 现在会在 prompt 中注入
deterministic planner context 和 structured planner scaffold。换句话说，LLM 收到
的不是一个空泛问题，而是一个已经包含本地证据和 dry-run 边界的计划草案。

`tests/test_planner_context.cpp` 保护检索正确性。它验证 Marmousi、多尺度 FWI、低频
缺失和 cycle skipping 这些关键词会落到正确 method、dataset、failure mode 和
parameter 上。

`tests/test_planner_answer.cpp` 保护输出形状。它验证 answer 中必须包含 FWI 算法、
参数表、cycle-skipping 风险、ExperimentSpec、JobSpec、`dry_run: true` 和
`lab-agent-experiment-record-v0.4` 记录 schema。这个测试防止以后重构时把结构化
输出退化成普通聊天回答。

关键资源包括 `resources/algorithms/fwi_cuda_mpi.json`、
`resources/research_knowledge/algorithms/multiscale_fwi.json`、
`resources/research_knowledge/failure_cases/cycle_skipping_low_frequency.json` 和
`resources/research_knowledge/experiments/marmousi_multiscale_fwi_dry_run.json`。
它们分别提供算法元数据、方法建议、失败模式诊断和历史 dry-run 实验参考。

### 4. 安全或产品边界

v0.4 没有接真实 CUDA/MPI。代码中出现 `mpirun -np 4` 是 dry-run JobSpec 文本的一
部分，用来表达未来可能的命令形状，但不会被执行。`DryRunBackend` 只渲染字符串，
不调用 shell、不启动进程、不提交作业。

v0.4 没有接 SSH、Slurm、PBS 或远程服务器。PlannerAnswer 的 experiment record 中
明确写入 `real_execution_enabled: false`，JobSpec 的 backend 固定是 `dry_run`。
这意味着即使用户请求“帮我跑一下”，当前能力也只能生成计划和 dry-run 预览。

v0.4 不执行来自用户输入的任意 shell 命令。用户文本只用于推断 method、dataset、
failure mode 和 parameter 等检索键。它不会被拼接成可执行命令。JobSpec 中的命令
是模板化 dry-run preview，不会提交给系统。

Code Agent 的边界没有变化。Code Agent 仍然默认只读，可以生成 patch 建议，但不会
自动应用 patch。本次改动也没有给 Code Agent 写权限。

这个边界很重要，因为科研计算平台后续一旦接入真实服务器，就会涉及账号权限、队列
资源、数据路径、日志、artifact、取消任务和审计。v0.4 的正确做法是先把“计划”和
“记录”做好，而不是过早把计划变成真实执行。

### 5. 调试或 TDD 证据

本轮先写 `tests/test_planner_answer.cpp`，要求新增 API
`agent_rpc/research/planner_answer.h`。第一次运行
`cmake --build build -j2` 时，构建按预期失败，错误是缺少该头文件。这是 RED 阶段，
证明测试确实在约束一个尚不存在的能力。

随后实现 `PlannerAnswer` 和 `build_planner_answer(...)`，并把源文件接入
`research/CMakeLists.txt`。再次运行 `cmake --build build -j2` 后构建通过。接着跑
`ctest --test-dir build -R "Planner(Context|Answer)" --output-on-failure`，两个
Planner 测试都通过。最后跑全量 `ctest --test-dir build --output-on-failure`，
23 个测试全部通过。

这个验证链条证明了三件事。第一，新测试不是事后补的，它先失败过。第二，
PlannerAnswer 的核心输出可以由本地资源确定性生成。第三，新增能力没有破坏已有
RPC、A2A、MCP、RAG、Code Agent、AlgorithmCard、ExperimentSpec 和 ResearchKnowledge
测试。

### 6. 面试怎么讲

项目短 pitch 可以这样说：这是一个面向地震 FWI 科研计算的多智能体工作台，底层用
C++、gRPC、A2A、MCP、RAG 和 Redis，前期重点不是直接跑集群任务，而是把研究知识、
算法元数据、实验计划和 dry-run 作业记录做成可验证的安全工作流。v0.4 已经能从
用户请求生成结构化实验计划、ExperimentSpec、dry-run JobSpec 和可复现实验记录。

技术深挖版可以这样讲：我没有让 LLM 直接自由生成实验方案，而是在 LLM 前面加了
deterministic planning scaffold。用户问题先通过 `PlannerContext` 落到 method、
dataset、failure mode、parameter 等结构化检索键，再从 AlgorithmCard 和本地研究
知识库取证据。然后 `PlannerAnswer` 把证据转成算法推荐、参数表、风险分析、下一步
计划、ExperimentSpec、JobSpec 和 experiment record。这个设计把“可验证的系统逻辑”
和“LLM 的自然语言表达”拆开，提高了可复现性和安全性。

常见追问：为什么不用纯 LLM 生成？回答是：科研实验计划需要可复现记录和安全边界，
纯 LLM 输出很难保证每次都有完整字段，也难保证不会暗示真实执行。把结构化部分放在
C++ builder 里，可以用测试约束输出形状。

常见追问：为什么现在不接 Slurm 或 PBS？回答是：真实后端需要认证、授权、工作目录
隔离、日志采集、artifact 管理和审计。v0.4 的目标是把计划和记录做好，真实执行放到
v0.8，更符合安全演进。

常见追问：JobSpec 里为什么出现 `mpirun`？回答是：这是 dry-run preview，用来让用户
看到未来真实命令的大致形状，但当前系统不会执行它。测试和文档都明确要求
`dry_run: true` 和 `real_execution_enabled: false`。

STAR 复盘可以这样说：Situation 是项目已有多 Agent 框架和研究知识库，但 Planner 还
只是骨架。Task 是把 Planner 做到可以稳定生成实验计划。Action 是先用 TDD 写
PlannerAnswer 测试，再实现确定性 context 检索和 structured answer builder，并将它
接入 Agent prompt。Result 是 v0.4 完成，全量 23 个测试通过，系统能输出结构化 dry-run
实验计划和可复现实验记录，同时保持真实执行关闭。
