# Sprint 4 — Wave 2：核心功能补完 + 公网部署

> 工时：约 4-5 天
> 依赖：Wave 1 完成（合并到 main 后开始）
> 优先级：分 5 子任务，可并行

## 0. 子任务概览

| ID | 任务 | 工时 | 串/并 |
|----|------|------|-------|
| **2A** | 砍掉 group_admin / monitor 插件 + 简化插件市场 UI | 半天 | 先做（避免 2B/2C 撞文件） |
| **2B** | 写《Telebot 插件开发指南》+ 移植样例 | 1 天 | 与 2C/2D 并行 |
| **2C** | 定时任务 plugin 业务实装 | 2 天 | 与 2B/2D 并行 |
| **2D** | 多 Bot 接入（项目通知 / 故障告警） | 1.5 天 | 与 2B/2C 并行 |
| **2E** | Caddy 反代 + HTTPS 部署文档 | 半天 | 与 2B/2C/2D 并行 |

**Wave 2 交付后 bump 0.4.0**（MINOR — 加了定时任务 + 多 Bot + 文档；删了 group/monitor）

---

## 2A. 砍空架子（先做，半天）

### 目的
- group_admin / monitor 是 PRD 列出但你不需要的功能；当前 20 行骨架占着 builtin 目录干扰阅读
- 插件市场 UI（plugin_repo 远程订阅 + zip 上传）你不会用，砍掉简化代码
- 保留 Plugin 目录扫描机制（这是开发框架的核心）和"已安装本地插件管理"

### 文件白名单（删除）
- `backend/app/worker/plugins/builtin/group_admin/` 整目录
- `backend/app/worker/plugins/builtin/monitor/` 整目录
- `backend/app/api/plugins_install.py` 中跟 `plugin_repo` 远程订阅相关的端点（保留本地 `installed/` 列表）
- `backend/app/services/plugin_repo_service.py` 整文件
- `frontend/src/pages/Settings/PluginMarket.tsx` 整文件
- `backend/alembic/versions/0012_drop_plugin_repo.py`（**新建迁移**）：`DROP TABLE plugin_repo`

### 文件白名单（修改）
- `backend/app/db/models/plugin.py` — 删 `PluginRepo` 类
- `backend/app/db/models/__init__.py` — 删 import
- `backend/app/api/plugins_install.py` — 保留 `GET /api/plugins/installed` / `POST /enable` / `POST /disable` / `DELETE /{key}`；删 `/repos*`、`/install/from-repo*`、`/install/upload`（zip 上传也砍）
- `backend/app/main.py` — 不动 router 注册（仅是端点缩水）
- `frontend/src/api/plugins.ts` — 删 repo 相关函数
- `frontend/src/pages/Settings/PluginManager.tsx` — 改成"已加载插件列表 + 启用/禁用"，去掉"上传"和"市场"按钮
- `frontend/src/pages/Settings/Index.tsx` — 删 `<PluginMarket />` 嵌入
- `frontend/src/App.tsx` — 删 `/settings/plugin-market` 路由
- `backend/app/db/__init__.py` 之类涉及 metadata 的 import — 一并清

### 替代方案：本地 git clone 装插件

文档里说明：

```bash
# 装一个第三方插件
cd /path/to/telebot/data/plugins/installed
git clone https://github.com/somebody/my-telebot-plugin.git
# worker 重启后自动扫描加载

# 卸载
rm -rf my-telebot-plugin
# worker 重启
```

把这个写进 2B 的开发指南。

### 验收
- `pytest -q` 全绿（删除后测试别红）
- 启动 worker 后只剩 3 个 builtin：auto_reply / forward / scheduler（scheduler 在 2C 里实装）
- `/settings/plugins` 页面只显示"已加载列表 + enable/disable"
- 数据库 `plugin_repo` 表已删

### 2-A 完成报告
- 已删除 `group_admin` / `monitor` builtin 目录与插件市场页面、仓库 service。
- `api/plugins_install.py` 已删除 `/repos*`、`/install/from-repo*`、`/install/upload`，保留 installed 列表与 enable/disable/uninstall。
- `PluginManager.tsx` 已简化为“已加载列表 + enable/disable（含卸载）”。
- 已新增 Alembic 迁移 `0012_drop_plugin_repo.py`，执行 `DROP TABLE IF EXISTS plugin_repo CASCADE`。
- 同步清理了 repo 相关前后端引用，保证构建与测试路径一致。

---

## 2B. 《插件开发指南》+ 移植样例（1 天）

### 文件白名单（新建）
- `docs/PLUGIN-DEV-GUIDE.md`（约 400 行）
- `examples/plugins/translate/` — 第一个移植样例
  - `__init__.py`
  - `manifest.py`
  - `plugin.py`
  - `README.md`（说明源出处 + 移植决策）

### 开发指南大纲（PLUGIN-DEV-GUIDE.md）

```markdown
# Telebot 插件开发指南

## 0. 这是什么 / 不是什么

**是**：让你给 Telebot 加新功能而不改主代码。一个插件 = 一个独立目录。
**不是**：PagerMaid 插件市场。我们不订阅远程仓库，也不兼容 Pyrogram 插件。

## 1. 目录结构（最小骨架）

    my-plugin/
      __init__.py     # 出 PLUGIN_CLASS 和 MANIFEST 两个常量
      manifest.py     # MANIFEST 定义
      plugin.py       # 实际逻辑

## 2. Manifest 字段

(完整字段表格 + 例子，参考 worker/plugins/manifest.py)

## 3. Plugin 基类钩子

- on_startup(ctx)        — 加载时调一次（注册外部资源、起协程）
- on_shutdown(ctx)       — 卸载时调一次
- on_message(ctx, event) — 收到 incoming 消息
- on_command(ctx, cmd, args, event) — 命令派发（自己发的 outgoing）

ctx 字段速查：account_id / feature_key / config / rules / client / engine / redis / log

## 4. 风控 / 限流接入

任何主动发消息前调：
    decision = await ctx.engine.acquire("send_message_group", peer_id=event.chat_id)
    if not decision.allowed: return

## 5. Telethon API 速查（最常用）

- await event.reply(text)              # 引用回复
- await event.respond(text)            # 直接发新消息
- await event.edit(text)               # 编辑（仅自己发的）
- await client.send_message(peer, text)
- await client.iter_messages(peer, limit=N, from_user=me)
- await event.get_reply_message()      # 取被回复的消息
- await event.get_chat()

## 6. 安装与启用

本地装：把目录丢到 data/plugins/installed/<key>/，重启 worker
内置：放在 backend/app/worker/plugins/builtin/<key>/

## 7. 从 PagerMaid 插件移植（看菜谱）

PagerMaid 插件用 Pyrogram，我们用 Telethon，**API 不兼容但逻辑可借鉴**。

### 速查映射

| PagerMaid (Pyrogram)            | Telebot (Telethon)              |
|---------------------------------|---------------------------------|
| @Client.on_message              | Plugin.on_message               |
| filters.command(["xxx"])        | on_command(cmd, args, event)    |
| filters.regex(r"...")           | match in on_message + re.search |
| client.send_message             | await ctx.client.send_message   |
| message.reply_text              | await event.reply(text)         |
| message.edit                    | await event.edit(text)          |
| message.text                    | event.raw_text                  |
| message.from_user.id            | event.sender_id                 |
| message.chat.id                 | event.chat_id                   |
| message.reply_to_message        | await event.get_reply_message() |
| pyrogram.errors.FloodWait       | telethon.errors.FloodWaitError  |

### 移植流程
1. 找想要的 PagerMaid 插件源码
2. 读懂逻辑（不看 Pyrogram API，只看"它在干嘛"）
3. 按本指南骨架建目录 + manifest
4. 用 Telethon 等价 API 重写
5. 测试 → 提 PR 到 examples/plugins/

## 8. 沙箱权限声明

ctx.client 在第三方插件里是 SandboxClient 包装，按 manifest.permissions 限制：
- send_message → send_message / respond
- edit_message → edit / edit_message
- read_chat → get_messages / iter_messages / get_chat
- send_file → send_file
- delete_message → delete_messages

未声明就调 → PermissionError

## 9. 完整样例

见 examples/plugins/translate/
```

### 移植样例：translate

参考 [PagerMaid_Plugins/translate](https://github.com/TeamPGM/PagerMaid_Plugins/blob/master/translate/main.py) 的逻辑（不抄代码），写一个 Telethon 版：

- `,fy <lang>` 翻译被回复消息到指定语言
- `,fy auto` 自动检测语言
- 调 OpenAI 的 LLM provider（复用我们现有的 `LLMProvider`）

放在 `examples/plugins/translate/` 作为开发模板，**不放进 builtin/**（这是示范，不是默认功能）。

### 验收
- 文档可读，5 章以上完整
- examples/plugins/translate/ 能 cp 到 data/plugins/installed/ 加载成功
- ,fy 命令在群里能用（reply 一条英文消息 → ,fy zh → 翻译）

### 2-B 完成报告
- 已新增 `docs/PLUGIN-DEV-GUIDE.md`，覆盖目录骨架、Manifest 字段、Plugin/Context hook、风控接入、Telethon 速查、安装启用、PagerMaid→Telethon 移植流程、沙箱权限与完整样例入口。
- 已新增 `examples/plugins/translate/`（`__init__.py` / `manifest.py` / `plugin.py` / `README.md`），按 Telethon + Telebot 命令机制重写 `,fy <lang|auto>`，逻辑参考 PagerMaid translate 的“功能目标”而非源码拷贝。
- `examples/plugins/translate/README.md` 已注明源出处与移植决策（命令接入、provider 选择、LLM 调用链复用）。
- 已按约定将样例保持在 `examples/`，未放入 builtin 目录，且未修改数据库与 `backend/app/worker/plugins/builtin/` 文件。

---

## 2C. 定时任务 plugin 业务实装（2 天）

### 目的
PRD §F 的"定时任务"——按 cron / once 触发动作。

### 文件白名单
- `backend/app/worker/plugins/builtin/scheduler/plugin.py` — 完整实装（替换 20 行骨架）
- `backend/app/worker/plugins/builtin/scheduler/manifest.py` — 升 0.2.0
- `backend/app/db/models/rule.py` — 检查 Rule 已有字段够不够（cron 表达式存 config）；不够则加迁移
- `backend/app/api/rules.py` — dry-run 加 scheduler 分支
- `frontend/src/api/types.ts` — 追加 `// ===== Sprint4 #2C =====` 块（`SchedulerRuleConfig`）
- `frontend/src/pages/Features/Scheduler.tsx` — 完整规则编辑页
- `backend/app/tests/test_scheduler_plugin.py` — 新建测试

### 数据模型（rule.config）

```python
{
    "kind": "cron" | "once" | "interval",
    "cron": "0 9 * * 1-5",         # cron 表达式（周一到周五 9 点）
    "fire_at": "2026-05-10T15:30:00+08:00",   # once 模式
    "interval_sec": 3600,           # interval 模式
    "action": {
        "type": "send_message" | "run_command" | "call_llm",
        "target_chat_id": -1001234567890,
        "text": "早安，今日待办...",
        # call_llm 时
        "provider_id": 1,
        "prompt": "今天股市怎么样？"
    },
    "enabled": True,
    "next_fire": "2026-05-10T09:00:00+08:00"  # 由 worker 维护
}
```

### 实现要点

```python
# scheduler/plugin.py 主循环
class SchedulerPlugin(Plugin):
    async def on_startup(self, ctx):
        ctx.tasks_handle = asyncio.create_task(self._tick_loop(ctx))

    async def on_shutdown(self, ctx):
        if ctx.tasks_handle:
            ctx.tasks_handle.cancel()

    async def _tick_loop(self, ctx):
        """每 30 秒扫一次该账号的所有 scheduler rules，触发到点的。"""
        while True:
            try:
                await self._tick_once(ctx)
            except Exception as e:
                ctx.log("error", f"scheduler tick error: {e!r}")
            await asyncio.sleep(30)

    async def _tick_once(self, ctx):
        now = datetime.now(timezone.utc)
        for rule in ctx.rules:
            if not rule.enabled:
                continue
            cfg = rule.config
            next_fire = self._compute_next_fire(cfg, now)
            if next_fire and next_fire <= now:
                await self._fire(ctx, rule, cfg)

    async def _compute_next_fire(self, cfg, now):
        kind = cfg.get("kind", "cron")
        if kind == "once":
            return parse_isoformat(cfg["fire_at"])
        if kind == "interval":
            last = cfg.get("last_fire")
            return parse(last) + timedelta(seconds=cfg["interval_sec"]) if last else now
        if kind == "cron":
            return croniter(cfg["cron"], now).get_next(datetime)
        return None

    async def _fire(self, ctx, rule, cfg):
        await ctx.engine.acquire("send_message_group", peer_id=cfg["action"]["target_chat_id"])
        action = cfg["action"]
        if action["type"] == "send_message":
            await ctx.client.send_message(int(action["target_chat_id"]), action["text"])
        elif action["type"] == "run_command":
            # 走 command.py 的 _BUILTIN 或模板派发
            ...
        elif action["type"] == "call_llm":
            from app.services.llm_client import build_client_for
            llm = await build_client_for(action["provider_id"])
            r = await llm.complete(action.get("system_prompt", ""), action["prompt"])
            await ctx.client.send_message(int(action["target_chat_id"]), r.text[:4000])

        # 更新 last_fire / 删除 once 类型的 rule
        ...
```

### 依赖
- `croniter`（cron 表达式解析）— 加进 `pyproject.toml`

### 前端 Scheduler.tsx 编辑器

仿 AutoReply.tsx：
- 左：规则列表
- 右：编辑器
  - kind 单选（cron / once / interval）
  - cron 模式 → cron 表达式输入 + 帮助"`* * * * *`：分 时 日 月 周"
  - once 模式 → datetime-local 输入
  - interval 模式 → 秒数输入
  - action.type 单选（send_message / run_command / call_llm）
  - 各 type 对应字段
  - "下次触发"显示（前端用 cron-parser）

### 验收
- TG 收藏夹自配一条规则：每分钟 `* * * * *` send_message "tick" → 1 分钟后收到 → 跑 5 分钟收到 5 次
- once 规则：5 分钟后 → 准时触发一次 → 该 rule 自动 disable
- worker 重启 → 持久化保留 → 继续触发（计算 next_fire 跳过死时间）

### 2-C 完成报告
- 已将 `backend/app/worker/plugins/builtin/scheduler/plugin.py` 从骨架替换为完整实现：支持 `cron / once / interval` 三种触发与 `send_message / run_command / call_llm` 三类动作，包含风控 `acquire`、FloodWait 重试、`last_fire/next_fire/last_result/last_error` 状态回写。
- 已升级 `backend/app/worker/plugins/builtin/scheduler/manifest.py` 到 `0.2.0`，并更新描述为业务版定时任务能力。
- 已在 `backend/pyproject.toml` 增加 `croniter` 依赖用于 cron 表达式解析。
- 已在 `backend/app/api/rules.py` 增加 `FEATURE_SCHEDULER` dry-run 分支，返回是否到点、next_fire 与动作摘要。
- 已完成 `frontend/src/pages/Features/Scheduler.tsx` 规则编辑器（列表、总开关、CRUD、dry-run、kind/action 分支表单）。
- 已按约定在 `frontend/src/api/types.ts` 末尾追加 `// ===== Sprint4 #2C =====` 类型块（`SchedulerRuleConfig` 等）。
- 已新增 `backend/app/tests/test_scheduler_plugin.py` 覆盖 interval/once/cron 与时间解析核心行为。

---

## 2D. 多 Bot 接入（1.5 天）

### 目的
项目自身（不是 userbot）需要发通知：
- 项目启动 / 故障告警 / FloodWait 触发
- 用户自己用脚本时也能调 `await notify("xxx")`

**Bot != userbot**：
- userbot = 你的个人 TG 账号，用 MTProto session
- Bot = `@BotFather` 创建的 bot，用 Bot Token 走 HTTP API
- 两者完全独立；Bot 不需要 worker 进程，不会被风控扫到

### 文件白名单
- `backend/app/db/models/notify.py`（**新建**）—
  ```python
  class NotifyBot(Base):
      __tablename__ = "notify_bot"
      id: Mapped[int] = primary_key
      name: Mapped[str]
      bot_token_enc: Mapped[str]   # Fernet 加密
      default_chat_id: Mapped[int]
      enabled: Mapped[bool] = True
      created_at: ...
  ```
- `backend/app/services/notify_service.py`（**新建**）—
  ```python
  async def send(channel_name: str | None, text: str, *, parse_mode="HTML") -> bool:
      """发到指定 NotifyBot；channel_name=None 用 default 那个。"""
      bot = await _select_bot(channel_name)
      if not bot: return False
      token = decrypt_str(bot.bot_token_enc)
      url = f"https://api.telegram.org/bot{token}/sendMessage"
      async with httpx.AsyncClient() as cli:
          r = await cli.post(url, json={"chat_id": bot.default_chat_id, "text": text, "parse_mode": parse_mode})
          return r.is_success
  ```
- `backend/app/api/notify_bots.py`（**新建**）— CRUD + `POST /test`（发一条 "test from telebot v..."）
- `backend/app/schemas/notify.py`（**新建**）
- `backend/alembic/versions/0013_notify_bot.py`
- `backend/app/main.py` — 启动时 `await notify_service.send(None, f"📦 telebot {__version__} started")`
- `backend/app/worker/supervisor.py` — worker 异常退出时 `await notify_service.send("alert", f"⚠️ account {aid} crashed: {e}")`
- `frontend/src/api/notify_bots.ts`（**新建**）
- `frontend/src/pages/Settings/NotifyBots.tsx`（**新建**）
- `frontend/src/pages/Settings/Index.tsx` — 嵌入 `<NotifyBots />`
- `frontend/src/api/types.ts` — 追加 `Sprint4 #2D` 块

### 触发点（建议）
- 项目启动（main.py lifespan）
- account 进入 dead 状态（supervisor）
- FloodWait 大于 1h（rate_limit engine）
- alembic 迁移成功（脚本里）

### 验收
- 系统设置 → 通知 Bot → 新建 → 填 token + chat_id → 测试按钮 → 收到测试消息
- `make backend` 重启 → 收到 "📦 telebot 0.4.0 started"
- 一个账号停掉（kill worker）→ supervisor 标 dead → 收到告警

### 2-D 完成报告
- 已新增 `notify_bot` 数据模型与迁移 `0013_notify_bot.py`，字段含 `name/bot_token_enc/default_chat_id/enabled`，`bot_token_enc` 采用 Fernet 加密存储。
- 已实现 `backend/app/services/notify_service.py`：按 channel 选取 Bot（`None` 优先 `default`），通过 `https://api.telegram.org/bot{token}/sendMessage` 发送消息，失败安全降级返回 `False`。
- 已新增 `backend/app/api/notify_bots.py` 与 `backend/app/schemas/notify.py`：提供 CRUD + `POST /api/notify-bots/{id}/test`，GET 仅返回 `has_token`，不返回明文 token。
- 已在 `backend/app/main.py` lifespan 追加启动通知：`📦 telebot vX.Y.Z started`（不改原有启动顺序，仅追加）。
- 已在 `backend/app/worker/supervisor.py` 的 account-dead 分支追加告警通知：发送到 `alert` channel（未配置时静默失败，不影响原逻辑）。
- 已新增前端 `frontend/src/api/notify_bots.ts`、`frontend/src/pages/Settings/NotifyBots.tsx`，并在 `frontend/src/pages/Settings/Index.tsx` 追加嵌入 `<NotifyBots />`。
- 已按约定在 `frontend/src/api/types.ts` 末尾追加 `// ===== Sprint4 #2D =====` 类型块。
- 验证结果：`backend` 侧 `ruff check .` 通过，`pytest -q` 通过（351 passed, 2 skipped），`frontend` 侧 `pnpm build` 通过。

---

## 2E. Caddy 反代 + HTTPS 部署文档（半天）

### 文件白名单（新建）
- `docs/DEPLOY-PUBLIC.md`（约 200 行）
- `deploy/Caddyfile.example`

### Caddyfile 模板

```caddy
telebot.example.com {
    encode gzip

    # 限流：每个 IP 每分钟 60 个写请求
    @writes method POST PUT PATCH DELETE
    rate_limit @writes 60r/m

    # API 反代
    handle /api/* {
        reverse_proxy 127.0.0.1:8000 {
            header_up X-Real-IP {remote}
            header_up X-Forwarded-For {remote}
        }
    }

    # 前端静态文件
    handle {
        root * /opt/telebot/frontend/dist
        try_files {path} /index.html
        file_server
    }

    # HSTS
    header Strict-Transport-Security "max-age=31536000; includeSubDomains"
}
```

### docs/DEPLOY-PUBLIC.md 大纲

```markdown
# 公网部署指南（Caddy + HTTPS）

## 1. 前提
- 域名一个（`telebot.example.com`）
- 服务器（VPS / 自己机器）公网 IP，端口 80/443 开放
- Docker（用 OrbStack/Docker Desktop 都行）

## 2. .env 强制项
COOKIE_SECURE=true
TRUST_FORWARDED_FOR=true
CORS_ORIGINS=https://telebot.example.com
JWT_SECRET=<32 位强随机>
MASTER_KEY=<32 位强随机>
POSTGRES_PASSWORD=<32 位强随机>

chmod 600 .env

## 3. 装 Caddy

# brew install caddy 或 apt install caddy
# 把 deploy/Caddyfile.example 改成自己的 → 放到 /etc/caddy/Caddyfile
# 启动：caddy run --config /etc/caddy/Caddyfile

## 4. 起后端 / 前端

# 后端
make dev-up  # PG + Redis
cd backend && alembic upgrade head
uvicorn app.main:app --host 127.0.0.1 --port 8000  # 注意 127.0.0.1 不暴露公网

# 前端 build
cd frontend && pnpm run build  # 产物在 frontend/dist/

## 5. systemd 守护（可选）

(贴 systemd unit file)

## 6. 自动备份

(crontab pg_dump → tar.gz → rsync 到第二台机)

## 7. 应急响应

- 怀疑被入侵 → 改 web_user.password_hash + pwd_version += 1（旧 token 全失效）
- 改 JWT_SECRET → 所有用户被踢
- 改 MASTER_KEY → 所有 session_enc / api_key_enc 失效，需要重登

## 8. 监控

- TG-self 通知（Wave 2-D 已配）
- Caddy access log → grep 异常 4xx/5xx
- 看 ,status 命令返回 worker 状态
```

### 验收
- 文档可执行（按步骤一遍能起）
- Caddyfile 在本机能验证 `caddy validate`

### 2-E 完成报告
- 已新建 `docs/DEPLOY-PUBLIC.md`，按大纲补齐公网 HTTPS 部署全流程（前提、.env 强制项、Caddy、启动、守护、备份、应急、监控、验收）。
- 已新建 `deploy/Caddyfile.example`，包含写请求限流、`/api/*` 反代、前端静态托管与 HSTS。
- 已检查并补全 `backend/.env.example`，包含公网必填项：
  `COOKIE_SECURE` / `TRUST_FORWARDED_FOR` / `CORS_ORIGINS` / `JWT_SECRET` / `MASTER_KEY` / `POSTGRES_PASSWORD`，
  并逐项标注“公网部署必填”。
- 文档内链已指向仓库内实际文件路径，便于直接跳转核对。

---

## Wave 2 整体验收

```bash
cd backend && alembic upgrade head && pytest -q && ruff check .
cd frontend && pnpm run build

# 浏览器手测
# 1. /settings/plugins 只剩 enable/disable 列表（market 没了）
# 2. ,version 显示 v0.4.0
# 3. 系统设置 → 通知 Bot 测试 OK
# 4. 配 scheduler 规则 → cron */1 send_message → 1 分钟后到
# 5. examples/plugins/translate/ 装到 data/plugins/installed/ → ,fy 能用
# 6. 按 docs/DEPLOY-PUBLIC.md 在本地或测试机走一遍
```

完成后 bump 0.4.0，CHANGELOG 记录：
- Added: scheduler plugin / multi-bot notify / plugin dev guide / Caddy 部署文档
- Removed: group_admin / monitor / plugin_repo 远程订阅 / zip 上传
- Changed: PluginManager UI 简化
