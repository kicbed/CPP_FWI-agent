# 单服务器账号接入准备设计 v0.10

日期：2026-06-23

状态：设计和实现计划已创建，代码尚未实现。

本文定义 M11-S1 单服务器账号受控运行准备阶段。它服务于当前实验室的现实场景：
自己或小组先用一个服务器账号跑实验，不一开始建设复杂多用户平台、Slurm/PBS、
SSH 真实连接或生产审计系统。

本文只设计 metadata、profile、template 和 dry-run review packet。它不执行命令、
不读取真实凭据、不连接服务器、不创建 workspace、不提交 CUDA/MPI 作业，也不改变
运行时后端守卫。

## 1. 目标

v0.9 已经能把 M11 preflight metadata 渲染成 operator 可以 review 的文本，但它仍然
面向通用真实后端决策：local wrapper、SSH、Slurm、PBS 都只是候选。当前实验室更小：
先按一个固定服务器账号、固定 workspace、固定 approved template 的路径推进。

v0.10 的目标是把这个最小路径建成可测试的非执行层：

- 用 `SingleServerProfile` 表达一个服务器账号配置的 metadata。
- 用 `SingleServerJobTemplate` 表达一个 approved template 的 metadata。
- 用结构化参数表达一次将来可能运行的实验请求。
- 只生成 dry-run review packet，给自己、同组同学或 operator 看。
- 继续保持所有真实执行关闭。

这一步完成后，下一步才考虑 fake lifecycle；fake lifecycle 也必须仍然不连接服务器。

## 2. 非目标

本阶段明确不做：

- 真实 CUDA/MPI、`mpirun`、`srun` 或 GPU 作业执行。
- SSH、Slurm、PBS、本地 wrapper 或远程服务器连接。
- 读取密码、token、私钥、集群账号或 secret manager。
- 在仓库中保存真实服务器账号、真实 workspace root、真实凭据或私钥路径。
- 从用户输入执行任意 shell 命令。
- 创建、删除或清理本地/远程 workspace。
- 写入生产审计系统。
- 让 Code Agent 自动应用未确认 patch。

`command_preview` 或 `entrypoint_label` 只能作为 review 文本存在，不能作为可执行命令
传给 shell。

## 3. 与现有 M11/v0.9 的关系

现有能力：

- `BackendApprovalDecision` 表达未来真实后端审批 metadata。
- `BackendPreflightPackage` 聚合请求、审批、模板、workspace 和审计预览。
- `render_dry_run_submission_packet` 能渲染通用 dry-run 提交包。
- 运行时 `validate_backend_enabled` 仍然拒绝 `local`、`ssh`、`slurm` 和 `pbs`。

v0.10 不替换这些能力，而是在它们旁边增加一个更窄的单服务器账号准备层。

设计关系：

```text
ExperimentSpec / JobSpec
        |
        v
SingleServerProfile metadata
        |
        v
SingleServerJobTemplate metadata
        |
        v
SingleServerReviewRequest
        |
        v
Single Server Dry-Run Review Packet
        |
        v
No execution, no credential loading, no server connection
```

通用 M11 preflight 继续回答“实验室是否批准真实后端”。单服务器层回答“如果以后
先用一个服务器账号，profile/template/review packet 应该长什么样”。

## 4. 核心 metadata

### 4.1 SingleServerProfile

`SingleServerProfile` 只保存非秘密 metadata。

建议字段：

- `profile_id`：稳定 ID，例如 `single-server-dev`。
- `display_name`：给 review packet 展示的名称。
- `account_reference`：账号引用名，不是用户名密码，例如 `lab-single-server-account`。
- `credential_reference`：凭据引用名，不是凭据内容，例如 `secret-ref:single-server-runner`。
- `workspace_root_reference`：workspace 根目录引用名，不是真实绝对路径。
- `allowed_template_ids`：这个 profile 允许使用的 approved template ID 列表。
- `runtime_enabled`：本阶段必须为 `false`。

校验规则：

- `profile_id`、`account_reference`、`credential_reference`、
  `workspace_root_reference` 均不能为空。
- `credential_reference` 不能看起来像内联秘密，例如包含 `password=`、`token=`、
  `-----BEGIN` 或 `PRIVATE KEY`。
- `allowed_template_ids` 至少包含一个模板 ID。
- `runtime_enabled == true` 必须被拒绝，因为本阶段仍是非执行准备。

### 4.2 SingleServerJobTemplate

`SingleServerJobTemplate` 表达一个已经批准的模板形状，但不包含可自由执行的 shell。

建议字段：

- `template_id`：稳定 ID，例如 `fwi_multiscale_review`。
- `version`：模板版本，例如 `1`。
- `profile_id`：允许使用该模板的 profile。
- `entrypoint_label`：固定入口的标签，例如 `fwi_multiscale_sanity_check`。
- `allowed_parameter_names`：允许出现在 review request 中的结构化参数名。
- `expected_artifacts`：预期 artifact 名称，例如 `loss_curve`、`final_velocity_model`。
- `max_gpus`、`max_mpi_ranks`、`max_wall_time_minutes`：资源上限 metadata。

校验规则：

- 模板必须绑定一个 `profile_id`。
- 模板 ID、版本和固定入口标签不能为空。
- `allowed_parameter_names` 不能为空。
- `max_gpus` 不能为负数，`max_mpi_ranks` 和 `max_wall_time_minutes` 必须大于 0。
- 模板只能被匹配的 profile 使用，并且 template ID 必须在 profile 的
  `allowed_template_ids` 中。

### 4.3 SingleServerReviewRequest

`SingleServerReviewRequest` 表达一次将来可能提交的实验请求，但只能用于 dry-run review。

建议字段：

- `request_id`
- `user_id`
- `profile_id`
- `template_id`
- `template_version`
- `parameters`：结构化键值对。
- `dry_run`：必须为 `true`。

校验规则：

- `dry_run == false` 必须被拒绝。
- profile 和 template 必须匹配。
- template version 必须匹配。
- 参数名必须都在 `allowed_parameter_names` 中。
- review request 不接受任意 `command` 字段。

## 5. Dry-Run Review Packet

review packet 是本阶段唯一输出。它给人看，不给执行器用。

内容应包括：

- `request_id`
- `user_id`
- `profile_id`
- `profile_display_name`
- `account_reference`
- `workspace_root_reference`
- `template_id`
- `template_version`
- `entrypoint_label`
- `parameters`
- `expected_artifacts`
- `resource_limits`
- `execution: disabled`
- `credentials_loaded: false`
- `server_connection: disabled`
- `workspace_created: false`
- `safety_boundary`

review packet 不应该输出：

- 真实密码、token、私钥、账号秘密。
- 真实私钥路径。
- 从用户自由文本拼出的 shell 命令。
- 让人误以为已经创建目录或提交作业的字段。

示例输出形状：

```text
Single Server Dry-Run Review Packet
request_id: req-single-server-001
user_id: researcher-a
profile_id: single-server-dev
profile_display_name: Single Server Dev Runner
account_reference: lab-single-server-account
workspace_root_reference: workspace-ref:single-server-runs
template: fwi_multiscale_review@1
entrypoint_label: fwi_multiscale_sanity_check
execution: disabled
credentials_loaded: false
server_connection: disabled
workspace_created: false
parameters:
- dataset_id=marmousi
- niter=20
- frequency_band=3-8Hz
expected_artifacts:
- loss_curve
- final_velocity_model
resource_limits:
- max_gpus=1
- max_mpi_ranks=4
- max_wall_time_minutes=60
safety_boundary: review packet only; no command is submitted or executed
```

## 6. 实现顺序

第一批代码任务应该很小：

1. 新增 `SingleServerProfile`、`SingleServerJobTemplate` 和
   `SingleServerReviewRequest` metadata 类型。
2. 增加 profile 校验，重点拒绝空 credential reference、疑似内联秘密、空 workspace
   reference、空模板列表和 `runtime_enabled == true`。
3. 增加 template/request 校验，重点拒绝未知 template、profile 不匹配、版本不匹配、
   未允许参数和 `dry_run == false`。
4. 增加 dry-run review packet renderer，输出稳定文本并明确所有执行相关状态都是
   disabled/false。
5. 增加 v0.10 测试报告和学习总结，再考虑 fake lifecycle。

不要在第一批任务里做真实连接、凭据加载、workspace 创建、日志收集或审计持久化。

## 7. 验证要求

文档阶段：

- `git diff --check`
- `cmake --build build -j2`
- `ctest --test-dir build --output-on-failure`

后续代码阶段：

- 先写失败测试。
- `cmake --build build -j2` 确认新 API 未实现时失败。
- 实现最小 C++ metadata 和 renderer。
- `ctest --test-dir build -R SingleServerBackendTest --output-on-failure`
- `ctest --test-dir build --output-on-failure`
- `git diff --check`

## 8. 完成标准

v0.10 第一批实现只有在满足以下条件时才算完成：

- 有测试覆盖 profile、template、review request 和 review packet。
- profile 只保存引用，不保存真实凭据。
- renderer 不读取凭据、不连接服务器、不创建目录、不执行命令。
- 未知 template、未允许参数、疑似内联秘密和 `dry_run == false` 都会被拒绝。
- 运行时后端守卫继续拒绝真实 backend。

在这些完成之前，不进入 fake lifecycle；在 fake lifecycle 完成之前，不讨论真实服务器连接。
