# Scientific Runtime P0 合同与执行门规范

<!-- scientific-runtime-p0-contracts: v1 -->

- 对应决策：`D-003`
- 合同版本：`1.0.0`
- JSON Schema 方言：Draft-07
- 实现阶段：P0.1
- 运行时状态：**P0 合同参考实现；P1.1a TaskStore、P1.1b Registry 与 P1.2a 固定 Deepwave
  Adapter 已验证；submit/API/调度仍未实现**

本文是七类公共对象、规范化、状态转换、确定性执行门、API 草案和 Algorithm Adapter v1
的 P0 规范。它只覆盖已注册 `marmousi_94_288` 到 Deepwave 二维声学 FWI artifact 的最小
链路，不能被解释为通用科研平台或持久任务服务已经可用。

## 1. 文件与责任

| 合同 | 文件 | P0 责任 |
|---|---|---|
| `DatasetRef` | `contracts/scientific_runtime/v1/dataset-ref.schema.json` | 不可变数据身份、hash、类型、metadata、lineage 与权限范围 |
| `AlgorithmManifest` | `contracts/scientific_runtime/v1/algorithm-manifest.schema.json` | 版本 pin、参数/I/O、资源上限、安全声明和 Adapter 能力 |
| `TaskDraft` | `contracts/scientific_runtime/v1/task-draft.schema.json` | 目标、数据、算法候选、参数、缺失项、建议和解释性置信度 |
| `PlanGraph` | `contracts/scientific_runtime/v1/plan-graph.schema.json` | typed 节点、依赖、资源、副作用、风险、验收条件、幂等键和 plan hash |
| `ApprovalDecision` | `contracts/scientific_runtime/v1/approval-decision.schema.json` | 对不可变 plan hash 的限域、限时、可审计批准或拒绝 |
| `RunEvent` | `contracts/scientific_runtime/v1/run-event.schema.json` | 单调事件序号、任务/节点状态、进度、checkpoint、错误和执行指纹 |
| `ArtifactManifest` | `contracts/scientific_runtime/v1/artifact-manifest.schema.json` | artifact 身份、受控位置、hash、展示、metrics、执行指纹和 lineage |

`common.schema.json` 只提供共享类型，不是第八类公共对象。
`scientific_runtime_contracts/validation.py` 是无存储、无调度、无提交副作用的参考实现，供
Schema 测试和 P1 实现对照。

## 2. 严格字段与版本演进

1. 所有公共对象必须包含精确的 `schema_version: "1.0.0"`。
2. 顶层 `additionalProperties` 为 `false`；未知字段不能被静默忽略。
3. 实验性字段只能放进 `extensions`，键必须是至少两段的命名空间，如 `org.example`；
   扩展不能改变核心字段语义或绕过 Gate。
4. consumer 遇到未知版本必须拒绝，不能按“最接近版本”猜测或静默降级。
5. patch 版本只允许修正文档、错误文本或不改变接受集合的实现缺陷；改变字段接受集合至少
   增加 minor 版本，并提供显式迁移器和双版本测试。
6. 删除/重命名字段、改变既有字段语义或 hash 输入规则必须增加 major 版本。已批准的旧
   `plan_hash` 不跨 major 迁移复用。
7. JSON Schema Draft-07 是为复用仓库现有 `jsonschema==3.2.0`、避免 P0 新增依赖而选择的
   方言；它和 Scientific Runtime 的 `schema_version` 是两条独立版本轴。

JSON Schema 只验证单对象结构。注册状态、权限、hash 一致性、I/O 兼容、DAG 无环、批准
有效期等跨对象规则必须由确定性 Gate 检查，不能交给 LLM 置信度。

## 3. PlanGraph 规范化和 hash

`plan_hash` 绑定 PlanGraph 的全部可执行语义。v1 规范化步骤固定为：

1. 先按 `plan-graph.schema.json` 验证；
2. 深拷贝对象并移除派生字段 `plan_hash`；
3. 对所有字符串和对象键执行 Unicode NFC；若两个键归一化后相同则拒绝；
4. v1 的 hash-bound PlanGraph 只接受整数数值，遇到浮点数（包括扩展内浮点）拒绝；
5. 对象键按 Unicode code point 排序；数组顺序保持不变；
6. UTF-8 编码，JSON 不转义非 ASCII，分隔符为 `,`/`:`，不含无意义空白；
7. 计算 SHA-256，输出小写 `sha256:<64 hex>`。

节点、依赖顺序、算法版本、数据身份/hash、参数、资源、副作用、风险、验收条件、
idempotency key、draft revision 或 extensions 任一变化都会改变 hash。变更后旧
`ApprovalDecision` 自动失效，必须重新验证并批准。把 hash 字段本身排除可避免自引用。

浮点禁令只针对 v1 PlanGraph hash 输入，不禁止 TaskDraft 的解释性置信度或 artifact metrics。
未来如果计划需要科学浮点参数，必须在新合同版本中定义跨语言一致的十进制定点/字符串表达
或等价规范，不能直接依赖某个语言的默认浮点 JSON 格式。

## 4. 状态机与服务端转换

规范状态如下：

```text
Draft -> NeedsInput -> AwaitingApproval -> Queued -> Running
                                              |       |
                                              |       +-> Waiting -> Running
                                              |       +-> Retrying -> Running
                                              v
                              Succeeded | Failed | Cancelled
```

允许的服务端转换：

| 当前状态 | 允许目标 | 必须满足 |
|---|---|---|
| `Draft` | `Draft`, `NeedsInput`, `AwaitingApproval` | revision 单调增加；进入 AwaitingApproval 时 `missing_fields=[]` |
| `NeedsInput` | `NeedsInput`, `Draft`, `AwaitingApproval` | 仅显式修改生成新 revision；缺失项清空后才能待批准 |
| `AwaitingApproval` | `AwaitingApproval`, `Queued`, `Cancelled` | Queued 前执行本文件第 5 节全部 Gate；放弃草稿可进入 Cancelled |
| `Queued` | `Running`, `Failed`, `Cancelled` | P1 仅定义身份和事件；取消到 Worker 与恢复属于 P2 |
| `Running` | `Running`, `Waiting`, `Retrying`, `Succeeded`, `Failed`, `Cancelled` | 状态/事件写入必须由服务端验证 |
| `Waiting` | `Running`, `Failed`, `Cancelled` | lease/checkpoint 规则在 P2 定义 |
| `Retrying` | `Running`, `Failed`, `Cancelled` | 有限重试预算在 P2 定义 |
| terminal | 同一 terminal 状态的幂等读取 | 不得返回非 terminal 状态 |

P0 只定义规则和参考 Gate，不实现状态存储。P1 的 SQLite TaskService 必须在同一事务内读取
当前 draft/plan/approval/registry snapshot、执行 Gate、写入 `Queued` 和 append event；不能先
提交 Worker 再补数据库状态。

## 5. 确定性执行门

进入 `Queued` 前必须同时通过以下检查；任何一项失败都返回稳定错误码且不产生执行副作用：

1. `TaskDraft`、`PlanGraph`、`ApprovalDecision` 和使用到的 Registry 对象 Schema 合法；
2. PlanGraph 自身 hash 正确，approval 的 `plan_id`/`plan_hash` 与当前 plan 完全一致；
3. decision 为 approved，尚未过期、不早于 plan、决定时间不在未来，actor 与提交主体匹配，
   approval 的任务预算未耗尽；P0/P1 Guided 阶段不激活 Agent delegation；
4. draft 当前状态为 `AwaitingApproval`，plan 指向当前 draft revision，draft/plan 无未解决
   字段；P0 最小 FWI plan 的 task type、每个节点的算法、参数和资源必须与该 draft 一致；
5. 每个 dataset `id@version` 已注册，类型和 content hash 一致；当前 principal/project 具有
   `execute` 权限；数据在 approval scope 内；
6. 每个 algorithm `id@version` 已注册、版本已 pin、manifest 合法且 allowlisted，并在
   approval scope 内；
7. task type、参数 Schema、输入/输出 port 类型兼容；
8. 所有 dependency 指向存在节点，DAG 无环，node ID 与 idempotency key 唯一且格式合法；
9. 资源不超过公共 Schema、AlgorithmManifest 和 ApprovalDecision 三层上限；
10. 节点副作用同时被 AlgorithmManifest 声明和 ApprovalDecision 授权；
11. Agent confidence 只保留解释用途，不参与上述任何“放行”判断。

参考实现返回 `GateViolation(code, path, message)`；P1 可以扩展错误元数据，但不能减少这些
Gate 或把失败降级为 warning。

## 6. 可复现执行指纹

`RunEvent` 和 `ArtifactManifest` 共用 `reproducibility_fingerprint`。可复现模式最少包含：

- algorithm 与 adapter 版本；
- Git commit、tree、dirty flag；dirty 时必须有精确 diff hash 或源码归档 hash；
- container image digest 或 environment lock hash；
- Python、PyTorch、Deepwave、CUDA 版本；
- seed、实际 device/device name 与可用时的 compute capability；
- 规范化 config hash、全部输入数据 hash；
- deterministic 请求、框架 flag 和已知非确定性。

`provenance_mode=reproducible` 要求 `identity_complete=true`；dirty 源码还必须绑定 diff 或
源码归档 hash。它不表示不同 GPU、驱动或数值库下 bitwise 一致。开发模式可以缺少完整
commit/tree/diff，但必须显式标为 `development`、`identity_complete=false`，并记录可获得的
环境材料；P1/P2 不能在材料缺失时伪装成 reproducible。

## 7. Workbench API 草案

以下只是路径、输入输出和副作用语义草案，**本阶段均未实现**：

| 方法与路径 | 输入 | 输出 | 副作用/阶段 |
|---|---|---|---|
| `GET /api/v1/datasets` | project、过滤条件 | `DatasetRef[]` | 只读；P1 |
| `GET /api/v1/datasets/{id}/versions/{version}` | path ID/version | `DatasetRef` | 只读；P1 |
| `POST /api/v1/task-drafts` | Guided 表单映射或后续 typed intent | `TaskDraft` | 创建 draft；要求 `Idempotency-Key`；P1 |
| `PATCH /api/v1/task-drafts/{draft_id}` | current revision + 字段修改 | 新 revision `TaskDraft` | 乐观并发；旧 revision 不覆盖；P1 |
| `POST /api/v1/task-drafts/{draft_id}/plans` | revision | `PlanGraph` | 确定性编译并计算 hash；P1 |
| `POST /api/v1/plans/{plan_id}/decisions` | `ApprovalDecision` | 持久 decision/event | 批准、拒绝或限域批准；`Idempotency-Key`；P1 |
| `POST /api/v1/plans/{plan_id}/submit` | approval ID | `{task_id,status}` | Gate + SQLite transaction；不直接接收路径/shell；P1 |
| `GET /api/v1/tasks/{task_id}` | task ID | task snapshot | 只读；P1 |
| `GET /api/v1/tasks/{task_id}/events` | `after_sequence` | `RunEvent[]` | 只读轮询；P1，SSE 属于 P2 |
| `GET /api/v1/tasks/{task_id}/artifacts` | task ID | `ArtifactManifest[]` | 只读；P1 |
| `POST /api/v1/tasks/{task_id}/cancel` | reason + idempotency key | accepted/current state | Worker 取消语义属于 P2 |
| `GET /api/v1/tasks/{task_id}/events/stream` | `Last-Event-ID` | SSE `RunEvent` | 断线恢复属于 P2 |

所有 mutation 都必须同源鉴权、校验 content type/size、使用 idempotency key，并持久记录 actor
与 outcome。浏览器、LLM、MCP 不能提交任意服务器路径、command 或 `extra_args`。API 不能把
MCP/A2A 内存状态当作 SQLite 的替代真源。

## 8. Algorithm Adapter v1

Adapter 注册时绑定不可变 `AlgorithmManifest@version`，实现六个方法：

| 方法 | 责任 | 禁止行为 |
|---|---|---|
| `validate` | 校验 typed inputs、参数、数据 metadata/hash 和环境可用性 | 不创建 job、不写 artifact |
| `estimate` | 给出有证据的资源/时间估计与限制 | 不改变计划、不自动放宽预算 |
| `submit` | 接收 task/node identity、规范化 config 和 idempotency key，返回受控 handle | 不接收 shell、任意路径或未注册数据 |
| `status` | 将真实 Worker 状态映射为合同状态/事件 | 不把未知或失败伪装为成功 |
| `cancel` | 以受控 handle 请求取消，幂等返回结果 | P1 不声称已实现；完整语义属于 P2 |
| `collect` | 校验 artifact 位置/hash/类型并生成 `ArtifactManifest` | 不跟随越界路径或信任 Worker 自报 hash |

Adapter 必须声明支持设备、资源上限、副作用、幂等提交、checkpoint 能力、health check、失败
模式和 smoke fixture。Deepwave 是第一个目标 Adapter，但 P0 不修改现有数值 Worker；P1 在
等价测试前继续保留固定 MCP 入口。

## 9. JSON Schema 到 Proto 映射规则

P0 不生成新的 `.proto`，但固定未来映射，避免 JSON 和 gRPC 产生两套语义：

| JSON Schema | Proto |
|---|---|
| `schema_version`、ID、semantic version、hash | `string`，由同一 validator 校验格式 |
| integer 上限字段 | `int32`/`int64`，服务端仍执行 Schema 范围校验 |
| RFC3339 timestamp | `google.protobuf.Timestamp` |
| enum | 同名 enum；零值为 `UNSPECIFIED` 且不能进入持久对象 |
| optional object | `optional` message 或 `oneof` |
| typed arrays | `repeated` message/enum |
| `extensions` | 仅此字段可映射 `google.protobuf.Struct` |

Proto 请求进入服务端后必须转换为同一域对象并执行同一 Gate；不能因为 protobuf 已解析就跳过
Schema、Registry、权限或 approval 检查。

## 10. 现有合同与 P0 v1 差异审计

| 现有来源 | 可复用字段/行为 | 不能直接复用的原因 | P0 映射 |
|---|---|---|---|
| `research/ExperimentSpec` | algorithm/dataset ID、参数、GPU/MPI/time、expected outputs | 无版本/hash/权限/schema version/审批/幂等；参数是自由字符串 map | 只作为历史字段来源，不作为 TaskDraft/PlanGraph 真源 |
| `research/AlgorithmCard` | ID、domain、参数名、I/O、failure mode、dry-run backend | 无算法版本、typed Schema、资源/安全声明；当前 real backend 被刻意禁用 | 新 `AlgorithmManifest`；保留旧 dry-run 行为 |
| A2A `AgentTask`/`TaskStatus`/`Artifact` | task/message/artifact 互操作形状、terminal 概念 | 内存/Redis 适配，状态粒度和 artifact metadata 不足；不是 SQLite 权威状态 | 未来只做边界适配，不当 Task Store |
| `resources/fwi_datasets/dataset_metadata.json` | 数据名称、采集/采样/单位说明 | 条目未绑定真实文件版本/hash/access scope；`marmousi2_synthetic` 也不等于当前小型模型 ID | P1 Catalog 注册时生成不可变 `DatasetRef`，不能直接视为已注册 |
| `resources/fwi_models/model_metadata.json` | shape、spacing、速度范围、来源说明 | 主要描述大模型；缺当前 `marmousi_94_288` 的不可变 catalog version/access scope | 仅作说明性 metadata 来源 |
| `fwi_worker/model_io.py` sidecar | 当前真实 `marmousi_94_288` shape/dtype/physics/unit/hash 双校验 | 包含本机路径，且由 Worker 私有代码读取；不能让浏览器/LLM 传入路径 | P1 导入时提取安全 metadata/hash，Task 只保留 DatasetRef |
| `fwi_worker/FWIConfig` | fixed model ID、preset、cpu/cuda、严格 iterations、seed 和数值上限 | 包含 Worker 私有路径和更多数值字段；没有 plan/approval 身份 | v1 先规范化为最小 `fwi_parameters`，Adapter 内再解析固定配置 |
| MCP `fwi_submit_demo` | 固定 `marmousi_94_288`、`forward|fwi_smoke|fwi_demo`、cpu/cuda、iterations 1–100、无额外字段、受控 spawn | job 状态在运行目录；无 SQLite task/approval/plan hash；最多两个进程只是本地 runner 限制 | P1 Adapter 前继续作为安全回归基线，不绕过批准接入 |
| 旧 FWI `status.json/progress.jsonl` | queued/running/succeeded/failed、真实进度 | 无 task/node identity、单调 sequence、lease/cancel/retry/reconciliation | P1/P2 由 `RunEvent` 适配并持久化 |
| 旧 FWI `manifest.json` | metrics、图、URL、合成验证免责声明 | 含绝对 artifact path、缺 content hash/lineage/完整 provenance | `collect` 只输出受控 relative path/URL 并补 hash/fingerprint |

本审计不迁移或删除旧 prompt-like 文件，因为 `D-005` 仍是 Proposed。

## 11. 威胁模型与控制

| 威胁 | P0 控制 | 后续责任 |
|---|---|---|
| LLM/浏览器注入服务器路径或 shell | Schema 无 path/command/extra_args；artifact 位置只允许受控相对路径/URL | P1 API 与 Adapter 重复验证；保留固定 spawn |
| 数据替换、版本漂移或越权 | `id@version + content_hash + access_scope`，Registry/Gate 三方核对 | P1 Catalog 与 TaskService 事务性快照 |
| 算法冒名、未 pin 或越权版本 | Registry、exact version、allowlist、typed I/O、parameter schema | P5 注册签名/发布流程可加固 |
| 批准后偷偷修改计划 | 规范化 plan hash；approval 绑定 hash/expiry/scope | P1 原子 Gate + Queued 事务 |
| 重复提交造成重复计算 | 节点 idempotency key 格式/唯一性 | P1 SQLite 唯一约束，P2 submit/reconciliation |
| 有环 DAG 或未知依赖 | 确定性图验证 | P3 scheduler 执行语义 |
| 资源耗尽或副作用升级 | 三层资源上限；manifest 与 approval 双重副作用集合 | P2/P3 实际资源锁、超时、取消 |
| dirty 源码或环境不明却声称可复现 | dirty diff/archive hash、环境 digest/lock、runtime/hardware/determinism | Adapter 收集真实证据，失败时标 development/拒绝 reproducible |
| Worker 输出伪造/路径逃逸 | ArtifactManifest 受控 location 与 content hash | Adapter `collect` 从受控根重新计算 hash，不信任自报路径 |
| Redis/A2A 状态覆盖任务真值 | 合同明确 SQLite 是唯一权威真源 | P1 Task Store；Redis 只做可重建派生状态 |

P0 不能防止尚未实现的 API/TaskService 竞态、进程丢失或取消失败；这些不得在 P0 完成报告中
声称已解决。

## 12. P0/P1 边界

本切片实现：JSON Schema、参考 canonical/hash、确定性 Gate、状态/API/Adapter/Proto 规范、
威胁模型、差异审计及自动化正反例测试。

本切片不实现：SQLite、task ID 持久化、Dataset Catalog 写入、审批 API、Deepwave Adapter、
Web 确认卡、SSE、取消、lease、重试、reconciliation、DAG scheduler 或 Agent Planner。

后续状态：SQLite/task identity/草稿、plan、approval 和 event 持久基础已在 P1.1a 实现；该
事实不改变本节对历史 P0 切片边界的描述。P1.1b 还增加了不可变 Dataset/Algorithm 注册
快照、批准预算持久行和服务端 registry resolution，并补齐 plan/manifest port 集合一致性；
P1.2a 后固定 Deepwave 反演已有六方法 Adapter，但尚未接入 SQLite submit/Queued 或产品 API。
详情见 `docs/architecture/SCIENTIFIC_RUNTIME_P1_TASK_STORE.md`、
`docs/architecture/SCIENTIFIC_RUNTIME_P1_REGISTRY.md` 与
`docs/architecture/SCIENTIFIC_RUNTIME_P1_FWI_ADAPTER.md`。
