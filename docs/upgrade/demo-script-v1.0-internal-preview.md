# v1.0 Internal Preview Demo Script

日期：2026-06-23

目标：演示实验室成员如何在不写 shell、不接触凭据、不连接服务器的情况下，查看
approved template review packet、fake lifecycle、workspace/artifact preview 和
sanity runner gate。

## Preparation

确认工作区干净并构建：

```bash
git status --short
cmake --build build -j2
```

运行 internal preview 相关 gate 测试：

```bash
ctest --test-dir build -R "(SingleServerBackendTest|SafeOperationsTest|SingleServerLifecycleTest|WorkspacePlannerTest|ApprovedTemplateRunPacketTest|InternalSanityRunnerTest)" --output-on-failure
```

运行全量测试：

```bash
ctest --test-dir build --output-on-failure
```

## Scene 1: User Chooses An Approved Template

Narration:

> 用户想做 Marmousi multi-scale FWI 的内部试运行评审，但不写 shell command。
> 系统只允许选择 approved template，并填写结构化参数。

Point to the template fixture in tests:

```text
template_id: fwi_multiscale_review
version: 1
entrypoint_label: fwi_multiscale_sanity_check
allowed parameters: dataset_id, niter, frequency_band, gpu_count
```

Verification:

```bash
ctest --test-dir build -R SingleServerBackendTest --output-on-failure
```

Expected talking points:

- `SingleServerProfile.runtime_enabled` must remain false.
- inline secret-looking credential references are rejected.
- unknown parameters are rejected.
- non-dry-run review requests are rejected.
- rendered review packet does not reveal credential reference.

## Scene 2: User Requests A Delete Preview

Narration:

> 用户想清理一次实验目录。internal preview 只允许生成 delete dry-run review
> packet，不能删除文件，也不能移动 trash。

Verification:

```bash
ctest --test-dir build -R SafeOperationsTest --output-on-failure
```

Expected talking points:

- `readonly` cannot request delete preview.
- `lab_user` can request workspace-scoped delete dry-run preview.
- `lab_root` still cannot request real delete execution.
- path traversal, workspace-root deletion, protected paths, symlinks, and
  missing confirmation are rejected.
- packet keeps `deletion_executed: false`, `trash_move_executed: false`, and
  `shell_executed: false`.

## Scene 3: Operator Reviews Fake Lifecycle

Narration:

> operator 接受 review packet 后，系统展示 fake lifecycle 状态。这个状态只用于
> 内部预览和用户理解，不连接服务器。

Verification:

```bash
ctest --test-dir build -R SingleServerLifecycleTest --output-on-failure
```

Expected walkthrough:

```text
requested -> reviewed -> approved -> queued -> running -> succeeded
```

Expected talking points:

- `allowed_next_states` tells the user what can happen next.
- terminal states block invalid follow-up transitions.
- `server_connected: false`, `command_executed: false`, and
  `workspace_created: false` remain visible.

## Scene 4: User Inspects Workspace And Artifact Preview

Narration:

> 系统展示未来 workspace、run directory、log file 和 artifact paths，但不创建目录。

Verification:

```bash
ctest --test-dir build -R WorkspacePlannerTest --output-on-failure
```

Expected talking points:

- preview paths stay under `/lab/workspaces`.
- `..` traversal and absolute escape paths are rejected.
- protected labels such as `secrets`, `env`, and `shared_data` are rejected.
- `directories_created: false`, `files_moved: false`, and
  `server_connected: false` remain visible.

## Scene 5: System Renders Approved Template Run Packet

Narration:

> 系统把 profile、approved template、structured parameters、workspace plan 和
> lifecycle id 合成 run packet。packet 说明未来如何运行，但本阶段仍不执行。

Verification:

```bash
ctest --test-dir build -R ApprovedTemplateRunPacketTest --output-on-failure
```

Expected talking points:

- packet includes profile id, template id/version, fixed entrypoint label,
  lifecycle id, workspace path, run path, log path, artifact paths, and resource
  limits.
- free-form command is rejected and not rendered.
- unapproved parameter is rejected and not rendered.
- credential reference is not rendered.
- `command_executed: false`, `credentials_loaded: false`,
  `server_connected: false`, `workspace_created: false`,
  `directories_created: false`, `files_moved: false`, and
  `free_form_command_accepted: false` remain visible.

## Scene 6: Operator Checks The Sanity Runner Gate

Narration:

> 未来如果要做最小 fixed sanity runner，必须先通过 gate。当前 gate 仍然只生成
> review packet，不执行 runner。

Verification:

```bash
ctest --test-dir build -R InternalSanityRunnerTest --output-on-failure
```

Expected talking points:

- runner id must be allowlisted.
- timeout must be positive.
- stdout/stderr capture must be planned.
- artifact paths must stay under workspace root.
- free-form command, deletion, credential read, SSH, Slurm, PBS, and remote
  server access are rejected.
- `execution: disabled` and `command_executed: false` remain visible.

## Close The Demo

Run the final proof:

```bash
git diff --check
cmake --build build -j2
ctest --test-dir build --output-on-failure
```

Closing statement:

> v1.0 internal preview can demonstrate the safe single-server review workflow:
> approved template selection, structured parameters, review packet, fake
> lifecycle, workspace/artifact preview, and sanity runner gate. It still does
> not run CUDA/MPI, SSH, Slurm, PBS, arbitrary shell, credentials, deletion, or
> workspace mutation.
