# v0.9 Backend Readiness Review Learning Summary

## 1. 解决的问题

v0.8 和 M11 preflight 已经把未来真实后端上线前需要的安全 metadata 建起来了：
审批记录、授权 submitter、approved template、workspace root、audit event、audit
log，以及统一的 `BackendPreflightReport`。但这些能力仍然偏内部模型，适合测试和
代码调用，不适合操作员、导师或后续 Web UI 直接 review。

v0.9 解决的是“从结构化安全模型到可审查产品输出”的问题。它不接 Slurm、PBS、
SSH，也不运行本地 wrapper，而是把已有 metadata 渲染成稳定文本：哪些审批信息
已完整，runtime 是否仍被关闭，未来提交包长什么样，审计事件会如何呈现，
workspace 和 artifact 路径会落在哪里。这样做的价值是先让团队能 review 后端上线
计划，而不是一边写真实执行适配器一边补安全边界。

上一版不足在于：`BackendPreflightReport` 只能告诉代码“metadata_ready” 和
“runtime_enabled”，但没有面向 operator 的 packet、audit、workspace/artifact
预览。v0.9 补上这层，让后续产品界面或审批流程可以复用同一组确定性输出。

## 2. 实现方式

核心数据流是：

`BackendPreflightPackage` -> `evaluate_backend_preflight` ->
`BackendPreflightReport` -> v0.9 render helpers。

这次新增的 API 都在 `server_job` 边界内：

- `render_backend_preflight_report` 输出 readiness 状态、validation errors、
  runtime blockers 和 safety boundaries。
- `render_dry_run_submission_packet` 输出 request、user、experiment、template、
  backend、resource 和 command preview 信息，并明确 `execution: disabled`。
- `render_job_audit_log_preview` 输出 audit log 的 job id、event count、event type、
  message、timestamp 和 backend，但不写入任何生产审计存储。
- `render_workspace_artifact_plan` 输出 workspace root、job directory、计划路径、
  artifact paths 和 expected outputs，并明确 `directories_created: false`。

设计取舍上没有新增复杂 DTO 或 JSON schema，因为 v0.9 的目标是完成非执行型
review layer，不是公开稳定外部 API。直接渲染文本足够小、可测，也能被后续 Web UI
或 CLI 包装。更复杂的方案例如审计持久化、模板引擎、REST endpoint、权限系统，
都会把 v0.9 推向真实后端实现；更简单的方案只写文档则无法保护输出格式和安全
边界，所以这次选择“C++ 纯函数 + GoogleTest”的中间方案。

## 3. 关键文件、测试、资源

`research/include/agent_rpc/research/server_job.h` 是 v0.9 API 的公开入口。它仍然
围绕 `JobSubmissionRequest`、`BackendApprovalDecision`、`JobAuditLog` 和
`BackendPreflightPackage` 工作，没有引入执行器或远程客户端。

`research/src/server_job.cpp` 实现文本渲染。实现中只做字符串格式化、列表输出和
workspace preview path 拼接，不调用文件系统，不创建目录，不调用 shell，不读取凭据。

`tests/test_server_job.cpp` 是主要保护网：

- readiness report 测试保护 metadata/runtime 分离和 runtime blocker 可见性。
- dry-run submission packet 测试保护 request、user、experiment、backend、template
  和 command preview 会出现在 operator review 输出中。
- audit log preview 测试保护 audit event 能被 review，但 `persistence: disabled`
  明确说明还没有生产写入。
- workspace/artifact plan 测试保护 planned workspace path、artifact paths、
  expected outputs 可见，同时 `directories_created: false` 防止误解为已创建目录。

`docs/upgrade/test-report-v0.9.md` 记录 RED/GREEN 和最终验证结果。
`docs/upgrade/version-roadmap.md`、`milestones.md`、`README.md` 记录 v0.9 完成和
v1.0 进入门槛。

## 4. 安全或产品边界

v0.9 没有接真实 CUDA/MPI。即使 packet 中出现 `command_preview`，它也只是字符串
展示，不会被传给 shell，也不会被提交到本地或远程执行器。

v0.9 没有接 SSH、Slurm、PBS、本地 wrapper 或远程服务器。`approval_backend` 可以
显示未来审批选择的 `local`，但这不代表 runtime 已启用；共享 backend guard 仍然
拒绝 `local`、`ssh`、`slurm`、`pbs`。

v0.9 没有 credential loading。`credential_reference` 仍然只是审批 metadata，
不会读取 vault、环境变量或集群账号。

v0.9 没有生产 audit store。audit preview 展示未来审计记录的形状，但不写数据库、
文件、Redis 或远端服务。

v0.9 没有自动 Code Agent 写权限。Code Agent 仍默认只读，允许生成 patch 建议，
但不会自动应用未确认 patch。

## 5. 调试或 TDD 证据

第一轮 RED：先为 `render_backend_preflight_report` 写测试，构建失败，错误是函数
未声明。这证明测试确实覆盖了新增能力缺口，而不是复用已有行为。

第二轮 RED：继续为 dry-run submission packet、audit log preview、workspace/artifact
plan 写测试，构建失败，错误分别是三个 preview API 未声明。随后才添加声明和最小
实现。

GREEN：`ServerJobTest` 通过后，再运行完整 `cmake --build build -j2` 和全量
`ctest --test-dir build --output-on-failure`。最终结果证明 v0.9 preview 层没有破坏
已有 RPC、A2A、Planner、Lab Code Adapter、MCP 和 server-job safety 测试。

## 6. 面试怎么讲

项目短 pitch：

这个项目不是简单聊天机器人，而是一个面向地震 FWI 研究计算的多智能体 workbench。
它能做算法知识检索、实验规划、dry-run 作业预览、日志诊断和后端上线前 readiness
review。真实 CUDA/MPI 和集群执行被刻意放在安全边界后面，只有审批、授权、workspace、
审计和运行时 guard 都准备好以后才会启用。

技术深挖版：

v0.9 的关键是把“metadata ready”和“runtime enabled”分开。`BackendPreflightPackage`
聚合审批、请求、模板、workspace 和 audit log；`evaluate_backend_preflight` 负责
校验；render helpers 再把校验结果和 preview packet 输出给操作员。即使 metadata
完整，runtime guard 仍然会拒绝 `local`、`ssh`、`slurm`、`pbs`，所以系统不会因为
审批数据完整就自动执行真实作业。

常见追问和回答：

问：为什么不直接接 Slurm？
答：因为接 Slurm 前必须先有实验室审批、凭据策略、workspace root、授权策略、审计
保留策略、quota/operator 规则和失败处理测试。v0.9 先做 review 输出，降低真实
后端上线时的安全风险。

问：`command_preview` 会不会导致命令注入？
答：不会。它只是文本预览，不被执行，也不传给 shell。未来真实执行也必须走
approved template 和结构化参数，而不是用户自由文本拼 shell。

问：为什么用文本渲染而不是 JSON？
答：当前目标是 operator review 和测试安全语义，文本输出足够稳定、低成本。后续
如果要接 Web UI 或 API，可以继续复用同一组结构体，再增加 JSON adapter。

STAR 复盘：

Situation：项目准备从 dry-run 走向未来真实后端，但直接实现执行器风险高。
Task：需要先完成一个非执行型 readiness review 阶段，让审批、提交包、审计和路径
计划可被 review。
Action：用 TDD 增加 readiness report、submission packet、audit preview、
workspace/artifact plan 四类输出，并保持 runtime guard 不变。
Result：v0.9 完成，测试通过，项目能清楚说明“现在可以 review 后端上线包，但还不能
执行真实 CUDA/MPI 或集群任务”。
