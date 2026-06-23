# v0.15 Internal Sanity-Check Runner Gate Test Report

日期：2026-06-23

## 范围

本次实现新增 `internal_sanity_runner` metadata-only 组件，用于定义内部
sanity-check runner gate 的第一批 review packet。

新增内容：

- `SanityRunnerDefinition`
- `SanityRunnerRequest`
- `SanityRunnerReviewPacket`
- `make_sanity_runner_review_packet`
- `render_sanity_runner_review_packet`
- `InternalSanityRunnerTest`
- `docs/upgrade/internal-sanity-runner-execution-gate-v0.15.md`

本次只生成 review packet。模块不执行命令，不接受用户自由 command，不删除文件，
不读取凭据，不连接服务器，不连接 SSH/Slurm/PBS，不创建 workspace，也不采集真实
stdout/stderr 或 artifact。

## TDD 证据

基线：

- 进入实现前运行 `cmake --build build -j2`，退出码为 0。
- 进入实现前运行 `ctest --test-dir build --output-on-failure`，通过 31/31 个测试。

RED：

- 先新增 `tests/test_internal_sanity_runner.cpp` 和 `InternalSanityRunnerTest`
  CMake target。
- 运行 `cmake --build build -j2`。
- 构建按预期失败于：
  `fatal error: agent_rpc/research/internal_sanity_runner.h: No such file or directory`。

GREEN：

- 新增 `research/include/agent_rpc/research/internal_sanity_runner.h` 和
  `research/src/internal_sanity_runner.cpp`。
- 在 `research/CMakeLists.txt` 中接入 `src/internal_sanity_runner.cpp`。
- 实现 allowlisted runner id validation、timeout/capture metadata validation、
  artifact path workspace-root 校验、危险操作拒绝和 review packet renderer。
- `cmake --build build -j2` 退出码为 0。
- `ctest --test-dir build -R InternalSanityRunnerTest --output-on-failure`
  通过 1/1 个测试目标。

## 覆盖的行为

`InternalSanityRunnerTest` 覆盖：

- allowlisted runner id 可以生成 review packet。
- packet 保留 fixed entrypoint label、timeout、stdout/stderr capture plan、
  workspace plan id、workspace root、artifact path plan 和 audit event type。
- packet 明确输出 `execution: disabled`、`command_executed: false`、
  `free_form_command_accepted: false`、`credentials_loaded: false`、
  `server_connected: false` 和 `workspace_created: false`。
- unknown runner id 被拒绝。
- 用户自由 command、删除请求、凭据读取、SSH、Slurm、PBS 和 remote server access
  请求都会产生 validation error。
- renderer 不输出用户自由 command 内容。
- artifact path 必须留在 workspace root 下，且不能包含 `..` traversal。
- 缺失正数 timeout、stdout capture plan 或 stderr capture plan 的 runner definition
  会被拒绝。

## 验证命令

代码和文档完成后运行：

```bash
cmake --build build -j2
ctest --test-dir build -R InternalSanityRunnerTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

结果：

- PASS. `cmake --build build -j2` 退出码为 0。
- PASS. `InternalSanityRunnerTest` 通过 1/1 个测试目标。
- PASS. 全量 `ctest --test-dir build --output-on-failure` 通过 32/32 个测试。
- PASS. `git diff --check` 没有输出。

## 安全边界

v0.15 不做：

- 不执行用户自由 shell。
- 不运行真实 CUDA/MPI。
- 不调用 `mpirun`、`srun`、SSH、Slurm、PBS 或 local wrapper。
- 不连接远程服务器。
- 不读取密码、token、私钥、secret manager、`.env` 或凭据文件。
- 不创建 workspace、run directory、log directory 或 artifact directory。
- 不删除目录，不移动文件，不移动 trash。
- 不采集真实 stdout/stderr、日志或 artifact。
- 不改变 shared backend guard；真实 backend 仍未启用。

`SanityRunnerReviewPacket` 是 execution gate review metadata。它可以说明未来固定
sanity runner 需要满足哪些边界，但不能代表系统已经具备真实执行能力。
