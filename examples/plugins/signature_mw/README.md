# signature_mw：统一署名（消息管线中间件示例）

演示 Plugin API 1.2 的 `outbound` / `delivered` 中间件与 `render_profile`（TelePilot 0.109.0+）。

- `outbound`（order 800，只匹配 `interaction_bot` 通道）：给所有插件发出的文本消息加署名；
  开启「表格紧凑」时不自己改表格，而是写 `render_profile: {"table": {"compact": true}}`，
  由平台 `core.rich_compact` 统一渲染（外观与插件直接写 `compact_tables: true` 完全一致）；
- `delivered`：投递结果确定后异步收到摘要（不含正文），这里按动作类型累计成功 / 失败次数；
- `sources: ["self", "any_plugin"]` 需要 `permissions: ["message_middleware"]`。

只想让**自己的**表格紧凑时不需要中间件：在 `plugin.json` 写 `"render_profile": {"table": {"compact": true}}`，
或在单条动作上写 `render_profile` / `compact_tables: true` 即可。

完整说明见 `docs/PLUGIN-API-REFERENCE.md` 的「消息管线中间件（middleware）」。
