# v0.8 Server Backend Safety Foundation Test Report

Date: 2026-06-22

Status: complete for the v0.8 safety-foundation scope.

## Scope

v0.8 prepares the project for future controlled server execution without
enabling real execution yet. The work adds a server-job safety model around the
existing dry-run backend boundary:

- `JobSubmissionRequest` for structured future submission input.
- `JobRecord` for lifecycle, validation, log, and artifact tracking.
- `JobLifecycleState` for stable job state names.
- `ApprovedJobTemplate` for template-driven execution boundaries.
- `validate_submission_boundary` to keep non-`dry_run` backends rejected.
- `validate_approved_template` to reject unknown or mismatched templates.
- `validate_workspace_path` to reject workspace traversal and path separators.
- `make_rejected_job_record` and `append_lifecycle_event` for in-memory job
  records.

The project still does not submit, run, cancel, poll, or monitor real jobs.

## Files Changed

- `research/include/agent_rpc/research/server_job.h`
- `research/src/server_job.cpp`
- `research/CMakeLists.txt`
- `tests/test_server_job.cpp`
- `tests/CMakeLists.txt`
- `docs/upgrade/server-backend-safety-v0.8.md`
- `docs/superpowers/plans/2026-06-22-server-backend-v0.8.md`
- `docs/upgrade/README.md`
- `docs/upgrade/milestones.md`
- `docs/upgrade/version-roadmap.md`
- `docs/upgrade/career-notes.md`
- `docs/upgrade/upgrade-log.md`
- `docs/upgrade/test-report-v0.8.md`

## Verification Commands

Final verification command:

```bash
cmake --build build -j2
ctest --test-dir build --output-on-failure
git diff --check
```

Result:

- PASS. `cmake --build build -j2` exited 0.
- PASS. Full `ctest --test-dir build --output-on-failure` passed 26/26 tests.
- PASS. `git diff --check` produced no output.

Targeted test command used during implementation:

```bash
ctest --test-dir build -R ServerJobTest --output-on-failure
```

Result:

- PASS. `ServerJobTest` passed after each implementation step.

## TDD Evidence

The v0.8 code work used RED/GREEN steps:

- Server job model RED: build failed because
  `agent_rpc/research/server_job.h` did not exist.
- Approved template RED: build failed because `ApprovedJobTemplate` and
  `validate_approved_template` did not exist.
- Workspace guard RED: build failed because `validate_workspace_path` did not
  exist.
- Lifecycle helper RED: build failed because `make_rejected_job_record` and
  `append_lifecycle_event` did not exist.

After each RED failure, only the minimal API and implementation needed for that
batch was added. The targeted `ServerJobTest`, full CTest suite, and
`git diff --check` were then run before committing.

## Test Coverage

`ServerJobTest.SubmissionRequestDefaultsToDryRun`

This test protects the default safety posture. A newly constructed
`JobSubmissionRequest` defaults to `dry_run: true` and
`JobBackendType::DryRun`. That matters because future API handlers should not
accidentally create executable requests by omission.

`ServerJobTest.ParsesLifecycleStateNames`

This test protects stable string names for lifecycle states. Future UI,
database, and audit-log code can rely on values such as `draft`, `queued`,
`running`, `succeeded`, `failed`, and `cancelled`.

`ServerJobTest.RejectsNonDryRunSubmissionBeforeBackendsAreEnabled`

This test protects the v0.7 backend guard inside the new v0.8 submission
boundary. Even if a request names `slurm`, the validator still returns the
existing "only dry_run is enabled" message.

`ServerJobTest.RequiresApprovedTemplateForSubmission`

This test protects the approved-template boundary. Future server submission
must reference an approved template ID rather than a raw command string.
Unknown templates are rejected.

`ServerJobTest.AcceptsMatchingDryRunTemplate`

This test protects the positive path for safe metadata-only validation. A
matching dry-run template and version passes validation, proving the API can
accept known-good template records without enabling execution.

`ServerJobTest.RejectsWorkspaceTraversal`

This test protects workspace isolation. A job directory name such as
`../outside` is rejected before it can escape the configured workspace root.

`ServerJobTest.AcceptsGeneratedWorkspaceName`

This test protects the expected happy path: generated leaf workspace names such
as `job-20260622-0001` are accepted.

`ServerJobTest.CreatesRejectedRecordFromValidationErrors`

This test protects auditability for rejected jobs. Validation errors can be
converted into a `JobRecord` with state `Rejected` before any future submission
attempt.

`ServerJobTest.AppendsLifecycleEventWithoutExecutingCommands`

This test protects lifecycle history as a pure in-memory operation. Updating a
record to `Queued` appends a status event but does not execute commands,
contact a scheduler, or mutate external infrastructure.

## Safety Boundaries

v0.8 does not add any of the following:

- Real CUDA/MPI execution.
- SSH connections.
- Slurm submission.
- PBS submission.
- Remote server execution.
- Local wrapper script execution.
- Arbitrary shell execution from user input.
- Credentials, tokens, private keys, or cluster account handling.
- Automatic Code Agent patch application.

The only enabled backend remains `dry_run`. The existing
`validate_backend_enabled` guard is reused by `validate_submission_boundary`, so
reserved backend values still fail before execution.

The Code Agent remains read-only by default. It may propose patches as text,
but this version does not give it permission to apply patches automatically.

## Chinese Learning Summary

For a reading order, code guide, test-risk map, and self-check questions, see
`docs/upgrade/study-pack-v0.8.md`. For a fuller study and interview-review
version, see `docs/upgrade/learning-summary-v0.8.md`. For the final acceptance
audit, see `docs/upgrade/v0.8-completion-audit.md`.

### 1. 解决的问题

v0.7 已经把 `JobBackend` 抽象和 `dry_run/local/ssh/slurm/pbs` 后端枚举建好，
并且明确拒绝所有非 `dry_run` 后端。这解决了“不能误开真实后端”的问题，
但还没有解决“以后如果真的要接服务器，提交请求应该长什么样、怎么审计、
怎么隔离文件、怎么防止 prompt 变成 shell 命令”的问题。

v0.8 的目标不是提交作业，而是补上真实执行前必须有的安全骨架。一个研究
计算 agent 和普通聊天机器人不同：用户一句“帮我跑这个 FWI 实验”背后可能
牵涉 GPU、MPI、队列资源、文件系统、实验日志、产物归档和责任追溯。如果没
有结构化边界，系统很容易把自然语言直接变成命令，产生命令注入、路径穿越、
误提交、资源滥用和审计缺失。

所以 v0.8 先把“未来提交作业时必须经过哪些结构”定义出来：提交请求必须是
`JobSubmissionRequest`，执行状态必须落到 `JobRecord`，可执行形状必须来自
`ApprovedJobTemplate`，工作目录必须是受控 workspace 下的生成名字，状态变化
必须进入 lifecycle history。这样后续即使接 Slurm/PBS/SSH，也是在安全模型上
接，而不是在 prompt 后面拼一条命令。

### 2. 实现方式

数据流可以这样理解：

用户请求先进入 Planner。Planner 生成 `ExperimentSpec` 和 dry-run `JobSpec`。
这一步仍然是计划，不是执行。未来如果用户明确点击提交或 API 发起提交，服务
端应该构造 `JobSubmissionRequest`。这个结构里包含请求 ID、用户 ID、实验 ID、
后端类型、模板 ID、模板版本、实验 spec、job spec，以及 `dry_run` 标记。

验证顺序是安全设计的核心。第一层是 `validate_submission_boundary`，它复用
v0.7 的 `validate_backend_enabled`。因此即使请求写了 `slurm`，当前仍然会被
拒绝。第二层是 `validate_approved_template`，它要求请求中的 `template_id`
必须出现在批准模板列表里，且版本和后端类型匹配。第三层是
`validate_workspace_path`，它把 job 目录限定为一个生成的 leaf name，拒绝
`../outside`、斜杠和反斜杠。第四层是 lifecycle helper，把验证失败或状态变化
记入 `JobRecord`，方便审计。

这里没有选择“更简单”的方案，例如直接在 `JobSpec.command` 上做字符串过滤。
原因是命令字符串过滤很难覆盖 shell 元字符、重定向、环境变量、路径逃逸、
多命令串联等情况，而且无法表达资源上限、模板版本、用户身份和审计记录。也
没有选择“一步到位接 Slurm/PBS”的复杂方案，因为当前还没有真实 lab 账号、队
列策略、workspace 根目录、授权模型和运维审计要求。v0.8 的折中是先把安全
模型和测试固定住，让后续真实后端只能沿着这些结构扩展。

API 形状上，`JobSubmissionRequest` 是输入，`JobRecord` 是可追踪结果，
`ApprovedJobTemplate` 是执行白名单，`JobLifecycleState` 是状态机的最小词汇。
这些类型都在 `server_job.h` 中，避免把未来 server backend 逻辑散落到 Planner
或 UI 里。真正的提交、轮询、取消等操作还没有实现，也不应该塞进
`DryRunBackend::render`。渲染预览和提交作业是两个产品动作，必须保持 API
分离。

### 3. 关键文件、测试和资源

`research/include/agent_rpc/research/server_job.h` 是 v0.8 的核心头文件。它
定义 server job 安全模型：`JobSubmissionRequest`、`JobRecord`、
`ApprovedJobTemplate` 和 `JobLifecycleState`。面试时可以强调这是把“用户想跑
实验”转换成“受控、可验证、可审计的结构化请求”的第一步。

`research/src/server_job.cpp` 是纯验证和纯内存状态更新实现。它没有执行命令，
没有连接远程服务，也没有创建文件。`validate_submission_boundary` 复用现有
后端 guard，`validate_approved_template` 做模板匹配，`validate_workspace_path`
做 workspace 逃逸保护，`make_rejected_job_record` 和 `append_lifecycle_event`
维护 job record。

`tests/test_server_job.cpp` 是 v0.8 的主要测试。每个测试都保护一个具体风险：
默认 dry-run 防止遗漏字段导致可执行；生命周期解析保护 UI/API/审计日志的稳定
状态名；非 dry-run 拒绝保护 Slurm/PBS/SSH 不被提前打开；模板校验保护 raw
command 不直接进入执行层；workspace 测试保护路径穿越；rejected record 和
lifecycle event 测试保护未来审计链路。

`docs/upgrade/server-backend-safety-v0.8.md` 是安全设计文档，适合学习“真实执行
前应该先想清楚什么”。它覆盖威胁模型、非目标、API 草图、数据流、模板边界、
workspace 边界和实现门禁。

`docs/superpowers/plans/2026-06-22-server-backend-v0.8.md` 是执行计划。它展示了
如何把一个有风险的大功能拆成多个可 TDD 的小任务：先模型、再模板、再工作区、
再生命周期、最后报告。

### 4. 安全或产品边界

CUDA/MPI 边界：v0.8 没有调用 `mpirun`、没有启动 CUDA 程序、没有提交任何 GPU
作业。`JobSpec` 中可以描述资源，但描述不等于执行。当前系统只允许 dry-run。

SSH 边界：v0.8 没有 SSH client、没有 host、没有 key、没有远程命令。设计里把
SSH 归为后续高风险适配器，必须等授权、凭据、主机边界和审计策略明确后再做。

Slurm/PBS 边界：v0.8 没有 `sbatch`、`qsub`、轮询或取消逻辑。`JobBackendType`
里有 `slurm` 和 `pbs`，但它们仍被 `validate_backend_enabled` 拒绝。枚举存在
只是为了未来接口稳定，不是为了当前可用。

远程执行和 shell 执行边界：v0.8 不把用户输入变成 shell command。approved
template 的思想是让用户选择“批准过的执行形状”和结构化参数，而不是提交自由
文本命令。后续如果真的执行，也应该由 backend adapter 根据模板内部规则渲染，
而不是拼接用户字符串。

Code Agent 写权限边界：Code Agent 仍然默认只读。它可以解释代码、诊断错误、
提出 patch 建议，但不能自动应用未经确认的 patch。v0.8 没有改变这个权限模型。

### 5. 调试或 TDD 证据

这个版本的代码工作按 RED/GREEN 推进。第一步先写 `ServerJobTest`，构建失败在
缺少 `server_job.h`，说明测试确实引用了不存在的新 API。然后才创建头文件和
源文件。

第二步追加 approved template 测试，构建失败在 `ApprovedJobTemplate` 和
`validate_approved_template` 未定义。实现后目标测试和全量测试通过。

第三步追加 workspace 测试，构建失败在 `validate_workspace_path` 未声明。实现
后能拒绝 `../outside` 并接受生成式 job 目录名。

第四步追加 lifecycle helper 测试，构建失败在 `make_rejected_job_record` 和
`append_lifecycle_event` 未声明。实现后验证 rejected record 和 lifecycle event
都只是内存数据变化。

最终验证运行了 `cmake --build build -j2`、全量 `ctest` 和 `git diff --check`。
全量 CTest 结果是 26/26 通过，说明新增 `ServerJobTest` 已进入项目常规测试矩阵，
并且没有破坏已有 Code Agent、Experiment Planner、Research Knowledge、Lab Code
Adapter、Web branding、MCP 和 A2A 集成测试。

### 6. 面试怎么讲

项目短 pitch：

我做的是一个面向地震 FWI 研究计算的多智能体工作台。它不是简单聊天机器人，
而是把论文知识、算法卡片、实验规划、dry-run 作业预览、日志诊断和未来集群
执行边界串起来。v0.8 重点是给真实服务器执行做安全地基：先建提交模型、模板
白名单、工作区隔离、生命周期记录和审计边界，再讨论接 Slurm/PBS/SSH。

技术深挖版：

真实执行最危险的地方不是调用哪个 API，而是 LLM 可能把自然语言变成不可控命令。
我的设计把请求拆成 `ExperimentSpec`、`JobSpec` 和 `JobSubmissionRequest`，
并要求通过 `ApprovedJobTemplate`。后端类型仍由 `validate_backend_enabled`
控制，当前只有 `dry_run` 能通过。workspace 只接受生成的 leaf name，拒绝路径
穿越。所有状态变化进入 `JobRecord`，这样后续日志、产物和审计可以挂在同一个
记录上。这是把 agent 的“建议能力”和系统的“执行权限”隔离开。

常见追问和回答：

问：为什么不直接接 Slurm？

答：因为在没有 auth、workspace、模板白名单、审计和资源策略前接 Slurm，相当
于给自然语言一个集群提交入口。我的做法是先让非 `dry_run` 后端在运行时被拒绝，
同时建立将来提交需要的结构化模型。

问：approved template 和普通命令白名单有什么区别？

答：命令白名单通常只看 executable 名字，解决不了参数类型、资源上限、输入根
目录、模板版本、日志路径和产物策略。approved template 是一个版本化执行契约，
能把“允许做什么”和“怎样审计”写清楚。

问：workspace guard 为什么重要？

答：实验会产生配置、日志、模型文件和中间结果。如果 job directory 可以包含
`../` 或路径分隔符，就可能写到别人的实验目录或系统目录。v0.8 先限制为生成式
leaf name，后续再由服务器拼接到配置好的 workspace root 下。

问：这个版本有没有执行真实作业？

答：没有。v0.8 的价值正是没有急着执行，而是把真实执行前的安全前置条件建好，
并用测试证明非 `dry_run` 仍被拒绝。

STAR 复盘：

Situation：项目已经有 dry-run 实验规划和 JobBackend 接口，但还没有真实服务器
执行前的安全模型。

Task：在不接 CUDA/MPI、SSH、Slurm/PBS 的前提下，为未来 server backend 建立
结构化提交、模板边界、workspace 隔离、生命周期和审计基础。

Action：我先写安全设计和实施计划，再用 TDD 增加 `server_job` 模型、approved
template 校验、workspace guard 和 lifecycle helper。每一步先看见编译失败，
再实现最小代码，并运行目标测试、全量测试和 diff 检查。

Result：v0.8 完成后，系统仍然 dry-run only，但已经具备未来安全接入真实后端的
核心数据模型和验证边界。测试矩阵增加到 26 个 CTest，全部通过。
