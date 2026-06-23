# M11 实验室后端决策包

日期：2026-06-23

状态：模板已创建，实验室尚未批准。

这份文档是 M11-T1 完成前必须填写的评审表。它只记录实验室决策所需的信息，不代表
已经选择真实后端，也不会读取凭据、连接 SSH、提交 Slurm/PBS 作业、运行本地 wrapper、
创建 workspace，或修改运行时后端守卫。

配套流程指南：

- `docs/upgrade/m11-lab-process-guide.md`

## 决策状态

当前状态：

- 选定后端：未选择。
- 批准记录：未提供。
- 凭据策略：未提供。
- workspace 根目录：未提供。
- 授权策略：未提供。
- 审计保留策略：未提供。
- 配额或 operator 规则：未提供。
- operator 联系人：未提供。

在实验室补齐以上所有信息之前，M11-T1 仍然是未完成状态。

## 必须提供的批准记录

真实后端选择必须先在源码仓库之外完成审批，然后只把不含秘密的信息记录到项目
metadata 中。

必须提供：

- 选定后端：local wrapper、SSH、Slurm 或 PBS 四选一。
- 实验室批准人和批准记录引用。
- 凭据引用策略。仓库里只能保存引用名，不能保存密码、token、私钥或集群账号。
- workspace 根目录和目录命名规则。
- 允许提交任务的用户列表，以及访问控制策略。
- 已批准的 job templates 和允许填写的结构化参数。
- 资源配额、wall time、GPU/MPI 限制和取消规则。
- operator 联系人和故障升级路径。
- 审计保留策略和审计存储负责人。
- artifact 收集策略，包括日志、loss curve、模型文件和诊断文件。
- 出事故后停用后端的回滚计划。

## 后端候选对比表

| 候选后端 | 适用场景 | 主要风险 | 写代码前必须具备的控制 |
| --- | --- | --- | --- |
| Local wrapper | 适合第一版接入受控实验室机器，结构简单。 | shell 注入、workspace 越界、本地重负载任务误触发。 | 只允许 approved templates，不允许用户命令字符串；必须有用户授权、配额检查、workspace 生命周期、审计日志和取消测试。 |
| SSH | 适合已有固定远程实验室主机、但没有统一队列系统的场景。 | 凭据处理、网络失败、主机信任、远程清理不完整。 | 凭据引用策略、主机白名单、密钥轮换策略、workspace 隔离、超时/取消行为、审计记录和 operator runbook。 |
| Slurm | 适合已经使用 Slurm 管理共享 HPC 队列的实验室。 | 队列/账号策略、调度器错误、资源滥用、取消边界。 | account/partition 策略、sbatch 模板批准、sacct/squeue 状态映射、配额限制、artifact 收集和审计保留。 |
| PBS | 适合使用 PBS/Torque 系列集群的实验室。 | 队列语法差异、账号策略、状态解析、取消语义差异。 | qsub 模板批准、qstat/qdel 状态映射、队列/账号策略、配额限制、artifact 收集和审计保留。 |

这张表只用于评审。本文档不会启用任何候选后端。

## 实现前安全门槛

在 M11-T2 到 M11-T7 开始之前，选定的决策包必须通过这些检查：

- `BackendApprovalDecision` 可以填入具体、非占位的 metadata。
- `validate_backend_approval_decision` 能接受批准记录。
- `validate_submitter_authorization` 能接受授权测试用户，并拒绝未授权用户。
- approved job templates 有版本号，并且会拒绝未知 template ID。
- workspace 名称会拒绝路径穿越、路径分隔符、绝对路径和用户自定义根目录。
- audit events 可以覆盖 requested、rejected、lifecycle、artifact 和 operator-note
  事件。
- `BackendPreflightReport.metadata_ready` 可以为 true，同时 `runtime_enabled` 仍然
  保持 false。

在认证、workspace 生命周期、提交/状态/取消、artifact 收集、可视化和审计日志都有测试
之前，运行时守卫必须继续拒绝 `local`、`ssh`、`slurm` 和 `pbs`。

## 批准后的交接顺序

实验室提供完整决策包之后，继续按这个顺序推进：

1. M11-T2：实现身份认证和访问控制。
2. M11-T3：实现 workspace 创建和清理，所有目录都必须受 approved root 限制。
3. M11-T4：只为选定后端实现提交、状态轮询和取消。
4. M11-T5：实现日志收集和 artifact indexing。
5. M11-T6：实现 loss curve 和输出模型可视化。
6. M11-T7：实现审计持久化和 operator review。
7. 只有以上控制全部通过测试和评审后，才允许修改运行时后端守卫。

不要从调度器调用或 shell 执行开始。必须从授权、workspace、template 和 audit 测试开始。

## M11-T1 完成规则

只有满足以下条件，M11-T1 才能被勾选完成：

- 已经明确选定第一个真实后端。
- 批准记录是具体可追溯的。
- 凭据策略说明凭据存放在哪里，但不暴露凭据内容。
- workspace root 和清理策略是具体的。
- 授权策略和第一批 submitters 是具体的。
- 审计保留策略和 operator 联系人是具体的。
- 决策包已经由实验室负责人评审。

在这些条件满足之前，本仓库继续保持 dry-run 和非执行 review 模式。
