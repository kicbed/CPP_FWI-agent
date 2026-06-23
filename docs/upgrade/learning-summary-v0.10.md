# v0.10 单服务器账号接入准备学习总结

## 1. 解决的问题

v0.9 已经完成了后端就绪评审：系统能把 M11 preflight metadata 渲染成 readiness
report、dry-run submission packet、audit preview 和 workspace/artifact plan。但 v0.9
还是偏“通用真实后端上线前评审”，它面向 local wrapper、SSH、Slurm、PBS 这些候选项。

当前实验室真实情况更简单：先由自己或小组内部用一个服务器账号跑实验。这个场景不需要一
开始建设完整多用户平台、Slurm/PBS 调度器集成或复杂生产审计系统，但仍然不能直接把
用户输入变成 shell 命令，也不能把服务器密码、token、私钥或账号秘密写进仓库。

v0.10 解决的是这个中间层问题：在真实服务器连接之前，先建立一个非常小、可测试、可学习
的 metadata 边界。它让项目可以表达“准备用哪个单服务器 profile、允许哪个 approved
template、这次 review request 带了哪些结构化参数、review packet 给人看什么”，但仍然
不执行任何命令。

上一阶段不足在于：`BackendPreflightPackage` 适合通用 M11 审批，但对“一个服务器账号”
这个实际初始阶段来说太宽。v0.10 把范围收窄，让下一步学习和开发更容易抓住主线。

## 2. 实现方式

核心数据流是：

```text
SingleServerProfile
        |
        v
SingleServerJobTemplate
        |
        v
SingleServerReviewRequest
        |
        v
render_single_server_review_packet
        |
        v
Single Server Dry-Run Review Packet
```

`SingleServerProfile` 表示一个单服务器账号使用场景的非秘密 metadata。它保存
`profile_id`、展示名、账号引用、凭据引用、workspace root 引用和允许 template
列表。这里最重要的是“引用”两个字：`credential_reference` 不是密码，`account_reference`
不是账号秘密，`workspace_root_reference` 也不是用户可以自由写入的真实路径。

`SingleServerJobTemplate` 表示一个固定 approved template。它保存 template id、版本、
profile id、固定入口标签、允许参数名、预期 artifact 和资源上限。用户后续只能选择
template 并填写结构化参数，不能传任意 command。

`SingleServerReviewRequest` 表示一次 dry-run review 请求。它包含 request、user、
profile、template、版本、参数和 `dry_run`。如果 `dry_run == false`，校验直接拒绝。

`render_single_server_review_packet` 输出稳定文本，用于人类 review。它故意输出
`execution: disabled`、`credentials_loaded: false`、`server_connection: disabled`、
`workspace_created: false`。这四个字段是学习重点：它们说明这个 packet 只是预览，不是执行。

## 3. API 形状

公开入口位于：

- `research/include/agent_rpc/research/single_server_backend.h`

主要类型：

- `SingleServerProfile`
- `SingleServerJobTemplate`
- `SingleServerReviewRequest`

主要函数：

- `validate_single_server_profile`
- `validate_single_server_template`
- `validate_single_server_review_request`
- `render_single_server_review_packet`

实现位于：

- `research/src/single_server_backend.cpp`

测试位于：

- `tests/test_single_server_backend.cpp`

CMake 入口：

- `research/CMakeLists.txt` 把 `single_server_backend.cpp` 编进 `agent_rpc_research`。
- `tests/CMakeLists.txt` 新增 `SingleServerBackendTest`。

## 4. 设计取舍

为什么不直接接 SSH？

因为 SSH 接入不是只写一个连接命令。真实 SSH 需要 host 白名单、凭据引用、主机信任、
远程目录隔离、超时、取消、日志回收和失败处理。当前阶段这些都没有准备好，所以先用
metadata 和 review packet 把边界定清楚。

为什么不直接做 local wrapper？

local wrapper 也会执行命令。如果现在做 wrapper，很容易把 review packet 的
`entrypoint_label` 误当成可执行入口。v0.10 故意只保留 label，不引入 process API。

为什么不一开始做多用户权限系统？

当前实验室是一个账号、自己或小组内部先跑。过早做复杂多租户系统会让学习和开发偏离主线。
但这不代表可以跳过安全底线。v0.10 仍然要求 profile allowlist、approved template、
结构化参数和 dry-run-only 校验。

为什么 renderer 不输出 `credential_reference`？

测试里故意确认 `secret-ref:single-server-runner` 不出现在 review packet 中。即使它只是
引用名，也不应该默认展示给每个 review packet 读者。packet 只需要证明没有加载凭据：
`credentials_loaded: false`。

## 5. 测试保护点

`AcceptsMetadataOnlyProfile` 保护正常 profile 可以通过校验。

`RejectsProfileWithoutCredentialReference` 保护 profile 不能缺少凭据引用。注意这不是要求
提供凭据内容，而是要求有一个外部凭据管理策略的引用名。

`RejectsInlineSecretLookingCredentialReference` 保护开发者不要把 `password=`、`token=`、
私钥头这类内容直接写进 metadata。

`RejectsRuntimeEnabledProfile` 保护当前阶段不能启用真实运行。

`AcceptsTemplateAllowedByProfile` 保护 profile allowlist 内的 template 可以通过。

`RejectsTemplateNotAllowedByProfile` 保护 template 不能绕过 profile allowlist。

`RejectsReviewRequestWithUnknownParameter` 保护用户不能传 `extra_flags` 这类未批准参数。

`RejectsNonDryRunReviewRequest` 保护 review request 不能变成真实执行请求。

`RendersDryRunReviewPacketWithoutSecretsOrExecution` 保护 review packet 的输出语义：能给人看
请求、profile、template、参数和 artifact，但明确不执行、不读凭据、不连服务器、不创建
workspace。

## 6. 安全边界

CUDA/MPI：v0.10 没有运行 CUDA/MPI，也没有新增 `mpirun` 或 `srun`。GPU/MPI 只作为资源上限
metadata 出现在 template 中。

SSH：v0.10 没有 SSH host、用户名、私钥路径或连接代码。

Slurm/PBS：v0.10 没有 `sbatch`、`squeue`、`scancel`、`qsub`、`qstat`、`qdel`。

凭据：v0.10 不读取环境变量、密钥文件或 secret manager。`credential_reference` 只是引用，
而且 review packet 不输出它的值。

workspace：v0.10 不创建、删除或清理目录。`workspace_root_reference` 只是引用。

shell：v0.10 没有自由命令执行路径。用户参数必须是 template 允许的结构化键值对。

Code Agent：Code Agent 仍然只读，不自动应用未确认 patch。

## 7. TDD 过程

第一轮先写 profile 测试和 CMake target，构建失败在 header 不存在。这是正确的 RED。
随后添加 header、source 和 CMake source，profile 校验测试通过。

第二轮添加 template/request 负例测试，目标测试失败 3 个用例，因为校验函数还返回空
errors。随后实现 allowlist、profile/version 匹配、unknown parameter 和 dry-run-only
校验，目标测试通过。

第三轮添加 renderer 测试，目标测试失败 1 个用例，因为 renderer 返回空字符串。随后实现
review packet 文本输出，目标测试、全量 ctest 和 diff check 通过。

这个过程的价值是：每个能力都有失败证据，说明测试不是事后补的装饰，而是真的约束了行为。

## 8. 面试和汇报讲法

短 pitch：

我在一个 FWI-first 科研多智能体平台里实现了 v0.10 单服务器账号接入准备层。它不是直接
SSH 或提交 Slurm，而是先用 C++ metadata、校验函数和 dry-run review packet 建立
profile/template/request 边界，确保凭据不入库、用户文本不变成 shell、真实执行继续关闭。

技术深挖版：

v0.10 新增 `SingleServerProfile`、`SingleServerJobTemplate` 和
`SingleServerReviewRequest`。profile 只保存账号、凭据、workspace 的引用；template
定义允许的参数和资源上限；request 必须保持 dry-run。renderer 输出 review packet，
明确 execution、credential loading、server connection、workspace creation 都是 disabled。
测试覆盖内联秘密、未知 template、未知参数、非 dry-run 请求和 review packet 不泄露
credential reference。

常见追问：

问：为什么不直接接服务器？
答：因为真实服务器连接会立刻引入凭据、路径、命令执行、资源占用和失败恢复问题。先做
metadata 和 review packet，可以把安全边界稳定下来，避免把 LLM 生成内容直接推向执行。

问：单服务器账号还需要权限设计吗？
答：不需要一开始做复杂多租户平台，但仍需要 profile allowlist 和 approved template。
否则一个内部工具也可能变成任意命令执行入口。

问：credential reference 和 credential 有什么区别？
答：credential 是秘密内容，例如密码、token、私钥；credential reference 是外部凭据管理
系统中的引用名。代码可以保存引用名，但不能保存或展示秘密内容。

问：review packet 能不能执行？
答：不能。review packet 是人看的预览文本。测试要求它显示 execution disabled、
credentials loaded false、server connection disabled、workspace created false。

STAR 复盘：

Situation：项目已经有 dry-run 实验规划和后端 readiness review，但实验室当前只是一个
服务器账号的小规模使用场景。
Task：需要在不连接服务器的前提下，建立单服务器账号初步接入的数据边界。
Action：用 TDD 新增 profile/template/request metadata、校验函数和 dry-run review packet
renderer，并补测试报告与学习总结。
Result：项目现在能清楚表达单服务器接入准备包，同时仍然不执行命令、不读凭据、不连服务器，
为后续 fake lifecycle 或真实接入留下受控入口。
