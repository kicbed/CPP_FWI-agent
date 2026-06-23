# v0.12 Fake Lifecycle Test Report

日期：2026-06-23

## 范围

本次实现新增单服务器 fake lifecycle 的 metadata-only 状态机：

- 新增 `SingleServerLifecycleState`，覆盖 `requested`、`reviewed`、
  `approved`、`rejected`、`queued`、`running`、`succeeded`、`failed` 和
  `cancelled`。
- 新增 `SingleServerLifecycleEvent` 和 `SingleServerLifecycleRecord`。
- 新增内存态 transition helper，用于追加 lifecycle event 并更新当前状态。
- 新增 lifecycle preview renderer，展示当前状态、允许的下一状态、历史事件和安全边界。
- 新增 `SingleServerLifecycleTest`。

本次没有新增真实服务器连接、命令执行、目录创建、凭据读取、日志采集、artifact 采集、
SSH、Slurm/PBS、CUDA/MPI 或 local wrapper。

## TDD 证据

RED 1：

- 先新增 `tests/test_single_server_lifecycle.cpp` 和 CMake target。
- 运行 `cmake --build build -j2`。
- 构建按预期失败于：
  `fatal error: agent_rpc/research/single_server_lifecycle.h: No such file or directory`。

GREEN 1：

- 新增 `research/include/agent_rpc/research/single_server_lifecycle.h`、
  `research/src/single_server_lifecycle.cpp`，并接入 `research/CMakeLists.txt`。
- 实现状态名解析和 requested record 创建。
- `cmake --build build -j2` 退出码为 0。
- `ctest --test-dir build -R SingleServerLifecycleTest --output-on-failure`
  通过 1/1 个测试目标。

RED 2：

- 追加状态转换、取消、终态拒绝和 lifecycle preview 测试。
- 运行 `cmake --build build -j2`。
- 构建按预期失败于 `append_single_server_lifecycle_event` 和
  `render_single_server_lifecycle_preview` 未声明。

GREEN 2：

- 新增状态转换校验、内存 event append 和 preview renderer。
- `cmake --build build -j2` 退出码为 0。
- `ctest --test-dir build -R SingleServerLifecycleTest --output-on-failure`
  通过 1/1 个测试目标。

RED 3：

- 追加 preview 中 `allowed_next_states` 的断言，要求 renderer 告诉用户下一步允许什么。
- 运行 `cmake --build build -j2 && ctest --test-dir build -R SingleServerLifecycleTest --output-on-failure`。
- 构建通过，聚焦测试按预期失败于缺少 `allowed_next_states`、`approved` 和 `rejected`。

GREEN 3：

- renderer 增加当前状态对应的允许下一状态列表。
- `cmake --build build -j2 && ctest --test-dir build -R SingleServerLifecycleTest --output-on-failure`
  退出码为 0。

## 覆盖的行为

`SingleServerLifecycleTest` 覆盖：

- 所有状态名的 parse 行为。
- 新 record 默认进入 `requested`，并且 `server_connected`、`command_executed`、
  `workspace_created` 都是 `false`。
- 成功路径：`requested -> reviewed -> approved -> queued -> running -> succeeded`。
- 取消路径：`approved -> cancelled`。
- 终态拒绝：`rejected -> running` 会返回 `invalid lifecycle transition`，不修改状态。
- preview 输出当前状态、允许下一状态、event history 和非执行安全边界。

## 验证命令

代码实现后已运行：

```bash
cmake --build build -j2
ctest --test-dir build -R SingleServerLifecycleTest --output-on-failure
ctest --test-dir build --output-on-failure
```

结果：

- PASS. `cmake --build build -j2` 退出码为 0。
- PASS. `SingleServerLifecycleTest` 通过 1/1 个测试目标。
- PASS. 全量 `ctest --test-dir build --output-on-failure` 通过 29/29 个测试。
- PASS. 文档更新后 `git diff --check` 没有输出。
- PASS. 文档更新后 `cmake --build build -j2` 退出码为 0。
- PASS. 文档更新后全量 `ctest --test-dir build --output-on-failure` 通过 29/29 个测试。

## 安全边界

v0.12 只模拟生命周期状态。它不会：

- 连接服务器。
- 执行 shell 或 fixed runner。
- 创建 workspace 或 run directory。
- 读取真实凭据。
- 采集真实日志或 artifact。
- 改变 `JobBackend` runtime guard。

`queued` 和 `running` 在本阶段只是 UI/metadata 可以展示的假状态，不表示作业已经进入
真实队列或真实运行。
