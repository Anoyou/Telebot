# ui_page_demo：插件自带页面与菜单示例（Plugin API 1.4 / TelePilot 0.111.0+）

- `ui.pages[dashboard]`：`ui/index.html`，Bridge 权限 `config.read` / `cards.read` / `actions.invoke`，只允许调用 `summarize`；
- `ui.actions`：插件详情菜单「生成计数摘要」（调 `on_ui_action`），账号详情菜单「打开 ping 面板」（打开页面）；
- `ui.cards[ping_stats]`：账号详情卡片，`on_ui_card` 只读访问 `ctx.storage`（能力 `hooks.storage_read`）。

页面里用宿主注入的 `window.telepilot`：`ready()`、`theme()`、`config()`（密钥字段 `api_token` 读不到）、`card(id)`、`action(id, payload)`。页面不能联网（CSP `connect-src 'none'`），也拿不到登录态。

旧宿主（< 0.111.0）会忽略 `ui.pages` / `ui.actions`，卡片钩子里 `ctx.storage` 为 None（示例已兼容）。
