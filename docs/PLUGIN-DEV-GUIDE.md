# TeleBot 插件开发指南

> 本文档涵盖插件开发全流程：本地插件、远程插件、框架约束、调试建议。

---

## 目录

1. [快速开始](#1-快速开始)
2. [插件结构](#2-插件结构)
3. [Plugin 基类](#3-plugin-基类)
4. [PluginContext](#4-plugincontext)
5. [Manifest 元数据](#5-manifest-元数据)
6. [命令系统](#6-命令系统)
7. [消息监听](#7-消息监听)
8. [Conversation 工具](#8-conversation-工具)
9. [远程插件](#9-远程插件)
10. [清理生命周期（cleanup）](#10-清理生命周期cleanup)
11. [安全边界](#11-安全边界)
12. [前端集成](#12-前端集成)
    - [模式概览](#模式概览)
    - [模式 A：规则驱动配置页](#模式-a规则驱动配置页forward--autoreply--scheduler--autorepeat)
    - [模式 A 补充：后端 Dry-Run 适配](#模式-a-补充后端-dry-run-适配)
    - [模式 B：单配置对象页](#模式-b单配置对象页game24)
    - [模式 C：Schema 驱动弹窗](#模式-cschema-驱动弹窗configdialog)
    - [适配自检清单](#适配自检清单)
13. [调试建议](#13-调试建议)
14. [安全与合规](#14-安全与合规)
15. [完整示例](#15-完整示例)

---

## 1. 快速开始

### 文件结构

```
plugins/installed/{插件名}/
├── __init__.py        # 导出 PLUGIN_CLASS 和 MANIFEST
├── manifest.py        # Manifest 元数据
├── plugin.py          # 插件主类
└── (其他模块)
```

### 最小可运行插件

**plugin.py：**
```python
from app.worker.plugins.base import Plugin, register

@register
class PingPlugin(Plugin):
    key = "ping"
    display_name = "Ping"

    async def on_command(self, ctx, cmd, args, event) -> bool:
        if cmd == "ping":
            await event.edit("pong")
            return True
        return False
```

**manifest.py：**
```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="ping",
    display_name="Ping",
    version="0.1.0",
    author="example",
    description="响应 ping 命令",
)
```

**__init__.py：**
```python
from .manifest import MANIFEST
from .plugin import PingPlugin

PLUGIN_CLASS = PingPlugin
__all__ = ["PLUGIN_CLASS", "MANIFEST"]
```

放进 `plugins/installed/ping/` 后重启 worker 即可。

---

## 2. 插件结构

### 目录约定

```
backend/app/worker/plugins/
├── base.py              # Plugin 基类 + register 装饰器
├── manifest.py          # Manifest 数据类
├── loader.py            # 插件加载器 + 热重载 + generation guard
└── builtin/             # 内置插件
    ├── game24/
    └── forward/

plugins/installed/       # 远程/用户安装的插件
├── translate/
└── (更多插件...)
```

### 生命周期

```
loader._load_all()
  → scan builtin/ + plugins/installed/
  → import plugin.py + manifest.py
  → 验证 Manifest 合法性
  → 实例化 Plugin 子类
  → 调用 on_startup(ctx)

热重载 (reload_plugin):
  → state.generation += 1          # generation guard
  → 旧插件: on_shutdown(ctx)
  → 重新 import + 实例化
  → 新插件: on_startup(ctx)

消息派发:
  → 检查 ctx.generation == state.generation
  → 跳过过期 handler（竞态保护）
  → 调用 on_command / on_message
```

---

## 3. Plugin 基类

```python
class Plugin:
    # === 必须设置 ===
    key: str                          # 唯一标识
    display_name: str                 # 显示名

    # === 可选配置 ===
    message_channels: list[str]       # 监听方向: ["group", "private", "channel", "outgoing"]
    description: str = ""             # 描述（用于帮助系统）

    # === 生命周期钩子 ===
    async def on_startup(self, ctx: PluginContext) -> None:
        """插件激活时调用一次。"""

    async def on_shutdown(self, ctx: PluginContext) -> None:
        """插件关停前调用一次。必须幂等。"""

    # === 事件处理 ===
    async def on_message(self, ctx: PluginContext, event) -> None:
        """消息事件回调。"""

    async def on_command(self, ctx: PluginContext, cmd: str, args: list[str], event) -> bool:
        """命令派发回调。返回 True 表示已处理。"""
        return False
```

### 注册

```python
@register
class MyPlugin(Plugin):
    key = "my_plugin"
    ...
```

`@register` 装饰器把插件类注册到全局表，loader 通过 key 查找。

---

## 4. PluginContext

```python
@dataclass
class PluginContext:
    account_id: int
    feature_key: str
    config: dict           # rule.config
    rules: list            # 规则列表
    client: TelegramClient | None
    engine: Any            # RateLimitEngine
    redis: Any             # redis.asyncio.Redis
    log: Callable          # 日志函数
    generation: int        # generation guard 计数

    # 工具方法
    async def conversation(self, peer, timeout=30) -> Conversation:
        """创建与 bot 的对话会话。"""
```

---

## 5. Manifest 元数据

### 必填字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `key` | str | 唯一标识，与 Plugin.key 一致 |
| `display_name` | str | 显示名称 |
| `version` | str | 语义化版本（如 `1.0.0`） |
| `author` | str | 作者 |
| `description` | str | 功能描述，用于帮助系统 |

### 可选字段

| 字段 | 类型 | 说明 |
|------|------|------|
| `permissions` | list | 权限声明，默认 `["send_message", "edit_message", "read_chat"]` |
| `config_schema` | dict | JSON Schema，有配置的插件必须写 |
| `requires_features` | list | 依赖的其他插件 key |

### 完整示例

```python
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="my_plugin",
    display_name="我的插件",
    version="1.0.0",
    author="your_name",
    description="插件功能描述",
    permissions=["send_message", "edit_message", "read_chat"],
    config_schema={
        "type": "object",
        "properties": {
            "api_key": {
                "type": "string",
                "title": "API Key",
                "level": "global",
            },
            "target_chat": {
                "type": "string",
                "title": "目标聊天 ID",
                "level": "account",
            },
        },
    },
    requires_features=[],
)
```

### config_schema 配置规范

`config_schema` 遵循 JSON Schema 规范，额外支持 `level` 字段控制配置的作用域：

| level | 作用域 | 存储位置 | 说明 |
|-------|--------|---------|------|
| `global` | 全局（所有账号共享） | plugin_config | API Key、通用参数等 |
| `account` | 单个账号 | rule.config | 聊天 ID、行为开关等 |
| （不填） | 默认 account | rule.config | 向后兼容 |

**优先级：** 账号级配置 > 插件全局配置 > config_schema 中的 default

**前端渲染：** 有 config_schema 的插件，点击"配置"按钮会弹出 Dialog 表单：
- `level: global` 的字段 → 全局配置区（所有账号共享）
- `level: account` 的字段 → 账号配置区（按账号隔离）
- 无 level 的字段 → 默认按账号隔离

**必填字段验证清单（内置插件）：**

| 插件 | config_schema | 状态 |
|------|--------------|------|
| forward | ✅ target_chat_id, mode | 已有 |
| game24 | ✅ time_limit, prize, max_players | 已补 |
| scheduler | ✅ default_notify, max_tasks | 已补 |
| translate | ✅ default_lang, llm_provider | 已补 |

### Manifest 验证

安装插件时（远程插件），框架会验证：

```python
def validate_manifest(manifest: dict) -> tuple[bool, str]:
    required = ["key", "display_name", "description", "version"]
    for field in required:
        if not manifest.get(field):
            return False, f"缺少必填字段: {field}"
    return True, "ok"
```

---

## 6. 命令系统

### 命令派发流程

1. 消息到达 → 检查前缀匹配
2. 提取命令名和参数
3. 检查别名（贪心最长匹配）
4. 遍历已注册插件，调用 `on_command(ctx, cmd, args, event)`
5. 第一个返回 True 的插件接管，后续不再传递

### on_command 签名

```python
async def on_command(
    self,
    ctx: PluginContext,       # 上下文
    cmd: str,                 # 命令名（如 "weather"）
    args: list[str],          # 参数列表
    event: NewMessage.Event,  # 原始事件
) -> bool:
    """返回 True 表示已处理。"""
```

### 别名支持

命令别名支持多词贪心匹配和参数透传：

```
用户: ,fy zh hello
→ 别名 "fy zh" → "translate"
→ 参数透传: translate hello
```

---

## 7. 消息监听

```python
class MyPlugin(Plugin):
    message_channels = ["group", "private"]

    async def on_message(self, ctx: PluginContext, event) -> None:
        """监听所有匹配方向的消息。"""
        if event.outgoing:
            return  # 忽略自己发的
        # 处理逻辑
```

### channels 类型

| 值 | 说明 |
|---|------|
| `group` | 群组消息 |
| `private` | 私聊消息 |
| `channel` | 频道消息 |
| `outgoing` | 自己发出的消息 |

---

## 8. Conversation 工具

与其他 Bot 交互的工具类（如 @BotFather）：

```python
async with ctx.conversation("@BotFather") as conv:
    await conv.send("/newbot")
    resp = await conv.get_response(timeout=30)
    print(resp.text)

    # 点击内联按钮
    await conv.click_button(msg, row=0, col=0)
```

### API

| 方法 | 说明 |
|------|------|
| `send(text, **kwargs)` | 发送文本/文件/图片 |
| `get_response(timeout)` | 等对方回复 |
| `click_button(msg, row, col)` | 点击 inline keyboard |
| `mark_read()` | 标记已读 |
| `close()` | 清理 handler |

### 超时处理

```python
from app.worker.conversation import ConversationTimeout

try:
    resp = await conv.get_response(timeout=10)
except ConversationTimeout:
    await conv.send("超时了，请重试")
```

---

## 9. 远程插件

### 安装方式

**通过 Web UI：**
1. 进入远程插件页面
2. 输入 GitHub 仓库地址或子目录 URL
3. 点击安装

**通过 Bot 命令：**
```
/plugin install https://github.com/user/repo
/plugin list
/plugin enable weather
/plugin disable weather
/plugin remove weather
```

### 远程插件规范

远程仓库必须包含 `manifest.json`：

```json
{
  "name": "weather",
  "display_name": "天气查询",
  "description": "查询天气信息",
  "author": "community",
  "version": "1.0.0",
  "entry": "weather.py",
  "min_telebot_version": "0.9.0",
  "commands": ["weather", "w"],
  "cleanup_mode": "no-op",
  "tags": ["weather", "utility"]
}
```

**必填字段：** name, display_name, description, author, version, entry

### 安装流程

```
1. git clone 到 plugins/installed/{name}/
2. 读取 manifest.json → validate_manifest() 验证
3. 验证通过 → 注册到数据库
4. 调用 reload_plugin() 热加载
5. 验证失败 → 删除目录，返回错误
```

### Registry 机制

支持从远程 registry 同步可用插件列表：

```json
{
  "plugins": [
    {
      "name": "weather",
      "display_name": "天气查询",
      "source_url": "https://github.com/user/repo",
      "version": "1.0.0"
    }
  ]
}
```

---

## 10. 清理生命周期（cleanup）

参考 TeleBox 的三种风格：

| 风格 | 适用场景 | cleanup 行为 |
|------|---------|-------------|
| `resource` | 持有定时器/子进程/网络连接 | 真正释放资源 |
| `reset` | 持有 db/缓存/配置引用 | 引用置空 |
| `no-op` | 流程型插件，无长期资源 | 空方法 + 注释说明 |

### 统一约束

- **必须幂等**：重复调用不报错
- **不应依赖用户输入**
- **不应误伤系统级资源**：systemd 服务、iptables 等不要在 reload 时停掉

### 实现

```python
class MyPlugin(Plugin):
    _timer = None
    _db = None

    async def on_startup(self, ctx):
        self._timer = create_timer(...)
        self._db = get_db()

    async def on_shutdown(self, ctx):
        """resource 风格：释放资源"""
        if self._timer:
            self._timer.cancel()
            self._timer = None
        if self._db:
            self._db = None
```

---

## 11. 安全边界

### 命令前缀

- 所有命令必须有明确前缀（如 `,` 或自定义）
- 前缀由 `ctx.config` 中的 `prefix` 控制

### 权限声明

Manifest 中的 `permissions` 字段声明插件需要的能力：

| 权限 | 说明 |
|------|------|
| `send_message` | 发送消息 |
| `edit_message` | 编辑消息 |
| `read_chat` | 读取聊天历史 |

默认给三类常用能力，内置插件漏写时不会被沙箱拦截。

### 禁止行为

- 不允许 `os.system` / `subprocess` 执行系统命令（除非显式声明）
- 不允许把明文 key 写入日志
- 不允许持久化完整隐私消息到外部系统
- 对外部请求必须做超时和异常处理

---

## 12. 前端集成

插件前端配置页分三种模式，按复杂度递增：

### 模式概览

| 模式 | 适用场景 | 典型插件 | 配置入口 |
|------|---------|---------|---------|
| **A — 规则驱动** | 每条规则独立配置，需 CRUD + 试运行 | forward、auto_reply、scheduler、autorepeat | 专属配置页 |
| **B — 单配置对象** | 整个插件一份配置，无规则概念 | game24 | 专属配置页 |
| **C — Schema 驱动** | 轻量插件，不需要专属页面 | 无 config_schema 的插件 / 远程插件 | ConfigDialog 弹窗 |

**关键判断**：插件是否有 `config_schema` 且需要按「规则」管理多份配置？是 → 模式 A；只有一份全局/账号配置 → 模式 B；无专属页面需求 → 模式 C。

---

### 模式 A：规则驱动配置页（Forward / AutoReply / Scheduler / Autorepeat）

规则驱动插件每条 rule 存储独立的 `config` JSON，通过 CRUD API 管理。前端专属页面提供：规则列表 + 创建/编辑对话框 + 试运行（dry-run）。

#### 适配清单（6 处必改）

| # | 文件 | 修改内容 |
|---|------|---------|
| 1 | `frontend/src/api/types.ts` | 添加 `XxxRuleConfig` 接口（描述单条规则的 config 字段） |
| 2 | `frontend/src/pages/Features/XxxConfig.tsx` | **新建**：规则列表页（参考 `AutoReply.tsx` 或 `Forward.tsx`） |
| 3 | `frontend/src/App.tsx` | ① import 新页面组件 ② 添加路由 `:aid/features/xxx` ③ 在 `FEATURE_CONFIG_PAGES` 中添加 key |
| 4 | `frontend/src/pages/Accounts/Detail.tsx` | 在 `FEATURE_CONFIG_PAGE_KEYS` Set 中添加 key |
| 5 | `frontend/src/pages/Extensions.tsx` | 在 `FEATURE_CONFIG_PAGE_KEYS` Set 中添加 key |
| 6 | `backend/app/db/models/feature.py` | 添加 `FEATURE_XXX = "xxx"` 常量（如已有可跳过） |

#### 1. types.ts — RuleConfig 接口

```typescript
// frontend/src/api/types.ts
export interface AutorepeatRuleConfig {
  target_chat_id: number;   // 必填
  time_window?: number;     // 可选，默认 300
  min_users?: number;       // 可选，默认 5
}
```

接口字段应与 `manifest.py` 中 `config_schema.properties` 一一对应，必填字段不加 `?`。

#### 2. 新建配置页面

创建 `frontend/src/pages/Features/XxxConfig.tsx`，核心结构：

```tsx
// 标准页面骨架（以 AutoReply 为模板）
import { useParams } from "react-router-dom";
// ... UI 组件导入

export function XxxConfig() {
  const { aid } = useParams<{ aid: string }>();
  const queryClient = useQueryClient();

  // ① 规则列表查询
  const { data: rules } = useQuery({
    queryKey: ["rules", Number(aid), "xxx"],
    queryFn: () => api.getRules(Number(aid), "xxx"),
  });

  // ② 创建/编辑对话框状态
  const [dialogOpen, setDialogOpen] = useState(false);
  const [editing, setEditing] = useState<RuleOut | null>(null);

  // ③ CRUD mutations（create / update / delete）
  // ④ 试运行 mutation（dry-run）
  // ⑤ 表单渲染 + 规则表格
}
```

**页面要素**：
- 顶部：返回按钮 + 标题 + 新建按钮
- 主体：规则表格（序号 / 关键字段 / 启用状态 / 操作按钮）
- 对话框：创建/编辑表单，字段来自 RuleConfig
- 试运行：选规则 → 填样本消息 → 显示命中结果

#### 3. App.tsx — 路由 + 注册

```tsx
// ① import
import { XxxConfig } from "@/pages/Features/XxxConfig";

// ② 路由（在 <Route path=":aid/features/game24"> 之后添加）
<Route path=":aid/features/xxx" element={<XxxConfig />} />

// ③ FEATURE_CONFIG_PAGES 注册
const FEATURE_CONFIG_PAGES: Record<string, { title: string; description: string }> = {
  auto_reply: { title: "自动回复", description: "..." },
  xxx:        { title: "插件显示名", description: "..." },
  // ...
};
```

路由路径格式固定为 `:aid/features/{plugin_key}`，`plugin_key` 必须与 `MANIFEST.key` 一致。

#### 4 & 5. FEATURE_CONFIG_PAGE_KEYS — 两个入口点

两个文件中的 `FEATURE_CONFIG_PAGE_KEYS` 必须同步添加：

```tsx
// Detail.tsx（账号详情页 → 插件列表"配置"按钮）
const FEATURE_CONFIG_PAGE_KEYS = new Set([
  "auto_reply", "autorepeat", "forward", "scheduler", "game24",
  "xxx",  // ← 新增
]);

// Extensions.tsx（插件中心 → 账号插件"配置"按钮）
const FEATURE_CONFIG_PAGE_KEYS = new Set([
  "auto_reply", "autorepeat", "forward", "scheduler", "game24",
  "xxx",  // ← 新增
]);
```

**作用**：Set 中的 key 会让"配置"按钮跳转到专属页面路由 `/:aid/features/xxx`；
不在 Set 中的 key 会弹 `ConfigDialog`（模式 C）。

#### 6. feature.py — 后端常量

```python
# backend/app/db/models/feature.py
FEATURE_XXX = "xxx"
```

此常量供 `rules.py` dry-run 分支和其它模块引用。

---

### 模式 A 补充：后端 Dry-Run 适配

规则驱动页面通常需要试运行功能，后端需同步适配 `rules.py`：

#### 插件侧导出 _dry_run_match

```python
# backend/app/worker/plugins/builtin/xxx/plugin.py

def _dry_run_match(cfg: dict, text: str, chat_id: int | None = None) -> tuple[bool, str | None]:
    """纯函数：给定规则 config + 样本消息，返回 (matched, output)。
    不访问 DB / Redis / 网络，仅做模式匹配逻辑判断。
    """
    # 匹配逻辑（与 on_message 中使用的判断一致）
    if cfg.get("target_chat_id") and chat_id == cfg["target_chat_id"]:
        return True, "命中目标群组"
    return False, None
```

```python
# backend/app/worker/plugins/builtin/xxx/__init__.py
from .plugin import _dry_run_match  # noqa: F401 — 供 API dry-run 导入
```

#### rules.py — 添加 dry-run 分支

```python
# backend/app/api/rules.py

# ① import
from ..db.models.feature import FEATURE_XXX
from ..worker.plugins.builtin.xxx.plugin import _dry_run_match as _xxx_dry_run_match

# ② 在 dry_run_rule() 函数中，在 fallback return 之前添加分支
#    ⚠️ 必须放在最后的 `return RuleDryRunResponse(matched=False, ...)` 之前！

if key == FEATURE_XXX:
    cfg = rule.config or {}
    matched, output = _xxx_dry_run_match(cfg, payload.sample_message, payload.sample_chat_id)
    logs = [
        {"step": "config", "msg": f"关键字段：{cfg.get('xxx_field', '(未设置)')}"},
        # ... 更多诊断步骤
    ]
    if not matched:
        logs.append({"step": "result", "msg": "未命中"})
    else:
        logs.append({"step": "result", "msg": "命中"})
    return RuleDryRunResponse(
        matched=matched,
        output=output,
        detail={"feature": key, "rule_id": rid, "logs": logs},
    )
```

**常见错误**：dry-run 分支放在 `return RuleDryRunResponse(matched=False, ...)` 之后 → 永远不可达。
**正确位置**：在所有已实现的 dry-run 分支之后、fallback return 之前。

---

### 模式 B：单配置对象页（Game24）

只有一份配置、无规则列表的插件，使用专属页面但不需要 CRUD 和 dry-run：

- 创建 `frontend/src/pages/Features/XxxConfig.tsx`，直接展示/编辑单个 config 对象
- 其余适配步骤与模式 A 相同（App.tsx 路由 + FEATURE_CONFIG_PAGES + 两个 PAGE_KEYS）
- 后端不需要 dry-run 分支

---

### 模式 C：Schema 驱动弹窗（ConfigDialog）

无专属页面的插件，在"配置"按钮点击后弹出 `ConfigDialog`，自动根据 `manifest.py` 中的 `config_schema` 渲染表单：

- `level: "global"` 的字段 → 全局配置区
- `level: "account"` 或无 level → 账号配置区
- **不需要**添加到 `FEATURE_CONFIG_PAGE_KEYS`，不需要创建页面文件
- `config_schema` 写好即可，ConfigDialog 自动渲染

```python
# config_schema 示例（适用于 ConfigDialog 自动渲染）
config_schema={
    "type": "object",
    "properties": {
        "api_key": {
            "type": "string",
            "title": "API Key",
            "level": "global",
        },
        "threshold": {
            "type": "number",
            "title": "阈值",
            "default": 5,
        },
    },
}
```

---

### 风格要求

- 深色主题卡片布局
- 与 TeleBot 现有页面风格一致
- React + TypeScript + TailwindCSS
- 新页面参考 `AutoReply.tsx`（规则驱动）或 `Game24Config.tsx`（单配置）的代码结构

### 适配自检清单

新增插件前端配置页后，逐项检查：

- [ ] `types.ts` 中 `XxxRuleConfig` 接口与 `manifest.py` config_schema 字段一致
- [ ] `App.tsx` 中路由路径 `:aid/features/{key}` 与插件 key 一致
- [ ] `App.tsx` 中 `FEATURE_CONFIG_PAGES` 包含该 key
- [ ] `Detail.tsx` 中 `FEATURE_CONFIG_PAGE_KEYS` 包含该 key
- [ ] `Extensions.tsx` 中 `FEATURE_CONFIG_PAGE_KEYS` 包含该 key
- [ ] 如需 dry-run：`plugin.py` 导出 `_dry_run_match`，`__init__.py` re-export，`rules.py` 在 fallback 之前添加分支
- [ ] 如需 dry-run：`feature.py` 中有 `FEATURE_XXX` 常量
- [ ] 前端 `pnpm build` 通过

---

## 13. 调试建议

### 快速自检

- [ ] `__init__.py` 是否导出 `PLUGIN_CLASS` 和 `MANIFEST`
- [ ] `MANIFEST.key` 是否和插件 class key 一致
- [ ] `permissions` 是否覆盖实际调用的方法
- [ ] `on_command` 签名是否是 5 参数
- [ ] 错误是否都被捕获并反馈给用户

### 常见问题

| 现象 | 原因 | 解决 |
|------|------|------|
| 插件被跳过 | MANIFEST 类型不对或导出缺失 | 检查 `__init__.py` |
| 命令没反应 | feature 未启用或前缀不匹配 | 检查 rule 配置和前缀 |
| 热重载后旧 handler 还在触发 | generation guard 未生效 | 检查 loader.py 版本 |
| 远程插件安装失败 | manifest.json 缺必填字段 | 检查 name/description/entry |
| cleanup 后插件状态异常 | cleanup 未幂等 | 重复调用测试 |

---

## 14. 安全与合规

- 不要把明文 key 写入日志
- 不要把完整隐私消息持久化到外部系统
- 对外部请求做超时和异常处理
- 对高风险操作（删消息、批量发送）加显式开关

---

## 15. 完整示例

### 天气查询插件

```python
# manifest.py
from app.worker.plugins.manifest import Manifest

MANIFEST = Manifest(
    key="weather",
    display_name="天气查询",
    version="1.0.0",
    author="community",
    description="查询天气信息，支持城市名",
    permissions=["send_message"],
    config_schema={
        "type": "object",
        "properties": {
            "api_key": {"type": "string", "description": "可选的 API Key"},
        },
    },
)
```

```python
# plugin.py
import httpx
from app.worker.plugins.base import Plugin, register

@register
class WeatherPlugin(Plugin):
    key = "weather"
    display_name = "天气查询"
    message_channels = ["group", "private"]

    async def on_command(self, ctx, cmd, args, event) -> bool:
        if cmd not in ("weather", "w"):
            return False

        city = " ".join(args) if args else "Beijing"
        try:
            async with httpx.AsyncClient() as client:
                geo = await client.get(
                    f"https://geocoding-api.open-meteo.com/v1/search?name={city}&count=1"
                )
                if not geo.json().get("results"):
                    await event.edit(f"未找到: {city}")
                    return True
                lat = geo.json()["results"][0]["latitude"]
                lon = geo.json()["results"][0]["longitude"]

                weather = await client.get(
                    f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current_weather=true"
                )
                data = weather.json()["current_weather"]
                temp = data["temperature"]
                wmo = data["weathercode"]

                await event.edit(f"🌤 {city}: {temp}°C (代码: {wmo})")
        except Exception as e:
            await event.edit(f"天气查询失败: {e}")

        return True
```

```python
# __init__.py
from .manifest import MANIFEST
from .plugin import WeatherPlugin

PLUGIN_CLASS = WeatherPlugin
MANIFEST_OBJ = MANIFEST

__all__ = ["PLUGIN_CLASS", "MANIFEST_OBJ"]
```

---

## 版本与兼容

- `0.x`：开发阶段，允许快速迭代
- `1.x`：接口稳定后
- 不要依赖私有内部模块路径
- 尽量只依赖 `Plugin` / `Manifest` / `PluginContext` 公开契约
- 新增行为优先通过 `config` 可选项实现
