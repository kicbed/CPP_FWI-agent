# deploy 目录说明

当前唯一推荐的日常启停入口位于仓库根目录：

```bash
./start.sh
./stop.sh
```

完整部署说明统一维护在 [docs/DEPLOYMENT.md](../docs/DEPLOYMENT.md)。本文件不再
重复维护另一套环境、编译和启动步骤，以免与实际实现发生偏差。

- 模型白名单和反演参数：[MODEL_GUIDE.md](../docs/MODEL_GUIDE.md)
- 浏览器测试教程：[FRONTEND_TEST.md](../docs/FRONTEND_TEST.md)

## 旧脚本的定位

| 路径 | 当前用途 |
|---|---|
| `deploy/scripts/start.sh` | 根 `start.sh` 的兼容入口 |
| `deploy/scripts/stop.sh` | 根 `stop.sh` 的兼容入口 |
| `deploy/scripts/start_http.sh` | 兼容入口，不再维护独立启动逻辑 |
| `deploy/scripts/start_web.sh` | 兼容入口，不再维护独立启动逻辑 |
| `deploy/scripts/start_grpc.sh` | 设置 `ENABLE_GRPC=true` 后转交根启动器的兼容包装 |
| `deploy/scripts/setup_embedding.sh` | 可选的本地 Embedding 准备工具 |

`examples/ai_orchestrator/start_system.sh` 是根启动脚本使用的内部组件启动器，
不应再作为 README 或新用户教程中的首选命令。

旧 gRPC 脚本不再自动下载/启动 Embedding、调用 `pkill`/`fuser` 或拉起前台客户端；
需要 gRPC Web 模式时直接使用 `ENABLE_GRPC=true ./start.sh`。

## 不进入仓库的内容

以下内容应在目标机器上创建，不应提交：

- `build/` 和 `mcp_server_integrated/build/`
- Python 虚拟环境
- `.env` 与任何 API Key
- Marmousi 或其他模型文件
- 日志、PID、缓存和 `/root/fwi-runs` 运行结果

Docker 镜像同样从源码自行编译，并通过只读 bind mount 使用模型；详见统一部署
文档。
