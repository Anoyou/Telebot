# Plugin API 1.0 冻结计划

> **状态：已定（第 22 轮，2026-11-01 项目负责人拍板 1B 2B 3B 4B 5A 6B 7A）。只加保修、不动现状，不删除任何东西。**
>
> - 2026-10-31 项目负责人决定：**兼容层全部保留**，不删除、不标记“即将删除”；真要删只能放在契约 2.0。
> - 2026-11-01 冻结范围定下：每块接口的「稳定 / 实验」划分见**第 0 节**，稳定部分已写进契约快照的 `stability` 段并冻结在 [`schemas/plugin-api-contract-1.0-scope.frozen.json`](../schemas/plugin-api-contract-1.0-scope.frozen.json)，CI 自动拦截破坏性改动。
> - 正式承诺以 [PLUGIN-CONTRACT.md](PLUGIN-CONTRACT.md)（稳定契约 1.0）为准；本文记录范围决定、接口盘点（第 2 节）和兼容层清单（第 3 节）。不抬任何插件的 `min_telepilot_version`，不改群内行为与游戏币结算。
> - 兼容层清单的机器可读版本与守护测试：`backend/app/tests/test_plugin_compat_layer.py`（`COMPAT_LAYER`，与第 3 节编号一一对应，增删兼容层必须同时改两边，CI 会检查）。
> - 0.115.0-beta.3（第 22 轮）起每个兼容层都有**使用统计**（只计数、不告警，见第 3.2 节）：管理员在 Web「插件 → 安装与更新」底部的「兼容层使用统计」卡片，或 `GET /api/plugins/compat-usage` 查看。

## 0. 1.0 冻结范围（已定）

**大白话**：契约就是给插件作者的「保修单」。下表「稳定」= 写进保修单，契约 1.x 期间只会加、不会删或改名，CI 自动拦截；「实验」= 照样能用，但可能调整（会提前公告），不在保修范围；「兼容」= 兼容层照常生效，但不推荐新插件用、也不进保修单。

| # | 部分 | 决定 | 稳定（进保修单） | 实验 / 不承诺 | 机器可读 |
| --- | --- | --- | --- | --- | --- |
| 1 | 标准动作字段 | B | 6 种常用动作的常用字段：`send_message`（`text` `chat_id` `reply_to_message_id` `parse_mode` `reply_markup` `save_message_id_key`）、`edit_message`（`message_id` `chat_id` `text` `parse_mode` `reply_markup`）、`delete_message`（`message_id` `chat_id`）、`end_session`（只有 `type`）、`answer_callback`（`callback_query_id` `text` `show_alert`）、`payout`（`amount` `chat_id` `text` `reply_to_message_id` `parse_mode` `payout_key`，只冻结字段名与含义，结算仍由平台执行，“金额”是群内游戏币） | 实验：`send_via` 通道覆盖、`send_via_options`、`channel_selector`、`rich_message`（Rich Message 走 UserBot 等）、`bot_api_extra`、`render_profile`、`reply_to_user_id` 等回复锚点细项、`edit_message_id` / `message_id_key` / `replace_saved_message_id_key` / `pin` / `send_on_edit_failure`；其余 14 种动作只冻结类型名、字段不承诺。兼容：`compact_tables`（C14） | `stability.action_fields` |
| 2 | Event Bus 信封 | B | 栏目 `source`（`type` `channel` `account_id` `update_id` `callback_query_id` `callback_data`）、`message`（`text` `message_id` `chat_id` `reply_to_message_id`）、`chat`（`id` `type`）、`sender` / `actor`（`user_id` `display_name` `username`）、`source_actor` / `player`（`user_id` `display_name`）、`payment`（`status` `amount` `payer_user_id` `payer_display_name` `receiver_display_name`）、`reply_to`（`message_id`）、`trigger`（`type` `rule_id` `module_key` `entry_key`）、`session`（`key` `scope` `ttl_seconds` `active` `data`）。栏目在某类事件里可能为空（如非转账事件没有 `payment`） | 实验：`raw`、`native_raw` / `native_raw_meta`、`inline_query`、`chosen_inline_result`、`module_config`、`event_type`、`webhook`，以及表里没列的子字段。兼容：`owner_user_ids` / `admin_user_ids`（C07）与旧平铺字段照常下发，但不进保修单 | `stability.event_payload` |
| 3 | `ctx.client` | B | **方法名 + 权限对应**不变：`send_message`→`send_message` `respond` `reply`；`edit_message`→`edit` `edit_message`；`read_chat`→`get_messages` `get_chat` `iter_messages`；`resolve_entity`→`get_entity`；`send_file`→`send_file`；`forward_message`→`forward_messages`；`join_chat`→`join_chat`；`delete_message`→`delete_messages` `unpin_message`；`moderate_chat`→`ban_user` `kick_user` `mute_user` `unban_user`；免权限的 `get_me` | 整体定级「**高级 / 兼容**」：返回值是 Telethon 对象，以 Telethon 为准、不承诺；新插件请用标准动作 / `ctx.messages` | `stability.client_methods` |
| 4 | `manifest.py` 的 `Manifest(...)` | B | 以 plugin.json 为准。稳定参数：`key` `display_name`（唯二承诺位置的参数）`version` `min_telepilot_version` `author` `description` `usage` `permissions` `category` `requires_platform_capabilities` `config_schema` `config_actions` `interaction_entries` `interaction_profile` `event_subscriptions` `capabilities` `allowed_hosts` `agent_tools`，以及推荐只写在 plugin.json 的 `plugin_api` `reads` `middleware` `render_profile` `ui` `provides`。**两边一致性**进 CI（见下） | 实验：`requires_features` `agent_keywords` `command_fallback` `preserve_command_trigger` `strict_trace` `experimental` `http` `interaction_send_via` `on_install`。兼容：`min_telebot_version`（C03） | `stability.manifest_py` |
| 5 | AI 错误类型 | A | `AIUnavailableError`、`AICapabilityError`、`AIResponseTruncated` 三个名字（`app.worker.plugins.ai_facade`），后两个都继承 `AIUnavailableError`（`except AIUnavailableError` 一定能接住）。以后细分只能加子类 | 错误提示文字不冻结 | `stability.ai_errors` |
| 6 | 远程插件 / Webhook | B | 插件包结构：`__init__.py` `manifest.py` `plugin.json` `plugin.py` 四个文件；签名：**Ed25519 detached 签名，签的是整个 ZIP 的字节**，公钥 PEM（`PLUGIN_PUBKEY`），上传 `.sig` 文件 | 实验：入站 **Webhook** 信封（`webhook` `hook_key` `body` `headers`）——等有真实用户再转正 | `stability.remote_package`、`stability.webhook_envelope` |
| 7 | 插件市场 `registry.json` | A | **目录版本 1（`schema_version: 1`）定死**：顶层 `schema_version` `name` `trust` `repository` `homepage` `plugins`；条目 `key` `name` `kind` `icon` `summary` `version` `author` `min_telepilot_version` `plugin_api` `permissions` `reads` `tags` `path` `channels` `repository` `trust`；`kind` 六种、信任等级三种。以后改格式发「目录版本 2」，宿主两版都读 | 宿主读索引的行为不变（不按 `schema_version` 拒绝任何索引） | `stability.registry` |

**manifest.py ↔ plugin.json 一致性检查**（题 4）：`app.worker.plugins.stability.manifest_parity_problems`——两边都写了且不一致才报错（以 plugin.json 为准），一边没写不算矛盾；`author` / `description` / `usage` 这类说明文字不一致只给警告。主仓库 CI 用它检查全部示例插件（`test_plugin_api_stability.py`），插件库 `scripts/check_host_compat.py` 在宿主提供该函数时同样调用（旧宿主回退到插件库自带的检查，结论不变）。

**版本号**：契约版本保持 **1.0**（0.112.0-beta.4 起就是正式的契约 1.0，本次是 1.x 内追加冻结范围，按 PLUGIN-CONTRACT.md 第 1 节规则不升）；Plugin API 保持 **1.7**（没有新增运行时能力，插件感知不到任何变化）。

**怎么防破坏**：`python scripts/export-plugin-contract.py --check`（CI「插件 API 契约」job）除了冻结基线，还对照冻结范围文件检查 `stability` 段——删字段、改名、把稳定降成实验、改签名算法、改 registry 目录版本都会失败；实验部分可以随时调整。守护测试 `test_plugin_api_stability.py` 确认每个稳定条目宿主今天确实还在提供（执行器仍读取字段、事件信封仍产出栏目、白名单仍有方法、继承关系不变、签名能验、registry 字段都被读取）。

## 1. 名词先说清楚

| 叫法 | 是什么 | 现在的值 |
| --- | --- | --- |
| Plugin API 版本 | 宿主给插件的接口版本，插件 `plugin.json` 用 `plugin_api` 范围声明，`ctx.capabilities.plugin_api` 可读 | **1.7**（0.115.0 起；1.6 → 1.7 只新增了契约版本探测） |
| 插件稳定契约版本 | 宿主“只增不减”的正式承诺，见 PLUGIN-CONTRACT.md | **1.0**（冻结基线 = Plugin API 1.6 的接口面） |
| “Plugin API 1.0 冻结” | 路线图 P5 的目标：把对外接口的**范围**定死、写清每项的稳定级别、对兼容层做出去留决定、以此作为插件市场的长期基础 | 第 22 轮已定，见第 0 节 |

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

### 2.2 第 22 轮补进快照的部分（原“在用但没进快照”）

这些接口原来只靠文档和各自的测试维持，第 22 轮按第 0 节的决定补进契约快照 `stability` 段：稳定部分受冻结范围文件保护，实验部分只做标注。

| 接口 | 原状（第 21 轮） | 谁在用 |
| --- | --- | --- |
| 标准动作的**字段**（`send_message` 的 `text` / `reply_markup` / `send_via` …） | 快照只记录动作类型名；字段语义在 PLUGIN-API-REFERENCE.md 与 golden 测试里 | 全部交互插件 |
| Event Bus **payload 信封字段**（`trigger`、`entry_key`、`callback_data`、`session` …） | 文档 + golden 测试 | 全部 `on_event` 插件 |
| `ctx.client`（沙箱包装的 Telethon 客户端） | 字段在快照里，但可调用的 Telethon 方法不在 | 官方库 23 个插件直接用 |
| `manifest.py` 的 `Manifest(...)` 构造参数 | 安装 Schema 覆盖 plugin.json，Python 构造参数（含 `requires_platform_capabilities`）没有单独冻结 | 官方库全部 42 个插件 |
| 异常类型（`AIUnavailableError`、`AICapabilityError`、`AIResponseTruncated` …） | 文档列出，未入快照 | AI 类插件 |
| 远程插件协议、入站 Webhook 信封 | 见 PLUGIN-REMOTE.md、PLUGIN-WEBHOOK-QUICKSTART.md | 远程 / Webhook 插件 |
| 插件市场 `registry.json` 格式、ZIP 包与签名格式 | 见 PLUGIN-REMOTE.md「插件市场与 registry.json」、PLUGIN-SAFETY.md | 插件库、安装流程 |
| `GET /api/plugins/capabilities` 等插件相关 HTTP 接口 | OpenAPI 快照（`openapi/telepilot.openapi.json`）约束，不在插件契约里 | Web / iOS / 开发工具 |

上表前 7 行现在的归属见第 0 节；最后一行（HTTP 接口）仍由 OpenAPI 快照约束，不属于插件契约。

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
- 6 项有调用计数（弃用登记）；第 22 轮起另有**兼容层使用统计**覆盖全部 20 项中可计数的 18 项（见下节），14 项“没有数据”的缺口已补上。

### 3.2 兼容层使用统计（0.115.0-beta.3 起）

目的：给“还有没有人在用”提供数据。**只统计**：不删除、不改名、不标记即将删除，也**不**写警告日志、不提醒插件作者或群用户，任何兼容层的行为都不变。

- 计数维度：兼容项（C01–C20）× 插件 key，记累计次数、首次与最近命中时间（Unix 秒）。与具体插件无关的宿主兜底（如没有安装转账来源插件时用内置解析）记在 `-` 下，界面显示为“宿主（无插件）”。
- 实现：`backend/app/compat_usage.py`（清单 + 进程内计数，命中只做一次字典累加）→ `backend/app/services/plugin_compat_usage.py`（主进程与每个账号 worker 最多每 60 秒批量写入数据库表 `plugin_compat_usage`，进程退出时再写一次；SQLite 与 PostgreSQL 都用 `INSERT … ON CONFLICT DO UPDATE` 累加，多进程并发写不丢计数；写库失败时计数留在内存下次再写，统计失败从不影响插件）。
- 查看：管理员（Web 登录用户）在「插件 → 安装与更新」底部展开「兼容层使用统计」卡片；或 `GET /api/plugins/compat-usage`（只读，返回 `items[]`：`id`、`name`、`trackable`、`counts`（计数口径）、`total`、`plugin_count`、`first_at`、`last_at`、`plugins[]`）。客户端用 `GET /api/plugins/capabilities` 的 `compat_usage: true` 探测；旧服务器没有这个字段，按不支持处理、不显示卡片。
- 弃用登记里的 C02–C06 仍按原来的方式计数（插件详情「运行统计」可见），同时也计入兼容层使用统计。

| 编号 | 什么时候 +1 |
| --- | --- |
| C01 | 无法计数：插件读的是类属性（`getattr(ctx.messages, "xxx", False)`），宿主拦截不到；为计数改成可拦截会拖慢每次读取 |
| C02 | 交互入口改走插件的 `on_interaction` |
| C03 | 加载插件时发现它只写了 `min_telebot_version` |
| C04 | 插件用 `tag=` / `tags=` 调 `ctx.ai.complete / stream_complete` |
| C05 | 插件动作指定已下线的 notice 通道 |
| C06 | 插件动作的按钮直接写 `callback_data` |
| C07 | 无法计数：管理员名单随事件下发，插件读不读宿主看不到 |
| C08 | 宿主替未声明入口策略的旧版插件查兜底入口表并命中 |
| C09 | 存量“算数题”旧规则（`action="math10"`）被触发 |
| C10 | 配置里的 codex_image 旧模型名被改写 |
| C11 | chatgpt_image 旧版本没声明 `x-secret-list`、宿主补声明 |
| C12 | 规则试运行走旧协议 `_dry_run_match` |
| C13 | `,ai` 出图按旧模板值或无声明回退到 codex_image |
| C14 | 插件动作带 `compact_tables` |
| C15 | 消息经旧分发（`message_channels`）交给插件 `on_message` |
| C16 | 直通插件 `on_direct_message` 返回 `True` / `{"consume": ...}` 旧写法 |
| C17 | 从旧 `backend/plugins/installed` 目录加载插件，或启动时把旧内置可选插件记录转成普通插件 |
| C18 | 新配置表缺数据、改读 `Feature.manifest["global_config"]` 或账号级旧 global 字段 |
| C19 | 签名状态未知（`signature_ok` 为空）的旧插件被放行加载 |
| C20 | 转账通知最终采用宿主内置解析结果（无来源插件、插件失败或对照期以宿主为准） |

读数提示：“暂无使用”只代表**这台服务器自统计上线以来**没用到，不能代表第三方插件和其它部署；去留决定仍按第 5 节第 3 项走。

## 4. 本轮（第 21 轮）为冻结做的准备

- `ctx.capabilities.contract_version` / `GET /api/plugins/capabilities` 的 `contract_version`（Plugin API 1.7，第 1.1 节）。
- `test_plugin_compat_layer.py`：兼容层清单 + 每项至少一个守护测试；守护测试被删或改名、文档与清单不一致时 CI 失败；新增直接守护覆盖 C01–C03、C07–C14、C17（用“旧插件的写法”调用宿主，断言结果不变）。
- 契约快照 / golden / 插件库兼容检查照旧运行，旧插件在新服务器上的行为不变。

## 5. 冻结进度

| # | 待办 | 说明 | 需要谁 |
| --- | --- | --- | --- |
| 1 | ✅ 决定冻结范围（第 22 轮） | 7 块全部定下（第 0 节），稳定部分补进契约快照 `stability` 段与冻结范围文件，CI 破坏检测覆盖 | 已完成 |
| 2 | ✅ 给每项接口标稳定级别（第 22 轮） | 第 0 节 + PLUGIN-CONTRACT.md 第 2.6 节 + PLUGIN-API-REFERENCE.md「稳定级别」一节；实验项不进冻结承诺 | 已完成 |
| 3 | 兼容层去留决定 | 当前决定：全部保留。若将来要动，只能在契约 2.0 里做，且要先在弃用登记公告至少一个 minor 宿主版本、征得负责人同意（PLUGIN-CONTRACT.md 第 3 节） | 项目负责人 |
| 4 | ~~补使用量统计~~ ✅ 第 22 轮完成 | 0.115.0-beta.3 起 20 项中可计数的 18 项都有按插件的使用统计（第 3.2 节）；C01、C07 技术上无法计数 | 工程（已完成）；之后由负责人定期查看数据 |
| 5 | 官方插件迁到新写法（可选，不影响冻结） | 17 个插件的规则入口改走 `on_event`、7 个插件按钮改 `own_buttons`、codex_image 导出 `dry_run_rule`、interaction-ai-chat 改 `render_profile`；迁移不抬最低版本、旧宿主照旧可用 | 插件库 |
| 6 | 插件作者工具 | 目前没有可 `pip install` 的 SDK / 类型存根；插件库兼容检查会显示宿主契约版本（第 21 轮插件库 PR），但还不会按契约版本给建议 | 工程 |
| 7 | ✅ 正式公告（第 22 轮） | 本文已改为「已定」；插件库 README / CHANGELOG 同步说明稳定 / 实验划分与 manifest 一致性检查 | 已完成 |

## 6. 本文明确**不**做的事

- 不删除、不改名、不标记“即将删除”任何兼容层或已弃用接口；
- 不抬任何插件的 `min_telepilot_version`，不改变群内可见行为或游戏币结算；
- 不修改冻结基线 `schemas/plugin-api-contract-1.0.frozen.json`，也不修改冻结范围 `schemas/plugin-api-contract-1.0-scope.frozen.json`（第 22 轮写入后永不修改）；
- 不抬契约版本、不抬 Plugin API 版本（冻结范围只是把已有接口写进保修单）。
