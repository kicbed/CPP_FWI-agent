# Scientific Runtime P1.2a Deepwave Adapter

<!-- scientific-runtime-p1-fwi-adapter: v1 -->

- 对应决策：`D-003`
- 实现切片：`P1.2a`（P1 阶段仍未完成）
- 实现状态：**Implemented / Verified — fixed single-node adapter only**
- 绑定算法：`deepwave.acoustic_fwi@1.0.0`
- 绑定数据：`marmousi_94_288@1.0.0`

本切片把既有 Deepwave 反演 Worker 包装为 Algorithm Adapter v1 的六方法边界，但没有把
Adapter 接入 TaskService、HTTP、MCP 或 `Queued` 状态转换。它是可供后续事务性提交路径调用的
受控组件，不是已经开放的产品执行入口，也不是通用 scheduler。

## 1. 文件与责任

| 文件 | 责任 |
|---|---|
| `scientific_runtime/fwi_adapter.py` | validate/estimate/submit/status/cancel/collect、受控 handle、幂等索引、固定 launcher、artifact 复核 |
| `fwi_worker/adapter_probe.py` | 在固定 Worker venv 中只读验证模型、设备和 development 环境快照 |
| `scientific_runtime/__init__.py` | 导出 Adapter 的 typed result/error/API |
| `tests/test_scientific_runtime_fwi_adapter.py` | 合同、安全、并发、崩溃点、artifact 语义和固定 venv probe 测试 |

Adapter v1 精确绑定打包的 AlgorithmManifest canonical hash。manifest 字段漂移必须升级 Adapter，
不能只保留相同 algorithm ID/version 后静默继续。

## 2. 六方法边界

| 方法 | P1.2a 行为 | 明确限制 |
|---|---|---|
| `validate` | 复核 project/principal、服务端 Registry DatasetRef snapshot、参数、设备、资源上限和固定运行环境 | 启动只读 probe，但不创建 job/artifact；本地 provider 不是产品鉴权替代物 |
| `estimate` | 返回规范化 config hash、请求资源和 manifest policy caps | 没有校准 wall-time 模型；CPU/memory/time 不是 OS 隔离保证 |
| `submit` | 以 task + plan hash + node idempotency key 生成受控 handle，首次请求才做 live readiness 并固定启动 Worker | 不接受 shell、路径、command 或 extra args；尚未由 SQLite Gate/approval 调用 |
| `status` | 只映射 queued/running/succeeded/failed，校验身份、进度和时间；返回受控消息 | 原始 Worker 异常只留在私有日志，不向公共状态泄露路径 |
| `cancel` | 对合法 handle 稳定返回 `CANCEL_NOT_SUPPORTED_IN_P1`，不依赖 status、不发信号、不改状态 | Worker 取消、lease 和进程身份属于 P2 |
| `collect` | 成功后从固定输出重新验证并生成两个 ArtifactManifest | 不发布旧 manifest 的绝对路径，不接受未知输出或 forward 伪装 |

标准 Adapter 当前只支持 `acoustic_fwi_2d` 的 `fwi_smoke|fwi_demo`。旧 `forward` 会把初始模型
写到名为 `models/inverted.npy` 的位置；在输出合同重新版本化前，把它发布为 inverted model 会
造成科学语义错误。因此旧 MCP forward 行为保留，但不进入标准 Adapter。

## 3. 身份、权限与幂等

- `validate/submit` 要求服务端提供 current `project_id/principal_id`；DatasetRef 的 project、
  principals 和 execute permission 必须匹配，并在首次 live 验证时与必需的
  `registry_snapshot_provider` 返回值逐字段一致。未配置该 provider 的新执行 fail closed；未来
  产品入口由 TaskService 从 SQLite Registry 解析 snapshot，并先经过 approval/Gate。
- 独立的本地 identity provider 只证明固定 Marmousi 文件/sidecar/hash、metadata 和 lineage，
  不比较也不产生产品 ACL。probe 内隔离的 `adapter-validation` scope 只是让该证据满足 DatasetRef
  Schema；它不能拒绝真实 Registry scope，也不能被描述为用户授权。
- submission 唯一域是 `(task_id, plan_hash, idempotency_key)`；request hash 还绑定 node、当前
  scope、algorithm、dataset、parameters、resources 和 normalized config。相同请求跨线程、
  Adapter 实例和进程返回同一 handle；同域不同请求 fail closed。
- 私有幂等记录包含覆盖 job identity、Worker config、fingerprint、创建时间和 launch state 的
 完整 envelope hash。它只负责 Worker dispatch 去重，不是 task/status 真源；SQLite 仍是唯一
  权威任务数据库。
- `preparing/launching` 或失败 launch 不会被 P1 猜测性重启。前者返回
  `SUBMISSION_RECONCILIATION_REQUIRED`，后者保持 sticky failure；完整恢复属于 P2。

## 4. 文件系统、进程与证据边界

- run root 必须是服务账号拥有、group/other 不可写的专用绝对目录；禁止项目树、home 根、
  系统目录和 symlink 组件。
- 控制目录、锁、submission record、job 目录和状态/artifact 读取使用目录 FD、
  `openat/O_NOFOLLOW/fstat`；原子 JSON 写入 fsync 文件和目录，新目录 fsync 父目录。artifact
  父目录交换或最终 symlink 都不能逃逸受控根。
- launcher 只允许固定 Worker venv、固定 argv、`shell=False`、无 stdin 和最小环境；probe 与
  numerical Worker 必须使用同一 venv 入口。Worker 上限只是进程内两任务保护，不是全局资源
  scheduler；probe 另有进程级并发和流式输出上限。
- status 的未知/损坏证据一律报错；失败消息经过固定映射。P1 不把私有 log、PID、run path
  放入 handle 或公共 manifest。
- collect 不信任旧 manifest 的自报 path/hash。它复核固定 config、legacy identity、status、
  metrics 一致性，并从打开的文件重新计算 size/hash。
- NPY 必须是精确 `(94, 288)`、C-order、float32、固定字节数、finite 且速度位于
  `1500..5500 m/s`；先解析小 header 再建立固定大小 view，避免声明超大 shape 的内存 DoS。
- loss CSV 必须有精确行数、连续 iteration、finite 正频率和非负 loss；初/末 loss 与 reduction
  从 CSV 交叉验证。iterations、nan/inf、device、runtime versions 和 device name 必须与请求及
  fingerprint 一致。

## 5. Provenance 诚实边界

当前 fingerprint 固定为 `provenance_mode=development`、`identity_complete=false`。它记录可获得
的 commit/tree/dirty、安装包快照、Python/PyTorch/Deepwave/CUDA、硬件、输入/config hash 和
seed，但明确声明：

- 旧 Worker 记录 seed，却没有在数值路径消费它；
- 未启用 deterministic algorithms，不承诺跨库/驱动/GPU bitwise equality；
- environment hash 是安装分布快照，不是可重建 lock；
- dirty source 没有完整 diff/archive identity 时不能升级为 reproducible。

因此本切片不能用于声称结果可普遍复现，也不能把一次合成反演 smoke 外推为科学效果结论。

## 6. 验证证据与剩余工作

- Adapter 聚焦测试：17/17 PASS，覆盖严格输入、scope/registry snapshot 漂移、side-effect-free
  validate/estimate、跨线程/实例/进程幂等、launch 崩溃状态、记录完整性、状态损坏/脱敏、稳定
  unsupported cancel、路径竞态/symlink、NPY header、CSV/metrics/config/provenance 反例；
- 固定 venv CPU dataset/runtime probe：PASS，输出无服务器数据路径且不创建 run state；测试
  同时证明真实 Registry scope 与 probe placeholder scope 分离；
- 真实固定 venv CUDA `fwi_smoke` 一次迭代：Adapter submit → status → collect PASS，两个
  ArtifactManifest Schema 合法，跨 Adapter 实例 replay 返回相同 handle；
- 该 smoke 是固定小型合成 Marmousi 链路证据，不是一般反演质量或生产可靠性证明。

仍 Pending：SQLite 中 Gate + current draft/plan/approval/registry + budget consumption + submit
idempotency + 首个 `task_queued` 的原子状态变化；事务提交后的 durable dispatch/reconciliation
边界；HTTP/Guided Web；P2 cancel/lease/retry/recovery；P3 DAG。数据库事务内不能直接调用
`Popen`，否则会产生“Worker 已启动但事务回滚”或“已排队但未启动”的不可恢复缝隙。
