# TelePilot 使用指南

> 面向使用者，不面向插件开发者。本文按当前版本代码编写，界面入口与本文不一致时以界面为准。
> 需要二次开发或写插件请看 [CONTRIBUTING.md](../CONTRIBUTING.md) 与 [PLUGIN-DEV-GUIDE.md](./PLUGIN-DEV-GUIDE.md)。

TelePilot 是一套自托管的 Telegram 账号管理与自动化控制台：一个 Web 控制台 + 一个 Telegram 账号 worker，加上
按需加载的插件。数据全部在你自己的服务器上（PostgreSQL + Redis），模型调用由你配置的 Provider 决定。

---

## 1. 安装与首次启动

三条路径，按熟练程度选一条即可。

### 路径一：一键安装（推荐，VPS）

```bash
curl -fsSL https://raw.githubusercontent.com/Anoyou/telebot/main/scripts/install-server.sh | bash
```

脚本会安装依赖、Docker Compose v2 与 GitHub CLI，拉取仓库到 `/opt/telepilot`、生成生产 `.env`、
从 GHCR 拉取预构建的 AMD64/ARM64 应用镜像，并启动 `postgres` / `redis` / `web` / `updater` / `frontend`。
启动前会校验镜像的 OCI source 与当前 checkout 一致；端口被占用时自动递增到可用端口。

细节与自定义安装见 [DEPLOY-PUBLIC.md](./DEPLOY-PUBLIC.md) 的「一条命令安装」。

访问地址与后续运维（HTTPS、`make prod-update`、回滚）见 [DEPLOY-PUBLIC.md](./DEPLOY-PUBLIC.md)。

### 路径二：本地源码运行

```bash
git clone https://github.com/Anoyou/Telebot.git
cd TelePilot
make dev              # 本地开发栈：数据库 + Redis + 后端 + 前端
```

`make dev` 会在 `http://localhost:5173` 提供前端，后端在 `:8000`。

### 路径三：如果你在容器里

先确认部署形态，再决定更新方式：

- **容器 + 内网 updater**：Web「检查更新」能直接比对远程差异，可在面板里一键更新。
- **容器 + 手动部署**：面板会给出需要在宿主机执行的命令，复制执行即可。

首次登录请立刻做三件事：

1. **改默认密码**：服务器设置 → 账号与安全 → 账户安全。
2. **开启 TOTP / 登录风控**：服务器设置 → 账号与安全 → 登录安全（可按需打开 OTP、失败锁定）。
3. **确认更新远端与分支**：检查更新弹窗里的「Git 远端 / 检查分支」，默认跟随部署时的分支。

---

## 2. Web 界面导览

左侧导航分三组：最上面固定一行 **Agent**（红点是待确认数，点开 Agent 后右上角「待确认」进入处理页）；
「常用」默认是插件、交互、概览；「管理」是账号、AI、指令与任务、资金台账、日志；「工具与设置」（可折叠）是 Webhook、命中调试、服务器设置。
「常用」放哪几个由你在「服务器设置 → 界面偏好 → 常用入口」里选（最多 4 个），桌面侧栏第一组和手机底栏共用这一份；手机底栏是常用入口 + 「更多」+ 右侧 Agent 圆钮，其余入口都在「更多」里。
下面是各区域**做什么**、**先配哪里**。

### 概览 `/overview`

系统体检一页看完：DB 连通与 alembic 版本、Redis、LLM Provider 池、代理、账号 worker 状态分布。
任何一项标红都别急着改业务配置，先在这里定位是基础设施还是配置问题。

### 插件 `/plugins`

插件中心。上传 ZIP、浏览可安装插件、启停、查看权限声明与事件订阅。

- 上传的 ZIP 超过上限会直接返回 **413**，不会被完整读进内存；上限见 `settings.plugin_zip_max_bytes`。
- 插件可声明 `requires_platform_capabilities`（`ai`、`interaction_bot`、`webhooks` 等）。
  平台按能力开关裁剪入口，多通道插件不会被整体误停。规则见 [PLATFORM-CAPABILITIES.md](./PLATFORM-CAPABILITIES.md)。
- 远程插件仓库的自动更新检查与「插件」页面里的手动更新是两套开关，互不影响。

### AI `/ai`

所有模型相关配置都在这里，顺序建议：**Provider → 模型 → 密钥池 → 路由模板**。

- **新建模型提供商**：填 Base URL、协议与服务类型；「Magpie 网关」快捷模板会预填 `http://127.0.0.1:3425/v1`。
- **获取模型列表**：除模型 ID 外还会解析上游 `/v1/models` 的元数据（上下文窗口、最大输出、是否支持图片、
  推理档位、`responses` 协议）。合并规则是「只填空缺」，你手改过的 `label` / `enabled` 永远保留。
- **密钥池**：同一 Provider 挂多把 Key（最多 32 把），支持优先级、轮转、最少使用三种策略，
  以及 Key 级每日请求/Token 上限、独立出口代理、Key 级冷却。完整规则见 [LLM-KEY-POOL.md](./LLM-KEY-POOL.md)。
- **近期调用**：每次调用记录命中的 `provider_key_label`，空表示用了主密钥。System Agent 等结构化调用还展示缓存读取/写入 Token；上游未返回的缓存字段按 0 记录，旧接口及历史记录显示“未采集”。各协议的输入统计口径不同，暂不计算缓存命中率。

> **Provider 可用性判断**：列表徽标区分「主 Key / 池内 Key / 未配置」。只配了池内 Key 的 Provider 同样算可用。

### 待确认 `/assistant/inbox`

System Agent 的 Action 确认箱。开启 `require_tool_approval` 后，写操作会先在这里等你批准。
Provider 密钥校验、远程插件更新等敏感 Action 也走同一通道。

### 交互 `/interaction`

Telegram 账号的自动化规则与分组。账号的 userbot / interaction bot 行为在这里编排。

### 指令与任务 `/operations`

命令模板、定时任务与调度。可复用 AI 指令、插件命令与账号动作。

### 入站 Webhook `/webhooks`

给每个账号/hook 生成一个公网 URL，外部系统 POST 即可触发 TelePilot。

- 响应语义是**已受理（accepted）**：`202` 只表示事件被投递到账号 worker，**不代表插件执行成功**。
- 响应里的 `trace_id` 是唯一线索，`matched` / `invoked` / `failed` 是投递统计；
  `failed > 0` 时会记 warning 日志。排障请拿 `trace_id` 去「日志」页查执行细节。

### 命中调试 `/dispatch-debug`

一条消息进来后命中了哪些规则、为什么命中/没命中。交互规则不生效时先来这里看。

### 日志 `/logs`

账号 worker 的 runtime 日志与风控事件。runtime 日志是**有界队列**（约 5000 条），满了覆盖最旧的，
只用于观测，不作为审计依据。

### 服务器设置 `/settings`

「账号与安全」放管理员账户、登录安全与 Sudo；「平台与模块」放指令前缀、平台模块开关、时区与日志保留；
「连接与通知」放代理、登录设备（账号登录 Telegram 时上报的机型、系统、App 版本和语言）与通知渠道；「限流与风控」放限流模板、风控与预算和紧急停止状态；
「备份与维护」做备份与恢复；「界面偏好」选常用入口、调侧栏组内顺序。

---

## 3. Telegram 通道

### 账号接入

账号以 session 方式接入，Model 里是账号 worker 与它承载的账号列表。userbot 与 interaction bot 是两种角色：

- **userbot**：以你的账号身份收发，规则与交互编排对它生效。
- **interaction bot**：独立的机器人账号，适合需要「以机器人身份」回复/确认的场景。

### 交互规则

交互规则按「入口声明 → 事件订阅 → 编排」组织。命中顺序可在「命中调试」里回放验证。
插件订阅的事件以 loader 派发结果为准，与上面的 Webhook 一样遵循已受理语义。

### 命令与 Action 确认

- userbot 的 `,ai` 默认只携带当前问题；在 Web「系统」设置页的“AI 指令上下文条数”中设为 1–20，或执行 `,aictx <0-20>`，可为所有账号开启最近聊天上下文，设为 0 关闭。历史按时间正序拼装，排除命令和空消息，发送前脱敏；当前问题始终位于最后。实际命令前缀跟随系统配置。
- 普通斜杠命令（`/status`、`/rules` 等）始终优先于助手模式。
- 敏感操作（密钥保存、远程插件更新、写操作）会走 Action 二次确认：
  待确认项进入 `/assistant/inbox`， Bot 侧也可用 `/agent pending` 查看。
- Action 过期后密文会被清除，确认链接不会长期可点。

### 访客模式（Guest Mode，Bot API 10.0）

Telegram Bot API 10.0（2026-05-08）新增访客模式：Bot 在 BotFather 开启 “Guest Mode” 后，可以在**没有加入**的群聊或私聊里被 `@提及`（或被回复）时回复**一次**，且看不到该会话的历史消息。

开启与使用：

1. 在 Telegram 打开 [@BotFather 的 Mini App](https://t.me/BotFather?startapp)，进入对应 Bot 的设置并启用 “Guest Mode”。管理 Bot 和交互 Bot 各自独立开关。
2. 在 Web「账号 → Bot 联动」（管理 Bot / 交互 Bot 页）或 iOS「账号管理 → 访客模式」查看“访客模式（Guest Mode）：已开启/未开启”，开启后点“重新检测”。状态来自 `getMe.supports_guest_queries`，接口为 `GET /api/accounts/{aid}/bot/capabilities`。
3. 在任意聊天输入 `@你的Bot用户名 内容`，或直接回复该 Bot 之前发出的消息。

不同 Bot 的处理方式：

| 被召唤的 Bot | 谁能用 | 进入的能力 |
| --- | --- | --- |
| 管理 Bot | 仅 Bot 用户与权限里已授权的 Telegram 用户 | 斜杠命令（如 `@管理Bot /status`）；开启系统助手模式后，自由文本交给系统助手回答。未授权用户的 `@` 被忽略，`/start` `/help` 只返回授权提示。 |
| 交互 Bot | 任何人 | 交互规则的查询指令、开关/状态指令和关键词入口（仍按规则自身的会话范围判断，全局规则才会在陌生会话生效）；以及订阅了 `guest_message` 的 Event Bus 插件。 |

限制：

- 每条访客消息只能回复一次。TelePilot 会把处理过程中的所有发送/编辑合并为最终的一条消息（占位“处理中…”会被最终结果替换），处理结束后通过 `answerGuestQuery` 发送；AI 等耗时流程会在得出结果后才回复，期间没有流式预览。
- 回复上限 4096 字，超长会截断；HTML 解析失败时自动退回纯文本。平台自己合成的访客回复（命令、AI 助手、规则）会去掉回调按钮，只保留 URL / 复制 / 切换 Inline 按钮；插件用 `answer_guest_query` 显式给的回调按钮（如 AI 答疑失败时的「重试」「换模型重试」）从 0.106.0-beta.17 起保留，点击后交给订阅了访客按钮（`guest_all` 的 `callback_query`）的插件处理并原地编辑这条回复。
- 图片、文件无法作为访客回复发送（只保留说明文字）；付款/发奖、置顶、按钮 ACK、转账指令和付款通知在访客场景下一律跳过并记录 `guest_action_unsupported`。
- 访客会话不会写入授权用户的“最近会话”，也不会成为通知投递目标；访客消息不会续接交互会话（游戏等多轮玩法）。
- 每个调用者每分钟最多 6 次、每个 Bot 每分钟最多 60 次，超出会被忽略并记录 `guest_rate_limited`。
- 系统设置的“访客消息（Guest Mode）”运行开关关闭后，平台不再拉取 `guest_message`。

---

## 4. System Agent（系统助手）

平台级自然语言助手，用已有能力回答与执行操作，自己不做新的持久化。

### 三种入口

| 渠道 | 怎么用 |
| --- | --- |
| Web | 任意页面右下角「系统助手」悬浮球 |
| 管理 Bot | `/agent`、`/agent <问题>`，或助手模式下的自由文本 |
| API | `/api/system-agent/*` |

### 开启前的准备

1. AI 中心至少配好一个**支持 tools** 的 Provider/模型。
2. 打开右下角「系统助手」悬浮球 →「配置」：开启、选 Provider/模型、决定是否填 `fallback_provider_ids`。
3. 需要写操作审批就把 `require_tool_approval` 打开（默认关闭）。

模型第一次进入 Agent 前会接受一次真实的**强制工具调用探测**：只有返回指定工具及正确参数才会放行。
探测结果会缓存（支持 7 天、明确不支持 30 分钟、429/5xx 只算暂时不可用缓存 5 分钟），
所以换模型后第一次对话可能稍慢，之后就走缓存。

### 常用 Bot 命令

| 命令 | 行为 |
| --- | --- |
| `/agent` | 进入助手模式并显示帮助 |
| `/agent <自然语言>` | 直接提问 |
| `/agent pending` | 列出待确认 Action |
| `/agent new` / `/agent clear` | 新建 / 删除会话 |
| `/agent stop` / `/agent resume` | 停止 / 恢复排队任务 |
| `/agent approve` / `/agent reject` | 批准 / 拒绝当前工具调用 |
| `/agent exit` | 退出助手模式（保留历史） |

助手模式标记 30 分钟滚动刷新；既有斜杠命令始终优先。能力与工具清单见
[SYSTEM-AGENT.md](./SYSTEM-AGENT.md)。

### 会话、记忆与技能

- **会话**：Web 与 Bot 共用会话模型，可新建、切换、删除。Web 与 iOS 删除单个会话时，服务器会检查全部执行中、等待补充、等待审批和排队（含暂停队列）任务；仍有未结束任务时返回 `409 SESSION_BUSY`，需先停止或处理任务后再删除。Web/iOS 不能删除 Telegram Bot 会话。
- **长期记忆**：`/api/system-agent/memory` 提供 CRUD；超长会话会自动做摘要压缩（后台任务，无需干预）。
- **技能与工具**：模型可选当前注册的技能与工具；运行时最多 16 个工具参与一轮。

---

## 5. 密钥池与 Magpie

密钥池让同一 Provider 挂多把 Key，把限流/凭据失败的代价压到一把 Key 上。要点：

- **策略**：`order`（默认，永远先试最小优先级）/ `rotate`（轮转起点摊薄并发）/ `least_used`（按当日用量从少到多）。
- **冷却**：只有鉴权失败（401/403）与限流/额度耗尽（429）会换 Key，冷却时长 30s / 10min / 指数递增封顶 10min；
  冷却中的 Key 排到末尾而不是剔除。流式只在收到第一个增量前允许换 Key。
- **亲和**：同一对话尽量粘在同一把 Key 上，让上游 prompt cache 继续命中（30 分钟有效）。
- **用量上限**：池内 Key 可设每日请求/Token 上限，超限当日不再参与选钥；全部超限且无主密钥时该 Provider 本轮不可用。

[Magpie](https://github.com/yetone/magpie) 默认监听 `127.0.0.1:3425`。在「新建模型提供商」的 Base URL 快捷模板里
点「Magpie 网关」即可预填地址、协议与服务类型，API Key 填网关自身的 Key。网关返回的 `provider/model` ID、
上下文窗口与推理档位会随「获取模型列表」一并写入。规则细节见 [LLM-KEY-POOL.md](./LLM-KEY-POOL.md)。

---

## 6. 自动检查更新

自动检查只做**检查 + 提示**，不会自动拉取代码或重启服务。

- 顶栏齿轮旁的「检查更新」按钮打开更新面板；服务端按你设置的周期在后台检查。
- 面板里的「自动检查更新」可开关，频率可选 6 小时 / 12 小时 / 每天 / 3 天 / 每周，或自定义 1~168 小时。
- 后台检查发现新版本时，顶栏「检查更新」按钮会出现圆点角标，标题变成「检查更新（发现新版本）」。
  容器内无法自动比对 Git（`can_check: false`）时不显示角标，避免把"无法检查"误报成"有新版本"。
- 「保存并检查」会立刻跑一次检查并记住你选的远端/分支；检查和实际更新用的是同一目标分支。

更新流程、updater、回滚见 [DEPLOY-PUBLIC.md](./DEPLOY-PUBLIC.md)。

---

## 7. 常见问题与排障入口

| 现象 | 先看哪里 |
| --- | --- |
| web 容器起不来 / unhealthy | 数据库 DSN 与 `alembic upgrade head`；看 `make dev` 或 compose 日志 |
| Provider 显示未配置 Key | 只有池内 Key 时也算可用；看徽标是「主 Key / 池内 Key / 未配置」 |
| 模型能连但 Agent 不放行 | 强制工具调用探测没过；换支持 tools 的模型，或用 `/agent status` 看列举的工具 |
| Webhook 触发了但插件没反应 | 响应是已受理语义；拿 `trace_id` 去「日志」页查，看 `failed` 计数 |
| 交互规则不生效 | 「命中调试」回放一条消息，看命中顺序与入口声明 |
| 日志被刷没了 | runtime 日志是有界队列（约 5000 条），不是审计日志 |
| 更新后面板 502 | updater 正在重启 web；等任务轮询恢复，或看「检查更新」面板里的任务日志 |
| 想把 Key 换成占位符发给上游 | 已在 System Agent 聊天场景落地；详见 [SYSTEM-AGENT.md](./SYSTEM-AGENT.md) 的密钥与隐私 |

更深入的材料：

- 部署与更新：[DEPLOY-PUBLIC.md](./DEPLOY-PUBLIC.md)
- 密钥池与模型元数据：[LLM-KEY-POOL.md](./LLM-KEY-POOL.md)
- System Agent 全貌：[SYSTEM-AGENT.md](./SYSTEM-AGENT.md)
- 插件安全边界：[PLUGIN-SAFETY.md](./PLUGIN-SAFETY.md)
- 安全运维口令：[SECURITY-OPS.md](./SECURITY-OPS.md)
- 平台能力开关：[PLATFORM-CAPABILITIES.md](./PLATFORM-CAPABILITIES.md)
- 版本变化：[CHANGELOG.md](../CHANGELOG.md)
