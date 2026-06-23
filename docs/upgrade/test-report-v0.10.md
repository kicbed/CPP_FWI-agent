# v0.10 单服务器账号接入准备测试报告

日期：2026-06-23

状态：v0.10 单服务器账号 metadata/profile/template 和 dry-run review packet 第一批实现已完成。

## 范围

v0.10 面向当前实验室的简化场景：自己或小组先用一个服务器账号跑实验。第一批实现只做
单服务器账号接入准备，不连接真实服务器。

已完成能力：

- 新增 `SingleServerProfile` metadata，用于记录 profile id、展示名、账号引用、
  凭据引用、workspace root 引用、允许 template 列表和 runtime enabled 状态。
- 新增 `SingleServerJobTemplate` metadata，用于记录 template id、版本、profile id、
  固定入口标签、允许参数、预期 artifact 和资源上限。
- 新增 `SingleServerReviewRequest` metadata，用于记录 request、user、profile、
  template、版本、结构化参数和 dry-run 状态。
- 新增 profile 校验：拒绝空 profile/account/credential/workspace 引用，拒绝看起来像
  内联秘密的 credential reference，拒绝空 template 列表，拒绝 runtime enabled。
- 新增 template/request 校验：拒绝未被 profile 允许的 template、profile 不匹配、
  版本不匹配、未允许参数和 `dry_run == false`。
- 新增 dry-run review packet renderer，稳定输出 profile、template、参数、artifact
  和资源上限，并明确执行、凭据读取、服务器连接和 workspace 创建都关闭。

明确不包含：

- 真实 CUDA/MPI 执行。
- SSH、Slurm、PBS、本地 wrapper 或远程服务器连接。
- 密码、token、私钥、服务器账号秘密或 secret manager 读取。
- 本地或远程 workspace 创建、清理、上传、下载或删除。
- 生产审计持久化。
- 用户文本到任意 shell 命令的执行路径。
- Code Agent 自动应用未确认 patch。

## TDD 证据

RED 1：metadata 模型缺失。

- 先添加 `tests/test_single_server_backend.cpp` 和 `SingleServerBackendTest` CMake target。
- 运行 `cmake --build build -j2` 失败，错误为：
  `fatal error: agent_rpc/research/single_server_backend.h: No such file or directory`。
- 这证明测试确实在检查新增 API，而不是误用已有行为。

GREEN 1：最小 profile metadata 和校验实现。

- 新增 `research/include/agent_rpc/research/single_server_backend.h`。
- 新增 `research/src/single_server_backend.cpp`。
- 将 `single_server_backend.cpp` 加入 `agent_rpc_research`。
- `SingleServerBackendTest` 的 4 个 profile 测试通过。

RED 2：template/request 校验缺失。

- 添加 template 允许列表、未知参数和非 dry-run request 测试。
- `SingleServerBackendTest` 失败 3 个测试，失败点是 validation functions 仍返回空
  errors。

GREEN 2：template/request 校验实现。

- 实现 `validate_single_server_template`。
- 实现 `validate_single_server_review_request`。
- `SingleServerBackendTest` 通过。

RED 3：review packet renderer 缺失。

- 添加 `RendersDryRunReviewPacketWithoutSecretsOrExecution` 测试。
- `SingleServerBackendTest` 失败 1 个测试，失败点是 renderer 返回空字符串。

GREEN 3：review packet renderer 实现。

- 实现 `render_single_server_review_packet`。
- 测试确认 packet 包含 request、user、profile、account reference、workspace
  reference、template、entrypoint、参数和 artifact。
- 测试确认 packet 包含 `execution: disabled`、`credentials_loaded: false`、
  `server_connection: disabled`、`workspace_created: false`。
- 测试确认 packet 不输出 `credential_reference` 的值。

## 测试覆盖

新增测试文件：

- `tests/test_single_server_backend.cpp`

新增测试目标：

- `SingleServerBackendTest`

测试用例：

- `AcceptsMetadataOnlyProfile`
- `RejectsProfileWithoutCredentialReference`
- `RejectsInlineSecretLookingCredentialReference`
- `RejectsRuntimeEnabledProfile`
- `AcceptsTemplateAllowedByProfile`
- `RejectsTemplateNotAllowedByProfile`
- `RejectsReviewRequestWithUnknownParameter`
- `RejectsNonDryRunReviewRequest`
- `RendersDryRunReviewPacketWithoutSecretsOrExecution`

这些测试保护的风险：

- 凭据引用为空或被误写成内联秘密。
- 单服务器 profile 被误标为 runtime enabled。
- template 绕过 profile allowlist。
- 用户传入未批准参数，例如 `extra_flags`。
- review request 从 dry-run 误变成真实执行。
- review packet 泄露 credential reference 或暗示已经连接服务器。

## 最终验证

命令：

```bash
cmake --build build -j2
ctest --test-dir build -R SingleServerBackendTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

结果：

- PASS。`cmake --build build -j2` 退出码为 0。
- PASS。`ctest --test-dir build -R SingleServerBackendTest --output-on-failure`
  通过 1/1 个目标测试，目标内 9 个用例通过。
- PASS。全量 `ctest --test-dir build --output-on-failure` 通过 27/27 个测试。
- PASS。`git diff --check` 没有输出。

## 安全结果

v0.10 第一批实现仍然是非执行层：

- 没有新增 `ssh`、`sbatch`、`qsub`、`mpirun`、`srun`、`system()` 或 `popen()` 调用。
- 没有读取环境变量、密钥文件、secret manager 或服务器凭据。
- 没有创建或删除 workspace 目录。
- review packet 是文本预览，不是提交包执行器。
- 运行时后端守卫仍由既有 `JobBackend`/M11 逻辑控制，本次没有启用真实 backend。
