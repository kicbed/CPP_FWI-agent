# 模型与数值配置指南

本文说明当前实验性 Deepwave 二维声学 FWI 模块实际接受什么模型、如何校验模型，以及开发者怎样安全地注册新模型。这里描述的是当前代码行为，不代表系统已经支持聊天上传模型或任意路径计算。

## 1. 当前模型边界

面向用户的 MCP 和 Web 计算入口目前只允许：

```text
model_id = marmousi_94_288
```

`fwi_submit_demo` 接受三个必填白名单字段和一个可选的有界整数：

```json
{
  "model_id": "marmousi_94_288",
  "preset": "forward|fwi_smoke|fwi_demo",
  "device": "cpu|cuda",
  "iterations": 50
}
```

`iterations` 可省略；反演允许 1–10000，正演必须省略（Worker resolved config 会记录为 0）。

Schema 禁止额外字段，插件也会再次检查字段数量和值。以下输入不会被接受：

- 聊天消息中的任意 MAT、NPY、JSON 或目录路径；
- Python 可执行文件、模块名、shell 命令或 `extra_args`；
- 白名单外的 `model_id`；
- 小数、负数、超过 10000 的迭代数，或给正演传入 `iterations`；
- 通过 `..`、绝对路径或符号链接访问其他文件。

`homogeneous_48_96` 是 Worker 的内部数值 smoke 模型，不是 MCP/Web 可选数据模型。独立 Worker 配置文件是开发和测试接口，也受 `FWIConfig` 的 model/preset 枚举以及模型完整性校验约束，不能把它当作任意文件执行入口。

## 2. 已注册 Marmousi 三件套

三份文件必须同时存在：

| 文件 | 固定路径 | 当前用途 |
|---|---|---|
| 原始 MAT | `/root/fwi-data/models/marmousi_94_288.mat` | 数据来源和来源哈希校验，不参与传播 |
| 计算 NPY | `/root/fwi-data/models/marmousi_94_288.npy` | Deepwave 使用的 `Vp` 数组 |
| Sidecar JSON | `/root/fwi-data/models/marmousi_94_288.json` | 物理语义、网格、路径和哈希契约 |

### 2.1 原始 MAT 契约

- MATLAB 变量名：`data`
- shape：`[94, 288]`
- 轴顺序：`[z, x]`
- 原始 dtype：`uint16`
- 物理量：二维常密度声学纵波速度 `Vp`
- 单位：`m/s`
- SHA256：

```text
4E1A50D4AFC5C81016E775FE99C0AC716B975701FCB89885DA6F4CE433DC4357
```

Worker 会检查原始 MAT 文件存在并复算该哈希。它不会在运行时重新猜测变量名、shape、单位或轴顺序。

### 2.2 计算 NPY 契约

- NumPy shape：`(94, 288)`，含义为 `[z, x]`
- dtype：严格为 `float32`
- 速度范围：严格为 `1500–5500 m/s`
- 所有样本必须为有限值，不允许 NaN 或 Inf
- 加载时使用 `allow_pickle=False`
- SHA256：

```text
B80918E3A609A679F16A47DD30978812D80E4FAB1FCBD5CE692D9CA97022A688
```

### 2.3 Sidecar JSON 契约

当前校验器要求 sidecar 至少准确表达以下值：

| 字段 | 当前值 |
|---|---|
| `id` | `marmousi_94_288` |
| `shape` | `[94, 288]` |
| `axis_order` | `["z", "x"]` |
| `mat_variable` | `data` |
| `source_dtype` | `uint16` |
| `compute_dtype` | `float32` |
| `physics` | `2d_acoustic_constant_density` |
| `parameter` | `vp` |
| `velocity_unit` | `m/s` |
| `velocity_min_mps` / `velocity_max_mps` | `1500.0` / `5500.0` |
| `dx_m` / `dz_m` | `10.0` / `10.0` |
| `path` | 固定 NPY 绝对路径，且必须与配置一致 |
| `source_path` | 固定 MAT 绝对路径 |
| `sha256` | NPY SHA256 |
| `source_sha256` | MAT SHA256 |

运行配置还会记录和绘图使用 cell extent：

```json
{
  "x_cell_extent_m": [0.0, 2880.0],
  "z_cell_extent_m": [0.0, 940.0]
}
```

网格点范围为 x `0–2870 m`、z `0–930 m`，而 cell extent 为 x `0–2880 m`、z `0–940 m`，两者含义不同。

### 2.4 只读原则

Worker 从 NPY 创建私有内存副本，初始模型、反演模型和误差全部写入 `FWI_RUN_ROOT/<job_id>`，不会用输出覆盖三件套。宿主机仍建议主动限制权限：

```bash
chmod a-w /root/fwi-data/models/marmousi_94_288.{mat,npy,json}
```

执行前应确认这些文件没有其他进程需要写入。Docker/Compose 部署应把模型目录以只读 bind mount 挂载；运行结果目录必须与模型目录分离。

## 3. 当前实际数值参数

`forward`、`fwi_smoke` 和 `fwi_demo` 共享以下 Marmousi 默认配置；MCP 的 `device` 字段可以把默认 CUDA 改为 CPU。

| 参数 | 实际值 |
|---|---|
| 模型 | `marmousi_94_288`，`[z,x]=[94,288]` |
| `dx_m` / `dz_m` | `10.0 / 10.0` |
| device / dtype | 默认 `cuda` / `float32` |
| 波动方程 | Deepwave `scalar`，二维常密度声学 |
| 反演参数 | 单参数 `Vp` |
| 震源 | Ricker，主频 `8.0 Hz` |
| Ricker 峰值时间 | `1.5/f = 0.1875 s` |
| `dt_s` / `nt` | `0.001 s / 2000`，记录长度 2.0 s |
| 差分精度 | `accuracy=4` |
| PML | `pml_width=20`，`pml_freq=8.0 Hz` |
| Deepwave `max_vel` | `5500 m/s` |
| 炮数 / 检波器数 | `3 / 96` |
| `shot_batch_size` | `1` |
| 源深度 / 检波器深度 | `20 m / 20 m`，网格 z 索引均为 2 |
| 速度约束 | `1500–5500 m/s` |
| 随机种子 | `2026` |
| 初始模型平滑 | 对慢度 `1/v` 做 Gaussian 平滑，`sigma=8` 个网格，`mode=nearest` |
| 顶部保护 | 恢复真实模型顶部 1 行，再执行速度裁剪 |
| 优化器 | Adam |
| learning rate | `10.0` |
| 梯度裁剪 | 每次更新取 `abs(gradient)` 的 `0.98` 分位，执行 value clipping |
| 用户步长 Courant 数 | `0.7778174593` |

Deepwave 的 spacing 顺序为 `[dz_m, dx_m]`。程序把用户配置的 `dt_s`、Courant 数和差分精度写入 `config.resolved.json`；Deepwave 会在内部按 CFL 要求使用时间子步，输入和接收数据仍以 `dt_s=0.001 s` 采样。

### 3.1 采集几何

物理坐标先除以网格间距并四舍五入为整数网格索引，再执行边界和重复位置检查。水平边界留出 `max(1, accuracy/2)=2` 个网格：

- 源 x 网格索引为 `[2, 144, 285]`，对应 `[20, 1440, 2850] m`；
- 96 个检波器在 x 网格索引 2 到 285 之间均匀分布并保证唯一；
- 传播数据 shape 为 `[shot, receiver, time] = [3, 96, 2000]`。

### 3.2 观测、初始模型和目标函数

1. 用真实 Marmousi `Vp` 正演生成 `observed.npy`。
2. 对真实模型的慢度 `1/v` 平滑，生成初始模型。
3. 真实和初始模型使用相同 shape、dx、dz 和采集几何。
4. 目标函数是全炮归一化平方 L2 波形残差：

```text
loss = sum((predicted - observed)^2) / sum(observed^2)
```

5. 每炮批次的 loss 使用同一个全观测能量分母，反向传播后累积梯度。
6. 每次 optimizer step 后把 `Vp` 投影到 `[1500, 5500] m/s`。
7. prediction、loss、gradient 和模型任一出现 NaN/Inf，或梯度全零、模型未更新，任务均失败并生成失败状态。

这是合成逆犯罪链路：观测数据与反演传播都由 Deepwave 生成。它适合验证正演、梯度、优化、任务调度和展示，不代表真实数据反演性能。

### 3.3 三个用户 preset 与迭代覆盖

| Preset | 默认更新次数 | 默认记录的模型状态/loss 数 | 成功判据 |
|---|---:|---:|---|
| `forward` | 0 | 1 | 真模型观测和初始模型预测均非零、finite，产物完整 |
| `fwi_smoke` | 2 | 3（state 0、1、2） | forward/backward、梯度裁剪和两次更新链路 finite，模型确有更新 |
| `fwi_demo` | 5 | 6（state 0–5） | 先通过独立小模型方向导数检查，再满足 smoke 检查，且 final loss 严格低于 initial loss |

`fwi_smoke` 的目的不是证明反演质量，因此它没有“final loss 必须低于 initial loss”的硬判据。`fwi_demo` 的梯度检查使用独立的 float64 小模型；若相对误差不小于 `5e-3`，不会继续 demo 反演。

MCP/Web 可以用 `iterations=1..10000` 覆盖两个反演 preset 的默认更新次数；状态和指标会
记录实际值。自然语言中的“运行 500 次迭代”会选择 `fwi_demo` 的检查规则并传入 500，
不会静默回退成默认 5。当前只接受用户明确给出的次数；参数建议、人工审批或授权 Agent
自行选择迭代预算属于后续交互工作流。

10000 是显式校验上限，不是推荐默认值或完成时间保证。超过 100 次可能显著延长运行并占用
runner 槽位；P1 没有运行中取消、checkpoint 或 retry，应先用 smoke 验证配置再批准长任务。

虽然 `FWIConfig` 也声明了 SGD 选项，但当前三个 Marmousi preset 实际均使用 Adam。

## 4. 安全注册新模型：开发流程

当前版本不能只复制一个 NPY 或在聊天中输入路径就切换模型。新增模型是代码开发和安全评审工作，至少完成以下步骤。

### 步骤 1：在仓库外准备不可变数据

1. 保留原始数据文件，不覆盖、不原地转换。
2. 明确并记录变量名、shape、`[z,x]` 轴顺序、物理量、单位、dx、dz 和采样含义。
3. 在独立转换脚本或受控笔记本中生成二维、C-contiguous 的 `float32` NPY。
4. 拒绝 NaN/Inf，核对实际速度范围和物理合理性。
5. 分别计算原始文件和 NPY 的 SHA256，生成独立 sidecar。
6. 把模型目录设为只读；不要把大型模型提交到 Git。

不要把密度、慢度、深度或无单位数组仅通过重命名伪装成 `Vp (m/s)`。

### 步骤 2：扩展 Worker 的显式注册表

需要实际修改并评审：

- `fwi_worker/config.py`
  - 增加新 `model_id`、固定路径、默认网格和数值参数；
  - 扩展 `FWIConfig.model_id` 的 Literal 和一致性校验；
  - 决定它对应哪些 preset，不能继承未经验证的 Marmousi 参数。
- `fwi_worker/model_io.py`
  - 为新模型增加不可变的期望元数据和双哈希；
  - 扩展 sidecar 路由与 `load_model` 分支；
  - 严格检查 shape、dtype、finite、速度范围、单位、轴顺序和路径；
  - 始终把输入复制到私有内存，不原地修改。
- `fwi_worker/acquisition.py`、`deepwave_2d.py`、`plots.py`
  - 根据新尺寸验证源/检波器不越界、不重复；
  - 验证 dt、网格、最大速度、频率、PML 和绘图 extent；
  - 若现有通用逻辑已经满足契约，可以不改代码，但必须补测试证明。
- `fwi_worker/__main__.py`
  - 检查 resolved config、manifest、真实模型误差和模型特有 smoke 条件是否仍成立。

建议把现有单模型常量重构为以 `model_id` 为键的显式只读注册表；不要退化成“调用者传入任意路径”。

### 步骤 3：扩展 MCP 和自然语言路由

需要实际修改并评审：

- `mcp_server_integrated/plugins/fwi-runner/FWIRunner.cpp`
  - 把单一 `kModelId` 改成明确的 ID→固定配置映射；
  - 扩展 JSON Schema enum 和服务端二次校验；
  - 仍然只把 `model_id/preset/device` 传给 Worker，绝不接收路径或命令。
- `orchestrator/include/agent_rpc/orchestrator/tool_calling_engine.h`
  - 增加新模型的明确名称匹配和固定参数生成；
  - 保留理论问题不触发计算的判定。
- `examples/ai_orchestrator/orchestrator_main.cpp`
  - 更新面向用户的当前白名单范围说明。

Web 结果面板从 manifest 读取通用字段，通常无需为模型写专用路径，但必须确认它不会显示或请求模型原始路径。

### 步骤 4：增加测试并完整验收

至少扩展：

- `tests/fwi_worker/test_config_model_io.py`：sidecar、双哈希、shape、dtype、单位、范围、NaN/Inf 和只读行为；
- `tests/fwi_worker/test_acquisition.py`：新 shape 的坐标、唯一性和越界；
- `tests/fwi_worker/test_deepwave_smoke.py`：CPU 小模型正演和梯度；
- `tests/fwi_worker/test_plots_metrics.py`：extent、共同色标、指标和 PNG 解码；
- `tests/fwi_worker/fixtures/`：新模型的 forward/smoke/demo 固定配置；
- `mcp_server_integrated/plugins/fwi-runner/FWIRunnerTest.cpp`：允许新 ID，同时继续拒绝路径、额外参数和符号链接逃逸；
- `tests/test_fwi_tool_routing.cpp`：新模型中文执行请求、状态/结果和理论负例；
- `web/tests/ui_message_rendering_test.js` 与 `web/tests/test_artifact_route.py`：结果渲染和原始模型不可经 artifact 路由读取。

验收顺序应为：Worker validate → CPU/CUDA smoke → 新模型 forward → 梯度检查 → 2 次 smoke → 小规模 demo → CTest/MCP/Web 测试 → 自然语言端到端。最终还要复算输入哈希，确认原始文件未改变，并检查 Git 中没有模型、运行产物、虚拟环境或密钥。

## 5. 相关源码和文档

- 数值配置：`fwi_worker/config.py`
- 模型校验：`fwi_worker/model_io.py`
- 采集几何：`fwi_worker/acquisition.py`
- Deepwave 包装：`fwi_worker/deepwave_2d.py`
- 反演更新：`fwi_worker/inversion.py`
- MCP 白名单：`mcp_server_integrated/plugins/fwi-runner/FWIRunner.cpp`
- 部署说明：[DEPLOYMENT.md](DEPLOYMENT.md)
- 前端验收：[FRONTEND_TEST.md](FRONTEND_TEST.md)
