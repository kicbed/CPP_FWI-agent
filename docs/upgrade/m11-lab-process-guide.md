# M11 实验室真实后端推进流程指南

日期：2026-06-23

状态：流程指南已创建，实验室决策尚未完成。

这份文档回答一个实际问题：如果要把当前 dry-run 科研平台推进到未来可以安全连接
实验室真实后端，下一步应该找实验室确认什么、拿到哪些材料、哪些事情不能做、拿到
材料后代码应该按什么顺序继续。

当前项目仍然只允许 dry-run。本文不会启用 CUDA/MPI、SSH、Slurm、PBS、本地 wrapper、
远程服务器、凭据读取、生产审计存储、任意 shell 执行或 Code Agent 自动写文件。

## 1. 当前结论

现在可以继续推进的不是“接服务器代码”，而是“M11-T1 实验室后端决策流程”。

M11-T1 需要实验室给出一个明确、可审计、可落地的后端决策包。这个包至少要说明：

- 第一个真实后端选什么：local wrapper、SSH、Slurm 或 PBS，只能先选一个。
- 谁批准了这个选择，批准记录在哪里。
- 凭据放在哪里，代码只能引用凭据名称或 secret reference，不能保存密码、token、
  私钥或集群账号。
- 作业 workspace 根目录在哪里，目录如何命名，多久清理，哪些路径绝对不能碰。
- 谁允许提交任务，谁只能查看，谁可以取消任务。
- 作业模板有哪些，用户只能填哪些结构化参数，不能让用户自由拼 shell 命令。
- GPU、MPI 进程数、队列、wall time、并发数、磁盘用量等资源限制是什么。
- 日志、loss curve、模型文件、诊断文件收集到哪里，保留多久。
- 出问题时谁负责、如何停用后端、如何保留审计证据。

这些信息没拿到之前，代码层面只能继续做文档、review preview、dry-run 包和学习总结。

## 2. 推荐推进顺序

### 第一步：内部确认候选后端

先和导师、课题组或平台负责人确认“第一个后端”到底是什么。不要同时推进 local、
SSH、Slurm、PBS 四条线。第一版真实后端应该选实验室最容易管理、最容易审计、最少
绕过现有制度的一条。

选择建议：

- 如果实验室没有统一调度器，只是一台受控工作站，可以考虑 local wrapper，但必须
  先解决命令模板、权限、资源限制和工作目录隔离。
- 如果已经有一台固定远程机器，但没有队列系统，可以考虑 SSH，但必须有主机白名单、
  凭据引用策略、远程 cleanup 和超时取消策略。
- 如果实验室用 Slurm 管集群，优先考虑 Slurm，因为队列、账号、资源和审计通常已经
  有制度基础。
- 如果实验室用 PBS/Torque 系列集群，选择 PBS，但要先确认 qsub/qstat/qdel 的具体
  输出格式和队列规则。

本项目当前最稳妥的原则是：真实后端越接近实验室已有运维制度，后续越容易解释和
上线；越像“自己在代码里偷偷执行命令”，风险越高。

### 第二步：确认批准和责任人

真实后端不是技术人员单方面决定的功能。需要实验室有人对资源、账号、安全和故障
负责。

需要确认：

- 批准人是谁：导师、PI、实验室管理员或集群管理员。
- 批准记录是什么：邮件、issue、会议纪要、内部文档编号或其他可追溯记录。
- 运行责任人是谁：谁处理失败任务、资源占满、用户误提交、日志缺失、磁盘占满。
- 变更窗口是什么：什么时候允许接入、什么时候允许做小规模测试。
- 停用条件是什么：发现异常时谁有权停用真实后端。

没有这些信息，不能进入 M11-T2 的 auth/access control 代码实现。

### 第三步：确认凭据策略

仓库不能保存真实凭据。代码和文档也不应该让开发者把密码、私钥、token、集群账号
复制进来。

需要实验室给出的不是凭据本身，而是凭据策略：

- 凭据放在哪里：例如实验室 secret manager、部署机环境、运维托管路径或平台配置。
- 代码如何引用：例如 `credential_reference = lab-slurm-submit-v1` 这样的引用名。
- 谁能读取凭据：服务账号、管理员、还是用户本人。
- 凭据如何轮换：多久轮换，谁负责，泄露后如何吊销。
- 本地开发环境如何处理：默认不接真实凭据，只使用 dry-run 或 fake secret reference。

这一步的目标是让项目后续只处理“凭据引用”，不处理“凭据内容”。

### 第四步：确认 workspace 和数据边界

真实后端最容易出问题的地方之一是路径。用户输入如果能影响路径，就可能造成覆盖、
泄露、路径穿越或误删。

需要确认：

- workspace root：所有 job 的工作目录必须在这个根目录下。
- job directory 命名规则：建议由系统生成，例如 `job_<date>_<id>`，不要用用户自由文本。
- 清理策略：成功、失败、取消、超时后的目录保留多久。
- 只读输入数据位置：例如 Marmousi、SEG/EAGE 等数据放在哪里，是否允许复制。
- 输出 artifact 类型：日志、loss curve、模型、梯度、配置快照、诊断摘要。
- 磁盘配额：单 job 最大空间、单用户最大空间、总保留空间。
- 禁止路径：用户 home、系统目录、源码仓库、凭据目录、共享数据原始目录。

代码里已经有 workspace path traversal rejection 的基础模型，但真实 backend 前还需要
创建、清理、权限和 artifact indexing 的实现与测试。

### 第五步：确认授权和配额

真实后端必须知道“谁可以提交什么”。否则一个 demo 系统可能变成绕过实验室队列制度
的入口。

需要确认：

- 初始 authorized submitters：第一批允许提交的人。
- 角色：submitter、viewer、operator、admin 分别能做什么。
- 提交限制：每人并发 job 数、每天 job 数、GPU 数、MPI 进程数、wall time。
- 取消权限：用户只能取消自己的任务，还是 operator 可以取消所有任务。
- 审批升级：谁可以把新用户加入 authorized submitters。
- 失败处理：超配额、未授权、模板不匹配时返回什么错误。

这些信息对应后续 M11-T2 的 auth/access control 实现。

### 第六步：确认 approved job templates

真实执行不能把用户文本变成 shell 命令。后端只能接受实验室批准过的 job template
和结构化参数。

一个 FWI job template 至少要定义：

- template id 和版本。
- 固定入口：例如受控脚本、调度器模板或内部 wrapper 名称。
- 允许参数：频带、迭代次数、步长策略、模型 ID、数据集 ID、GPU 数、MPI 数。
- 参数范围：最小值、最大值、枚举值、默认值。
- 禁止字段：任意 command、任意 environment、任意 output path、任意 extra flags。
- 输出 artifact 约定：日志、loss curve、模型文件、config snapshot。
- dry-run 渲染：上线前能预览，但不能提交。

只有模板和参数范围明确后，才能考虑 M11-T4 的 submission/status/cancellation。

### 第七步：确认监控、取消和失败语义

真实 job 一定会失败，所以不能只设计 happy path。

需要确认：

- 提交成功后如何拿 job id。
- 如何查询 queued、running、succeeded、failed、cancelled、timeout。
- 取消命令或 API 的权限边界是什么。
- 后端不可达时如何重试，重试几次，多久超时。
- 任务状态和日志冲突时谁为准。
- 部分 artifact 丢失时如何标记。
- 用户看到的是原始调度器错误，还是标准化后的错误码和解释。

这些信息决定 M11-T4 和 M11-T5 的测试设计。

### 第八步：确认审计和事故处理

审计不是最后补的日志，而是真实后端的核心边界。

需要确认：

- 每个 job 记录哪些字段：request id、job id、user、backend、template、参数摘要、
  状态变化、artifact、operator note。
- 审计日志写到哪里：文件、数据库、Redis、对象存储或实验室已有系统。
- 保留多久：例如 90 天、180 天、一年或按项目周期保留。
- 谁可以查看和导出审计记录。
- 失败或安全事件后如何冻结记录。
- 如何证明某个 job 是由哪个用户、哪个模板、哪个参数包触发的。

代码里现在只有 metadata-only audit event 和 in-memory audit log。生产 audit store 是
后续 M11-T7 的内容，不应在 M11-T1 之前实现。

## 3. 可以直接发给实验室的确认问题

下面这组问题可以直接用作会议 agenda 或邮件提纲。

### 后端选择

- 实验室第一个允许接入的平台是什么：local wrapper、SSH、Slurm 还是 PBS？
- 是否只允许一个小规模测试队列或测试机器？
- 是否有推荐的 first-user 或 first-dataset，例如 Marmousi dry-run 后再小规模真实跑？

### 凭据和账号

- 后端提交使用个人账号、服务账号，还是实验室统一代理账号？
- 凭据由谁保管，代码应如何引用凭据？
- 凭据是否允许出现在环境变量、配置文件或 CI 中？
- 凭据泄露或误用时如何吊销？

### workspace 和数据

- job workspace 根目录是哪一个？
- 是否允许系统创建和删除 job 目录？
- 输入数据从哪里读取，是否只读？
- 输出 artifact 保存多久，是否有空间配额？

### 授权和配额

- 谁是第一批 authorized submitters？
- 谁有 operator 权限，可以查看、取消、备注 job？
- 单用户 GPU、MPI、wall time、并发 job 限制是多少？
- 未授权或超配额时希望用户看到什么错误说明？

### 作业模板

- 第一个 approved job template 是哪个算法：FWI、frequency extrapolation、post-stack
  inversion，还是更小的 sanity-check job？
- 允许哪些参数，参数范围是什么？
- 禁止用户传哪些字段？
- dry-run packet 需要给 operator 看哪些内容才算可批准？

### 监控和审计

- 调度器或 wrapper 的 job id 如何获取？
- 状态轮询频率和超时时间是多少？
- 日志和 loss curve 从哪里收集？
- 审计记录保存在哪里，保留多久？
- 事故时谁负责停用后端和导出审计？

## 4. 拿到实验室材料后的代码推进顺序

拿到 M11-T1 决策包以后，不要马上改 runtime guard。后续代码顺序应该是：

1. 先实现 M11-T2：身份认证和访问控制。测试必须证明未授权用户无法提交。
2. 再实现 M11-T3：workspace 创建和清理。测试必须证明路径不能逃逸 approved root。
3. 再实现 M11-T4：只针对选定后端做提交、状态轮询、取消。测试必须覆盖失败和超时。
4. 再实现 M11-T5：日志收集和 artifact indexing。测试必须覆盖 artifact 缺失和异常日志。
5. 再实现 M11-T6：loss curve 和输出模型可视化。测试必须证明展示层不触发执行。
6. 再实现 M11-T7：生产审计持久化。测试必须证明 request、user、template、状态变化可追溯。
7. 最后才考虑改变 `validate_backend_enabled` 的真实后端允许逻辑，而且只允许已经完成
   上述控制的那个后端。

这条顺序很重要。它保证系统先有“谁能做、在哪里做、做什么模板、如何记录、如何停止”
这些边界，再有“真的提交任务”。

## 5. 不应该做的事

在 M11-T1 没完成之前，不应该做这些事：

- 不要在代码里写死 SSH host、用户名、密码、私钥路径或 token。
- 不要把用户输入拼成 shell 命令。
- 不要添加 `system()`、`popen()` 或等价的自由命令执行路径。
- 不要新增真实 `sbatch`、`qsub`、`ssh`、`mpirun`、`srun` 调用。
- 不要创建远程目录或删除远程文件。
- 不要把 Code Agent 从只读改成自动应用 patch。
- 不要因为有 `command_preview` 就把它当成可以执行的命令。
- 不要把 M11 preflight metadata 当成 runtime approval。

这些限制不是保守过度，而是科研计算系统上线前必须有的安全顺序。

## 6. 和现有文档的关系

相关文档：

- `docs/upgrade/m11-lab-backend-decision-package.md`：记录 M11-T1 决策包模板。
- `docs/upgrade/test-report-m11-preflight.md`：记录 metadata-only preflight 的测试证据。
- `docs/upgrade/m11-preflight-completion-audit.md`：说明 M11 preflight 为什么可以收口。
- `docs/upgrade/learning-summary-m11-preflight.md`：解释 preflight 模型如何学习和面试复盘。
- `docs/upgrade/test-report-v0.9.md`：记录 non-executing backend readiness review 的测试证据。
- `docs/upgrade/learning-summary-v0.9.md`：解释 readiness report、submission packet、audit preview
  和 workspace/artifact plan。

本文是操作流程层：告诉你下一步找实验室要什么、怎么问、拿到以后代码怎么排队推进。

## 7. 学习总结：为什么这一步重要

### 7.1 解决的问题

上一阶段已经完成了 M11 preflight 和 v0.9 backend readiness review。它们能证明系统
可以用结构化 metadata 表达审批、授权、workspace、模板和审计，也能把这些信息渲染
成 operator 可以 review 的文本。但这些文档仍然偏“代码侧”和“产品侧”，还缺一份
面向真实实验室推进的流程指南。

这份文档解决的是项目落地前的组织和安全问题：谁批准、谁负责、凭据怎么管、数据放
哪里、用户权限怎么划、资源上限是多少、失败后谁处理、审计保存多久。科研计算平台
不是只要能 `sbatch` 或 `ssh` 就算完成。真正难的是把真实资源接入一个可控、可追溯、
可停用的系统。

### 7.2 实现方式和设计取舍

这次没有新增 C++ 代码，因为当前瓶颈不是缺一个函数，而是缺实验室决策输入。继续写
后端适配器会绕过 M11-T1 的前置条件，风险比收益大。更合理的做法是把流程文档写清楚，
让后续实现有明确边界。

数据流可以理解成：

实验室流程确认 -> M11 决策包 -> `BackendApprovalDecision` metadata ->
`BackendPreflightPackage` -> `BackendPreflightReport` -> M11-T2 到 M11-T7 实现。

这里有一个关键取舍：文档把“选后端”和“启用后端”分开。实验室可以先选择 Slurm 或
PBS，但 runtime guard 仍然必须保持关闭，直到认证、workspace、提交/状态/取消、artifact、
可视化、审计都实现并通过测试。这样可以避免“批准字段存在，所以代码直接执行”的错误。

### 7.3 关键文件、测试和资源

`docs/upgrade/m11-lab-process-guide.md` 是实验室推进入口，负责说明流程、问题清单、
代码推进顺序和禁止事项。

`docs/upgrade/m11-lab-backend-decision-package.md` 是决策包模板，负责记录 M11-T1 是否
真的具备完成条件。

`docs/upgrade/milestones.md` 继续把 M11-T1 标记为未完成，防止文档模板被误读成批准。

`research/include/agent_rpc/research/server_job.h` 和 `research/src/server_job.cpp` 是现有
metadata/preflight 模型的代码位置。它们现在只做验证、渲染和内存 metadata 操作，不做
真实执行。

`tests/test_server_job.cpp` 是未来代码继续推进时最重要的保护网。当前它已经保护 runtime
guard、approval validation、authorized submitters、workspace traversal rejection、audit
event/log validation 和 readiness rendering。后续 M11-T2 到 M11-T7 应该继续围绕这个
边界扩展测试。

### 7.4 安全和产品边界

CUDA/MPI：当前不接真实 CUDA/MPI，不运行 `mpirun`、`srun` 或任何 GPU 作业。文档里提到
GPU/MPI 只是为了要求实验室给出资源限制。

SSH：当前不接 SSH，不保存 host、用户名、私钥或远程路径。SSH 只是候选后端之一。

Slurm/PBS：当前不执行 `sbatch`、`squeue`、`scancel`、`qsub`、`qstat`、`qdel`。Slurm/PBS
只是未来可能选择的后端，需要先有队列、账号、模板和审计政策。

远程执行和 shell 执行：当前不允许用户文本变成 shell 命令。未来也必须走 approved
template 和结构化参数，不允许自由命令。

Code Agent 写权限：Code Agent 仍然只读，可以给 patch 建议，但不能自动应用未确认的
patch。真实后端接入不能借 Code Agent 绕过人工确认。

### 7.5 调试和验证证据

本次是文档流程工作，没有新增生产代码，所以没有 TDD RED 编译失败。对应的验证是：

- 先确认工作区干净。
- 新增流程文档和升级索引。
- 运行 `cmake --build build -j2`，确认没有构建系统或已有代码回归。
- 运行 `ctest --test-dir build --output-on-failure`，确认 26 个现有测试仍然通过。
- 运行 `git diff --check`，确认文档 diff 没有 whitespace 问题。

这类验证证明的是：文档推进没有夹带运行时能力变更，也没有破坏现有测试基线。

### 7.6 面试怎么讲

短 pitch：

我把一个 FWI-first 科研多智能体平台推进到真实后端前的实验室流程阶段。这个阶段没有
急着接 Slurm 或 SSH，而是先把实验室批准、凭据策略、workspace、授权、配额、模板、
artifact 和审计要求整理成可执行流程，确保后续真实执行有安全边界。

技术深挖版：

项目已有 `BackendApprovalDecision`、`BackendPreflightPackage` 和 readiness rendering。
我补的是组织流程到工程实现之间的桥：实验室必须先给出 backend decision package，
代码再把它映射为 metadata validation，然后按 M11-T2 到 M11-T7 的顺序实现 auth、
workspace、submission/status/cancellation、artifact collection、visualization 和 audit。
关键点是 runtime guard 不因 metadata 完整就打开，真实后端只在全部控制和测试存在后
才允许被启用。

常见追问和回答：

问：为什么还不接 Slurm？
答：因为 Slurm 接入不是只调用 `sbatch`。必须先知道账号、partition、队列策略、凭据
引用、模板、配额、状态映射、取消权限、日志收集和审计保留。否则系统可能绕过实验室
资源制度。

问：为什么要先写流程文档？
答：真实后端的风险来自组织边界和运维责任，不只是代码。流程文档让导师、管理员、
开发者对“谁批准、谁能提交、出了事谁处理、日志怎么追踪”达成一致。

问：如何防止命令注入？
答：用户永远不能提交自由 shell。后续只能选择 approved template，并填结构化参数；
参数有类型、范围和枚举限制，最终由受控 backend adapter 渲染。

STAR 复盘：

Situation：项目已经有 dry-run 实验规划和 backend readiness review，但还不能真实提交
实验室作业。
Task：需要明确实验室下一步必须提供什么，避免开发者直接接 SSH/Slurm/PBS。
Action：整理 M11 实验室流程指南，把后端选择、凭据、workspace、授权、模板、资源、
监控、审计和事故处理全部写成推进清单，并保持 runtime guard 关闭。
Result：项目有了从 v0.9 review 到 M11-T1 实验室决策的清晰路径，后续可以带着文档去
找实验室确认，而不是靠口头假设写真实执行代码。
