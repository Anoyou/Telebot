# Agent 分工总索引

> 项目 PRD：[`teleuserbot.md`](./teleuserbot.md)  
> 共享契约（**只读**）：[`CONTRACTS.md`](./CONTRACTS.md)  
> 总实施计划：`/Users/anoyou/.claude/plans/cheerful-bouncing-hippo.md`（Telethon 切换后版本）

## 依赖关系

```
A (认证+账号 API)  ┐
B (worker 运行时)  ├─ 4 个并行 Wave-1
C (风控引擎)       │
E (前端)           ┘
                    ↓
D (插件+features API)  ← Wave-2（C 完成后）
                    ↓
F (Docker / 部署)      ← Wave-3（A/B/C/D 后端全完成后）
```

## 各 Agent plan 路径

| Agent | 模块 | Plan 文件 |
|---|---|---|
| **A** | 认证 + 账号 API + 登录状态机 + main.py + Alembic 初始迁移 | `backend/app/services/AGENT_PLAN.md` |
| **B** | Worker 运行时 + supervisor + Telethon client | `backend/app/worker/AGENT_PLAN.md` |
| **C** | 风控引擎（令牌桶 + 三层继承 + 5 策略 + FloodWait/PeerFlood/SlowMode 自动响应 + 拟人化 + 冷启动） | `backend/app/worker/ratelimit/AGENT_PLAN.md` |
| **D** | 插件框架 + auto_reply + features/rules/plugins API | `backend/app/worker/plugins/AGENT_PLAN.md` |
| **E** | React 前端 | `frontend/AGENT_PLAN.md` |
| **F** | Docker / docker-compose / nginx / README 部署文档 | `deploy/AGENT_PLAN.md` |

## 在新 Claude Code 会话里启动某个 Agent

1. `cd /Users/anoyou/Desktop/telebot && claude`（或在 IDE 里开新会话）
2. 粘贴下表对应行：

```
请仔细读 <plan 路径> 然后严格按 plan 执行；用中文写注释，完工后给我汇报。
```

每份 plan 都是**自包含**的——会指明要读哪些只读文件、要写哪些文件（白名单）、Telethon 关键 API、自检命令、汇报格式。

## 共同约束（每个 Agent 都要遵守）

- 注释用**中文**
- 错误返回统一形态：`{"error": {"code": "...", "message": "..."}}`，FastAPI 端用 `HTTPException(detail={"code":..., "message":...})` + main.py 全局 handler 转换（A 已实现）
- 加密敏感字段：`encrypt_str/decrypt_str/encrypt_bytes/decrypt_bytes` 来自 `backend/app/crypto.py`
- DB 用 `AsyncSessionLocal()` from `backend/app/db/base.py`，FastAPI 注入用 `DBSession` from `backend/app/deps.py`
- 当前用户注入：`CurrentUser` from `backend/app/deps.py`
- IPC：常量与 `make_cmd/make_event` 从 `backend/app/worker/ipc.py` import
- TG 客户端：**Telethon 1.36+**（不要写 Pyrogram）
- 不要修改契约文件：`pyproject.toml`、`db/models/*`、`schemas/*`、`crypto.py`、`settings.py`、`deps.py`、`worker/ipc.py`、`alembic/env.py`

## 整合阶段（用户人工做或主会话做）

1. 装 Python 3.12（`brew install python@3.12`）
2. `cd backend && python3.12 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev]"`
3. `make dev-up`（拉起 PostgreSQL + Redis）
4. `cd backend && alembic upgrade head`
5. `make backend` + `make frontend`
6. 浏览器走完登录 → 绑定 TG → 配置 auto_reply → 端到端验证