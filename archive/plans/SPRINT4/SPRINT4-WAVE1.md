# Sprint 4 — Wave 1：体验改进（合并到 main 后即 0.3.1）

> 工时：约 1 天
> 依赖：无
> 优先级：先做（小、独立、立竿见影）

## 1. 三个改进合并成一个会话

| 子任务 | 工时 | 描述 |
|--------|------|------|
| 1A. Telethon 1.43.x 升级 | 30min | patch 升级 + 跑测试 |
| 1B. `,help` 折叠 + 短别名 | 半天 | 内置 + 自定义命令都支持 alias |
| 1C. 内置 `,del N` | 半天 | 撤自己最近 N 条消息 |

## 2. 1A. Telethon 1.43.x 升级

### 文件白名单
- `backend/pyproject.toml` — 改版本约束 `telethon>=1.43,<2.0`
- 重新 pip install
- 跑 `pytest -q` 看回归

### 验收
- `pytest -q` 全绿
- 手动登录一个账号 + auto_reply 触发一次 → 正常
- TG 内 `,version` 第二行 Telethon 版本应为 1.43.x

### 已知风险
- Telethon 1.43 改了几个错误类的 `__init__` 签名（`SlowModeWaitError` 等）；测试如有红就修
- 可能 `events.NewMessage.Event.is_private` 行为有微调；查 changelog

## 3. 1B. `,help` 折叠 + 短别名（含自定义命令模板）

### 设计

**目标**：
- 内置 `,status` 同时可用 `,s` / `,st`
- 自定义命令模板也能配多个 alias
- `,help` 显示折叠为 `,status (s, st) — 描述` 一行

### 文件白名单

后端：
- `backend/app/worker/command.py` — `_BUILTIN` 字典从 `dict[str, fn]` 改为：
  ```python
  @dataclass
  class BuiltinCmd:
      handler: Callable
      aliases: tuple[str, ...] = ()
      doc: str = ""
  ```
  注册时把 alias 也指向同一个 handler；`,help` 显示时按主名分组。
- `backend/app/db/models/command.py` — `CommandTemplate` 加 `aliases: Mapped[list[str]]`（JSONB 数组，默认 `[]`）
- `backend/app/schemas/command.py` — schema 加 `aliases` 字段
- `backend/app/services/command_service.py` — 派发时把 aliases 一并查；保存时唯一性校验（同账号下 aliases ∪ name 全集不能撞自己 / 撞别人 / 撞 builtin）
- `backend/alembic/versions/0011_command_aliases.py` — `ALTER TABLE command_template ADD COLUMN aliases JSONB NOT NULL DEFAULT '[]'::jsonb`

前端：
- `frontend/src/api/types.ts` — `CommandTemplateOut` / `CommandTemplateCreate` 加 `aliases: string[]`
- `frontend/src/pages/Settings/CommandTemplates.tsx` — 编辑表单加"别名"输入（chip 风，逗号/回车分隔；字符规则同 name `^[a-zA-Z0-9_]{1,16}$`）
- `frontend/src/pages/Settings/CommandTemplates.tsx` — 表格新增"别名"列展示 chips

### 内置命令默认别名约定

```python
# command.py
BUILTIN_DEFAULTS = {
    "help":    BuiltinCmd(_cmd_help,    aliases=("h",),       doc="显示可用命令列表"),
    "status":  BuiltinCmd(_cmd_status,  aliases=("s", "st"),  doc="查看账号运行状态"),
    "id":      BuiltinCmd(_cmd_id,      aliases=("i",),       doc="返回当前会话 chat_id"),
    "ping":    BuiltinCmd(_cmd_ping,    aliases=(),           doc="测试 worker 是否在线"),
    "pause":   BuiltinCmd(_cmd_pause,   aliases=(),           doc="暂停本账号"),
    "resume":  BuiltinCmd(_cmd_resume,  aliases=(),           doc="恢复本账号"),
    "version": BuiltinCmd(_cmd_version, aliases=("v",),       doc="显示版本号"),
    "del":     BuiltinCmd(_cmd_del,     aliases=(),           doc="撤回自己最近 N 条消息（见 1C）"),
}
```

### `,help` 输出示例

```
📋 可用命令（前缀 ,）：

内置：
• ,help (h)            — 显示可用命令列表
• ,status (s, st)      — 查看账号运行状态
• ,id (i)              — 返回当前会话 chat_id
• ,version (v)         — 显示版本号
• ,del N               — 撤回自己最近 N 条消息
• ,pause / ,resume     — 暂停 / 恢复
• ,ping                — 测试在线

自定义：
• ,fy / ,t / ,trans    — 翻译（绑定 OpenAI）
• ,w (we, wea)         — 天气查询（reply_text）
```

### 验收
- TG 输入 `,s` 等同 `,status` 输出
- 自定义模板配 alias 后立刻生效（IPC reload_commands）
- 唯一性：alias 撞 builtin 时 API 拒，alias 撞已有模板时 API 拒
- `,help` 折叠成 `,name (a, b, c) — desc`

## 4. 1C. `,del N` 撤自己最近 N 条

### 设计

`,del 5` 表示在**当前会话**中删除自己（worker 账号）最近发出的 5 条消息。

### 文件白名单
- `backend/app/worker/command.py` — 新加 `_cmd_del` handler，注册到 `BUILTIN_DEFAULTS["del"]`

### 实现要点

```python
async def _cmd_del(client, event, args, account_id):
    """撤回自己在当前会话最近发出的 N 条消息。"""
    if not args or not args[0].isdigit():
        await event.edit("用法：,del <数字>，例如 ,del 5")
        return
    n = int(args[0])
    if n <= 0 or n > 100:
        await event.edit("N 必须在 1-100 之间")
        return

    me = await client.get_me()
    chat = await event.get_chat()

    # 收集自己发的最近 N+1 条（+1 是因为 ,del 命令本身也算一条，要算进去）
    to_delete = []
    async for msg in client.iter_messages(chat, limit=200, from_user=me.id):
        to_delete.append(msg.id)
        if len(to_delete) >= n + 1:
            break

    # 走风控（delete_message 动作）
    if not to_delete:
        await event.edit("没找到可撤回的消息")
        return

    # delete_messages 一次最多 100 条
    await client.delete_messages(chat, to_delete[:n + 1])
    # 命令本身也被删了，所以这里不再 edit；如失败 except 里 log
```

### 验收
- 发 5 条 → `,del 5` → 6 条全消失（5 条 + 命令本身）
- `,del 0` / `,del abc` → 提示用法
- `,del 200` → 提示 N <= 100
- 群里没自己发的消息 → 提示"没找到可撤回的消息"
- 风控配额计入（runtime_log 应有 `delete_message` 动作记录）

## 5. 验收清单（合并到 main 前）

```bash
cd backend
pip install -U "telethon>=1.43,<2.0"  # 1A
alembic upgrade head                    # 0011_command_aliases
pytest -q                               # 全绿
ruff check .                            # 全绿

cd frontend && pnpm run build           # 全绿

# 浏览器手测
# 1. 系统设置 → 命令模板 → 编辑某条 → 别名加 ,t / ,trans → 保存
# 2. TG 内试 ,t 是否能命中
# 3. ,help 输出是不是折叠样式
# 4. ,del 3 撤自己最近 3 条
# 5. ,version 显示 0.3.1
```

## 6. 版本号同步

完成后 bump 到 **0.3.1**（patch，体验性增量）：
- `backend/app/__init__.py`
- `backend/pyproject.toml`
- `frontend/package.json`
- `frontend/src/lib/version.ts`
- `CHANGELOG.md` 顶部加 `## [0.3.1] — yyyy-mm-dd · Sprint 4 Wave 1`

## 7. 完成报告模板

```markdown
## 完成报告 — Wave 1

- [x] Telethon 升 1.43.x（实际版本号：x.y.z）
- [x] aliases 字段 + 0011 迁移 + IPC reload
- [x] ,help 折叠输出
- [x] ,del N 实装（含风控）
- 改动文件：XX 个
- 测试：pytest XX passed / pnpm build OK
- 版本号：0.3.0 → 0.3.1
- 已知遗留：（如有）
```

## 完成报告 — Wave 1

- [x] Telethon 升 1.43.x（约束：`telethon>=1.43,<2.0.0`）
- [x] aliases 字段 + 0011 迁移 + IPC reload
- [x] ,help 折叠输出
- [x] ,del N 实装（含删除命令本身）
- 改动文件：14 个
- 测试：
  - `pnpm run build` ✅
  - `pytest -q` ❌（现有阻塞：`app/worker/plugins/builtin/__init__.py` 导入 `group_admin/monitor` 失败，非 Wave1 改动范围）
  - `ruff check` ❌（现有历史 lint 项，集中在 `app/tests/*` 与 `app/worker/supervisor.py`，非 Wave1 改动范围）
- 版本号：0.3.0 → 0.3.1
- 已知遗留：
  - 需由对应会话修复 builtin plugin 目录导入一致性后再跑全量 pytest
  - 需统一清理历史 ruff 项后再达成全绿
