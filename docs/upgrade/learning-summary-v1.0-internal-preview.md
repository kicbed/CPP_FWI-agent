# v1.0 Internal Preview Learning Summary

日期：2026-06-23

## 1. 这次解决什么问题

v0.10 到 v0.15 已经把“一个服务器账号、实验室内部先试用”的路线拆成多个
metadata gate：profile/template、safe operations、fake lifecycle、workspace
planner、approved-template run packet、sanity runner gate。问题是这些能力虽然已经
分别完成，但还缺少一个可以交给用户、operator 和未来面试讲述的完整收口：

- 哪些 gate 已经满足。
- 用户如何理解 review packet。
- operator 如何审核模板和危险操作。
- 演示时按什么顺序讲。
- 测试证据如何支撑 v1.0 internal preview 的版本标记。

本次 v1.0 internal preview closeout 解决的是 release readiness，而不是新增执行
能力。它把前面五个 gate 串成一个可审计、可学习、可演示的内部预览版本。

## 2. 为什么不能直接做真实执行

真实执行的风险不是“能不能调用命令”，而是“谁能调用、调用什么、在哪里运行、
如何收集结果、如何审计、出错怎么停”。如果在没有这些边界时直接接 SSH、Slurm、
PBS 或 local wrapper，用户的一句话就可能变成：

- 任意 shell command。
- 凭据泄漏。
- 删除代码、环境或共享数据。
- 作业跑在错误 workspace。
- 日志和 artifact 无法追踪。
- 出错后没有审计证据。

所以 v1.0 internal preview 的设计选择是：先让实验室成员看懂将来要做什么，同时
系统明确展示不会做什么。review packet、fake lifecycle 和 workspace preview 是
训练用户和 operator 的界面，也是未来真实 backend 的合同雏形。

## 3. 实现方法

这次主要新增文档：

- `docs/upgrade/v1.0-internal-preview-audit.md`
- `docs/upgrade/v1.0-internal-user-guide.md`
- `docs/upgrade/v1.0-internal-operator-runbook.md`
- `docs/upgrade/demo-script-v1.0-internal-preview.md`
- `docs/upgrade/test-report-v1.0-internal-preview.md`
- `docs/upgrade/learning-summary-v1.0-internal-preview.md`

同时更新升级指南、里程碑、路线图、career notes 和 upgrade log，把 v0.15 的
M11-S6 状态补齐，并在所有前置 gate 满足后把 v1.0 internal preview 标记为完成。

核心收敛方式是 evidence map：

- 每个 gate 对应一个测试报告。
- 每个 gate 对应一个 CTest target。
- 每个 gate 都列出明确禁止的副作用。
- v1.0 audit 只在全量 CTest 通过后给出 PASS 结论。

## 4. 数据流怎么讲

内部预览工作流可以这样讲：

```text
用户选择 approved template
  -> 填结构化参数
  -> 生成 dry-run review packet
  -> operator 审核 packet
  -> fake lifecycle 展示状态
  -> workspace planner 展示路径预览
  -> approved-template run packet 合成 future-run review
  -> sanity runner gate 检查未来 fixed runner 的最小边界
```

这条链路里没有真实执行。最重要的产品价值是把“将来可能执行的事情”提前拆成可审查
metadata，而不是让用户自由写命令。

## 5. 关键边界

v1.0 internal preview 保持这些边界：

- 不执行 CUDA/MPI。
- 不运行用户自由 shell。
- 不读取密码、token、私钥或 `.env`。
- 不连接 SSH、Slurm、PBS 或远程服务器。
- 不创建 workspace。
- 不删除文件，不移动 trash。
- 不采集真实 stdout/stderr、日志或 artifact。
- 不持久化生产审计日志。
- 不让 Code Agent 自动应用 patch。

这些边界不是口头承诺，而是在 v0.11-v0.15 的 test reports 和 CTest targets 中有
断言保护。

## 6. 测试保护什么

- `SingleServerBackendTest`：保护 profile/template/review request 的 dry-run 边界，
  拒绝 runtime enabled、inline secret-like credential、未知参数和非 dry-run。
- `SafeOperationsTest`：保护角色 allowlist 和删除 preview，证明 `lab_root` 也不能
  绕过真实删除、路径保护、symlink 和确认短语。
- `SingleServerLifecycleTest`：保护 fake lifecycle 状态机，证明状态只在内存中变化。
- `WorkspacePlannerTest`：保护路径 preview，拒绝 traversal、绝对逃逸、危险 root 和
  protected labels。
- `ApprovedTemplateRunPacketTest`：保护 approved template run packet，拒绝自由命令、
  未批准参数、缺失必填参数和 profile/template mismatch。
- `InternalSanityRunnerTest`：保护 future fixed runner gate，拒绝 unknown runner、
  自由命令、删除、凭据读取、SSH、Slurm、PBS、remote access、artifact path 逃逸和
  缺失 timeout/capture plan。

全量 `ctest` 还覆盖更早的通信、路由、MCP、RAG、planner、lab code adapter 和 Web UI
基础，证明 v1.0 文档收口没有破坏已有项目状态。

## 7. 面试讲法

项目一句话：

> 我把一个 FWI 多智能体 demo 收敛成了实验室内部 research computing workbench。
> 在接真实服务器前，我先实现了 approved template、workspace、lifecycle、audit
> metadata 和 review packet gate，让用户能预览实验流程，同时系统不会执行任意命令、
> 读取凭据或删除文件。

技术深挖：

- 我没有直接写 SSH/Slurm adapter，而是先定义 `JobBackend` 和 single-server metadata。
- 用户输入被限制为 template id 和结构化参数，不能成为 shell command。
- 删除操作被设计成 dry-run review packet，并且 root 角色也不能绕过安全检查。
- lifecycle 是 fake/in-memory，主要用于产品演示和状态流审查。
- workspace planner 只做字符串级路径 preview，先捕获 traversal 和 protected path 风险。
- sanity runner gate 只定义未来 fixed runner 的最小合规条件，不运行 runner。

可能追问：

Q: 这是不是过度设计？

A: 对公网产品可能还不够，对实验室内部预览刚好。因为真实计算任务会接触服务器账号、
数据集、模型、日志和结果目录。先把 command、credential、workspace、delete 和 audit
边界拆开，可以避免后续为了赶 demo 把用户文本直接接到 shell。

Q: 为什么 fake lifecycle 有 `queued` 和 `running`？

A: 它们是 UI/metadata 状态，不是调度器状态。这样用户和 operator 可以先练习审核流程，
同时 preview 里明确写着 `server_connected: false` 和 `command_executed: false`。

Q: v1.0 internal preview 和真正 v1.0 平台差在哪？

A: internal preview 是 review-only workflow。真正平台还需要 M11-T1 到 M11-T7：
后端批准、认证授权、workspace 生命周期、提交和取消、日志/artifact 收集、可视化和
生产审计。

## 8. STAR 复盘

Situation:

实验室希望最终能让 agent 帮忙准备和运行 FWI 实验，但当前还没有安全的真实 backend。

Task:

把单服务器内部预览路线收口成可以交给用户和 operator 试用、学习、演示的版本，同时
不能新增危险能力。

Action:

我审计 v0.11-v0.15 的 metadata gate 和测试报告，补齐 v0.15 milestone 状态，新增
audit、user guide、operator runbook、demo script、test report 和 learning summary，
并更新 README、milestones、version-roadmap、career-notes 和 upgrade-log。最后运行
构建、全量 CTest 和 diff whitespace 检查。

Result:

v1.0 internal preview 被标记为完成。用户可以按 approved template -> review packet
-> fake lifecycle -> workspace/artifact preview -> sanity gate 的路线理解系统；operator
有明确的 stop-before-real-execution runbook；真实 CUDA/MPI、SSH、Slurm/PBS、凭据、
删除和 shell 执行仍然关闭。
