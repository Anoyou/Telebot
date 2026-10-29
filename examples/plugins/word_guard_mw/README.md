# word_guard_mw：敏感词守卫（消息管线中间件示例）

演示 Plugin API 1.2 的 `inbound` / `outbound` 中间件（TelePilot 0.109.0+）。

- `inbound`（order 100）：群里收到含敏感词的消息时 `mw.drop(...)`，本账号所有 Event Bus 插件都收不到它；
- `outbound`（order 200，`sources: ["self", "any_plugin"]`）：任何插件要发的消息含敏感词时打码（默认）
  或 `mw.reject(...)` 整条拒绝；`stream_preview` 让 AI 流式回复的中间态也打码；
- 需要 `permissions: ["message_middleware"]`（inbound 与 `any_plugin` 都要求）。

要点：

- 中间件声明只写在 `plugin.json` 的 `middleware`，`manifest.py` 不用写；
- 钩子拿到的嵌套值是只读视图，改 `rich_message` 时先 `mw.thaw(...)` 拿副本，改完整体赋回；
- 只能改正文 / Rich / 按钮等白名单字段；改 `chat_id`、通道、金额会被整次回滚并计一次失败；
- 超时（这里声明 100 ms）或抛异常时按原样放行（fail-open），并计入插件运行统计与熔断。

完整说明见 `docs/PLUGIN-API-REFERENCE.md` 的「消息管线中间件（middleware）」。
