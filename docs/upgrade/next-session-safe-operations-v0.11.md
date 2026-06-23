# 下一窗口交接：v0.11 安全操作策略

日期：2026-06-23

用途：新开窗口后，把这份文档作为主要提示词来源，继续实现 v0.11。

## 背景

当前项目已经完成 v0.10 单服务器账号接入准备：

- `SingleServerProfile`
- `SingleServerJobTemplate`
- `SingleServerReviewRequest`
- dry-run review packet renderer

下一步不是直接真实删除或真实执行，而是做实验室内部账号场景下的 safe operation policy。

实验室内部暂时不对外发布，所以权限模型可以简单：

- `lab_root`
- `lab_user`
- `readonly`

但是危险操作必须严格控制。root 角色也不能绕过删除 dry-run preview、多次确认和路径保护。

## 新窗口可直接复制的提示词

```text
你现在在仓库 /root/projects/project/agent-communication-main-v2。

请先阅读：
- docs/upgrade/README.md
- docs/upgrade/milestones.md
- docs/upgrade/version-roadmap.md
- docs/upgrade/upgrade-log.md
- docs/upgrade/single-server-backend-v0.10.md
- docs/upgrade/test-report-v0.10.md
- docs/upgrade/learning-summary-v0.10.md
- docs/upgrade/safe-operations-v0.11.md
- docs/upgrade/next-session-safe-operations-v0.11.md
- docs/upgrade/learning-summary-v0.11-safe-operations.md
- docs/superpowers/plans/2026-06-23-safe-operations-v0.11.md

当前项目按实验室内部使用场景处理，不做公网平台，不做复杂多租户。
权限模型先按 lab_root、lab_user、readonly 三类处理。

下一步请按计划实现 v0.11 第一批：
- LabAccountRole
- SafeOperationType
- SafeOperationRequest
- SafeOperationPolicy
- DeleteReviewRequest
- DeleteReviewPacket
- validate_safe_operation_request
- validate_delete_review_request
- render_delete_review_packet

范围限制：
- 只做 metadata、validation 和 dry-run review packet。
- 不做真实删除。
- 不移动到 trash。
- 不调用 filesystem remove。
- 不执行 rm、system、popen 或任意 shell。
- 不连接服务器。
- 不读取真实凭据。
- 不创建或删除 workspace。
- 删除相关输出必须明确 deletion_executed: false。
- root 角色也不能绕过 dry-run 和确认边界。

执行规则：
- 先运行 git status --short。
- 使用 TDD：先写失败测试，再写最小实现。
- 每次只做一个小任务或一个紧密相关小批次。
- 代码改动至少跑 cmake --build build -j2 和 ctest --test-dir build --output-on-failure。
- 文档改动跑 git diff --check。
- 更新 docs/upgrade/upgrade-log.md。
- 如新增架构/技术能力/学习价值，更新 docs/upgrade/career-notes.md。
- 提交到 git。
- 最后告诉我改了什么、测试结果、commit hash、下一步建议，并用中文写学习总结。
```

## 本窗口已经完成的内容

- 新增 v0.11 安全操作策略设计。
- 新增 v0.11 实现计划。
- 新增本交接提示词文档。
- 新增 v0.11 学习总结。
- 更新升级索引和日志。

## 下一步实现时最重要的边界

不要因为用户说“root 有 root 权限”就在工具里允许危险删除。系统层面的 root 权限和工具
层面的安全策略要分开。工具必须防误删，尤其不能删除：

- 仓库代码。
- conda/venv/environment。
- `.ssh`。
- credentials/secrets。
- shared dataset。
- 其他人的 workspace。
- workspace root 本身。
- 任意不在 approved workspace root 下的路径。

第一版只生成删除预览，不执行删除。
