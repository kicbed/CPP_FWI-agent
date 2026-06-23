# v0.13 Workspace Planner Test Report

日期：2026-06-23

## 范围

本次实现新增 `workspace_planner` metadata-only 组件，用于为未来单服务器
internal preview 生成 workspace、run directory、log path 和 artifact path 预览。

新增内容：

- `WorkspacePlanRequest`
- `WorkspacePlan`
- `validate_workspace_plan_request`
- `make_workspace_plan`
- `render_workspace_plan_preview`
- `WorkspacePlannerTest`

本次只做字符串级路径计划和校验，不调用 filesystem 创建、删除、移动、扫描、symlink
跟随或远程访问 API。

## TDD 证据

RED 1：

- 先新增 `tests/test_workspace_planner.cpp` 和 `WorkspacePlannerTest` CMake target。
- 运行 `cmake --build build -j2`。
- 构建按预期失败于：
  `fatal error: agent_rpc/research/workspace_planner.h: No such file or directory`。

GREEN 1：

- 新增 `research/include/agent_rpc/research/workspace_planner.h` 和
  `research/src/workspace_planner.cpp`，并接入 `research/CMakeLists.txt`。
- 实现 workspace/run/log/artifact preview、基础 root 和相对路径组件校验。
- `cmake --build build -j2` 退出码为 0。
- `ctest --test-dir build -R WorkspacePlannerTest --output-on-failure`
  通过 1/1 个测试目标。

RED 2：

- 追加保护标签测试，覆盖 `secrets`、`env` 和 `shared_data` 等危险路径标签。
- 运行
  `cmake --build build -j2 && ctest --test-dir build -R WorkspacePlannerTest --output-on-failure`。
- 构建通过，聚焦测试按预期失败于缺少
  `run directory name must not use protected labels` 和
  `artifact subdirectory must not use protected labels`。

GREEN 2：

- 抽出保护标签列表，并在 workspace root segment 与相对路径组件上复用。
- `cmake --build build -j2 && ctest --test-dir build -R WorkspacePlannerTest --output-on-failure`
  退出码为 0。

## 覆盖的行为

`WorkspacePlannerTest` 覆盖：

- 渲染 workspace/run/log/artifact preview。
- `directories_created`、`files_moved`、`server_connected` 均保持 `false`。
- 拒绝 job directory 中的 `..` 路径穿越。
- 拒绝空 job directory，避免把 workspace root 当成 job directory。
- 拒绝绝对路径形式的 job directory 逃逸。
- 拒绝空 workspace root。
- 拒绝危险 workspace root，例如 `/`。
- 拒绝 log file 和 artifact subdirectory 中的路径穿越或绝对路径。
- 拒绝 `secrets`、`env`、`shared_data` 等保护标签。

## 验证命令

代码实现后已运行：

```bash
cmake --build build -j2
ctest --test-dir build -R WorkspacePlannerTest --output-on-failure
ctest --test-dir build --output-on-failure
git diff --check
```

结果：

- PASS. `cmake --build build -j2` 退出码为 0。
- PASS. `WorkspacePlannerTest` 通过 1/1 个测试目标。
- PASS. 全量 `ctest --test-dir build --output-on-failure` 通过 30/30 个测试。
- PASS. `git diff --check` 没有输出。

## 安全边界

v0.13 不做：

- 不创建 workspace、run directory、log directory 或 artifact directory。
- 不删除目录。
- 不移动文件。
- 不连接服务器。
- 不访问远程文件系统。
- 不跟随 symlink。
- 不扫描真实目录树。
- 不执行 shell、CUDA/MPI、SSH、Slurm、PBS 或 local wrapper。
- 不读取真实凭据。

`WorkspacePlan` 只是 preview metadata。后续 v0.14 可以引用这些路径计划生成
approved-template run packet，但仍不能把它解释成真实目录已经存在。
