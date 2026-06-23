# v0.11 Safe Operations Test Report

日期：2026-06-23

## 范围

本次完成 v0.11 第一批 safe operations 实现：

- `LabAccountRole`：`lab_root`、`lab_user`、`readonly`、`unknown`。
- `SafeOperationType` 和 `SafeOperationPolicy`：表达读、review、approved-template
  dry-run、delete dry-run preview 和维护模板等操作边界。
- `SafeOperationRequest`：校验用户、角色和操作 allowlist。
- `DeleteReviewRequest` 和 `DeleteReviewPacket`：只表达删除预览 metadata。
- `render_delete_review_packet`：渲染删除 dry-run review packet，并明确所有副作用为
  false。

不在本次范围内：

- 不做真实删除。
- 不移动 trash。
- 不调用 filesystem remove。
- 不执行 shell。
- 不读取凭据。
- 不连接服务器。
- 不创建 workspace。

## TDD 证据

RED 1：

- 先新增 `tests/test_safe_operations.cpp` 和 `SafeOperationsTest` target。
- `cmake --build build -j2` 失败于
  `fatal error: agent_rpc/research/safe_operations.h: No such file or directory`。

GREEN 1：

- 新增 `safe_operations.h`、`safe_operations.cpp` 和 CMake source wiring。
- `cmake --build build -j2` 通过。
- `ctest --test-dir build -R SafeOperationsTest --output-on-failure` 通过。

RED 2：

- 追加删除 dry-run review request、protected path、symlink、确认短语、renderer 和
  `DeleteReviewPacket` metadata 测试。
- `cmake --build build -j2` 失败于缺少 `DeleteReviewRequest`、
  `validate_delete_review_request`、`render_delete_review_packet` 和
  `build_delete_review_packet` 声明。

GREEN 2：

- 新增 `DeleteReviewRequest`、`DeleteReviewPacket`、删除 preview validation、
  packet builder 和 renderer。
- `cmake --build build -j2` 通过。
- `ctest --test-dir build -R SafeOperationsTest --output-on-failure` 通过。

## 测试覆盖

`SafeOperationsTest` 覆盖：

- 角色字符串解析。
- `readonly` 可以读文件但不能请求 delete preview。
- `lab_user` 可以请求 workspace 下的 delete dry-run preview。
- `lab_root` 仍不能请求真实删除执行。
- 非 dry-run delete review request 被拒绝。
- 路径穿越被拒绝。
- workspace root 本身不能作为删除目标。
- protected path 标记被拒绝。
- symlink 标记会 block delete preview。
- 缺少确认短语会 block delete preview。
- renderer 输出 `deletion_executed: false`、`trash_move_executed: false` 和
  `shell_executed: false`。
- `DeleteReviewPacket` metadata 中执行、trash move 和 shell flags 永远为 false。

## 验证命令

聚焦验证：

```bash
cmake --build build -j2
ctest --test-dir build -R SafeOperationsTest --output-on-failure
```

结果：

- PASS. `cmake --build build -j2` 退出码为 0。
- PASS. `SafeOperationsTest` 通过 1/1 个 CTest target。

提交前全量验证：

```bash
git diff --check
cmake --build build -j2
ctest --test-dir build --output-on-failure
```

结果：

- PASS. `git diff --check` 没有输出。
- PASS. `cmake --build build -j2` 退出码为 0。
- PASS. 全量 `ctest --test-dir build --output-on-failure` 通过 28/28 个测试。
