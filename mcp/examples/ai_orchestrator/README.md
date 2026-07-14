# 已退役的 Orchestrator 快照

该目录只保留为早期开发参考，不属于当前构建、启动或安全支持面。它包含已退役的
共享历史与旧启动方式，不得用于真实 API Key 或实验数据。

唯一受支持的实现位于 [`examples/ai_orchestrator`](../../../examples/ai_orchestrator)，并通过仓库根目录的
`./start.sh` / `./stop.sh` 运行。本目录的 CMake 入口会主动拒绝构建，避免误启动旧栈。
