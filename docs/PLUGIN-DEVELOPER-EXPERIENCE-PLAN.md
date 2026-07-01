# TelePilot 插件开发者体验优化执行计划

> 状态：已执行，随 `0.41.0` 落地
> 建议目标版本：文档和示例单独落地可走 `0.40.x` PATCH（修订版本）；若同步调整插件安装页的开发指南入口，可作为 `0.41.0` MINOR（次版本）发布。
> 核心原则：不重写 0.40.x 插件运行时，只降低开发者第一眼理解成本。

## 0. 落地状态

- 已新增 `docs/PLUGIN-QUICKSTART.md` 和 `docs/PLUGIN-RULES.md`。
- 已新增 `examples/plugins/hello_ping`，并纳入 `scripts/validate-plugin-examples.py`。
- 已更新插件开发指南索引、速查表、概览、安全边界、README 和插件安装页开发指南入口。
- 已按前端入口变更选择 `0.41.0` minor（次版本）发版。

## 1. 背景结论

TelePilot 0.40.x 已经完成插件框架主干，不需要照搬 AWBotNest 的运行时模型。

当前已有能力包括：

- 插件统一通过 `PluginContext` 获取平台能力，例如 `ctx.messages`、`ctx.http`、`ctx.ai`、`ctx.scheduler`、`ctx.log`、受控 `ctx.client`。
- 新 Telegram 插件主路径已经是 Event Bus + 标准事件信封 + MessageOps + Trace。
- 插件启停和更新不要求重启主进程，loader 支持热重载和 generation guard。
- TelePilot 按个人可信插件标准模式运行：插件和插件库由账号主人主动安装与启用，平台负责风险提示、权限声明、Trace、审计、日志脱敏和受控 facade。
- 远程插件安装后默认不运行，必须按账号启用后才会收到事件和执行逻辑。
- 插件中心已经区分“安装插件”和“账号插件启用详情与配置”，不需要再做一套新的插件状态系统。

AWBotNest 值得参考的是表达层，而不是能力层：

- 给开发者一个一眼能复制的最短模板。
- 把插件规范写得更像硬契约，而不是散落在长文档里的建议。
- 让“安装、启用、配置、更新、删除”的心智更直白。

## 2. 本轮目标

1. 新开发者能在 5 分钟内复制一个最小插件，知道文件放哪里、必须声明什么、如何回消息。
2. 文档入口分成三层：Quickstart、插件开发铁律、完整 API，不让新人一开始被 Event Bus 全字段吓住。
3. 新增一页短而硬的“插件开发铁律”，明确必须、禁止、推荐，减少模糊表述。
4. 新增或突出 `hello_ping` 最小示例，保留 `event_bus_demo` 作为完整能力示例。
5. 轻量审计 Web UI 的开发指南入口和安装提示，只修“看不懂”和“重复提示”，不重构插件中心状态体系。

## 3. 非目标

- 不重写 Event Bus、MessageOps、Trace、Delivery Executor、Contract Guard、PluginContext 或 loader。
- 不把远程插件改成安装后默认启用。
- 不新增“运行中、未运行、热重载完成”等持久状态面板。
- 不在插件页重复展示已有的“当前账号已启用”类状态。
- 不鼓励插件直接 import 底座内部模块、直接拼 Bot API、直接操作 Telethon/Kurigram/Pyrogram live event。
- 不恢复旧 `notice` / `bbot_notice` / `notice_bot` 作为主动发送通道。
- 不把旧 `raw_event` 或旧平铺 payload 写成新插件推荐入口。

## 4. 关键决策

### 4.1 `ctx` 能力面已经存在，只补易懂入口

TelePilot 当前的 `ctx` 不是能力缺口。后续文档应该强调：

- 插件只面向 `PluginContext` 和标准事件信封开发。
- 常规消息操作走 `ctx.messages` 或标准 action。
- `ctx.client` 是受控高级能力，不是普通消息发送主路径。

### 4.2 热重载已经存在，只补感知和验证

热重载等于不重启主进程完成插件代码替换和重新加载。当前目标不是重做 loader，而是让文档和更新结果讲清：

- 安装：下载插件代码，不运行。
- 启用：当前账号开始接收事件。
- 禁用：停止事件投递，并清理插件注册的任务和会话。
- 更新：替换本地代码；已启用插件由 worker 热重载。
- 卸载：移除安装记录和本地插件文件。

### 4.3 安装后默认禁用保持不变

远程插件来自账号主人主动添加的仓库，但默认启用仍然不合适。正确心智是：

- 安装表示“代码进入本地插件库”。
- 启用表示“某个账号允许它运行”。
- 配置表示“这个账号如何运行它”。

### 4.4 UI 只做轻量整理

当前插件页已有已安装列表、安装插件页、账号插件启用详情与配置。计划只要求：

- 开发指南 Tab 第一屏更清楚。
- 安装按钮附近如缺少提示，则补一句“安装不会运行；启用和配置请到插件中心按账号处理。”
- 如果同一说明重复出现，保留最靠近操作按钮的一处。
- 热重载证据优先放在更新 toast、日志或 Trace 文档里，不新增常驻状态栏。

## 5. 可并行任务拆分

执行时可以拆成 4 个互不抢文件的任务。若多人或多 agent 并行，必须按下面的写入范围隔离，不允许互相格式化或改动对方文件。

### 任务 A：Quickstart 与最小示例

写入范围：

- `docs/PLUGIN-QUICKSTART.md`
- `examples/plugins/hello_ping/`
- `scripts/validate-plugin-examples.py`
- `examples/plugins/README.md`

具体工作：

1. 新增 `docs/PLUGIN-QUICKSTART.md`，标题建议为“5 分钟写出第一个插件”。
2. Quickstart 只讲最小路径：
   - 插件目录结构。
   - `plugin.json` 最小字段。
   - `manifest.py` 最小字段。
   - `plugin.py` 最小 `on_event`。
   - 匹配 `ping`。
   - 返回 `send_message` action 或使用 `ctx.messages` 生成标准消息操作。
   - 安装后必须在账号上启用才会运行。
3. 新增 `examples/plugins/hello_ping`：
   - 只订阅 `message`。
   - 只匹配纯文本 `ping`。
   - 只回复 `pong`。
   - 不依赖外部 API、真实 Telegram token、live event。
   - 声明最小 `usage`、`event_subscriptions`、`capabilities`、`permissions`。
4. 将 `hello_ping` 纳入 `scripts/validate-plugin-examples.py`。
5. 在 `examples/plugins/README.md` 中说明：
   - `hello_ping` 是入门最小示例。
   - `event_bus_demo` 是完整事件和 action 示例。
   - `with_http`、`with_ai`、`with_interaction` 是专项能力示例。

验收标准：

- 新人只看 Quickstart 就能复制最小插件。
- Quickstart 不展示旧 `notice`、旧 `raw_event`、旧平铺 payload。
- `backend/.venv/bin/python scripts/validate-plugin-examples.py` 通过。

### 任务 B：插件开发铁律与文档入口

写入范围：

- `docs/PLUGIN-RULES.md`
- `docs/PLUGIN-DEV-GUIDE.md`
- `docs/PLUGIN-CHEATSHEET.md`
- `docs/PLUGIN-OVERVIEW.md`
- `docs/PLUGIN-SAFETY.md`
- `README.md`

具体工作：

1. 新增 `docs/PLUGIN-RULES.md`，标题建议为“插件开发铁律”。
2. 控制在 30 条以内，按三类组织：
   - 必须。
   - 禁止。
   - 推荐。
3. 必须包含这些硬规则：
   - 新 Telegram 插件必须走 Event Bus + MessageOps。
   - 插件必须声明 `usage`、`event_subscriptions`、`capabilities`。
   - 发送、编辑、删除、置顶、按钮 ACK、Inline answer、settlement 必须通过 `ctx.messages` 或标准 action。
   - 远程插件安装后默认不运行，启用才运行。
   - 钱相关能力必须走 UserBot 或平台受控结算链路。
   - 外部转账结果通知 Bot 只是付款证据来源，不是 TelePilot 主动发送通道。
   - 不允许把旧 `notice` / `bbot_notice` / `notice_bot` 当主动发送通道。
   - 不允许依赖旧 `raw_event` 或旧平铺 payload；需要原生字段必须声明 `telegram_native_raw`，并写出降级路径。
   - 不允许把 token、session、完整原生 payload、隐私消息写入日志。
4. 调整 `docs/PLUGIN-DEV-GUIDE.md` 的入口顺序：
   - 第一位：Quickstart。
   - 第二位：插件开发铁律。
   - 第三位：完整 API 参考。
   - 后续再列 HTTP、AI、远程插件、安全、速查表。
5. 在 `docs/PLUGIN-CHEATSHEET.md` 开头增加“先看铁律”的入口，并把前几条改成明确的“必须 / 禁止”语气。
6. 在 `docs/PLUGIN-OVERVIEW.md` 生命周期章节补充安装、启用、禁用、更新、卸载的五句话。
7. 在 `docs/PLUGIN-SAFETY.md` 补充插件清理检查表：
   - handler。
   - session。
   - scheduler job。
   - asyncio task。
   - 临时消息。
   - 临时文件。
   - 游戏状态。
8. 在 `README.md` 的插件开发入口加入 Quickstart 和铁律链接。

验收标准：

- `docs/PLUGIN-DEV-GUIDE.md` 像索引，不再把新人直接推到完整 API。
- `docs/PLUGIN-RULES.md` 可以独立解释“什么不能做”。
- 文档中的“必须 / 禁止 / 推荐”语气明确，不把阻断性要求写成建议。

### 任务 C：插件安装页和开发指南入口轻量整理

写入范围：

- `frontend/src/pages/Extensions.tsx`
- 仅当实际组件拆分后需要时，才改 `frontend/src/pages/Plugins/Home.tsx`

具体工作：

1. 在 `frontend/src/pages/Extensions.tsx` 的开发指南 Tab 第一屏置顶三个入口：
   - 5 分钟 Quickstart。
   - 插件开发铁律。
   - 完整 API 参考。
2. 每个入口只写一句用途：
   - Quickstart：复制最小插件。
   - 铁律：确认不能违反的边界。
   - API：查字段、facade、事件信封和 MessageOps。
3. 检查安装按钮、仓库插件卡片、已安装插件列表附近的提示：
   - 如果已经能说明“这里只负责安装、更新、卸载；启用和配置去插件中心按账号处理”，不重复新增。
   - 如果缺少，则只在最靠近操作按钮的位置补一句。
4. 不新增这些状态标签：
   - 运行中。
   - 未运行。
   - 热重载完成。
   - 当前账号已启用的重复 badge。
5. 更新插件成功后的 toast 或日志入口可以补一句“已启用账号会自动热重载”，但不能让 UI 伪造运行时事实。

验收标准：

- 打开插件安装与管理页，第一屏能看出开发者从 Quickstart、铁律、API 三个入口开始。
- 安装插件时不会误解为“安装后立刻运行”。
- 页面没有同一说明在三处以上重复出现。
- 没有新增一套和插件中心现有启用状态冲突的状态体系。

### 任务 D：最终校验与发布材料

写入范围：

- `CHANGELOG.md`，仅在准备发版时修改。
- 四处版本文件，仅在准备发版时修改：
  - `backend/app/__init__.py`
  - `backend/pyproject.toml`
  - `frontend/package.json`
  - `frontend/src/lib/version.ts`

具体工作：

1. 做文档旧词检查。
2. 做示例校验。
3. 如果任务 C 改了前端，做类型检查和构建。
4. 准备中文 changelog，只记录实际落地内容。
5. 按变更范围判断版本：
   - 只新增文档和示例：`0.40.x` PATCH（修订版本）。
   - 文档、示例、插件安装页入口一起落地：`0.41.0` MINOR（次版本）。

验收标准：

- 验证命令结果清楚。
- CHANGELOG 不写愿景，只写已完成内容。
- 版本号和四处版本文件一致。

## 6. 推荐执行顺序

1. 先做任务 A：Quickstart 和 `hello_ping` 是后续文档引用的锚点。
2. 再做任务 B：铁律和索引入口引用 Quickstart。
3. 再做任务 C：UI 只引用已经存在的文档入口。
4. 最后做任务 D：统一验证、判断版本、写中文 changelog。

任务 A 和任务 B 可以并行，但需要约定：

- 任务 A 负责 Quickstart 正文和示例代码。
- 任务 B 可以先写 `PLUGIN-RULES.md`，但引用 Quickstart 时只引用文件名，不复制 Quickstart 大段内容。
- 合并前由一个人统一检查 `PLUGIN-DEV-GUIDE.md` 的最终入口顺序。

## 7. 验证命令

文档与示例：

```bash
backend/.venv/bin/python scripts/validate-plugin-examples.py
rg -n "bbot_notice|notice_bot|旧 notice|旧 raw_event|平铺 payload|ctx\\.client\\.send_message" docs/PLUGIN-QUICKSTART.md docs/PLUGIN-RULES.md docs/PLUGIN-CHEATSHEET.md docs/PLUGIN-OVERVIEW.md docs/PLUGIN-SAFETY.md
git diff --check
```

前端改动后追加：

```bash
cd frontend
./node_modules/.bin/tsc -b --pretty false
./node_modules/.bin/vite build
```

人工验收：

- 打开插件安装与管理页，确认开发指南第一屏有 Quickstart、铁律、API 三个入口。
- 安装一个远程插件但不启用，确认 UI 没有暗示它正在运行。
- 打开插件中心，确认已有“账号插件启用详情与配置”仍是账号级启用和配置主入口。
- 更新一个已启用插件，确认 toast、日志或 Trace 文档能解释热重载，而不是新增不可靠的假状态。
- 打开 `docs/PLUGIN-QUICKSTART.md`，确认新人不读完整 API 也能复制最小插件。

## 8. 风险与处理

| 风险 | 处理 |
| --- | --- |
| 新增文档入口后反而更乱 | `PLUGIN-DEV-GUIDE.md` 只做索引，固定 Quickstart、铁律、API 三层入口 |
| Quickstart 太短导致误导 | 只展示标准事件信封、`ctx.messages` 或标准 action，不展示底层 Bot API |
| UI 提示和插件中心已有状态重复 | 只在最靠近安装/更新操作的位置保留一句，其余重复提示删除 |
| 开发者以为可以直接用旧 `notice` 通道 | 铁律和 Quickstart 都明确禁止把旧 notice 系列当主动发送通道 |
| 热重载被理解成“插件永远不会失败” | 文档写清热重载会记录日志/Trace，失败应显示加载错误或规范警告 |
| 示例和真实 schema 漂移 | `hello_ping` 纳入 `validate-plugin-examples.py` |

## 9. 交付物清单

最终完成后应至少包含：

- `docs/PLUGIN-QUICKSTART.md`
- `docs/PLUGIN-RULES.md`
- 更新后的 `docs/PLUGIN-DEV-GUIDE.md`
- 更新后的 `docs/PLUGIN-CHEATSHEET.md`
- 更新后的 `docs/PLUGIN-OVERVIEW.md`
- 更新后的 `docs/PLUGIN-SAFETY.md`
- 更新后的 `README.md`
- `examples/plugins/hello_ping/`
- 更新后的 `scripts/validate-plugin-examples.py`
- 如执行 UI 轻量整理，更新后的 `frontend/src/pages/Extensions.tsx`

## 10. 建议 CHANGELOG 文案

若按 `0.41.0` 发布，可使用以下中文口径：

```text
## 0.41.0

### 插件开发者体验
- 新增 5 分钟 Quickstart，帮助新开发者快速复制最小 Event Bus + MessageOps 插件。
- 新增插件开发铁律页，把必须、禁止、推荐的规则集中成短契约。
- 新增 hello_ping 入门示例，并纳入插件示例验证，减少文档和真实模板漂移。
- 优化插件安装页的开发指南入口，明确 Quickstart、铁律和完整 API 的阅读顺序。
- 梳理安装、启用、配置、更新、热重载和卸载的说明，保持远程插件安装后默认禁用。
```

若只落地文档和示例，不改前端入口，则应降级为 `0.40.x` PATCH（修订版本），并删除“优化插件安装页”的发布项。
