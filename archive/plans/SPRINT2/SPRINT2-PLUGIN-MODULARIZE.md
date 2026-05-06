# Sprint 2 — Session #4：插件模块化（tier C 全套）

> 工时：约 5-7 天，分 A / B / C 三个阶段
> 依赖：无（**但 #5 必须等 A 阶段完成才能起**）
> 优先级：与 #1/#2/#3 同时启动，优先把 A 阶段在 Day 2 前交付以解锁 #5

## 0. 三个阶段切片

| 阶段 | 目标 | 工时 | 交付完成意味着 |
|------|------|------|--------------|
| **A** | 把现有 `plugins/builtin/*.py` 重组为目录结构 + 加 manifest + loader 改成扫描目录 | 1.5 天 | #5 可起 |
| **B** | 用户自上传 zip 安装 + 签名校验 + DB 记录 + 启用/禁用/卸载 | 2 天 | 用户能从前端拖 zip 装第三方插件 |
| **C** | apt 风格仓库订阅（多源）+ 最小权限沙箱（限制可调用的 telethon API 范围） | 2-3 天 | 整套 modular 系统 |

## 1. 文件白名单

> 各阶段都在同一个会话内推进，但合并到 main 时按阶段切 PR。

### 阶段 A
- 把 `backend/app/worker/plugins/builtin/{auto_reply,forward,group_admin,monitor,scheduler}.py` 改成目录：
  ```
  builtin/
    auto_reply/
      __init__.py        # from .plugin import AutoReplyPlugin
      manifest.py        # MANIFEST = {...}
      plugin.py          # 原 auto_reply.py 全部内容
    forward/
      __init__.py
      manifest.py
      plugin.py          # 占位骨架
    ... 同上
  ```
- `backend/app/worker/plugins/manifest.py`（**新建**）— `Manifest` dataclass 定义
- `backend/app/worker/plugins/loader.py` — 改成扫描 `builtin/*/manifest.py` + 后续 `installed/*/manifest.py`
- `backend/app/worker/plugins/base.py` — 不动（保留 Plugin 基类）

### 阶段 B
- `backend/app/db/models/plugin.py` — 扩展 `PluginInstall` 增加 `source(builtin/zip/repo)`、`version`、`manifest_json`、`signature_ok`、`enabled` 字段
- `backend/app/services/plugin_install_service.py`（**新建**）— zip 解析 / 签名校验 / 解压到 `data/plugins/installed/{key}/`
- `backend/app/api/plugins_install.py`（**新建**）—
  - `GET /api/plugins/installed`
  - `POST /api/plugins/install/upload` (multipart zip)
  - `POST /api/plugins/{key}/enable` / `/disable`
  - `DELETE /api/plugins/{key}`
- `backend/alembic/versions/0005_plugin_install.py`
- 前端 `pages/Settings/PluginManager.tsx`（**新建**）

### 阶段 C
- `backend/app/db/models/plugin.py` — 加 `PluginRepo` 表
- `backend/app/services/plugin_repo_service.py`（**新建**）— 拉远程 `manifest.json` 列表
- `backend/app/api/plugins_install.py` — 加 `/repos`、`/install/from-repo` 端点
- `backend/app/worker/plugins/sandbox.py`（**新建**）— 权限控制装饰器
- `backend/app/worker/plugins/manifest.py` — 增加 `permissions: list[str]` 字段
- `backend/app/worker/plugins/loader.py` — 加载时按 manifest.permissions 包装 ctx.client

### 不要动
account/auth/login/rate_limit/command 任何部分；前端 auto_reply 编辑器；worker/runtime.py（除非加一个加载入口调用）。

## 2. 关键设计

### 2.1 Manifest 结构（阶段 A 定型，B/C 扩展）

```python
# worker/plugins/manifest.py
from dataclasses import dataclass, field

@dataclass
class Manifest:
    key: str                            # auto_reply / forward / xxx_yyy
    display_name: str                   # 自动回复
    version: str = "0.1.0"
    author: str = "builtin"
    description: str = ""
    requires_features: list[str] = field(default_factory=list)  # 依赖其它插件
    config_schema: dict | None = None   # JSON schema for rule.config (前端编辑器用)
    # ===== 阶段 C 新增 =====
    permissions: list[str] = field(default_factory=lambda: ["send_message", "edit_message", "read_chat"])
    # 可选：pre/post hook
    on_install: str | None = None       # python module path of optional installer
```

每个插件目录里 `manifest.py` 顶层导出 `MANIFEST: Manifest`。

### 2.2 Loader 扫描策略

```python
# worker/plugins/loader.py
def discover_plugins() -> dict[str, type[Plugin]]:
    out = {}
    base = Path(__file__).parent
    for sub in (base / "builtin").iterdir():
        if not sub.is_dir() or sub.name.startswith("_"): continue
        out.update(_load_dir(sub, source="builtin"))
    installed_root = settings.data_dir / "plugins" / "installed"
    if installed_root.exists():
        for sub in installed_root.iterdir():
            out.update(_load_dir(sub, source="installed"))
    return out

def _load_dir(path: Path, source: str) -> dict[str, type[Plugin]]:
    spec = importlib.util.spec_from_file_location(
        f"plugin_{path.name}", path / "__init__.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    cls = getattr(mod, "PLUGIN_CLASS", None)
    manifest = getattr(mod, "MANIFEST", None)
    if not cls or not manifest:
        log.warning("plugin %s missing PLUGIN_CLASS or MANIFEST, skip", path)
        return {}
    cls._manifest = manifest
    cls._source = source
    return {manifest.key: cls}
```

每个 `__init__.py` 出 `PLUGIN_CLASS` 和 `MANIFEST` 两个常量。

### 2.3 阶段 B：zip 上传

- 用户从前端选 `myplugin-0.1.0.zip` 上传
- 后端解压到临时目录 → 校验有 `manifest.py` 和 `__init__.py` 和 `plugin.py`
- 校验 `MANIFEST.key` 不与 builtin 冲突
- 签名（先做最简单：上传时附 `.sig` 文件 + 公钥环境变量 `PLUGIN_PUBKEY`，不通过则 `signature_ok=False` 且必须管理员手动确认才能启用；无 sig 文件时 `signature_ok=None`，前端显示警告）
- 通过则移到 `data/plugins/installed/{key}/`，写 `plugin_install` 表
- 通知所有 worker `reload_plugins` 重新扫描

### 2.4 阶段 C：仓库 + 沙箱

仓库结构（远程 `https://example.com/plugins/index.json`）：

```json
{
  "name": "Official Repo",
  "plugins": [
    {"key": "weather", "version": "1.2.0", "url": "https://.../weather-1.2.0.zip", "sig_url": "..."}
  ]
}
```

`/api/plugins/repos` CRUD 维护多个仓库 URL。前端"插件市场"页拉所有仓库 → 列表 → 点装。

**沙箱**最小可行版：

```python
# worker/plugins/sandbox.py
ALLOWED_API = {
    "send_message": ["send_message", "respond"],
    "edit_message": ["edit", "edit_message"],
    "read_chat": ["get_messages", "get_chat", "iter_messages"],
    "send_file": ["send_file"],
    "join_chat": ["join_chat"],
    "delete_message": ["delete_messages"],
}

class SandboxClient:
    def __init__(self, real: TelegramClient, perms: list[str]):
        self._real = real
        self._allowed = set()
        for p in perms:
            self._allowed.update(ALLOWED_API.get(p, []))

    def __getattr__(self, name):
        if name not in self._allowed:
            raise PermissionError(f"plugin lacks permission to call client.{name}; declare in manifest.permissions")
        return getattr(self._real, name)
```

`PluginContext.client` 在 builtin 插件传 real client，installed/repo 插件传 `SandboxClient(real, manifest.permissions)`。

## 3. 阶段切交点

### 阶段 A 完成验收（**这之后通知 Session #5 开工**）：

```bash
cd backend
pytest -v                # 全绿（auto_reply 等所有 builtin 测试还能跑）
python -c "from app.worker.plugins.loader import discover_plugins; print(discover_plugins())"
# 输出：{'auto_reply': <class>, 'forward': <class>, 'group_admin': <class>, ...}
```

并且：
- `backend/app/worker/plugins/builtin/auto_reply/manifest.py` 文件存在
- `backend/app/worker/plugins/builtin/auto_reply/plugin.py` 内容 = 原 `auto_reply.py`
- worker 实际启动后能加载所有 builtin 插件，TG 收"hello"还能 reply

**及时把这两个事实在团队群同步**，#5 拿到信号才起。

### 阶段 B 完成验收

```bash
# 装一个示例 plugin（手工打包一个 hello 插件）
zip -r hello-plugin.zip hello/
curl -X POST -F "file=@hello-plugin.zip" -F "signature=..." \
  -b "auth_token=..." http://localhost:8000/api/plugins/install/upload
# DB 应有一行 plugin_install where key='hello' source='zip'
# 在前端 PluginManager 看到列表 → 点启用 → worker 日志显示 "loaded plugin: hello"
```

### 阶段 C 完成验收

```bash
# 配一个仓库
curl -X POST -d '{"name":"local","url":"http://localhost:9999/index.json"}' \
  http://localhost:8000/api/plugins/repos
# 前端"插件市场"刷新看到列表
# 点装 → 后端从 url 下载 zip → 校验 sig → 解压 → 启用
# 该插件如果调 client.delete_messages 但 manifest 没声明 → 抛 PermissionError，runtime_log 应该有记录
```

## 4. 完成报告模板

```markdown
## 完成报告 — Session #4

### 阶段 A
- [x] builtin 5 个插件全部目录化
- [x] Manifest 类定型
- [x] loader 改扫描目录
- [x] pytest 全绿
- 阶段 A 完成时间：YYYY-MM-DD HH:MM（**此时通知 Session #5 起**）

### 阶段 B
- [x] zip 上传 + 签名校验 + DB 记录
- [x] 启用/禁用/卸载 IPC reload
- [x] 前端 PluginManager 页面

### 阶段 C
- [x] 仓库订阅（多源）
- [x] 权限沙箱（SandboxClient）
- [x] 插件市场前端页

- 改动文件：XX 个
- 测试：pytest XX passed
- 已知遗留：（如 SandboxClient 不能拦截 raw API、签名机制简陋等，写明并归 V1.5）
```
