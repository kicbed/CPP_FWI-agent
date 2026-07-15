# Scientific Runtime P1.1a 持久任务内核基础

<!-- scientific-runtime-p1-task-store: v1 -->

- 对应决策：`D-003`
- 实现切片：`P1.1a`（父工作项 P1.1 仍为 **Partially implemented**）
- 实现状态：**Verified — persistent foundation only**
- 公共合同：继续使用 `contracts/scientific_runtime/v1/`

本子切片建立 SQLite 权威任务身份、不可变记录和无执行副作用的服务层。它不接入 Web、
Dataset Catalog、Deepwave Adapter 或 Worker，也没有开放 `submit`/`Queued` 入口。

后续状态：P1.1b 已在 `docs/architecture/SCIENTIFIC_RUNTIME_P1_REGISTRY.md` 实现并验证
Dataset Catalog/Algorithm Registry、SQLite v2 升级和批准预算持久行；这不改变本文对历史
P1.1a 切片的范围描述，submit/Adapter/`Queued` 仍未实现。

## 1. 文件与边界

| 文件 | 责任 |
|---|---|
| `scientific_runtime/migrations/0001_task_store.sql` | 首版表、索引、外键、唯一约束和 append-only trigger |
| `scientific_runtime/task_store.py` | WAL migration、短事务、SQLite 读写和 TaskStore Protocol |
| `scientific_runtime/task_service.py` | JSON Schema、plan hash、修订/状态/关系和稳定领域错误 |
| `tests/test_scientific_runtime_task_service.py` | 持久性、幂等、并发、回滚和无提交边界测试 |

`scientific_runtime_contracts` 继续保持无存储、无调度、无提交；旧 A2A Memory/Redis store 和
FWI 运行目录都不是本 Task Store 的真值来源。

## 2. 内部任务聚合

P0 没有定义第八个公共 `Task` JSON Schema。本切片因此只增加内部 `TaskSnapshot`：

```text
task_id + project/principal + current status
        + current immutable draft revision
        + optional current plan
        + optional current approval
        + created/updated timestamps
```

完整 `TaskDraft`、`PlanGraph`、`ApprovalDecision` 和 `RunEvent` 仍按公共 Schema 验证并保存
完整 JSON；常用身份、revision、hash、status 和 sequence 另列索引。读取时重新计算文档
hash 并核对索引身份，损坏或漂移时 fail closed。调用者修改返回的 Python 对象不会覆盖
数据库记录。

任务聚合可在草稿阶段提前获得服务端生成的不可变 `task_id`，但这不表示任务已经提交。
后续 API 仍可在草稿接口只返回合同规定的 `TaskDraft`，到 submit 成功时再公开同一 task ID。

## 3. SQLite 真值与事务

首迁移包含：

- `tasks`：当前聚合指针和状态；task/project/principal/created identity 不可修改；store 与
  migration trigger 都拒绝让新任务直接从 runtime 状态开始；
- `draft_revisions`：只追加 `(task, revision)`；
- `plans` 与 `plan_node_idempotency`：不可变计划及 plan 内节点幂等键；
- `approvals`：绑定精确 plan ID/hash 的只追加决定；
- `run_events`：全局唯一 event ID、每任务唯一单调 sequence、只追加 JSON；
- `idempotency_records`：按 project/principal/operation/key 保存请求 hash 和 task/result 映射；
  当前只实现并验证 create；`submit_task` 值只为后续 migration 兼容预留，不能算已实现；
- `schema_migrations`：migration 版本与源码 checksum。

数据库必须是专用私有目录中的绝对文件路径，不能使用 `:memory:` 或穿过 symlink；父目录
不得向 group/other 开放。首次打开通过数据库文件 advisory lock 串行化；首次迁移只接管无
用户对象、`user_version=0` 且 application ID 为 0 的空库，随后写入专用 `SRT1` application ID；重开时核对 migration checksum、live schema
manifest、`quick_check` 和 `foreign_key_check`，拒绝误指向其他 SQLite 库或结构漂移。初始化要求
WAL，所有连接启用 foreign keys、
`synchronous=FULL` 和有限 `busy_timeout`，所有写操作使用短 `BEGIN IMMEDIATE` 事务。每个
操作新建自己的连接，不跨线程共享 `sqlite3.Connection`。migration 表、checksum 与
`PRAGMA user_version` 必须一致，数据库文件权限收紧为 `0600`；有界写锁超时转换成稳定的
store unavailable 错误。

运行数据库由部署管理员放在仓库和 `FWI_RUN_ROOT` 之外的专用持久目录；本切片没有把某个
机器路径写进公共合同，也不在 Git 中产生数据库。Git 与 Docker 构建上下文都排除 SQLite/
DB 文件及其 WAL/SHM sidecar。

## 4. 创建、修订、计划与批准

- 创建先验证 TaskDraft，要求 revision 1，由服务端生成 task ID；同作用域、同 key、同请求
  在生成新 ID/时间前返回原 task identity，不重复插入；同 key、不同请求原子冲突。
- HTTP `Idempotency-Key` 的公共格式仍未在 P0 标准化。P1.1 只施加 1–255 字符、无控制字符
  的本地存储安全上限，不复用 PlanGraph 节点 key 正则，也不把该字段塞进公共对象。
- create idempotency 通过 task/project/principal 复合外键和重放时 scope 复核防止跨租户映射；
- 修订用 `expected_revision` 比较并交换，只允许当前 revision + 1；旧 revision 保留。新修订
  清除 current plan/approval 指针，但历史记录继续可审计。
- plan 必须通过 Schema 和 canonical plan hash，绑定当前 draft revision，并与 draft 的 task
  type、算法、参数、资源和输入数据身份一致；node/dependency/idempotency key 还要唯一且无环；
  plan ID 不允许指向不同内容。
- approval 必须通过 Schema，并绑定当前 plan ID/hash；旧决定不被覆盖。精确旧请求的重放
  不会重新激活已被新计划或新决定取代的 current 指针。所有读写按 project/principal 隔离，
  Guided approval actor 必须等于当前 principal；Agent delegation 仍未启用。

## 5. 事件与执行边界

TaskService 对已进入 runtime 的事件执行 Schema、task ID、状态转换、sequence 和事件语义
检查，并在同一事务更新 task snapshot 和 append event。它额外拒绝 Schema 尚不能表达的
不一致组合，例如 `node_failed + Succeeded`、成功事件携带 error、缺少 node/error/progress，
或 `completed > total`；node 还必须存在于 current plan，fingerprint 的算法、seed、device 和
input hashes 必须与该节点一致，同一节点的完整 fingerprint 在后续事件中不得漂移。runtime
状态必须有从 `task_queued` 开始的连续事件历史，task snapshot 状态必须等于最新事件状态；P1
单节点边界要求 `node_succeeded` 同时进入 `Succeeded`，多节点生命周期留给 P3。

P1.1 明确保留以下入口：

- `task_queued`：必须等 Dataset Catalog/Algorithm Registry、当前 draft/plan/approval、批准
  预算、完整 Gate 和 submit idempotency 能在同一事务读取和写入后实现；
- waiting/retrying/cancel：属于 P2 lease、有限重试和 Worker 取消语义；
- checkpoint：恢复语义属于 P2，P1.1a 不允许借 checkpoint 进入 Waiting；
- terminal 状态：只允许幂等读取，不追加“同状态转换”事件。

因此本切片不能让 TaskService 或底层 runtime transition 从 Draft/NeedsInput/
AwaitingApproval 进入 Queued，也不会创建 FWI job。测试中的 Queued 记录只通过临时测试库
直接构造，用于验证 post-submit 事件持久机制，不是产品提交路径。

## 6. 已验证与待实现

33 项聚焦测试覆盖 WAL/migration 重开、并发首次初始化、application/schema/integrity 一致性与误接管拒绝、私有
路径/文件权限、持久 JSON hash 和聚合关系损坏、Schema 失败和中途写入回滚、不可变 task ID、
创建幂等/复合 scope/隔离、并发同 key 收敛、draft CAS、plan/approval hash/actor 绑定和历史
失效、有界锁错误、事件顺序/过滤/append-only、状态与事件原子回滚、单节点 terminal 及完整
fingerprint 稳定性，以及 P1.1a 无 Queued 入口。

本子切片结束时尚未实现 Catalog/Registry；后续 P1.1b 已补齐不可变注册快照和批准预算
持久行。当前仍未实现：批准预算消费、submit 幂等读写与原子事务、Deepwave Adapter、真实
Worker handle、Web/API、运行中取消、lease、retry、reconciliation 和 SSE。
P1 后续 submit 还必须增加“当前 runtime 只支持单个 FWI 节点”的 capability guard；P0 v1
合同允许多节点，但 DAG 调度属于 P3，不能把任意 Gate-pass 多节点计划提前入队。
