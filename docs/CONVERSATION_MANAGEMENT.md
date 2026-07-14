# 对话上下文与历史管理

本文说明 Web、HTTP/gRPC、Orchestrator 和 Redis 之间的真实会话语义，以及当前版本的
保留、裁剪和隐私边界。

## 当前数据流

```text
浏览器对话（localStorage 缓存）
  └─ 稳定 contextId
      ├─ HTTP :5000 ───────────────┐
      └─ HTTP bridge :50052 → gRPC :50051 → A2A
                                    │
                                    ▼
                            C++ Orchestrator
                              ├─ Redis canonical transcript
                              ├─ bounded ContextWindow
                              └─ per-context FWI tool state
                                    │
                                    ▼
                                   LLM
```

- 每个 Web 新对话在首轮发送前生成独立的 `contextId`，HTTP 与 gRPC 使用同一个 ID。
- 如果非 Web 客户端省略 ID，Orchestrator 会生成 ID；非空 ID 必须匹配
  `[A-Za-z0-9][A-Za-z0-9_-]{0,127}`。
- Orchestrator 是用户可见 transcript 的唯一写入者，存储键为
  `a2a:session:<contextId>`。专业 Agent 从 A2A DataPart 接收有界、只含
  `user/assistant` 的 context envelope，不再读写共享 legacy history。
- 同一 `contextId` 的请求在进程内串行处理，避免两轮回答交错写入。
- FWI 的“刚才任务”按 `contextId` 保存到独立 tool state；不同对话不会共用一个全局
  `last_job_id`。重启恢复是 best-effort：先读最近 tool state，再只从最近 30 条 assistant
  transcript 中兼容恢复；Redis 写入失败时可能无法恢复“刚才任务”。

## 发给模型的上下文

`ContextWindow` 会执行：

1. 仅读取之前已完成的回合；当前 user 消息作为本轮 prompt 单独发送，
   等回答生成后再与 assistant 消息一起原子落盘；
2. 先组成最近且完整的 user/assistant 回合；中断产生的孤立消息不会与别的回合拼接；
3. 限制消息数、总序列化字节数和单消息 UTF-8 字节数；配置名中的 `CHARS` 为兼容
   现有环境变量而保留，不代表 Unicode 字符数或 tokenizer token 数；
4. 总预算按 JSON 转义后的真实序列化字节检查，超长消息 UTF-8 安全裁剪；
5. 历史经过严格角色校验后，以原始 user/assistant role 发送给 LLM，不再提升为 system role；
6. 工具返回同样标为不可信，并额外限制长度。

当 `LLM_PROVIDER` 为 `deepseek`、`qwen` 或 `openai` 时，当前问题和上述有界历史会发往
对应的固定官方 endpoint，并受该 provider 的数据政策约束。不要在聊天中粘贴 API Key、
未授权数据或不允许交给外部服务的科研内容。`local` provider 只允许 loopback endpoint，不读取云端 key。

固定路由的意图判断和 Agent-RAG 检索也会读取更小的最近上下文，因此“继续刚才第二点”
这类追问不再完全依赖当前一句话猜路由；它仍不是持久化工作流状态机，复杂省略表达可能误判。

默认值：

```dotenv
CONTEXT_MAX_MESSAGES=10
CONTEXT_MAX_CHARS=12000
CONTEXT_MAX_MESSAGE_CHARS=4000
```

JSON/A2A 客户端的 `historyLength` 现在会生效，`0` 表示不发送历史，只能缩小窗口，不能
突破服务端上限。proto3 gRPC 的标量 `0` 无法区分“未设置”，因此直接 gRPC 客户端的 `0`
沿用服务端默认值。预算是转义后 UTF-8 字节，不是模型 tokenizer 的精确 token 计数。

## 历史保留和重启恢复

内置 Redis 默认使用仓库外 AOF 目录：

```dotenv
REDIS_PERSISTENCE=true
# REDIS_DATA_DIR=/root/.local/state/cpp-fwi-agent/redis
CONVERSATION_MAX_STORED_MESSAGES=200
CONVERSATION_TTL_SECONDS=2592000
```

Session TTL 是每个完整回合成功落盘后重置的 30 天滑动保留期。user/assistant 两条消息
的追加、长度裁剪和 TTL 刷新在同一个 Redis Lua 操作中原子完成；落盘失败会显式使请求失败，
不会把半个回合当成已保存。FWI tool-state 的 TTL 只在持久化新 job_id 时刷新，普通聊天
不会刷新它。运行期 Redis 读失败会记录错误并退化为空历史，因此不应把 Redis 当作无故障的强一致存储。

内置 Redis 使用 AOF `appendfsync everysec`：正常停止/重启可恢复，但异常断电或崩溃时最后约
1 秒写入可能丢失，且当前没有备份、复制或 HA。若连接的是已经运行的外部 Redis，启动器不会修改它的磁盘持久化策略，
`REDIS_PERSISTENCE=false` 也不会关闭该外部实例的 AOF/RDB；应用仍会设置
session/tool-state TTL。当前不支持 Redis 密码、ACL 或 TLS，因此只应使用同机受控 Redis。

Docker Compose 将 Redis AOF 放入命名卷 `conversation-state`。`docker compose down`
保留该卷；确认不再需要后端历史时使用 `docker compose down -v` 删除该 Redis 卷。
该命令不会删除浏览器 localStorage，也不会删除 bind mount 中的 FWI 结果。

敏感或临时实验可设置 `REDIS_PERSISTENCE=false`。停止内置 Redis 后后端历史会丢失，
浏览器仍可能保留本地历史，需要在 Web 中“清空全部历史”。localStorage 与 Redis/AOF
都是明文数据；需要更强隐私时，应使用加密磁盘、受控浏览器并缩短 TTL。

## Web 历史行为

- 刷新页面会恢复最近活动的本地对话、transport 模式、消息和对应的 FWI job 面板。
- “清空对话”会真正删除当前本地记录并创建新的 `contextId`；侧栏可删除单条或清空全部。
- 发出问题后立即保存 user 消息；失败和浏览器中止也有状态记录。
- 切换对话或清空时会中止当前浏览器请求，过期响应不能写入另一个对话。
- 流式请求失败不会自动以同步请求重放，避免已执行的 FWI 被重复提交。
- 浏览器中止只代表停止等待，不代表服务端/FWI 已取消；请求已发出后的失败记为
  `outcome_unknown`，界面会提示先查任务状态，不要直接重提。
- localStorage 会做 schema/类型/容量检查；它仍是浏览器缓存，不是多用户服务端会话 API。
- localStorage 只负责 UI 回放，不会在每次请求中重传给模型；模型上下文以 Redis 为准。
  Redis TTL 到期或长度裁剪后，页面可能仍显示旧消息，但 Agent 已不再记得它们。

## 当前仍有的限制

- 没有登录、`user_id/tenant_id` 所有权校验；系统只适合 loopback 单用户实验，不能直接暴露
  到公网或不可信局域网。
- 同一 `contextId` 只在单个 Orchestrator 进程内加锁；没有分布式锁、leader 选举或租户隔离，
  不支持让多个 Orchestrator 负载均衡处理同一 context。
- 尚无正式的 Conversation list/history/delete HTTP API，浏览器历史与 Redis 历史没有跨
  设备同步。
- 尚未实现滚动摘要、精确 tokenizer 预算、语义长期记忆或结构化 tool-call message。
- 升级前遗留的 `a2a:history:*`/`a2a:task:*` 键不会自动安全擦除；新专业 Agent 已不再使用或
  续写它们。需要强清理时应在停机后删除专用 Redis 数据目录/卷。
- 尚无服务端 request-id 幂等表或 request→FWI job 查询；网络在提交后断开时只能报告
  “结果未知”，无法证明没有创建任务。
- Web 仍从固定第三方 CDN 加载样式/Markdown/公式依赖。服务端 CSP 限制了连接、图片、
  frame 和对象来源，fallback 也拒绝主动脚本 URL，但高敏感离线部署仍应把依赖本地化。
- 删除 localStorage 不等于立即安全擦除 Redis AOF 的历史磁盘块；强删除需要停止服务并删除
  对应受控数据目录或卷。

下一阶段建议先增加身份所有权和 Conversation API，再引入“滚动摘要 + 最近完整回合 +
结构化 tool result”；不要在隔离和幂等性尚未完备时直接加入复杂的向量长期记忆。
