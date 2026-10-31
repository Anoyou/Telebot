# TelePilot 插件稳定契约 1.0

> 本文是宿主对插件的**正式承诺**：列在这里（以及机器可读快照里）的接口，在契约 1.x 期间只增不减、语义不变。
> 详细用法仍以 [PLUGIN-API-REFERENCE.md](PLUGIN-API-REFERENCE.md) 为准；本文只说明“哪些东西被冻结、怎么判断兼容、宿主改动受什么约束”。
>
> - 契约版本：**1.0**（自 TelePilot `0.112.0-beta.4` 起承诺）
> - 冻结基线：**Plugin API 1.6** 时的接口面
> - 机器可读快照：[`schemas/plugin-api-contract.json`](../schemas/plugin-api-contract.json)（当前）/ [`schemas/plugin-api-contract-1.0.frozen.json`](../schemas/plugin-api-contract-1.0.frozen.json)（冻结基线，永不修改）
> - 详细 plugin.json Schema：[`schemas/plugin-contract.schema.json`](../schemas/plugin-contract.schema.json)
> - 插件运行时探测：`ctx.capabilities.contract_version`（0.115.0-beta.2 / Plugin API 1.7 起；旧宿主为 `None`，见第 6 节）
> - 冻结准备与兼容层清单（草案，不冻结、不删除）：[PLUGIN-API-1.0-PLAN.md](PLUGIN-API-1.0-PLAN.md)

## 1. 版本号：契约版本与 Plugin API 版本各管各的

TelePilot 有三种和插件相关的版本号：

| 版本号 | 现在的值 | 谁在用 | 怎么变 |
| --- | --- | --- | --- |
| 宿主版本 `TELEPILOT_VERSION` | 0.115.x | 插件 `min_telepilot_version` | 每次发版 |
| Plugin API 版本 `PLUGIN_API_VERSION` | **1.7** | 插件 `plugin.json` 的 `plugin_api` 范围、`ctx.capabilities.plugin_api` | 新增能力 MINOR + 1；破坏兼容才 MAJOR + 1 |
| 插件契约版本 `PLUGIN_CONTRACT_VERSION` | **1.0** | 本文、契约快照、CI 契约检查；插件读 `ctx.capabilities.contract_version`、HTTP 读 `GET /api/plugins/capabilities` 的 `contract_version`（0.115.0-beta.2+） | 契约冻结时定；契约 1.x 内不变，只有发布不兼容的新契约才升到 2.0 |

**为什么不把 Plugin API “重置”成 1.0？** 已发布插件写的是 `"plugin_api": "^1.0"`、`">=1.3"`、`"^1.4"` 这类范围。若把宿主的 Plugin API 改回 1.0，声明 `>=1.3` 的插件会被判“宿主太旧”而拒绝加载。所以：

- `PLUGIN_API_VERSION` **继续单调递增**（下一次新增能力是 1.7，依此类推），插件对 `plugin_api` 的判断方式、`ctx.capabilities` 的探测方式**完全不变**；
- “1.0 冻结”用一个**独立的契约版本号**表达：契约 1.0 = Plugin API 1.6 时的全部接口面。以后 Plugin API 1.7、1.8 … 都属于契约 1.x，只能在基线上**追加**；
- 契约 1.x 期间 Plugin API 的 MAJOR 必须保持 1（CI 会拦截 MAJOR 变化）。

插件作者不需要为契约改任何东西：继续写 `plugin_api` 范围、继续用 `ctx.capabilities.has(...)` 探测新能力即可。

## 2. 冻结范围

冻结基线文件逐项记录了以下内容（括号里是快照中的键）。下表是摘要，以快照为准。

### 2.1 钩子（`hooks`）

| 钩子 | 形式 | 时限（秒） | 引入 | 说明 |
| --- | --- | --- | --- | --- |
| `on_startup(ctx)` | 插件类方法 | — | 0.33.0 | 插件实例启动（每个账号 worker 一次） |
| `on_shutdown(ctx)` | 插件类方法 | — | 0.33.0 | 插件实例停止 / 热重载前 |
| `on_message(ctx, event)` | 插件类方法 | — | 0.33.0 | UserBot 新消息 |
| `on_message_edited(ctx, event)` | 插件类方法 | — | 0.33.0 | UserBot 消息被编辑 |
| `on_direct_message(ctx, event)` | 插件类方法 | — | 0.46.0 | 交互 Bot 直通入口 |
| `on_command(ctx, cmd, args, event)` | 插件类方法 | — | 0.33.0 | 命令 |
| `on_interaction(ctx, entry_key, payload)` | 插件类方法 | — | 0.40.0 | 交互入口迁移桥（已登记弃用，新插件用 `on_event`；契约 1.x 内仍保留） |
| `on_event(ctx, payload)` | 插件类方法 | — | 0.40.0 | Event Bus 事件，返回标准动作列表 |
| `on_config_action(ctx, action_key, payload)` | 插件类方法 | — | 0.45.0 | 配置页动作按钮 |
| `on_config_validate(ctx, config, scope)` | 插件类方法 | 2 | 0.110.0-beta.1 | 保存配置前校验（fail-open） |
| `on_config_saved(ctx, config, scope)` | 插件类方法 | — | 0.110.0-beta.1 | 配置保存后通知 |
| `on_ui_card(ctx, card_id, context)` | 插件类方法 | 3 | 0.110.0-beta.1 | 声明式卡片数据 |
| `on_ui_action(ctx, action_id, context)` | 插件类方法 | 10 | 0.110.0-beta.1 | 卡片按钮 / 菜单项 / 页面 Bridge `action.invoke` |
| `dry_run_rule(config, sample)` | 模块函数（可 async） | 3 | 0.110.0-beta.2 | 规则试运行，无副作用 |
| `parse_payment_notice(text, meta)` | 模块函数（可 async） | 1 | 0.111.0-beta.1 | 转账通知解析，只返回解析结果；结算仍由宿主执行（“金额”是群内游戏币） |
| 中间件处理函数 `(self, ctx, value, mw)` | manifest `middleware[].handler` | `timeout_ms` 50–2000，默认 250 | 0.109.0-beta.1 | 消息管线 inbound / outbound / delivered |

### 2.2 插件上下文（`context`）与 facade（`facades`）

- `PluginContext` 字段：`account_id`、`feature_key`、`config`、`account_config`、`rules`、`client`、`engine`、`redis`、`storage`、`data_dir`、`log`、`scheduler`、`http`、`ai`、`skills`、`messages`、`identities`、`host`、`generation`、`account_proxy_url`、`account_proxy_error`、`event`、`args`、`command`；成员 `capabilities`、`conversation()`、`reply()`。
- 每个 facade 的公开方法与参数（名称、位置 / 关键字、是否有默认值）都在快照里：`ctx.messages`（buffered / live / entry 三种）、`ctx.messages.stream_editor()`、`ctx.ai`、`ctx.http`、`ctx.storage`（含卡片 / 配置钩子里的只读版）、`ctx.skills`、`ctx.scheduler`、`ctx.host`、`ctx.identities`、`ctx.capabilities`。

### 2.3 能力（`capabilities`）

- 能力注册表（冻结基线 45 项，Plugin API 1.7 追加 `runtime.contract_version` 后当前 46 项）：每项的名称、版本、`since`、`plugin_api`、旧属性名、适用 facade 都冻结。
- **`ctx.capabilities` 快照**（`ctx_capabilities_has`）：在“没有 messages / buffered / live / entry”四种上下文里 `has()` 为真的能力清单；以及每种 MessageOps 上为 `True` 的旧能力属性（`legacy_attrs_true`）。契约 1.x 内这些清单只增不减。

### 2.4 plugin.json（`manifest`）

- 安装 Schema [`schemas/plugin.schema.json`](../schemas/plugin.schema.json)（安装 / 升级时的 Pydantic 校验）的全部顶层字段；
- 详细 Schema [`schemas/plugin-contract.schema.json`](../schemas/plugin-contract.schema.json) 再往下描述一层，共 100+ 个字段路径，含：`interaction_entries[]`（`entry_policy`、`legacy_entry_keys[]`、`session_scope`、`participant_policy` …）、`event_subscriptions[]`、`middleware[]`、`payment_sources[]`、`ui.cards[] / ui.pages[] / ui.actions[] / ui.rule_editor`、`reads`、`provides`、`config_actions[]`、`plugin_api`、`min_telepilot_version`。枚举值直接取自宿主运行时常量。
- 所有对象都允许额外字段（向前兼容）：新宿主新增字段不会让旧插件校验失败。

### 2.5 其它

| 部分 | 快照键 | 内容 |
| --- | --- | --- |
| 权限 | `permissions` | 宿主认识的权限名（`send_message` … `message_middleware`） |
| 声明式 UI | `ui` | 控件契约版本与 23 个控件、配置键、卡片 slot（`plugin.overview` / `account.detail` / `dashboard` / `interaction.rule_editor`）与布局、卡片可读资源、UI 钩子时限、页面菜单与字段 |
| 页面 Bridge | `bridge` | 协议 v1；方法 `ready`、`theme.get`（任意页面）、`config.get` / `card.data` / `action.invoke`（各需声明 `config.read` / `cards.read` / `actions.invoke`）；`window.telepilot` JS API |
| Event Bus | `events` | 事件来源、事件类型、订阅范围（含 `own_buttons` / `guest_all` / `inline_all`）、过滤键 |
| 标准动作 | `actions` | `send_message` … `update_session` 共 20 种 |
| 扩展点 | `extension_points` | `provides`（`ai.image_backend`）、`payment_sources`（钩子、1 秒时限、结算字段）、中间件阶段与时限 |
| 只读宿主资源 | `host_read_resources` | `account.admins@1`、`ai.models@1`、`chat.allowed@1`、`plugin.stats@1` |
| 弃用登记 | `deprecations` | 已登记弃用的接口 id（登记 ≠ 删除，见下） |

## 3. 承诺

契约 1.x 期间，宿主**可以**：

- 新增钩子、facade 方法、能力、manifest 字段、控件、卡片 slot、事件类型、动作类型、权限、只读资源；
- 给已有方法 / 钩子**追加带默认值**的参数（或关键字参数）；
- 放宽 manifest 字段允许的类型；
- 修改说明文字、时限数值、返回值说明；
- 登记新的弃用（`deprecated_in`），并在运行统计与日志里提示。

宿主**不可以**（CI 拦截）：

- 删除或改名上述任何条目；
- 删除参数、改变位置参数顺序、把可选参数改成必填、追加没有默认值的参数；
- 改变能力的 `since` / 版本 / 适用 facade，或把能力标记为移除（`removed_in`）；
- 让某个 facade 不再提供基线里有的能力；
- 收窄 manifest 字段允许的类型；
- 升级 Plugin API MAJOR、改变 Bridge 协议号、回退控件契约版本。

此外（不由 CI 判定，但同样承诺）：

- **兼容层不删**（完整清单、用途与保留理由见 [PLUGIN-API-1.0-PLAN.md](PLUGIN-API-1.0-PLAN.md) 第 3 节，守护测试 `test_plugin_compat_layer.py`）：旧属性探测（`getattr(ctx.messages, "rich_table_compact", False)`）、`on_interaction` 迁移桥、`min_telebot_version`、兜底入口表（`app/plugin_compat.py`）、旧 `codex_image` 图片后端固定值等在契约 1.x 内保留；真正删除只会出现在契约 2.0，并且事先在弃用登记里公告至少一个 minor 宿主版本，且需项目负责人同意。去留以使用量为依据：0.115.0-beta.3 起宿主按「兼容项 × 插件」统计每个兼容层的使用次数（只计数、不告警，管理员可见，见 PLUGIN-API-1.0-PLAN.md 第 3.2 节）。
- 不借契约改动抬高任何插件的 `min_telepilot_version`，不改变群内可见行为或游戏币结算。

## 4. 测试与 CI

| 检查 | 位置 | 内容 |
| --- | --- | --- |
| 契约快照最新 + 无破坏性改动 | `scripts/export-plugin-contract.py --check`；`backend/app/tests/test_plugin_api_contract.py` | 运行时内省结果与快照一致；冻结基线 ⊆ 当前；破坏检测器自测（21 类破坏、1 组允许的追加） |
| manifest 校验 | 同上 | 示例 / 内置插件通过安装 Schema 与契约 Schema；13 类常见写错会被契约 Schema 指出；未知字段放行 |
| `ctx.capabilities` 快照、钩子签名 | 同上 | 基线能力在同一 facade 下仍可探测；基类钩子与示例插件重写的钩子能按基线参数调用 |
| 兼容层 | `backend/app/tests/test_plugin_compat_layer.py` | 20 项兼容层（C01–C20）每项至少一个守护测试；守护测试被删 / 改名或与 PLUGIN-API-1.0-PLAN.md 不一致时失败；按旧插件写法直接调用宿主，断言行为不变 |
| golden | `backend/app/tests/test_plugin_golden.py` + `fixtures/plugin_golden/golden.json` | 10 个示例插件在宿主上的输出：安装解析、交互入口与 UI 声明整理、Event Bus / 交互入口 / 卡片 / 菜单钩子输出、中间件 outbound / inbound / stream_preview / delivered |
| 插件库 | CI「插件 API 契约」job | 检出官方插件库 `Anoyou/telebot-plugins` main，跑 `scripts/check_host_compat.py`（宿主兼容检查），并用契约 Schema 校验全部 `plugin.json` |

另有 `插件库兼容` 工作流（`plugin-compat.yml`）带基线宿主对照、跑插件库全量 pytest，两者互补。

## 5. 宿主开发者：改动插件接口时怎么做

1. **新增**接口（新能力、新字段、新钩子…）：照常改代码，再运行
   ```bash
   python scripts/export-plugin-contract.py
   ```
   重新生成 `schemas/plugin-api-contract.json` 与 `schemas/plugin-contract.schema.json` 并提交。新增能力仍按原规则抬 `PLUGIN_API_VERSION` 的 MINOR、登记到能力注册表。
2. `--check` 报“破坏性改动”：说明改动删除或改变了冻结条目。**不要修改冻结基线文件**；改成追加式写法（新参数给默认值、旧名保留为别名、旧能力保留）。确实需要不兼容时，走契约 2.0 流程（先在弃用登记里公告、征得同意，再用 `--freeze` 写新的 `plugin-api-contract-2.0.frozen.json`）。
3. golden 测试失败：先确认是不是有意的行为变化。若是，运行
   ```bash
   TELEPILOT_UPDATE_GOLDEN=1 pytest app/tests/test_plugin_golden.py
   ```
   更新快照，并在 PR 里写清楚哪个插件的哪项输出变了、为什么。

## 6. 插件作者：自查

```bash
# 在主仓库检出里，用契约 Schema 校验自己的插件目录（目录下每个 */plugin.json）
python scripts/export-plugin-contract.py --validate-plugins /path/to/my-plugins
```

想知道某个宿主提供什么，读 `schemas/plugin-api-contract.json`，或在运行时用 `ctx.capabilities.names()`、`GET /api/plugins/capabilities`。

想知道宿主承诺的契约版本（0.115.0-beta.2 / Plugin API 1.7 起）：

```python
caps = getattr(ctx, "capabilities", None)
contract = getattr(caps, "contract_version", None) if caps else None  # 旧宿主为 None
```

`None` 表示宿主早于契约版本探测（契约 1.0 本身自 0.112.0-beta.4 起承诺，只是那时还不能在运行时读到）；不要因此抬 `min_telepilot_version`，具体能力仍用 `caps.has(...)` 判断。
