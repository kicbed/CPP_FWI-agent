# Scientific Runtime P1.1b 注册快照基础

<!-- scientific-runtime-p1-registry: v1 -->

- 对应决策：`D-003`
- 实现切片：`P1.1b`（父工作项 P1.1/P1 阶段仍未完成）
- 实现状态：**Implemented / Verified — immutable registry foundation only**
- 公共合同：继续使用 `contracts/scientific_runtime/v1/`

本切片把 P0 Gate 原先由调用方传入的 Dataset/Algorithm metadata 落为 SQLite 权威、不可变
注册快照，并让 TaskService 从同一个 WAL read snapshot 解析任务引用。它没有 submit、Worker
副作用、Adapter、HTTP API 或 `Queued` 入口。

## 1. 文件与责任

| 文件 | 责任 |
|---|---|
| `scientific_runtime/migrations/0002_catalog_registry.sql` | Dataset version/catalog、Algorithm Registry、approval budget 行、索引和不可变 trigger |
| `scientific_runtime/task_store.py` | 连续 migration、原子注册/读取、hash/index/core identity 复核和同连接 snapshot helper |
| `scientific_runtime/registry_service.py` | Schema/manifest 校验、受信任 provisioning 边界以及 project/principal/permission 限域读取 |
| `scientific_runtime/fwi_registry.py` | 固定 Marmousi sidecar/hash 验证到无路径 DatasetRef 的映射，以及已审 Deepwave manifest 加载 |
| `scientific_runtime/registrations/deepwave_acoustic_fwi_v1.json` | P0 最小 FWI AlgorithmManifest 的版本固定副本 |
| `tests/test_scientific_runtime_registry.py` | migration、并发、不可变性、权限、损坏、TaskService/Gate 和预算测试 |

普通 TaskService 和未来 Web 只读注册结果，不获得 registry mutation。当前 mutation 方法是内部
bootstrap/admin 边界；P5 SDK/发布权限完成前，不把它暴露给浏览器、LLM 或 MCP。

## 2. SQLite v2 与升级

初始化器不再只认识单个 SQL 文件，而是按 `0001`、`0002` 连续应用 migration，并核对每个
已应用版本的文件名和 SHA-256。`PRAGMA user_version` 必须等于连续 migration 的最大版本；
未知未来版本、缺号、名称/checksum 漂移或最终 live schema 漂移都 fail closed。

fresh install 和 v1→v2 原位升级继续受数据库文件 advisory lock 和一个 `BEGIN IMMEDIATE`
事务保护。v2 DDL、既有 approval budget 回填、migration row 与 `user_version` 要么一起提交，
要么一起回滚。升级不会修改旧 task/draft/plan/approval/event JSON。

新增内部表：

- `dataset_versions`：全项目共享的 `id@version`、content hash/type 和不含 access scope 的
  `core_hash`；同一数据版本不能在不同项目伪装成不同 metadata/lineage/extensions；
- `dataset_catalog`：每项目完整、不可变 DatasetRef/access snapshot；同项目同版本精确重放
  幂等，不同内容冲突；
- `algorithm_registry`：不可变 AlgorithmManifest 与 allowlist 索引；allowlist 不能原地翻转；
- `approval_budgets`：从 ApprovalDecision 的 `scope.max_tasks` 固化 `max_tasks/tasks_used`；
  当前只持久化和校验，不消费预算。

四类记录都有 DB 约束或 trigger 防止越界更新/删除。读取时重新计算 document hash，并核对
JSON 与索引 identity、Dataset core identity 及 approval JSON/budget；hash 一致也不能替代
RegistryService/TaskService 的 JSON Schema 重验证。

## 3. 服务端注册解析

TaskDraft 创建、修订和 PlanGraph 持久化现在必须解析 SQLite 中的精确：

```text
project + Dataset id@version -> full immutable DatasetRef
Algorithm id@version         -> full immutable AlgorithmManifest
```

TaskService 拒绝未注册/错版本、Dataset hash/metadata 漂移、无 execute 权限、非 allowlist、
task type/parameter/resource 不兼容，以及与 manifest 不一致的输入、输出 port/type 或副作用。
Dataset 和 Algorithm 在一个 WAL read transaction 中读取；未来 submit 可以在自己的
`BEGIN IMMEDIATE` 中复用同连接 helper，将 registry、draft、plan、approval、budget、submit
idempotency 和首个 queued event 合并为一个事务。

P0 side-effect-free Gate 同步收紧为 plan 输入/输出 port 集合必须与 manifest 精确一致；缺失、
重复、未知 port 都不能只因其余字段类型看似正确而通过。

## 4. Marmousi/Deepwave 基线

固定 bootstrap 先复用 Worker 的 sidecar 验证，对当前 NPY 与来源 MAT 做既有双 hash 检查，
再只映射 DatasetRef 允许字段。公共记录为：

- `marmousi_94_288@1.0.0`；
- NPY content hash（小写 `sha256:` 形式）；
- `[z,x]=[94,288]`、`float32`、m/s、`dx=dz=10m`、`vp=1500..5500m/s`；
- 调用方提供且通过 Schema 的真实 project/principal access scope；
- 无 `path`、`source_path`、运行目录或其他本机位置。

Deepwave manifest 固定为 `deepwave.acoustic_fwi@1.0.0`，保留 P0 的 typed parameters、I/O、
policy resource caps、安全声明和 Adapter v1 metadata。

这里的 `fwi.deepwave_adapter` 尚未实现；现有 MCP runner 也不是六方法、幂等的标准 Adapter。
因此 **registered/allowlisted 不等于 executable/ready**。CPU/memory/wall-time 数值当前是 Gate
policy caps，不代表旧 Worker 已有 OS 级资源隔离。

## 5. 已验证边界与待实现

聚焦自动化覆盖 fresh v2、真实 v1 原位/并发升级、升级失败全回滚、每版 checksum/name、并发
注册、精确 replay、版本共存、跨项目 core identity、权限过滤、不可变 trigger、重启、hash/
index/schema 损坏、TaskService server-owned snapshot、P0 Gate、批准预算和路径脱敏。真实 FWI
模型测试还验证 sidecar/hash 到无路径 DatasetRef 的映射。

仍未实现：部署数据库/API、独立 ACL/allowlist 撤销、批准预算消费、submit idempotency 事务、
单 FWI 节点 capability gate、Deepwave Adapter/Worker handle、artifact 收集、Web 确认卡和 P2
取消/lease/retry/reconciliation。当前 `task_queued` 和所有 pre-runtime→runtime store 转换继续
被拒绝。
