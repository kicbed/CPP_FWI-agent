# v0.11 安全操作策略学习总结

## 1. 为什么需要 v0.11

v0.10 已经把单服务器账号接入准备做成了 metadata/profile/template/review packet。下一步如果
要继续靠近真实实验室使用，就一定会遇到“工具能做哪些操作”的问题。

用户给出的现实约束是：项目暂时只给实验室内部使用，不对外发布，所以权限模型不需要做成
复杂 SaaS。实验室账号本身已经有系统权限，root 账号在服务器上确实拥有 root 能力。

但是这不等于工具应该无保护地执行危险操作。真正的风险不是“权限模型不够企业级”，而是：

- 一个误删请求删掉代码。
- 一个清理请求删掉 conda 环境。
- 一个路径拼错删掉共享数据集。
- 一个自动化工具跟随 symlink 删除了不该删的目录。
- 一个 LLM 生成的命令被当成 shell 执行。

所以 v0.11 的目标不是复杂权限系统，而是实验室内部场景下的防误伤策略。

## 2. 设计核心

v0.11 把操作分成四类：

1. 默认安全操作：读文件、列目录、看日志、解析 loss curve、渲染 review packet。
2. 受控写操作：只写当前 job workspace 或明确允许的报告路径。
3. 运行操作：只能来自 approved template，不能来自用户自由文本。
4. 危险操作：删除、覆盖、改权限、清环境、删仓库、删共享数据，默认禁止。

第一批实现只处理 metadata 和 review packet，不执行删除。

核心类型建议：

- `LabAccountRole`：`lab_root`、`lab_user`、`readonly`。
- `SafeOperationType`：表达 read/list/parse/render/run-approved-template/delete-preview 等操作。
- `SafeOperationRequest`：表达谁要做什么操作。
- `SafeOperationPolicy`：表达角色允许做哪些操作。
- `DeleteReviewRequest`：表达一次删除预览请求。
- `DeleteReviewPacket`：表达删除预览结果。

本次实现已经把这些类型落到 `safe_operations` C++ 模块中。它们仍然是 metadata 和
validation helper，不包含删除函数、trash move、shell 执行、凭据读取、服务器连接或
workspace 创建。`SafeOperationPolicy` 让角色和操作 allowlist 分开；`DeleteReviewPacket`
把 reviewable 状态、validation errors、affected file type 预览和所有非执行标志写入结构化
metadata。

## 3. 为什么 root 也要受限制

系统 root 是服务器权限。工具里的 `lab_root` 是应用角色。这两者不能混为一谈。

如果工具把 `lab_root` 映射成“可以直接删除任何路径”，那这个项目就会变成一个危险的自动化
删除入口。即使使用者是自己，也会因为路径写错、复制粘贴错误、LLM 误解指令而造成事故。

更合理的设计是：

- `lab_root` 可以 review 更多东西。
- `lab_root` 可以维护 policy/template。
- `lab_root` 可以看到所有 workspace 的 delete preview。
- 但 `lab_root` 仍然不能绕过 dry-run、路径检查、确认短语和禁止路径规则。

这就是“内部可信账号 + 应用层防误伤”。

## 4. 删除为什么只做 dry-run review packet

删除是不可逆风险最高的操作。第一版不应该实现真实删除，甚至不应该移动到 trash。

第一版应该只回答：

- 请求删除的路径是什么。
- 这个路径规范化以后在哪里。
- 是否在 approved workspace root 下。
- 是否是 workspace root 本身。
- 是否包含路径穿越。
- 是否命中禁止路径。
- 是否缺少确认短语。
- 是否因为 symlink 风险而 blocked。
- 如果未来实现删除，可能影响哪些文件类型。

review packet 必须明确：

```text
deletion_executed: false
trash_move_executed: false
shell_executed: false
```

这样做的好处是：你可以先学习和验证删除安全语义，而不会真的删东西。

## 5. 和 v1.0 的关系

v1.0 需要真实可用的实验室闭环，但真实可用不等于越快执行越好。一个能执行但会误删数据的
系统不能叫 v1.0。

v0.11 是进入 v1.0 前的关键安全层：

```text
v0.10 single-server metadata
-> v0.11 safe operation policy
-> fake lifecycle
-> approved workspace lifecycle
-> first real controlled backend
-> logs/artifacts/audit
-> v1.0
```

如果跳过 v0.11，后续 fake lifecycle 或真实 backend 很容易缺少操作边界。

## 6. 面试和汇报讲法

短 pitch：

我把实验室内部使用场景下的权限问题简化成三类角色，但没有把 root 权限直接等同于危险操作
权限。系统采用 safe operation policy，把读操作、受控写、approved template 运行和删除
这类危险操作分开。删除第一版只做 dry-run review packet，不执行真实删除。

技术深挖：

v0.11 的关键不是复杂 RBAC，而是操作分级和路径安全。`lab_root`、`lab_user`、`readonly`
只是角色输入，真正决定能不能做的是 `SafeOperationPolicy` 和具体操作校验。删除请求必须
保留 dry-run，检查 workspace root、路径穿越、禁止路径、确认短语和 symlink 风险。renderer
必须明确 `deletion_executed: false`，保证第一批实现没有任何真实删除副作用。

实现细节可以这样讲：`SafeOperationRequest` 先判断角色是否允许某个操作；删除 preview
再走 `DeleteReviewRequest` 的专门校验，因为删除风险高于普通读操作。`DeleteReviewPacket`
统一保存校验结果和非执行标志，renderer 只是把这些 metadata 转成稳定文本。这避免了
“review 文案看起来安全，但内部数据其实允许执行”的设计漏洞。

常见追问：

问：为什么实验室内部还要做权限？
答：不是为了防外部攻击者，而是为了防误操作。内部工具最常见事故就是误删代码、环境、
共享数据或别人结果。

问：root 为什么不能直接删？
答：root 在系统上可以删，但工具不应该帮它无确认地删。工具的职责是防误伤，尤其是在
LLM 参与解释用户意图时。

问：为什么不直接做 trash？
答：trash 也是文件移动，仍然有副作用。第一批先做 review packet，把风险识别和确认语义
测试清楚，再考虑后续实现。

STAR 复盘：

Situation：项目已经能生成单服务器 dry-run review packet，但下一步要靠近真实使用时，
必须定义工具能做哪些操作。
Task：需要在实验室内部账号模型下设计安全操作策略，避免误删代码、环境、数据和结果。
Action：把角色简化为 lab_root/lab_user/readonly，把操作分为安全读、受控写、approved
template 运行和危险操作；删除第一版只做 dry-run review packet。
Result：后续实现可以先测试操作策略和删除预览，不会直接引入真实删除风险。
