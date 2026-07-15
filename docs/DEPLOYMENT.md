# 部署与运行

本文档是本仓库唯一的详细部署说明。当前系统面向单机科研验证：二维常密度声学
Deepwave FWI 可在 CPU 或单张 NVIDIA GPU 上运行；不提供多 GPU、MPI、远程集群、
任意模型路径或任意 shell 执行能力。

## 1. 固定目录与数据准备

默认路径如下：

| 内容 | 路径 | 访问方式 |
|---|---|---|
| 仓库 | `<repo-root>` | 源码与构建；下文命令先进入实际克隆目录 |
| Python venv | `/root/.venvs/cpp-fwi-agent` | 仓库外 |
| 模型目录 | `/root/fwi-data/models` | 只读使用 |
| 运行结果 | `/root/fwi-runs` | 每个 job 独立子目录 |

Marmousi 演示需要宿主机上已存在：

```text
/root/fwi-data/models/marmousi_94_288.mat
/root/fwi-data/models/marmousi_94_288.npy
/root/fwi-data/models/marmousi_94_288.json
```

Worker 从 sidecar JSON 验证 shape、轴顺序、网格间距、速度范围，并分别核验原始 MAT
与计算 NPY 的 SHA256；缺少三者中的任意一个都会拒绝运行。不要修改、覆盖或在镜像中
复制原始模型；Docker 将包含 MAT、NPY、JSON 的整个模型目录只读 bind mount 到同一路径。

```bash
mkdir -p /root/fwi-runs
test -r /root/fwi-data/models/marmousi_94_288.mat
test -r /root/fwi-data/models/marmousi_94_288.npy
test -r /root/fwi-data/models/marmousi_94_288.json
```

## 2. 推荐方式：宿主机外置 venv + 根入口

Ubuntu 22.04、Python 3.10、CMake 3.20+ 和 GCC 11 是当前验证环境。根工程使用
C++17；集成 MCP Server 的插件构建启用 C++20，因此编译器还必须完整支持 C++20。
首次安装 C++/gRPC/Redis 依赖可使用仓库的环境准备脚本；它会修改系统软件包，运行前
应先审阅：

```bash
cd /path/to/agent-communication-main-v2
bash setup_agent_env_grpc151.sh
```

创建仓库外的数值环境：

```bash
python3 -m venv --system-site-packages /root/.venvs/cpp-fwi-agent
source /root/.venvs/cpp-fwi-agent/bin/activate
python -m pip install --upgrade pip
```

PyTorch 只选择下面一种安装方式。CPU：

```bash
python -m pip install "torch==2.12.0" \
  --index-url https://download.pytorch.org/whl/cpu
```

当前实机验收使用的 NVIDIA CUDA 13.0 wheel：

```bash
python -m pip install "torch==2.12.0" \
  --index-url https://download.pytorch.org/whl/cu130
```

再安装完整 Worker 依赖；这些包同时用于 SciPy 慢度平滑、Matplotlib 绘图和 PNG 解码：

```bash
python -m pip install \
  "deepwave==0.0.27" \
  "jsonschema==3.2.0" \
  "numpy>=1.26,<3" \
  "scipy>=1.11,<2" \
  "matplotlib>=3.8,<4" \
  "pillow>=10,<13" \
  "pydantic>=2,<3" \
  "pyyaml>=5.4,<7"
python -c 'import importlib.metadata, torch; print(importlib.metadata.version("deepwave"), torch.__version__, torch.cuda.is_available())'
```

NVIDIA 环境若不适合 `cu130`，应按驱动和 PyTorch 官方版本表选择兼容 wheel，并重新跑
CPU/CUDA smoke。不要因为 Deepwave wheel 选择失败而直接安装完整 CUDA Toolkit。

### 2.1 Runtime secret

启动器支持从环境读取 key，并向 Agent 进程传递字面量 `@env`，因此 key 不出现在
进程命令行或普通日志。这不是 secret manager：同用户/root 仍可能查看进程环境。推荐把
secret 放在仓库外、权限为 `0600` 的文件中：

```bash
install -m 600 /dev/null "$HOME/.config/fwi-agent.env"
```

文件内容示例（只配置实际使用的 provider）：

```dotenv
LLM_PROVIDER=qwen
QWEN_API_KEY=replace-me
# DEEPSEEK_API_KEY=replace-me
# OPENAI_API_KEY=replace-me
```

加载时不要打印文件内容：

```bash
set -a
source "$HOME/.config/fwi-agent.env"
set +a
```

注意：上述文件和仓库 `.env` 都会由 Bash `source`，不是只读取 `KEY=VALUE` 的纯
dotenv parser；其中的 shell 代码会执行。只能加载自己创建的可信文件。Provider、模型、
endpoint 和密钥变量由同一个严格映射选择；未知 `LLM_PROVIDER` 会在网络请求前拒绝。
Cloud provider 的 `LLM_API_URL` 固定为对应官方 endpoint，避免将 key 发往误配地址；
`local` 模式才允许配置 loopback HTTP endpoint，且不读取云端 key。

仓库外文件与仓库根 `.env` 是二选一。若采用本节方式，不要再创建仓库 `.env`；根
`start.sh` 发现 `.env` 时会在已导出的环境之后加载它，同名变量将以仓库 `.env` 为准。
QUICKSTART/前端教程里的 `cp .env.example .env` 是更方便的本机开发方式，不要与本节混用。

### 2.1.1 本地 FWI 知识库与可选 Embedding

这两项能力彼此独立：

- FWI 理论问答始终从仓库内受控的 `resources/fwi_knowledge`、`fwi_models` 和
  `fwi_datasets` 检索本地资料，再让 LLM 组织有界答案；它不要求 Embedding 在线。
- 本地 Embedding 只为 `ROUTING_MODE=agent-rag` 提供 AgentCard 语义路由。默认模型是
  `Qwen/Qwen3-Embedding-0.6B`，不是 FWI 文档向量库。
- `ENABLE_RAG` 控制的是 MCP Tool-RAG，不能代替上述两项。

第一次使用本地 Embedding 时，显式在仓库外 Python 环境安装依赖并下载模型：

```bash
cd /path/to/agent-communication-main-v2
deploy/scripts/setup_embedding.sh
```

该准备脚本会使用 `/root/.venvs/cpp-fwi-agent`，不会向仓库创建 venv。模型下载属于
用户明确执行的准备步骤；日常 `./start.sh` 使用 `local_files_only`，不会在启动过程中
静默下载大型模型。配置示例：

```dotenv
ROUTING_MODE=agent-rag
EMBEDDING_PROVIDER=local
ENABLE_LOCAL_EMBEDDING=auto
LOCAL_EMBEDDING_URL=http://127.0.0.1:6000
LOCAL_EMBEDDING_MODEL=Qwen/Qwen3-Embedding-0.6B
LOCAL_EMBEDDING_DEVICE=cpu
```

`auto` 只在 `agent-rag + local` 组合下让根启动器托管 Embedding，并由 `./stop.sh`
按可信 PID 文件停止。默认使用 CPU，避免与 Deepwave FWI 争用单张 GPU；确实接受额外
显存占用时才能改为 `cuda`。Web 通过自己的同源健康端点显示模型维度和设备，不直接跨源
访问 6000 端口。Embedding 临时离线时 Agent 路由自动退回关键词匹配，FWI 本地文档检索
仍可继续工作。

### 2.2 唯一推荐启动入口

根目录 `./start.sh` 和 `./stop.sh` 是日常使用的唯一推荐入口：

```bash
cd /path/to/agent-communication-main-v2
export FWI_VENV=/root/.venvs/cpp-fwi-agent
export FWI_RUN_ROOT=/root/fwi-runs
export SCIENTIFIC_RUNTIME_DB_PATH="$HOME/.local/state/cpp-fwi-agent/scientific-runtime/tasks.sqlite3"
export ENABLE_MCP=true
export WEB_HOST=127.0.0.1
export WEB_PORT=8080

./start.sh                 # 首次运行或源码改变后，自动配置/增量构建
./start.sh --grpc          # 同时启用 Web 的 gRPC 模式
./start.sh --no-build      # 已有最新构建时，仅启动
./start.sh --rebuild       # 仅在需要 clean-first 重建时使用
```

数值 Worker 解释器按安全约束固定为
`/root/.venvs/cpp-fwi-agent/bin/python`；`FWI_VENV`/`FWI_WORKER_PYTHON` 不能通过
聊天、MCP 或 dotenv 改成其他可执行文件。`FWI_RUN_ROOT` 可以由部署管理员改为其他
专用绝对结果目录，但不能是符号链接、仓库、HOME、系统敏感目录或它们的上级。
P1 Guided Task Store 默认位于仓库外的
`~/.local/state/cpp-fwi-agent/scientific-runtime/tasks.sqlite3`。
`SCIENTIFIC_RUNTIME_DB_PATH` 只允许专用私有绝对路径：不得与 `FWI_RUN_ROOT` 重叠，不得位于
仓库或系统敏感目录，现有父目录必须由当前用户所有且权限为 `0700`。

可选 gRPC Server/Web bridge 也通过同一入口启动：

```bash
./start.sh --grpc
```

默认不开启；开启后额外监听本机 `127.0.0.1:50051/50052`，`./stop.sh` 会一并关闭。
50052 是浏览器可访问的 HTTP bridge，并会调用 50051 的原生 gRPC
`AIQueryService`。`--grpc` 是命令行强制开关，不会被 `.env` 中的
`ENABLE_GRPC=false` 覆盖。

内置 Redis 默认把对话状态以 AOF 写到仓库外的
`~/.local/state/cpp-fwi-agent/redis`。Session 每个完整回合刷新 TTL，FWI tool state 只在新
job_id 落盘时刷新。可在 `.env` 调整：

```dotenv
REDIS_PERSISTENCE=true
# REDIS_DATA_DIR=/absolute/private/path
CONTEXT_MAX_MESSAGES=10
CONTEXT_MAX_CHARS=12000
CONTEXT_MAX_MESSAGE_CHARS=4000
CONVERSATION_MAX_STORED_MESSAGES=200
CONVERSATION_TTL_SECONDS=2592000
```

目录必须是专用绝对路径、非符号链接，并会被收紧为 `0700`。已存在的通用非空
目录会被拒绝，避免启动器误改 `/etc`、仓库父目录等路径权限。内置 Redis 使用
`appendfsync everysec`；异常断电/崩溃时最后约 1 秒可能丢失，且当前没有备份或 HA。临时敏感实验可设
`REDIS_PERSISTENCE=false`，但停止内置 Redis 后模型将不再记得旧上下文；浏览器中的本地
历史仍需单独清除。完整语义见[对话上下文与历史管理](CONVERSATION_MANAGEMENT.md)。
如果端口上已经有外部 Redis，启动器不会更改其 AOF/RDB 设置，
`REDIS_PERSISTENCE=false` 也不会关闭外部持久化。当前没有 Redis 密码/ACL/TLS 参数，
不要把会话明文发送到远程或不可信 Redis。

启动器在后台启动 Agent 和 Web，健康检查通过后返回。访问：

- Web：`http://127.0.0.1:8080`
- Orchestrator：`http://127.0.0.1:5000`

关闭脚本可重复执行：

```bash
./stop.sh
```

`stop.sh` 不删除 Redis AOF 或 localStorage，不会把“浏览器停止等待”变成请求撤销，
也不是 FWI cancel。外部 Redis 不由该脚本停止。当前没有服务端 request-id 幂等表。

`deploy/scripts/` 仅保留兼容或高级调试用途；
`examples/ai_orchestrator/start_system.sh` 是根启动器使用的内部实现，不应作为另一套日常
入口。不要同时运行多套启动脚本，否则固定端口和 PID 文件会冲突。

### 2.3 Web 环境变量

- `WEB_HOST` 默认 `127.0.0.1`。P1 Guided API 只在 `127.0.0.1`/`localhost` 绑定时
  启用；`0.0.0.0` 只保留容器 legacy/static 兼容，Guided 路由返回 503。当前不应用
  Compose 验收 Guided Runtime。
- `WEB_PORT` 默认 `8080`；端口占用时启动会明确失败，不会静默切换端口。
- 默认不发送 CORS 允许头。只有确需跨源访问时设置精确的
  `WEB_ALLOW_ORIGIN`，不要使用不受控的 `*`。
- `FWI_RUN_ROOT` 默认 `/root/fwi-runs`。Web artifact 路由只允许该目录下受控的
  `.json`、`.csv` 和 `.png`；服务会拒绝将该 root 设为 `/`、HOME、仓库或系统敏感目录。
- `SCIENTIFIC_RUNTIME_DB_PATH` 默认为上述仓库外 SQLite 文件；它不能位于
  `FWI_RUN_ROOT` 内，避免被 legacy artifact 路由服务或与 Worker 输出碰撞。
- `AGENT_BIND_HOST` 默认 `127.0.0.1`，宿主机运行时不要改成公网地址。容器内部显式
  使用 `0.0.0.0`，但 Compose 只把端口发布到宿主机 loopback。
- `AGENT_CORS_ORIGIN` 默认 `http://127.0.0.1:8080`，用于 Web 跨端口访问 Agent；
  不要设置为 `*`。若修改 Web 端口，需同步设置准确 origin。
- 数学公式支持 `$...$`、`$$...$$`、`\(...\)` 和 `\[...\]`。固定版本的 KaTeX 只在
  答案实际含公式时按需加载，并校验 CDN 资源完整性；渲染时禁用可信 HTML/URL，限制尺寸
  与宏展开。CDN 不可用或公式无效时保留经过转义的 TeX，不会显示空白。

## 3. Docker Compose 隔离部署

Docker 构建从干净源码自行执行 CMake 和 MCP 构建。多阶段最终镜像不包含宿主机
`build/`、模型、运行产物、`.env` 或仓库内 venv；只保留运行所需源码、安装后的二进制、
MCP 插件和固定路径 `/root/.venvs/cpp-fwi-agent`。Compose 不维护第二套 Agent 启动命令；
它只调用同一个根入口 `./start.sh --no-build`，退出或收到信号时调用 `./stop.sh`。
对话 AOF 使用命名卷 `conversation-state`，因此普通 `docker compose down` 后仍保留；只有
明确执行 `docker compose down -v` 才删除该 Redis 卷。`-v` 不会删除浏览器 localStorage，
也不会删除 bind-mounted `FWI_RUN_DIR` 中的结果。

默认镜像不内置 0.6B Embedding 权重，Compose 因此显式保持
`ENABLE_LOCAL_EMBEDDING=false`；FWI 本地文档问答仍然可用。需要容器内 Agent-RAG
Embedding 时，应构建包含 `sentence-transformers` 的派生镜像并只读挂载预下载模型缓存，
不能依赖容器启动时联网下载。

### 3.1 Runtime env_file

Compose 的 env file 只在容器启动时注入，不作为 Docker build argument。继续使用仓库外
文件：

```bash
export FWI_ENV_FILE="$HOME/.config/fwi-agent.env"
export FWI_MODEL_DIR=/root/fwi-data/models
export FWI_RUN_DIR=/root/fwi-runs
```

不要把 key 写入 Dockerfile、`compose.yaml`、镜像标签、build args 或命令行。`.env` 和
常见 dotenv 变体已被 `.dockerignore` 排除。

当前 Compose 使用 `WEB_HOST=0.0.0.0`，因此只验收 legacy Web/MCP 链路，不创建或暴露
P1 Guided Task Store/API。Guided 容器部署需要后续明确的认证/反向代理边界，不在 P1 内隐式开启。

### 3.2 默认 CPU 构建和启动

```bash
cd /path/to/agent-communication-main-v2
docker compose build fwi-agent
docker compose up -d fwi-agent
docker compose ps
docker compose logs -f fwi-agent
```

Compose 默认安装当前实机验收所用的 PyTorch 2.12.0 CPU wheel，且不请求宿主机 GPU。
提交 FWI 时明确指定 CPU，
例如“使用 marmousi_94_288 在 CPU 运行两次迭代的 FWI smoke test”。

访问地址仍只在本机：

- `http://127.0.0.1:${FWI_WEB_PORT:-8080}`
- `http://127.0.0.1:5000`

查看容器内详细服务日志：

```bash
docker compose exec fwi-agent sh -lc \
  'tail -n 100 /opt/fwi-agent/examples/ai_orchestrator/logs/*.log /tmp/fwi-agent/logs/*.log'
```

停止并删除容器网络（不会删除 bind mount 中的结果）：

```bash
docker compose down
```

### 3.3 单 NVIDIA GPU

宿主机必须先安装可用的 NVIDIA 驱动、Docker Engine/Compose plugin 和
[NVIDIA Container Toolkit](https://docs.nvidia.com/datacenter/cloud-native/container-toolkit/latest/install-guide.html)。
按 NVIDIA 官方说明配置 Docker runtime：

```bash
sudo nvidia-ctk runtime configure --runtime=docker
sudo systemctl restart docker
docker run --rm --gpus all nvidia/cuda:13.0.0-base-ubuntu22.04 nvidia-smi
```

Compose GPU device reservation 的格式遵循
[Docker Compose GPU 文档](https://docs.docker.com/compose/how-tos/gpu-support/)。GPU 服务只
请求一张卡，并构建 CUDA 13.0 PyTorch wheel：

```bash
docker compose down
docker compose --profile gpu build fwi-agent-gpu
docker compose --profile gpu up -d fwi-agent-gpu
docker compose --profile gpu exec fwi-agent-gpu \
  python -c 'import torch; print(torch.__version__, torch.cuda.is_available(), torch.cuda.get_device_name(0))'
```

不要同时启动 `fwi-agent` 与 `fwi-agent-gpu`；两者有意占用相同的 localhost 端口。若需
选择不同的 PyTorch wheel，可在构建前覆盖 `TORCH_VERSION` 和
`PYTORCH_GPU_INDEX_URL`，但偏离当前验收的 2.12.0 后必须重新执行数值 smoke，并应先对照
[PyTorch 官方版本表](https://pytorch.org/get-started/previous-versions/)。

停止 GPU profile：

```bash
docker compose --profile gpu down
```

## 4. Compose 挂载与安全边界

Compose 使用以下限制：

- 模型目录以只读方式挂载到 `/root/fwi-data/models`；
- 该目录必须同时提供 `marmousi_94_288.mat`、`.npy` 和 `.json`，Worker 会核验 MAT/NPY
  两个哈希；
- 只有运行目录以读写方式挂载到 `/root/fwi-runs`；
- Web `8080` 和 Agent `5000` 仅发布到宿主机 `127.0.0.1`；
- 容器根文件系统只读，`/tmp` 为临时文件系统；
- 删除 Linux capabilities，并设置 `no-new-privileges`；
- 不挂载 Docker socket，不使用 privileged 模式；
- MCP FWI runner 只接受固定 model/preset/device 和仅反演可用的
  `iterations=1..10000`，使用固定 argv 启动 Worker，单个 runner 最多并发 2 个作业；默认
  smoke/demo 仍为 2/5 次，长任务在 P1 没有运行中取消或 timeout 保证；
- `.env` 不进入镜像，数值 Worker 也不会继承 Agent 的 API key/token/secret 环境。

由于固定模型和结果路径位于 `/root`，容器当前以容器内 root 运行；这不授予宿主机 root
文件系统访问权，但容器可写所配置的 `FWI_RUN_DIR` bind mount。不要把 `/`、`/root` 或
包含其他敏感数据的父目录挂载为运行目录。

Web/Agent 当前没有面向公网的 TLS、认证和租户隔离。loopback 发布是安全边界的一部分；
若要跨主机使用，应在完成认证、TLS、反向代理和访问审计后另行设计，不要简单把端口
改成 `0.0.0.0`。
同一 context 只有进程内串行锁，没有分布式锁/leader；不要用多个 Orchestrator 实例
负载均衡处理同一 `contextId`。

## 5. 验证

宿主机模式：

```bash
curl --fail http://127.0.0.1:8080/ >/dev/null
curl --fail http://127.0.0.1:5000/.well-known/agent-card.json
source /root/.venvs/cpp-fwi-agent/bin/activate
python -m fwi_worker validate \
  --config tests/fwi_worker/fixtures/marmousi_forward_cpu.json
```

Compose 配置和镜像：

```bash
FWI_ENV_FILE="$HOME/.config/fwi-agent.env" docker compose config --quiet
docker compose build fwi-agent
docker compose exec fwi-agent \
  JOB_ID=fwi-YYYYMMDDTHHMMSSZ-xxxxxxxxxxxx
  python -m fwi_worker status --run-dir "/root/fwi-runs/$JOB_ID"
```

一次完整验收应提交固定 Marmousi forward 或 smoke，轮询到明确的 `succeeded`/`failed`，
再确认 Web 能加载 manifest、metrics 和六张 PNG。所有结论仅限当前小型合成 Marmousi
模型和所记录参数。

## 6. 故障排查

### `docker compose config` 报 env file 不存在

设置绝对路径并检查权限：

```bash
export FWI_ENV_FILE="$HOME/.config/fwi-agent.env"
test -r "$FWI_ENV_FILE"
```

### bind source 不存在或模型验证失败

Docker 不应创建一个空目录来掩盖路径错误。先在宿主机检查：

```bash
test -r "$FWI_MODEL_DIR/marmousi_94_288.mat"
test -r "$FWI_MODEL_DIR/marmousi_94_288.npy"
test -r "$FWI_MODEL_DIR/marmousi_94_288.json"
```

若 checksum、shape、axis order 或速度范围失败，停止运行并恢复经过核验的副本；不要修改
原始 MAT/NPY 来绕过验证。

### CPU 容器报告 CUDA 不可用

这是预期行为。CPU 服务必须以 `device=cpu` 提交。需要 GPU 时关闭 CPU 服务，按 3.3 节
启动 GPU profile。

### GPU 容器中 `torch.cuda.is_available()` 为 false

依次检查宿主机 `nvidia-smi`、NVIDIA Container Toolkit、Docker `--gpus all` 测试、GPU
profile 是否实际启动，以及构建日志是否使用 `cu130` 而非 CPU index。

### Deepwave 尝试源码构建或报 `nvcc` 错误

确认 Python/平台和 `deepwave==0.0.27` wheel 匹配。不要直接安装完整 CUDA Toolkit；先
检查 pip 选择的 index、Python ABI 和架构。

### 端口已占用

宿主机入口会明确失败。先执行 `./stop.sh` 或 `docker compose down`，再检查：

```bash
ss -ltnp | grep -E ':(5000|8080)\b'
```

也可以只改变 Web 的宿主机映射：`FWI_WEB_PORT=18080 docker compose up -d`。
Agent 映射固定为宿主机 `127.0.0.1:5000`，因为当前静态 Web 配置使用该地址；容器
内部端口保持 8080/5000。

### 容器 unhealthy 或某个 Agent 退出

```bash
docker compose ps
docker compose logs fwi-agent
docker compose exec fwi-agent sh -lc \
  'for f in /opt/fwi-agent/examples/ai_orchestrator/logs/*.log /tmp/fwi-agent/logs/*.log; do echo "== $f =="; tail -n 80 "$f"; done'
```

失败任务的真实原因还应记录在 `/root/fwi-runs/<job_id>/status.json`、`progress.jsonl` 和
`run.log`；不要把失败迭代报告为成功。
