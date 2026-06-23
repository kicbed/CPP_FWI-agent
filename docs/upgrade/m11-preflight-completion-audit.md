# M11 预检完成审计

日期：2026-06-22

本审计用于关闭“仅 metadata 的 M11 预检阶段”。它不关闭 M11-T1 的真实后端选择，因为
当时还没有实验室批准的后端、凭据策略、workspace root、授权策略、审计保留策略或
operator runbook。

## 已完成的预检项

后端批准决策门槛：

- `BackendApprovalDecision` 记录未来真实后端类型、实验室批准标记、批准人、批准记录、
  workspace root、凭据引用、授权策略、授权提交人、审计保留策略和 operator 联系人。
- `validate_backend_approval_decision` 会拒绝缺失 metadata 或占位 metadata。
- `local`、`ssh`、`slurm` 和 `pbs` 仍然是保留的运行时值，没有启用。

授权提交人门槛：

- `authorized_submitters` 必须包含具体用户 ID。
- `validate_submitter_authorization` 会拒绝未出现在批准决策中的请求用户。

审计 metadata 门槛：

- `JobAuditEvent` 记录 job、request、user、事件类型、消息、时间戳和后端类型。
- `JobAuditLog` 在内存里把同一个 job 的审计事件分组。
- 审计验证会拒绝空日志、无效事件和跨 job 混合事件。

统一就绪报告：

- `BackendPreflightPackage` 组合提交请求、批准决策、approved templates、workspace
  目录名和审计日志。
- `BackendPreflightReport` 把 metadata 就绪和运行时启用状态分开。
- `evaluate_backend_preflight` 聚合所有预检检查，并返回共享后端守卫产生的运行时阻塞原因。

## 验收证据

聚焦的 `ServerJobTest` 测试套件覆盖：

- 不完整预检包会被拒绝。
- 完整 metadata 包可以被识别。
- 保留真实后端类型时，运行时阻塞原因仍然保留。
- 批准字段中的占位值会被拒绝。
- 授权提交人验证。
- 审计事件验证。
- 审计日志验证和 append 行为。
- workspace 路径穿越拒绝。
- 被拒绝 job 的生命周期 metadata。

最终验证命令：

```bash
cmake --build build -j2
ctest --test-dir build --output-on-failure
git diff --check
```

结果：

- PASS。构建退出码为 0。
- PASS。全量测试通过 26/26。
- PASS。diff 空白检查没有输出。

## v0.9 进入决策

本审计完成后，项目可以进入 v0.9，但前提是 v0.9 只做“非执行的后端就绪与评审”。
也就是说，v0.9 可以围绕预检报告、dry-run 提交包评审、operator checklist 和审计预览
构建 UI 或 planner 展示能力。

项目不能进入真实后端实现阶段，直到 M11-T1 被实验室决策包解除阻塞。该决策包必须
包括选定后端、批准记录、凭据处理策略、workspace root、授权策略、审计保留策略、
配额或 operator 规则，以及明确的 operator 联系人。

## 最终边界

M11 预检在 metadata 就绪层面已经完成。运行时执行仍然只允许 dry-run。
