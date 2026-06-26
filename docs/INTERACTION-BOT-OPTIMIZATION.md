# TelePilot 双 Bot / 交互插件联动优化方案

本文用于统一 TelePilot 当前“UserBot / 交互 Bot / 通知 Bot / 插件”之间的职责边界、插件接入规范、前端展示结构与迁移步骤。TelePilot 的标准模式是个人可信插件模式：插件由管理员安装和启用后即视为可信，平台负责频控、审计、急停和通道代发，而不是按公共插件市场做强沙箱。目标不是推翻现有插件，而是在**不影响插件原有命令触发能力**的前提下，把管理员命令和群内高频玩法纳入同一套调度模型。

适用范围：

- 内置互动插件，如 24 点、十以内算数、九宫格猜骰、猜数字、诗词填空。
- 后续需要由交互 Bot 承接高频消息的远程/第三方互动插件。
- 账号 Bot 页面中的交互规则编辑器、运行时路由与中奖/发奖链路。

## 1. 目标与硬约束

### 1.1 目标

1. UserBot 作为主控感知层监听全量消息、处理管理员命令、确认收款和执行发奖；高频互动由交互 Bot 承担，减少 UserBot 本体暴露在高频群聊消息里。
2. 插件接入时不再为每个玩法重复维护一套规则字段。
3. 平台能稳定拿到“谁触发、谁中奖、奖金多少、由谁发奖、回复哪条消息”。
4. 前端能优雅承载不同插件的不同入口和不同参数，不把所有规则堆在同一页的同一层级。
5. 新插件作者只要遵守规范，就能低成本接入 TelePilot 的双通道调度：管理员命令走 userbot，群内玩法走交互 Bot，资金动作仍走 userbot。

### 1.2 硬约束

1. **不得影响插件原本的交互能力。**
   - 原有 UserBot 指令触发必须保留。
   - 交互 Bot 入口是新增能力，不是替代原命令。
2. **交互 Bot 不直接拥有发奖权限。**
   - 钱相关动作只能由 UserBot 账号代发或走平台受控结算流程。
3. **前端必须兼容 PWA / 移动端宽度。**
   - 交互规则页不能依赖大屏专属布局。
4. **插件层只关心业务逻辑。**
   - 收款人匹配、金额门槛、冷却、每日次数、触发来源过滤等，属于平台层。

## 1.3 标准调度方式

TelePilot 只保留一个标准模式，但插件入口可以有两种调度方式：

1. **管理员命令入口**
   - 管理员使用系统命令前缀触发，例如 `{prefix}game 100`。
   - 由 userbot 监听、触发和继续交互。
   - 适合低频管理、人工开局、查询状态、后台任务和需要人形身份的操作。

2. **群内玩法入口**
   - 群成员发送配置好的关键词，或转账命中规则后启动。
   - UserBot 负责监听、识别、确认收款和发奖；交互 Bot 负责题面、按钮、消息编辑、回调 ACK 等高频互动。
   - 适合多人游戏、抢答、抽奖和按钮玩法。

插件 manifest 可用兼容字段 `launch_mode`，也可用新字段 `dispatch_modes`、`message_channels`、`money_channel` 明确表达这件事。推荐写法：

```json
{
  "dispatch_modes": ["admin_command", "public_keyword"],
  "message_channels": {
    "admin_command": "userbot_reply",
    "public_keyword": "interaction_bot"
  },
  "money_channel": "userbot_reply"
}
```

## 2. 当前痛点

### 2.1 规则层和插件层职责混杂

当前最容易失控的点，是把插件自己的业务参数混进交互规则里。结果是：

- 每个插件都要在规则页维护一组不同字段。
- 同一页里既有平台通用项，又有插件专属项，信息层级混乱。
- 新插件接入时，平台需要持续认识“这个插件有哪些特殊字段”。

### 2.2 赢家和发奖结果缺少统一结构

如果平台只能从文本里猜“谁赢了、奖金多少、回复哪条消息”，就会出现：

- 插件之间结果无法隔离；
- 自动发奖或人工补发难以准确引用赢家消息；
- 日后加更多互动玩法时，维护成本指数上升。

### 2.3 前端把“规则”与“玩法入口”挤在同一层

不同插件的交互入口参数差异很大，但规则页当前仍容易给人一种“所有规则字段都应该平铺”的感觉。随着插件变多，问题会越来越明显：

- 用户先不知道自己是在配置“规则”，还是在配置“插件入口”；
- 技术字段（`module_key` / `module_action`）容易抢占主视觉；
- 移动端宽度下，选择器、文本域、动态表单容易堆叠得过密。

## 3. 总体设计原则

### 3.1 触发器与业务逻辑分离

平台负责：

- 群/关键词/转账通知匹配
- 金额与收款人限制
- 用户冷却、每日次数、会话路由
- 结算日志、发送通道执行

插件负责：

- 开局
- 生成题面/素材
- 校验答案
- 产出结构化结果
- 必要的插件内部状态与幂等

### 3.2 声明式接入，而不是平台硬编码插件特例

平台只识别插件声明的调度入口，不再为每个娱乐插件单独写一套规则表单逻辑。插件通过 Manifest 声明：

- 自己有哪些交互入口；
- 这些入口属于什么玩法类型；
- 平台可覆盖哪些参数；
- 会返回什么类型的结果；
- 管理员命令入口和群内玩法入口分别走哪个消息通道。

### 3.3 原命令不动，交互入口只做“桥接”

交互 Bot 的职责是承接群内高频互动，不是改写插件原本的命令语义。任何支持群内玩法入口的插件，都必须满足：

- 原本 `{prefix}24d 1000`、`{prefix}ct 1234` 之类的 UserBot 命令继续可用；
- 交互 Bot 命中规则后调用的是同一份业务逻辑或同一份业务内核；
- `preserve_command_trigger=true` 是硬规则。

## 4. 角色边界

### 4.1 交互 Bot

负责：

- 接群内高频消息
- 接规则触发事件（关键词、付费命中、手动开局等）
- 发送题面、答复、状态提示
- 执行平台允许的标准动作

不负责：

- 直接发奖
- 自行决定监听所有消息来源
- 绕过平台路由直接调用插件私有逻辑

### 4.2 通知 Bot

负责：

- 公告、通知、结果提示、对账提示
- 与交互 Bot 分开部署时承接低频通知场景

不负责：

- 高频群局承接
- 钱相关执行

### 4.3 UserBot

负责：

- 保留原插件命令触发能力
- 作为账号身份进行最终发奖、补发、回复赢家消息
- 监听必要公告并执行受控账号动作

不负责：

- 承担大规模群内高频抢答

### 4.4 插件

负责：

- 声明交互入口
- 实现 `on_interaction`
- 返回结构化动作与结构化结果

不负责：

- 解析 Bot Token
- 判断是不是正确收款人
- 自己做规则级冷却 / 每日限制
- 任意选择发送者

## 5. 统一交互契约

## 5.1 插件入口声明

插件需要通过 `interaction_entries` 声明可被 TelePilot 调度的入口。每个入口至少包含：

- `key`
- `title`
- `description`
- `interaction_profile`
- `launch_mode`
- `dispatch_modes`
- `message_channels`
- `money_channel`
- `events`
- `session_scope`
- `input_schema`
- `payload_contract`
- `result_contract`
- `settlement`
- `preserve_command_trigger`

推荐约定：

- 新入口名使用 `start_<plugin_key>`，避免继续扩散 `start_game` 这类泛化 key。
- 历史别名只在插件内部兼容，不再作为新规范推广。

### 5.2 玩法类型（interaction_profile）

该字段是**声明性元数据**，不改变原命令语义，只用于：

- 前端分类展示；
- 规则页筛选与提示；
- 插件作者理解该入口的接入形态。

当前约定值：

- `session_game`：群局抢答、竞猜、填空、算题、24 点、九宫格
- `challenge_game`：双人/多人对战
- `reward_pool`：红包、奖池、抽奖、下注开奖
- `utility_trigger`：只是借交互 Bot 做入口，但主体不是群局玩法

### 5.3 启动方式（launch_mode）

- `bridge`：由交互 Bot 收到事件，平台组装 payload 后调用插件
- `direct`：只保留原命令或插件内部调用，不依赖交互 Bot
- `hybrid`：同一能力同时支持桥接与原命令

推荐：

- 群局类娱乐插件优先 `hybrid`
- 纯工具触发可用 `bridge` 或 `direct`

### 5.4 会话边界（session_scope）

- `chat`：一群一局，群友共同参与
- `user`：同群不同用户互不影响
- `none`：一次性动作，不保存会话

注意：

- `session_scope` 是插件入口的会话边界；
- `concurrency` 是规则层的限流维度；
- 二者不能混用。

群局玩法漏写 `session_scope=chat`，就很容易出现“开局后群友回复没反应”。

## 6. 统一输入信封

平台调用插件 `on_interaction(ctx, entry_key, payload)` 时，应尽量统一提供以下信封：

- `source`
- `actor`
- `source_actor`
- `payment`
- `player`
- `reply_to`
- `trigger`
- `session`
- `settlement`

### 6.1 source

表示事件来源与消息通道，例如：

- 来自交互 Bot 的群消息
- 来自平台内部的规则命中
- 来自 UserBot 回调或平台补发链路

它用于：

- 路由
- 调试
- 审计

**它不等于赢家。**

### 6.2 actor

表示行为主体，也就是：

- 谁答题
- 谁点击
- 谁回复
- 谁应被记为赢家

答题、按钮点击、关键词触发等普通行为事件的主体以 `actor` 为准；付费开局和独玩权限绑定优先看 `player`。

付费开局还有一个独立的 `player` 信封。为了兼容旧插件，`payment_confirmed` 开局时 `actor` 会尽量映射为付款玩家；新插件应优先读取 `player`，并根据 `player.identity_confidence` 判断是否足够可信。`source_actor` 始终保留实际发出 Telegram 消息的一方，转账触发时通常是转账通知 Bot。

### 6.2.1 payment / player

转账联动采用“双证据”模型：

- UserBot/回复上下文负责补充“玩家是谁”，例如付款人回复 `+1000` 时的真实 `user_id`；
- 可信转账通知 Bot 负责证明“钱已到账”，包括金额、收款人和付款人显示名。

只有可信转账通知命中规则后，平台才会生成 `event.type=payment_confirmed` 和 `payment.status=confirmed`。普通群友发送 `+金额` 只是支付意图，不会直接启动付费玩法。

`player.identity_confidence` 当前可能是：

- `verified_user_id`：转账通知或结构化数据里已有付款人 user_id；
- `reply_context`：转账通知回复了付款人的原消息，平台从 reply_to 中取得 user_id；
- `callback_confirmed`：到账通知只有名称，付款人点击确认按钮后绑定 user_id；
- `name_only`：只有付款人名称，不足以安全限制独玩/按钮操作；
- `unknown`：无法识别。

入口可通过 `participant_policy` 声明后续参与边界：

- `open_race`：一人付款或关键词开局，全群可抢答；
- `solo_owner`：只有付款人/触发人能继续操作，适合 21 点、按钮个人局；
- `paid_pool`：只有已确认付费玩家池可参与；
- `notify_only`：只做通知或一次性动作。

`solo_owner` / `paid_pool` 在缺少真实 `player.user_id` 时，平台会先发送确认按钮，付款人点击后再启动插件；这样不会把转账通知 Bot 当成玩家，也不会把余额不足但未到账的 `+金额` 当成成功支付。

### 6.3 reply_to

表示插件结果应尽量引用的原消息。例如：

- 中奖公告回复赢家答案消息
- 后续 UserBot 发奖时继续引用同一条答案消息

### 6.4 trigger

表示为什么这次调用发生，包括：

- 命中的规则 ID / 名称
- 插件 key
- 入口 key
- 触发事件类型
- 原始命中参数

### 6.5 session

表示：

- 当前会话 key
- 会话范围
- TTL
- 是否新建
- 关联状态

### 6.6 settlement

表示平台给本次调用附带的结算上下文，例如：

- 本局奖金
- 预期发奖模式
- 预期发奖账号标签

它是上下文，不是执行权。

## 7. 统一输出结果

插件返回平台标准动作列表，典型动作包括：

- `send_message`
- `send_photo`
- `send_file`
- `delete_message`
- `pin_message`
- `answer_callback`
- `result`
- `end_session`

`send_message` 可携带 Bot API `reply_markup`（inline keyboard）。按钮点击会以 `callback_query` 事件回到对应活跃会话，payload 会同时包含 `callback_query_id`、`callback_data` 和原按钮消息的 `message_id` / `message_text`。新插件推荐用 `ctx.messages.send/edit/delete/pin/answer_callback` 生成这些动作，而不是直接调用 Bot API。

### 7.1 标准动作与发送通道

动作可带 `send_via`，但必须命中入口声明的白名单。当前推荐白名单只有：

- `interaction_bot`
- `userbot_reply`
- `bbot_notice`

解释：

- `interaction_bot`：交互 Bot 发群内题面/答复/题图
- `userbot_reply`：由账号 worker 的 UserBot 代发
- `bbot_notice`：由通知 Bot 发公告

这里的关键点是：

> `send_via` 是“平台允许的发送通道”，不是“插件自由指定任何发送者”的能力。

运行时会按 `result_contract` 执行守卫：未声明 `send_via` 时按个人可信插件标准允许 `interaction_bot`、`userbot_reply`、`bbot_notice` 三个受控通道；声明了 `actions` 时，未声明动作会被丢弃；显式收窄 `send_via` 后，越权通道会写入 runtime log；`reply_markup` 只透传给 `interaction_bot` / `bbot_notice`，不会交给 `userbot_reply`。

## 7.2 结果与结算分离

建议把插件返回拆成两层：

### result

用于平台理解业务结果，至少应包含：

- `status`
- `winner_user_id`
- `winner_name`
- `winner_message_id`
- 业务结果细节（答案、题面、轮次、分数等）

### settlement

用于平台理解发奖和对账语义，至少应包含：

- `mode`
- `amount`
- `winner_user_id`
- `winner_name`
- `payout_account_label`
- `status`

### 7.3 为什么必须分离

这样做可以同时解决三个问题：

1. 平台不用再从文本里猜谁赢了；
2. 不同插件的中奖结果可以按 `feature_key + entry_key + session_key` 隔离记录；
3. 后续不管是自动发奖还是人工补发，都能直接引用赢家消息与结构化赢家信息。

### 7.4 平台读取边界

平台不再在账号详情的交互通道页提供“最近互动结果”列表，也不再暴露专用的 `interaction-results` 查询接口。
原因是当前发奖链路已经由当次中奖公告、赢家消息引用和 userbot 自动发奖监听完成；再额外保留一份最近结果列表会让用户误以为需要手工二次核对，也会让页面承担过多运维职责。

插件仍然可以返回结构化 `result` / `settlement` 动作：

- `winner_user_id`
- `winner_name`
- `winner_message_id`
- `amount`
- `payout_account_label`

这些字段只用于当次动作、结算公告、运行日志和后续受控发奖链路；前端规则页不再展示最近结果历史。

## 8. 对“消息带来源、插件自由选择监听谁/由谁发送”的分析

这个思路**方向是对的，但不能完全放给插件自由发挥**。

### 8.1 可采纳的部分

平台确实应该在 payload 里带来源信息，例如：

- `source.channel`
- `source.actor_kind`
- `source.bot_role`
- `source.message_origin`

插件也确实应该能在结果动作里声明“希望由哪个受控通道发送”，例如：

- `send_via=interaction_bot`
- `send_via=userbot_reply`

### 8.2 不可直接放开的部分

不建议让插件自己随意决定：

- 我要监听所有谁发来的消息；
- 我要指定任何 bot / 账号去发送。

原因：

1. 会破坏平台层的风控与审计边界；
2. 插件作者容易绕过原本的双 Bot 分工；
3. 后续排查“为什么这个插件突然自己发消息/发奖”会很难。

### 8.3 正确落点

更稳妥的做法是：

1. **平台注入来源元数据**，插件只读取，不自己抢订阅。
2. **插件声明允许的接收形态与发送通道**，平台负责执行。
3. 如有必要，可在入口声明层新增类似：
   - `accepted_sources`
   - `result_contract.send_via`

但依旧由平台校验与裁决。

一句话总结：

> 可以“带来源”，也可以“声明希望由谁发送”，但不能把消息监听权和账号发送权无边界地下放给插件。

## 9. 规则层如何统一

规则层只保留平台通用项，不再为每个插件做特化。

建议规则层长期固定为以下几组：

### 9.1 触发条件

- 规则开关
- 群 Chat ID
- 触发方式：`payment` / `keyword` / `both`
- 触发文本 / 插件启动关键词

### 9.2 付费与收款限制

- 金额门槛
- 金额匹配方式
- 收款人用户 ID
- 收款人用户名/名称辅助匹配

### 9.3 运行与限流

- 有效期
- 用户冷却
- 每日次数
- 并发维度

### 9.4 插件入口绑定

- `module_key`
- `module_action`
- 平台统一管理的 `prize`
- 平台统一管理的会话与启动文案

### 9.5 高级设置

- 开启 / 关闭 / 状态命令
- 关闭提示

### 9.6 哪些参数不该放规则层

以下应留在插件入口 `input_schema`：

- 题面渲染样式
- 选项数量
- 题库类别
- 特殊玩法模式
- 素材模板
- 插件私有开关

这样平台规则可以长期稳定，插件作者只要维护自己的 `input_schema` 即可。

## 10. 前端展示方案

前端的核心问题不是“字段太多”，而是“层级不清”。建议交互规则页长期按下面的结构演进。

## 10.1 信息架构

每条规则拆成六层：

1. **规则卡头**
   - 规则名
   - 启停状态
   - 玩法标签
   - 快捷复制 / 删除 / 排序
2. **触发条件卡**
   - 群、关键词、付费命中
3. **玩法入口卡**
   - 选择插件入口
   - 展示玩法简介、启动方式、是否保留命令触发
4. **入口参数卡**
   - 只渲染 `input_schema`
   - 平台已托管字段不重复渲染
5. **运行与结算卡**
   - 奖金、有效期、冷却、并发
6. **高级设置折叠卡**
   - 技术详情、开关命令、关闭提示

## 10.2 入口选择视图

不要只把插件入口做成一个纯下拉框。建议逐步演进到：

- 先按 `interaction_profile` 做一级筛选
- 再在筛选结果里选入口卡片
- 卡片上展示：
  - 显示名
  - 所属插件
  - 玩法标签
  - `hybrid / bridge / direct`
  - “保留命令触发”
  - 是否存在命令回退

这样用户会更清楚自己是在选择“玩法入口”，不是在填神秘技术字段。

## 10.3 技术字段降级展示

`module_key` / `module_action` 应始终放在：

- 折叠区
- 只读技术详情

而不是作为主要输入控件暴露给普通用户。

## 10.4 移动端 / PWA 约束

移动端下建议坚持：

1. 一次只展开一条规则；
2. 大段说明文案放在卡片说明，不要夹在表单流里；
3. 动态表单字段单列堆叠；
4. 多个 Badge 自动换行；
5. 技术详情默认折叠；
6. 规则列表与规则详情分层，不做大宽表。

## 11. 对插件作者的规范要求

后续同步到插件仓库时，建议把以下几条写成明确规范：

1. 需要交互 Bot 承接的插件才声明 `interaction_entries`。
2. 所有声明入口的插件必须实现 `on_interaction`。
3. `preserve_command_trigger=true` 必须成立。
4. 群局插件必须显式写 `session_scope=chat`。
5. 入口名推荐 `start_<plugin_key>`。
6. 返回结果必须带结构化赢家信息，不依赖文本解析。
7. 涉及奖金的入口必须写 `settlement`，但不能在插件里直接发钱。
8. `send_via` 只能命中声明白名单。
9. 使用 inline keyboard 时必须声明 `callback_query` 事件，并通过 `send_message.reply_markup` 返回按钮。
10. 业务逻辑可抽共享函数，UserBot 命令与交互入口共同调用。

## 12. 迁移步骤与 0.33 落地状态

### 阶段 1：平台契约收口

- 已落地统一 payload 信封。
- 已落地统一动作结果。
- 已落地 `result_contract.actions` 与 `result_contract.send_via` 运行时守卫；未主动收窄时按可信插件标准允许三通道代发。
- 已落地 `settlement` 结构化记录。

### 阶段 2：插件声明升级

- 内置互动插件已按 `interaction_entries` 暴露入口。
- 已验证的 installed 互动插件通过 `scripts/validate-installed-interaction-plugins.py` 做契约对齐检查。
- 历史入口名仅保留必要兼容，新入口推荐 `start_<plugin_key>`。

### 阶段 3：前端交互框架入口

- 已新增独立「交互框架」顶级页面，和 AI 一样作为 TelePilot 内的重要框架入口。
- 「交互」中心承载具体规则配置，账号详情的「交互通道」页只维护 Bot 凭证、通知模板等账号级通道设置。
- 远程插件仓库已支持在插件页直接刷新列表。

### 阶段 4：动作执行与结算链路稳定化

- `app.services.interaction.contracts` 负责插件主动收窄时的动作契约守卫。
- `app.services.interaction.delivery.InteractionDeliveryExecutor` 负责发送、编辑、删除、置顶、按钮 ACK、媒体发送和 message_id 保存。
- 自动发奖与人工补发继续读取结构化 `result/settlement`，交互 Bot 不直接执行钱相关动作。

### 阶段 5：同步插件仓规范

- 插件 API 参考、速查表、远程插件规范、安全边界和本文已同步个人可信插件标准模式、两类调度入口、`ctx.messages`、按钮回调和发送通道。
- `examples/plugins/with_interaction` 作为最小交互插件骨架继续由校验脚本覆盖。
- installed 插件兼容清单由 `scripts/validate-installed-interaction-plugins.py` 自动发现并校验。

## 13. 验收标准

完成后，应至少满足：

1. 原有 UserBot 命令触发仍然可用；
2. 任一声明交互入口的插件都能在规则页被选中；
3. 规则页只维护平台通用项，插件私有参数走 `input_schema`；
4. 中奖结果能结构化得到：
   - 谁赢了
   - 奖金多少
   - 赢家消息 ID
   - 由谁发奖
5. 交互 Bot 不直接执行钱相关动作；
6. 移动端宽度下规则页无重叠、无不可读表单。

## 14. 当前仓库状态

截至 0.33.0，这套方案已经从“交互 Bot 功能块”收口为 TelePilot 的交互框架：

- 平台采用统一输入信封；
- 插件可通过 `ctx.messages` 生成受控动作；
- 运行时按 `result_contract` 守卫动作类型和发送通道；
- delivery executor 已从账号 Bot 大 runtime 中拆出；
- 前端已有独立交互框架页；
- 插件开发文档已经同步最新框架和迁移路径。

后续版本可以继续增强可观测性、发奖工作流和更多玩法模板，但不再需要推翻这套框架。
