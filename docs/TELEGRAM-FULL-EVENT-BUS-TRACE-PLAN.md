# TelePilot 全量 Telegram 消息事件总线与链路日志重构计划

## 0. 审查结论

这份计划方向正确，尤其是“先做 Trace / 日志，再做全量 Event Bus”。当前日志系统是 `runtime_log` / `audit_log` 的平面文本流，无法天然回答“消息走到哪一步、哪个插件为什么没执行、动作最后由谁发出”。继续美化旧日志页意义不大，应该以 `trace_id` 为主线重构。

但原计划还缺三块必须补齐的设计：

1. **原生数据免检通道**：需要给指定可信插件提供 `payload["native_raw"]`，否则做严格数字 ID 关系链风控时只能依赖平台投影字段，确实不够。
2. **Inline 模式闭环**：需要支持 `inline_query` 进入 Event Bus，并支持 `answer_inline_query` 动作；只支持群消息和按钮回调，交互能力是不完整的。
3. **Trace 与旧日志的关联边界**：旧 `runtime_log` 可以保留，但插件 `ctx.log`、Contract Guard、Delivery Executor、loader 错误都必须带上 `trace_id` / `plugin_key` / `entry_key`，否则新日志页还是会断链。

### 0.1 最终版口径

这份计划的“最终版”不是 0.38、0.39、0.40 三个互相独立的愿景版本，而是一个完整施工目标：

- 当前基线版本是 `0.37.0`。
- `0.38.0`、`0.39.0`、`0.40.0` 是可独立验收的发布检查点，便于并行、review、回滚和部署观察。
- 如果一次性在当前分支完成全部计划，最终发布版本应直接按实际落地范围定为下一个阶段性 minor（次版本），建议为 `0.40.0`，并在 `CHANGELOG.md` 中完整说明 0.37.0 之后的新增能力。
- 分支只有通过第 17 节最终验收矩阵后，才算“最终版完成”。只完成 Trace 或只完成 Event Bus 都不算最终版。

### 0.2 唯一产品模式

后续不再设计“标准模式 / 个人模式”双模式。TelePilot 的统一口径是：

- **个人可信插件标准**：插件和插件仓库由账号主人主动安装、更新和启用，插件风险由账号主人在清楚风险提示后自行承担。
- 平台不做公共插件市场式强沙箱，不把 Contract Guard 设计成业务拦截器。
- 平台仍保留最小客观边界：不下发凭据，不暴露 live client，不把普通 Bot 伪装成可转账主体，不让旧 `notice` / `bbot_notice` 继续作为发送通道。
- 平台必须把插件声明、实际调用、越声明调用、发送通道选择、失败原因和高风险能力写入 Trace，让账号主人能看见、能追责、能停用。

### 0.3 当前分支已半落地内容与缺口

本计划执行前必须承认当前分支已经有部分代码改动，后续不能从零重写，也不能假设它们已经完整：

已半落地：

- `event_trace` / `event_span` / `event_action` / `plugin_runtime_status` 模型和迁移已有雏形。
- `event_trace.py` Trace Service 已有雏形。
- 交互 Bot `message` / `callback_query` / `inline_query` / `chosen_inline_result` 解析已有雏形。
- Delivery Executor 已开始记录 action，并新增 `answer_inline_query` 雏形。
- `ctx.log` 已开始补 `trace_id`。

必须补齐后才能算完成：

- `native_raw` 当前不能默认下发给所有插件，必须按 `capabilities.telegram_native_raw.enabled=true` gate。
- `event_subscriptions` 和 `capabilities` 还没有完整进入 manifest、远程插件解析、feature matrix、WebUI 和 lint。
- Event Bus 服务和订阅匹配还没成为真实调度入口。
- UserBot/Telethon 消息和命令链路还没有完整 Trace/Event Bus 接入。
- Inline Query 当前只做到解析/动作雏形，还缺插件订阅投递、scope、rate limit、Trace 闭环和前端排障展示。
- 新日志中心 UI 还没重构，旧 `runtime_log` 表格不能作为最终日志页。
- Trace 保留策略、清理任务和 native_raw 持久化设置还没落地。
- 插件开发指南还没有按最终 Event Bus + Trace + MessageOps 口径重写。

### 0.4 最终版不可缩水清单

以下内容任何一项缺失，都只能称为“阶段性可测版本”，不能称为最终版：

- 所有 Telegram 来源先标准化为 `TelePilotEvent`，再进入 Event Bus。
- UserBot 消息、管理员命令、交互 Bot 消息、按钮回调、Inline Query、Inline 选择结果、外部转账通知消息全部有 Trace。
- 插件通过 `event_subscriptions` 接收事件，平台记录 matched / skipped / delivered 的稳定 reason_code。
- 插件收到统一事件信封，业务主路径不再依赖旧平铺 payload。
- `native_raw` 只给显式声明能力的插件，并在日志页可见是否下发、大小和是否持久化。
- 插件用 `ctx.messages` 或标准 action 请求发送、编辑、删除、置顶、按钮 ACK、Inline Answer。
- 插件选择 `interaction_bot` / `userbot_reply` / `auto`，平台执行并记录实际通道；涉及转账/发奖仍由 userbot 或受控结算流程处理。
- 旧 `notice` / `bbot_notice` / `notice_bot` 主动发送通道不恢复，只能明确失败并提示迁移。
- 日志页默认以 Trace 视角排障，不再以旧 runtime log 表格作为主入口。
- 插件开发文档能让开发者直接写出 message、command、callback、inline、payment 插件，并知道如何从日志页排查。

### 0.5 最终版稳定不变量

后续实现必须保护以下不变量。只要某条被打破，即使功能表面可用，也不能称为最终版：

- **唯一事件入口**：Telegram 来源只允许通过 Source Adapter 标准化后进入 Event Bus；业务代码不得在运行期绕过 Event Bus 直接调用插件。
- **唯一插件消息协议**：新插件主路径只读标准事件信封；旧平铺 payload 只允许作为迁移层输出，不允许出现在新版开发指南的最小示例里。
- **唯一消息操作出口**：插件只能通过 `ctx.messages` 或标准 action 请求发送、编辑、删除、置顶、按钮 ACK、Inline Answer；插件不能直接拿 Bot token、UserBot client 或 Telegram driver。
- **可见的自由**：插件可以自由选择发送通道和读取声明内能力，但平台必须在 Trace 中记录声明、实际调用、越声明调用、失败原因和最终通道。
- **可解释的未触发**：任意消息未触发插件时，日志页必须能给出稳定 reason_code，而不是只显示“没有执行”。
- **可解释的失败**：任意插件加载失败、运行异常、Contract Guard 告警、Telegram API 失败、UserBot 离线、Bot token 缺失，都必须能从日志页定位到插件、入口、动作和 trace。
- **可回滚的数据层**：新增 Trace 表和设置不能破坏旧 `runtime_log` / `audit_log`；紧急回滚时不要求删新表。
- **可迁移的旧规则**：旧交互规则 UI 可以保留，但内部语义必须收敛为 Event Bus 订阅条件，不能形成第二套并行调度真相。
- **可审计的高风险能力**：`telegram_native_raw`、`inline_all`、跨通道发送、转账/发奖动作必须有 WebUI 风险提示和 Trace 留痕。
- **可照文开发**：插件开发指南必须和真实代码字段一致；文档里的最小插件复制后能通过验证脚本和本地运行。

### 0.6 最终版硬门禁

实现完成后必须逐项打勾，不能用“已基本完成”替代：

- 代码中所有新 Telegram update 处理入口都能找到 `trace_id` 创建或继承逻辑。
- 所有插件调用路径都能找到 Event Bus decision 或旧规则映射后的 decision。
- 所有 action 执行路径都能产生 `event_action`，失败动作不得被记录为成功。
- 所有 manifest 新字段都贯穿：解析、远程仓库写回、数据库 feature 信息、前端类型、WebUI 展示、lint、文档。
- 所有旧发送通道值 `notice` / `bbot_notice` / `notice_bot` 都不再被当作可执行通道；manifest lint、规则保存或运行时必须给出迁移提示。
- 文档 grep 不得再把旧平铺 payload、旧规则驱动、旧 notice 通道写成推荐主路径。
- 至少有一组 fixture 或测试插件覆盖 message、command、callback、inline、payment 五类事件。
- 前端日志页必须用真实 API 或固定 fixture 验收空状态、成功链路、未命中链路、插件失败链路、动作失败链路和窄屏/PWA。

### 0.7 最终版执行合同

后续执行时，“完成”只能按证据判断，不能按主观进度判断。每个任务卡必须在合并前给出以下证据：

- **代码证据**：列出实际改动文件、公共接口变化、兼容层位置和禁区是否被触碰。
- **测试证据**：列出自动化测试命令、关键测试用例、失败命令的第一条可行动错误和是否阻塞。
- **链路证据**：对消息、命令、callback、inline、payment、action 至少给出对应 Trace/span/action 的产生路径。
- **UI 证据**：涉及页面时必须说明桌面和窄屏/PWA 验收入口、空状态、错误态、长文本是否可用。
- **文档证据**：涉及插件契约时必须同步开发文档、示例和验证脚本，不能只改代码。
- **发布证据**：准备推送或部署时必须给出版本文件、中文 CHANGELOG、commit、远端 commit、部署健康检查。

任务状态统一使用：

- `未开始`：没有可复用实现。
- `半落地`：已有代码或 UI 雏形，但缺最终验收矩阵中的任一必备证据。
- `可测`：实现已贯通，定向测试通过，但尚未通过最终验收矩阵。
- `已完成`：自动验证、人工验收、文档审计和发布/回滚要求全部满足。

当前分支已有大量 `半落地` 代码。执行者必须先复核它们是否满足本合同，不能因为文件存在、定向测试通过或页面能打开，就把对应任务记为 `已完成`。

### 0.8 半落地代码处置规则

本计划不是要求从零重写，而是要求把当前分支已有实现收敛到最终版。处置规则如下：

- 已存在的 `event_trace`、`event_bus`、`Logs.tsx`、Delivery Executor、Contract Guard 改动，先按最终验收矩阵逐项补缺，不轻易推翻重写。
- 任何已经跑通的旧规则路径都视为回归底线；Event Bus 接入必须先旁路/映射，再逐步成为主路径。
- 半落地实现如果暴露公共契约不一致，例如字段名、reason_code、send_via、native_raw 边界，以第 11 节公共接口契约为准修正。
- 半落地实现如果只覆盖交互 Bot，不能据此宣称“所有消息进入 Event Bus”；UserBot、命令、外部转账通知、inline 都必须补齐。
- 半落地前端如果只展示 API 数据，不能据此宣称“日志系统可排障”；必须能回答第 1 节列出的排障问题。
- 半落地文档如果仍把旧规则、旧平铺 payload、旧 `notice` 当主路径，必须在最终版发布前重写或降级为迁移说明。
- 半落地测试如果只验证纯函数，必须补集成链路测试；如果只验证后端，必须补前端类型/构建和关键页面验收。

### 0.9 一票否决项

以下任一情况存在时，即使主要功能可用，也不得发布为最终版：

- 新插件仍需要直接理解旧交互规则才能接收普通消息。
- 插件可以绕过 `capabilities.telegram_native_raw` 拿到完整原生 Telegram 数据。
- 旧 `notice` / `bbot_notice` / `notice_bot` 被自动改写成可执行发送通道。
- 插件动作发送失败被记录为成功。
- 任一 Telegram 入口没有 Trace，或 Trace 中看不到插件匹配/跳过/投递原因。
- 日志页无法定位“消息为什么没触发插件”或“插件为什么没启动”。
- 远程插件安装/升级会丢失 `usage`、`event_subscriptions`、`capabilities`。
- 插件开发指南复制出来的最小插件不能通过验证脚本。
- 生产部署没有备份、没有迁移验证、没有远端版本和健康检查证据。

## 1. 目标

TelePilot 最终形态改为：所有 UserBot、交互 Bot、按钮回调、外部转账通知来源收到的 Telegram 事件，先进入统一 Event Bus，再按插件订阅投递给插件。平台不再替插件过度判断业务是否应该启动，而是负责标准化消息、记录完整链路、提供双通道消息操作、执行客观能力边界和风险提示。

新版日志页必须能回答：

- 系统当前是否健康。
- 某条消息进入系统后走到了哪一步。
- 消息为什么没有进入插件。
- 插件为什么没有启动。
- 插件被什么调用。
- 命令启动后调用了什么。
- 插件执行到哪一步卡住。
- 插件返回了什么动作。
- 平台实际发送、删除、置顶、编辑了什么。
- Contract Guard、频控、Telegram API、UserBot 离线等失败原因。

## 2. 非目标

- 不恢复旧 `notice` / `bbot_notice` 主动发送通道。
- 不把外部转账通知 Bot 当成 TelePilot 的发送通道。
- 不下发 Bot Token、UserBot session、API key、私钥等敏感凭据。
- 不要求平台替插件判断所有业务规则。
- 不把新版日志页做成旧 `runtime_log` 的美化版。
- 不为旧插件保留旧平铺字段作为主路径。
- 不把完整 Telegram driver 对象直接塞给插件；`native_raw` 必须是可序列化 dict，而不是 Telethon event / Bot API client / token 之类的 live object。
- 不默认把 `native_raw` 持久化进日志库；日志默认只保存摘要、体积、来源和是否下发。

## 3. 总体架构

```text
Telegram Update
  -> Source Adapter
  -> TelePilotEvent 标准化
  -> Trace 开始
  -> Event Bus
  -> Plugin Subscription Matcher
  -> Plugin Invocation
  -> Plugin Actions
  -> Contract Guard 软告警
  -> Delivery Executor / MessageOps
  -> Trace 完成
  -> 新日志中心展示
```

## 4. 关键决策

### 4.1 日志先于全量广播

先做 Trace / 日志系统，再做全量消息开放。原因是全量开放后消息量、插件候选、跳过原因、动作执行路径都会变多，没有 Trace 会比现在更难排查。

### 4.2 所有消息进入 Event Bus

所有 Telegram 消息都先进入统一 Event Bus。插件是否收到，由插件声明决定。支持插件声明：

```json
{
  "event_subscriptions": [
    {
      "source": ["userbot", "interaction_bot"],
      "events": ["message", "callback_query", "payment_confirmed"],
      "scope": "all_allowed_chats"
    }
  ]
}
```

### 4.3 插件可以自由判断

平台只做来源标准化、敏感字段剥离、频控、审计、客观能力边界。插件拿到标准事件后自行判断是否处理。

### 4.4 两种调度方式是正式主路径

新版不是“普通 Bot 完全独立跑游戏”，也不是“所有交互都必须由 userbot 回复”。主路径固定为两类：

1. **管理员命令调度**
   - 触发：账号主人或授权管理员用系统命令前缀触发。
   - 来源：UserBot 监听到命令消息。
   - 后续交互：默认由 userbot 继续回复或编辑，适合管理、配置、补发、查询、低频人工指令。
   - Trace：事件类型为 `command` 或 `message`，`source.channel=userbot`，`dispatch_mode=admin_command`。

2. **玩家关键词调度**
   - 触发：群内普通玩家发送插件声明的关键词、按钮、答案或 Inline Query。
   - 来源：UserBot 或交互 Bot 监听到消息后进入 Event Bus。
   - 后续交互：默认由 `interaction_bot` 承接高频玩法消息、按钮、开奖公告和普通结果通知。
   - 钱相关动作：收款确认、发奖、补发、转账等仍由 userbot 或平台受控结算流程处理；普通 Bot 只能公告和参与交互，不能被视为有转账能力。
   - Trace：事件类型为 `message` / `callback_query` / `inline_query` / `payment_confirmed`，`dispatch_mode=public_keyword` 或订阅声明中的显式模式。

插件可以在 action 中选择 `interaction_bot`、`userbot_reply` 或 `auto`。平台尊重插件选择，但必须记录实际发送通道、失败原因和回退路径。

### 4.5 日志以 Trace 为中心

旧 `runtime_log` 只能回答“某时刻写了一行什么”。新版必须围绕 `trace_id` 展示一条消息完整生命周期。

### 4.6 旧日志保留为原始日志

当前 `runtime_log` 和 `/api/logs/runtime` 可保留为底层兼容和原始文本流，但新版日志页默认不再以它为主。

### 4.7 `native_raw` 是可信插件显式能力

`payload["native_raw"]` 支持，但必须是插件显式声明、账号主人确认后的能力，不默认下发给所有插件。

设计理由：

- TelePilot 是个人可信插件系统，插件和插件库由账号主人主动安装，平台不应该把“平台投影字段不完整”变成插件开发者绕不开的限制。
- 防改名诈骗、付款关系链、回复链、forward 来源、via bot、sender_chat 等风控场景，确实需要完整数字 ID 关系链。
- 风险不应通过隐藏数据来伪安全，而应该通过插件声明、WebUI 风险提示、审计、Trace 留痕、保留策略和急停来承担。

边界：

- UserBot / Telethon 来源：`native_raw` 放 `event.message.to_dict()` 的 JSON 兼容版本，尽量完整保留 Telegram 原生字段。
- 交互 Bot / Bot API 来源：`native_raw` 放 Telegram Bot API 原始 update 中对应对象，例如 `message`、`callback_query`、`inline_query` 或完整 `update` 的 JSON 兼容版本。
- 外部转账通知来源：如果来自 UserBot 监听群消息，则按 Telethon message 处理；如果来自 Bot API，则按 Bot API update 处理。
- 不加入 Bot Token、UserBot session、平台 API key、数据库连接串、插件仓库凭据等传输/平台凭据，因为这些本来不属于 Telegram 原始消息对象。
- `payload_snapshot` 默认不保存 `native_raw`，只保存 `native_raw_meta`；需要保存完整原生数据时必须有单独的全局设置和短保留期。

推荐插件声明：

```json
{
  "capabilities": {
    "telegram_native_raw": {
      "enabled": true,
      "sources": ["userbot", "interaction_bot"],
      "reason": "需要完整数字 ID 关系链做防改名诈骗风控"
    }
  }
}
```

标准事件中增加：

```json
{
  "native_raw": {},
  "native_raw_meta": {
    "enabled": true,
    "source": "userbot",
    "driver": "telethon",
    "object": "message",
    "stored_in_trace": false,
    "size_bytes": 12345
  }
}
```

日志页必须能看到“该插件请求了原生数据免检通道、本次事件是否下发、大小是多少、是否被持久化”，但默认不展示完整内容。

### 4.8 Inline 模式是一等事件

Inline 模式不是按钮回调的变体。它应作为独立事件进入 Event Bus：

- `inline_query`：用户在任意聊天框输入 `@botname 关键词`。
- `chosen_inline_result`：用户选择了某个 inline 结果，建议同步纳入 Event Bus 以便统计和后续状态回写。

标准事件增加：

```json
{
  "source": {
    "type": "inline_query",
    "channel": "interaction_bot",
    "driver": "telegram_bot_api",
    "inline_query_id": "AA...",
    "chat_id": null
  },
  "inline_query": {
    "id": "AA...",
    "query": "关键词",
    "offset": "",
    "chat_type": "sender",
    "from": {
      "user_id": 123,
      "display_name": "Alice",
      "username": "alice"
    }
  }
}
```

Delivery Executor 增加动作：

```json
{
  "type": "answer_inline_query",
  "inline_query_id": "AA...",
  "results": [
    {
      "type": "article",
      "id": "result-1",
      "title": "标题",
      "input_message_content": {
        "message_text": "发送到聊天里的内容",
        "parse_mode": "HTML"
      }
    }
  ],
  "cache_time": 0,
  "is_personal": true
}
```

Inline 事件没有稳定群 `chat_id`，不能套用“允许会话”判断。订阅声明必须单独支持 `scope`：

- `owner_only`：仅账号主人/管理员可触发。
- `known_users`：仅平台见过的用户。
- `inline_all`：允许所有 Telegram 用户触发，需要 WebUI 高级风险提示。

交互 Bot polling / webhook 的 `allowed_updates` 必须加入：

```json
["message", "callback_query", "inline_query", "chosen_inline_result"]
```

### 4.9 `raw` / `raw_event` / `native_raw` 边界

新版只保留三个清晰概念：

- `raw`：平台生成的脱敏摘要，只用于日志和排障，不承诺包含完整 Telegram 原始结构。
- `native_raw`：插件显式声明 `capabilities.telegram_native_raw.enabled=true` 后才下发的原生 Telegram dict，用于严格风控、关系链校验、reply/forward/via bot/source chat 等高级场景。
- `native_raw_meta`：不论是否下发 `native_raw` 都要提供，记录本次是否允许、来源、driver、对象类型、大小、是否持久化、失败原因。

不再把 `raw_event` 作为新版公开协议。若历史代码里仍有 `raw_event`，最终版必须处理为以下两种之一：

- 内部变量：只存在于 Source Adapter 内部，不进入插件 payload 和文档。
- 迁移失败提示：插件读取旧 `raw_event` 时得到空值或兼容摘要，并在 Trace / 插件规范警告中提示改用 `native_raw` 声明。

禁止把 `raw_event` 作为绕过 `telegram_native_raw` 声明的后门。

### 4.10 Contract Guard 的最终定位

Contract Guard 不再是“平台替个人插件做强安全沙箱”，而是可信插件系统里的契约记录器和客观失败保护层：

- 插件调用声明外能力时，平台原则上不替账号主人阻断业务，但必须产生规范警告、Trace span 和日志页提示。
- 插件请求平台根本不支持的动作或通道，例如旧 `notice`、缺失 inline_query_id 的 `answer_inline_query`、普通 Bot 执行转账，必须明确失败并返回可读错误。
- 插件请求高风险但可执行能力，例如 `native_raw`、`inline_all`、跨通道发送，必须在安装/启用/配置页显示风险提示，并在 Trace 中记录实际使用。
- Contract Guard 的输出必须同时包含机器可筛选的 `reason_code` 和中文可读说明。

第一版 severity 分级：

- `info`：声明内调用，记录审计。
- `warning`：越声明调用但平台可执行，放行并告警。
- `blocked`：客观不可执行或明确废弃能力，拒绝执行。
- `failed`：平台尝试执行但 Telegram API、UserBot、Bot token、网络或权限失败。

### 4.11 `notice` 通道收口

用户所说的“转账通知 Bot”是群里第三方已有 Bot，它只作为外部消息来源，用于确认转账结果；它不是 TelePilot 的发送通道。

最终版必须统一为：

- 外部转账通知 Bot 的消息进入 Event Bus 时，事件来源为 `external_payment_notice` 或 `source_actor.type=external_bot`。
- 插件普通交互内容、玩法结果、开奖公告、按钮反馈，如果选择普通 Bot 执行，必须走 `interaction_bot`。
- 涉及转账、发奖、补发、余额等动作，必须走 userbot 或平台受控结算动作，不能走 `interaction_bot`。
- 旧 `notice` / `bbot_notice` / `notice_bot` 只作为迁移错误值处理，不能再恢复为任何发送通道别名。

运行时遇到旧值时必须：

- `event_action.status = "failed"` 或 manifest lint 失败/警告。
- `reason_code = "send_channel_deprecated"`。
- 中文提示：“notice/bbot_notice 已不是系统发送通道，请改用 interaction_bot、userbot_reply 或 auto；外部转账通知 Bot 仅作为消息来源。”

### 4.12 两种调度流程硬定义

最终版只保留两条产品主流程，避免继续出现“规则、命令、Bot 回调、插件自跑”多套解释。

管理员命令调度：

```text
管理员/账号主人发送命令
  -> UserBot 收到消息
  -> Source Adapter 标准化为 command/message
  -> Event Bus 匹配 owner_only / command filters
  -> 插件入口执行
  -> 插件通过 ctx.messages/action 请求 userbot_reply 或 auto
  -> Delivery Executor 执行
  -> Trace 展示命令解析、插件调用、动作发送
```

玩家关键词调度：

```text
玩家在群里发送关键词/答案/点击按钮/Inline Query
  -> UserBot 或 interaction_bot 收到事件
  -> Source Adapter 标准化为 message/callback_query/inline_query
  -> Event Bus 匹配 all_allowed_chats/known_users/inline_all 与 filters
  -> 插件入口执行并维护会话状态
  -> 普通交互动作走 interaction_bot
  -> 转账/发奖/结算动作走 userbot 或 settlement
  -> Trace 展示玩家、会话、付款归属、动作结果
```

任何插件都可以在这两条流程中自由决定是否处理事件；平台只负责让事件完整、动作可执行、风险可见、失败可查。

## 5. 新数据模型

### 5.1 event_trace

记录一条 Telegram 事件的主链路。

字段：

- `trace_id`
- `account_id`
- `source_channel`
- `event_type`
- `chat_id`
- `message_id`
- `update_id`
- `callback_query_id`
- `sender_user_id`
- `sender_name`
- `text_preview`
- `status`
- `started_at`
- `ended_at`
- `duration_ms`
- `raw_summary`
- `payload_snapshot`
- `native_raw_meta`

### 5.2 event_span

记录链路中的每一步。

典型阶段：

- `receive`
- `normalize`
- `route`
- `rule_match`
- `session_match`
- `subscription_match`
- `plugin_load`
- `plugin_invoke`
- `plugin_return`
- `contract_guard`
- `delivery`
- `settlement`
- `finish`
- `inline_answer`

字段：

- `span_id`
- `trace_id`
- `parent_span_id`
- `phase`
- `component`
- `plugin_key`
- `entry_key`
- `status`
- `reason_code`
- `message`
- `detail`
- `started_at`
- `ended_at`
- `duration_ms`

`reason_code` 必须使用稳定枚举，避免日志页变成不可搜索的自由文本。第一批枚举：

- `plugin_disabled`
- `plugin_not_installed`
- `plugin_load_failed`
- `subscription_not_matched`
- `rule_not_matched`
- `session_not_found`
- `rate_limited`
- `contract_warning`
- `contract_failed`
- `telegram_api_error`
- `userbot_offline`
- `bot_token_missing`
- `native_raw_not_allowed`
- `inline_query_answer_failed`

### 5.3 event_action

记录插件返回动作与平台实际执行动作。

字段：

- `action_id`
- `trace_id`
- `plugin_key`
- `action_type`
- `requested_send_via`
- `actual_send_via`
- `target_chat_id`
- `target_message_id`
- `status`
- `telegram_message_id`
- `inline_result_count`
- `error_code`
- `error_message`
- `detail`

### 5.4 plugin_runtime_status

记录插件加载和运行状态。

字段：

- `plugin_key`
- `account_id`
- `enabled`
- `installed_version`
- `load_status`
- `last_load_error`
- `last_invoked_at`
- `last_invocation_status`
- `last_trace_id`

### 5.5 索引与保留策略

Trace 表会明显比旧日志更大，必须随迁移一起建索引和清理策略：

- `event_trace(account_id, started_at desc)`
- `event_trace(account_id, chat_id, message_id)`
- `event_trace(account_id, update_id)`
- `event_trace(status, started_at desc)`
- `event_span(trace_id, started_at)`
- `event_span(plugin_key, started_at desc)`
- `event_action(trace_id)`
- `event_action(plugin_key, status, created_at desc)`

默认保留：

- `event_trace` / `event_span` / `event_action`：30 天。
- `payload_snapshot`：7 天或按全局设置关闭。
- 完整 `native_raw`：默认不保存；开启后默认只保留 1 天，并在日志页显式标记高风险。

### 5.6 设置与回退开关

为了让最终版可以安全上线、实测和回滚，必须新增或复用以下系统设置。最终版 WebUI、API 文档和开发文档统一使用下列设置名；如历史代码已有不同命名，兼容别名只能留在迁移层，并必须在最终证据台账中登记映射关系。

- `trace_enabled`：是否写入 Trace。默认开启；关闭后不得影响旧 `runtime_log` / `audit_log` 和插件主流程。
- `trace_payload_snapshot_enabled`：是否保存脱敏 payload snapshot。默认开启。
- `trace_payload_snapshot_retention_days`：payload snapshot 保留天数。默认 7 天。
- `trace_retention_days`：trace/span/action 主记录保留天数。默认 30 天。
- `native_raw_persist_enabled`：是否持久化完整 `native_raw`。默认关闭。
- `native_raw_retention_days`：完整 `native_raw` 保留天数。默认 1 天，且仅在持久化开关开启时生效。
- `event_bus_delivery_enabled`：是否启用 Event Bus 新投递路径。最终版默认开启；紧急回滚时可关闭并回退到旧规则驱动路径。
- `inline_updates_enabled`：是否允许交互 Bot 拉取/接收 `inline_query` 和 `chosen_inline_result`。默认按账号 Bot 配置开启。

这些开关不是产品上长期保留的多模式入口，而是部署和回滚护栏。最终版 WebUI 不应把它们包装成“标准模式/个人模式”，只在系统高级设置或运维配置中暴露。

### 5.7 Trace 数据体积上限

为了避免日志系统本身拖垮主流程，必须定义数据体积上限：

- `text_preview` 默认截断到 500 字符以内。
- `payload_snapshot` 默认脱敏并限制大小，超过上限时写入 `truncated=true`、`size_bytes` 和截断原因。
- `native_raw` 即使允许下发给插件，也不能默认写入数据库；若下发对象过大，必须记录 `native_raw_meta.size_bytes` 和 `native_raw_meta.truncated_for_trace=true`。
- 单条 trace 的 span/action 数量应有软上限；超过上限时继续执行业务，但日志页展示聚合摘要并记录 `trace_span_limit_reached`。
- Trace 写入失败不得阻断插件业务，但必须写旧 runtime error 以便发现日志系统故障。

## 6. 新日志页

### 6.1 总览

展示：

- worker 状态。
- Bot 状态。
- Redis / DB / 队列状态。
- 最近 5 分钟事件数。
- 最近错误插件。
- 最近失败发送动作。
- 最近 Contract Guard 告警。

### 6.2 消息链路

支持按以下条件搜索：

- chat_id
- message_id
- trace_id
- 用户 ID
- 插件 key
- 关键词
- 时间范围
- 状态

点开一条消息后展示完整时间线：

```text
收到消息
-> 标准化成功
-> 命中 3 个插件订阅
-> 插件 A 跳过：关键词不匹配
-> 插件 B 执行成功
-> 插件 B 返回 send_message
-> Contract Guard warning
-> interaction_bot 发送成功
```

### 6.3 插件诊断

展示每个插件：

- 是否安装。
- 是否启用。
- 是否加载成功。
- 最近被什么调用。
- 最近失败原因。
- 最近 20 条 invocation。
- 最近返回 actions。
- 最近 Contract Guard 告警。

### 6.4 命令链路

管理员命令触发后展示：

- 命令来源。
- 命令解析。
- 命中的插件/系统处理器。
- 调用的服务。
- 产生的动作。
- 最终发送结果。

### 6.5 动作发送

展示：

- 插件请求动作。
- 请求通道。
- 平台实际通道。
- Telegram API 返回。
- UserBot 是否离线。
- Bot token 是否缺失。
- 删除/置顶/编辑失败原因。

### 6.6 原始日志

保留旧 runtime log / audit log 作为高级排障页，不再作为默认入口。

### 6.7 原生数据与 Inline 调试

新增高级折叠区：

- 标准事件信封。
- `raw_summary`。
- `native_raw_meta`。
- `native_raw` 是否下发给插件。
- Inline Query 请求参数。
- Inline Answer 返回结果数量、Telegram API 错误、cache_time / is_personal。

默认不展开完整 JSON；点击展开时提醒“这是插件免检通道数据，可能包含完整消息内容和关系链”。

## 7. 实施阶段

这些阶段是可独立合并、可独立部署观察的施工波次，不是最终目标的降级版。每个波次完成后系统都必须保持可用；如果任一波次失败，应能回滚到上一波次而不破坏旧 `runtime_log`、旧交互规则和现有插件调用。

最终版执行时采用以下口径：

- 需要快速交付给服务器实测时，可以先发布 `0.38.0` 和 `0.39.0` 作为稳定检查点。
- 用户要求“一次做到最终版”时，三个波次仍按顺序施工和 review，但最终只在全部验收通过后统一 bump 到 `0.40.0`。
- 版本号只能在发布检查点或最终合并前统一修改，不能每个小任务单独 bump。

### 0.38.0 minor（次版本）：链路日志与日志页重构

- 新增 `event_trace`、`event_span`、`event_action`、`plugin_runtime_status`。
- 接入现有交互 Bot、UserBot 命令、插件 loader、Delivery Executor。
- 新增 Trace 写入服务。
- 新增日志中心 API。
- 重构前端日志页为“总览 / 消息链路 / 插件诊断 / 命令链路 / 动作发送 / 原始日志”。
- 所有现有 `ctx.log`、Contract Guard、Delivery Executor 日志补 `trace_id` / `plugin_key` / `entry_key`。
- `payload_snapshot` 默认脱敏，不保存完整 `native_raw`。
- 当前规则驱动调度保持不变。
- 交付后必须仍能按旧规则启动插件；新日志页已经可用，但还不宣称 Event Bus 全量开放。

### 0.39.0 minor（次版本）：统一 Event Bus 与插件订阅

- 所有 Telegram 消息进入 Event Bus。
- 新增插件 `event_subscriptions` 声明。
- UserBot 消息、交互 Bot 消息、callback、inline_query、chosen_inline_result、付款确认统一生成 TelePilotEvent。
- 插件可声明接收所有允许会话消息。
- 插件可声明 `telegram_native_raw` 能力，WebUI 展示高风险提示并在 Trace 留痕。
- 交互 Bot polling / webhook 支持 `inline_query` / `chosen_inline_result`。
- Delivery Executor 支持 `answer_inline_query`。
- 每次投递和跳过都写入 Trace。
- 旧规则命中逻辑降级为订阅条件的一种来源。
- 交付后新插件可不依赖旧规则接收事件；旧规则仍能作为筛选条件继续工作。

### 0.40.0 minor（次版本）：最终开放插件运行模型

- 插件可在同一入口处理命令、关键词、按钮、付款、普通消息。
- 插件可自由选择 `interaction_bot` / `userbot_reply` / `auto`。
- 平台只做风险提示、频控、审计、敏感字段剥离和客观失败返回。
- 插件开发指南全面切换到 Event Bus + Trace 模型。
- 插件开发指南新增 `native_raw` 风控示例、Inline Query 插件示例、Trace 排障清单。
- 清理 WebUI 和文档中的旧主路径描述；旧字段只作为迁移说明出现，不再作为新插件推荐路径。

## 8. 验证标准

必须能在日志页完成以下排查：

- 输入一条群消息 ID，看到它是否进入系统。
- 看到它为什么没有触发任何插件。
- 看到它命中了哪些插件订阅。
- 看到插件为什么跳过。
- 看到插件为什么加载失败。
- 看到插件执行耗时。
- 看到插件返回 actions。
- 看到平台最终用哪个账号/Bot 发送。
- 看到 Telegram API 失败原因。
- 看到 Contract Guard 是 warning 还是 failed。
- 看到命令触发后完整调用链。
- 指定插件声明 `telegram_native_raw` 后，能在插件 payload 中拿到 `native_raw`，并在日志页看到 `native_raw_meta`。
- 未声明 `telegram_native_raw` 的插件拿不到 `native_raw`，日志页记录 `native_raw_not_allowed` 或 `native_raw_skipped`。
- 发送 `@botname 关键词` 后能产生 `inline_query` trace，插件能返回 `answer_inline_query`，日志页能看到 Telegram API 成功或失败。
- Inline 结果被选择时能产生 `chosen_inline_result` trace。

## 9. 风险与处理

- 数据量变大：Trace 表必须有保留策略、索引和按时间清理。
- 文本敏感：默认保存 `text_preview`，完整 payload 需脱敏并受设置控制。
- 插件过多：订阅匹配必须先过滤账号、会话、事件类型，再调用插件。
- UI 复杂：默认展示时间线，JSON 细节折叠。
- 回滚：保留旧 `runtime_log`，新 Trace 可独立关闭写入。
- `native_raw` 过大：默认只投递不持久化；如开启保存，必须有短保留期、大小记录和清理任务。
- `native_raw` 字段类型复杂：进入插件前统一转换成 JSON 兼容 dict，保留字段名和值，不暴露 live driver object。
- Inline Query 没有 chat_id：不能用群白名单硬套，必须使用独立 scope 和风险提示。
- Inline Answer 结果格式复杂：第一版支持 Bot API 常用 `article` / `photo` / `gif` / `document` 结果透传，并把 Telegram API 错误完整写入 `event_action`。

## 10. 最终版完成定义

“最终版”不是指把所有 Telegram API 都封装一遍，而是指 TelePilot 对插件提供稳定、可排障、可扩展的事件与消息操作框架。完成后必须满足：

- 所有 UserBot、交互 Bot、按钮回调、Inline Query、Inline 结果选择、外部转账通知来源都进入统一 Event Bus。
- 插件通过 `event_subscriptions` 声明自己要接收哪些事件，平台记录每一次匹配、跳过和投递原因。
- 插件收到统一标准事件信封，业务优先读标准字段，需要严格风控时可声明 `telegram_native_raw` 获取 `payload["native_raw"]`。
- 插件通过 `ctx.messages` 或标准 action 请求发送、编辑、删除、置顶、回应按钮、回应 Inline Query；插件选择通道，平台负责执行、回退、记录和客观失败返回。
- 日志中心默认围绕 `trace_id` 展示消息生命周期，而不是围绕旧文本日志；旧 `runtime_log` 只作为原始日志高级入口。
- 插件加载失败、未启用、未订阅、会话不匹配、Contract Guard 告警、频控、Telegram API 失败、UserBot 离线等原因都能在日志页直接看到。
- 插件开发指南以 Event Bus + Trace + MessageOps 为主路径，旧规则驱动和平铺 payload 不再作为新插件开发主路径。

最终版验收口径：

- 任意一条群消息：能查到是否进入系统、标准化结果、匹配了哪些插件、哪些插件跳过、哪些插件执行、执行耗时、返回动作和最终发送结果。
- 任意一个插件：能查到安装/启用/加载状态、最近被什么事件调用、最近为什么失败、最近返回了什么动作。
- 任意一个动作：能查到插件请求内容、Contract Guard 结果、实际通道、Telegram API 响应或 UserBot 失败原因。
- 任意一个 Inline Query：能查到 query 来源、订阅匹配、插件返回结果数量、`answerInlineQuery` 成败和 `chosen_inline_result`。
- 任意一个声明 `telegram_native_raw` 的插件：能拿到 `native_raw`，日志页能看到 `native_raw_meta`，默认不会把完整 `native_raw` 长期持久化。

## 11. 公共接口契约

### 11.1 标准事件信封

所有插件收到的 payload 统一包含以下顶层字段：

```json
{
  "trace_id": "evt_...",
  "source": {},
  "message": {},
  "chat": {},
  "sender": {},
  "actor": {},
  "source_actor": {},
  "player": {},
  "payment": null,
  "reply_to": null,
  "session": null,
  "trigger": {},
  "inline_query": null,
  "chosen_inline_result": null,
  "raw": {},
  "native_raw_meta": {},
  "native_raw": null
}
```

字段边界：

- `trace_id`：贯穿 Trace、插件日志、action、Delivery Executor。
- `source`：事件来源和 driver 信息，例如 `type`、`channel`、`driver`、`account_id`、`update_id`、`message_id`、`callback_query_id`、`inline_query_id`。
- `message` / `chat` / `sender`：Telegram 消息、会话、实际发送者的稳定投影。
- `actor`：业务动作主体；可等于 sender，也可由付款/回复链推断。
- `source_actor`：实际产生事件的一方，例如外部转账通知 Bot。
- `player`：游戏/玩法中的玩家身份，付款事件中通常为付款人。
- `payment`：只有付款确认类事件有有效内容。
- `reply_to`：被回复消息摘要，用于付款原消息归属等场景。
- `inline_query`：只有 `source.type == "inline_query"` 时有值。
- `chosen_inline_result`：只有 `source.type == "chosen_inline_result"` 时有值。
- `raw`：脱敏摘要，排障用，不作为长期业务协议。
- `native_raw`：可信插件显式声明后才下发的原生 Telegram dict。

### 11.2 插件 manifest 新增字段

远程插件和内置/官方插件 manifest 均支持：

```json
{
  "event_subscriptions": [
    {
      "source": ["userbot", "interaction_bot", "external_payment_notice"],
      "events": ["message", "command", "callback_query", "inline_query", "chosen_inline_result", "payment_confirmed"],
      "scope": "all_allowed_chats",
      "entry_key": "main",
      "filters": {
        "keywords": ["开始"],
        "command_prefix_required": false
      }
    }
  ],
  "capabilities": {
    "telegram_native_raw": {
      "enabled": true,
      "sources": ["userbot"],
      "reason": "需要完整数字 ID 关系链做防改名诈骗风控"
    }
  }
}
```

订阅 scope 第一版支持：

- `all_allowed_chats`：账号允许会话内事件。
- `owner_only`：仅账号主人/管理员。
- `known_users`：平台已见过用户。
- `inline_all`：Inline 对所有 Telegram 用户开放，WebUI 必须高风险提示。
- `rule_bound`：旧交互规则迁移过渡，按现有规则范围触发。

### 11.3 MessageOps / Action 契约

标准 action 类型：

- `send_message`
- `send_photo`
- `send_file`
- `edit_message`
- `delete_message`
- `pin_message`
- `answer_callback`
- `answer_inline_query`
- `settlement`
- `end_session`

`send_via` / `channel_selector` 支持：

- `interaction_bot`
- `userbot_reply`
- `auto`

旧 `notice` / `bbot_notice` / `notice_bot` 不恢复；这些值只能产生明确失败和迁移提示。

`answer_inline_query` 第一版字段：

```json
{
  "type": "answer_inline_query",
  "inline_query_id": "AA...",
  "results": [],
  "cache_time": 0,
  "is_personal": true,
  "next_offset": "",
  "button": null
}
```

### 11.4 Event Bus Service 接口

新增服务放在 `backend/app/services/event_bus.py`，第一版必须提供以下接口：

- `normalize_bot_update(account_id, update, *, channel) -> TelePilotEvent`
- `normalize_userbot_event(account_id, event, *, command_meta=None) -> TelePilotEvent`
- `normalize_payment_notice(account_id, event, parsed) -> TelePilotEvent`
- `normalize_event_subscription(raw, *, plugin_key, entry_key=None) -> EventSubscription`
- `match_subscriptions(event, subscriptions, account_state) -> list[SubscriptionDecision]`
- `dispatch_event(event) -> DispatchResult`

`SubscriptionDecision` 必须包含：

- `plugin_key`
- `entry_key`
- `matched`
- `reason_code`
- `reason_message`
- `dispatch_mode`
- `scope`
- `filters`

匹配顺序固定：

1. account 是否一致。
2. 插件是否安装和启用。
3. 事件来源 `source.channel` 是否匹配。
4. 事件类型是否匹配。
5. scope 是否匹配，例如 `all_allowed_chats`、`owner_only`、`known_users`、`inline_all`、`rule_bound`。
6. filters 是否匹配，例如关键词、命令、callback data、付款金额、reply_to、chat_id。
7. session 是否匹配或是否需要新建。
8. 投递给插件。

跳过记录采用“候选明细 + 聚合摘要”：

- 对已进入候选集的插件，必须写 `event_span`，记录 matched / skipped / delivered 和稳定 `reason_code`。
- 对明显不相关的插件，例如未订阅该 source/event_type 的插件，可以写聚合摘要 span：`subscription_not_matched_count`、`source_not_subscribed_count`、`event_type_not_subscribed_count`。
- 如果用户在日志页按某个插件 key 深挖，API 再按 manifest 现场解释“该插件为什么不是候选”，避免每条消息为每个插件落库。
- 不能只在最终“没触发插件”时写一条笼统日志。

`reason_code` 第一版至少包含：

- `account_not_matched`
- `plugin_disabled`
- `plugin_not_installed`
- `event_type_not_subscribed`
- `source_not_subscribed`
- `scope_not_matched`
- `filter_not_matched`
- `session_not_found`
- `rate_limited`
- `native_raw_not_allowed`
- `plugin_load_failed`
- `plugin_runtime_error`
- `action_failed`

### 11.5 Trace Service 接口

新增服务建议放在 `backend/app/services/event_trace.py`，提供以下稳定接口：

- `start_trace(event) -> TraceContext`
- `record_span(trace, phase, status, **detail) -> EventSpan`
- `record_action(trace, action, status, **detail) -> EventAction`
- `finish_trace(trace, status, **summary) -> None`
- `trace_log_context(trace, plugin_key=None, entry_key=None) -> dict`
- `redact_payload_snapshot(payload) -> dict`

所有接口必须吞掉非关键日志写入异常，不能因为 Trace 写库失败阻断插件主流程；但 Trace 服务故障要写入 `runtime_log` 的 system/error。

### 11.6 日志 API 契约

新增 API 放在 `backend/app/api/logs.py` 或拆分 `backend/app/api/event_traces.py` 后在 `backend/app/main.py` 注册：

- `GET /api/logs/trace/overview`
- `GET /api/logs/trace/events`
- `GET /api/logs/trace/events/{trace_id}`
- `GET /api/logs/trace/plugins`
- `GET /api/logs/trace/plugins/{plugin_key}`
- `GET /api/logs/trace/actions`
- `GET /api/logs/trace/commands`

必须保留：

- `GET /api/logs/runtime`
- `GET /api/logs/audit`

新 API 统一支持 `account_id`、`since`、`until`、`status`、`plugin_key`、`event_type`、`chat_id`、`message_id`、`trace_id`、`keyword`、`limit`。

### 11.7 插件运行时 API 契约

开发者最终只需要记住三类入口，不应再理解平台内部旧规则细节：

事件读取：

- `payload["trace_id"]`
- `payload["source"]`
- `payload["message"]`
- `payload["chat"]`
- `payload["sender"]`
- `payload["actor"]`
- `payload["player"]`
- `payload["payment"]`
- `payload["reply_to"]`
- `payload["session"]`
- `payload["trigger"]`
- `payload["inline_query"]`
- `payload["chosen_inline_result"]`
- `payload["native_raw_meta"]`
- `payload["native_raw"]`

消息操作：

- `ctx.messages.send_text(...)`
- `ctx.messages.send_photo(...)`
- `ctx.messages.send_file(...)`
- `ctx.messages.edit_message(...)`
- `ctx.messages.delete_message(...)`
- `ctx.messages.pin_message(...)`
- `ctx.messages.answer_callback(...)`
- `ctx.messages.answer_inline_query(...)`
- `ctx.messages.settlement(...)`
- `ctx.messages.end_session(...)`

排障记录：

- `ctx.log.info(...)`
- `ctx.log.warning(...)`
- `ctx.log.error(...)`

所有 `ctx.log` 必须自动补上 `trace_id`、`plugin_key`、`entry_key`。插件开发者不需要手动拼 trace 字段，但可以读取 `payload["trace_id"]` 在业务日志或外部记录里关联。

### 11.8 插件 manifest 最小最终版

新插件 manifest 至少应能表达以下内容：

```json
{
  "key": "example_game",
  "name": "示例玩法",
  "version": "1.0.0",
  "description": "演示 Event Bus + MessageOps 的最小插件",
  "usage": "在允许群内发送“开始示例”启动玩法；点击按钮或回复答案继续。",
  "event_subscriptions": [
    {
      "source": ["userbot", "interaction_bot"],
      "events": ["message", "callback_query"],
      "scope": "all_allowed_chats",
      "entry_key": "main",
      "filters": {
        "keywords": ["开始示例"]
      }
    }
  ],
  "capabilities": {
    "telegram_native_raw": {
      "enabled": false
    }
  }
}
```

规范要求：

- `usage` 必须由插件声明；没有详细使用说明时，插件配置页和规范警告必须显示红色高级警告。
- `event_subscriptions` 为空时，插件只能被手动命令或系统内部显式调用；WebUI 必须提示“该插件没有声明可自动接收的事件”。
- `capabilities.telegram_native_raw.enabled=true` 时必须提供 `reason`，否则 lint 报警。
- `send_via` 只允许 `interaction_bot`、`userbot_reply`、`auto`；旧 `notice` / `bbot_notice` / `notice_bot` 必须报迁移警告或保存失败。
- 远程插件仓库同步、私有 GitHub 仓库、`tree/<branch>` 分支 URL、安装记录和本地插件 manifest 必须保留这些字段，不得在任一层丢失。

### 11.9 文档漂移审计

最终版发布前必须对开发文档做一次反向审计：

- 从代码 schema 反查文档字段，确认文档没有漏掉 `event_subscriptions`、`capabilities`、`native_raw_meta`、`answer_inline_query`、`settlement`。
- 从文档示例反跑验证脚本，确认示例 manifest 和示例 payload 可被当前代码接受。
- 搜索旧概念：`notice`、`bbot_notice`、`raw_event`、旧平铺 `payload["text"]` 主路径、旧规则驱动主路径。保留处必须明确标注“迁移说明”或“废弃值”，不能作为推荐写法。
- 插件开发指南必须包含“插件为什么没启动”的排查顺序：安装状态、启用状态、manifest lint、event_subscriptions、scope、filters、session、rate limit、plugin load、plugin runtime、action delivery。

## 12. 实施依赖图

```text
0.38 Trace 数据层
  -> Trace Service
  -> 现有交互链路埋点
  -> 新日志 API
  -> 新日志 UI
  -> 文档/测试

0.39 Source Adapter / Event Bus
  -> 插件订阅匹配
  -> native_raw 能力
  -> Inline Query 入口
  -> answer_inline_query Delivery
  -> Trace 覆盖新链路
  -> 文档/测试

0.40 插件运行模型收敛
  -> 旧规则降级为订阅条件
  -> 插件迁移示例
  -> 开发指南最终版
  -> 清理旧概念和 UI 文案
```

并行原则：

- 数据模型和 Trace Service 先于 UI 与链路埋点。
- Event Bus 先于全量消息投递。
- Inline Delivery 可和 Source Adapter 并行，但必须最后用 Trace 串起来。
- 插件文档可以先写草案，但最终必须以代码实际字段为准复核。

## 13. 0.38.0 施工任务卡：链路日志与日志页重构

### T38-1 数据模型与迁移

写入范围：

- `backend/app/db/models/log.py`
- `backend/app/db/models/__init__.py`
- `backend/alembic/versions/0031_event_trace.py`

具体改动：

- 新增 `EventTrace`、`EventSpan`、`EventAction`、`PluginRuntimeStatus` ORM。
- 建立第 5.5 节索引。
- `payload_snapshot`、`raw_summary`、`native_raw_meta`、`detail` 使用 JSON。
- 所有外键删除策略不得影响原始 `runtime_log`。

禁止改动：

- 不删除 `RuntimeLog` 和 `AuditLog`。
- 不改旧日志 API 返回字段。

测试：

- 新增迁移结构测试或模型导入测试。
- 执行 `cd backend && .venv/bin/alembic upgrade head`。

验收：

- 空库可迁移成功。
- 旧库可迁移成功。
- 新表存在且索引存在。

### T38-2 Trace Service

写入范围：

- `backend/app/services/event_trace.py`
- `backend/app/services/redactor.py`
- `backend/app/tests/test_event_trace_service.py`

具体改动：

- 实现第 11.5 节接口。
- 实现 payload 脱敏和 `native_raw` 默认剥离。
- `trace_id` 使用稳定可搜索字符串，例如 `evt_` 前缀加 UUID/ULID。
- Trace 写入失败不阻断主流程。

禁止改动：

- 不在业务代码里直接拼 SQL 写 Trace。

测试：

- start/span/action/finish happy path。
- payload_snapshot 不含完整 `native_raw`。
- Trace 写库异常不向外抛。

验收：

- 单元测试能证明 Trace 服务可独立使用。

### T38-3 现有交互链路埋点

写入范围：

- `backend/app/services/account_bot_runtime.py`
- `backend/app/services/interaction/delivery.py`
- `backend/app/services/interaction/contracts.py`
- `backend/app/worker/plugins/loader.py`
- `backend/app/worker/plugins/message_ops.py`

具体改动：

- 在交互 Bot 收到 message/callback/payment 时创建 trace。
- 规则匹配、会话匹配、插件加载、插件调用、插件返回、Contract Guard、Delivery Executor 全部写 span。
- 插件 `ctx.log` 自动带 `trace_id`、`plugin_key`、`entry_key`。
- Delivery Executor 每个 action 写 `event_action`，记录请求通道、实际通道、结果、错误。

禁止改动：

- 不改变现有规则驱动调度语义。
- 不因 Trace 写入失败中断消息处理。

测试：

- 扩展 `backend/app/tests/test_account_bot.py`。
- 扩展 `backend/app/tests/test_plugin_security_regression.py`。

验收：

- 现有交互规则仍能启动插件。
- 成功和失败动作都有 trace/action 记录。
- Contract Guard warning/failed 能关联 trace。

### T38-4 UserBot 命令链路埋点

写入范围：

- `backend/app/worker/runtime.py`
- `backend/app/worker/command.py`
- `backend/app/worker/plugins/loader.py`

具体改动：

- 管理员命令进入时创建 trace。
- 命令解析、命中系统命令/插件命令、插件调用、返回动作、发送结果写 span。
- sudo / alias / plugin command 保留原行为。

禁止改动：

- 不重写命令系统。

测试：

- 扩展 `backend/app/tests/test_worker_command.py`。
- 扩展 `backend/app/tests/test_plugin_loader.py`。

验收：

- 日志页能按命令文本或 trace_id 看到完整命令链路。

### T38-5 日志 Trace API

写入范围：

- `backend/app/api/logs.py` 或 `backend/app/api/event_traces.py`
- `backend/app/schemas/logs.py` 或现有 schema 文件
- `backend/app/main.py`
- `backend/app/tests/test_logs_trace_api.py`

具体改动：

- 实现第 11.6 节 API。
- 列表接口默认不返回大 JSON；详情接口才返回 payload 摘要和 span/action。
- 支持按 chat_id/message_id/trace_id/plugin_key/status/event_type/keyword 搜索。

禁止改动：

- 不破坏 `/api/logs/runtime` 和 `/api/logs/audit`。

测试：

- API 鉴权、过滤、详情、空状态。

验收：

- 前端可以只靠新 API 画出日志中心。

### T38-6 前端日志中心重构

写入范围：

- `frontend/src/pages/Logs.tsx`
- `frontend/src/api/system.ts`
- `frontend/src/api/types.ts`
- 必要时新增 `frontend/src/components/logs/*`

具体改动：

- 重构为“总览 / 消息链路 / 插件诊断 / 命令链路 / 动作发送 / 原始日志”。
- 默认页展示系统健康、最近错误插件、最近失败动作、最近事件量。
- 消息链路详情使用时间线，不直接铺 JSON。
- JSON、payload、native_raw_meta 放高级折叠区。
- 原始日志 tab 继续调用旧 `/api/logs/runtime` / `/api/logs/audit`。

禁止改动：

- 不把新日志页做成旧日志表格换皮。
- 不在首屏展示大段 JSON。

测试：

- `cd frontend && ./node_modules/.bin/tsc -b --pretty false`
- `cd frontend && ./node_modules/.bin/vite build`
- 桌面和窄屏手动验收日志页。

验收：

- 用户打开日志页能按 trace/message/plugin/action 四种路径排查问题。

### T38-7 保留策略和设置

写入范围：

- `backend/app/api/rate_limit.py`
- `backend/app/worker/supervisor.py`
- `backend/app/services/event_trace.py`
- `frontend/src/pages/Settings/Index.tsx`
- `frontend/src/api/types.ts`

具体改动：

- 复用现有 `/api/system/settings` 的 `log_retention` 设置，新增 Trace 保留天数、payload_snapshot 保留天数、是否保存完整 native_raw、native_raw 保留天数。
- 定时清理过期 Trace 和 payload_snapshot。
- 默认不保存完整 native_raw。
- Trace 清理与现有 runtime_log 清理同属 supervisor 后台维护职责，但要独立开关和独立保留天数。
- `payload_snapshot` 到期时只清空大字段，不删除 `event_trace` 主记录，保证历史链路统计仍可用。

禁止改动：

- 不改变旧 runtime_log 默认保留策略。
- 不把 `native_raw` 完整内容默认写入数据库。

测试：

- 保留策略归一化测试。
- 清理任务测试。
- payload_snapshot 到期清空但 trace 主记录保留。

验收：

- 大量 Trace 不会无限增长。

### T38-8 文档和发布检查

写入范围：

- `CHANGELOG.md`
- `docs/PLUGIN-API-REFERENCE.md`
- `docs/PLUGIN-CHEATSHEET.md`
- `docs/INTERACTION-BOT-OPTIMIZATION.md`
- `docs/TELEGRAM-FULL-EVENT-BUS-TRACE-PLAN.md`

具体改动：

- 写入 0.38.0 实际落地内容。
- 文档说明 Trace 日志页用法。
- 插件开发文档暂不宣称 Event Bus 已全量开放。

验收命令：

- `cd backend && .venv/bin/ruff check app`
- `cd backend && .venv/bin/pytest -q`
- `cd frontend && ./node_modules/.bin/tsc -b --pretty false`
- `cd frontend && ./node_modules/.bin/vite build`
- `git diff --check`

## 14. 0.39.0 施工任务卡：Event Bus、native_raw 与 Inline

### T39-1 Source Adapter 与标准事件生成

写入范围：

- `backend/app/services/event_bus.py`
- `backend/app/worker/plugins/events.py`
- `backend/app/services/account_bot_runtime.py`
- `backend/app/worker/runtime.py`

具体改动：

- 新增 Source Adapter，把 UserBot/Telethon、Bot API message、callback_query、inline_query、chosen_inline_result、payment_confirmed 转为同一标准事件信封。
- 保留现有 `event_from_interaction_payload(payload)`，但让它读取新标准信封。
- 所有新事件必须带 `trace_id`。

禁止改动：

- 不把 live Telethon event/client 直接放进 payload。

测试：

- `backend/app/tests/test_plugin_events.py`
- 新增 `backend/app/tests/test_event_bus_source_adapters.py`

验收：

- 同一插件能用同一字段读取 UserBot 消息、交互 Bot 消息和 Inline Query。

### T39-2 Event Bus 与订阅匹配

写入范围：

- `backend/app/services/event_bus.py`
- `backend/app/services/account_bot_service.py`
- `backend/app/services/remote_plugin_service.py`
- `backend/app/worker/plugins/loader.py`
- `backend/app/schemas/account_bot.py`

具体改动：

- 解析 manifest `event_subscriptions`。
- 按 account、source、event_type、scope、filters 先过滤，再调用插件。
- 每次匹配、跳过、投递都写 Trace span，跳过必须有 `reason_code`。
- 旧 `interaction_entries.events` 映射为 `event_subscriptions` 的兼容输入。

禁止改动：

- 不删除现有交互规则 UI，0.39 只让它降级为订阅条件来源。

测试：

- manifest 规范化测试。
- 订阅匹配 happy path / skip reason / disabled plugin。

验收：

- 插件没启动时，日志页能说明是未订阅、未启用、未安装、scope 不匹配还是过滤条件不匹配。

### T39-3 `native_raw` 能力

写入范围：

- `backend/app/services/event_bus.py`
- `backend/app/services/account_bot_service.py`
- `backend/app/services/remote_plugin_service.py`
- `backend/app/api/plugins.py`
- `frontend/src/pages/Plugins/*`
- `frontend/src/pages/Interaction/*` 如存在相关提示

具体改动：

- 插件声明 `capabilities.telegram_native_raw.enabled=true` 后才下发 `payload["native_raw"]`。
- WebUI 插件详情、安装/启用、规范警告处展示高风险提示。
- Trace 记录 `native_raw_meta`。
- 默认不在 `payload_snapshot` 中保存完整 `native_raw`。

禁止改动：

- 不把 token/session/API key 当 native_raw 的一部分下发。
- 不给未声明插件下发 native_raw。

测试：

- 声明插件能拿到 native_raw。
- 未声明插件拿不到 native_raw。
- payload_snapshot 不含 native_raw。
- native_raw_meta 包含 source/driver/object/size_bytes。

验收：

- 插件可以基于 Telegram 数字 ID 关系链做严格风控。

### T39-4 Inline Query 入口

写入范围：

- `backend/app/services/account_bot_runtime.py`
- `backend/app/services/account_bot_service.py`
- `backend/app/db/models/rate_limit.py`
- `backend/app/services/rate_limit_service.py`
- `frontend/src/api/types.ts`

具体改动：

- getUpdates / webhook `allowed_updates` 加入 `inline_query`、`chosen_inline_result`。
- `_extract_incoming` 或新的 Source Adapter 支持 Inline Query。
- Inline Query 事件没有 chat_id，必须按 inline scope 判断。
- rate limit 增加 `inline_query`。

禁止改动：

- 不把 Inline Query 当普通 message 伪造 chat_id。

测试：

- Bot API inline_query update 生成标准事件。
- chosen_inline_result update 生成标准事件。
- inline_all / owner_only / known_users scope 匹配。

验收：

- 发送 `@botname keyword` 能在 Trace 中看到 inline_query。

### T39-5 Delivery Executor 支持 `answer_inline_query`

写入范围：

- `backend/app/services/account_bot_service.py`
- `backend/app/services/interaction/delivery.py`
- `backend/app/services/interaction/contracts.py`
- `backend/app/worker/plugins/message_ops.py`
- `backend/app/tests/test_account_bot.py`

具体改动：

- 新增 `account_bot_service.answer_inline_query()` 调 Bot API `answerInlineQuery`。
- `BufferedMessageOps` 新增 `answer_inline_query()`。
- Contract Guard 支持 `answer_inline_query` 动作。
- Delivery Executor 记录 action 结果和错误。

禁止改动：

- 不让 userbot_reply 承接 Inline Answer。

测试：

- answer_inline_query happy path。
- 缺 inline_query_id 返回 failed action。
- Telegram API 失败写 event_action。

验收：

- 插件可以返回 Inline 结果，日志页能看到结果数量和 API 响应。

### T39-6 Event Bus 前端与交互中心提示

写入范围：

- `frontend/src/pages/Interaction/*`
- `frontend/src/pages/Plugins/*`
- `frontend/src/api/types.ts`

具体改动：

- 插件详情展示 event_subscriptions。
- 对 `telegram_native_raw` 和 `inline_all` 展示高风险提示。
- 交互中心规则详情中标出该规则已映射到哪些 Event Bus 订阅。

禁止改动：

- 不把风险提示做成阻断安装；按个人可信插件标准只提醒和留痕。

测试：

- 前端类型检查和构建。
- 手动检查长插件名、长订阅列表、窄屏。

验收：

- 管理员能看懂插件会收到哪些事件、是否请求原生数据、是否开放 Inline。

### T39-7 文档与示例插件

写入范围：

- `docs/PLUGIN-API-REFERENCE.md`
- `docs/PLUGIN-REMOTE.md`
- `docs/PLUGIN-CHEATSHEET.md`
- `docs/PLUGIN-SAFETY.md`
- 示例插件或测试 fixture

具体改动：

- 新增 Event Bus 插件最小示例。
- 新增 native_raw 防改名诈骗风控示例。
- 新增 Inline Query 插件示例。
- 明确旧平铺字段不作为新插件主路径。

验收命令：

- `backend/.venv/bin/python scripts/validate-plugin-examples.py`
- `backend/.venv/bin/python scripts/validate-installed-interaction-plugins.py`

### T39-8 废弃通道和旧字段收口

写入范围：

- `backend/app/worker/plugins/manifest.py`
- `backend/app/services/remote_plugin_service.py`
- `backend/app/services/interaction/contracts.py`
- `backend/app/services/interaction/delivery.py`
- `frontend/src/pages/Plugins/*`
- `frontend/src/pages/Interaction/*`
- `docs/PLUGIN-API-REFERENCE.md`

具体改动：

- manifest lint 对 `notice` / `bbot_notice` / `notice_bot` 输出迁移警告或阻止保存，提示改用 `interaction_bot`、`userbot_reply`、`auto`。
- Delivery Executor 遇到旧通道值时不尝试发送，直接记录 failed action 和 `send_channel_deprecated`。
- 插件 payload 不公开 `raw_event`；如存在兼容字段，只能是脱敏摘要，并在规范警告中提示改用 `native_raw` 声明。
- WebUI 规范警告将旧通道和旧字段标成红色高级警告。

禁止改动：

- 不把 `notice` 重命名成 `interaction_bot` 自动执行，避免误发到错误通道。
- 不给未声明 `telegram_native_raw` 的插件提供旧 `raw_event` 后门。

测试：

- 旧通道 manifest lint。
- 运行时旧通道 action failed。
- 未声明 native_raw 时 payload 不含 raw_event/native_raw。

验收：

- 旧插件必须看到明确迁移提示，新插件不会再从文档学到旧通道。

### T39-9 插件仓库字段贯通

写入范围：

- `backend/app/services/remote_plugin_service.py`
- `backend/app/services/feature_service.py`
- `backend/app/schemas/feature.py`
- `frontend/src/api/types.ts`
- 插件仓库刷新/更新相关 API 和页面

具体改动：

- 远程插件列表、私有 GitHub 仓库、`tree/<branch>` URL、仓库一键更新、已安装插件升级，都必须保留 `event_subscriptions`、`capabilities`、`usage`、`interaction_entries`。
- 仓库刷新后 WebUI 能立即看到新增/变更的 Event Bus 声明和风险提示。
- 仓库一键更新时，先展示将升级的插件、版本变化、风险能力变化；执行后写 audit/trace 或 runtime log。

禁止改动：

- 不把远程插件字段裁剪成旧 feature matrix 子集。

测试：

- 远程 manifest 解析保留新字段。
- feature matrix API 返回新字段。
- 前端类型覆盖新字段。

验收：

- 从远程插件库安装/升级的插件，与本地插件 manifest 在 Event Bus 字段上表现一致。

## 15. 0.40.0 施工任务卡：最终开放插件运行模型

### T40-1 旧规则模型收敛

写入范围：

- `backend/app/services/account_bot_runtime.py`
- `backend/app/services/account_bot_service.py`
- `frontend/src/pages/Interaction/*`
- `docs/INTERACTION-BOT-OPTIMIZATION.md`

具体改动：

- 旧交互规则仍可配置，但内部作为 Event Bus 订阅条件。
- 文案从“规则驱动插件”调整为“事件订阅 + 规则过滤”。
- 日志页显示旧规则 ID 和新 trace 之间的映射。

验收：

- 老规则配置路径仍可用。
- 新插件可完全不依赖旧规则，通过 event_subscriptions 工作。

### T40-2 插件运行入口统一

写入范围：

- `backend/app/worker/plugins/loader.py`
- `backend/app/worker/plugins/events.py`
- `backend/app/worker/plugins/message_ops.py`
- 官方/内置插件目录

具体改动：

- 插件统一在 `on_event` 或新版 `on_interaction` 中处理 message/command/callback/inline/payment。
- 保留一个清晰兼容层，但文档主推新入口。
- 官方插件迁移到新事件字段。

验收：

- 官方插件不再依赖旧平铺 payload 作为主路径。

### T40-3 最终日志页验收与 UI 打磨

写入范围：

- `frontend/src/pages/Logs.tsx`
- `frontend/src/components/logs/*`

具体改动：

- 日志页默认入口能直接回答第 1 节的问题。
- PWA/窄屏下底部导航、筛选、时间线、详情抽屉不重叠。
- 错误原因使用中文可读文案，同时保留 reason_code。

验收：

- 桌面、平板、窄屏截图验收。
- 真实或 fixture trace 展示完整链路。

### T40-4 开发指南最终版

写入范围：

- `docs/PLUGIN-API-REFERENCE.md`
- `docs/PLUGIN-REMOTE.md`
- `docs/PLUGIN-CHEATSHEET.md`
- `README.md`
- `CHANGELOG.md`

具体改动：

- 插件开发指南全面切换到 Event Bus + Trace + MessageOps。
- 移除或降级旧系统描述，避免开发者照旧机制写新插件。
- 写清“如何排查插件为什么没启动”“如何查某条消息走到哪一步”。

验收：

- 开发者只看插件开发指南，就能写出 message/callback/inline/payment 四类插件。

### T40-5 发布检查

验收命令：

- `cd backend && .venv/bin/ruff check app`
- `cd backend && .venv/bin/pytest -q`
- `backend/.venv/bin/python scripts/validate-plugin-examples.py`
- `backend/.venv/bin/python scripts/validate-installed-interaction-plugins.py`
- `cd frontend && ./node_modules/.bin/tsc -b --pretty false`
- `cd frontend && ./node_modules/.bin/vite build`
- `git diff --check`

发布要求：

- 按 SemVer 更新到对应版本。
- 四处版本号同步。
- 中文 CHANGELOG。
- 中文 commit / PR 文案。
- 不覆盖 main，继续推到新分支或当前 0.33+ 工作分支。

### T40-6 插件迁移最终验收

写入范围：

- 官方/内置/可选插件目录
- `docs/PLUGIN-API-REFERENCE.md`
- `docs/PLUGIN-CHEATSHEET.md`
- 插件验证脚本

具体改动：

- 所有随 TelePilot 一起维护的插件 manifest 补齐 `usage`、`event_subscriptions`、`capabilities`。
- 交互型插件迁移到标准事件信封和 `ctx.messages`。
- 游戏/玩法类插件必须明确两类动作：普通互动走 `interaction_bot`，收款/发奖/结算走 userbot/settlement。
- 图片/AI/工具类插件必须明确是管理员命令调度还是玩家关键词调度。
- 插件示例必须覆盖 message、command、callback、inline、payment 五类事件中的至少四类；剩余类别必须在文档中说明如何扩展。

禁止改动：

- 不为了通过验证而把插件写成空声明。
- 不保留依赖旧平铺 payload 的官方主路径。

测试：

- `backend/.venv/bin/python scripts/validate-plugin-examples.py`
- `backend/.venv/bin/python scripts/validate-installed-interaction-plugins.py`
- 定向插件单元测试或 dry-run 测试。

验收：

- 开发者照官方插件或示例插件写新插件，不需要再“缝补”旧规则、旧 notice、旧 raw_event。

### T40-7 最终版文档发布审计

写入范围：

- `README.md`
- `docs/PLUGIN-API-REFERENCE.md`
- `docs/PLUGIN-REMOTE.md`
- `docs/PLUGIN-CHEATSHEET.md`
- `docs/PLUGIN-SAFETY.md`
- `docs/INTERACTION-BOT-OPTIMIZATION.md`
- `CHANGELOG.md`

具体改动：

- README 只保留面向用户的一键部署、插件仓库、交互中心、日志中心入口，不展开旧机制。
- 插件开发指南按“准备 manifest -> 声明事件订阅 -> 读取事件 -> 返回动作 -> 看日志排障 -> 发布远程插件”的顺序重写。
- `PLUGIN-SAFETY` 从“平台强沙箱”改为“个人可信插件风险提示 + 可审计能力边界”。
- `INTERACTION-BOT-OPTIMIZATION` 归档为历史设计或更新为最终框架说明，避免和新开发指南冲突。
- CHANGELOG 用中文把 0.32.0 之后已落地的重要改动分组写清楚，UI 小修简写，架构改动详细写。

文档审计命令：

- `rg -n "notice|bbot_notice|notice_bot|raw_event|平铺 payload|旧规则驱动|Contract Guard|Event Bus|native_raw|answer_inline_query" README.md docs`

验收：

- 搜索命中的旧概念只能出现在迁移说明、废弃说明或历史说明里。
- 新开发者只看插件开发指南，可以写出符合最终版框架的插件。

## 16. 多 Agent 并行分工建议

Wave 0：冻结契约和现状复核

- 主 Agent：确认当前分支、版本文件、未提交改动、已半落地 Trace 代码。
- 主 Agent：先修明显的语法/导入错误，跑最小静态检查，保证后续 Agent 不是在坏基线上并行。
- 只读 Reviewer：对照第 0.4 节确认本轮没有遗漏最终版不可缩水项。

Wave 0 通过门禁：

- `git status --short --branch` 已记录。
- 半落地代码的风险已列入执行清单，特别是 `native_raw` 默认下发问题。
- 没有未解释的语法错误阻塞并行。

Wave 1：0.38 数据和 Trace 底座

- Agent A：T38-1 + T38-2，负责模型、迁移、Trace Service。
- Agent B：T38-5，等 A 的 schema 稳定后做 API。
- Agent C：T38-6，先按 API mock 写 UI，后接真实 API。
- 主 Agent：T38-3 + T38-4 集成埋点，避免多人同时改 runtime 主链路。

Wave 1 通过门禁：

- 数据库迁移可从空库和现有库升级。
- 交互 Bot 现有 message/callback/payment 规则行为不回归。
- 新日志 API 能返回 trace、span、action、plugin status。
- 新日志 UI 可用，旧 runtime/audit 仍在原始日志入口。

Wave 2：0.39 Event Bus 与 Inline

- Agent A：T39-1 + T39-2，负责 Event Bus 和订阅匹配。
- Agent B：T39-4 + T39-5，负责 Inline 入口和 Delivery Executor。
- Agent C：T39-3 + T39-6，负责 native_raw 能力和 WebUI 风险提示。
- Agent D：T39-7 + T39-8，只写文档、示例、废弃通道/旧字段 lint，不改 runtime 主链路。
- Agent E：T39-9，负责远程插件仓库字段贯通、仓库刷新、一键更新相关字段保真。
- 主 Agent：合并冲突、补 Trace 串联、跑全量验证。

Wave 2 通过门禁：

- `event_subscriptions` 和 `capabilities` 已贯穿 manifest、远程插件解析、feature manifest、前端类型和 WebUI 提示。
- 未声明 `telegram_native_raw` 的插件拿不到 `native_raw`；声明插件可以拿到，并有 Trace 留痕。
- Inline Query 能被订阅插件处理并返回 `answer_inline_query`。
- 所有匹配、跳过、投递都有稳定 `reason_code`。
- 旧 `notice` / `bbot_notice` / `notice_bot` 不能被执行，只能给出迁移提示。
- 远程插件库安装和升级不会丢失 Event Bus 新字段。

Wave 3：0.40 收敛最终版

- Agent A：T40-1 + T40-2，负责旧规则收敛和官方插件迁移。
- Agent B：T40-3，负责日志页最终 UI 验收。
- Agent C：T40-4 + T40-7，负责开发指南最终版和文档发布审计。
- Agent D：T40-6，负责插件迁移最终验收和示例 dry-run。
- 主 Agent：T40-5 发布检查、版本号、CHANGELOG、最终 review。

Wave 3 通过门禁：

- 新插件开发文档不再把旧规则驱动和平铺 payload 写成主路径。
- 官方/可选插件示例已按新事件信封和 MessageOps 校准。
- 日志页能完成第 17 节人工验收。
- 版本号、中文 CHANGELOG、中文 commit/PR 文案准备完毕。
- 文档审计确认旧概念只出现在迁移/废弃/历史说明里。

并行禁区：

- `backend/app/services/account_bot_runtime.py`、`backend/app/worker/runtime.py`、`backend/app/worker/plugins/loader.py` 同一时间只能由主 Agent 或一个指定 Agent 写入。
- Alembic revision 只能由一个 Agent 创建。
- 版本号和 CHANGELOG 正式发布段只由主 Agent 在 release check 时写。
- 前端日志页组件可以拆，但 `frontend/src/pages/Logs.tsx` 的路由和数据流由一个 Agent 统一收口。

任何 Agent 发现以下情况必须停下来交给主 Agent：

- 需要修改同一个 runtime 主链路文件但当前不在自己的写入范围。
- 需要新增或重写数据库迁移。
- 需要改变 `send_via`、`event_subscriptions`、`native_raw` 这三个公共契约的语义。
- 测试失败显示现有插件行为回归。
- 需要部署、推送、bump 版本号或写正式 CHANGELOG 发布段。

### 16.1 Agent 交付格式

每个执行 Agent 的最终报告必须按以下格式交付，方便主 Agent 做最终版收口：

```text
任务卡：
状态：未开始 / 半落地 / 可测 / 已完成
改动文件：
公共契约变化：
未触碰禁区：
自动验证：
人工验收：
文档同步：
剩余风险：
需要主 Agent 复核：
```

状态只能由证据支撑：

- 只改了代码，没有测试：最多 `半落地`。
- 定向测试通过，但未跑相关集成/构建：最多 `可测`。
- 前端页面未做桌面和窄屏验收：前端相关任务最多 `可测`。
- 文档未同步：插件契约相关任务最多 `可测`。
- 有失败验证命令且未解释是否阻塞：不能标 `已完成`。

主 Agent 合并任何子任务前必须核验：

- diff 是否只在写入范围内。
- 是否引入了第二套事件入口、第二套发送通道或第二套插件 payload 主协议。
- 是否有未解释的测试失败。
- 是否破坏旧命令和旧交互规则回归底线。
- 是否需要补充 CHANGELOG `Unreleased`，但不得在非发布节点随手 bump 版本。

### 16.2 最终版收口顺序

主 Agent 收口时必须按以下顺序执行，不能先写发布材料再补实现：

1. 固定当前分支和工作树状态，确认没有未解释的外部改动。
2. 对照第 0.4 节，把最终版不可缩水清单逐项标为 `未开始`、`半落地`、`可测`、`已完成`。
3. 先修 `未开始` 和阻塞性的 `半落地`，再处理 UI 打磨和文档措辞。
4. 运行第 18.2 节部署前检查中的全部自动验证。
5. 完成第 17 节人工验收，至少覆盖普通消息、未触发消息、插件动作、Contract Guard、插件加载失败、inline、native_raw、旧 notice、远程插件安装/升级、窄屏日志页。
6. 通过文档审计后，才 bump 版本、整理中文 CHANGELOG、commit、push。
7. 部署到服务器前备份，部署后按第 18.3 节确认远端 commit、版本、迁移、健康检查和关键 Trace。

如果第 4-5 步任一项失败，版本号和 CHANGELOG 正式版本段不得提前落定；失败项必须回到对应任务卡修复。

## 17. 最终验收矩阵

### 17.0 能力闭环登记表

最终版验收时必须先填写这张表。任一能力没有达到 `已完成`，最终版不得发布。

| 能力 | 对应任务卡 | 必须证据 | 完成标准 |
| --- | --- | --- | --- |
| Trace 数据层 | T38-1、T38-2、T38-7 | 迁移、模型导入、Trace Service 测试、清理任务测试 | 新表可迁移，Trace 写入失败不阻断业务，保留策略生效 |
| 旧链路 Trace | T38-3、T38-4 | 交互 Bot、UserBot 命令、loader、Delivery、Contract Guard 定向测试 | 现有规则和命令行为不回归，成功/失败都能查 trace |
| 新日志页 | T38-5、T38-6、T40-3 | Trace API 测试、前端类型/构建、桌面/窄屏截图验收 | 日志页能查消息、插件、命令、动作和原始日志 |
| Event Bus 主路径 | T39-1、T39-2、T40-1 | Source Adapter 测试、订阅匹配测试、旧规则映射测试 | 所有 Telegram 来源先标准化，再匹配订阅并写 reason_code |
| `native_raw` 边界 | T39-3、T39-8 | 声明/未声明插件测试、payload_snapshot 测试、WebUI 风险提示 | 只有声明插件拿到 native_raw，日志默认不持久化完整原生数据 |
| Inline 闭环 | T39-4、T39-5 | inline_query/chosen_inline_result 标准化、answer_inline_query 成功/失败测试 | Inline 事件可订阅、可回应、可在日志页排障 |
| MessageOps / Delivery | T38-3、T39-5、T40-2 | action 执行测试、失败动作测试、实际通道记录 | 插件请求动作和平台实际执行都写 event_action |
| Contract Guard 新定位 | T38-3、T39-8 | warning/blocked/failed 测试、旧通道迁移提示 | 越声明调用有告警，不支持/废弃能力明确失败 |
| 远程插件字段贯通 | T39-9 | 远程解析、安装、升级、feature matrix、前端类型测试 | `usage`、`event_subscriptions`、`capabilities` 任一层不丢失 |
| 交互中心和插件 UI | T39-6、T40-1、T40-3 | 风险提示、订阅展示、旧规则映射、窄屏验收 | 管理员能看懂插件收什么事件、用什么能力、由谁发送 |
| 官方/示例插件迁移 | T40-2、T40-6 | 验证脚本、dry-run、定向插件测试 | 示例覆盖 message、command、callback、inline、payment 的核心写法 |
| 开发指南最终版 | T39-7、T40-4、T40-7 | 文档 grep、示例验证脚本、README/CHANGELOG 审计 | 新开发者不读旧机制也能写出可运行插件 |
| 发布与部署 | T40-5、18.2、18.3 | 版本四处同步、中文 CHANGELOG、push、远端健康检查 | 服务器运行新版本，Trace API 和日志页可用，有回滚点 |

后端自动验证：

- Trace 表迁移成功。
- Trace Service 单元测试通过。
- 交互 Bot message/callback/payment 产生 trace。
- UserBot command 产生 trace。
- Event Bus 订阅匹配和跳过 reason_code 正确。
- `native_raw` 只给声明插件。
- `payload_snapshot` 默认不含完整 `native_raw`。
- `inline_query` / `chosen_inline_result` 可标准化。
- `answer_inline_query` 成功和失败都记录 event_action。
- Contract Guard warning/failed 都能关联 trace。
- 旧 `notice` / `bbot_notice` / `notice_bot` 不能执行，且返回 `send_channel_deprecated`。
- 插件读取旧 `raw_event` 不能绕过 `telegram_native_raw` 声明。
- 远程插件仓库解析、安装、升级保留 `event_subscriptions`、`capabilities`、`usage`。
- Trace 清理任务能清理过期 payload/native_raw，同时保留主记录。

前端自动验证：

- 日志 API 类型完整。
- 日志页构建通过。
- 空状态、加载态、错误态可用。
- 插件详情、规范警告、交互中心能展示 event subscriptions、native_raw 风险、inline_all 风险、废弃通道警告。
- 插件仓库刷新/一键更新后，新字段和风险提示实时更新。

人工验收：

- 在群里发一条普通消息，日志页能查到完整链路。
- 触发一个不会启动插件的消息，日志页能显示未启动原因。
- 启动一个插件并让它返回 `send_message`，动作发送页能看到实际通道和 Telegram message_id。
- 触发一个 Contract Guard warning，日志页能定位到插件和 action。
- 让插件加载失败，插件诊断页能显示失败原因和最近 trace。
- 发送 `@botname 关键词`，日志页能看到 inline_query 和 answer_inline_query。
- 声明 `telegram_native_raw` 的测试插件能读取 native_raw；未声明插件读取不到。
- 窄屏/PWA 打开日志页，筛选、时间线、详情区域不重叠。
- 用一个旧 `notice` send_via 测试插件触发动作，页面能看到明确迁移错误，不会误发消息。
- 从远程插件库安装或升级一个声明 Event Bus 的插件后，插件详情页能看到完整订阅和能力提示。
- 只看新版插件开发指南，新建一个最小 message/callback 插件并通过验证脚本。

回滚验收：

- 关闭 Trace 写入后，旧 `runtime_log` / `audit_log` 仍可用。
- Event Bus 新链路异常时，可临时回退到旧规则驱动路径。
- Inline 支持异常时，不影响普通 message/callback 处理。
- 关闭 `native_raw_persist_enabled` 后，不影响已声明插件运行，只是不再持久化完整原生数据。

## 18. 发布、部署与回滚要求

### 18.1 发布要求

最终版准备推送时必须：

- 按 SemVer 选择版本；完整最终版建议为 `0.40.0 minor（次版本）`。
- 同步修改 `backend/app/__init__.py`、`backend/pyproject.toml`、`frontend/package.json`、`frontend/src/lib/version.ts`。
- `CHANGELOG.md` 使用中文写入正式版本段，只记录实际落地内容。
- commit、PR、部署说明使用中文。
- 不覆盖 main，继续推送到当前 0.33+ 工作分支或新建 `codex/0.40-event-bus-trace-final` 分支。

### 18.2 部署前检查

部署到服务器前必须完成：

- `git diff --check`
- `cd backend && .venv/bin/ruff check app`
- `cd backend && .venv/bin/pytest -q`
- `backend/.venv/bin/python scripts/validate-plugin-examples.py`
- `backend/.venv/bin/python scripts/validate-installed-interaction-plugins.py`
- `cd frontend && ./node_modules/.bin/tsc -b --pretty false`
- `cd frontend && ./node_modules/.bin/vite build`

如果某条命令因环境失败，必须写清原因、替代验证和剩余风险；不能把环境失败当通过。

### 18.3 服务器部署验收

部署到 `144.24.5.159` 后必须确认：

- 远端当前 commit 与本地推送 commit 一致。
- Docker 服务启动成功，数据库迁移已执行。
- Web 首页显示新版本号。
- `/api/logs/trace/overview` 可返回数据或空状态。
- 日志页可打开，原始日志 tab 仍可用。
- 至少触发一次普通消息 trace、一次插件调用 trace、一次 action trace。
- 如果有可测交互 Bot，触发一次 callback 或 Inline Query 并确认 Trace。
- `docker compose logs --tail=100 web` 没有迁移、导入、API 路由或前端资源错误。

### 18.4 回滚要求

回滚必须优先保护数据：

- 部署前备份 `.env`、compose 文件和数据库，记录备份路径。
- 代码回滚到上一稳定 commit 后，旧 `runtime_log` / `audit_log` 仍可排障。
- 新 Trace 表可以保留不用，不在紧急回滚中删表。
- 如果 Event Bus 新链路出问题，先通过配置关闭新投递或回退旧规则路径，再考虑代码回滚。
- 如果 Inline Query 出问题，先移除 `allowed_updates` 中的 inline 事件或停用相关订阅，不影响普通消息和 callback。

## 19. 最终版执行总控

本节用于把前面的架构、任务卡和验收矩阵收束为一套可以直接执行的总控流程。执行者不能跳过本节直接按单个任务卡挑着做；否则很容易出现“某个局部功能可用，但最终版链路仍然断开”的半落地状态。

### 19.1 最终版前置锁定

开始实现前必须完成以下锁定：

- **契约锁定**：第 11 节中的标准事件信封、manifest 字段、MessageOps/action、Event Bus Service、Trace Service、日志 API 不再随意改名。确需改名时，必须同时改代码、前端类型、测试 fixture、插件示例和开发文档。
- **入口锁定**：所有 Telegram 来源只能经 Source Adapter 进入 `TelePilotEvent`。新代码不得新增绕过 Event Bus 的插件调用入口。
- **出口锁定**：所有插件动作只能经 MessageOps/action 到 Delivery Executor。新代码不得让插件直接拿 Bot token、UserBot client、Telegram driver 或 live event。
- **风险边界锁定**：平台按个人可信插件标准放宽业务自由度，但不放宽凭据、live client、旧 notice 通道、普通 Bot 转账这四条客观边界。
- **版本锁定**：最终版未通过第 17 节和第 19.9 节之前，不提前 bump 到正式版本，不提前写正式 CHANGELOG 版本段。

### 19.2 五条真实链路必须闭环

最终版不是“有 Event Bus 类”和“有日志页”就算完成。以下五条真实链路必须全部闭环，每条链路都要能在日志页查到 trace、span、action 和失败原因。

1. **普通群消息链路**
   - 输入：UserBot 或交互 Bot 收到群消息。
   - 必经：Source Adapter -> `TelePilotEvent` -> Trace -> Event Bus matcher -> 插件投递或 skipped reason。
   - 产出：日志页能看到消息标准化、订阅匹配、插件跳过/执行、动作发送。

2. **管理员命令链路**
   - 输入：账号主人或授权管理员发送带命令前缀的命令。
   - 必经：UserBot command parser -> Source Adapter -> Trace -> Event Bus matcher 或旧命令兼容映射 -> 插件/系统处理器。
   - 产出：日志页能看到命令解析、权限、命中处理器、插件调用、消息操作。

3. **按钮回调链路**
   - 输入：交互 Bot 收到 `callback_query`。
   - 必经：Source Adapter -> Trace -> session/rule/subscription match -> 插件执行 -> `answer_callback` 或后续消息动作。
   - 产出：日志页能看到 callback data、会话命中、插件入口、按钮 ACK 成败。

4. **Inline 链路**
   - 输入：交互 Bot 收到 `inline_query` 或 `chosen_inline_result`。
   - 必经：Source Adapter -> Trace -> inline scope -> 插件执行 -> `answer_inline_query`。
   - 产出：日志页能看到 query、scope、结果数量、Telegram API 成败和选择结果。

5. **转账/付款确认链路**
   - 输入：UserBot 监听到第三方转账通知 Bot 的群消息，或平台解析到付款确认。
   - 必经：Source Adapter -> `source_actor.type=external_bot` 或 `payment_confirmed` -> Trace -> 插件投递 -> settlement/userbot 动作。
   - 产出：日志页能看到外部通知来源、付款人/玩家归属、插件处理、发奖/结算动作；普通 Bot 不得执行转账。

任一链路只能做到“收到消息”或“插件能运行”，但缺 Trace、reason_code、action 记录、失败展示中的任一项，状态都只能记为 `半落地` 或 `可测`，不能记为 `已完成`。

### 19.3 验收夹具和测试插件包

最终版必须准备一组稳定 fixture 和测试插件，用来防止后续继续靠线上手测猜问题。

固定 fixture 覆盖项如下。文件名可以按现有测试目录调整，但最终证据台账必须逐项映射到这些事件类型，不能只写“已有相关测试”：

- `backend/app/tests/fixtures/event_bus/userbot_message.json`
- `backend/app/tests/fixtures/event_bus/interaction_bot_message.json`
- `backend/app/tests/fixtures/event_bus/callback_query.json`
- `backend/app/tests/fixtures/event_bus/inline_query.json`
- `backend/app/tests/fixtures/event_bus/chosen_inline_result.json`
- `backend/app/tests/fixtures/event_bus/external_payment_notice.json`
- `backend/app/tests/fixtures/event_bus/native_raw_telethon_message.json`
- `backend/app/tests/fixtures/event_bus/deprecated_notice_action.json`

固定测试插件或示例插件覆盖项如下。插件名可以按实际目录调整，但最终证据台账必须证明每类行为都被可运行示例或自动化测试覆盖：

- `event_echo`：订阅 message/callback，回显标准字段，验证标准事件信封和 `ctx.messages.send_text`。
- `event_inline_demo`：订阅 inline_query/chosen_inline_result，返回 `answer_inline_query`。
- `event_payment_guard`：订阅 payment_confirmed/external_payment_notice，验证付款人、玩家、reply_to 和 settlement/userbot 动作。
- `event_native_raw_audit`：声明 `telegram_native_raw`，读取 `native_raw` 并记录 `native_raw_meta`。
- `event_deprecated_notice_probe`：故意返回旧 `notice`/`bbot_notice` 通道，验证 `send_channel_deprecated` 和不会误发。

这些插件不一定都作为用户可见内置插件发布，但必须能被验证脚本或测试用例调用。最终版文档中的示例代码应优先从这组可运行示例中抽取，避免文档和真实运行时漂移。

### 19.4 代码落地的唯一顺序

最终版必须按以下顺序收口。可以并行做局部任务，但合并和验收顺序不能反过来。

1. **Trace 数据层和服务稳定**
   - 先保证迁移、模型、Trace Service、清理策略稳定。
   - 任何业务链路接 Trace 前，Trace 写入失败必须已被证明不会阻断主流程。

2. **现有链路先埋点，不改变行为**
   - 交互 Bot、UserBot 命令、loader、Contract Guard、Delivery Executor 先全部记录 trace/span/action。
   - 这一阶段旧规则语义不变，作为回归底线。

3. **manifest 新字段端到端贯通**
   - `usage`、`event_subscriptions`、`capabilities` 必须贯穿 loader、远程仓库、安装记录、feature matrix、前端类型、插件 UI、规范警告。
   - 字段未端到端贯通前，不开始大规模迁移插件。

4. **Source Adapter 和 Event Bus 成为主路径**
   - UserBot、交互 Bot、callback、inline、payment 都标准化为 `TelePilotEvent`。
   - 旧规则只作为订阅条件来源，不再作为第二套插件调度真相。

5. **MessageOps/action 和 Contract Guard 收口**
   - 所有发送、编辑、删除、置顶、callback ACK、inline answer、settlement 都写 `event_action`。
   - 越声明调用放行但告警；不支持/废弃能力明确失败。

6. **日志页按 Trace 重构**
   - UI 使用真实 Trace API 和 fixture 验收，不再围绕旧文本日志组织默认入口。
   - 错误态必须暴露 API 错误，不能静默伪装为空状态。

7. **插件和文档最终迁移**
   - 维护内插件、示例插件、开发指南、远程插件说明统一切到 Event Bus + MessageOps。
   - 旧机制只作为迁移说明，不作为推荐路径。

8. **发布和部署**
   - 全部自动验证、人工验收、文档审计通过后，才 bump 版本、写中文 CHANGELOG、commit、push、部署。

### 19.5 当前分支必须关闭的已知阻塞项

执行最终版时，以下项必须被逐一关闭。它们不是“后续优化”，而是最终版门禁的一部分：

- 插件开发文档仍推荐旧 `interaction_entries`、旧规则、旧平铺 payload 或旧 `event.reply/respond` 作为主路径时，必须重写。
- `PLUGIN-SAFETY` 仍把平台描述成强沙箱主导时，必须改为“个人可信插件风险提示 + 可审计能力边界”。
- 官方/示例/维护插件 manifest 缺 `usage`、`event_subscriptions`、`capabilities` 时，不能通过最终版插件验收。
- 日志页如果 Trace API 出错却显示空状态，必须修复为显式错误态。
- 日志总览如果看不到 DB、Redis、Worker、账号/Bot 状态，不能作为最终日志中心。
- 插件中心、远程仓库、交互中心如果不展示 `event_subscriptions`、`telegram_native_raw`、`inline_all`、废弃通道风险，不能通过最终 UI 验收。
- 远程仓库一键更新如果不能在更新前展示版本变化和风险能力变化，不能称为最终版插件仓库体验。
- 交互中心如果只能展示旧规则列表，而看不到规则映射到哪些 Event Bus 订阅，不能通过最终交互中心验收。
- `event_bus_delivery_enabled`、`inline_updates_enabled`、Trace 保留策略如果只是设置字段但未接入运行时，不得记为完成。
- `raw_event` 如果仍可让插件绕过 `telegram_native_raw` 声明拿到原生对象，必须移除或改成迁移警告。

### 19.6 Review 必跑审计命令

最终 review 必须执行以下审计。命中结果不能简单删除；每条命中都要归类为“合法迁移说明、合法历史说明、测试故意覆盖、需要修复”。

旧机制审计：

```bash
rg -n "notice|bbot_notice|notice_bot|raw_event|平铺 payload|旧规则驱动|event\\.reply|event\\.respond|ctx\\.client\\.send_message" README.md docs backend frontend examples scripts
```

新契约覆盖审计：

```bash
rg -n "event_subscriptions|capabilities|telegram_native_raw|native_raw_meta|answer_inline_query|chosen_inline_result|settlement|send_channel_deprecated" README.md docs backend frontend examples scripts
```

插件 payload 主路径审计：

```bash
rg -n "payload\\[\"text\"\\]|payload\\.get\\(\"text\"\\)|payload\\[\"chat_id\"\\]|payload\\.get\\(\"chat_id\"\\)" docs backend examples
```

发送通道审计：

```bash
rg -n "send_via|channel_selector|interaction_bot|userbot_reply|auto|notice" backend frontend docs examples
```

Trace 覆盖审计：

```bash
rg -n "start_trace|record_span|record_action|finish_trace|trace_id|reason_code" backend/app
```

审计通过标准：

- 旧机制命中只能出现在迁移、废弃、历史或故意回归测试里。
- 新契约命中必须覆盖后端 schema、服务、前端类型、UI、文档、验证脚本。
- 插件 payload 示例必须优先读取标准事件信封字段，例如 `payload["message"]["text"]`、`payload["chat"]["id"]`。
- `notice` 只允许作为废弃值、测试值或迁移提示出现，不允许作为可执行通道出现。

### 19.7 最终版开发者验收剧本

最终版必须让开发者按以下顺序完成一个新插件，而不是靠多次试错缝补：

1. 阅读插件开发指南，理解 TelePilot 是个人可信插件系统，平台提供事件、消息操作、Trace 和风险提示。
2. 新建 `plugin.json`，填写 `key`、`name`、`version`、`usage`、`event_subscriptions`、`capabilities`。
3. 写一个 `on_event` 或新版主入口，只读取标准事件信封字段。
4. 用 `ctx.messages.send_text` 或 action 返回普通互动消息。
5. 需要按钮时使用 `answer_callback`，需要 Inline 时使用 `answer_inline_query`。
6. 需要严格风控时声明 `telegram_native_raw`，并在代码里处理 `native_raw_meta.enabled=false` 的降级情况。
7. 需要付款/发奖时返回 settlement/userbot 动作，不让 `interaction_bot` 承担转账。
8. 运行验证脚本，通过 manifest lint 和示例 dry-run。
9. 在 WebUI 安装/启用插件，能看到使用说明、订阅、能力、风险提示。
10. 触发插件后在日志页按 trace、message、plugin、action 任一路径排障。

只要开发者仍需要先理解旧交互规则、旧 notice 通道、旧平铺 payload、旧 runtime log 才能写出可用插件，就说明最终版没有达标。

### 19.8 最终版用户验收剧本

给账号主人验收时，必须按真实使用场景跑完：

1. 打开系统设置，确认 Trace 保留、payload snapshot、native_raw 持久化默认值合理。
2. 打开插件中心，确认每个插件都有使用说明、订阅事件、能力声明和风险提示。
3. 刷新远程插件仓库，确认新字段不丢失；执行一键更新前看到版本变化和风险变化。
4. 打开交互中心，选择账号和交互 Bot，确认规则列表、规则详情、Event Bus 映射、最近错误可见。
5. 在允许群发一条普通消息，日志页能按 chat_id/message_id 查到 trace。
6. 发一条不会触发插件的消息，日志页能看到 skipped reason。
7. 用管理员命令启动插件，日志页能看到 command 链路和 userbot_reply 动作。
8. 用玩家关键词启动插件，日志页能看到 interaction_bot 动作。
9. 点击按钮，日志页能看到 callback_query 和 answer_callback。
10. 发送 Inline Query，日志页能看到 inline_query 和 answer_inline_query。
11. 触发转账通知解析，日志页能看到 external payment notice、付款归属和 settlement/userbot 动作。
12. 故意触发一个失败动作，日志页能看到失败原因、reason_code 和中文说明。

这些场景全部通过后，才可以向用户称为“最终版框架落地”。

### 19.9 Go / No-Go 清单

发布前必须逐项回答 `是`。任一项为 `否`，不得发布最终版。

| 检查项 | Go 标准 |
| --- | --- |
| 数据迁移 | 空库和旧库都能升级，回滚不需要删 Trace 表 |
| Trace | 五条真实链路都有 trace/span/action/reason_code |
| Event Bus | 所有 Telegram 来源先标准化，再通过订阅匹配 |
| 插件协议 | 新插件主路径只读标准事件信封，不依赖旧平铺 payload |
| MessageOps | 插件所有动作经 Delivery Executor，成功/失败都记录 |
| Contract Guard | 越声明调用告警，不支持/废弃能力失败 |
| native_raw | 只有声明插件拿到，默认不持久化完整原生数据 |
| Inline | inline_query/chosen_inline_result/answer_inline_query 可测可查 |
| notice 收口 | 旧 notice/bbot_notice 不执行，只给迁移错误 |
| 远程插件 | 安装、刷新、升级、一键更新都不丢失新字段 |
| 日志页 | 默认入口能查消息、插件、命令、动作、原始日志 |
| 交互中心 | 能配置规则，也能看规则映射到 Event Bus 的结果 |
| 插件 UI | 使用说明缺失、native_raw、inline_all、废弃通道都有显式提示 |
| 文档 | 新指南能直接开发 message/callback/inline/payment 插件 |
| 自动验证 | 第 18.2 节命令全部通过，或环境失败有可接受替代证据 |
| 人工验收 | 第 19.8 节场景全部跑完 |
| 发布材料 | 四处版本号同步，中文 CHANGELOG，中文 commit/PR |
| 部署 | 远端 commit、版本、迁移、健康检查、关键 trace 均确认 |

### 19.10 为什么按本计划可以到“最终版”

按本计划执行后，TelePilot 的插件系统会从“旧规则 + 局部交互 Bot + 难排查文本日志”收敛成一套稳定框架：

- 插件不再需要关心消息来自 UserBot、交互 Bot、按钮、Inline 还是付款通知，统一读取事件信封。
- 插件可以自由选择普通互动由交互 Bot 发，管理/结算/转账由 userbot 或 settlement 发，但平台记录实际通道和失败原因。
- 平台不再用强沙箱假装替个人用户承担插件风险，而是把风险声明、越声明调用、高风险能力和失败动作可视化。
- 日志页不再只是文本流，而是围绕 trace 回答“消息到哪了、插件为什么没启动、动作为什么失败”。
- 文档和示例不再教旧机制，新开发者可以直接按 Event Bus + MessageOps + Trace 写插件。

因此，“最终版”的核心不是封装 Telegram 所有能力，而是完成统一事件入口、统一插件协议、统一消息操作出口、统一排障视角和统一开发文档这五个闭环。

### 19.11 当前分支审查补充 blocker 对照表

后端只读审查确认：当前分支已经具备 Trace、Event Bus、native_raw、Inline、远程字段贯通的一部分可测实现，但仍不能称为最终版。以下问题必须在执行中逐项关闭，不能被归类为 UI 小修或后续优化。

| blocker | 当前风险 | 必须关闭到什么程度 | 对应任务卡 |
| --- | --- | --- | --- |
| Source Adapter 未成为运行时唯一入口 | `normalize_bot_update` / `normalize_userbot_event` / `normalize_payment_notice` 仍像 helper，实际运行时还有 `_extract_incoming()`、`_incoming_trace_payload()` 等旧入口 | UserBot、交互 Bot、callback、inline、payment 都必须先生成 `TelePilotEvent`，旧 helper 只能作为兼容适配层 | T39-1、T39-2、T40-1 |
| UserBot command 没有 Trace/Event Bus decision | 管理员命令仍直接执行 builtin/template/plugin handler，日志页无法回答命令调用了什么 | 命令解析、权限、命中处理器、插件调用、消息动作都要写 trace/span/action/reason_code | T38-4、T39-2 |
| 管理 Account Bot 入口缺 Trace，allowed_updates 缺 inline | 管理 Bot 的 message/callback 入口和交互 Bot 的入口不一致，inline 能力覆盖不完整 | 管理入口也要有 receive/normalize/route trace；需要 inline 时 allowed_updates 与交互 Bot 口径一致 | T38-3、T39-4 |
| 外部转账通知和旧 rule/session fallback 绕过 Event Bus | `_try_handle_transfer_notice()`、旧 rule/session/math fallback 仍可能在 Event Bus 前截走事件 | 外部付款通知和旧规则都要转成 Event Bus decision；旧规则只能是订阅条件来源 | T39-1、T39-2、T40-1 |
| 回滚开关未完整接入运行时 | `trace_enabled`、`event_bus_delivery_enabled`、`inline_updates_enabled` 若只是设置字段，部署回滚不可控 | 开关必须实际控制 Trace 写入、新投递路径和 inline updates；关闭后旧路径仍可用 | T38-7、T39-4、T40-5 |
| 并非所有 action 都写 `event_action` | 空文本、非法 media 等直接 return 时没有失败 action，日志页会断链 | 插件返回的每个 action 都要记录成功、失败或跳过；失败不得静默 | T38-3、T39-5 |
| 插件加载失败未进入 `PluginRuntimeStatus` | loader startup 失败只写旧状态和 runtime log，新日志插件诊断页读不到 | 插件加载、热更新、启动失败必须更新 `PluginRuntimeStatus` 并关联最近 trace 或 reason_code | T38-3、T38-5、T40-2 |
| `native_raw_persist_enabled` 未实际持久化完整 raw | 设置存在但 `_native_raw_meta.stored_in_trace` 始终为 false，设置语义不完整 | 默认不持久化；开启后按短保留期保存、脱敏/大小记录、清理任务可验证 | T38-7、T39-3 |
| Event Bus 字段枚举漂移 | lint 允许的事件值与 Event Bus `VALID_EVENT_TYPES` 不一致时，插件可通过规范检查却永远不匹配 | manifest lint、Event Bus matcher、文档、前端提示必须共用同一事件类型枚举 | T39-2、T39-7、T40-4 |
| Trace 写入失败不可见 | Trace best-effort 失败只 debug，线上会表现为日志缺失但无排障入口 | Trace 服务故障要写旧 runtime system/error，同时不阻断插件主流程 | T38-2、T38-7 |

针对这些 blocker，最终版执行必须新增或补齐以下验证：

- UserBot command creates trace。
- 管理 Bot `_handle_update` creates trace。
- `event_bus_delivery_enabled=false` 时新投递路径停用且旧规则不回归。
- `inline_updates_enabled=false` 时 inline updates 不进入处理，但 message/callback 不受影响。
- 空文本、非法 media、缺 inline_query_id 等动作都会产生 failed `event_action`。
- 插件启动/加载失败会更新 `PluginRuntimeStatus`，日志页插件诊断可见。
- manifest lint 允许的事件类型与 Event Bus matcher 的事件类型完全一致。
- Trace 写库失败会落旧 runtime system/error。

### 19.12 最终版证据台账

最终版发布前必须新增或更新 `docs/release/0.40.0-final-evidence.md`。这不是普通总结文档，而是 Go / No-Go 的证据台账；没有这份台账，不能称为最终版完成。

证据台账必须包含：

| 区块 | 必填内容 | 不合格表现 |
| --- | --- | --- |
| 分支和版本 | 本地分支、远端分支、commit、四处版本号、CHANGELOG 版本段 | 只写“已更新版本” |
| 变更范围 | 本次实际改动文件分组、公共契约变化、兼容层位置 | 按计划愿景写，和 diff 不对应 |
| 五条链路 | 普通消息、管理员命令、callback、inline、payment 的 trace/span/action 证据 | 只说“测试通过”，没有 trace 路径 |
| blocker 关闭 | 第 19.11 节每个 blocker 的关闭方式、测试名和剩余风险 | 把 blocker 降级成后续优化 |
| 自动验证 | 第 18.2 节每条命令、退出码、失败原因或替代证据 | 环境失败但写成通过 |
| 人工验收 | 第 19.8 节每个场景的页面 URL、账号/插件、观察结果 | 只验收桌面，不验收窄屏/PWA |
| 文档审计 | 第 19.6 节 grep 结果分类：合法迁移、历史说明、测试覆盖、已修复 | 删除命中但没有解释 |
| 部署证据 | 远端 commit、版本号、迁移、健康检查、关键 trace、docker 日志 | 只写“已部署” |
| 回滚证据 | 备份路径、关闭开关演练结果、旧日志可用性 | 没有备份或没有验证旧路径 |

台账中的每条“通过”都必须能回到具体命令、测试、页面或 trace。若某项只能人工观察，必须写清观察入口和通过标准。若某项暂时无法验证，最终版状态只能是 `可测`，不能发布。

### 19.13 稳定 reason_code 与状态字典

日志页要能排障，必须先让后端输出稳定 reason_code。最终版禁止用临时英文句子当唯一失败原因；中文说明可以改，reason_code 不能随意改名。

事件和投递状态第一版固定为：

| 状态 | 含义 | 日志页展示要求 |
| --- | --- | --- |
| `received` | 已收到 Telegram update 或 UserBot event | 展示来源、账号、update/message/callback/inline ID |
| `normalized` | 已转为 `TelePilotEvent` | 展示事件类型和标准字段摘要 |
| `matched` | 至少一个插件订阅命中 | 展示插件、entry、dispatch_mode |
| `skipped` | 候选插件跳过 | 展示 reason_code 和可读原因 |
| `delivered` | 已投递给插件运行时 | 展示插件入口和耗时 |
| `plugin_succeeded` | 插件运行成功 | 展示返回 action 数量 |
| `plugin_failed` | 插件运行失败 | 展示异常摘要和插件 runtime 状态 |
| `action_succeeded` | 动作执行成功 | 展示请求通道、实际通道、Telegram 返回 ID |
| `action_failed` | 动作执行失败 | 展示失败原因、是否可重试 |
| `trace_degraded` | Trace 写入降级 | 展示旧 runtime_log 的 fallback 记录 |

reason_code 第一版必须至少覆盖：

| reason_code | 类别 | 触发场景 |
| --- | --- | --- |
| `account_not_matched` | 订阅匹配 | 事件账号与插件/规则账号不一致 |
| `plugin_not_installed` | 插件状态 | 插件不存在或安装记录缺失 |
| `plugin_disabled` | 插件状态 | 插件未启用 |
| `manifest_invalid` | 插件状态 | manifest 解析失败或缺必要字段 |
| `plugin_load_failed` | 插件状态 | loader 加载失败 |
| `event_type_not_subscribed` | 订阅匹配 | 插件未订阅该事件类型 |
| `source_not_subscribed` | 订阅匹配 | 插件未订阅该来源 |
| `scope_not_matched` | 订阅匹配 | 允许会话、owner_only、known_users、inline_all 不匹配 |
| `filter_not_matched` | 订阅匹配 | 关键词、命令、callback data、金额等不匹配 |
| `session_not_found` | 会话 | 需要已有会话但未找到 |
| `session_expired` | 会话 | 会话过期或已结束 |
| `rate_limited` | 频控 | 命中账号、插件、用户或 inline 频控 |
| `command_unauthorized` | 权限 | 非管理员触发管理员命令 |
| `inline_disabled` | Inline | 系统或账号关闭 inline updates |
| `native_raw_not_allowed` | 能力 | 插件未声明 `telegram_native_raw` |
| `native_raw_skipped` | 能力 | 已声明但来源、大小或设置导致未下发 |
| `send_channel_deprecated` | 动作 | 请求 `notice` / `bbot_notice` / `notice_bot` |
| `bot_not_configured` | 动作 | 需要交互 Bot 但未配置 token 或未启用 |
| `userbot_offline` | 动作 | 需要 userbot 但账号离线 |
| `settlement_requires_userbot` | 动作 | 普通 Bot 请求转账/发奖能力 |
| `telegram_api_error` | 动作 | Telegram API 返回失败 |
| `plugin_runtime_error` | 插件运行 | 插件执行抛错 |
| `trace_write_failed` | 日志 | Trace 写库失败，已 fallback 到 runtime_log |

新增 reason_code 必须同时更新：后端常量/测试、前端中文映射、开发文档排障表、最终证据台账。否则视为日志契约漂移。

### 19.14 全量消息开放的真实边界

“传递所有消息给插件”在最终版里的含义必须精确定义，避免重新落回强沙箱或完全无边界两端：

| 层级 | 默认是否给插件 | 内容 | 边界 |
| --- | --- | --- | --- |
| 标准事件信封 | 给订阅命中的插件 | `source`、`message`、`chat`、`sender`、`actor`、`reply_to`、`payment`、`inline_query`、`session`、`trigger` | 不含凭据、不含 live client、不含 Bot token |
| `raw_summary` | 默认给 | 脱敏摘要、实体摘要、原始来源类型、大小 | 只用于排障，不作为推荐业务主协议 |
| `native_raw_meta` | 默认给 | 是否可用、是否下发、大小、来源、是否持久化 | 不包含完整原文对象 |
| `native_raw` | 仅声明能力后给 | Telegram 原生 dict 兼容结构 | 必须声明 `capabilities.telegram_native_raw.enabled=true`，且不得包含凭据或 live object |
| Trace payload snapshot | 默认按设置保存脱敏快照 | 标准事件和动作摘要 | 默认不保存完整 `native_raw` |

最终版平台不再替个人用户判断“某个插件业务上应不应该看这条消息”，但仍必须执行四条客观边界：

- 不下发账号 session、Bot token、API key、私钥、数据库连接串等凭据。
- 不下发 Telethon event/client、Bot API client、HTTP session 等 live object。
- 不把普通 Bot 包装成有转账能力的主体；转账/发奖必须走 userbot 或 settlement。
- 不恢复旧 `notice` / `bbot_notice` / `notice_bot` 主动发送通道。

这四条是最终版的底线，不属于“强沙箱”残留，也不能由插件声明绕过。

### 19.15 插件生态迁移边界

最终版必须重新登记插件身份，避免“平台功能、官方插件、远程插件、示例插件”混在一起：

| 类型 | 定义 | 最终版处理 |
| --- | --- | --- |
| 平台功能 | 系统运行所需或明显不是插件的能力，例如定时任务框架、日志、账号管理、插件仓库管理 | 不再伪装成普通插件；在系统或平台设置中展示 |
| 官方可选插件 | TelePilot 维护，但不是系统必需，例如自动回复、自动复读 | 首次部署或升级后可提示安装；安装后可手动移除；manifest 必须完整声明 `usage`、`event_subscriptions`、`capabilities` |
| 官方远程插件 | 由官方仓库维护、按需安装的能力，例如图片生成、游戏玩法、算数题等 | 从远程插件库安装/更新；仓库刷新和一键更新必须保留新字段和风险提示 |
| 示例插件 | 用于开发者学习和验证的插件，例如 event bus demo | 不默认启用；必须能通过验证脚本；文档示例从这里抽取 |
| 用户安装插件 | 用户从私有库或第三方库安装的插件 | 不强制迁移代码，但安装/启用/更新时必须显示规范警告、风险提示和废弃通道错误 |

官方可选插件和官方远程插件不允许为了通过 lint 写空声明。每个插件必须说明：

- 谁能触发：管理员命令、玩家关键词、callback、inline、payment。
- 收到什么事件：`event_subscriptions`。
- 用什么能力：`capabilities`。
- 普通互动默认由谁发送：`interaction_bot`、`userbot_reply` 或 `auto`。
- 付款、发奖、结算是否需要 userbot。
- 开发者如何在日志页排查它为什么没启动。

如果某个历史内置插件暂时不能迁移到新模型，它必须被降级为“待迁移官方远程插件”，不能继续作为最终版内置主路径发布。

### 19.16 前端最终版页面合同

最终版不是后端链路能跑就结束。以下页面必须能用真实 API 或固定 fixture 验收，且桌面和窄屏/PWA 都要可用：

| 页面 | 必须回答的问题 | 验收重点 |
| --- | --- | --- |
| 日志中心 | 系统是否健康、消息走到哪、插件卡在哪、动作为什么失败 | 总览、时间线、详情、错误态、原始日志、reason_code 中文说明 |
| 交互中心 | 当前账号/交互 Bot 有哪些规则，规则如何映射 Event Bus | 顶部账号/Bot 选择、规则列表、规则详情、订阅映射、最近错误 |
| 插件中心 | 插件有什么使用说明、订阅事件、能力风险、规范警告 | 缺 usage 红色高级警告、native_raw/inline_all 风险、一键更新风险预览 |
| 插件配置页 | 开发者自定义配置是否在容器框架内可用 | 使用说明、总开关、配置区、预览区顺序固定；无默认说明兜底 |
| 系统设置 | 运维开关是否清楚，是否能支持回滚 | Trace/Event Bus/Inline/payload/native_raw 设置和默认值 |

前端验收不能只跑 typecheck/build。凡是改到 UI，最终证据台账必须写明：

- 验收 URL。
- 桌面视口结果。
- 窄屏/PWA 结果。
- 长中文、长插件名、长错误消息是否撑破布局。
- loading、empty、error、disabled、success 状态是否可见。

### 19.17 部署和回滚演练最低要求

部署到 `144.24.5.159` 前，最终版必须先证明“出问题时能退”：

1. 备份 `.env`、compose 文件和数据库，记录绝对路径和时间。
2. 记录部署前远端 commit、版本号和容器状态。
3. 部署后执行迁移，确认新 Trace 表存在且旧 `runtime_log` / `audit_log` 仍可读。
4. 触发至少一条普通消息 trace 和一条插件 action trace。
5. 将 `event_bus_delivery_enabled=false` 演练一次，确认旧规则路径仍能处理已有交互规则。
6. 将 `inline_updates_enabled=false` 演练一次，确认普通 message/callback 不受影响。
7. 将 `trace_enabled=false` 演练一次，确认旧 runtime/audit 仍有排障记录。
8. 恢复最终默认开关，再确认日志页和插件运行正常。

如果第 5-7 步因为线上不适合直接演练，必须在同版本本地或临时环境完成，并在证据台账中写明为什么线上跳过、替代环境是什么、剩余风险是什么。

### 19.18 最终版执行启动条件

当用户要求“按计划执行”时，主 Agent 不再重新讨论方向，直接按以下顺序启动：

1. 读取 `AGENTS.md`、全局经验文档、`docs/AGENT-PLAYBOOKS.md` 和本计划。
2. 固定分支、版本、工作树和当前 diff。
3. 建立第 19.12 节证据台账骨架。
4. 按 Wave 0-3 分配子 Agent；每个子 Agent 必须带写入范围、禁区、验证命令和交付格式。
5. 主 Agent 只在契约漂移、数据迁移、部署发布、跨 Agent 冲突和 blocker 无法关闭时重新决策。
6. 全部 Go / No-Go 为 `是` 后，才更新版本、写中文 CHANGELOG、commit、push、部署。

按这套启动条件执行后，计划不再依赖“记得检查一下”的口头约束，而是每一步都有证据、门禁、回滚和最终判定。

### 19.19 最终版状态控制板

最终版执行期间，主 Agent 必须维护一个状态控制板，并同步写入 `docs/release/0.40.0-final-evidence.md`。控制板不是进度汇报，而是 Go / No-Go 的事实来源。

控制板固定包含以下行，状态只允许使用第 0.7 节的四种状态：

| 控制项 | 对应证据 | 不得标为已完成的情况 |
| --- | --- | --- |
| 数据层和迁移 | Alembic、模型导入、索引、保留策略测试 | 只在开发库迁移过，没有空库/旧库验证 |
| 五条事件链路 | 普通消息、命令、callback、inline、payment 的 trace/span/action | 只有单元测试，没有日志页或 API 详情证据 |
| 插件契约 | manifest、Event Bus、MessageOps、Contract Guard、reason_code | 后端可用但前端类型、文档或验证脚本未同步 |
| 官方和示例插件 | 官方插件 manifest、示例插件、验证脚本、dry-run | 只是补了字段，但插件主路径仍读旧 payload |
| 远程插件仓库 | 私有库、`tree/<branch>`、刷新、一键更新、字段保真 | 安装可用但升级前不能看到风险变化 |
| 日志中心 | Trace API、时间线、插件诊断、动作详情、原始日志 | typecheck/build 通过但未做桌面和窄屏验收 |
| 交互中心 | 账号/Bot 选择、规则列表、规则详情、Event Bus 映射 | 仍要求钻到账号详情页才能理解或配置规则 |
| 插件 UI | usage、配置容器、自定义样式、预览建议、规范警告 | 缺 usage 没有红色高级警告 |
| 设置和回滚 | `trace_enabled`、`event_bus_delivery_enabled`、`inline_updates_enabled`、保留期 | 设置字段存在但没有影响运行时 |
| 开发文档 | API Reference、Cheatsheet、Remote、Safety、README | 旧机制仍作为推荐路径出现 |
| 发布材料 | 版本四处同步、中文 CHANGELOG、中文 commit/PR | 根据计划写发布说明，未按实际 diff 写 |
| 服务器部署 | 备份、远端 commit、迁移、健康检查、关键 trace、docker logs | 只写“已部署”，没有可复核命令和结果 |

主 Agent 每次合并子 Agent 工作后必须更新控制板。任何一行仍为 `未开始`、`半落地` 或 `可测` 时，最终版状态必须保持为 `可测`，不得向用户表达“最终版已完成”。

### 19.20 端到端验收数据包

最终版必须有一套可以重复运行的端到端验收数据包，用来证明框架不是只在真实 Telegram 环境里靠手感可用。

建议固定为以下结构；如实际目录不同，证据台账必须写清映射：

```text
backend/app/tests/fixtures/event_bus/
  userbot_message.json
  interaction_bot_message.json
  callback_query.json
  inline_query.json
  chosen_inline_result.json
  external_payment_notice.json
  native_raw_telethon_message.json
  deprecated_notice_action.json

examples/plugins/event_bus_demo/
  plugin.json
  manifest.py
  plugin.py
  fixtures/
```

这些 fixture 必须支持以下自动化断言：

- 标准化后 `TelePilotEvent` 字段完整，且不包含 token、session、live client。
- `event_subscriptions` 匹配结果包含 matched、skipped、delivered 和稳定 reason_code。
- 未声明 `telegram_native_raw` 的插件没有 `native_raw`；声明插件拿到 JSON 兼容 dict。
- `answer_callback`、`answer_inline_query`、`send_message`、`settlement` 都能进入 `event_action`。
- 旧 `notice` / `bbot_notice` / `notice_bot` 只能产生 `send_channel_deprecated`，不能被自动改写为可执行通道。
- 付款确认事件能区分 `source_actor`、`player`、`payment`、`reply_to`，普通 Bot 不执行转账。

最终验收命令至少包括：

```bash
cd backend && .venv/bin/pytest app/tests/test_event_bus.py app/tests/test_event_trace.py app/tests/test_account_bot.py app/tests/test_worker_command.py -q
backend/.venv/bin/python scripts/validate-plugin-examples.py
backend/.venv/bin/python scripts/validate-installed-interaction-plugins.py
```

如果全量 `pytest -q` 已覆盖这些用例，证据台账仍必须列出具体测试名，避免未来测试被删除后只剩一个笼统命令。

### 19.21 数据库、设置和迁移闭环

最终版涉及新增表、设置项和运行时开关，必须把迁移和升级体验当作正式交付内容。

数据库闭环必须证明：

- `alembic upgrade head` 可在空库执行。
- `alembic upgrade head` 可在现有数据目录执行。
- 新表存在，索引存在，旧 `runtime_log` / `audit_log` 仍可读。
- Trace 写入失败不会回滚业务事务。
- 清理任务能处理过期 `payload_snapshot` 和持久化 `native_raw`，并保留 `event_trace` 主记录。
- 紧急回滚时不要求执行删表 downgrade；新表可留存不用。

设置闭环必须证明：

- `trace_enabled=false`：不写新 Trace，但旧 runtime/audit 仍记录关键错误。
- `event_bus_delivery_enabled=false`：新 Event Bus 投递停用，旧规则路径不回归。
- `inline_updates_enabled=false`：inline updates 不处理，message/callback 不受影响。
- `native_raw_persist_enabled=false`：插件仍可在声明后收到 `native_raw`，但日志库不持久化完整原生数据。
- `native_raw_retention_days=1`：开启持久化后，一天外完整原生数据被清理或标记过期。

这些开关是运维护栏，不是产品双模式。前端系统设置可以展示它们，但文案必须避免让用户以为 TelePilot 有“标准模式 / 个人模式”两套框架。

### 19.22 运行时稳态和性能边界

全量消息开放会放大消息量，最终版必须定义稳态边界，避免日志系统和插件调度拖垮主流程。

运行时必须满足：

- Trace 写库使用 best-effort；失败写旧 runtime system/error，不阻断 Telegram 消息处理。
- Event Bus 订阅匹配先按 account、source、event_type、scope 做粗过滤，再进入 filters 和插件调用。
- 对明显不相关插件写聚合 skipped span，不为每条消息给所有插件逐一落库。
- `payload_snapshot`、`raw_summary`、`native_raw_meta` 都有大小上限和截断标记。
- 单 trace 的 span/action 数量超过软上限时，继续执行业务并记录 `trace_span_limit_reached`。
- Inline Query、callback、公共关键词触发必须经过频控；频控命中写 `rate_limited`。
- 插件运行异常只影响当前插件调用，不应中断同一事件下其他已匹配插件的处理，除非插件入口显式声明独占会话。

最终 review 必须抽查代码里是否存在绕过这些边界的路径，尤其是直接循环所有插件、直接保存完整 raw、Trace 异常向外抛、动作失败不落库这四类问题。

### 19.23 前端实测脚本和验收入口

最终版前端验收必须绑定具体 URL，不允许只说“页面看过”。本地或远端至少检查：

| 页面 | URL 示例 | 必测状态 |
| --- | --- | --- |
| 首页/版本 | `/` | 版本号、导航、移动端底部栏 |
| 日志中心 | `/logs` | 总览、筛选、详情时间线、错误态、原始日志 |
| 插件中心 | `/plugins` | usage 缺失警告、订阅/能力展示、长插件名 |
| 插件仓库 | `/plugins/manage?tab=plugins` | 刷新、私有库、`tree/<branch>`、一键更新预览 |
| 插件配置 | `/accounts/1/features/<plugin_key>?from=plugins` | 使用说明、总开关、配置容器、预览建议 |
| 交互中心 | `/interaction?aid=1` | 账号/Bot 选择、规则列表、规则详情、Event Bus 映射 |
| 系统设置 | `/settings` | Trace、Event Bus、Inline、payload、native_raw 设置 |

每个页面至少验收两种视口：

- 桌面宽屏：`1440x900` 或当前浏览器宽屏。
- 窄屏/PWA：`390x844` 或接近手机宽度。

验收重点：

- 底部导航不能换行、重叠或遮挡主按钮。
- 固定按钮必须固定在网页显示区域内，而不是跟随内容滚动到不可见位置。
- 长中文、长英文、长插件名、长错误消息不能撑破容器。
- loading、empty、error、disabled、success 状态都有可读文案。
- 高级 JSON、native_raw、风险提示默认折叠，展开前有明确提示。

### 19.24 文档最终同步包

最终版文档必须按读者路径重组，而不是把所有历史设计堆在一起。

发布前每份文档的定位如下：

| 文档 | 最终定位 | 必须包含 | 不应包含 |
| --- | --- | --- | --- |
| `README.md` | 用户安装、部署、入口导航 | 一键部署、docker compose、WebUI 配置、插件仓库、交互中心、日志中心 | 旧交互 Bot 内部机制长篇说明 |
| `docs/PLUGIN-API-REFERENCE.md` | 插件开发主文档 | 标准事件信封、manifest、MessageOps、Trace 排障、示例 | 旧平铺 payload 推荐写法 |
| `docs/PLUGIN-CHEATSHEET.md` | 快速抄写手册 | 最小插件、常用 action、reason_code 快查 | 过时 hook 或 `event.reply` 主路径 |
| `docs/PLUGIN-REMOTE.md` | 远程插件发布和仓库维护 | 私有 GitHub、`tree/<branch>`、usage、订阅、能力、一键更新 | 旧字段裁剪说明 |
| `docs/PLUGIN-SAFETY.md` | 个人可信插件风险说明 | 风险自担、平台提醒、客观边界、审计、回滚 | 平台强沙箱会替用户兜底的暗示 |
| `docs/INTERACTION-BOT-OPTIMIZATION.md` | 历史/架构说明 | 若保留，必须标注历史背景或更新到最终框架 | 与最终 Event Bus 文档冲突的主路径 |

发布前必须运行文档审计命令，并把旧概念命中逐条归类。允许保留旧概念，但只允许出现在三种上下文：

- 废弃值说明，例如 `notice` / `bbot_notice` / `notice_bot`。
- 迁移说明，例如旧平铺 payload 如何迁移到标准事件信封。
- 历史设计说明，并明确不再是当前开发主路径。

### 19.25 残余风险允许范围

最终版可以带少量残余风险上线，但这些风险必须被明确归类，不能伪装成完成。

允许作为残余风险的情况：

- 用户安装的第三方插件仍缺 `usage` 或仍使用旧 `interaction_entries`，但 WebUI 和验证脚本已给出规范警告，平台维护插件已迁移。
- 线上不适合直接演练某个回滚开关，但已在同版本本地或临时环境演练，并记录原因和剩余风险。
- 某些历史设计文档保留旧概念，但已标注历史背景，不会被开发指南引用为主路径。
- 某个 Telegram 罕见 update 类型暂不支持，但不影响 message、command、callback、inline、payment 五条主链路。

不允许作为残余风险的情况：

- 官方/示例插件仍依赖旧平铺 payload 主路径。
- 旧 `notice` / `bbot_notice` / `notice_bot` 还能发送消息。
- 未声明插件能拿到完整 `native_raw`。
- 插件动作失败没有 `event_action`。
- 日志页无法解释插件为什么没启动。
- 远程插件安装或升级会丢失 `usage`、`event_subscriptions`、`capabilities`。
- 版本、CHANGELOG、远端部署和实际 diff 不一致。

最终报告必须单独列出残余风险。若残余风险属于“不允许”类别，发布结论只能是 No-Go。

### 19.26 最终版宣称边界

计划全部执行后，可以向用户和插件开发者宣称：

- TelePilot 已形成统一 Telegram Event Bus、Trace 日志、MessageOps 和个人可信插件风险提示框架。
- 插件可以用统一事件信封处理 UserBot 消息、交互 Bot 消息、命令、callback、inline、payment。
- 插件可以自由选择普通互动发送通道，转账/发奖等能力仍由 userbot 或 settlement 承接。
- 日志中心可以从消息、插件、命令、动作四个入口排查链路。
- 新插件开发应以 `event_subscriptions`、标准 payload、`ctx.messages`、Trace 排障为主路径。

不能宣称：

- TelePilot 封装了 Telegram 的所有 update 类型和所有 Bot API 方法。
- 平台会替用户审查远程插件是否安全可信。
- 普通 Bot 具备转账能力。
- 旧插件无需修改即可自动符合最终版最佳实践。
- 关闭 Trace 后仍能获得同等详细的链路日志。

这条宣称边界必须同步到最终 CHANGELOG 和插件开发指南，避免外部读者把“最终版框架落地”理解成“所有 Telegram 能力和所有旧插件都已完美覆盖”。
