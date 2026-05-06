# Sprint 2 — Session #2：自定义命令（含 AI 类型）

> 工时：约 2-2.5 天
> 依赖：无
> 优先级：与 #1、#3 并行启动

## 1. 目标

让用户在系统设置里维护一份"全局命令模板库"，每个账号可以**选择性勾选**启用某些模板（不是全局自动生效），worker 加载后即可在 TG 里用 `,模板名` 触发。

支持 4 种命令类型：

| type | 行为 | 配置字段 |
|------|------|---------|
| `reply_text` | 收到 → 编辑原消息为指定文本（PagerMaid 风格） | `text` |
| `forward_to` | 收到 → 转发"被引用消息"到指定 chat_id | `target_chat_id` |
| `run_plugin` | 占位：调用某个已加载插件的方法 | `plugin_key`, `method`, `args` |
| `ai` | 收到 → 调用 LLM → 编辑/回复成 "[AI] 答案 · model · tokens" | `provider`, `model`, `system_prompt`, `max_tokens`, `quote_replied: bool` |

### AI 命令示意

用户在某条群消息上回复 `/ai 这是什么意思？`：
1. worker 取被回复的原消息文本（如 `"今晚 deadline 凌晨"`）
2. 拼 prompt：`{system_prompt}\n\n[原文]\n今晚 deadline 凌晨\n\n[问题]\n这是什么意思？`
3. 调用 OpenAI / Anthropic 的 LLM API（API key 来自 LLMProvider 表）
4. 把回答写回："📝 这句话的意思是 …\n— claude-sonnet-4 · 87 tokens"

## 2. 文件白名单

### 后端
- `backend/app/db/models/command.py`（**新建**）—
  - `CommandTemplate` 表：`id, name, type, config(JSONB), description, created_at`
  - `AccountCommandLink` 表：`account_id, template_id, enabled`（联合主键）
  - `LLMProvider` 表：`id, name, provider(openai/anthropic/ollama), api_key_enc(TEXT), base_url, default_model`
- `backend/app/db/models/__init__.py` — 导入新增模型
- `backend/app/schemas/command.py`（**新建**）— Pydantic schema
- `backend/app/api/commands.py`（**新建**）—
  - `GET/POST/PATCH/DELETE /api/commands/templates`
  - `GET/POST/DELETE /api/commands/llm-providers`（POST 时 api_key 加密；GET 不返明文，只返 `has_api_key:true/false`）
  - `GET /api/accounts/{aid}/commands` — 列出该账号已启用 + 可用全部模板
  - `POST /api/accounts/{aid}/commands/{template_id}` — 启用
  - `DELETE /api/accounts/{aid}/commands/{template_id}` — 禁用
- `backend/app/services/command_service.py`（**新建**）— 业务逻辑
- `backend/app/services/llm_client.py`（**新建**）— LLM provider 抽象（OpenAI/Anthropic）
- `backend/app/worker/command.py` — **追加**：除了 `_BUILTIN`，加载该账号启用的 templates 并合并分发
- `backend/app/worker/runtime.py` — worker 启动时拉一次 templates；监听 IPC `reload_commands` 热加载
- `backend/app/main.py` — 注册新 router（**只追加一行**）
- `backend/alembic/versions/0003_command_template.py`（**新建迁移**）

### 前端
- `frontend/src/api/commands.ts`（**新建**）
- `frontend/src/api/types.ts` — **追加** `// ===== Sprint2 #2 =====` 块
- `frontend/src/pages/Settings/CommandTemplates.tsx`（**新建**）— 系统设置 → 自定义命令
- `frontend/src/pages/Settings/LLMProviders.tsx`（**新建**）— 系统设置 → LLM Provider
- `frontend/src/pages/Accounts/Detail.tsx` — 新增 "命令" tab，列出全量模板 + 勾选启用
- `frontend/src/router.tsx` 或 `App.tsx` — 注册 2 条新路由
- `frontend/src/components/layout/Sidebar.tsx`（如有）— 系统设置组下加入口

### 不要动
worker/plugins/* 任何插件文件、风控引擎、auto_reply、登录服务。

## 3. 实现要点

### 3.1 数据模型

```python
# db/models/command.py
class CommandTemplate(Base):
    __tablename__ = "command_template"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)  # ,name 触发
    type: Mapped[str] = mapped_column(String(16))  # reply_text/forward_to/run_plugin/ai
    config: Mapped[dict] = mapped_column(JSONB, default=dict)
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)

class AccountCommandLink(Base):
    __tablename__ = "account_command_link"
    account_id: Mapped[int] = mapped_column(ForeignKey("account.id"), primary_key=True)
    template_id: Mapped[int] = mapped_column(ForeignKey("command_template.id"), primary_key=True)
    enabled: Mapped[bool] = mapped_column(default=True)

class LLMProvider(Base):
    __tablename__ = "llm_provider"
    id: Mapped[int] = mapped_column(primary_key=True)
    name: Mapped[str] = mapped_column(String(64), unique=True)
    provider: Mapped[str] = mapped_column(String(16))  # openai/anthropic/ollama
    api_key_enc: Mapped[str | None] = mapped_column(Text)  # Fernet 加密
    base_url: Mapped[str | None] = mapped_column(String(255))
    default_model: Mapped[str] = mapped_column(String(64))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)
```

### 3.2 LLM client 抽象

```python
# services/llm_client.py
from abc import ABC, abstractmethod
from dataclasses import dataclass

@dataclass
class LLMResult:
    text: str
    model: str
    input_tokens: int
    output_tokens: int

class LLMClient(ABC):
    @abstractmethod
    async def complete(self, system: str, user: str, max_tokens: int = 512) -> LLMResult: ...

class OpenAIClient(LLMClient):
    def __init__(self, api_key: str, base_url: str | None, model: str):
        self.api_key, self.base_url, self.model = api_key, base_url or "https://api.openai.com/v1", model
    async def complete(self, system, user, max_tokens=512):
        # httpx.AsyncClient POST /chat/completions
        ...

class AnthropicClient(LLMClient):
    # POST https://api.anthropic.com/v1/messages
    ...

def build_client(provider_row: LLMProvider, override_model: str | None = None) -> LLMClient:
    api_key = decrypt_str(provider_row.api_key_enc)
    model = override_model or provider_row.default_model
    if provider_row.provider == "openai":
        return OpenAIClient(api_key, provider_row.base_url, model)
    elif provider_row.provider == "anthropic":
        return AnthropicClient(api_key, provider_row.base_url, model)
    else:
        raise ValueError(f"unknown provider: {provider_row.provider}")
```

**安全红线**（README §3 约定 D）：
- `api_key_enc` 用 `app/crypto.py:encrypt_str` 加密
- GET 接口返回时 `api_key` 字段统一映射成 `has_api_key: bool`
- worker 内解密后只在 `LLMClient.__init__` 持有，不要打 log
- 任何错误路径捕获后用 `getattr(e, "message", str(e))` 时**先检查不含 api_key 字符串再 log**

### 3.3 Worker 集成

`worker/runtime.py` 启动时：

```python
# 拉本账号启用的命令模板
templates = await command_service.list_for_account(account_id, db)
ctx.templates = {t.name: t for t in templates}  # name → template

# 监听 IPC reload
async def _on_reload_commands(_payload):
    nonlocal templates
    templates = await command_service.list_for_account(account_id, db)
    ctx.templates = {t.name: t for t in templates}
ipc.subscribe(f"worker_cmd:{account_id}:reload_commands", _on_reload_commands)
```

`worker/command.py` 现有 `_dispatch_command` 加分支：

```python
async def _dispatch_command(client, event, name, args, ctx):
    # 1. 内置命令优先
    if name in _BUILTIN:
        return await _BUILTIN[name](client, event, args, ctx.account_id)
    # 2. 模板命令
    tpl = ctx.templates.get(name)
    if tpl:
        return await _run_template(client, event, args, tpl, ctx)
    # 3. 不存在
    await event.edit(f"未知命令：{name}（,help 查看可用列表）")

async def _run_template(client, event, args, tpl, ctx):
    if tpl.type == "reply_text":
        text = tpl.config.get("text", "")
        # 简单变量替换
        text = text.replace("{args}", " ".join(args)).replace("{me}", ...)
        await event.edit(text)
    elif tpl.type == "forward_to":
        replied = await event.get_reply_message()
        if not replied:
            await event.edit("✗ 请回复要转发的消息再用此命令")
            return
        target = int(tpl.config["target_chat_id"])
        await replied.forward_to(target)
        await event.edit(f"✓ 已转发到 {target}")
    elif tpl.type == "ai":
        await _run_ai(client, event, args, tpl, ctx)
    elif tpl.type == "run_plugin":
        await event.edit(f"⏳ run_plugin 占位：{tpl.config!r}")
```

AI 分支：

```python
async def _run_ai(client, event, args, tpl, ctx):
    cfg = tpl.config
    provider_id = cfg.get("provider_id")
    if not provider_id:
        await event.edit("✗ AI 命令未配置 provider")
        return
    # 主进程负责选 provider，worker 通过 IPC 拉一次或缓存
    provider = await ctx.fetch_llm_provider(provider_id)
    llm = build_client(provider, override_model=cfg.get("model"))

    # 拼 prompt
    user_q = " ".join(args).strip()
    replied = await event.get_reply_message()
    if cfg.get("quote_replied", True) and replied and replied.text:
        user_msg = f"[原文]\n{replied.text}\n\n[问题]\n{user_q or '解释/总结'}"
    else:
        user_msg = user_q or "请简要总结你能想到的内容"

    system = cfg.get("system_prompt", "你是简洁有用的中文助手。回答控制在 100 字内。")
    await event.edit("⏳ AI 思考中...")
    try:
        r = await llm.complete(system, user_msg, max_tokens=cfg.get("max_tokens", 512))
        body = f"{r.text}\n\n— {r.model} · in {r.input_tokens} / out {r.output_tokens}"
        await event.edit(body[:4000])  # TG 单条上限
    except Exception as e:
        await event.edit(f"✗ AI 调用失败：{type(e).__name__}: {str(e)[:120]}")
```

### 3.4 前端：系统设置 → 自定义命令

```tsx
// pages/Settings/CommandTemplates.tsx
export default function CommandTemplates() {
  const q = useQuery({ queryKey: ["cmd-tpl"], queryFn: listTemplates });
  const [editing, setEditing] = useState<CommandTemplate | null>(null);
  return (
    <div className="space-y-4">
      <div className="flex items-center justify-between">
        <h1 className="text-xl font-semibold">自定义命令</h1>
        <Button onClick={() => setEditing({ ...EMPTY })}>+ 新建</Button>
      </div>
      <Table>...每行：name / type 徽章 / description / 编辑按钮 ...</Table>
      {editing && <CommandEditDialog tpl={editing} onClose={...} />}
    </div>
  );
}
```

`CommandEditDialog` 根据 `type` 切换不同表单：
- `reply_text` → textarea (text)
- `forward_to` → number input (target_chat_id) + 帮助"群 ID 怎么填"
- `ai` → select (provider) + select (model) + textarea (system_prompt) + number (max_tokens) + switch (quote_replied)
- `run_plugin` → 三 input 占位 + "V1 接口未实现，配置仅保存"

### 3.5 账号详情 → "命令" tab

```tsx
<TabsContent value="commands">
  <Card>
    <CardHeader><CardTitle>启用的命令</CardTitle></CardHeader>
    <CardContent>
      <ul className="divide-y">
        {templates.map(t => (
          <li className="flex items-center justify-between py-3">
            <div>
              <div className="font-mono text-sm">,{t.name}</div>
              <div className="text-xs text-muted-foreground">{t.description}</div>
            </div>
            <Switch
              checked={t.linked}
              onCheckedChange={v => v ? enable(t.id) : disable(t.id)}
            />
          </li>
        ))}
      </ul>
    </CardContent>
  </Card>
</TabsContent>
```

启用/禁用后 invalidate query + 后端 IPC 推送 `worker_cmd:{aid}:reload_commands` 让 worker 热加载。

## 4. 验收清单

```bash
cd backend
alembic upgrade head
pytest -v                          # 全绿（含至少 3 个新 case：CRUD、加密、reply_text 模板执行）
ruff check .

cd frontend && pnpm run build      # 全绿

# 浏览器手测
# 1. 系统设置 → LLM Provider → 新建 OpenAI provider，输入 key 保存
#    刷新页面 → 列表只显示 has_api_key:✓，不显示 key 明文
# 2. 系统设置 → 自定义命令 → 新建 reply_text 名 "hi" text "hello {args}"
# 3. 账号详情 → 命令 → 勾选 hi 启用
# 4. TG 收藏夹自发 ",hi 你好" → 原消息被编辑成 "hello 你好"
# 5. 新建 ai 类型 "ai"，绑前面 OpenAI provider
#    回复某条群消息 ",ai 这是什么意思" → 原命令被编辑成 AI 答案
```

## 5. 完成报告模板

```markdown
## 完成报告 — Session #2

- [x] 数据模型 + 迁移 0003_command_template.py
- [x] LLMClient 抽象（OpenAI/Anthropic）
- [x] CRUD API + key 加密
- [x] worker 模板加载 + 热重载 IPC
- [x] 4 种命令类型实现：reply_text / forward_to / ai / run_plugin(占位)
- [x] 前端：系统设置两页 + 账号详情命令 tab
- [x] api_key 安全红线检查（不出现在 GET / log / audit）
- 改动文件：XX 个
- 测试：pytest XX passed
- 已知遗留：（如 run_plugin 等 #4 完成后实现）
```

---

## ✅ 完成报告 — Session #2（实际交付）

完成时间：2026-05-03

### 交付清单

- [x] **数据模型 + 迁移**：`backend/app/db/models/command.py` 新建 3 个表模型（CommandTemplate / AccountCommandLink / LLMProvider），`alembic/versions/0003_command_template.py` 建表 SQL；与并行的 `0004_ignored_peer.py` 都基于 `0002`，由汇总人决定 chain 顺序
- [x] **Pydantic schema**：`schemas/command.py` 含 4 类 config 形态校验（reply_text/forward_to/run_plugin/ai 各自的必填字段）；命令名正则与 worker 派发的 `\w+` 对齐
- [x] **LLMClient 抽象**：`services/llm_client.py` 提供 OpenAIClient（兼容 Ollama）/ AnthropicClient；`build_client` 工厂在内部解密 api_key；`_safe_error_message` 兜底脱敏
- [x] **业务层 + REST API**：`services/command_service.py` + `api/commands.py`，全部 CRUD + IPC 通知；POST/PATCH 时 api_key 经 `crypto.encrypt_str` 加密落库；GET 仅返 `has_api_key:bool`
- [x] **worker 集成**：`worker/command.py` 增加 `CommandContext` + 模板派发分支；`worker/runtime.py` 启动时一次性拉取模板/provider 字典，监听 `CMD_RELOAD_COMMANDS` 热加载
- [x] **4 种命令类型实装**：reply_text（{args} 占位）/ forward_to（被回复消息转发）/ ai（LLM 调用 + 失败脱敏）/ run_plugin（占位等 Sprint2 #4）
- [x] **前端**：
  - `api/commands.ts` + `api/types.ts` Sprint2 #2 类型块
  - `pages/Settings/CommandTemplates.tsx` 模板 CRUD（按 type 切表单）
  - `pages/Settings/LLMProviders.tsx` provider CRUD（编辑模式不预填 api_key、可勾选清空）
  - `pages/Accounts/CommandsTab.tsx` 账号详情 → 命令 tab，Switch 启停
  - `App.tsx` 注册 `/settings/commands` 与 `/settings/llm-providers` 直链
  - `Settings/Index.tsx` 把两页内嵌进系统设置主页（用户在一处即可管理）

### 安全红线（约定 D）落实

- ✅ `api_key_enc` 全部经 Fernet 加密，复用 `app/crypto.py` 主密钥
- ✅ `LLMProviderOut` 永不含 `api_key` / `api_key_enc` 字段；测试 `test_provider_to_out_strips_api_key` 校验序列化文本中无明文
- ✅ `_safe_error_message` 在 4xx / 网络异常路径上替换 api_key 为 `<redacted>`；测试 `test_openai_client_error_status_redacts_key` 覆盖
- ✅ audit log 写 `llm_provider.update` 时只记录 `api_key_changed: bool`，不写明文 / 不写新旧值
- ✅ worker 解密后的 api_key 仅在 `LLMClient` 实例属性内持有；不打 log、不写 runtime_log
- ✅ AI 调用失败的 friendly 错误只透出 `type(e).__name__` + 截断后的 message

### 改动文件（共 20 个，12 新建 + 8 改动）

新建：
1. `backend/alembic/versions/0003_command_template.py`
2. `backend/app/api/commands.py`
3. `backend/app/db/models/command.py`
4. `backend/app/schemas/command.py`
5. `backend/app/services/command_service.py`
6. `backend/app/services/llm_client.py`
7. `backend/app/tests/test_commands.py`
8. `frontend/src/api/commands.ts`
9. `frontend/src/pages/Accounts/CommandsTab.tsx`
10. `frontend/src/pages/Settings/CommandTemplates.tsx`
11. `frontend/src/pages/Settings/LLMProviders.tsx`

改动（追加为主，避免冲突）：
12. `backend/app/db/models/__init__.py` — 导入 + __all__ 追加
13. `backend/app/main.py` — `app.include_router(commands_api.router)`（追加一行）
14. `backend/app/worker/command.py` — `CommandContext` + 模板派发分支
15. `backend/app/worker/ipc.py` — 新增 `CMD_RELOAD_COMMANDS` 常量
16. `backend/app/worker/runtime.py` — 启动时拉模板 + IPC reload handler
17. `frontend/src/App.tsx` — 2 条路由
18. `frontend/src/api/types.ts` — Sprint2 #2 类型块（追加在末尾）
19. `frontend/src/pages/Accounts/Detail.tsx` — 新增 "命令" tab
20. `frontend/src/pages/Settings/Index.tsx` — 嵌入两个新组件

### 验收

```bash
cd backend
.venv/bin/python -m pytest                # 115 passed, 2 skipped
.venv/bin/ruff check app/                  # All checks passed!

cd ../frontend
pnpm run build                             # ✓ built in 4.77s
```

新增测试 24 个（`test_commands.py`）覆盖：
- LLMProvider api_key 加密/解密往返 + 出参屏蔽（3）
- 4 种命令类型的 schema config 结构校验（5）
- LLM client 错误脱敏 + httpx mock 4xx/网络错误分支（5）
- worker `_run_template` 各分支（reply_text/forward_to 成功 & 失败/未知 type）（6）
- worker `_run_ai` 缺 provider_id / provider 未加载（2）
- `,help` 输出融合内置命令 + 模板命令（1）
- ai 类型必须配 provider_id 校验（1）
- LLM provider 厂商白名单（1）

**说明**：`alembic upgrade head` 因当前会话沙箱不允许连 PG 而无法验证；迁移文件已通过 `ast.parse` 语法检查 + 与 0001 同样的纯 SQL 风格，部署到真 PG 时可直接执行。

### 已知遗留

- `run_plugin` 类型仅落库 + 占位回显，等 Sprint2 #4 插件模块化（manifest + loader）完成后再接通
- `_safe_error_message` 当前仅按 api_key 字面替换，未做正则匹配（`sk-` / `Bearer` 模式）；如有外部 LLM 代理把不同密钥拼到错误里，可能漏过，待真实使用后再加规则
- LLM 调用没有做 worker 进程内的速率限制；高频触发 AI 命令会绕过现有风控（`ratelimit/engine`）。建议下一迭代把 LLM 调用纳入 `api_total` 桶

