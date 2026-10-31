# Plugin API 1.0 冻结计划（草案）

> **状态：草案，未冻结，不删除任何东西。**
>
> - 2026-10-31 项目负责人决定：**兼容层暂时全部保留**，不删除、不标记“即将删除”。本文只做盘点和准备。
> - 本文回答三个问题：现在对插件开放了哪些接口（第 2 节）；为旧插件保留了哪些兼容层、各自为什么还在（第 3 节）；离“Plugin API 1.0 冻结”还差什么（第 5 节）。
> - 已经生效的正式承诺仍以 [PLUGIN-CONTRACT.md](PLUGIN-CONTRACT.md)（稳定契约 1.0）为准；本文不改变那份承诺，也不抬任何插件的 `min_telepilot_version`。
> - 兼容层清单的机器可读版本与守护测试：`backend/app/tests/test_plugin_compat_layer.py`（`COMPAT_LAYER`，与第 3 节编号一一对应，增删兼容层必须同时改两边，CI 会检查）。

## 1. 名词先说清楚

| 叫法 | 是什么 | 现在的值 |
| --- | --- | --- |
| Plugin API 版本 | 宿主给插件的接口版本，插件 `plugin.json` 用 `plugin_api` 范围声明，`ctx.capabilities.plugin_api` 可读 | **1.7**（0.115.0 起；1.6 → 1.7 只新增了契约版本探测） |
| 插件稳定契约版本 | 宿主“只增不减”的正式承诺，见 PLUGIN-CONTRACT.md | **1.0**（冻结基线 = Plugin API 1.6 的接口面） |
| “Plugin API 1.0 冻结” | 路线图 P5 的目标：把对外接口的**范围**定死、写清每项的稳定级别、对兼容层做出去留决定、以此作为插件市场的长期基础 | 本文是它的准备稿 |

为什么不把 Plugin API 版本“改回 1.0”：已发布插件写的是 `"^1.0"`、`">=1.3"`、`"^1.4"` 这类范围，改回 1.0 会让声明 `>=1.3` 的插件被误判“宿主太旧”。所以“1.0 冻结”用**契约版本号**表达，Plugin API 版本继续单调递增（详见 PLUGIN-CONTRACT.md 第 1 节）。

### 1.1 插件怎么知道宿主的契约版本（0.115.0 / Plugin API 1.7 起）

```python
caps = getattr(ctx, "capabilities", None)                       # 0.107 之前没有 ctx.capabilities
contract = getattr(caps, "contract_version", None) if caps else None  # 0.115 之前没有这个属性
if contract is None:
    ...  # 旧宿主：按“契约之前”处理，继续用 caps.has(...) / 旧属性探测
```

- 运行时：`ctx.capabilities.contract_version`（字符串，当前 `"1.0"`）；能力名 `runtime.contract_version` 可用 `caps.has(...)` 探测。
- HTTP：`GET /api/plugins/capabilities` 新增 `contract_version`、`contract_baseline_plugin_api` 两个字段；旧服务器不返回，客户端按缺省处理。
- 只读字段，不改变任何已有行为；旧插件不读它也完全不受影响。

## 2. 当前对外接口盘点（Plugin API 1.7）

数字取自 `schemas/plugin-api-contract.json`（运行时内省生成）。“快照”列表示该部分是否已被契约快照逐项记录、受 CI 破坏检测保护。

### 2.1 已在契约快照里的部分

| 部分 | 内容 | 数量 | 快照 |
| --- | --- | --- | --- |
| 钩子 | `on_startup` `on_shutdown` `on_message` `on_message_edited` `on_direct_message` `on_command` `on_interaction` `on_event` `on_config_action` `on_config_validate` `on_config_saved` `on_ui_card` `on_ui_action`；模块函数 `dry_run_rule` `parse_payment_notice`；中间件处理函数 | 16 | ✅ |
| `PluginContext` | 字段 24 个（`account_id` … `command`）+ `capabilities` / `conversation()` / `reply()` | 27 | ✅ |
| `ctx.messages` | buffered 17 个方法 / live 20 个 / entry 21 个；`stream_editor()` 7 个 | — | ✅ |
| 其它 facade | `ctx.ai` 7、`ctx.http` 4、`ctx.storage` 8（只读版 9）、`ctx.skills` 4、`ctx.scheduler` 4、`ctx.host` 4、`ctx.identities` 3、`ctx.capabilities` 7（含新增 `contract_version`） | — | ✅ |
| 能力注册表 | 名称、版本、since、plugin_api、旧属性名、适用 facade | 46（基线 45 + `runtime.contract_version`） | ✅ |
| plugin.json | 安装 Schema 顶层字段 28 个；详细契约 Schema 字段路径 109 个 | — | ✅ |
| 权限 | `send_message` … `message_middleware` | 20 | ✅ |
| 声明式 UI | 控件契约 v3、23 个控件、25 个 schema 键、4 个卡片 slot、5 种布局、5 个 UI 钩子、页面菜单与字段 | — | ✅ |
| 页面 Bridge | 协议 v1：`ready` `theme.get` `config.get` `card.data` `action.invoke`；`window.telepilot` | — | ✅ |
| Event Bus | 事件来源 4、事件类型 14、订阅范围 7、过滤键 11 | — | ✅ |
| 标准动作 | `send_message` … `update_session`（只记录了动作**类型名**） | 20 | ✅ |
| 扩展点 | `provides`（`ai.image_backend`）、`payment_sources`、中间件阶段与时限 | 3 | ✅ |
| 只读宿主资源 | `account.admins@1` `ai.models@1` `chat.allowed@1` `plugin.stats@1` | 4 | ✅ |
| 弃用登记 | 见第 3 节 C02–C06 等 | 6 | ✅ |

### 2.2 插件实际在用、但还没进快照的部分

这些接口今天同样被插件依赖，只是没有被机器逐项冻结，靠文档和各自的测试维持。**“1.0 冻结”要先决定它们进不进冻结范围**（第 5 节第 1 项）。

| 接口 | 现状 | 谁在用 |
| --- | --- | --- |
| 标准动作的**字段**（`send_message` 的 `text` / `reply_markup` / `send_via` …） | 快照只记录动作类型名；字段语义在 PLUGIN-API-REFERENCE.md 与 golden 测试里 | 全部交互插件 |
| Event Bus **payload 信封字段**（`trigger`、`entry_key`、`callback_data`、`session` …） | 文档 + golden 测试 | 全部 `on_event` 插件 |
| `ctx.client`（沙箱包装的 Telethon 客户端） | 字段在快照里，但可调用的 Telethon 方法不在 | 官方库 23 个插件直接用 |
| `manifest.py` 的 `Manifest(...)` 构造参数 | 安装 Schema 覆盖 plugin.json，Python 构造参数（含 `requires_platform_capabilities`）没有单独冻结 | 官方库全部 42 个插件 |
| 异常类型（`AIUnavailableError`、`AICapabilityError`、`AIResponseTruncated` …） | 文档列出，未入快照 | AI 类插件 |
| 远程插件协议、入站 Webhook 信封 | 见 PLUGIN-REMOTE.md、PLUGIN-WEBHOOK-QUICKSTART.md | 远程 / Webhook 插件 |
| 插件市场 `registry.json` 格式、ZIP 包与签名格式 | 见 PLUGIN-REMOTE.md「插件市场与 registry.json」、PLUGIN-SAFETY.md | 插件库、安装流程 |
| `GET /api/plugins/capabilities` 等插件相关 HTTP 接口 | OpenAPI 快照（`openapi/telepilot.openapi.json`）约束，不在插件契约里 | Web / iOS / 开发工具 |

## 3. 兼容层清单（全部保留）

“兼容层”= 宿主为了让**旧插件、旧版本插件或旧数据**在新服务器上照旧运行而保留的代码路径。新插件都不需要它们。

- “谁还依赖”一列：官方插件库 `Anoyou/telebot-plugins` main（`28ae016`）源码粗扫结果；第三方插件与用户手里的旧版本 ZIP 无法统计，所以**任何一项都不能只凭官方库没用到就删**。
- “弃用登记”列：✅ 表示已在弃用登记里，宿主会按插件统计调用次数（插件详情页可见）；— 表示没有计数。
- 守护测试见 `test_plugin_compat_layer.py` 的 `COMPAT_LAYER`。

| 编号 | 兼容层 | 用途（大白话） | 谁还依赖 | 弃用登记 | 为什么现在保留 |
| --- | --- | --- | --- | --- | --- |
| C01 | `ctx.messages` 旧能力属性（`rich_table_compact` `inline_rich_edit` `live_inline_edit` `rich_table_default_style` `guest_reply_markup` `streaming_edit`） | 0.107 之前插件用 `getattr(ctx.messages, "xxx", False)` 判断宿主支不支持某功能；现在由能力注册表自动生成这些属性 | interaction-ai-chat；更早的第三方插件 | — | 零成本（自动生成），删了旧插件会误以为宿主不支持而关掉功能 |
| C02 | `on_interaction(ctx, entry_key, payload)` 迁移桥 | 交互入口的老调用方式；新插件用 `on_event` | **官方库 17 个游戏插件**的规则触发入口仍走它（它们同时实现了 `on_event`，只在事件订阅时走 `on_event`）；只实现 `on_interaction` 的旧插件会计入弃用统计 | ✅ `hook.on_interaction` | 目前仍是 17 个官方插件的实际调用路径，删除等于让这些游戏的规则入口失效 |
| C03 | `min_telebot_version` 旧字段 | 改名前的“最低宿主版本”字段名 | 官方库已全部改用 `min_telepilot_version`；旧 ZIP、远程插件元数据仍可能带 | ✅ `manifest.min_telebot_version` | 删了旧插件不会被拒，反而会失去版本门槛保护 |
| C04 | `ctx.ai.complete / stream_complete` 的 `tag=` / `tags=` | `provider_tag=` 的老名字 | 官方库未发现；第三方未知 | ✅ `ai.tag_alias` | 只是参数别名，保留成本极低 |
| C05 | 动作 `send_via: notice / bbot_notice / notice_bot` | 早已下线的通知通道；现在只返回清楚的失败原因并计数 | 官方库未发现 | ✅ `action.send_via_notice` | 保留的只是“报错说明”，删了旧插件会得到含糊的失败 |
| C06 | 按钮直接写 `callback_data` | 0.108 之前的按钮写法，点击按会话范围投递；新写法 `{"callback": {...}}` + `own_buttons` | 官方库 7 个插件源码含该写法（ai_redpacket、blackjack、dead_revolver、dice_grid_hunt、interaction-ai-chat、quick_qa、ten_half，粗扫） | ✅ `markup.raw_callback_data` | 多个官方游戏仍在用，删了按钮会失灵 |
| C07 | Event Bus payload 里的 `owner_user_ids` / `admin_user_ids` | 0.106 临时下发的管理员名单；新写法 `ctx.host.read("account.admins@1")` | interaction-ai-chat、random_benefit、ten_half | ✅ `payload.admin_user_ids`（字段无法统计读取次数） | 读取次数无法统计，删了会让“仅管理员可点”的按钮判断失效 |
| C08 | 兜底入口表（`app/plugin_compat.py`） | 早期游戏插件没声明“会话范围 / 仅发起人 / 无奖品”，宿主替它们补 | 只有旧版本：game24 < 1.1.18、pt_promote < 1.0.30、blackjack < 1.0.33、math10 < 1.0.15、dice_grid_hunt < 1.1.39、guess_number < 1.0.22（官方库 main 已全部自带声明，声明后表对它不生效） | — | 用户可能还装着旧版本；表只有 7 行，对新版本完全不生效 |
| C09 | 交互规则旧动作 `action="math10"` 与主进程算数题兜底 | 0.2x 时代保存的“算数题”规则，运行时转成通用模块调用 | 用户数据库里的存量规则（不是插件） | — | 是用户数据而非插件代码，要先有迁移脚本改写存量规则 |
| C10 | codex_image 旧模型名改写（`gpt-5.4` → `gpt-5.5`） | 保存配置时把下线的模型名换成新名 | 用户已保存的 codex_image 配置 | — | 用户数据兼容，删了旧配置会调用不存在的模型 |
| C11 | chatgpt_image 多 Token 旧配置 | 旧版本没声明 `x-secret-list`，宿主补一份等价声明以加密保存 Token | chatgpt_image 旧版本（main 已自带 `x-secret-list`，自带后兜底不生效） | — | 删了旧版本的 Token 可能明文显示或丢失 |
| C12 | 规则试运行旧协议 `_dry_run_match` | 0.110 之前插件的试运行函数形状与说明文案 | **codex_image 仍只有旧协议**；auto_reply / autorepeat 旧版本 | — | 当前官方 codex_image 还在用 |
| C13 | `,ai` 图片后端 `image_backend="codex_image"` 与无声明回退 | 旧模板固定走 codex_image；没有插件声明 `ai.image_backend` 时也回退到它 | 旧模板配置；codex_image 旧版本（新版本已声明扩展点） | — | 保证“旧插件 + 新服务器”出图行为与 0.110 一致 |
| C14 | 动作 `compact_tables: true` | `render_profile.table.compact` 的旧写法，平台内部转换，不发给 Telegram | interaction-ai-chat | — | 官方插件在用，转换零成本 |
| C15 | UserBot 旧分发 `on_message`（`message_channels`） | Event Bus 之前的消息分发；Event Bus 没命中时继续交给 `on_message` | **官方库 38 个插件**声明了 `message_channels` | — | 覆盖面最大，是大量插件的主路径，不是“残留” |
| C16 | 直通入口 `on_direct_message` 旧返回值（`True` / `{"consume": ...}`） | 新写法 `{"status": "consumed"}` | ten_half、todo_reminder 等（粗扫） | — | 返回值兼容，保留成本极低 |
| C17 | 已迁出 Core 的可选插件 key（auto_reply、autorepeat、chatgpt_image、codex_image、game24、math10）与旧 `backend/plugins/installed` 目录、旧 `account_feature` 行 | 让旧镜像升级后残留的目录和记录还能被识别 | 从老版本升级上来的部署 | — | 删了老部署升级后插件可能“消失” |
| C18 | 账号级旧 global 配置字段 / `Feature.manifest["global_config"]` 回读 | 新配置表缺行时读旧位置 | 老部署的用户数据 | — | 用户数据兼容，需要先做数据迁移 |
| C19 | 旧版免签名插件（`plugin_allow_legacy_unsigned_plugins`） | 允许签名机制出现之前安装的插件继续加载（新上传默认必须签名） | 老部署上早期安装的插件 | — | 由部署者开关控制，删了老部署的插件会被拒绝加载 |
| C20 | 宿主内置转账通知解析器 | 转账通知解析下沉到 `payment_sources` 插件后，宿主仍以内置解析为准、插件结果只对照；插件失败 / 超时 / 未安装时回到内置（“金额”是群内游戏币） | payment-source-cn-transfer 双跑对照期 | — | 双跑对照期未结束，内置解析是兜底 |

### 3.1 一句话总结

- 20 项兼容层里，**C02、C06、C12、C14、C15 今天仍是官方插件的在用路径**，谈不上“残留”；C09、C10、C17、C18、C19 保护的是**用户数据和老部署**；其余主要服务第三方插件和用户手里的旧版本。
- 6 项有调用计数（弃用登记），14 项没有。没有计数的项目即使将来要讨论去留，也得先补统计。

## 4. 本轮（第 21 轮）为冻结做的准备

- `ctx.capabilities.contract_version` / `GET /api/plugins/capabilities` 的 `contract_version`（Plugin API 1.7，第 1.1 节）。
- `test_plugin_compat_layer.py`：兼容层清单 + 每项至少一个守护测试；守护测试被删或改名、文档与清单不一致时 CI 失败；新增直接守护覆盖 C01–C03、C07–C14、C17（用“旧插件的写法”调用宿主，断言结果不变）。
- 契约快照 / golden / 插件库兼容检查照旧运行，旧插件在新服务器上的行为不变。

## 5. 离“Plugin API 1.0 冻结”还差什么

| # | 待办 | 说明 | 需要谁 |
| --- | --- | --- | --- |
| 1 | 决定冻结范围 | 第 2.2 节那些还没进快照的接口（动作字段、payload 信封、`ctx.client`、`Manifest` 构造参数、异常类型、远程 / Webhook 协议、registry.json 格式）哪些纳入 1.0，纳入的要补进契约快照与破坏检测 | 项目负责人拍板范围，然后工程实现 |
| 2 | 给每项接口标稳定级别 | 在 PLUGIN-API-REFERENCE.md 里把每项标成“稳定 / 实验 / 已弃用”，实验项不进冻结承诺 | 工程 |
| 3 | 兼容层去留决定 | 当前决定：全部保留。若将来要动，只能在契约 2.0 里做，且要先在弃用登记公告至少一个 minor 宿主版本、征得负责人同意（PLUGIN-CONTRACT.md 第 3 节） | 项目负责人 |
| 4 | 补使用量统计 | C07–C20 中 14 项没有调用计数；没有数据就无法判断“还有没有人在用” | 工程 |
| 5 | 官方插件迁到新写法（可选，不影响冻结） | 17 个插件的规则入口改走 `on_event`、7 个插件按钮改 `own_buttons`、codex_image 导出 `dry_run_rule`、interaction-ai-chat 改 `render_profile`；迁移不抬最低版本、旧宿主照旧可用 | 插件库 |
| 6 | 插件作者工具 | 目前没有可 `pip install` 的 SDK / 类型存根；插件库兼容检查会显示宿主契约版本（第 21 轮插件库 PR），但还不会按契约版本给建议 | 工程 |
| 7 | 正式公告 | 范围定下来后，把本文从“草案”改为正式计划，并在插件库 README / 市场页说明 | 项目负责人 |

## 6. 本文明确**不**做的事

- 不删除、不改名、不标记“即将删除”任何兼容层或已弃用接口；
- 不抬任何插件的 `min_telepilot_version`，不改变群内可见行为或游戏币结算；
- 不修改冻结基线 `schemas/plugin-api-contract-1.0.frozen.json`。
