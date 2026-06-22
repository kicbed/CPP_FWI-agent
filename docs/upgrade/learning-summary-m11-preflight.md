# M11 Preflight Learning Summary

## 1. 解决的问题

v0.8 已经建立了 server backend safety foundation：有提交请求模型、审批模板、
workspace 路径检查、生命周期记录和审计事件雏形。但 v0.8 之后还有一个明显缺口：
这些安全模型是分散的，后续如果有人开始接真实后端，很容易只看其中一两个检查，
误以为“审批字段存在”就等于“可以提交任务”。

M11 preflight 解决的是这个过渡阶段的问题。它不是接 Slurm、PBS、SSH 或本地
wrapper，而是在真实执行前先回答：一个后端审批包需要哪些元数据？谁可以提交？
模板是否受控？workspace 名称是否安全？审计事件是否能归属到同一个 job？这些
信息完整以后，runtime 是否仍然保持关闭？这一步让项目可以向 v0.9 的产品化
review 流程前进，同时不越过真实执行的安全边界。

## 2. 实现方式

核心数据流是从单点验证变成统一 preflight report。

`BackendApprovalDecision` 描述未来真实后端选择所需的审批元数据。它要求 lab
approval、审批人、审批引用、workspace root、credential reference、授权策略、
authorized submitters、audit retention policy 和 operator contact。验证函数会拒绝
`TBD`、`pending`、`unknown`、`n/a`、`none` 这类占位值，避免“字段填了但其实没
审批”的情况。

`JobSubmissionRequest` 仍然必须走 `validate_submission_boundary`。这一步复用
`validate_backend_enabled`，所以当前 runtime 只接受 `dry_run`。即使 approval
decision 里已经写了 `local`，它也只是一个审批候选，不会让 runtime 自动执行。

`JobAuditEvent` 和 `JobAuditLog` 负责未来审计持久化之前的元数据边界。单条事件
要有 job、request、user、message、timestamp 和 backend type；日志批次还要保证
所有事件属于同一个 job。`append_job_audit_event` 先验证候选状态，再修改原日志，
避免错误事件污染审计流。

最后的 `BackendPreflightPackage` 把 request、approval、approved templates、
workspace directory name 和 audit log 放在一起。`evaluate_backend_preflight`
输出 `BackendPreflightReport`，其中 `metadata_ready` 表示这些元数据检查是否通过，
`runtime_enabled` 表示共享 runtime guard 是否允许后端执行。这里的设计取舍是：
不要把二者合成一个布尔值。因为当前最重要的状态正是“metadata 可以准备好，但
runtime 仍然关闭”。这比简单返回 true/false 更适合安全审查，也比直接引入状态机、
数据库或调度器适配器更轻量。

## 3. 关键文件、测试和资源

`research/include/agent_rpc/research/server_job.h` 定义 M11 preflight 的公开模型：
审批决策、审计事件、审计日志、preflight package 和 preflight report。

`research/src/server_job.cpp` 实现所有验证逻辑。它没有进程创建、文件写入、网络
连接或远程调用；所有函数都是纯元数据检查或内存结构更新。

`tests/test_server_job.cpp` 是主要保护网。`ReportsIncompleteBackendPreflightPackage`
保护缺少审批、授权、workspace、审计日志时不能通过；`CompletePreflightPackageDoesNotEnableRuntimeBackend`
保护一个完整的审批元数据包不能绕过 runtime guard。审计日志测试保护空日志、跨
job 事件和无效事件不会进入未来持久化边界。

`docs/upgrade/test-report-m11-preflight.md` 记录 RED/GREEN 和最终验证命令。
`docs/upgrade/m11-preflight-completion-audit.md` 记录为什么 preflight 可以收口、
以及 v0.9 的进入条件。

## 4. 安全和产品边界

本阶段没有接真实 CUDA/MPI。项目仍然不能提交 FWI 作业到 GPU、MPI、实验室服务器
或集群队列。

本阶段没有 SSH、Slurm、PBS、本地 wrapper、远程服务器、凭据读取或 scheduler
API。`local`、`ssh`、`slurm`、`pbs` 仍然会被 `validate_backend_enabled` 拒绝。

本阶段没有执行来自用户输入的 shell 命令。用户文本只能影响元数据模型和 dry-run
计划，不能变成命令行。

Code Agent 仍然默认只读。它可以解释代码、诊断错误、提出 patch 建议，但不能自动
应用未确认的 patch。

产品上，M11 preflight 的价值是让后端上线前的审批、授权、审计和 workspace 要求
变成可测试模型。它不是“已经能跑任务”，而是“知道跑任务前必须满足什么，而且代码
会证明现在还不能跑”。

## 5. 调试和 TDD 证据

本轮先写了 `BackendPreflightPackage` 和 `evaluate_backend_preflight` 的测试。
第一次运行 `cmake --build build -j2 && ctest --test-dir build -R ServerJobTest --output-on-failure`
时，构建因为这些类型和函数不存在而失败。这是预期 RED，证明测试确实覆盖新增
行为，而不是验证已有代码。

随后只实现最小模型和聚合函数。targeted `ServerJobTest` 通过后，再运行完整
`cmake --build build -j2` 和完整 `ctest --test-dir build --output-on-failure`。
最终 26/26 测试通过，说明新增 preflight report 没有破坏 v0.2 到 v0.8 的已有
能力，也没有打开真实后端。

## 6. 面试怎么讲

短 pitch：

我在一个 FWI-first 多智能体科研平台里，为未来真实服务器后端设计了 metadata-only
preflight 层。它把审批、授权、模板、workspace、审计日志和 runtime guard 串成
一个 readiness report，让系统可以证明“安全材料准备好了”和“真实执行仍然关闭”
这两件事可以同时成立。

技术深挖版：

我没有直接接 Slurm 或 SSH，而是先建了 `BackendApprovalDecision`、
`JobAuditEvent`、`JobAuditLog` 和 `BackendPreflightReport`。关键设计是把
`metadata_ready` 和 `runtime_enabled` 分开。metadata readiness 聚合审批、授权、
模板、workspace 和 audit 检查；runtime enablement 仍然只看共享 backend guard。
因此完整审批包不会自动启用执行。这个边界对科研平台很重要，因为真实集群作业
涉及 GPU/MPI 资源、用户权限、审计追踪和实验可复现性。

常见追问和回答：

问：为什么不直接接 Slurm？
答：因为还没有真实 lab approval、credential policy、workspace root、授权策略、
audit retention 和 operator runbook。先做可测试的 preflight，可以避免把用户文本
或不完整审批包变成真实任务。

问：为什么需要 audit log，而不是只记录最终状态？
答：科研任务出错时要复盘谁请求了任务、为什么被拒绝、状态如何变化、产物如何
索引。只记录最终状态不够审计，也不利于复现实验。

问：为什么 report 里有两个布尔值？
答：`metadata_ready` 回答材料是否齐全，`runtime_enabled` 回答执行是否允许。
当前正确状态是前者可以为 true，后者仍然为 false。

STAR 复盘：

Situation：项目准备从 dry-run 实验规划走向真实后端，但真实执行有高风险。
Task：在不接真实服务器的前提下，把审批、授权、审计和 workspace 前置条件做成
可测试边界。Action：用 TDD 先写 preflight report 测试，再实现聚合模型，并更新
测试报告、完成审计和学习总结。Result：完整测试通过，M11 preflight 可以收口，
但 runtime 仍然只允许 dry-run，为 v0.9 的非执行型 readiness workbench 打下基础。
