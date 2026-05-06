# Telegram Userbot 管理系统 PRD（v3 final）

> 单用户、多账号独立运行的 Telegram userbot 管理平台。  
> Web 后台统一编排，每账号一个隔离的 worker 进程；功能以插件形式实现，按"账号 × 功能"粒度独立启停与配置；风控可按动作分桶细粒度自定义。  
> 借鉴：[PagerMaid-Pyro](https://github.com/TeamPGM/PagerMaid-Pyro) 的插件机制、命令体系、Hook 与 apt 风格仓库。

---

## 一、产品定位

为账号持有者本人提供一个 **Web 管理 + 多账号独立运行** 的 Telegram userbot 平台。
- 一个 Web 系统下挂载 N 个 TG 账号，每账号 = 一个 session = 一个 worker 子进程，互不影响。
- 核心能力（自动回复、转发、群管、定时、监控等）以**插件**形式实现，按账号粒度装卸/启停。
- 提供按动作分桶的细粒度风控，含拟人化、冷启动、自动退避等，避免触发 Telegram 限制。
- Web 之外，账号本身也支持在 TG 对话内通过命令（如 `,help`）进行控制。

## 二、目标用户

仅本人使用。无多用户、无团队权限模型。Web 用单一账号 + 2FA 保护。

## 三、核心实体模型

```
Account ─── 1:N ── AccountFeature ── 1:N ── Rule
   │                    │
   │                    └── 配置项（每"账号×功能"独立）
   │
   ├─ Session（加密存储）
   ├─ HumanizeConfig
   ├─ RateLimitRule（动作阈值，账号级）
   └─ Proxy（出口/代理，可选）

RateLimitTemplate ─── 1:N ── RateLimitRule（模板级，作为默认）
```

- **Account**：一个 TG 账号 = 一个 session = 一个 worker 进程
- **Feature/Plugin**：可装卸的能力单元
- **AccountFeature**：N×M 关系，决定某功能在某账号上是否生效
- **Rule**：从属于 AccountFeature
- 转发规则**仅限同一账号内**（不跨账号）

## 四、功能列表

### A. 账号管理
| 功能 | 说明 |
|---|---|
| 新增账号 | API ID/Hash/手机号 → 验证码 → 2FA → 生成加密 session |
| 账号列表 | 卡片/表格视图：头像、用户名、在线状态、绑定时间、风控状态、启用功能数 |
| 启停账号 | 单账号"暂停"——保留配置但不连接 TG |
| 标签/备注 | 便于在配置时筛选 |
| 独立日志/指标 | 每账号独立运行日志、消息数、错误数 |
| 删除账号 | 撤销 session + 清理本地数据（二次确认） |
| 复制配置 | 新账号绑定时可"复制账号 X 的功能配置作为初始" |

### B. 功能矩阵（系统总览）
| 功能 | 说明 |
|---|---|
| 矩阵页 | 行=账号、列=功能；点格子启用/禁用 |
| 一键全开/全关 | 整行/整列批量切换 |
| 进入配置 | 点格子跳转到 [账号×功能] 专属配置页 |

### C. 自动回复
关键词、正则、作用范围（私聊/指定群/全群）、变量、冷却、白/黑名单、优先级。

### D. 消息转发（同账号内）
- 源 → 过滤 → 改写 → 目标 流水线
- **源和目标必须属于同一账号**
- 想让账号 B 也转发同样内容 → 在 B 上单独建（提供"复制规则到其他账号"按钮）

### E. 群组管理
入群欢迎/验证、反垃圾（链接、@、转发、刷屏）、黑名单、关键词处置。

### F. 定时任务
Cron 触发；目标可以是某账号下的若干会话或多账号广播。

### G. 消息监控/归档
关键词命中告警；按账号分别归档；统一搜索界面（可跨账号搜）。

### H. 插件市场
| 功能 | 说明 |
|---|---|
| 内置插件 | C–G 都作为内置插件实现，可禁用 |
| 第三方插件源 | 配置插件源 URL（apt 风格），抓取清单 |
| 安装/卸载/启用/禁用 | Web 上对每个账号独立操作 |
| 沙箱与权限声明 | 插件 manifest 声明所需权限，安装前显示 |
| 失败诊断 | active / failed / disabled 三态（对齐 PagerMaid） |
| 热重载 | 改动只重启对应账号 worker |

### I. TG 内命令
即使没打开 Web，也能在 TG 对话里向自己发命令控制 userbot：

| 命令 | 作用 |
|---|---|
| `,help` | 列出当前账号可用命令 |
| `,status` | 查看 userbot 运行状态、加载的插件 |
| `,plugin list/enable/disable <name>` | 管理当前账号的插件 |
| `,rule list <feature>` | 查看某功能的规则摘要 |
| `,pause` / `,resume` | 暂停/恢复当前账号 |
| `,log tail` | 拉取最近若干条运行日志 |

命令前缀可在 Web 中修改。

### J. Hook 体系（开发者向）
插件可注册 `on_startup` / `on_shutdown` / `command_preprocessor` / `message_preprocessor`。

### K. 系统设置
- 通知渠道（邮件 / Webhook / TG 自发到收藏夹）
- 全局速率限制 + 全局总闸（一键停用所有账号主动动作）
- 风控模板（多套，可应用到新账号）
- 备份与恢复（加密导出 session + 配置）
- 操作日志、运行日志中心（按账号过滤）

### L. 风控与限流（核心）

设计要点：
- **三层叠加**：全局 ≥ 账号 ≥ 规则。任一层超限即触发抑制。
- **按动作分桶**：每类 TG 操作独立配置阈值。
- **抑制策略可选**：drop / queue / backoff / pause / notify。

#### L.1 可配置动作维度

| 动作（action key） | 默认上限（建议起点） | 说明 |
|---|---|---|
| `send_message_private` | 1/秒、20/分、500/时 | 含文本+媒体 |
| `send_message_group` | 1/秒、30/分、1000/时 | |
| `same_peer_send` | 3/分 | 同会话内防刷屏 |
| `edit_message` | 5/分 | |
| `delete_message` | 30/分 | |
| `forward_message` | 20/分 | |
| `callback_query` | 6/分、60/时 | 自动签到/抽奖类 |
| `read_history` | 30/分 | 标记已读 |
| `join_chat` | 5/时、20/天 | 高危动作 |
| `leave_chat` | 5/时 | |
| `create_chat` | 2/天 | |
| `invite_user` | 10/时、50/天 | 易触发 PeerFlood |
| `dm_stranger` | 3/时、20/天 | 最危险动作 |
| `update_profile` | 3/时 | 头像/名字/简介 |
| `upload_file` | 5/分 | |
| `download_file` | 10/分 | |
| `search` | 10/分 | |
| `api_total` | 30/秒、1000/分 | 兜底，所有 MTProto 调用计入 |

每行可"勾选覆盖"或"继承上层"；可勾选"对该账号禁用此动作"。

#### L.2 抑制策略
- **Drop**：直接丢弃 + 日志
- **Queue + Delay**：排队，按当前限速节流（默认）
- **Backoff**：本次延迟 X 秒重试，连续失败指数增长（X·2、X·4…，上限可配）
- **Pause Account**：达到阈值即整账号暂停，等人工恢复
- **Notify Only**：不抑制仅告警（先观察后调参）

#### L.3 拟人化
- 随机抖动：基线 ±N% 延迟
- 打字模拟：发送前 1–3s typing
- 阅读延迟：自动回复前先模拟"已读"
- 活跃时段窗口：限制只在某时段执行主动动作
- 冷启动渐进：新号前 N 天阈值 ×0.3，逐日抬升

#### L.4 自动响应 Telegram 反馈
- **FloodWait**：worker sleep 服务端要求秒数；同类动作阈值 ×0.7，TTL 30 分钟逐步恢复
- **PeerFlood**：自动停用 `dm_stranger` 24h，告警
- **SlowmodeWait**：单会话队列尊重等待秒数
- **AuthKey/Session 失效**：worker 停止，账号置"待重新登录"，告警

#### L.5 风控仪表盘（账号详情内）
- 每动作环形图（最近 1 分 / 1 时 / 1 天 三套切换）
- 最近 24h 抑制事件流
- FloodWait 累计统计
- 一键"调严"：阈值临时 ×0.5，N 小时后恢复
- 模拟测算：输入"我打算发 500 条到 50 个群"，估算耗时与是否超限

#### L.6 全局总闸
- 紧急停用按钮：暂停所有账号主动动作（被动接收照常）
- 全局每秒上限：所有账号 API 调用合计上限
- 出口 IP/代理：每账号可独立绑定（SOCKS5/HTTPS/MTProxy）

#### L.7 配置继承
```
全局风控模板（系统设置）
   ↓ 继承（可覆盖）
账号级（账号详情 → 风控）
   ↓ 继承（可覆盖）
规则级（自动回复/转发/定时单条规则）
```
每层字段标注"继承自上层"或"已覆盖"，便于排查。

---

## 五、页面结构（信息架构）

```
登录页（账号+密码+TOTP）
└── 主框架（左导航 + 顶部状态栏 + 紧急停用按钮）
    ├── Dashboard
    │   ├── 多账号状态卡片
    │   ├── 今日消息/触发/失败趋势图
    │   └── 最近告警与最近操作
    ├── 账号管理
    │   ├── 账号列表
    │   ├── 新增账号向导（4 步）
    │   └── 账号详情
    │        ├─ 概览
    │        ├─ 功能开关
    │        ├─ 已加载插件
    │        ├─ 风控与限流 ★
    │        │    ├─ 仪表盘
    │        │    ├─ 动作阈值
    │        │    ├─ 抑制策略
    │        │    ├─ 拟人化
    │        │    ├─ 自动响应
    │        │    └─ 出口/代理
    │        └─ 运行日志
    ├── 功能矩阵
    ├── 功能配置（按 [账号×功能] 进入）
    │   ├── 自动回复
    │   ├── 转发规则（同账号内）
    │   ├── 群组管理
    │   ├── 定时任务
    │   └── 消息监控
    ├── 插件市场
    │   ├── 已安装（按账号筛选）
    │   ├── 可用
    │   └── 插件源管理
    ├── 日志中心
    │   ├── 操作日志
    │   └── 运行日志
    └── 系统设置
        ├── 命令前缀
        ├── 通知渠道
        ├── 风控模板 ★
        ├── 全局总闸 + 全局每秒上限
        └── 备份与恢复
```

---

## 六、关键交互流程

### 流程 1：首次登录到 Bot 上线
```
注册/登录 Web
   ↓
Dashboard 提示"尚未绑定 TG 账号"
   ↓
点击「绑定」进入向导
   1) API ID / API Hash（附 my.telegram.org 链接）
   2) 手机号 → 收验证码
   3) 输入验证码（如启用 2FA 再输入二步密码）
   4) 完成 → 询问"是否复制已有账号配置？"
   ↓
返回 Dashboard，userbot worker 启动 → 状态变绿
```

### 流程 2：在功能矩阵上批量配置
```
功能矩阵页
   ↓
看到"转发"列：A ✓  B ✗  C ✗
   ↓
点击 B-转发 格子 → 弹层"为账号 B 启用转发"
   ├─ 空配置 → 启用
   └─ 从 A 复制规则 → 启用并克隆
   ↓
进入 [B × 转发] 配置页继续编辑
```

### 流程 3：调整某账号的风控
```
账号详情 → 风控与限流 → 仪表盘
   ↓
看到 "callback_query" 桶最近 1 小时 90%、3 次抑制
   ↓
切到 "动作阈值" → 把 callback_query 上限调低（6→4 /分）
   ↓
保存 → worker 热加载新配置（无需重启）
   ↓
仪表盘实时反映新上限
```

### 流程 4：FloodWait 自适应
```
账号 A worker 调用接口 → Telegram 返回 FloodWait(120s)
   ↓
worker 暂停该动作 120s
   ↓
本地把"该动作"阈值临时 ×0.7、TTL 30m
   ↓
仪表盘记录事件 + 阈值变化 + 通知告警
   ↓
30m 后自动恢复；期间未再触发就不升级；再次触发则继续 ×0.7 并延长 TTL
```

### 流程 5：新号冷启动
```
绑定新账号 → 默认开启冷启动渐进（7 天）
   ↓
day 1：阈值 ×0.3
day 2：×0.45
…
day 7：×1.0
   ↓
任意一天触发 FloodWait → 进度回退一档
```

### 流程 6：插件安装到指定账号
```
插件市场 → 选中插件 → 详情（权限声明、版本、源）
   ↓
点击安装 → 选择应用到哪些账号（多选）
   ↓
对每个所选账号：下载 → 校验 → worker 内加载 → 状态变 active
   ↓
失败的账号单独标记 failed + 错误原因
```

### 流程 7：通过 TG 命令快速操作
```
在 TG 收藏夹给自己发：,plugin disable forward
   ↓
当前账号 userbot 拦截命令（仅响应自己发的消息）
   ↓
执行 → 编辑原消息为执行结果
   ↓
Web 端功能矩阵实时同步
```

---

## 七、风控与限流页 ASCII 线框稿

### 仪表盘 Tab
```
┌─ 账号 A · 风控与限流 ─────────────────────────────── [ 调严 ½×2h ] [ 紧急停用 ] ┐
│                                                                                │
│  Tabs:  [ 仪表盘 ]  动作阈值   抑制策略   拟人化   自动响应   出口/代理         │
│                                                                                │
│  ┌── 实时用量  窗口：[ 1m | 1h | 24h ] ────────────────────────────────┐         │
│  │                                                                    │         │
│  │   发消息(群)        发消息(私)         点击按钮         加群       │         │
│  │   ╭────╮            ╭────╮             ╭────╮          ╭────╮      │         │
│  │   │ 62%│            │ 18%│             │ 95%│ ⚠        │  0%│      │         │
│  │   ╰────╯            ╰────╯             ╰────╯          ╰────╯      │         │
│  │   18/30/min         3/20/min           5.7/6/min       0/5/h       │         │
│  │                                                                    │         │
│  │   编辑   删除   转发   邀请   私聊陌生人   API 总计                │         │
│  │   25%    8%     40%    0%     0%           33%                     │         │
│  └────────────────────────────────────────────────────────────────────┘         │
│                                                                                │
│  ┌── 最近 24h 事件流 ──────────────────────────────────────────────────┐        │
│  │ 14:02:11  ⚠ FloodWait(60s)  动作=send_message_group  会话=xxx 群   │        │
│  │ 14:02:11  ↪ 阈值临时 ×0.7  TTL=30m                                 │        │
│  │ 13:48:02  ⏸ Queue+Delay  动作=callback_query  规则=自动签到        │        │
│  │ 12:11:55  ✗ Drop          动作=send_message_group  原因=同会话超频 │        │
│  │ ...                                                          [更多] │        │
│  └─────────────────────────────────────────────────────────────────────┘        │
│                                                                                │
│  ┌── 模拟测算 ─────────────────────────────────────────────────────────┐        │
│  │  我打算  [发送消息▾]  到 [50] 个 [群组▾]  共 [500] 条               │        │
│  │  → 预计耗时 17 分 22 秒  · 不会超限 ✓     [ 试运行 ]                │        │
│  └─────────────────────────────────────────────────────────────────────┘        │
└──────────────────────────────────────────────────────────────────────────────┘
```

### 动作阈值 Tab
```
┌─ Tab: 动作阈值 ───────────────────────────────────────────────────────────┐
│  说明：未勾选"覆盖"则继承自 系统设置 → 风控模板。带 * 的为继承值（灰色）   │
│                                                                            │
│  动作                覆盖  上限1          上限2          上限3      策略   │
│  ─────────────────────────────────────────────────────────────────────────│
│  发消息(群)           ☑    [30]/[分▾]    [1000]/[时▾]   —          [Queue▾] │
│  发消息(私)           ☐    20/分*        500/时*        —          Queue*   │
│  同会话内发送         ☑    [3]/[分▾]     —              —          [Drop▾]  │
│  编辑消息             ☐    5/分*         —              —          Queue*   │
│  删除消息             ☐    30/分*        —              —          Queue*   │
│  转发消息             ☐    20/分*        —              —          Queue*   │
│  点击按钮             ☑    [4]/[分▾]    [40]/[时▾]      —          [Backoff▾]│
│  标记已读             ☐    30/分*        —              —          Queue*   │
│  加入群/频道          ☐    5/时*         20/天*         —          Pause*   │
│  退出群/频道          ☐    5/时*         —              —          Queue*   │
│  邀请用户入群         ☐    10/时*        50/天*         —          Pause*   │
│  私聊陌生人           ☑    [禁用]                                           │
│  上传文件             ☐    5/分*         —              —          Queue*   │
│  下载文件             ☐    10/分*        —              —          Queue*   │
│  API 总计(兜底)       ☐    30/秒*        1000/分*       —          Backoff* │
│                                                                            │
│                                              [ 取消 ]   [ 保存（热加载）]  │
└────────────────────────────────────────────────────────────────────────────┘
```

### 抑制策略 Tab
```
┌─ Tab: 抑制策略（默认全局，可单条动作覆盖） ──────────────────────────────┐
│                                                                            │
│  策略                行为                                  适用场景         │
│  ──────────────────────────────────────────────────────────────────────── │
│  ○ Drop              直接丢弃 + 日志                       低优先广播       │
│  ● Queue + Delay     按限速排队（默认）                    自动回复         │
│  ○ Backoff           延迟重试（指数）                      callback/API     │
│      └─ 基础秒数  [10]   最大秒数 [1800]                                    │
│  ○ Pause Account     超限即整账号暂停                      高危动作         │
│  ○ Notify Only       不抑制仅告警                          先观察后调参     │
│                                                                            │
│  全局策略：[ Queue + Delay ▾ ]                                              │
│  单条动作覆盖：在「动作阈值」表的最后一列设置                              │
│                                                                            │
│                                                          [ 保存 ]          │
└────────────────────────────────────────────────────────────────────────────┘
```

### 拟人化 Tab
```
┌─ Tab: 拟人化 ─────────────────────────────────────────────────────────────┐
│                                                                            │
│  ☑ 随机抖动           基线 ± [15] %                                        │
│  ☑ 打字模拟           发送前模拟输入 [1] – [3] 秒  概率 [80] %             │
│  ☑ 阅读延迟           自动回复前先模拟"已读" [0.5] – [2] 秒                │
│  ☑ 活跃时段窗口       [08:00] – [24:00]   非窗口仅被动接收                │
│  ☑ 冷启动渐进         前 [7] 天，阈值由 ×0.3 逐日抬升至 ×1.0              │
│      └─ 触发 FloodWait 时回退一档                                          │
│                                                                            │
│                                                          [ 保存 ]          │
└────────────────────────────────────────────────────────────────────────────┘
```

### 自动响应 Tab
```
┌─ Tab: 自动响应 Telegram 反馈 ─────────────────────────────────────────────┐
│                                                                            │
│  FloodWait                                                                 │
│    ☑ 遵循服务端 wait_seconds                                               │
│    ☑ 同类动作阈值临时 × [0.7]   TTL [30] 分钟                              │
│                                                                            │
│  PeerFlood                                                                 │
│    ☑ 停用「私聊陌生人」 [24] 小时                                          │
│                                                                            │
│  SlowmodeWait                                                              │
│    ☑ 单会话队列尊重 wait_seconds                                           │
│                                                                            │
│  Auth/Session 失效                                                         │
│    ☑ Worker 停止，账号置"待重新登录"，并通过通知渠道告警                  │
│                                                                            │
│                                                          [ 保存 ]          │
└────────────────────────────────────────────────────────────────────────────┘
```

### 出口/代理 Tab
```
┌─ Tab: 出口 / 代理 ────────────────────────────────────────────────────────┐
│                                                                            │
│  当前出口： SOCKS5 · 1.2.3.4:1080  ·  延迟 86ms ✓                          │
│                                                                            │
│  类型      [ SOCKS5 ▾ ]   ( SOCKS5 / HTTPS / MTProxy / 直连 )              │
│  地址      [____________________________]   端口 [______]                  │
│  用户名    [____________]   密码 [____________]                            │
│                                                                            │
│  [ 测试连接 ]   [ 保存 ]                                                   │
└────────────────────────────────────────────────────────────────────────────┘
```

---

## 八、数据库 Schema（PostgreSQL）

```sql
-- ────────────────────────────────────────────────────────────────────────
-- 系统级
-- ────────────────────────────────────────────────────────────────────────

CREATE TABLE web_user (
  id              BIGSERIAL PRIMARY KEY,
  username        TEXT UNIQUE NOT NULL,
  password_hash   TEXT NOT NULL,
  totp_secret     TEXT,                      -- 加密
  created_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE system_setting (
  key             TEXT PRIMARY KEY,          -- command_prefix, kill_switch, global_api_qps...
  value           JSONB NOT NULL,
  updated_at      TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE notification_channel (
  id              BIGSERIAL PRIMARY KEY,
  type            TEXT NOT NULL,             -- email | webhook | tg_self
  config          JSONB NOT NULL,            -- 加密敏感字段
  enabled         BOOLEAN DEFAULT true
);

-- ────────────────────────────────────────────────────────────────────────
-- 账号
-- ────────────────────────────────────────────────────────────────────────

CREATE TABLE proxy (
  id              BIGSERIAL PRIMARY KEY,
  type            TEXT NOT NULL,             -- socks5 | https | mtproxy
  host            TEXT NOT NULL,
  port            INTEGER NOT NULL,
  username        TEXT,
  password_enc    TEXT
);

CREATE TABLE account (
  id                 BIGSERIAL PRIMARY KEY,
  phone              TEXT NOT NULL,
  display_name       TEXT,
  api_id_enc         TEXT NOT NULL,
  api_hash_enc       TEXT NOT NULL,
  session_enc        BYTEA NOT NULL,         -- 主密钥加密
  status             TEXT NOT NULL DEFAULT 'active',
                                              -- active | paused | floodwait | dead | login_required
  template_id        BIGINT REFERENCES rate_limit_template(id),
  proxy_id           BIGINT REFERENCES proxy(id),
  cold_start_until   DATE,                   -- NULL = 已结束
  tags               TEXT[],
  notes              TEXT,
  created_at         TIMESTAMPTZ DEFAULT now(),
  updated_at         TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON account (status);

CREATE TABLE humanize_config (
  account_id           BIGINT PRIMARY KEY REFERENCES account(id) ON DELETE CASCADE,
  jitter_pct           SMALLINT DEFAULT 15,
  typing_simulate      BOOLEAN DEFAULT true,
  typing_min_ms        INTEGER DEFAULT 1000,
  typing_max_ms        INTEGER DEFAULT 3000,
  typing_probability   SMALLINT DEFAULT 80,
  read_before_reply    BOOLEAN DEFAULT true,
  active_window_start  TIME,
  active_window_end    TIME,
  cold_start_days      SMALLINT DEFAULT 7
);

-- ────────────────────────────────────────────────────────────────────────
-- 功能 / 插件 / 规则
-- ────────────────────────────────────────────────────────────────────────

CREATE TABLE feature (
  key             TEXT PRIMARY KEY,          -- auto_reply | forward | group_admin | scheduler | monitor | <plugin_key>
  display_name    TEXT NOT NULL,
  is_builtin      BOOLEAN DEFAULT false,
  version         TEXT,
  manifest        JSONB                      -- 权限声明、依赖
);

CREATE TABLE account_feature (
  account_id      BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  feature_key     TEXT NOT NULL REFERENCES feature(key),
  enabled         BOOLEAN DEFAULT false,
  config          JSONB DEFAULT '{}',        -- 该账号下该功能的总配置
  state           TEXT DEFAULT 'disabled',   -- active | failed | disabled
  last_error      TEXT,
  installed_at    TIMESTAMPTZ DEFAULT now(),
  PRIMARY KEY (account_id, feature_key)
);

CREATE TABLE rule (
  id              BIGSERIAL PRIMARY KEY,
  account_id      BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  feature_key     TEXT NOT NULL,
  name            TEXT NOT NULL,
  enabled         BOOLEAN DEFAULT true,
  priority        INTEGER DEFAULT 100,
  config          JSONB NOT NULL,            -- 规则内容（关键词/cron/源-目标等）
  created_at      TIMESTAMPTZ DEFAULT now(),
  updated_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON rule (account_id, feature_key, enabled);

-- ────────────────────────────────────────────────────────────────────────
-- 风控
-- ────────────────────────────────────────────────────────────────────────

CREATE TABLE rate_limit_template (
  id            BIGSERIAL PRIMARY KEY,
  name          TEXT NOT NULL,
  is_default    BOOLEAN DEFAULT false,
  created_at    TIMESTAMPTZ DEFAULT now()
);

CREATE TABLE rate_limit_rule (
  id              BIGSERIAL PRIMARY KEY,
  scope           TEXT NOT NULL,              -- template | account | rule
  scope_id        BIGINT NOT NULL,
  action          TEXT NOT NULL,              -- send_message_group | callback_query | join_chat ...
  per_second      INTEGER,
  per_minute      INTEGER,
  per_hour        INTEGER,
  per_day         INTEGER,
  same_peer_per_minute INTEGER,
  policy          TEXT NOT NULL DEFAULT 'queue',
                                              -- drop | queue | backoff | pause | notify
  backoff_base_seconds INTEGER DEFAULT 5,
  backoff_max_seconds  INTEGER DEFAULT 1800,
  enabled         BOOLEAN DEFAULT true,
  UNIQUE(scope, scope_id, action)
);

CREATE TABLE rate_limit_event (
  id          BIGSERIAL PRIMARY KEY,
  account_id  BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  ts          TIMESTAMPTZ DEFAULT now(),
  action      TEXT NOT NULL,
  outcome     TEXT NOT NULL,                  -- ok | drop | queued | backoff | pause | floodwait | peerflood | slowmode
  detail      JSONB
);
CREATE INDEX ON rate_limit_event (account_id, ts DESC);
CREATE INDEX ON rate_limit_event (account_id, action, ts DESC);

-- 临时调整（如 FloodWait 触发的阈值衰减），TTL 到期由后台清理
CREATE TABLE rate_limit_override (
  id              BIGSERIAL PRIMARY KEY,
  account_id      BIGINT NOT NULL REFERENCES account(id) ON DELETE CASCADE,
  action          TEXT NOT NULL,
  multiplier      NUMERIC(4,2) NOT NULL,      -- 例：0.70
  reason          TEXT,
  expires_at      TIMESTAMPTZ NOT NULL,
  created_at      TIMESTAMPTZ DEFAULT now()
);
CREATE INDEX ON rate_limit_override (account_id, expires_at);

-- ────────────────────────────────────────────────────────────────────────
-- 日志
-- ────────────────────────────────────────────────────────────────────────

CREATE TABLE audit_log (              -- 操作日志（Web 端动作）
  id          BIGSERIAL PRIMARY KEY,
  ts          TIMESTAMPTZ DEFAULT now(),
  user_id     BIGINT REFERENCES web_user(id),
  action      TEXT NOT NULL,
  target      TEXT,
  detail      JSONB
);

CREATE TABLE runtime_log (            -- 运行日志（worker 输出）
  id          BIGSERIAL PRIMARY KEY,
  account_id  BIGINT REFERENCES account(id) ON DELETE CASCADE,
  ts          TIMESTAMPTZ DEFAULT now(),
  level       TEXT NOT NULL,                  -- debug | info | warn | error
  source      TEXT,                           -- 插件名/模块
  message     TEXT NOT NULL,
  detail      JSONB
);
CREATE INDEX ON runtime_log (account_id, ts DESC);
CREATE INDEX ON runtime_log (account_id, level, ts DESC);

-- ────────────────────────────────────────────────────────────────────────
-- 插件市场
-- ────────────────────────────────────────────────────────────────────────

CREATE TABLE plugin_repo (
  id              BIGSERIAL PRIMARY KEY,
  name            TEXT NOT NULL,
  url             TEXT NOT NULL,
  enabled         BOOLEAN DEFAULT true,
  last_synced_at  TIMESTAMPTZ
);

CREATE TABLE plugin_available (        -- 从 repo 同步来的清单
  repo_id         BIGINT NOT NULL REFERENCES plugin_repo(id) ON DELETE CASCADE,
  key             TEXT NOT NULL,
  name            TEXT NOT NULL,
  version         TEXT NOT NULL,
  author          TEXT,
  description     TEXT,
  manifest        JSONB,
  PRIMARY KEY (repo_id, key)
);
```

---

## 九、REST API

通用约定：
- 鉴权：Web 登录 cookie/JWT；Worker→主进程走内网 token
- 错误：`{ "error": { "code": "...", "message": "..." } }`
- 时间：ISO 8601 UTC

### 9.1 账号
```http
GET    /api/accounts                 # 列表（含状态、启用功能数）
POST   /api/accounts                 # 创建（启动绑定向导）
GET    /api/accounts/{aid}
PATCH  /api/accounts/{aid}           # 改名、备注、标签、模板、代理
DELETE /api/accounts/{aid}           # 撤销 session + 清数据

POST   /api/accounts/{aid}/login/start    { phone }
POST   /api/accounts/{aid}/login/code     { code }
POST   /api/accounts/{aid}/login/2fa      { password }

POST   /api/accounts/{aid}/pause
POST   /api/accounts/{aid}/resume

POST   /api/accounts/{aid}/clone-config   { from_account_id, features:[...] }
```

### 9.2 功能矩阵 / 功能开关
```http
GET    /api/feature-matrix                 # 一次返回 N×M 矩阵
PATCH  /api/accounts/{aid}/features/{key}  # { enabled, config? }
GET    /api/accounts/{aid}/features        # 该账号所有功能开关与状态
```

### 9.3 规则（自动回复 / 转发 / 群管 / 定时 / 监控）
统一 CRUD，按 `feature_key` 区分语义：
```http
GET    /api/accounts/{aid}/features/{key}/rules
POST   /api/accounts/{aid}/features/{key}/rules
GET    /api/accounts/{aid}/features/{key}/rules/{rid}
PATCH  /api/accounts/{aid}/features/{key}/rules/{rid}
DELETE /api/accounts/{aid}/features/{key}/rules/{rid}

POST   /api/accounts/{aid}/features/{key}/rules/{rid}/dry-run   # 试运行
POST   /api/accounts/{aid}/features/{key}/rules/copy            # 复制到其他账号
       { rule_ids:[...], target_account_ids:[...] }
```

### 9.4 风控
```http
# 模板
GET    /api/rate-templates
POST   /api/rate-templates
PATCH  /api/rate-templates/{id}
DELETE /api/rate-templates/{id}
GET    /api/rate-templates/{id}/rules
PATCH  /api/rate-templates/{id}/rules/{action}

# 账号级
GET    /api/accounts/{aid}/rate-limit                # 含继承后的有效配置
PUT    /api/accounts/{aid}/rate-limit                # 整体覆盖
PATCH  /api/accounts/{aid}/rate-limit/{action}       # 单条动作覆盖/取消覆盖
DELETE /api/accounts/{aid}/rate-limit/{action}       # 取消覆盖（恢复继承）

# 用量与事件
GET    /api/accounts/{aid}/rate-limit/usage?window=1m|1h|24h
GET    /api/accounts/{aid}/rate-limit/events?since=...&action=...&outcome=...

# 临时调整
POST   /api/accounts/{aid}/rate-limit/strict        # 一键调严
       { multiplier: 0.5, ttl_seconds: 7200 }
GET    /api/accounts/{aid}/rate-limit/overrides     # 当前生效的临时覆盖

# 拟人化
GET    /api/accounts/{aid}/humanize
PUT    /api/accounts/{aid}/humanize

# 模拟测算
POST   /api/accounts/{aid}/rate-limit/estimate
       { action, target_count, total_count } → { eta_seconds, exceeds_limit }

# 全局
GET    /api/system/kill-switch
POST   /api/system/kill-switch                      # { enabled: true|false }
GET    /api/system/global-limits
PUT    /api/system/global-limits                    # { api_qps_total }
```

### 9.5 插件市场
```http
GET    /api/plugin-repos
POST   /api/plugin-repos                            # 添加源
DELETE /api/plugin-repos/{id}
POST   /api/plugin-repos/{id}/sync

GET    /api/plugins/available                       # 已同步的可用插件
GET    /api/plugins/installed?account_id=
POST   /api/plugins/install                         # { plugin_key, account_ids:[...] }
POST   /api/plugins/uninstall                       # { plugin_key, account_ids:[...] }
POST   /api/plugins/{key}/enable                    # { account_ids:[...] }
POST   /api/plugins/{key}/disable                   # { account_ids:[...] }
POST   /api/plugins/{key}/reload                    # { account_ids:[...] }
```

### 9.6 日志 / 通知 / 系统
```http
GET    /api/logs/audit?since=&user_id=
GET    /api/logs/runtime?account_id=&level=&since=

GET    /api/notification-channels
POST   /api/notification-channels
PATCH  /api/notification-channels/{id}
DELETE /api/notification-channels/{id}
POST   /api/notification-channels/{id}/test

GET    /api/system/settings                         # 命令前缀、其他
PATCH  /api/system/settings

POST   /api/system/backup                           # 触发加密备份
POST   /api/system/restore                          # 上传备份恢复
```

### 9.7 请求/响应样例

```http
PATCH /api/accounts/12/rate-limit/callback_query
Content-Type: application/json

{
  "per_minute": 4,
  "per_hour": 40,
  "policy": "backoff",
  "backoff_base_seconds": 10,
  "backoff_max_seconds": 600
}
```

```http
GET /api/accounts/12/rate-limit/usage?window=1m

200 OK
{
  "window": "1m",
  "buckets": [
    { "action": "send_message_group", "used": 18,  "limit": 30, "pct": 60 },
    { "action": "send_message_private","used": 3,  "limit": 20, "pct": 15 },
    { "action": "callback_query",      "used": 5.7,"limit": 6,  "pct": 95, "warn": true },
    { "action": "join_chat",           "used": 0,  "limit": 5,  "pct": 0  }
  ],
  "active_overrides": [
    { "action": "send_message_group", "multiplier": 0.7, "expires_at": "2026-05-02T14:32:11Z" }
  ]
}
```

```http
GET /api/feature-matrix

200 OK
{
  "features": [
    { "key": "auto_reply",   "name": "自动回复" },
    { "key": "forward",      "name": "转发" },
    { "key": "group_admin",  "name": "群组管理" },
    { "key": "scheduler",    "name": "定时任务" }
  ],
  "accounts": [
    {
      "id": 11, "name": "@anoyou_main",
      "features": { "auto_reply": "active", "forward": "active", "group_admin": "disabled", "scheduler": "active" }
    },
    {
      "id": 12, "name": "@anoyou_alt",
      "features": { "auto_reply": "active", "forward": "disabled", "group_admin": "active", "scheduler": "disabled" }
    }
  ]
}
```

---

## 十、技术架构

- **后端**：Python 3.12 + FastAPI（Web/REST）+ Pyrogram（与 PagerMaid 同库，便于复用插件生态）
- **进程模型**：每账号一个独立 worker 子进程；主进程做调度与 API；进程间用 Redis pub/sub
- **存储**：PostgreSQL（主数据）+ 加密文件/字段（session、API hash）+ Redis（速率令牌桶、任务队列）
- **风控实现**：worker 内令牌桶/漏桶，多窗口（秒/分/时/天）并存；同会话桶单独维护
- **前端**：React + TypeScript + 表格/矩阵组件（如 AG Grid）+ ECharts
- **部署**：Docker Compose（web、worker-supervisor、postgres、redis）

## 十一、非功能需求

- **隔离性**：账号 worker 崩溃不影响其他账号；OOM 自动重启
- **安全**：Session/API Hash 用主密钥加密；Web 强制 HTTPS + 2FA；操作日志可追溯
- **风控友好**：默认每账号 ≤20 条/分钟、自动指数退避、全局每秒上限
- **可观测**：每账号独立 Prometheus 标签
- **可恢复**：每日加密备份所有 session + 配置（可选上传对象存储）

## 十二、与 PagerMaid-Pyro 对照

| 维度 | PagerMaid-Pyro | 本系统 |
|---|---|---|
| 形态 | CLI/systemd，单账号实例 | Web 管理 + 多账号实例统一编排 |
| 配置方式 | 文件 + TG 命令 | Web 图形化为主 + TG 命令为辅 |
| 插件机制 | listener 装饰器 + Hook + apt 风格仓库 | 完整借鉴，加上"账号粒度启用" |
| 状态分类 | active / failed / disabled | 沿用 |
| 命令前缀 | 默认 `,` | 沿用 + 可改 |
| 多账号 | 不原生支持 | 原生支持，N 账号 N worker |
| 跨账号转发 | — | **不支持**（按账号隔离） |
| 风控可配置粒度 | 基本无 | 按动作分桶 + 拟人化 + 冷启动 + FloodWait 自适应 |

## 十三、里程碑

1. **MVP（2–3 周）**：多账号绑定与隔离运行、Dashboard、自动回复（首个内置插件）、TG 命令骨架、基础风控（按动作分桶 + Queue 策略）
2. **V1（+2 周）**：功能矩阵 UI、转发（同账号内）、群管、定时
3. **V1.5（+2 周）**：插件市场（apt 风格）、Hook 体系、监控/归档、风控仪表盘 + 拟人化 + 冷启动 + FloodWait 自适应
4. **V2**：插件沙箱、备份/恢复云端化

---

## 参考

- PagerMaid-Pyro 主仓库：https://github.com/TeamPGM/PagerMaid-Pyro
- PagerMaid 官方插件源：https://github.com/TeamPGM/PagerMaid_Plugins_Pyro
- Pyrogram 文档：https://docs.pyrogram.org
