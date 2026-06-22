# v0.8 Server Backend Safety Foundation Learning Summary

Date: 2026-06-22

Status: complete for the v0.8 safety-foundation scope.

This note is written for learning, interview review, and future upgrade
handoff. It explains what v0.8 changed, why the design is intentionally
conservative, how the code is structured, and how to talk about the work
clearly.

If you want a reading order, code-reading guide, test-risk map, interview
script, and self-check questions, start with
`docs/upgrade/study-pack-v0.8.md`.

## 1. 解决的问题

v0.7 完成后，项目已经有了 `JobBackend` 接口和后端类型枚举：
`dry_run`、`local`、`ssh`、`slurm`、`pbs`。最重要的是，v0.7 的运行时
guard 只允许 `dry_run`，会拒绝所有真实执行后端。这一步解决的是“不要误
开真实后端”的问题。

但是 v0.7 还没有解决另一个更大的问题：如果未来真的要让实验室成员提交
FWI、频率外推或其他研究计算作业，服务端应该怎样接住这个请求？一个真实
作业不是一句命令，它至少包含用户身份、实验配置、资源需求、模板版本、
工作目录、日志、产物、状态变化和审计记录。如果这些结构没有先设计好，
后续很容易把自然语言或 LLM 输出直接拼成 shell 命令。

v0.8 的目标就是在真实执行之前，把安全地基补出来。它不跑 CUDA，不跑 MPI，
不接 SSH，不接 Slurm/PBS，也不接本地 wrapper。它只建立一套可测试的安全
模型，让未来的真实后端必须沿着结构化请求、批准模板、受控 workspace 和
生命周期记录来扩展。

从产品角度看，v0.8 把“实验规划”和“提交执行”拆开。Planner 可以生成
`ExperimentSpec` 和 dry-run `JobSpec`，但这仍然只是计划。真正提交必须走
服务端 job 模型和验证链路。这个拆分让系统更可信：用户能看到计划，工程
师能审计计划，后端不会因为一句 prompt 就执行命令。

## 2. 实现方式

v0.8 的核心文件是 `server_job.h` 和 `server_job.cpp`。它们位于
`agent_rpc_research` 库中，和 `ExperimentSpec`、`JobSpec`、`JobBackend`
保持在同一个研究计算边界内。

主要数据结构包括：

- `JobSubmissionRequest`：未来服务端提交请求的输入结构，包含请求 ID、用户
  ID、实验 ID、后端类型、模板 ID、模板版本、`ExperimentSpec`、`JobSpec`
  和 `dry_run` 标记。
- `JobRecord`：未来作业记录，包含 job ID、生命周期状态、原始请求、workspace
  路径、验证消息、状态事件、日志路径和产物路径。
- `JobLifecycleState`：稳定的状态枚举，包括 `Draft`、`Rejected`、`Queued`、
  `Submitted`、`Running`、`Succeeded`、`Failed`、`Cancelled`。
- `ApprovedJobTemplate`：批准模板记录，包含模板 ID、版本、后端类型、允许的
  参数、允许的输入根目录、GPU 上限和 MPI rank 上限。

数据流可以这样理解：

1. 用户提出实验目标。
2. Experiment Planner 生成 `ExperimentSpec` 和 dry-run `JobSpec`。
3. UI 或 API 显示 dry-run 预览，并要求用户显式提交。
4. 未来服务端把提交动作转换成 `JobSubmissionRequest`。
5. 服务端先调用 `validate_submission_boundary`，确认当前运行时仍然只允许
   `dry_run`。
6. 再调用 `validate_approved_template`，确认请求引用的是批准模板，而不是
   用户自由输入的命令。
7. 再调用 `validate_workspace_path`，确认工作目录只是受控 root 下的生成式
   leaf name，不能包含 `..`、`/` 或 `\`。
8. 验证失败时用 `make_rejected_job_record` 生成 rejected 记录。
9. 状态变化通过 `append_lifecycle_event` 追加到内存记录中。

这里最关键的设计取舍是：不要把提交行为塞进 `DryRunBackend::render`。
`render` 是预览，`submit` 是真实副作用。二者在产品上、权限上、审计上都
不是一件事。如果混在一起，后续很容易出现“用户只是想看 dry-run，系统却
提交了作业”的事故。

为什么不用更简单的方案？例如直接检查 `JobSpec.command` 里有没有危险字符。
这种方案看起来轻量，但很脆弱。shell 元字符、环境变量、重定向、管道、命令
替换、路径逃逸、多命令串联都可能绕过简单过滤，而且过滤命令字符串也无法
表达用户身份、模板版本、资源上限、输入根目录和审计要求。

为什么不用更复杂的方案？例如现在就实现 Slurm/PBS/SSH。因为真实后端需要
实验室批准、凭据管理、workspace root、授权策略、审计保留策略和运维责任人。
这些外部条件没有确定时，接入真实后端会把项目从“安全 dry-run 平台”变成
“未经授权的执行入口”。v0.8 选择先建模型和测试，是更稳妥的工程步骤。

## 3. 关键文件、测试和资源

`research/include/agent_rpc/research/server_job.h`

这是 v0.8 的 API 边界。面试时可以说：我没有让 Planner 或 UI 直接控制执行，
而是新增了一个 server job 层，把未来提交请求变成显式数据模型。这个文件
定义了请求、记录、生命周期、批准模板和验证函数声明。

`research/src/server_job.cpp`

这是纯验证和纯内存状态更新实现。它没有调用 shell，没有连接远程机器，没有
创建目录，没有提交调度器作业。`validate_submission_boundary` 复用 v0.7 的
`validate_backend_enabled`，所以 `slurm`、`pbs`、`ssh`、`local` 继续被拒绝。
`validate_approved_template` 做模板 ID、版本和 backend 匹配。`validate_workspace_path`
拒绝路径穿越。生命周期 helper 只修改 `JobRecord` 内存状态。

`tests/test_server_job.cpp`

这是 v0.8 的核心保护网。每个测试都对应一个真实风险：

`SubmissionRequestDefaultsToDryRun` 保护默认安全姿态。新请求默认 `dry_run: true`
并使用 `JobBackendType::DryRun`，避免因为字段遗漏而生成可执行请求。

`ParsesLifecycleStateNames` 保护状态名稳定性。未来 UI、数据库和审计日志都可以
依赖 `draft`、`queued`、`running`、`failed` 等固定名字。

`RejectsNonDryRunSubmissionBeforeBackendsAreEnabled` 保护运行时 guard。即使提交
请求写了 `Slurm`，当前也必须继续失败。

`RequiresApprovedTemplateForSubmission` 保护模板白名单。未来提交不能靠 raw
command，必须引用批准模板。

`AcceptsMatchingDryRunTemplate` 保护正向元数据路径。已批准的 dry-run 模板可以
通过验证，说明 API 不是只会拒绝，也能表达安全的已知请求。

`RejectsWorkspaceTraversal` 保护工作区隔离。`../outside` 这种路径必须失败。

`AcceptsGeneratedWorkspaceName` 保护预期 happy path。生成式目录名
`job-20260622-0001` 可以通过。

`CreatesRejectedRecordFromValidationErrors` 保护审计链路。验证失败不是简单返回
错误字符串，而是可以转成 `JobRecord`，后续便于记录谁提交了什么、为什么被拒绝。

`AppendsLifecycleEventWithoutExecutingCommands` 保护生命周期更新边界。把状态改成
`Queued` 只追加内存事件，不代表提交到队列。

`docs/upgrade/server-backend-safety-v0.8.md`

这是设计文档，适合学习真实执行前应该先想什么。它覆盖威胁模型、非目标、
API 形状、数据流、模板边界、workspace 边界和实现门禁。

`docs/upgrade/test-report-v0.8.md`

这是测试报告，记录最终验证命令、测试覆盖、TDD 证据、安全边界和中文总结。

`docs/superpowers/plans/2026-06-22-server-backend-v0.8.md`

这是实施计划，展示如何把一个高风险能力拆成多个小任务：设计、模型、模板、
workspace、生命周期、报告。它对学习工程节奏很有价值。

## 4. 安全或产品边界

CUDA/MPI 边界：

v0.8 没有调用 CUDA 程序，也没有调用 `mpirun`。`JobSpec` 可以描述 MPI rank 和
GPU 数量，但描述资源不等于提交资源。当前系统只能做 dry-run 预览。

SSH 边界：

v0.8 没有 SSH client、host、key、远程命令或远程文件传输。即使枚举里有
`ssh`，运行时 guard 仍然拒绝它。SSH 后端需要额外的凭据、主机边界、用户
映射和审计策略。

Slurm/PBS 边界：

v0.8 没有 `sbatch`、`squeue`、`scancel`、`qsub`、`qstat`、`qdel`。`slurm` 和
`pbs` 只是未来接口的保留值，不是当前能力。后续如果接调度器，也应该通过
批准模板和结构化参数，不允许用户直接输入调度脚本。

远程执行和 shell 执行边界：

v0.8 不把用户文本、Planner 输出或 Code Agent 建议变成 shell 命令。模板边界
的设计目的就是阻断“自然语言 -> shell command”的危险直通路径。

workspace 边界：

作业目录必须是受控 workspace root 下的生成式 leaf name。用户不能控制 root，
也不能传入 `../` 或路径分隔符。这样可以降低读写其他实验目录、覆盖别的产物、
污染日志和泄露数据的风险。

Code Agent 写权限边界：

Code Agent 仍然只读。它可以解释代码、诊断错误、提出 patch 建议，但不能自动
应用未经确认的 patch。v0.8 没有改变这个权限模型。

M11 preflight 边界：

后续增加的 `BackendApprovalDecision` 属于 Milestone 11 preflight，不是 v0.8
真实后端接入。它只记录未来选择真实 backend 前必须具备的批准信息；即使审批
记录完整，`validate_backend_enabled(local)` 仍然失败。

## 5. 调试或 TDD 证据

v0.8 不是先写实现再补测试，而是按小步 TDD 推进。

第一步，先写 server job 模型测试。构建失败，因为
`agent_rpc/research/server_job.h` 不存在。这证明测试真的在驱动新 API。

第二步，实现最小 `JobSubmissionRequest`、`JobRecord`、`JobLifecycleState` 和
`validate_submission_boundary`。目标测试通过后再跑全量 CTest，确认没有破坏
已有模块。

第三步，追加 approved template 测试。RED 阶段失败在 `ApprovedJobTemplate`
和 `validate_approved_template` 不存在。GREEN 阶段只加模板匹配逻辑，不加执行。

第四步，追加 workspace guard 测试。RED 阶段失败在 `validate_workspace_path`
不存在。GREEN 阶段只做字符串级安全校验：root 必填、目录名必填、拒绝 `..`、
`/` 和 `\`。

第五步，追加 lifecycle helper 测试。RED 阶段失败在 `make_rejected_job_record`
和 `append_lifecycle_event` 不存在。GREEN 阶段只更新内存记录，不触碰外部系统。

最终验证包括：

```bash
cmake --build build -j2
ctest --test-dir build --output-on-failure
git diff --check
```

最终结果是构建通过、全量 CTest 26/26 通过、diff whitespace 检查无输出。

## 6. 面试怎么讲

### 项目短 pitch

这是一个面向地震 FWI 研究计算的多智能体工作台。它把多智能体通信、RAG、
算法卡片、实验规划、dry-run 作业预览、日志诊断和未来服务器执行边界组合
起来。v0.8 的重点不是跑作业，而是在接真实服务器前建立安全地基：结构化提交、
批准模板、workspace 隔离、生命周期记录和审计边界。

### 技术深挖版

我把真实执行拆成两层：规划层和提交层。规划层生成 `ExperimentSpec` 和
`JobSpec`，但只作为 dry-run。提交层必须构造 `JobSubmissionRequest`，引用
`ApprovedJobTemplate`，通过 backend guard、模板验证和 workspace guard，最后
才可能进入未来的 backend adapter。当前 `validate_backend_enabled` 仍然只允许
`dry_run`，所以 v0.8 不会误触发真实 CUDA/MPI、SSH 或 Slurm/PBS。

这个设计的关键是把 LLM 输出从执行权限中隔离出来。LLM 可以建议参数和实验
计划，但不能直接产生可执行命令。真正的执行形状由批准模板和服务端 adapter
控制。

### 常见追问和回答

问：为什么不直接把 `JobSpec.command` 发给 Slurm？

答：因为 `JobSpec.command` 是规划输出，不是安全执行契约。直接提交会让 prompt
或模型输出影响真实集群。安全做法是先选择批准模板，再传结构化参数，由后端
adapter 在受控规则下渲染。

问：为什么 v0.8 还不接真实服务器？

答：真实服务器需要实验室批准、凭据、workspace root、授权策略、资源限制和
审计保留策略。没有这些条件时接入执行能力是不负责任的。v0.8 的价值就是先把
安全边界和测试建好。

问：workspace path 为什么只允许 leaf name？

答：因为用户控制完整路径会带来路径穿越和数据污染风险。leaf name 加配置化
root 可以保证所有 job 目录都在受控范围内。

问：approved template 能解决什么？

答：它把“允许执行什么”从用户文本里拿出来，变成开发者或实验室维护的版本化
记录。模板可以约束 backend、参数、输入根目录和资源上限，也方便审计。

问：怎么证明没有误开真实执行？

答：测试里 `RejectsNonDryRunSubmissionBeforeBackendsAreEnabled` 会验证 `Slurm`
仍然被拒绝；后续 M11 preflight 还验证完整 `Local` 审批记录也不能绕过
`validate_backend_enabled`。

### STAR 复盘

Situation：项目已经能做 dry-run 实验规划，但真实作业执行涉及 GPU、MPI、
调度器、文件系统和审计，风险比普通聊天功能高很多。

Task：在不启用真实执行的前提下，为未来 server backend 建立安全模型和测试
边界。

Action：先写安全设计和实施计划，再按 TDD 增加 server job 模型、approved
template 校验、workspace guard、lifecycle helper 和测试报告。每一步先看到
编译失败，再实现最小代码，并跑目标测试和全量测试。

Result：v0.8 完成后，系统仍然 dry-run only，但已经具备未来真实后端接入前
必须有的结构化请求、模板白名单、workspace 隔离、生命周期记录和审计基础。
全量 CTest 26/26 通过，文档明确说明真实 CUDA/MPI、SSH、Slurm/PBS、远程执行
和任意 shell 执行仍未启用。

## 7. 学习时可以抓住的主线

学习 v0.8 不要只背类型名，要抓住这条主线：

```text
自然语言请求
  -> Planner 生成实验计划
  -> dry-run JobSpec 只做预览
  -> 未来提交必须变成 JobSubmissionRequest
  -> 请求必须引用 ApprovedJobTemplate
  -> workspace 必须受控
  -> 状态必须进入 JobRecord
  -> 当前 runtime guard 仍然只允许 dry_run
```

这条链路体现了一个重要工程思想：高风险能力不要直接从 UI 或 LLM 输出进入
副作用操作。先建数据模型、验证边界和审计链路，再考虑 adapter。
