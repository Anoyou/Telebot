# TelePilot 插件速查表

先读 [插件开发铁律](./PLUGIN-RULES.md)，再用本页回忆字段名和常用模式。

- 必须理解 TelePilot 插件按个人可信插件模式运行：管理员安装并启用后，视为信任插件业务逻辑；平台负责事件信封、MessageOps 代发、Trace、风险提示、急停和审计。
- 最快命令插件可用简单模式 SDK：`from telepilot import plugin`，再写 `@plugin.command("ping")` 的单函数 `async def ping(ctx): await ctx.reply("pong")`；逻辑只写在 `__init__.py`，目录名就是插件 key。但目录里仍要有 `plugin.json`（加载前兼容预检与安装校验都读它），安装通道还要求 `manifest.py`、`plugin.py` 文件存在（简单模式可留空），见 [Quickstart](./PLUGIN-QUICKSTART.md#0-最快路径简单模式-sdk)。
- 简单模式不写 `PLUGIN_CLASS` / `MANIFEST` 时，loader 会从装饰器合成隐式插件类和 Manifest；当前隐式权限是 `read_event` + `send_message`，命令默认是 owner-only 的账号命令。
- 简单模式适合快速账号命令、小工具和内部玩法；需要 `plugin.json` 展示字段、`event_subscriptions`、配置 schema、HTTP/AI 权限、按钮、Inline、付款或会话时，用显式 Manifest 模式。
- 当前 `tp_plugin new` 没有 `--profile simple`；可选 profile 只有 `session_game`、`command`、`passthrough`。其中 `session_game` / `command` 仍生成 `interaction_entries + on_interaction` 兼容桥，只用于迁移或作为包结构起点，新标准插件应改成 `event_subscriptions + on_event`。
- 必须把新 Telegram 插件写成 Event Bus + Trace + MessageOps：`plugin.json` 写 `usage`、`event_subscriptions`、`capabilities`，插件只读标准事件信封，动作只通过 `ctx.messages` 或标准 action 返回。
- 禁止把 `interaction_entries`、旧交互规则、旧平铺 payload、`notice` / `bbot_notice` / `notice_bot` 作为新插件模板；这些只用于迁移说明。
- 显式 Manifest 模式必须保留最小目录：`plugin.json`、`manifest.py`、`plugin.py`、`__init__.py`。`plugin.json.name`、`MANIFEST.key`、插件类 `key` 必须一致。
- `requires_platform_capabilities` 新装/升级必填（`plugin.json` 与 `Manifest` 同值，允许 `[]`）；`min_telepilot_version` 至少 `0.72.0-beta.2`（更早的平台不认识这个 `Manifest` 参数，导入即报错），用到更新能力再按 [版本与兼容](./PLUGIN-API-REFERENCE.md#版本与兼容) 提高。0.107.0-beta.1 起宿主比较考虑 beta 号（可写 `0.106.0-beta.12`；不带 beta 号的声明仍只比 `x.y.z`），更旧的宿主只看 `x.y.z`，所以 beta 才有的能力仍要探测。
- 新能力不抬 `min_telepilot_version`：先探测、旧平台回退，写法见 [新旧平台兼容写法](./PLUGIN-API-REFERENCE.md#新旧平台兼容写法)（按钮路由、`host.read`、卡片、配置钩子、`x-ui-visible-if` 用 `not_in`、`stream_complete(extra_body=)`）。`on_ui_card` / `on_config_validate` 等在主进程调用，`ctx` 没有 `storage` / `messages`。
- 能力探测（0.107.0-beta.1+）：`caps = getattr(ctx, "capabilities", None)`，有就用 `caps.has("messages.guest_reply_markup")`，没有再退回 `getattr(ctx.messages, "guest_reply_markup", False)`；能力全集与门槛以宿主注册表为准（`GET /api/plugins/capabilities`，见 [能力协商与 Plugin API 版本](./PLUGIN-API-REFERENCE.md#能力协商与-plugin-api-版本)）。`plugin_api: "^1.0"` 只写在 `plugin.json`，不要传给 `Manifest(...)`（旧宿主导入会失败）。
- 插件详情「运行统计」按钩子显示调用数、耗时、失败数和已弃用接口调用次数（`GET /api/plugins/{key}/stats`，见 [钩子统计与弃用登记](./PLUGIN-API-REFERENCE.md#钩子统计与弃用登记)）；看到弃用计数就按 [弃用登记](./PLUGIN-API-REFERENCE.md#弃用登记) 换成替代写法。
- 必须把 `usage` 写成插件中心可展示的使用说明；有 `config_schema` 时也可以补 `x-usage-guide` / `x-usage-steps`，但不要只靠口头说明。
- **声明式 UI（0.110.0+，Plugin API 1.3）**：配置控件以 `schemas/plugin-ui-widgets.json` 为准（Web / iOS 全部实现，未知 `x-ui-widget` 只读并提示更新 App）；多 Token 字段声明 `x-secret-list` 由宿主遮罩、按 id 保留原密钥；`on_config_validate`（2 秒，返回 `{config}` 或 `{errors}`）/ `on_config_saved`（后台）；`plugin.json` 的 `ui.cards` 在插件详情 / 账号详情 / 概览放原生卡片（stats / kv / table / list / buttons，数据来自 `reads` 或 `on_ui_card`，按钮调 `on_ui_action`）。先 `ctx.capabilities.has("ui.cards")` 等探测，详见 PLUGIN-API-REFERENCE「声明式 UI」。
- **插件页面与菜单（0.112.0+，Plugin API 1.4，iOS 0.15.0+）**：`plugin.json` 的 `ui.pages` 声明插件目录内的 HTML 页面（Web iframe sandbox / iOS WKWebView，严格 CSP 不能联网），页面只能经 `window.telepilot` Bridge 调 `ready` / `theme` / `config`（去密钥）/ `card` / `action`，权限按页面 `bridge` 声明由服务器逐次校验；`ui.actions` 声明菜单项（调 `on_ui_action` 或打开页面）。卡片 / 配置钩子里 `ctx.storage` 变为只读（旧宿主为 None，先判断）；`ai.models@1` 只增 `display_name` / `available` / `cost_tier` 等字段。详见 PLUGIN-API-REFERENCE「插件页面与菜单」。
- **通用规则页（0.110.0-beta.2+）**：多规则插件写 `x-ui-mode: "rules"` + `x-rule-schema`（每条规则的字段）/ `x-rule-summary`（列表摘要）/ `x-dry-run`（试运行样例字段，`false` 关闭），字段可用 `x-ui-visible-if: {field, equals|in|not_in}` 按同级字段显隐，数组 `x-ui-widget: "textarea"` 每行一项；试运行导出纯函数 `dry_run_rule(config, sample) -> {matched, output, logs}`（3 秒，无副作用）。不要再写专属规则页，详见 PLUGIN-API-REFERENCE「规则配置页」「规则试运行协议」。
- **转账通知解析（0.111.0-beta.1+，能力 `payment.sources`）**：`plugin.json` 声明 `payment_sources: [{"id": ...}]`，模块导出纯函数 `parse_payment_notice(text, meta) -> {payer_name, receiver_name, amount, payer_user_id?, receiver_user_id?, unit?} | None`（可 async，1 秒）。宿主只在可信转账 Bot 白名单通过后调用，插件只返回解析结果、不结算；双跑对照期默认以宿主旧解析为准，不一致记日志 / Trace / 运行统计，系统设置「转账通知以插件结果为准」打开后才改用插件结果，出错一律回退。必须通过 golden（`payment_notice_golden.json`）100% 一致。详见 PLUGIN-API-REFERENCE「转账通知解析（payment_sources）」。
- 禁止用空 `usage` 绕过检查；缺失会触发高级规范警告，插件库维护插件和示例插件都必须完整声明。
- `event_subscriptions[].events` 可选值：`message`、`command`、`keyword`、`callback_query`、`inline_query`、`chosen_inline_result`、`payment_confirmed`、`webhook`、`session_close`、`message_edited`、`session_expired`、`guest_message`、`all_events`、`all_messages`；`all_messages` 只等于 `message` / `command`，`message` / `all_events` 都不包含 `guest_message`。
- `event_subscriptions[]` / `interaction_entries[]` 可写 `timeout_seconds` 放宽单次处理时限（默认 15 秒，平台上限 90 秒，0.106.0-beta.1+）。
- `event_subscriptions[].source` 常用值：`userbot`、`interaction_bot`、`external_payment_notice`、`webhook`。
- `event_subscriptions[].scope` 可选值：`all_allowed_chats`、`owner_only`、`known_users`、`rule_bound`、`inline_all`、`guest_all`、`own_buttons`（0.108.0+，本插件用 `{"callback": {"data": ...}}` 发出的按钮被点击时投递，与会话范围无关）；Inline 插件必须显式用 `inline_all`，访客消息任意会话用 `guest_all`；inline 消息（含访客回复）上的按钮点击没有 chat，只有 `inline_all` / `guest_all` 的 `callback_query` 订阅能收到（0.106.0-beta.17+）。
- 账号级“允许会话”留空表示全部会话；列表非空时才只允许名单内会话。插件自定义的 `allowed_chat_ids=[]` 不自动继承这条语义。
- 交互 Bot 事件的 `all_allowed_chats` 只看账号级“允许会话”（会话白名单），不受交互规则（关键词 / 游戏 / 支付）配置的群范围影响；名单外会话丢弃并记 `reason=chat_not_in_allowed_chats` 日志（详见 API 参考「交互 Bot 事件的会话范围」）。
- `known_users` 只认平台 state 提供的真实集合，不会自动把当前 sender 算进去。
- `filters` 支持的键：`keyword` / `keywords`、`contains`、`command` / `commands`、`callback_data`、`chat_id`、`event_type`、`rule_id`、`hook_key` / `hook_keys`；未知 key 会保留但不生效，并告 warning。
- 严格来说只有 `telegram_direct_passthrough` 叫裸直通；Event Bus、会话入口、legacy hook 都是消息分发/会话路由。
- `capabilities.telegram_native_raw` 是高风险能力声明；需要原生 Telegram 字段时写 `enabled=true`、`reason`、`sources`，并处理 `native_raw_meta.enabled=false` 的降级。
- 标准事件信封优先读：`source`、`message`、`chat`、`sender`、`actor`、`source_actor`、`player`、`payment`、`reply_to`、`trigger`、`session`、`native_raw_meta`。
- 新插件读取文本优先用 `payload["tp_event"]` 或 `event_from_interaction_payload(payload)`；不要用 `payload["text"]` / `payload["chat_id"]` / `payload.get("message")` 当主路径。
- 互动玩法优先写成一个 `on_event(ctx, payload)`，在同一个入口里处理 `command`、`keyword`、`payment_confirmed`、`message`、`callback_query`、`session_expired`。
- 会话状态放进 `session.data`，状态变更返回 `update_session`；Event Bus 订阅入口首次建局先返回 `start_session`，已有有效会话时才可直接更新。不要再靠进程内 dict/lock 才能续局。
- `source` 描述事件类型和来源通道；`actor` 是当前行为主体；`sender` 是发出消息的人或 Bot；`source_actor` 可表示可信外部通知 Bot；`player` 是付款绑定玩家；`payment.status=confirmed` 才能作为到账依据。
- `session.channel` 表示当前整段会话默认收发通道；普通发送动作不用手写 `send_via`，平台会继承会话通道。
- 普通消息回复使用 `ctx.messages.send(...)` 或返回 `{"type": "send_message", ...}`；标题、任务列表、折叠详情、表格等原生格式使用 `ctx.messages.send_rich(html=...)` 或 `send_rich_message` action；图片题面可用 `ctx.messages.send_photo(..., save_message_id_key="round")`，后续用 `ctx.messages.edit_caption(message_id_key="round", caption="...")` 原地更新 caption。
- 编辑已发出的 Rich Message 用 `ctx.messages.edit_rich(chat_id=..., message_id=..., html|markdown|blocks=...)`（仅交互入口的缓冲 facade 有），对应带 `rich_message` 字段的 `edit_message` action；访客模式占位消息用 `edit_message` action 的 `inline_message_id` + `rich_message` 原地编辑（实时上下文先看 `getattr(ctx.messages, "live_inline_edit", False)`）。
- 表格默认样式（0.106.0-beta.15+）：平台发出的所有 Rich 表格默认带边框 + 隔行着色——Markdown 管道表格转 `<table bordered striped>`（保留表头 th 与对齐），HTML `<table>` 缺 `bordered` / `striped` 就补，blocks 缺 `is_bordered` / `is_striped` 就补 `true`；显式值优先（blocks 写 `false`，HTML 写 `bordered="false"` / `striped="false"` 即关闭）。插件无需改动，能力标记 `ctx.messages.rich_table_default_style`。
- 紧凑表格（Bot API 10.3，0.106.0-beta.12+）：`send_rich` / `edit_rich` / `answer_guest_query` 传 `compact_tables=True`，或在 `send_rich_message`、带 `rich_message` 的 `edit_message`、`answer_guest_query` action 上写 `"compact_tables": true`；平台给 blocks 加 `is_compact`、HTML 表格加 `compact`、Markdown 管道表格转 `<table bordered striped compact>`（`compact` 只缩小内边距，边框与隔行着色由上面的默认样式负责；beta.13/14 为 `<table bordered compact>`，blocks/HTML 不自动加边框）。先判断 `getattr(ctx.messages, "rich_table_compact", False) is True`，旧平台不要传；Bot API 拒绝时平台去掉标记重发一次。也可以直接写 `<table compact>` / `"is_compact": true`。
- `send_rich` 的 `html` / `markdown` / `blocks` 必须三选一，继续声明 `send_message` 权限；默认走 Interaction Bot。显式 `userbot_reply` 使用 Layer 228+，支持 HTML、Markdown 和纯文本 blocks，并要求 Premium 与 `rich_message_posting`；复杂/媒体 blocks 失败时不会静默退化。
- **0.108.0+ 推荐**（先 `ctx.capabilities.has("messages.callback_routing")`）：按钮写 `{"text": "重试", "callback": {"data": "retry:42"}}`，订阅 `events=["callback_query"]`、`scope="own_buttons"`，从 `payload["callback"]["data"]` 读回；访客回复 / inline 消息上的按钮同样适用。已有 `all_allowed_chats` / `guest_all` 的 `callback_query` 订阅、还要兼容旧宿主的插件**只改按钮写法、不加 `own_buttons` 订阅**（同一插件多条订阅不去重，同一点击会投递两次；旧宿主把 `own_buttons` 当 `all_allowed_chats`），`payload["callback_data"]` 会还原成插件自己的 data。同版本还有：`ctx.host.read("account.admins@1")`（plugin.json 声明 `reads`）、动作 `bot_api_extra`、`ctx.ai.complete(extra_body=...)`，详见 PLUGIN-API-REFERENCE「通用交互原语」。
- **0.109.0+ 消息管线中间件**（`plugin.json` 的 `middleware`，`plugin_api: "^1.2"`）：`outbound` 在投递前改所有 / 本插件动作的正文、Rich、按钮或 `render_profile`，`mw.reject()` 拦截；`inbound` 在路由前打 `annotations`、规整 `text` 或 `mw.drop()`；`delivered` 异步拿投递结果摘要。处理别的插件的动作或用 inbound 要 `permissions: ["message_middleware"]`；嵌套值是只读视图（`mw.thaw()` 拿副本后整体替换），改 `chat_id` / 通道 / 金额等非白名单字段会被整次回滚；单钩子 250 ms、每账号每阶段最多 8 个、失败一律按原样放行。只想让自己的表格紧凑就写 `"render_profile": {"table": {"compact": true}}`，不用写中间件。示例 `word_guard_mw`、`signature_mw`，详见 PLUGIN-API-REFERENCE「消息管线中间件」。
- 当前 Interaction Bot 发按钮、用户点击：订阅 `source=["interaction_bot"]`、`events=["callback_query"]`、`scope="all_allowed_chats"`，用 `answer_callback` ACK；不依赖旧 Interaction Bot 规则或活跃会话。
- 第三方 Bot 发按钮、TelePilot UserBot 主动点击：插件声明 `permissions=["click_bot_button"]`，在 UserBot 执行链路调用 `ctx.messages.click_callback_button(chat_id=..., message_id=..., row=..., column=..., expected_bot_id=..., expected_button_text=...)`；Interaction Bot 插件入口不支持。这不是 `callback_query`，也不能用 `answer_callback`。
- 平台只允许 callback 类型，重新读取 Telegram 消息和 callback data，并统一执行 Bot/文字校验、限流、Trace、ActionEvent、dry-run 和账号级物理点击锁；明确成功后保护 20 秒，超时/未知结果保护 5 分钟，Redis 不可用时拒绝点击。`message.buttons[row][column].click()` 已被安全阻断。
- userbot 会话没有原生按钮，平台会把按钮降级成文本编号，并把玩家回复合成为 callback；此时 `source.synthetic="text_button"`。
- 免费参与、按钮加入、互动游戏可按自身玩法保存完整业务状态；如果只是为了后续发奖锚点，记录玩家 `tgid` 即可。发奖动作优先用 `payout`，也可走 `userbot_reply` 并携带 `reply_to_user_id`，平台会搜索该玩家近期发言作为回复锚点，插件不要自己遍历群消息。已有 `reply_to_message_id` 时优先用消息 id；找不到锚点时动作失败并写入日志，默认提示 `未找到对应用户（用户 ID）的近期消息。`，可用 `reply_anchor_missing_text` 自定义提示。
- Inline 插件返回 `answer_inline_query`；选择结果进入 `chosen_inline_result`，用于记录选择、结算或后续状态。
- 访客模式（Bot API 10.0 Guest Mode）：订阅 `source=["interaction_bot"]`、`events=["guest_message"]`，`scope` 用 `guest_all`（任何会话）或 `owner_only` / `known_users` / `all_allowed_chats`。每条访客消息只能回复一次：可以直接返回 `send_message` / `send_rich_message`（平台合并后统一 `answerGuestQuery`），或返回 `answer_guest_query` 自定义结果；`message` / `all_events` 订阅不会收到访客消息。访客回复上的 callback 按钮（0.106.0-beta.17+，先看 `getattr(ctx.messages, "guest_reply_markup", False)`）：`answer_guest_query` 的 `reply_markup` 保留 `callback_data`，`apply()` 结果带 `inline_message_id`；点击以 `callback_query` 投递给 `scope: "guest_all"` 的订阅（没有 chat/message，只有 `inline_message_id`、`inline_callback` 与点击者 `sender.user_id`），用 `answer_callback` + `edit_message(inline_message_id=..., reply_markup=...)` 响应。
- 付款/发奖插件返回 `settlement` 或 userbot 受控动作；普通 Bot 只公告结果，不直接执行转账、催付或发奖。
- 常见 action：`send_message`、`send_rich_message`、`send_photo`、`send_file`、`edit_message`、`edit_caption`、`delete_message`、`pin_message`、`click_callback_button`、`answer_callback`、`answer_inline_query`、`answer_guest_query`、`payout`、`update_session`、`settlement`、`result`、`end_session`。
- `send_via` 只在高级覆盖时使用 `interaction_bot`、`userbot_reply` 或 `auto`；旧 `notice` 值应返回迁移错误，不能静默执行。
- 默认 `send_message` / `send_photo` / `send_file` 等动作按 `parse_mode="plain"` 发送；只有显式声明 `parse_mode="html"` 时才启用普通消息 HTML。`send_rich_message` 使用独立的 Rich HTML/Markdown/blocks 语法，动态 HTML 文案同样先转义再拼标签。
- 强按钮玩法可通过 `interaction_trigger_modes=keyword_only` 关闭命令触发；manifest 可用 `default_trigger_modes` 提供默认值。
- `callback_fast_ack=true` 适合耗时按钮入口；启用后晚到的 `answer_callback` 不再真正调用 Telegram ACK。
- `ctx.http` 需要 `permissions=["external_http"]` 和 `allowed_hosts`。
- `ctx.ai` 需要 `permissions=["ai_text"]`，复用平台 LLM Provider、fallback、预算和 usage 记录；`run_agent` 另需 `ai_agent` + `capabilities.agent_tools`。0.106.0-beta.1+：`complete(web_search=True)` 原生联网搜索、`complete(images=[...])` 与 `fetch_bot_image(file_id)` 识图（需 `ai_vision`），能力不可用时抛 `AICapabilityError`；0.106.0-beta.2+：`complete(thinking="disabled")`，`AIResult.text` 只含最终答案、推理在 `reasoning`。
- `ctx.skills` 需要 `permissions=["agent_skills"]`（0.106.0-beta.1+，旧平台为 `None`）：`list` / `load` / `read_file` / `bundled` 只读获取管理员对插件启用的 Agent Skills 文本，插件目录 `skills/<name>/SKILL.md` 会被登记为内置 skill。
- `ctx.messages` 分两种：交互入口（Event Bus / `on_interaction`）里是缓冲 facade，有 `send_photo` / `send_file` / `edit_rich` / `edit_caption` / `update_session`；命令、后台任务、scheduler、legacy `on_message` 里是 live facade，没有这几个 helper，要用标准 action + `ctx.messages.apply([...])`。
- `ctx.client` 只保留给管理员命令和高级兼容场景；远程插件仍不能直接拿 token、session、Bot API client 或 live event。
- `ctx.conversation()` 目前只适合获得真实 `TelegramClient` 的 builtin/内部兼容代码；普通安装插件拿到 `SandboxClient`，无法可靠注册会话 handler 或发送 raw MTProto，不应依赖它。
- 安装插件的 `ctx.redis` 是带 `plugin_store:{account_id}:{plugin_key}:` 前缀的 `PluginRedisFacade`，不是原始 Redis client；`keys`、`scan`、`eval`、`pipeline`、`pubsub` 等操作不可用，普通状态仍优先用 `ctx.storage`。
- `command` 只保存裸指令名，不保存前缀；schema/usage 模板用 `{prefix}`，运行时帮助与错误示例必须调用 `from app.worker.command import current_command_prefix`，使用 `current_command_prefix(fallback=",")`。不要从 `ctx.account_config` 读取系统前缀，也不要硬编码逗号。
- `on_command(ctx, cmd, args, event) -> bool` 保留给账号主人/授权管理员命令；群友公开触发走 Event Bus 订阅。
- `on_message` 是旧消息监听兼容 hook；新增 Telegram 交互优先写 `on_event` 标准事件入口，`on_interaction` 只作为迁移桥。
- 已有 `interaction_entries` 插件迁移时，要把入口事件映射到 `event_subscriptions`，把 `payload_contract/result_contract/settlement` 转成标准信封和标准 action。
- `interaction_entries[].session_scope` 的迁移含义：群局映射为 `session.scope=chat`，个人流程映射为 `session.scope=user`，一次性动作映射为无持久 session。
- 改过入口 key 的老插件：存量规则里的旧 key 写进正式入口的 `legacy_entry_keys`（`[{"key": "旧key", "session_scope": "chat"}]`，0.112.0-beta.3+，旧宿主忽略），不要为它写 `entry_policy` 或补成第二个正式入口，见 [旧入口 key 声明](./PLUGIN-API-REFERENCE.md#旧入口-key-声明legacy_entry_keys)。
- 规则 `concurrency=user` 只是触发频控粒度，不等于插件会话 key。
- 抢答、竞猜、抽奖要加锁和二次检查；禁用、热重载、超时和卸载都要清理状态。
- 外部请求必须有 timeout；日志里不要写 token、session、完整原生 payload、隐私消息或完整文件路径。
- 维护示例：新主模板看 `examples/plugins/event_bus_demo`；Webhook 看 `webhook_receiver`；HTTP 看 `with_http`；AI 看 `with_ai`；旧交互迁移看 `with_interaction`。
- 迁移边界：平台功能不伪装成插件；插件库维护插件必须完整声明；示例插件只用于学习和 CI；用户安装插件可保留代码但启用/更新时要提示规范警告。
- 验证示例：`backend/.venv/bin/python scripts/validate-plugin-examples.py`；检查已安装互动插件：`backend/.venv/bin/python scripts/validate-installed-interaction-plugins.py`；插件仓库的宿主兼容性检查：`TELEPILOT_REPO=<本仓库> python scripts/check_host_compat.py`（在 `Anoyou/telebot-plugins` 中运行，可加 `--plugins-root <本仓库>/examples/plugins` 检查示例）。

常见 `reason_code` 快查：

| reason_code | 含义 |
| --- | --- |
| `matched` | 订阅命中 |
| `subscription_not_matched` / `filter_not_matched` | 没有订阅命中 / 过滤条件未命中 |
| `plugin_disabled` / `plugin_load_failed` / `plugin_runtime_error` | 插件未启用 / 加载失败 / 运行异常 |
| `command_matched` / `command_not_matched` / `command_unauthorized` | 命令命中 / 未命中 / 权限不足 |
| `event_bus_delivery_disabled` / `inline_disabled` | 运维开关关闭 Event Bus 投递 / Inline |
| `native_raw_not_allowed` / `native_raw_skipped` | 未声明原生数据能力 / 本次未下发 |
| `send_channel_deprecated` / `unsupported_send_via` | 使用旧 notice 通道 / 未支持通道 |
| `premium_required` / `rich_message_posting_disabled` / `rich_message_blocks_unsupported` / `rich_message_media_unsupported` / `invalid_rich_message` | Userbot Rich Message 能力不足、输入超出当前适配范围或结构非法 |
| `already_acked` / `synthetic_callback` | fast-ack 已提前确认 / 文本按钮降级合成的 callback |
| `action_limit_exceeded` | 动作数量超出平台限制，后续动作会被截断并写入可见告警 |
| `bot_not_configured` / `bot_token_missing` / `userbot_offline` | Bot 未配置 / token 缺失 / UserBot 离线 |
| `payout_failed` / `telegram_api_error` / `trace_write_failed` | 发奖动作失败 / Telegram API 失败 / Trace 写入降级 |

最小单入口模板：

```python
from telepilot import plugin


@plugin.command("ping")
async def ping(ctx):
    await ctx.reply("pong")
```

显式 Event Bus 单入口模板：

```python
async def on_event(self, ctx, payload):
    event = payload["tp_event"]

    if event.type == "command":
        return [
            {"type": "start_session", "chat_id": event.message.chat_id},
            {"type": "update_session", "data": {"answer": "42"}},
        ]

    if event.type == "message" and event.message.text == "42":
        return [
            {"type": "payout", "amount": 88, "reply_to_user_id": event.sender.user_id},
            {"type": "end_session"},
        ]

    if event.type == "session_expired":
        return [{"type": "send_message", "chat_id": event.message.chat_id, "text": "本局已超时"}]

    return []
```

Rich Message 快速模板：

```python
await ctx.messages.send_rich(
    html=(
        "<h1>任务状态</h1>"
        '<ul><li><input type="checkbox" checked>已完成</li></ul>'
        "<details><summary>详情</summary><p>trace 已写入</p></details>"
        "<table bordered><tr><th>项目</th><th>状态</th></tr>"
        "<tr><td>Worker</td><td>正常</td></tr></table>"
    )
)
```
