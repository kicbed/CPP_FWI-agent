# v0.14 Approved Template Run Packet Test Report

日期：2026-06-23

## 范围

本次实现新增 `approved_template_run_packet` metadata-only 组件，用于把
single-server profile、approved template、结构化 review request、workspace plan
和 lifecycle id 合成一个 future-run review packet。

新增内容：

- `ApprovedTemplateRunPacketRequest`
- `ApprovedTemplateRunPacket`
- `make_approved_template_run_packet`
- `render_approved_template_run_packet`
- `ApprovedTemplateRunPacketTest`

本次只渲染“将来如何运行”的 packet。模块不执行命令，不接受用户自由 command，不读取
凭据，不连接服务器，不创建 workspace，不创建目录，不移动文件。

## TDD 证据

基线：

- 进入实现前运行 `cmake --build build -j2`，退出码为 0。
- 进入实现前运行 `ctest --test-dir build --output-on-failure`，通过 30/30 个测试。

RED：

- 先新增 `tests/test_approved_template_run_packet.cpp` 和
  `ApprovedTemplateRunPacketTest` CMake target。
- 运行 `cmake --build build -j2`。
- 构建按预期失败于：
  `fatal error: agent_rpc/research/approved_template_run_packet.h: No such file or directory`。

GREEN：

- 新增 `research/include/agent_rpc/research/approved_template_run_packet.h` 和
  `research/src/approved_template_run_packet.cpp`，并接入 `research/CMakeLists.txt`。
- 实现 packet validation、批准参数筛选、workspace plan validation error 汇总和
  review packet renderer。
- `cmake --build build -j2` 退出码为 0。
- `ctest --test-dir build -R ApprovedTemplateRunPacketTest --output-on-failure`
  通过 1/1 个测试目标。

## 覆盖的行为

`ApprovedTemplateRunPacketTest` 覆盖：

- 正常请求可以渲染 approved-template run packet。
- packet 包含 profile id、template id/version、entrypoint label、lifecycle id、
  workspace path、run directory path、log path、artifact path 和资源上限。
- packet 明确输出 `execution: disabled`、`command_executed: false`、
  `credentials_loaded: false`、`server_connected: false`、
  `workspace_created: false`、`directories_created: false`、
  `files_moved: false` 和 `free_form_command_accepted: false`。
- 凭据引用不会出现在渲染文本中。
- 未批准参数会产生 validation error，并且不会进入 `accepted_parameters` 或渲染文本。
- 用户自由 command 会产生 validation error，并且命令文本不会被渲染。
- 缺失必填参数会被拒绝。
- template/profile mismatch 会被拒绝。
- workspace plan validation error 会以 `workspace plan: ...` 前缀进入 run packet。

## 验证命令

代码和文档完成后运行：

```bash
cmake --build build -j2
ctest --test-dir build -R ApprovedTemplateRunPacketTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

结果：

- PASS. `cmake --build build -j2` 退出码为 0。
- PASS. `ApprovedTemplateRunPacketTest` 通过 1/1 个测试目标。
- PASS. 全量 `ctest --test-dir build --output-on-failure` 通过 31/31 个测试。
- PASS. `git diff --check` 没有输出。

## 安全边界

v0.14 不做：

- 不执行 shell、CUDA/MPI、`mpirun`、`srun`、SSH、Slurm、PBS 或 local wrapper。
- 不接受用户自由 command string；如果输入里出现 `free_form_command`，只记录拒绝错误，
  不渲染命令文本。
- 不读取真实密码、token、私钥、secret manager 或服务器凭据。
- 不连接服务器。
- 不创建 workspace、run directory、log directory 或 artifact directory。
- 不删除目录，不移动文件，不移动 trash。
- 不采集真实日志或 artifact。
- 不改变 shared backend guard；真实 backend 仍未启用。

`ApprovedTemplateRunPacket` 是 review metadata。它可以告诉用户 fixed approved
template、结构化参数和 workspace preview 将怎样组合，但不能代表系统已经具备真实执行能力。
