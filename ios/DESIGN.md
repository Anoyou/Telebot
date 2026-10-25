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

概览顶部是紧凑品牌头（BrandMark 图标、TelePilot、一句话说明与当前服务器），紧接一句话结论（一切正常 / N 项需要处理），再列出“需要处理”（失效账号、未运行进程、插件失败、AI 冷却、最近错误、待补付、待确认），随后是关键数字、快捷操作、收藏与可排序的服务模块。日志页以“最近问题 → 统一列表 → 详情 → 处理时间线”逐层下钻，每类日志都附中文解释；资金台账以汇总卡片和 Swift Charts 图表为主，明细使用中文标签。底部 Dock 支持入口选择与排序，保留始终可达的全部入口；旁边独立玻璃圆形按钮打开助手。iPad 使用导航双栏，助手有会话侧栏。系统登录与 Telegram 账号绑定分别呈现自己的验证步骤。

## 图标

- 每个导航行、列表入口和分组内的次要操作按钮都用 `Label(标题, systemImage:)` 带一个 SF Symbol；不再出现只有文字的 `NavigationLink("…")`。纯数据展示行（`ReadOnlyRow`、记录行）和主要提交按钮（如登录“连接”）可以不带图标。
- 页面根部使用 `.symbolRenderingMode(.hierarchical)`，图标随强调色或文字颜色分层，不手工上色；危险操作沿用红色前景（图标与文字一起变红）。
- 优先选择与动作语义一致的系统符号，同一概念全 App 只用一个符号：刷新 `arrow.clockwise`、编辑 `square.and.pencil`／`pencil`、删除 `trash`、导出 `square.and.arrow.up`、复制 `doc.on.doc`、Web 管理台 `safari`、服务器 `server.rack`、更新 `arrow.down.circle`、关于 `info.circle`、定时任务 `calendar.badge.clock`、插件 `puzzlepiece.extension`、Webhook `point.3.connected.trianglepath.dotted`。
- 通用助手函数的默认符号：资源列表 `collection(…)` 为 `list.bullet.rectangle.portrait`，只读文档 `document(…)` 为 `doc.text`，`ManagementActionLink` 按操作类型取默认值（危险／删除 `exclamationmark.triangle`、修改 `square.and.pencil`、执行 `play.circle`），有更贴切的符号时显式传入 `symbol:`。

| 模块 | 入口与符号 |
| --- | --- |
| 更多 | 账号 `person.2`、设置 `gearshape`、关于与更新 `info.circle`；管理模块沿用 `ManagementModule.symbol` |
| 设置 | 首页模块 `square.grid.2x2`、Apple 推送 `bell.badge`、用户名 `person.crop.circle`、界面锁 `faceid`／`timer`、关于与更新 `info.circle`、更换服务器 `arrow.left.arrow.right.circle`、退出登录 `rectangle.portrait.and.arrow.right` |
| 关于 | App 版本 `iphone`、构建号 `hammer`、服务器版本 `shippingbox`、发布通道 `arrow.triangle.branch`、部署提交 `number`、检查服务器更新 `arrow.down.circle`（全 App 唯一更新入口）、GitHub `chevron.left.forwardslash.chevron.right`、更新日志 `list.bullet.clipboard`、使用指南 `book`、问题反馈 `exclamationmark.bubble`、开源许可 `doc.plaintext`、隐私 `hand.raised` |
| 账号管理 | 编辑 `pencil`、重新登录 `person.badge.key`、复制配置 `doc.on.doc`、导出迁移 `square.and.arrow.up.on.square`、插件 `puzzlepiece.extension`、拟人化 `figure.walk`、限流 `gauge.with.dots.needle.33percent`、用量 `chart.bar`、覆盖规则 `slider.horizontal.below.rectangle`、直通顺序 `arrow.up.arrow.down`、会话白名单 `checklist`、最近会话 `bubble.left.and.bubble.right`、账号指令 `terminal`、Bot 用户 `person.2.badge.gearshape`、失败投递 `tray.and.arrow.down`、删除 `trash` |
| 指令与任务 | 指令模板 `text.badge.plus`、别名 `arrow.triangle.swap`、内置指令 `terminal`、模板变量 `curlybraces`、预览 `eye`、测试发送 `paperplane` |
| 系统设置 | 能力开关 `switch.2`、运行树 `point.3.filled.connected.trianglepath.dotted`、运行模式 `dial.medium`、服务器设置 `slider.horizontal.3`、代理 `network`、设备配置 `iphone.gen3`、通知 Bot `bell.badge`、Sudo `person.badge.shield.checkmark`、管理员密码 `key`、验证器 `lock.shield` |
| Apple 推送 | 系统通知权限 `hand.raised`、设备已注册 `iphone`、服务器 APNs `server.rack`、推送环境 `arrow.triangle.branch`、配置教程 `book`、GitHub 教程 `safari`、授权注册 `bell.badge`、测试通知 `paperplane`、注销 `bell.slash`、助手待确认 `checkmark.shield`、助手任务结果 `sparkles`、账号告警 `exclamationmark.triangle`、Auth Key `key`；检查状态：正常 `checkmark.circle.fill`、注意 `exclamationmark.triangle.fill`、未就绪 `xmark.circle.fill`、未知 `questionmark.circle` |
| 备份与维护 | 导入导出 `externaldrive.badge.timemachine`、网络诊断 `stethoscope`、重启服务 `restart.circle`、紧急停止 `exclamationmark.octagon` |
| 服务器更新面板 | 当前版本 `shippingbox`、最新版本 `sparkles`、更新通道 `arrow.triangle.branch`、选择通道 `arrow.left.arrow.right`、Git 远端 `server.rack`、应用更新 `arrow.down.to.line.circle`、自动检查 `clock.arrow.2.circlepath`、检查频率 `timer` |
| 插件中心 | 选择配置的账号 `person.crop.circle.badge.checkmark`、分类筛选 `line.3.horizontal.decrease.circle` |
| 助手 | 本轮详情 `chart.bar.doc.horizontal`、工具调用 `wrench.and.screwdriver` |

新增页面沿用上表；表中没有的概念先在本节补充符号再使用，避免同一含义出现多个图标。

## 材质

由最新 SDK 的原生导航组件承载 Liquid Glass。内容区不逐卡叠加玻璃。没有网页 CSS、手工折射、背景动画或夸张入场动画。

允许的玻璃用法（用户明确要求，仅限以下原生 API，均为 iOS 26 SDK）：

- **页面顶部分段切换**（插件中心／插件管理、AI 概览／供应商／用量、待确认操作的状态切换等）：原生 `Picker` + `.pickerStyle(.segmented)`，使用系统默认 `controlSize`（不加大），分段控件本身就是 Liquid Glass 控件；不再包白色卡片、实色背景条或额外 `glassEffect`，避免玻璃叠玻璃。
  - 大标题页面（插件、AI 等一级 Tab 根页面）：分段控件放在滚动内容顶部（`List` 的第一个 Section，`listRowBackground(.clear)`、零边距、隐藏分隔线；`ScrollView` 则作为内容第一项），随内容滚动。**禁止**挂在 `.safeAreaBar(edge: .top)` 上——大标题折叠/展开与安全区栏顶部插入互相改变布局，滑到顶部会持续抖动。
  - 行内标题页面（如 sheet 内的“待确认操作”）：可以挂在滚动视图上的 `.safeAreaBar(edge: .top)` 里，由系统负责滚动边缘效果。`safeAreaBar` 必须挂在 `List`／`ScrollView`（或以其为 body 的视图）上，挂在更外层时内容不会让出位置。
- **筛选胶囊**（例如插件状态筛选）：`Button` + `.buttonStyle(.glass)`，选中项 `.buttonStyle(.glassProminent)`（使用 AccentColor），同一行放进一个 `GlassEffectContainer`，横向滚动时用 `.scrollClipDisabled()` 避免裁掉玻璃边缘。选中状态同时加 `.isSelected` 辅助功能特征，不只靠颜色。
- 其余卡片、列表行、表单仍使用系统分组背景，不手工调用 `glassEffect(_:in:)`。

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


## 导航标题

- 一级 Tab 根页面（概览、插件、AI、更多）使用大标题（`.large`，或省略声明保持默认）。
- 其余所有推入页、详情页、sheet 都显式写 `.navigationBarTitleDisplayMode(.inline)`，不依赖上级继承。
- Agent 会话页以会话标题为行内标题。
