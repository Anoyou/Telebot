# Agent Plans — 协议与归档规范

> 本目录只放**未完成 / 进行中 / 待启动**的 sprint plan。
> 已交付的 plan 一律 `mv` 到 `archive/plans/SPRINTN/`（见 §5）。
> 任意时刻 `ls agent-plans/` 应只看到 `README.md` + 当前/未来 plan。

历史交付：
- Sprint 2 → `archive/plans/SPRINT2/`
- Sprint 3 → `archive/plans/SPRINT3/`（ad-hoc，仅追溯）
- 当前 Sprint → 见本目录下的 `SPRINTn-*.md`

---

## 1. 跨会话强制约定（**所有会话必读**）

### 约定 A：`frontend/src/api/types.ts` 追加协议

每个会话**只能在文件末尾追加**自己的类型块，**不允许修改**他人 / 他会话已写的内容。每块前加注释行划界：

```ts
// ==================== SprintN #X 模块名 ====================
export interface FooBar { ... }
// ...
```

合并冲突时按 Session 编号顺序解决（编号小的在前），**绝不删除**他人块。

### 约定 B：Alembic 迁移编号

- 编号 **严格递增**，下一位置 = `head + 1`，**绝不重号**
- 多个会话并行时由汇总人协调编号（每个 sprint 启动时在 plan 里预先分配）
- 同号撞车 → 后到的改名 `NNNNb_xxx.py` + 改 `revision = "NNNNb"` + 让下游迁移的 `down_revision` 指向新名
- 跑 `alembic heads` 必须只有一个 head

### 约定 C：API 路由前缀

- 每个会话**必须在 `app/main.py`** 注册自己的 router，**只追加**一行
- 文件不动他人已注册的行
- 冲突合并时保留所有

### 约定 D：AI 密钥处理（**安全红线**）

- LLM API key（OpenAI/Anthropic/etc.）**必须 Fernet 加密**入库，复用 `app/crypto.py:encrypt_str/decrypt_str`，密钥源是 `master_key`
- 任何 GET 接口**绝不返回明文密钥**，只返回 `has_api_key: bool`
- worker 内解密后只在 LLMClient 持有，**绝不写入** runtime_log / audit_log
- 错误路径捕获后 `str(e)` 前先确认不含 api_key 子串

### 约定 E：插件目录修改

- builtin/ 下的 plugin 仅由维护人改；多会话并行时**不要**两个 session 改同一个 plugin
- 第三方插件放 `data/plugins/installed/<key>/`，不进版本控制
- Plugin 接口（`Plugin` 基类 / `Manifest` / `PluginContext`）一旦稳定不要改签名，要扩展用 `**kwargs`

---

## 2. 会话开启提示词模板

打开新 Claude Code 会话，进入 `/Users/anoyou/Desktop/telebot/`，第一句话粘贴：

```
项目路径：/Users/anoyou/Desktop/telebot
你是 Sprint N 的 [Wave/Session 标识] 会话。

请按 agent-plans/SPRINTn-XXX.md 的"文件白名单"
和"实现要点"严格执行。重要协议见 agent-plans/README.md 第 1 节。

完成后填写 plan 末尾的"完成报告"贴回。
```

把 `N` / `Wave/Session` / 文件名 换掉即可。

---

## 3. 共用基础设施

```bash
# 一键启动（推荐）：自动 docker / 迁移 / uvicorn / vite + 清理孤儿 worker
make up        # 同 ./scripts/up.sh
make restart   # 改代码后整套重启（首选！理由见 §4.1）
make down      # 整套停

# 单独跑某一项（调试时用）
make dev-up    # 仅 PG + Redis docker
make backend   # 前台 uvicorn --reload（⚠️ worker 子进程不会自动重启，不要长时间用）
make frontend  # 前台 vite

# 测试
cd backend && pytest -v
cd frontend && pnpm run build
ruff check backend/
```

worker 进程由 `worker/supervisor.py` 管理（mp spawn context + atexit/SIGTERM 守护），新模块**不要**直接 `mp.Process`，要复用 supervisor 接口。

---

## 4. 完成验收

每个会话完成后必须回填一段"完成报告"到 plan 文件末尾。汇总到 main 前最低验收：

- `pytest -q` 全绿
- `pnpm run build` 全绿
- `ruff check backend/` 全绿
- `alembic upgrade head` 不报错（在生产 PG 上跑过 dry-run）
- `alembic heads` 只一个 head
- 浏览器手测各模块入口能打开 + 关键操作不报红

### 4.1 必跑 `make restart`（**写代码改完最后一步**）

`make backend` 启动的 uvicorn 带 `--reload`，看似很方便，**但项目用 `mp.spawn` 起 worker 子进程**：

- `--reload` 检测到改动只会重启 **主进程**
- worker 子进程**不会**重启 → 跑的还是老代码
- import-time 副作用（`@builtin` 装饰器、`BUILTIN_FEATURES` 字典 seed）在主进程 reload 后**会重 seed**，但**老 worker 子进程**带的还是老字典
- 这是项目第二次因 "代码改了但 uvicorn 没重启" 出现幻觉式 bug 了（首次：0.2.0 升 0.2.1 时 PG 没升 0006；二次：0.4.1 升时 group_admin/monitor 被老 uvicorn 反复 seed 回 DB）

**正确的开发循环**：

```bash
# 改代码 → 跑测试
pytest -q

# 测试绿了 → 整套重启（杀掉所有 telebot 进程 + 清孤儿 worker + 重启）
make restart

# 浏览器硬刷（cmd+shift+r）跳过 PWA service worker 缓存
```

**任何这些情况都必须 `make restart`**：

- 改了 `backend/app/worker/**` 任何文件（worker 子进程要重启）
- 改了 `pyproject.toml`（依赖变了）
- 加了新 alembic 迁移
- 改了 `main.py` lifespan / router 注册
- 改了模块顶层字典（`BUILTIN_FEATURES` / `_BUILTIN` / etc.）
- bump 了版本号

**只改下面这些时**才能依赖 `--reload`（不需要 restart）：

- 单个 API endpoint 的内部逻辑（不动 router 注册）
- service 层函数体（不动 import-time 副作用）

⚠️ 不确定就 restart。

---

## 5. 归档（sprint 收尾必走）

完成验收 + bump 版本号 + 更新 CHANGELOG 后，**立即**把 plan 搬到 archive：

```bash
cd /Users/anoyou/Desktop/telebot
mkdir -p archive/plans/SPRINTN
mv agent-plans/SPRINTn-*.md archive/plans/SPRINTN/

# 在 archive/plans/SPRINTN/ 加一份 README.md，简述本 sprint 交付清单
# 模板参考 archive/plans/SPRINT2/README.md
```

归档完成的判定标准：
1. plan 末尾有"完成报告"段
2. CHANGELOG 已 bump 对应版本号段
3. 验收清单全部勾完
4. plan 已 `mv` 到 `archive/plans/SPRINTN/`

详见 `archive/README.md`。

---

## 6. 版本号与 CHANGELOG（**sprint 收尾最后一步**）

按 SemVer 决定 bump 类型：

| Bump | 触发条件 |
|------|---------|
| **MAJOR** | 不向后兼容（schema 不兼容迁移 / 协议大改 / 配置项重命名 / API 路径变更） |
| **MINOR** | 向后兼容的功能增量（一个 Sprint 通常 +1） |
| **PATCH** | bug 修复 / 文档 / 小调整 / hotfix |

同步改这 5 处（缺一处用户会看到不一致）：

1. `backend/app/__init__.py`     `__version__ = "x.y.z"` + `APP_STAGE = "..."` （或 `None`）
2. `backend/pyproject.toml`       `version = "x.y.z"`
3. `frontend/package.json`         `"version": "x.y.z"`
4. `frontend/src/lib/version.ts`  `APP_VERSION` + `APP_STAGE` （达到 1.0.0 时建议 `APP_STAGE = null`）
5. `CHANGELOG.md`                 顶部新增 `## [x.y.z] — yyyy-mm-dd · <stage>` 段，列出 Added / Changed / Fixed / Removed

> `backend/app/__init__.py` 的 `APP_STAGE` 与 `frontend/src/lib/version.ts` 的 `APP_STAGE` **必须一致**——`GET /api/system/version` 返回的 stage 用前者，前端 sidebar 显示用后者。不一致前端版本横幅虽不弹（只比对 `version`），但 ,version 命令 / 前端 about 等显示会互相打架。
> `backend/app/main.py` 的 `FastAPI(version=__version__)` 自动读 `__init__.py`，**无需单独改**。
> `backend/telebot_backend.egg-info/PKG-INFO` 由 setuptools 自动生成，下次 `pip install -e .` 会刷新，**不要手动改**。

验收方式：
- 浏览器左下 sidebar 显示新版本（如 `v0.4.0 · Sprint 4`）
- TG 中 `,version` 返回 `📦 telebot vX.Y.Z` / Python+Telethon / Platform
- `,help` 自动列出 `,version`

---

## 7. 反模式（别这样做）

- ❌ 一个会话同时改两个 plan 的范围（违反"文件白名单"会和并行 session 撞车）
- ❌ 跨会话改同一份 builtin plugin 文件
- ❌ 自己加 alembic 迁移不查 `alembic heads`（容易撞号 → 分叉，参考 0003 的教训）
- ❌ 完成报告不写、CHANGELOG 不 bump、plan 不归档（接力的下一波会话会以为是未完成的）
- ❌ 把 `*` 加到 CORS / 把明文密钥放到 GET 响应 / 跳过 Fernet 直接落库
- ❌ 给"已经稳定"的 builtin plugin 改函数签名（应该用 `**kwargs` 扩展）
