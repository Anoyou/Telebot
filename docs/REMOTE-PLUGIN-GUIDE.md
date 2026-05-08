# TeleBot 远程插件管理系统 — 设计文档

## 1. 概述

为 TeleBot 搭建远程插件框架，支持从远程仓库（GitHub / 自定义 URL）安装、管理、更新插件。

### 目标
- 用户通过 Web UI 或 Bot 命令一键安装远程插件
- 插件有统一的元数据规范和验证机制
- 插件支持启用/禁用/卸载
- 后续只需提供远程插件地址即可导入

---

## 2. 插件规范（框架约束）

### 2.1 远程插件必须包含 `manifest.json`

```json
{
  "name": "weather",
  "display_name": "天气查询",
  "description": "查询天气信息，支持城市名和经纬度",
  "author": "TeleBox",
  "version": "1.0.0",
  "min_telebot_version": "0.9.0",
  "entry": "weather.py",
  "commands": ["weather", "w"],
  "cleanup_mode": "no-op",
  "tags": ["weather", "utility"],
  "license": "MIT"
}
```

**必填字段：**
| 字段 | 类型 | 说明 |
|------|------|------|
| `name` | string | 插件唯一标识（文件夹名） |
| `display_name` | string | 显示名称 |
| `description` | string | 功能描述（必填，参考 TeleBox 的 isValidPlugin 逻辑） |
| `author` | string | 作者 |
| `version` | string | 语义化版本号 |
| `entry` | string | 入口 Python 文件名 |

**可选字段：**
| 字段 | 类型 | 说明 |
|------|------|------|
| `min_telebot_version` | string | 最低 TeleBot 版本要求 |
| `commands` | list | 声明的命令列表（用于帮助系统） |
| `cleanup_mode` | string | cleanup 风格：`resource` / `reset` / `no-op` |
| `tags` | list | 标签，用于分类和搜索 |
| `license` | string | 许可证 |

### 2.2 插件 Python 入口规范

```python
# weather.py — 标准入口
from app.worker.plugins.base import Plugin, register, PluginContext

@register
class WeatherPlugin(Plugin):
    key = "weather"
    display_name = "天气查询"
    message_channels = ["group", "private"]

    async def on_startup(self, ctx: PluginContext) -> None:
        """插件激活时调用一次。"""
        pass

    async def on_shutdown(self, ctx: PluginContext) -> None:
        """插件关停前调用一次。必须幂等，重复调用不报错。"""
        pass

    async def on_command(self, ctx, cmd, args, event) -> bool:
        """命令处理入口。"""
        if cmd in ("weather", "w"):
            # 实现逻辑
            return True
        return False
```

### 2.3 Cleanup 生命周期（参考 TeleBox 三种风格）

| 风格 | 适用场景 | cleanup 行为 |
|------|---------|-------------|
| `resource` | 持有定时器/子进程/网络连接 | 真正释放资源 |
| `reset` | 持有 db/缓存/配置引用 | 引用置空 |
| `no-op` | 流程型插件，无长期资源 | 空方法 + 注释说明 |

**统一约束：**
- `cleanup()` 必须幂等（重复调用不报错）
- 不应依赖用户输入
- 不应误伤系统级资源

### 2.4 安全边界（参考 TeleBox）

- 命令触发器必须有明确前缀（如 `/weather` 或自定义前缀）
- 插件不允许直接访问 worker 的完整 Telegram client（通过 `ctx.client` 限制范围）
- 远程插件不允许执行系统命令（`os.system` / `subprocess`）除非显式声明

### 2.5 插件验证函数

```python
def validate_manifest(manifest: dict) -> tuple[bool, str]:
    """安装时强制验证 manifest.json"""
    required = ["name", "display_name", "description", "author", "version", "entry"]
    for field in required:
        if not manifest.get(field):
            return False, f"缺少必填字段: {field}"

    if not manifest["entry"].endswith(".py"):
        return False, "entry 必须是 .py 文件"

    ver = manifest.get("version", "")
    if not all(c.isdigit() or c in ".-" for c in ver):
        return False, f"版本号格式无效: {ver}"

    return True, "ok"
```

---

## 3. 远程 Registry 机制

### 3.1 Registry JSON 格式

```json
{
  "name": "TeleBot Community Plugins",
  "url": "https://github.com/Anoyou/telebot-plugins",
  "plugins": [
    {
      "name": "weather",
      "display_name": "天气查询",
      "description": "查询天气信息",
      "author": "community",
      "source_url": "https://github.com/Anoyou/telebot-plugins/weather",
      "version": "1.0.0",
      "tags": ["weather", "utility"],
      "min_telebot_version": "0.9.0"
    }
  ]
}
```

### 3.2 Registry URL 配置

Registry 地址存储在 TeleBot 配置中（数据库或 .env），支持多个 registry 源。

---

## 4. 数据库模型

```python
# remote_plugin 表
- id: int (pk, auto)
- name: str (unique, indexed)
- display_name: str
- description: str
- author: str
- source_url: str (git clone 地址)
- version: str
- installed_path: str (本地安装路径)
- enabled: bool (default True)
- cleanup_mode: str (resource/reset/no-op)
- created_at: datetime
- updated_at: datetime
```

---

## 5. API 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET | /api/remote-plugins | 列出已安装的远程插件 |
| POST | /api/remote-plugins/install | 安装 (body: {source_url}) |
| POST | /api/remote-plugins/{name}/enable | 启用 |
| POST | /api/remote-plugins/{name}/disable | 禁用 |
| POST | /api/remote-plugins/{name}/update | 更新 |
| DELETE | /api/remote-plugins/{name} | 卸载 |
| POST | /api/remote-plugins/registry/sync | 同步 registry |

---

## 6. 安装流程

```
1. 用户提供 source_url（GitHub 仓库或子目录）
2. git clone 到 plugins/installed/{name}/
3. 读取 manifest.json，调用 validate_manifest() 验证
4. 验证通过 → 注册到 remote_plugin 表
5. 调用 reload_plugin() 热加载
6. 验证失败 → 删除克隆目录，返回错误
```

---

## 7. Bot 命令

| 命令 | 说明 |
|------|------|
| /plugin list | 列出已安装远程插件 |
| /plugin install <url> | 从 URL 安装 |
| /plugin remove <name> | 卸载 |
| /plugin enable <name> | 启用 |
| /plugin disable <name> | 禁用 |

---

## 8. 前端页面

- 路由: /remote-plugins
- 深色主题卡片布局，与 TeleBot 现有风格一致
- 功能：安装输入框、插件列表、启用/禁用开关、卸载按钮
- Registry 同步按钮

---

## 9. 文件清单

| 文件 | 类型 | 说明 |
|------|------|------|
| backend/app/db/models/remote_plugin.py | 新建 | 数据库模型 |
| backend/app/api/remote_plugin.py | 新建 | API 路由 |
| backend/app/services/remote_plugin_service.py | 新建 | 业务逻辑 |
| backend/app/schemas/remote_plugin.py | 新建 | Pydantic schemas |
| backend/alembic/versions/0018_remote_plugin.py | 新建 | 数据库迁移 |
| frontend/src/pages/RemotePlugins/index.tsx | 新建 | 前端页面 |
| frontend/src/api/remotePlugin.ts | 新建 | 前端 API |
| frontend/src/types/remotePlugin.ts | 新建 | 类型定义 |
| docs/REMOTE-PLUGIN-GUIDE.md | 新建 | 插件开发指南 |

---

## 10. 约束总结（来自 TeleBox 参考）

| 约束 | TeleBox 做法 | TeleBot 采纳 |
|------|-------------|-------------|
| 必须有 description | isValidPlugin 验证 | manifest.json 必填 |
| cleanup 必须幂等 | 三种风格 + 幂等约束 | cleanup_mode 字段 + 验证 |
| 安全边界声明 | cmdHandlers 前缀约束 | commands 列表 + 前缀校验 |
| 插件验证 | 加载时 isValidPlugin() | install 时 validate_manifest() |
| 版本兼容 | 无 | min_telebot_version |
