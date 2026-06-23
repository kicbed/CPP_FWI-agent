# v0.11 实验室内部安全操作策略设计

日期：2026-06-23

状态：设计和实现计划已创建，代码尚未实现。

本文把下一阶段收敛为实验室内部使用场景下的安全操作策略。当前目标不是公网平台，
也不是复杂多租户系统，而是支持实验室内部账号按既有服务器权限工作，同时在应用层防止
误删代码、环境、数据集、其他人结果目录和凭据目录。

## 1. 当前判断

实验室内部版本可以采用简单账号模型：

- `lab_root`：实验室管理员、导师指定维护者或项目负责人。可以维护 profile/template，
  查看所有 review packet，批准内部操作。
- `lab_user`：普通实验室成员。可以查看、规划、运行 approved template 的 dry-run 或后续
  已批准执行路径，只能管理自己的 job/workspace。
- `readonly`：学习、查看、演示账号。只能读文档、配置、日志、结果摘要和 dry-run packet。

这里的 `lab_root` 不是说工具可以无约束删除系统文件。它只表示这个用户在实验室工具中
有最高应用角色。危险操作仍然必须走 dry-run preview、多次确认和路径保护。

## 2. 操作风险分级

### 2.1 默认安全操作

这些操作可以作为第一批实现的允许项：

- 列目录。
- 读文件。
- 搜索文本。
- 查看现有配置。
- 解析日志。
- 解析 loss curve。
- 渲染 dry-run review packet。
- 运行现有测试或构建命令，但命令必须来自固定开发者维护的 allowlist，不来自用户自由文本。

这些操作仍然应该限制在仓库或 approved workspace 范围内。

### 2.2 受控写操作

这些操作只允许写入当前 job workspace 或项目明确允许的报告路径：

- 生成 config snapshot。
- 保存 review packet。
- 保存日志解析报告。
- 保存 artifact index metadata。

第一版可以只做 metadata 和 review packet，不做真实文件写入。

### 2.3 运行操作

运行操作只能来自 approved template：

- 用户选择 template。
- 用户填写结构化参数。
- 系统校验参数名和范围。
- 后端只处理 template 渲染结果。

不允许用户输入任意 shell 命令，也不允许把 prompt 拼成 shell。

### 2.4 危险操作

危险操作默认禁止，后续即使实现也必须单独 review：

- 删除目录或文件。
- 覆盖已有结果目录。
- 修改权限。
- 清理 conda/venv/environment。
- 删除仓库代码。
- 删除共享数据集。
- 删除凭据目录。
- 跟随 symlink 删除。
- 执行来自用户输入的任意 shell。

v0.11 第一批只做“删除 dry-run review packet”，不做真实删除。

## 3. 删除请求边界

删除是本阶段最需要单独设计的风险点。

第一批只允许表达：

- 谁请求删除。
- 请求角色是什么。
- 目标路径是什么。
- 目标路径属于哪个 workspace root。
- 是否是 dry-run。
- 预览会影响哪些文件类型。
- 是否包含禁止路径、环境目录、代码目录、凭据目录、共享数据、symlink。
- 需要用户输入什么确认短语。

第一批不做：

- 不删除文件。
- 不移动到 trash。
- 不调用 filesystem remove。
- 不跟随 symlink。
- 不执行 `rm`。

## 4. 删除安全规则

后续实现 `DeleteReviewPacket` 时，必须满足：

- `dry_run` 必须为 `true`。
- 删除目标必须位于 approved workspace root 下。
- 目标路径不能是 `/`、`/home`、`/data`、`/opt`、`/usr`、仓库根目录、环境目录、
  `.ssh`、凭据目录或共享数据集根目录。
- 目标路径不能包含 `..`。
- 目标路径不能是空字符串。
- 目标路径不能是 workspace root 本身。
- 如果包含 symlink，review packet 必须标记为 blocked。
- 如果确认短语不等于规范化目标路径，review packet 必须标记为 not confirmed。
- 第一批只返回 review packet，不执行删除。

真实删除只有在后续版本中才允许考虑，并且必须先实现 trash、保留期、二次确认和审计。

## 5. v0.11 第一批目标

第一批只实现可测试 metadata 和 renderer：

- `LabAccountRole`
- `SafeOperationType`
- `SafeOperationRequest`
- `SafeOperationPolicy`
- `DeleteReviewRequest`
- `DeleteReviewPacket`
- `validate_safe_operation_request`
- `validate_delete_review_request`
- `render_delete_review_packet`

第一批测试必须证明：

- `readonly` 可以读文件，但不能请求删除 preview。
- `lab_user` 可以请求自己 workspace 下的 delete dry-run preview。
- `lab_root` 可以 review 所有 workspace，但仍不能绕过 dry-run。
- 非 dry-run delete request 被拒绝。
- 禁止路径被拒绝。
- 路径穿越被拒绝。
- 缺少确认短语时 review packet 不可执行。
- renderer 明确 `deletion_executed: false`。

## 6. 实现入口

公开路线：

- `docs/upgrade/v1.0-internal-preview-roadmap.md`

本地实现计划和新窗口复制提示词：

- 保存在 `docs/superpowers/plans/*.md` 和 `docs/upgrade/local-*.md`。
- 这些文件被 git 忽略，不提交到 GitHub。

学习总结：

- `docs/upgrade/learning-summary-v0.11-safe-operations.md`

## 7. 完成标准

v0.11 只有在以下条件满足后才算完成：

- 有 C++ metadata 和校验测试。
- 有删除 dry-run review packet renderer。
- 所有删除相关输出都显示 `deletion_executed: false`。
- 没有真实删除、没有 trash move、没有 filesystem remove、没有 shell。
- 全量 `ctest` 通过。
- 文档解释清楚 root 角色也不能绕过危险操作确认。
