# TelePilot iOS 设计约束

## 主题

个人使用的完整原生运维工作台。界面安静、状态真实、操作克制；业务能力与 Web 管理台对应，布局适应手机。

## 色彩

采用系统语义背景、文字与分隔线，自动跟随浅色和深色外观。暖橙 AccentColor 仅用于导航强调和主要操作。成功、警告、故障同时使用文字与图标，不能只靠颜色区分。

## 字体

刻意使用 Apple 系统字体，而不是引入 Web 字体。中文、Dynamic Type、辅助功能与原生控件度量优先；动态数字采用等宽数字。

## 组件

参考 iOS“设置”的分组列表和“家庭”的状态优先结构。TabView、NavigationStack、List、Form、系统确认框和 Sheet 为主要组件。保留 TelePilot 的现有品牌图标。

系统确认框（`confirmationDialog`）在 iOS 26+ 以 popover 形式锚定在它所修饰的视图上：必须挂在真正触发它的那个按钮（或工具栏按钮／行）上，不要挂在整个 List／Form／NavigationStack 上，否则会从屏幕中上部弹出。由多个按钮共用一个确认状态时，给每个按钮各挂一个只在对应操作时为真的绑定；`ForEach` 内的行要用“当前操作对象 == 本行”过滤绑定，避免多行同时绑定同一个状态。

## 布局

概览顶部是紧凑品牌头（BrandMark 图标、TelePilot、一句话说明与当前服务器），紧接一句只陈述可确认事实的状态（服务运行中 / 部分状态未能读取 / 无法读取服务器状态），随后是关键数字、快捷操作、收藏与可排序的服务模块。概览不再自行推断“需要处理”的事项（不准确且给不出解决方法）；全面判断由导航栏右上角的「一键体检」（`stethoscope`，原生 `ToolbarItem`，无障碍标签「一键体检」）负责：点按打开体检面板，直接调用服务器 `GET /api/system-agent/checkup`（与助手“体检一下”同一个只读工具），显示总体结论、每项状态、原因与修复建议，以及可跳转的处理入口（账号、插件、AI、日志、指令与任务、备份与维护、设置、助手）。日志页以“最近问题 → 统一列表 → 详情 → 处理时间线”逐层下钻，每类日志都附中文解释；资金台账以汇总卡片和 Swift Charts 图表为主，明细使用中文标签。底部 Dock 见下方「底部 Dock」一节：默认「概览 · 插件 · 交互 · 更多」，旁边独立的 Agent 打开助手；中间 3 格可在「App 设置 › 自定义 Dock」里选择与排序，没放进 Dock 的入口都在「更多」里，始终可达。iPad 使用导航双栏，助手有会话侧栏。系统登录与 Telegram 账号绑定分别呈现自己的验证步骤。

## 底部 Dock（0.18.0）

- 结构：中间最多 3 个自选格 +「更多」（固定最后）+ Agent（iOS 27 起 `Tab(role: .prominent)` 独立圆钮；**iOS 26 上是第 5 个普通 Tab，不是 bug**）。普通 Tab 不超过 4 个：再多 iPhone 的 `TabView` 会自动生成系统自己的“更多”，与我们的「更多」重复。
- 默认：概览 · 插件 · 交互。AI 在「更多 › 管理」。
- 可选入口（一级 + 二级，顺序即补位顺序）：概览、插件、交互、AI、指令与任务、账号、日志、资金台账（`DockItem.customizable`）。三级入口（Webhook、命中调试、服务器设置、备份与维护）不可选。
- 「App 设置 › 自定义 Dock」（`DockCustomizationView`，图标 `dock.rectangle`）：当前 Dock 预览（`dock.preview`）、已选入口行内「上移」`arrow.up`（`dock.moveUp.<项>`）与「移除」`minus.circle.fill`（`dock.remove.<项>`，至少保留一个）、其余入口「加入」`plus.circle.fill`（`dock.add.<项>`，放满 3 个时禁用并说明原因）、恢复默认（`dock.reset`）。自定义只存本机 `UserDefaults`（`telepilot.dock.slots`），不跨服务器区分。iPhone 底部不支持系统 `TabViewCustomization`（只在 iPad 侧边栏生效），所以用自己的设置页。
- 模块开关联动：`AppModel.disabledModules` 取自 `GET /api/system/capabilities` 中 `desired_enabled == false` 的模块（读取失败或旧服务器没有接口时为空 = 全部显示，与 Web `capabilityEnabledMap` 缺省为开一致）。Dock 里模块关闭的格隐藏，由可选顺序里下一个不在 Dock、模块开着的入口补在原位置（默认 Dock 关掉交互时补上 AI），没有可补的就少一格（`DockLayout.effective`）。「更多」、全局搜索里同样隐藏：交互（interaction_bot）、资金台账（ledger）、Webhook（webhooks）、AI（ai）；命中调试（dispatch_debug）关闭时隐藏「日志与诊断 › 高级诊断」里的模拟消息路由／投递统计／路由诊断记录。
- 「更多」分组：紧急停止 → 常用（被移出 Dock 的概览／插件／交互；全在 Dock 时不显示）→ 管理（账号、AI、指令与任务、资金台账、日志）→ 工具与设置（Webhook、服务器设置、备份与维护；命中调试原生页上线后在这里单独一行）→ App（App 设置、关于与更新）。已在 Dock 的入口不重复列出。
- 路由（推送、体检、概览快捷操作）：目标在 Dock 里就切到那一格（自选格各有自己的导航路径），否则进「更多」里对应页面。
- 导航名 = 页面标题：Dock「交互」进入的页面标题也叫「交互」（作为 Dock 根页面用大标题）。
- 插件 ↔ 交互：声明了 `interaction_entries` 的插件详情「概况」里有「交互规则」`bubble.left.and.text.bubble.right`（`plugin.detail.interactionRules`，交互模块关闭时不显示），打开交互中心并只列出启动该插件的规则（「显示全部规则」开关 `interaction.showAllRules`；新增规则预填该插件第一个入口）；规则编辑页「玩法入口」分组里「插件配置」`slider.horizontal.3`（`interaction.rule.pluginConfig`）打开该插件在本账号的配置分区。

## Web ↔ iOS 页面对照表

以代码为准（Web：`frontend/src/components/layout/Sidebar.tsx` 的 `NAV` 与路由；iOS 0.18.0）。**Web 新增、移动或删除侧栏项／设置分页／独立页面时，必须在同一个 PR 里更新这张表，并写明 iOS 的处理方式（做／不做＋理由），见根目录 `AGENTS.md`。**

| Web 页面（路由） | iOS 位置 | 状态 |
| --- | --- | --- |
| 插件 `/plugins`（卡片视图、按账号、启停、配置） | Dock「插件」› 插件中心 | ✅ 对齐（无批量全局启停，手机少用） |
| 插件 · 运行模式卡（生产／值守） | 更多 › 服务器设置 › 运行模式（插件页「运行」视图由后续任务重做） | ⚠️ 位置与 Web 不同 |
| 插件 · 树状视图（能力分类） | 更多 › 服务器设置 › 能力与插件树（插件页「运行」视图由后续任务重做） | ⚠️ 位置与 Web 不同 |
| 插件管理／安装 `/plugins/manage`、插件市场 | 插件 › 发现／已安装、插件市场 | ✅ 对齐 |
| 消息模板实验室 `/plugins/message-template-lab` | 更多 › 指令与任务 › 模板预览与测试（原始表单） | ⚠️ 缩水（无所见即所得预览） |
| AI `/ai`（概览／供应商／用量）+ 存活检测 `/ai/liveness` | 更多 › 管理 › AI（可自选进 Dock） | ✅ 对齐 |
| 待确认 `/assistant/inbox` | Agent 角标 + 待确认页 | ✅ 对齐 |
| 系统助手 `/assistant` | Dock 独立「Agent」 | ✅ 对齐 |
| 交互 `/interaction` | Dock「交互」（0.18.0 起；可自选移出，移出后在更多 › 常用） | ✅ 对齐 |
| 指令与任务 `/operations`（模板／定时／白名单） | 更多 › 管理 › 指令与任务（可自选进 Dock；白名单在账号 › 定时任务与白名单） | ✅ 基本对齐 |
| 概览 `/overview` | Dock「概览」+ 一键体检 | ✅ 对齐 |
| 资金台账 `/ledger` | 更多 › 管理 › 资金台账（可自选进 Dock） | ✅ 对齐 |
| 入站 Webhook `/webhooks` | 更多 › 工具与设置 › Webhook | ✅ 基本对齐 |
| 命中调试 `/dispatch-debug` | 更多 › 管理 › 日志与诊断 › 模拟消息路由（通用表单；原生页由命中调试任务提供后进「工具与设置」） | ⚠️ 明显缩水 |
| 日志 `/logs`（消息流／系统控制台／Agent 运行／运行事件） | 更多 › 管理 › 日志与诊断（可自选进 Dock） | ⚠️ 少「Agent 运行」 |
| 账号详情 `/accounts/:aid`（7 分页）+ 新增向导 `/accounts/new` | 更多 › 管理 › 账号（可自选进 Dock）› 详情 | ✅ 基本对齐 |
| 系统设置 `/settings`（5 分页） | 更多 › 工具与设置 › 服务器设置、备份与维护；App 设置 | ⚠️ 指令前缀、时区、Trace 等只能在「服务器参数」原始键值里改 |
| 能力开关联动导航（模块关了入口隐藏） | Dock、更多、全局搜索按 `/api/system/capabilities` 隐藏（0.18.0） | ✅ 对齐 |
| 导航顺序偏好（侧栏、PWA 底栏，跨设备） | App 设置 › 自定义 Dock（只存本机，可换入口，不读 Web 的 `ui_preferences`） | ⚠️ 不跨设备同步 |

## 图标

- 每个导航行、列表入口和分组内的次要操作按钮都用 `Label(标题, systemImage:)` 带一个 SF Symbol；不再出现只有文字的 `NavigationLink("…")`。纯数据展示行（`ReadOnlyRow`、记录行）和主要提交按钮（如登录“连接”）可以不带图标。
- 页面根部使用 `.symbolRenderingMode(.hierarchical)`，图标随强调色或文字颜色分层，不手工上色；危险操作沿用红色前景（图标与文字一起变红）。
- 列表行的图标统一是强调色（`NavigationLink`／`collection`／`document` 默认如此）。需要单独给文字着色时，用 `Label { Text… } icon: { Image… }` 分别着色，**不要**给整个 `Label` 设 `.foregroundStyle(.primary)`——那会把图标也染成白色（第十轮「修改管理员密码」与同组橙色图标不一致的根因，`ManagementActionLink` 已改）。
- 优先选择与动作语义一致的系统符号，同一概念全 App 只用一个符号：刷新 `arrow.clockwise`、编辑 `square.and.pencil`／`pencil`、删除 `trash`、导出 `square.and.arrow.up`、复制 `doc.on.doc`、Web 管理台 `safari`、服务器 `server.rack`、更新 `arrow.down.circle`、关于 `info.circle`、定时任务 `calendar.badge.clock`、插件 `puzzlepiece.extension`、Webhook `point.3.connected.trianglepath.dotted`。
- 通用助手函数的默认符号：资源列表 `collection(…)` 为 `list.bullet.rectangle.portrait`，只读文档 `document(…)` 为 `doc.text`，`ManagementActionLink` 按操作类型取默认值（危险／删除 `exclamationmark.triangle`、修改 `square.and.pencil`、执行 `play.circle`），有更贴切的符号时显式传入 `symbol:`。

| 模块 | 入口与符号 |
| --- | --- |
| 更多 | 分组与入口见「底部 Dock」一节；AI `cpu`、指令与任务 `terminal`、账号 `person.2`、App 设置 `apps.iphone`（0.15.0 由「设置」`gearshape` 改名，与管理模块「服务器设置」区分；0.15.0 写的 `iphone.gear` 不是有效符号、真机无图标，0.16.0 改 `apps.iphone`，SF Symbols 2.0／iOS 14 起可用）、关于与更新 `info.circle`；管理模块沿用 `ManagementModule.symbol`（0.16.0 新增「交互中心」`bubble.left.and.text.bubble.right`，排在「指令与任务」后）；紧急停止红色高亮行（0.16.1 起说明并入行内，替换原状态文字，Section footer 只在切换失败／结果未知时显示错误）：左侧红底图标 `exclamationmark.octagon`（开启后 `.fill`），标题「全局紧急停止」（`more.emergencyStop.title`），标题下一行说明按钮 `info.circle` **在文字左边**（footnote，无障碍标签「紧急停止说明」，视觉小图标、点按区域四周扩 10pt），文字（`more.emergencyStop.summary`，footnote，可换行）未开启时为「停止全部消息处理，数据和管理台不受影响」（次要色），开启时为「已停止全部消息处理，点“解除”恢复」（红色），读取中／失败显示「正在读取状态…」／「状态读取失败：…」；右侧「停止／解除」是 borderless 按钮（`more.emergencyStop`，无障碍值保留完整状态「未开启 · 账号与 Bot 正常运行」／「已开启 · 账号进程与 Bot 已停止」），整行其它位置点按同样弹出确认（行用 onTapGesture，ⓘ 子按钮优先，不能把整行包成 Button，否则 ⓘ 点不到）；ⓘ 点开原生 popover（`.presentationCompactAdaptation(.popover)`）：停止什么 `stop.circle`、不影响 `checkmark.shield`、如何恢复 `arrow.uturn.backward.circle`；确认弹窗文案不变 |
| 交互中心（0.16.0；0.18.0 起在 Dock「交互」，页面标题「交互」） | 对齐 Web `/interaction`。账号 `person.crop.circle.badge.checkmark`（原生 `Picker(.menu)`，有未保存修改时禁用）、放弃修改 `arrow.uturn.backward`；运行状态：交互总闸 `power.circle.fill`／`power.circle`、交互 Bot 已配置 `checkmark.circle.fill`／待配置 `exclamationmark.triangle.fill`、监听运行中 `checkmark.circle.fill`／未运行 `exclamationmark.triangle.fill`／未启用 `pause.circle`、启用订阅 `bubble.left.and.text.bubble.right`、失败投递 `tray.and.arrow.down`、Bot 入群情况 `person.2`（可展开）；交互总闸 `power.circle`（Toggle）、交互 Bot Token `key`（`SecureField`，只在保存时提交）、清除 Token `trash`；规则行：插件玩法 `puzzlepiece.extension`／算数题 `function`／只发通知 `bell.badge`，状态启用 `checkmark.circle.fill`／停用 `pause.circle`（文字＋图标），左滑停用／启用、删除 `trash`，添加规则 `plus`；规则编辑：命中后 `play.circle`、触发方式 `bolt.horizontal`、插件入口 `puzzlepiece.extension`、从会话白名单选择 `checklist`；高级 `slider.horizontal.3`。导航栏右上「保存」（`interaction.save`，没有修改时禁用）一次提交 `PUT /api/accounts/{id}/interaction-bot`，不自动重试，失败保留草稿 |
| App 设置 | 首页模块 `square.grid.2x2`、自定义 Dock `dock.rectangle`、Apple 推送 `bell.badge`、Cloudflare Access `lock.icloud`、用户名 `person.crop.circle`、界面锁 `faceid`／`timer`、更换服务器 `arrow.left.arrow.right.circle`、退出登录 `rectangle.portrait.and.arrow.right`（“关于与更新”只在“更多”里出现一次，设置页不再重复） |
| 关于 | App 版本 `iphone`、构建号 `hammer`、服务器版本 `shippingbox`、发布通道 `arrow.triangle.branch`、部署提交 `number`、检查服务器更新 `arrow.down.circle`（全 App 唯一更新入口）、GitHub `chevron.left.forwardslash.chevron.right`、更新日志 `list.bullet.clipboard`、使用指南 `book`、问题反馈 `exclamationmark.bubble`、开源许可 `doc.plaintext`、隐私 `hand.raised` |
| 账号管理 | 编辑 `pencil`、重新登录 `person.badge.key`、复制配置 `doc.on.doc`、导出迁移 `square.and.arrow.up.on.square`、插件 `puzzlepiece.extension`、拟人化 `figure.walk`、限流 `gauge.with.dots.needle.33percent`、用量 `chart.bar`、覆盖规则 `slider.horizontal.below.rectangle`、直通顺序 `arrow.up.arrow.down`、会话白名单 `checklist`、最近会话 `bubble.left.and.bubble.right`、账号指令 `terminal`、Bot 用户 `person.2.badge.gearshape`、失败投递 `tray.and.arrow.down`、删除 `trash` |
| 指令与任务 | 指令模板 `text.badge.plus`、别名 `arrow.triangle.swap`、内置指令 `terminal`、模板变量 `curlybraces`、预览 `eye`、测试发送 `paperplane` |
| 服务器设置（0.15.0 由「系统设置」改名，模块图标 `server.rack`） | 能力开关 `switch.2`、能力与插件树 `point.3.filled.connected.trianglepath.dotted`（0.16.0 由原始文档「平台运行树」改为原生分层视图，见下一行）、运行模式 `dial.medium`、服务器参数 `slider.horizontal.3`、代理 `network`、设备配置 `iphone.gen3`、通知 Bot `bell.badge`、Sudo `person.badge.shield.checkmark`、管理员密码 `key`、验证器 `lock.shield` |
| 能力与插件树（0.16.0） | 对齐 Web 插件中心「树状视图（能力分类）」。查看范围 `person.crop.circle.badge.checkmark`（服务器／某账号，原生 `Picker(.menu)`）；主干：Userbot 进程全部在线 `checkmark.circle.fill`／部分 `exclamationmark.triangle.fill`／没有 `xmark.octagon.fill`（`DisclosureGroup` 展开逐个账号进程：在线 `checkmark.circle.fill`、启动中 `arrow.triangle.2.circlepath`、排队 `clock`、离线 `xmark.circle`）、紧急停止 `exclamationmark.octagon`（已拉闸 `.fill` 红色）、运行预设 `dial.medium`；直接接入 `arrow.down.forward.circle`；能力分支标题 AI `cpu`、交互 `bubble.left.and.text.bubble.right`、入站 Webhook `point.3.connected.trianglepath.dotted`、资金台账 `book.closed`、命中调试 `scope`；分支状态运行中 `checkmark.circle.fill`、启动中 `arrow.triangle.2.circlepath`、停用中 `hourglass`、已暂停 `pause.circle`、失败 `xmark.octagon.fill`、已关闭 `minus.circle`、强制关闭 `lock`；接入／调用的插件 `puzzlepiece.extension`（可展开）；插件行启用 `checkmark.circle.fill`／未启用 `circle`，接入方式小标签、「+能力」、警告 `exclamationmark.triangle.fill` |
| Apple 推送 | 系统通知权限 `hand.raised`、设备已注册 `iphone`、服务器 APNs `server.rack`、推送环境 `arrow.triangle.branch`、配置教程 `book`、GitHub 教程 `safari`、授权注册 `bell.badge`、测试通知 `paperplane`、注销 `bell.slash`、助手待确认 `checkmark.shield`、助手任务结果 `sparkles`、账号告警 `exclamationmark.triangle`、Auth Key `key`；检查状态：正常 `checkmark.circle.fill`、注意 `exclamationmark.triangle.fill`、未就绪 `xmark.circle.fill`、未知 `questionmark.circle` |
| 备份与维护 | 导入导出 `externaldrive.badge.timemachine`、网络诊断 `stethoscope`、重启服务 `restart.circle`、紧急停止 `exclamationmark.octagon` |
| 服务器更新面板 | 当前版本／正在运行 `shippingbox`、目标版本 `sparkles`、当前通道／目标分支 `arrow.triangle.branch`、选择通道 `arrow.left.arrow.right`、当前提交 `number`、目标提交 `number.circle`、待合入提交 `arrow.triangle.pull`、Git 远端／运行方式 `server.rack`、应用更新与跟踪任务 `arrow.down.to.line.circle`、日志 `text.alignleft`、各步耗时 `timer`、自动检查 `clock.arrow.2.circlepath`、检查频率 `timer`、数据库迁移 `externaldrive.badge.exclamationmark`／不可回退迁移 `cylinder.split.1x2`、完整更新流程 `arrow.triangle.2.circlepath`、服务暂不可用重连 `wifi.exclamationmark`、核实上次任务 `checkmark.shield`、重新读取 `arrow.clockwise`、完整更新日志 `list.bullet.clipboard` |
| 插件中心 | 选择配置的账号 `person.crop.circle.badge.checkmark`（原生 `Picker(.menu)`）、状态与分类筛选 `line.3.horizontal.decrease.circle`（有筛选时 `.fill`，原生 `Menu` 内两个内联 `Picker`）、收藏 `star`、排序保存失败 `exclamationmark.triangle.fill`（卡片长按拖动排序，无单独按钮） |
| 页面级搜索 | 导航栏左上角 `magnifyingglass` 按钮（无障碍标签「搜索」，`.pageSearch(...)`），点按展开系统搜索栏（`.searchable`）；插件、AI、更多、概览、账号、日志列表、全部流水、管理记录列表、助手会话列表共用 |
| 概览 | 全局搜索 `magnifyingglass`（左上，展开系统搜索栏，列表换成全局搜索结果；“更多”同一写法）、刷新 `arrow.clockwise`、一键体检 `stethoscope`（右上角）；体检结论：正常 `checkmark.circle.fill`、需关注 `exclamationmark.triangle.fill`、异常 `xmark.octagon.fill`、未能检查 `questionmark.circle`；前往处理 `arrow.forward.circle`、需确认 `checkmark.shield` |
| AI 供应商与模型 | 添加供应商 `plus`（AI 页导航栏）、模型清单顶部「从供应商同步模型」`arrow.triangle.2.circlepath`、模型详情与测试 `info.circle`、单个模型快速测试 `bolt.horizontal`、多模型对话测试 `bubble.left.and.bubble.right`、发送测试 `paperplane`、连续对话 `text.bubble`、清空本页对话 `trash`、高级设置 `slider.horizontal.3`、测活 `waveform.path.ecg`；模型能力：输入（多模态（图片）／仅文本）`photo`、工具调用 `wrench.and.screwdriver`、推理 `brain`、上下文 `text.alignleft`、输出 `text.append`、联网搜索 `globe`。能力只依据服务器字段，没有数据显示“未知” |
| 插件发现／已安装／产品页 | 检查更新 `arrow.triangle.2.circlepath`、新功能 `sparkles`、版本历史 `clock.arrow.circlepath`、账号启用与配置 `person.2`、全局配置 `slider.horizontal.3`、安装历史 `clock`、完整技术信息 `info.circle`、权限 `lock`／未申请权限 `lock.open`、高风险能力 `exclamationmark.shield`、管理插件仓库 `books.vertical`、从 Git 地址安装 `link.badge.plus`、从文件安装 `doc.badge.plus`、远程插件记录 `icloud.and.arrow.down`、服务器总开关 `power.circle`、卸载 `trash`；结果：成功 `checkmark.circle.fill`、失败 `exclamationmark.triangle.fill`、结果待确认 `questionmark.circle.fill` |
| Cloudflare Access | 入口 `lock.icloud`、Google 验证状态 `person.badge.shield.checkmark`、使用 Google 验证 `person.badge.key`、账号 `envelope`、到期时间 `clock`、移除 Access 登录 `rectangle.portrait.and.arrow.right`、高级开关 `key.horizontal`、Service Token 状态 `lock.icloud`、保存并验证连接 `checkmark.shield`、清除 Token `trash`、使用说明 `book`、GitHub 说明 `safari`；结果：连接正常 `checkmark.circle.fill`、未通过 `exclamationmark.triangle.fill` |
| 助手 | 本轮详情 `chart.bar.doc.horizontal`、工具调用 `wrench.and.screwdriver`；输入栏（Liquid Glass 圆角卡片：上方多行输入框，下方工具栏）：左下添加附件 `plus`（原生 popover：从照片选择 `photo`、从文件选择图片 `folder`，只列服务器支持的图片附件）、紧挨「+」的模型胶囊（家族 logo＋「供应商 · 模型」全名＋`chevron.up.chevron.down`；原生 popover：自动 `sparkles`、各模型前为模型家族 logo（`Assets.xcassets/ModelBrands/<家族>`，无法识别 `cpu`），当前项 `checkmark`）、移除图片 `xmark.circle.fill`、右下发送 `arrow.up`（执行中且输入为空时为停止 `stop.fill`）；两者都是系统原生 popover，见「材质」；执行状态（回答位置的流光状态行，见「执行状态与待处理消息」）：正在开始 `hourglass`、排队 `clock`、理解问题 `sparkles`、连接模型 `cpu`、选择工具 `arrow.triangle.branch`、加载技能 `wand.and.stars`、思考中 `brain`、调用工具 `wrench.and.screwdriver`、生成回复 `text.bubble`、整理回复 `checkmark.circle`、等待补充 `text.bubble`、等待审批 `checkmark.shield`、正在停止 `stop.circle`、等待重试 `clock.arrow.circlepath`；待处理消息的处理方式：排队 `text.badge.plus`、插入调整 `arrow.turn.down.right`、停止并替换 `arrow.triangle.2.circlepath`；回答底部本轮详情两行：第一行供应商 · 模型，第二行输入/输出词元 · 工具调用次数 · 重试 · 耗时，点开显示完整详情；消息操作（见下方「消息操作按钮」）：我的消息下复制消息 `doc.on.doc`、选择文本 `text.cursor`、编辑消息 `pencil`（仅最新一轮），回答下复制回复 `doc.on.doc`（复制后短暂变 `checkmark`）、选择文本 `text.cursor`、重新生成 `arrow.clockwise`（仅最新一轮，需确认）；代码块右上角复制 `doc.on.doc`（图标＋文字「复制」）；正在生成的回答不显示 |
| Skills | Skills 入口 `wand.and.stars`（第十轮由 `sparkles.rectangle.stack` 改来：工具栏尺寸下叠放矩形＋小星点难以辨认，且与“助手”`sparkles` 混淆；魔杖＝Agent 可调用的技能，轮廓与插件 `puzzlepiece.extension` 区分；助手页右上角工具栏按钮，AI → 配置 → Skills 也可进入）、安装 `plus`／`square.and.arrow.down`、编辑 SKILL.md `square.and.pencil`、查看内容 `doc.text`、与默认版本差异 `plus.forwardslash.minus`、展开未改动内容 `arrow.up.and.down`、恢复默认 `arrow.uturn.backward`、保留我的修改 `checkmark.circle`、从源地址更新 `arrow.down.circle`、卸载 `trash`、对系统 Agent 启用 `sparkles`、对插件启用 `puzzlepiece.extension`；来源：TelePilot 内置 `shippingbox`、插件内置 `puzzlepiece.extension`、GitHub `chevron.left.forwardslash.chevron.right`、其它 `doc.text` |
| 插件声明式界面 | 插件卡片：刷新／重试 `arrow.clockwise`、读取失败 `exclamationmark.triangle.fill`、卡片按钮 `play.circle`（危险操作 `exclamationmark.triangle`）、操作成功 `checkmark.circle.fill`；配置控件：多选／允许的会话 `checklist`、配置列表 `list.bullet.rectangle`、密钥列表 `key`（新增未保存 `key.fill`）、添加一组／添加 `plus`、未知控件或卡片布局（只读，需更新 App）`arrow.down.app`、只读占位 `lock`；通用规则表单（0.14.1）：规则行启用 `checkmark.circle.fill`／停用 `pause.circle`（文字＋图标）、规则试运行入口 `play.rectangle`、开始试运行 `play.circle`、命中 `checkmark.circle.fill`、未命中 `xmark.circle`；按 `x-ui-visible-if` 隐藏的字段不渲染，数组 `x-ui-widget: textarea` 为每行一项的多行文本框 |
| 插件页面与操作（0.15.0） | 分组「插件页面与操作」（插件详情概况、账号详情；插件没有声明时不显示）：插件页面 `macwindow`、菜单操作 `play.circle`（危险操作 `exclamationmark.triangle`，确认框挂在行上）、结果 `checkmark.circle.fill`／`exclamationmark.triangle.fill`；页面打开失败 `exclamationmark.triangle` + 重试 `arrow.clockwise`。页面是隔离的 `WKWebView`（非持久数据、屏蔽 http(s)、只允许 `tp-plugin:`／`about:`），只经 Bridge 与 App 通信。「规则与自动化」分组只显示插件支持的入口：规则入口要插件声明规则或账号已有规则，「配置任务与进度」要插件有配置操作或账号已有任务，都没有时整个分组不显示 |

新增页面沿用上表；表中没有的概念先在本节补充符号再使用，避免同一含义出现多个图标。

**符号名必须真实存在**（0.16.0）：`Image(systemName:)` 遇到不存在的名字不报错、只显示空白（0.15.0「App 设置」`iphone.gear` 的根因）。UI 测试 `ManagementUITests/testAllSFSymbolNamesInSourcesExist` 扫描 `TelePilotApp/*.swift` 里 `systemImage:`／`systemName:`／`symbol:` 参数中的字面量（含三元表达式两侧），在模拟器上逐个 `UIImage(systemName:)` 校验；新符号请先在 SF Symbols App 里确认名字与最低系统版本（部署目标 iOS 26）。由计算属性返回的符号（如 `ManagementModule.symbol`、`PlatformTree.moduleSymbol`）不在扫描范围内，改动时人工核对。

### 消息操作按钮（第七轮起，第八轮调整，全 App 统一）

参考 Codex：消息下方一行轻量操作，不放进气泡。

- 原生 `Button(标题, systemImage:)`，统一 `.buttonStyle(.plain)` ＋ `AssistantMessageActionStyle`（`LabelStyle`：SF Symbol `.imageScale(.small)` 固定 16pt 宽＋文字，图文间距 4pt，整块至少 44pt 高、可点），`.font(.caption.weight(.medium))`、`.foregroundStyle(.tint)`（强调色，图标与文字同色，不用灰色），无边框、无背景；按钮之间间距 20pt（`AssistantMessageActionStyle.spacing`）。按钮没有水平内边距，按钮行边缘就是图标／文字边缘。
- 可见文字与无障碍标签相同：我的消息「复制消息」「选择文本」「编辑消息」，回答「复制回复」「选择文本」「重新生成」；标识 `assistant.message.copy`／`assistant.message.selectText`／`assistant.message.edit`／`assistant.reply.copy`／`assistant.reply.selectText`／`assistant.reply.regenerate`。
- **选择文本**（第九轮，参考 ChatGPT／Claude iOS）：SwiftUI `Text` 的 `.textSelection(.enabled)` 在 iOS 上只能整段复制，块级 Markdown 又拆成多个 `Text`，不能拖选部分或跨块选择。操作行与长按菜单（`contextMenu`：复制、选择文本、分享）里的「选择文本」打开原生 sheet（`.presentationDetents([.medium, .large])`，导航栏「选择文本」＋右上「完成」`assistant.selectText.done`），内容是 `UIViewRepresentable` 包装的 `UITextView`（`isEditable = false`、`isSelectable = true`，标识 `assistant.selectText.textView`）：回答由同一套块级 Markdown 解析转成 `NSAttributedString`（标题、列表、引用、代码块、表格、行内粗斜体／代码／链接，字体 `UIFont.preferredFont`），我的消息为纯文本；显示的是已脱敏的正文并带 `privacySensitive()`。系统的复制／查找／翻译菜单原样可用。
- 代码块右上角「复制」：图标＋文字、`.caption` 强调色、触控至少 44pt（`assistant.code.copy`），复制后变“已复制”2 秒。
- 我的消息：贴右的气泡（角色标签在气泡上方），操作行在气泡外下方，右边缘与气泡右边缘对齐。助手回答：正文 → 操作行 → 本轮详情（模型名／用量）；操作行左边缘与正文左边缘对齐。
- 复制后只把图标换成 `checkmark` 1.5 秒（文字不变，按钮行不跳动）并有成功触感，VoiceOver 读“已复制”。正在生成的回答不显示操作行。

### 执行状态与待处理消息（第八轮）

状态真实：只显示服务器给出的任务状态、`phase` 与进度事件，没有数据的阶段不显示、不推测。

- **不再有输入栏上方的独立状态条**。正在执行的任务在回答的位置（`AssistantLiveReplyView`）显示一行轻量状态：小图标（`.imageScale(.small)`）＋次要色 `.subheadline` 文字＋流光（斜向高光扫过文字，`assistantShimmer()`；减少动态效果时不动），阶段切换时文字淡入淡出（0.25s）。标识 `assistant.live.status`（无障碍标签即状态文字）。
- 阶段文字：`queued` 且 `queue_ahead > 0` 才显示「排队中 · 前面 N 个」，否则「正在开始…」；收到任何进度事件即视为已开始执行。事件→阶段与服务器 `_phase_from_event` 一致：`run_started` 理解问题、`provider_selected`／`model_capability_check` 连接模型、`route_selected` 选择工具、`skill_selected` 加载技能、`model_attempt`／推理增量／`tool_finished` 思考中、`tool_started` 调用工具：`tool_description`（无则 `tool_name`）、`assistant_delta` 生成回复、`assistant_message`／`done` 整理回复；`retry_scheduled` 等待重试；`waiting_input` 等待补充信息、`waiting_approval` 等待审批（旁边「审批」按钮）、`cancel_requested` 正在停止当前任务。订阅中断时旁边有「恢复进度」。
- 有正文输出后，状态行缩为正文末尾的一行 `.caption2` 三级色小字。
- **输入栏不再有“发送方式”**。上一轮未结束时发出的消息进入服务器排队，在对话末尾显示为**虚线描边**的待处理气泡（无实底，`assistant.message.pending`，无障碍值“待处理”），下方一行次要色 `.caption` 小字显示真实状态（`assistant.queue.status`：排队中 · 前面 N 条／将插入当前任务／已插入当前任务／等待停止当前任务…），再下方三个小胶囊（高 28pt、触控区 44pt）：「排队」（默认，强调色浅底＝已选中）`assistant.queue.queue`、「插入调整」`assistant.queue.adjust`（`POST /queue/{id}/steer`）、「停止并替换」`assistant.queue.replace`（确认后 `POST /queue/{id}/replace`）。每次只提交一次、不自动重试，失败时重新读取队列显示真实状态；改为插入调整／停止并替换后不能再改回。
- 轮到它执行时，它离开队列、成为正式历史消息（实线气泡），按钮消失；插入调整在服务器并入当前任务（`steer_applied`）后变为实线。上一轮已结束时发出的消息全程实线、没有按钮；助手等待补充信息时发出的消息自动作为补充信息（`/runs/{id}/input`），也是实线。

### 性能约束（第十一轮）

- 流式回答的状态（正文、推理、进度阶段）放在独立的 `AssistantLiveState`，只有回答位置的视图读取；写入前比较新旧值，增量约 12 Hz 提交，已闭合的块缓存、只重新解析最后一块，跟随滚动最多每 250 ms 一次。新增流式相关状态时不要放回 `AssistantStore` 的被观察属性。
- 图片一律用 ImageIO 按显示尺寸降采样，缓存按位图字节计成本；本机缓存不保存内联图片，缓存里缺图时如实显示“图片未缓存”。
- 冷启动有会话时先显示本机概览快照，状态行写“上次读取的状态 · 更新于 …”，不显示“服务运行中”或“已连接”；任一项服务器数据读到后切换为真实状态。只有 401／钥匙串失效才回登录页，断网或服务器暂不可用时留在主界面。
- 回前台 30 秒内不重复刷新；推送注册同一服务器与用户只并发一次，24 小时内成功过则回前台不再读取配置与设备。

## 材质

由最新 SDK 的原生导航组件承载 Liquid Glass。内容区不逐卡叠加玻璃。没有网页 CSS、手工折射、背景动画或夸张入场动画。

允许的玻璃用法（用户明确要求，仅限以下原生 API，均为 iOS 26 SDK）：

- **页面顶部分段切换**：原生 `Picker` + `.pickerStyle(.segmented)`，系统默认 `controlSize`，分段控件本身就是 Liquid Glass 控件；不再包白色卡片、实色背景条或额外 `glassEffect`，避免玻璃叠玻璃。位置与间距见下节「分段切换与筛选」。**禁止**挂在大标题页面的 `.safeAreaBar(edge: .top)` 上——大标题折叠/展开与安全区栏顶部插入互相改变布局，滑到顶部会持续抖动。
- **Agent 输入栏**（第五、六轮玻璃容器，第七轮改为白色卡片，第八轮按用户确认的草图恢复玻璃）：`.glassEffect(.regular, in: .rect(cornerRadius: 26, style: .continuous))` 的圆角卡片，两行：上方多行输入框，下方工具栏「+」（34pt 圆形浅底，触控 44pt）→ 模型胶囊（`.fill.tertiary` 胶囊，「供应商 · 模型」全名，供应商加粗，`truncationMode(.middle)`，可变宽）→ `Spacer` → 发送／停止。模型胶囊左缘紧挨「+」，宽度变化由 `Spacer` 吸收，不会挤动「+」与发送按钮。通过 `safeAreaInset(edge: .bottom)` 贴底部安全区并随键盘上移。输入栏里**不使用系统 `Menu`**（Menu 从玻璃里的标签“变形”弹出／收回导致错位，第六、七轮根因）。
- **输入栏 popover**（第九轮，替代第七、八轮自绘的浮层卡片＋手画水滴尖）：「+」附件与模型选择都是普通 `Button`，点按后弹出系统原生 `.popover(isPresented:attachmentAnchor: .rect(.bounds), arrowEdge: .bottom)`，内容加 `.presentationCompactAdaptation(.popover)`，iPhone 上也以锚定气泡呈现。第十轮：popover 不再挂在按钮上，而是挂在输入栏卡片**顶边**、按钮正上方的 2pt 隐形锚点上（`assistant.popoverAnchor.<attach|model>`）——气泡整体在卡片之上、不压输入框；锚点 x 离屏幕左右缘至少 64pt（`arrowMinScreenX`），箭头落在气泡底边平直段，而不是落进 iOS 26 气泡的大圆角（「+」贴近屏幕左缘时箭头与圆角连接处扭曲，第十轮真机截图），因此附件箭头会比「+」中心略偏右：材质（iOS 26 Liquid Glass）、箭头（指向按钮）、出现／消失动画、点外部收起都由系统负责。**不**自绘卡片、尖角、模糊缩放动画，也不给按钮做半透明／旋转等“打开中”样式；popover 内容不另加玻璃背景。不用系统 `Menu`（见上一条）。附件 popover 是两行按钮的 `VStack`，宽度取内容理想宽度（`fixedSize`，至少 200pt，随动态字号），每行 50pt；模型 popover 用原生 `List(.plain)`＋`scrollContentBackground(.hidden)`（宽 330pt、每行 58pt、最高 440pt 可滚动）。模型行：家族 logo（32pt 圆形浅底里 20pt 单色矢量图标，`AssistantModelLogo`）＋模型短名（`.body.weight(.semibold)`）＋「供应商 · 模型」全名（`.footnote` 次要色），当前项右侧强调色 `checkmark`；“自动”的副标题是服务器当前路由（能力接口的 `provider_name` · `resolved_model`，没有时“使用服务器配置”）。家族识别用 `TelePilotCore.ModelBrand`，与 Web `resolveModelBrand` 共用 `frontend/src/lib/modelBrands.json`（iOS 副本与 `Assets.xcassets/ModelBrands` 图标由 `ios/TelePilotCore/scripts/sync-model-brands.py` 生成，iOS 工作流 `--check`。第十轮：图标不再原样拷贝 Web 的压缩路径——Xcode 资源目录的 CoreSVG 解析不了紧挨的弧线标志位（如 `0 01-4.45`），Gemma／Poolside／智谱等图标错乱——改由 `svg_normalize.py` 规范化为只含绝对 M/L/C/Z、坐标显式分隔的单色路径；`check-model-brand-icons.py` 校验安全子集并与 resvg 渲染 Web 原始 SVG 的参考覆盖率比对，`ios/scripts/check-model-brand-render.swift` 在 macOS 上用系统 CoreSVG 实际渲染比对）。附件行选中后先收起 popover，约 0.35 秒后再打开系统照片／文件选择器。打开 popover 时键盘是否收起由系统决定（见 PR 说明与界面测试日志 `R9-KEYBOARD`）；点进输入框会收起 popover。标识：`assistant.panel.attach`／`assistant.panel.model`、输入栏卡片 `assistant.composerBar`、行 `assistant.attach.photos`／`assistant.attach.file`／`assistant.model.option.auto`／`assistant.model.option.<序号>`、行内 logo `assistant.model.logo.<家族>`（仅界面测试暴露，VoiceOver 在行提示里读“模型家族：…”）。助手页的“最小化”悬浮圆按钮沿用 `.glassEffect(.regular.interactive(), in: .circle)`。
- 其余卡片、列表行、表单仍使用系统分组背景，不手工调用 `glassEffect(_:in:)`。筛选不再使用 `.buttonStyle(.glass)` 胶囊行（第五轮改为原生筛选菜单）。

## 分段切换与筛选（全 App 统一）

总原则：能用 iOS 原生控件就用原生控件（`Picker`、`Menu`、`TextField`、`Toggle`、`Stepper`、`DatePicker`、`.searchable`／`UISearchTextField`）；同一功能的控件全 App 同一样式、同一位置。

1. **页面级分段**（插件中心／发现／已安装、AI 概览／供应商／用量、插件详情各分区、待确认操作的状态切换）：
   - 原生 `Picker(.segmented)`，`labelsHidden()`，系统默认尺寸，不加背景或玻璃。
   - 位置：滚动内容的第一项，紧贴导航标题下方；List 页面放在第一个 `Section`，用 `.pageHeaderRow()`（透明背景、零边距、隐藏分隔线），行宽即分组卡片宽度；ScrollView 页面（插件中心卡片网格）用 `PageLayout.margin(width:)` 取与系统分组列表相同的左右边距（宽度 ≥ 414pt 为 20pt，否则 16pt）。各页分段控件位置、尺寸、圆角一致，切换不跳动（UI 测试 `testSegmentedHeadersShareFrameAndAreNotClipped` 校验 frame，并逐像素比对分段控件两端，确认没有被裁切）。
   - 分段控件所在的列表行**只放分段控件**：iOS 26 起分组列表单元格按分组卡片的大圆角裁切行内容，行一高（第五轮把搜索框和分段控件放在同一行）两端就会被切掉。
2. **页面级搜索**（第六轮统一）：导航栏**左上角**放大镜按钮（`ToolbarItem(placement: .topBarLeading)`、`magnifyingglass`、无障碍标签「搜索」），由 `.pageSearch(text:isActive:prompt:identifier:)` 提供；点按后挂上系统 `.searchable(text:isPresented:placement: .navigationBarDrawer(displayMode: .always))` 并立即激活，可直接输入；点“取消”收起并清空。作用于当前页面／当前分页。内容里不常驻搜索框，也不用常驻的 `.searchable`（大标题页面会在标题下常驻搜索栏）。插件、AI、更多、概览（全局搜索结果替换列表内容）、账号、日志列表、全部流水、管理记录列表、助手会话列表都用这一写法。需要跨展开／收起保留的页面状态（如插件中心所选账号与筛选）放在不随内容重建的父视图里。
3. **内容内的筛选**（日志时间范围、台账时间范围／统计周期／分组方式、AI 用量范围与结果、测活结果、Skill 安装来源、差异视图）：表单 `Section` 里的一行原生 `Picker(.segmented)`；选项超过 5 个或文字较长时改为原生 `Picker`（默认 `.menu` 样式，行内显示当前值）。
4. **筛选菜单**：一个列表需要多个维度筛选时（插件中心的状态 + 分类），用一个原生 `Menu`，菜单内每个维度一个 `.inline` 的 `Picker`，标签显示当前筛选摘要，图标 `line.3.horizontal.decrease.circle`（有筛选时用 `.fill`）。
5. **多选**：用系统 `Toggle` 行（例如 AI 供应商“擅长的任务”、多模型对话测试的模型），不手绘勾选圆圈。
6. **单选当前对象**（选择配置的账号、发布通道、刷新频率）：原生 `Picker(.menu)`，标签带 SF Symbol。

### 第五轮原生控件替换清单

| 位置 | 原实现 | 现实现 |
| --- | --- | --- |
| 插件中心状态筛选 | 横向滚动的 `.glass`／`.glassProminent` 胶囊按钮行 + `GlassEffectContainer` | 原生 `Menu` 内的 `Picker(.inline)`「状态」 |
| 插件中心分类筛选 | 单独的 `Menu` 包 `Picker` | 与状态合并进同一个筛选菜单 |
| 插件中心“选择配置的账号” | `Menu` + 自绘文字与上下箭头标签 | 原生 `Picker(.menu)` |
| 插件／AI 页搜索栏 | 自绘 `HStack`（放大镜、`TextField`、清除按钮、填充背景） | 第五轮：系统 `UISearchTextField`；第六轮：导航栏左上角放大镜 → 系统 `.searchable` |
| 插件详情分区切换 | 裸 `Picker` 行（零边距） | 同一规则的页面级分段（第一个 Section、`.pageHeaderRow()`） |
| 待确认操作状态切换 | `.safeAreaBar(edge: .top)` 里的分段控件 | 与其它页面一致：列表第一行的页面级分段 |
| 多模型对话测试选择模型 | 按钮 + 手绘 `checkmark.circle.fill`／`circle` | 系统 `Toggle` 行 |
| Agent 输入栏 | 输入框上方左侧的自绘模型胶囊 + 自绘圆角描边输入框 | 原生玻璃容器：多行 `TextField` + 工具栏（`Menu`「+」、模型 `Menu`＋`Picker`、发送／停止） |
| AI 页分段控件外层 | 第四轮已去掉白色卡片 | 沿用 `.pageHeaderRow()` |

## 必须与禁止

- 必须显示加载、空数据、故障、过期数据、忙碌状态与操作确认。
- 账号配置状态不等于 Worker 真实存活，分别展示。
- 网络超时后不得自动重复执行写操作，也不得把未知结果显示成成功。
- 不把客户端和服务器版本号不相等视为不兼容。
- 不保存密码，不显示服务端密钥，不把调试数据混入真实会话。

## 自适应

iPhone 和 iPad 使用系统安全区；支持横竖屏、长中文、辅助功能字号、浅色与深色、减少透明度与减少动态效果。内容优先流动布局，按钮触控区域至少 44 点。

## 后续扩展

通用表单根据后端契约使用原生字段、枚举、对象和数组控件；动态插件遵守服务端 schema。复杂操作保留预览、确认、结果与失败恢复，页面不泄露实现日志。新增功能沿用同一套导航和确认规范。


## 插件商店（发现／已安装／产品页）

参考 App Store 的「更新」与产品页：

- 「已安装」：最上方「待更新」（标题带真实数量与「全部更新」），每张卡片显示版本 x → y、发布时间（取插件 CHANGELOG 日期，没有时显示发现时间）、可折叠的「新功能」与单个「更新」；下面「最近已更新」（安装历史中最近 30 天的更新），再下面全部已安装插件。插件 Tab 角标为服务器报告的可更新数。
- 「发现」：插件仓库与本地参考插件按分类分组，搜索用导航栏左上角放大镜（`pageSearch`）；未安装显示「获取」（安装前确认），已安装显示「打开」，有同来源新版本显示「更新」。
- 列表行：生成的圆角方块图标（插件名首字或分类符号，颜色由插件标识决定）、名称、一行简介、一行来源与版本，右侧胶囊按钮；点名称区域进入产品页，「打开」进入插件的账号启用与配置。
- 产品页：头部大图标、名称、作者与主按钮；横向信息条（版本、权限数、最低 TelePilot 版本、来源与签名、启用账号数）；「新功能」与版本历史；简介；权限；配置与管理；信息；底部「停用与卸载」危险区，确认框挂在各自按钮上。
- 「全部更新」逐个执行并逐项显示真实结果；某项失败继续下一项，网络中断（结果未知）时停止并重新读取状态。安装、更新、检查更新都不自动重试。没有 CHANGELOG 的插件不显示「新功能」。

## 导航标题

- 一级 Tab 根页面（概览、插件、交互、AI、更多）使用大标题（自选进 Dock 的账号、日志、资金台账、指令与任务沿用原页面的行内标题）（`.large`，或省略声明保持默认）。
- 其余所有推入页、详情页、sheet 都显式写 `.navigationBarTitleDisplayMode(.inline)`，不依赖上级继承。
- Agent 会话页以会话标题为行内标题。

## Cloudflare Access

- 主方案是 App 内 Google 验证（照搬 `cloudflared access login`）：系统登录窗 `ASWebAuthenticationSession` 打开 `/cdn-cgi/access/cli`（Google 禁止 WKWebView 内嵌登录），并行轮询 Cloudflare 令牌转交服务，取回后由 App 主动关闭登录窗；令牌以 `cf-access-token` 头随所有请求发送。转交轮询用独立 URLSession，不带 TelePilot 任何凭证。
- Service Token（`CF-Access-Client-Id` / `CF-Access-Client-Secret`）是「高级」可选项，配置后优先使用。所有网络出口统一在 `SessionTransport` 注入 Access 凭证；凭证存钥匙串、按服务器隔离、不进日志与错误文案。
- 识别 Access 拦截（3xx 指向 `*.cloudflareaccess.com` 或 `/cdn-cgi/access/`、Access 的 401/403 或登录页）并按凭证状态单独提示；登录页在 Access 区引导，已登录时弹出 Access 页面一键重新验证。不跟随任何重定向，其他重定向仍拒绝。
- Access 拦截不清除 TelePilot 会话；写操作被拦截时结果是确定的“未执行”，不显示“结果未知”，也不自动重试或重放。验证后只做只读刷新。
