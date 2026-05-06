# Agent D：插件框架 + Features API

> 你是新会话里的工程师。**先读完整份 plan**，再读引用的只读文件，然后开干。注释一律中文。

## 目标

实现插件框架与内置功能：
- 插件基类与 Hook 体系
- 5 个内置插件（auto_reply / forward / group_admin / scheduler / monitor）
- 插件加载器与沙箱
- Features API（功能矩阵 + 规则 CRUD）

## 项目根目录

`/Users/anoyou/Desktop/telebot`

## 必读（只读，禁止修改）

1. `/Users/anoyou/Desktop/telebot/teleuserbot.md`（PRD：§4-C~G 功能列表、§4-H 插件市场、§4-J Hook 体系）
2. `/Users/anoyou/Desktop/telebot/CONTRACTS.md`（契约总览，重点读插件 Hook 部分）
3. `/Users/anoyou/Desktop/telebot/backend/app/db/models/feature.py`（Feature / AccountFeature）
4. `/Users/anoyou/Desktop/telebot/backend/app/db/models/rule.py`（Rule）

## 要写的文件（白名单）

### 插件框架
- `backend/app/worker/plugins/base.py`：Plugin 基类 + PluginContext
- `backend/app/worker/plugins/loader.py`：插件加载器
- `backend/app/worker/plugins/sandbox.py`：插件沙箱（V1+ 实现）
- `backend/app/worker/plugins/manifest.py`：插件清单解析

### 内置插件
- `backend/app/worker/plugins/builtin/auto_reply/plugin.py`：自动回复
- `backend/app/worker/plugins/builtin/forward/plugin.py`：消息转发
- `backend/app/worker/plugins/builtin/group_admin/plugin.py`：群组管理
- `backend/app/worker/plugins/builtin/scheduler/plugin.py`：定时任务
- `backend/app/worker/plugins/builtin/monitor/plugin.py`：消息监控

### REST API
- `backend/app/api/features.py`：功能矩阵 + 账号功能开关
- `backend/app/api/rules.py`：规则 CRUD（按 feature_key 分类）

### 服务层
- `backend/app/services/feature_service.py`：功能服务

## 插件 Hook 接口（CONTRACTS.md）

```python
class PluginContext:
    account_id: int
    feature_key: str
    config: dict[str, Any]
    rules: list[Rule]
    client: TelegramClient
    engine: RateLimitEngine
    redis: redis.Redis
    log: Callable[..., None]

class Plugin:
    key: str
    display_name: str
    
    async def on_startup(self, ctx: PluginContext) -> None: ...
    async def on_shutdown(self, ctx: PluginContext) -> None: ...
    async def on_message(self, ctx: PluginContext, event: events.NewMessage.Event) -> None: ...
    async def on_command(self, ctx: PluginContext, cmd: str, args: list[str], event: events.NewMessage.Event) -> bool: ...
```

## 自检命令

```bash
cd /Users/anoyou/Desktop/telebot/backend
python -m pytest app/tests/test_plugin_loader.py -v
python -m pytest app/tests/test_auto_reply.py -v
python -m pytest app/tests/test_forward_plugin.py -v

# API 测试
curl -X GET http://localhost:8000/api/feature-matrix
curl -X GET http://localhost:8000/api/accounts/1/features
```

## 汇报格式

```
✅ Agent D 完工汇报

实现功能：
- [x] 插件基类与 Hook 体系
- [x] 插件加载器
- [x] 5 个内置插件实现
- [x] 功能矩阵 API
- [x] 规则 CRUD API
- [x] 功能服务层

自检结果：
- 测试通过：X/Y
- 插件可加载：✅/❌
- API 可访问：✅/❌

待其他 Agent：
- B 在 worker 中加载插件
- C 提供 RateLimitEngine 给插件使用
- E 实现功能矩阵前端界面
```