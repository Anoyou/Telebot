# Sprint 2 — Session #5：转发插件（基于 #4 A 阶段的目录结构）

> 工时：约 1.5 天
> 依赖：**Session #4 阶段 A 完成**（builtin 目录化 + Manifest 上线）
> 优先级：Wave 2，待 #4 通知后开工

## 1. 目标

落地 PRD §B 的"消息转发"插件：

- 一个账号上配置 N 条转发路由：从某个 source（peer 列表 / 全部群 / 关键词过滤）→ 转发到一个 target（chat_id）
- 支持 4 种转发方式：原生 forward / 复制文本 / 引用包装 / 仅链接
- 支持媒体过滤（仅文本 / 含媒体也转）
- 受风控引擎限流：每条转发都走 `engine.acquire("forward_message")`
- 触发 FloodWait 自动暂停 30s 后重试一次
- 失败计数 / 最近一次错误展示在前端

## 2. 文件白名单

### 后端
- `backend/app/worker/plugins/builtin/forward/` 目录（**#4 A 阶段已建好骨架**）—
  - `__init__.py` 出 `MANIFEST` 和 `PLUGIN_CLASS`
  - `manifest.py` —
    ```python
    MANIFEST = Manifest(
        key="forward",
        display_name="消息转发",
        version="0.2.0",
        description="按规则把 incoming 消息转发到指定 chat",
        permissions=["read_chat", "send_message", "send_file"],
        config_schema={  # rule.config 的 JSON schema
            "type": "object",
            "required": ["target_chat_id", "mode"],
            "properties": {
                "source_kind": {"enum": ["all", "peers", "keyword"]},
                "source_peers": {"type": "array", "items": {"type": "integer"}},
                "keyword": {"type": "string"},
                "target_chat_id": {"type": "integer"},
                "mode": {"enum": ["forward_native", "copy_text", "quote", "link_only"]},
                "include_media": {"type": "boolean"},
                "header": {"type": "string"},
            },
        },
    )
    ```
  - `plugin.py` — 完整实现（详见 §3）

- `backend/app/api/rules.py` — 在 dry-run 端点增加 forward 类型的模拟（**只追加**，不改 auto_reply 部分）

### 前端
- `frontend/src/api/types.ts` — 追加 `// ===== Sprint2 #5 =====` 块（`ForwardRuleConfig`）
- `frontend/src/pages/Features/Forward.tsx`（**新建**）— 完整规则编辑页（仿照 AutoReply.tsx 结构）
- `frontend/src/router.tsx` — 注册 `/accounts/:aid/features/forward`（如还没注册）

### 不要动
auto_reply 任何文件（即使在同一个 builtin 目录里）；风控引擎；登录服务；Session #4 还在改的 loader/manifest 类。

## 3. 实现要点

### 3.1 ForwardPlugin 主体

```python
# builtin/forward/plugin.py
from telethon import events
from app.worker.plugins.base import Plugin, PluginContext

class ForwardPlugin(Plugin):
    key = "forward"
    display_name = "消息转发"

    async def on_message(self, ctx: PluginContext, event: events.NewMessage.Event) -> None:
        # 遍历该账号此功能下所有 enabled rules
        for rule in ctx.rules:
            cfg = rule.config
            if not _match_source(event, cfg):
                continue
            if not cfg.get("include_media", True) and event.message.media:
                continue
            try:
                await self._do_forward(ctx, event, cfg)
            except FloodWaitError as e:
                ctx.log("warning", f"forward floodwait {e.seconds}s, sleep & retry once")
                await asyncio.sleep(min(e.seconds, 60))
                try:
                    await self._do_forward(ctx, event, cfg)
                except Exception as e2:
                    ctx.log("error", f"forward retry failed: {e2!r}")
            except Exception as e:
                ctx.log("error", f"forward failed: {e!r}")

    async def _do_forward(self, ctx, event, cfg):
        # 风控
        await ctx.engine.acquire("forward_message", peer_id=cfg["target_chat_id"])
        target = int(cfg["target_chat_id"])
        mode = cfg.get("mode", "forward_native")
        if mode == "forward_native":
            await event.message.forward_to(target)
        elif mode == "copy_text":
            text = (cfg.get("header", "") + (event.message.text or ""))
            await ctx.client.send_message(target, text or "(empty)")
        elif mode == "quote":
            src = await event.get_chat()
            chat_label = getattr(src, "title", None) or getattr(src, "username", None) or str(event.chat_id)
            body = f"📨 来自 {chat_label}\n\n{event.message.text or '(no text)'}"
            await ctx.client.send_message(target, body)
        elif mode == "link_only":
            link = _build_msg_link(event)
            await ctx.client.send_message(target, link)


def _match_source(event, cfg) -> bool:
    kind = cfg.get("source_kind", "all")
    if kind == "all":
        return True
    if kind == "peers":
        peers = set(cfg.get("source_peers") or [])
        return event.chat_id in _expand_chat_id(event.chat_id) and bool(peers & _expand_chat_id(event.chat_id))
    if kind == "keyword":
        kw = (cfg.get("keyword") or "").strip().lower()
        if not kw:
            return False
        return kw in (event.message.text or "").lower()
    return False


def _expand_chat_id(cid: int) -> set[int]:
    """复用 auto_reply 同名工具：处理 -100 / -xx / xxx 三种格式等价"""
    s = {cid}
    if cid is None: return s
    sid = str(cid)
    if sid.startswith("-100"):
        bare = sid[4:]
        s.add(int(bare))
        s.add(-int(bare))
    elif sid.startswith("-"):
        bare = sid[1:]
        s.add(int(bare))
        s.add(int("-100" + bare))
    else:
        s.add(-cid)
        s.add(int("-100" + sid))
    return s


def _build_msg_link(event) -> str:
    cid = event.chat_id
    mid = event.message.id
    sid = str(cid)
    if sid.startswith("-100"):
        return f"https://t.me/c/{sid[4:]}/{mid}"
    return f"消息引用：chat={cid}, id={mid}"


PLUGIN_CLASS = ForwardPlugin
```

### 3.2 Dry-run 扩展

```python
# api/rules.py 现有 dry-run 端点里：
elif rule.feature_key == "forward":
    cfg = body.config or rule.config
    matched = _match_source(_FakeEvent(...), cfg)
    return {"matched": matched, "output": f"would forward to {cfg.get('target_chat_id')}"}
```

### 3.3 前端规则编辑器（Forward.tsx）

仿照 `Features/AutoReply.tsx` 的结构（左侧规则列表，右侧编辑器，底部 dry-run）。

关键字段控件：

```tsx
<Select value={cfg.source_kind} onChange=...>
  <option value="all">所有 incoming 消息</option>
  <option value="peers">指定 peer 列表</option>
  <option value="keyword">关键词触发</option>
</Select>

{cfg.source_kind === "peers" && (
  <Textarea
    placeholder="每行一个 chat_id（例：-1001234567890）"
    value={peersText}
    onChange={...}     // ← 用独立 state，避免 #3 提到的换行 bug
  />
)}

{cfg.source_kind === "keyword" && (
  <Input value={cfg.keyword} onChange=... placeholder="关键词" />
)}

<Input value={cfg.target_chat_id} type="number" placeholder="目标 chat_id" />

<RadioGroup value={cfg.mode}>
  <Radio value="forward_native">原生转发（携带原作者）</Radio>
  <Radio value="copy_text">复制文本（不显示原作者）</Radio>
  <Radio value="quote">引用包装（带"来自 X"前缀）</Radio>
  <Radio value="link_only">仅发链接（公开群可点）</Radio>
</RadioGroup>

<Switch checked={cfg.include_media}>包含含媒体的消息</Switch>

<Textarea value={cfg.header} placeholder="copy/quote 模式可加固定前缀" />
```

底部 "试运行"：和 auto_reply 一致，输入样例文本 + chat_id → 调 dry-run。

## 4. 验收清单

```bash
# 后端
cd backend && pytest -v && ruff check .

# 前端
cd frontend && pnpm run build

# 手测
# 1. 账号详情 → 功能开关 → 启用"消息转发" → 点配置进入 Forward.tsx
# 2. 新建规则：source_kind=keyword，keyword="紧急"，target_chat_id=<你自己收藏夹 -1001>
# 3. 在小群发"紧急 测试一下" → 收藏夹应收到"📨 来自 ... 紧急 测试一下"
# 4. 切 mode=forward_native → 重测，收藏夹应该是原生转发气泡
# 5. 模拟 FloodWait（在小群连发 30 条）→ 看 runtime_log 应有 "forward floodwait Xs, sleep & retry once"
```

## 5. 完成报告模板

```markdown
## 完成报告 — Session #5

- [x] forward 插件主体（4 种 mode）
- [x] 风控接入（forward_message 动作）
- [x] FloodWait 重试逻辑
- [x] dry-run 端点扩展
- [x] 前端 Forward.tsx 编辑器
- 改动文件：XX 个
- 测试：pytest XX passed
- 验收已通过手测：XX/5 项
- 已知遗留：（如 link_only 仅支持公开群，私群应给 fallback 等）
```
