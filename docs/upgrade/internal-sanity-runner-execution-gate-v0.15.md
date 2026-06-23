# v0.15 Internal Sanity-Check Runner Execution Gate

日期：2026-06-23

## 目标

v0.15 的目标不是启用真实执行，而是在启用任何 limited execution 之前，把内部
sanity-check runner 的 gate 写清楚并用 metadata 测试锁住。

第一批实现只允许系统生成 review packet：

- 固定 allowlisted runner id。
- 固定 developer-maintained entrypoint label。
- timeout metadata。
- stdout/stderr capture plan。
- workspace root 下的 artifact path plan。
- metadata-only audit event plan。
- 显式非执行 flags。

## 必须满足的执行前条件

后续如果要让某个 sanity-check runner 真正执行，必须先满足这些条件，并且需要另一个
明确版本/任务来实现：

- 只有一个固定 runner id 被启用。
- runner id 映射到一个由开发者维护的固定 binary 或 script。
- 用户请求中没有自由 command string。
- runner 参数来自结构化字段或 allowlist，不来自用户拼接的 shell。
- workspace root 已经过字符串和真实 filesystem 边界检查。
- artifact path 必须在 workspace root 下。
- 不允许删除、移动 trash、清理代码目录、清理环境目录、清理共享数据或清理凭据。
- stdout 和 stderr 必须有 capture plan。
- timeout 必须有正数上限。
- 必须生成 audit event，记录 request id、user id、runner id、workspace plan id、
  artifact path 和非执行/执行状态。
- 凭据读取、SSH、Slurm、PBS、remote server access 必须继续由单独的后端批准流程控制。

## 当前实现边界

当前 `internal_sanity_runner` 只做 review metadata：

- `SanityRunnerDefinition` 描述 allowlisted runner。
- `SanityRunnerRequest` 描述用户选择的 runner id、workspace root 和 artifact path plan。
- `SanityRunnerReviewPacket` 输出 validation errors、capture plan、timeout、audit event
  type 和非执行 flags。
- `make_sanity_runner_review_packet` 只匹配 allowlisted definition 并做字符串级校验。
- `render_sanity_runner_review_packet` 只渲染 packet，不渲染用户自由 command 内容。

当前实现不会：

- fork/exec 进程。
- 调用 shell。
- 运行 CUDA/MPI、`mpirun` 或 `srun`。
- 连接 SSH、Slurm、PBS 或远程服务器。
- 读取密码、token、私钥、secret manager、`.env` 或凭据文件。
- 创建 workspace 或目录。
- 删除或移动任何文件。
- 采集真实 stdout/stderr、日志或 artifact。

## Review Packet 字段

review packet 必须展示：

- `runner_id`
- `fixed_entrypoint_label`
- `workspace_plan_id`
- `workspace_root_path`
- `timeout_seconds`
- `stdout_capture_planned`
- `stderr_capture_planned`
- `planned_artifact_paths`
- `audit_event_type`
- `validation_errors`
- `execution: disabled`
- `command_executed: false`
- `free_form_command_accepted: false`
- `deletion_executed: false`
- `credentials_loaded: false`
- `server_connected: false`
- `ssh_connected: false`
- `slurm_submitted: false`
- `pbs_submitted: false`
- `workspace_created: false`

这些字段的产品含义是：用户和 operator 可以审查将来固定 runner 需要哪些安全条件，
但不能把 packet 当作可执行脚本。

## 拒绝条件

v0.15 第一批测试锁住这些拒绝条件：

- unknown runner id。
- 缺失正数 timeout。
- 缺失 stdout capture plan。
- 缺失 stderr capture plan。
- 用户自由 command。
- 删除请求。
- 凭据读取请求。
- SSH 请求。
- Slurm 请求。
- PBS 请求。
- remote server access 请求。
- artifact path 逃出 workspace root。
- artifact path 包含 `..` traversal。

## 下一步

v0.15 完成后，下一步是 v1.0 internal preview closeout：把 v0.11 到 v0.15 的
metadata safety gates、review packet、fake lifecycle、workspace planner 和文档串成内部
演示流程。真实执行仍然需要后续单独批准和实现，不能在 closeout 文档中默认开启。
