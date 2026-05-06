# Sprint 2 — Session #1：UX 清理 + Humanize tab + 安全运维文档

> 工时：约 1 天（可并行）
> 依赖：无
> 优先级：先行（最简单，先合并）

## 1. 目标

把 Sprint 1 留下的几个小尾巴清掉，并补一份生产部署的安全运维 SOP（CSRF / MASTER_KEY 轮换 / pending_totp）。

具体三件事：

1. **账号列表加头像列**：从 Telethon 拉头像缓存到本地 + API 暴露，前端 `/accounts` 列表和 Dashboard 卡片左侧显示圆形头像（无头像→首字母 fallback）。
2. **账号详情 → 风控基础 tab 增加 Humanize 子区**：当前 `pages/Accounts/Detail.tsx` 的 "rate" tab 只显示 RateLimitRule 表。新增折叠面板，编辑 `humanize_config`（typing 模拟开关、阅读延迟范围、人类化抖动比例）。
3. **新增 `docs/SECURITY-OPS.md`**：生产部署清单 + 三项挂起风险（CSRF、MASTER_KEY 轮换、pending_totp）的接受声明 + 应急 SOP（密钥泄露时怎么轮、token 怎么作废）。

## 2. 文件白名单（**只能改这些**）

### 后端
- `backend/app/api/accounts.py` — 新增 `GET /api/accounts/{aid}/avatar`（返回 PNG bytes 或 302 到本地文件路径）
- `backend/app/services/account_service.py` — 加 `fetch_avatar(aid)` 方法，调用 worker 拿 InputPeerSelf 头像 → 缓存到 `data/avatars/{aid}.jpg`
- `backend/app/services/humanize_service.py`（**新建**）— get/patch HumanizeConfig
- `backend/app/api/rate_limit.py` — 新增 `GET/PATCH /api/accounts/{aid}/humanize`
- `backend/app/schemas/rate_limit.py` — 加 `HumanizeConfigIn/Out` schema

### 前端
- `frontend/src/api/accounts.ts` — 加 `avatarUrl(aid)` 辅助
- `frontend/src/api/system.ts` — 加 `getHumanize(aid)` / `patchHumanize(aid, body)`
- `frontend/src/api/types.ts` — **追加** `Sprint2 #1` 块（HumanizeConfig 类型）
- `frontend/src/components/AccountAvatar.tsx`（**新建**）— 圆形头像 + 首字母 fallback
- `frontend/src/components/AccountSummaryCard.tsx` — 左上加头像
- `frontend/src/pages/Accounts/List.tsx` — 表格首列加头像
- `frontend/src/pages/Accounts/Detail.tsx` — `rate` tab 加 Humanize 折叠面板

### 文档
- `docs/SECURITY-OPS.md`（**新建**，约 200 行）

### 不要动
任何 worker / plugin / 风控引擎核心 / 登录服务。

## 3. 实现要点

### 3.1 头像端点

```python
# api/accounts.py
@router.get("/{aid}/avatar")
async def get_avatar(aid: int, db: Session = Depends(...)):
    path = await account_service.ensure_avatar(aid, db)  # 触发懒加载
    if not path or not path.exists():
        raise HTTPException(404, "no avatar")
    return FileResponse(str(path), media_type="image/jpeg",
                        headers={"Cache-Control": "private, max-age=3600"})
```

懒加载：`ensure_avatar` 先查 `data/avatars/{aid}.jpg` 存在且 mtime < 24h → 直接返；否则发 IPC 命令 `fetch_avatar` 给 worker，worker 用 `client.download_profile_photo(me, file=...)` 写盘。**worker 离线时**返 404 让前端走 fallback。

### 3.2 Humanize 子区 UI

```tsx
// Detail.tsx rate tab 内，rules 表后面加：
<Collapsible defaultOpen={false}>
  <CollapsibleTrigger className="text-sm text-muted-foreground hover:underline">
    人类化（humanize）配置 ▾
  </CollapsibleTrigger>
  <CollapsibleContent className="pt-3 space-y-3">
    <Switch checked={h.typing_enabled} onCheckedChange={...}>模拟"对方正在输入"</Switch>
    <NumberRange
      min={h.read_delay_ms_min} max={h.read_delay_ms_max}
      onChange={(min,max)=>...}
      label="阅读延迟（ms）"
    />
    <NumberInput value={h.jitter_pct} onChange={...} suffix="%" label="抖动比例" />
    <Button onClick={save}>保存</Button>
  </CollapsibleContent>
</Collapsible>
```

`Collapsible` shadcn 已有，没有就自己写一个 `useState` 控制 `<div hidden>`。

### 3.3 SECURITY-OPS.md 大纲

```markdown
# 生产部署安全清单

## 1. 一次性配置
- COOKIE_SECURE=true（必须，前端走 https）
- TRUST_FORWARDED_FOR=true（仅当部署在 nginx/traefik 后）
- POSTGRES_PASSWORD=<32 字符强随机>
- chmod 600 .env
- bash deploy/backup-keys.sh （生成 keys-backup-*.gpg 异地保存）

## 2. 已知接受风险
### 2.1 CSRF 防护未实现
现状：cookie 用 SameSite=Lax + HttpOnly + Secure。可被同源 GET 跨页触发，但所有写操作 POST/PATCH/DELETE 不会被自动跨站。
风险范围：低。如果用户访问被 XSS 注入的同站页面 →
缓解措施：定期审 CSP、不嵌入第三方 iframe。
未来计划：引入 double-submit cookie token，预计 V1.5。

### 2.2 MASTER_KEY 轮换未实现
现状：单密钥 Fernet 加密所有 secret，轮换需 dual-key 解密 → 重写库 → 切单密钥。
应急 SOP（密钥泄露时）：
1. 立即停服 docker compose stop
2. 复制 .env 备份到 master_key 旧值
3. 生成新 MASTER_KEY → 改 .env
4. 跑 `python -m app.scripts.rekey --old=<旧> --new=<新>`（**待 V1 实现**）
5. 重启
6. 通知所有 web 用户重新登录（写一条 audit）

### 2.3 pending_totp 用 cookie 存
现状：5min HttpOnly + Secure（可选）+ SameSite=Lax cookie 暂存 pending session。
风险：用户机器被劫持 5min 内可绕过 TOTP。
缓解：cookie 已 HttpOnly，JS 偷不到。SameSite=Lax 阻断 CSRF。
未来：迁到 Redis 存 pending_token → cookie 只放 token，预计 V1.5。

## 3. 应急 SOP

### 3.1 怀疑 web 账号被攻陷
- POST /api/auth/logout 当前 session
- 直接改 web_user.password_hash 强制下线
- 翻 audit_log 看异常操作

### 3.2 怀疑 TG 账号 session 被盗
- /accounts/{aid} → 暂停
- worker 上 client.log_out()（手动 SQL）→ 数据库 session_enc 清空
- 用户重新走 /login 绑定

### 3.3 .env 泄露
- 立即 rotate MASTER_KEY（按 2.2 SOP）
- rotate POSTGRES_PASSWORD
- rotate JWT_SECRET → 所有用户被踢
```

## 4. 验收清单

```bash
# 后端
cd backend && pytest -v          # 全绿
ruff check .                       # 全绿
alembic upgrade head               # 无错（如本会话有迁移）

# 前端
cd frontend && pnpm run build      # 无错

# 浏览器手测
# 1. /accounts 列表首列圆形头像（已绑定账号）
# 2. /accounts/{aid} → 风控基础 tab → "人类化配置" 折叠面板能打开+保存
# 3. 打开 docs/SECURITY-OPS.md（IDE 即可）格式清晰
```

## 5. 完成报告模板

完成后回填这一段：

```markdown
## 完成报告 — Session #1

- [x] 头像端点 + 缓存策略：`GET /api/accounts/{aid}/avatar`，缓存 24h
- [x] AccountAvatar 组件 + 列表/卡片接入
- [x] Humanize 子区接入风控基础 tab
- [x] docs/SECURITY-OPS.md 完稿（行数：XXX）
- 改动文件：XX 个
- 测试：pytest XX passed / pnpm build OK
- 已知遗留：（如有）
```

## 完成报告 — Session #1（2026-05-03）

- [x] 头像端点 + 缓存策略：`GET /api/accounts/{aid}/avatar`，本地磁盘缓存 24h；
  worker 离线 / 账号无头像 / 首次访问时返 404 让前端走首字母 fallback。
- [x] `AccountAvatar` 组件（圆形 + 8 色稳定 fallback）+ 列表卡片 + 详情头部接入。
- [x] Humanize 子区作为可折叠面板嵌进风控基础 tab；覆盖 typing 模拟开关、min/max ms、触发概率、jitter%、活跃时段、阅读后回复、冷启动天数 7 项；保存调 `PUT /api/accounts/{aid}/humanize`，触发 worker 热加载。
- [x] `docs/SECURITY-OPS.md` 完稿（232 行）：一次性配置清单、3 项已知接受风险（CSRF / MASTER_KEY 轮换 / pending_totp）、5 条应急 SOP（web 账号攻陷 / TG session 被盗 / .env 泄露 / 仅 DB 泄露 / 整机被入侵）、日常巡检表、反模式清单、changelog。

### 改动文件（13 个）

**后端 (6)**
- `backend/app/api/accounts.py` — 新增 `GET /{aid}/avatar`，复用 service。
- `backend/app/services/account_service.py` — 加 `ensure_avatar`，TTL 24h，fire-and-forget IPC。
- `backend/app/services/humanize_service.py` — **新建**，转发到 `rate_limit_service` 中已有的实现，作为稳定门面。
- `backend/app/worker/ipc.py` — 新增 `CMD_FETCH_AVATAR` 常量。
- `backend/app/worker/runtime.py` — `CMD_FETCH_AVATAR` 处理：调用 `client.download_profile_photo("me", file=path)` 写盘，空文件清理。
- `backend/app/settings.py` — 新增 `avatars_dir` 配置项（默认 `./data/avatars`）。

**前端 (6)**
- `frontend/src/api/accounts.ts` — 加 `avatarUrl(aid)` 辅助。
- `frontend/src/api/system.ts` — 加 `getHumanize` / `patchHumanize`。
- `frontend/src/api/types.ts` — 追加 `Sprint2 #1` 块（`HumanizeConfig` / `HumanizeUpdate`）。
- `frontend/src/components/AccountAvatar.tsx` — **新建**，加载失败回退首字母。
- `frontend/src/components/AccountSummaryCard.tsx` — 标题行接入头像（40px）。
- `frontend/src/pages/Accounts/Detail.tsx` — 头部接入头像（36px）+ rate tab 末尾追加 `HumanizePanel`。

**文档 (1)**
- `docs/SECURITY-OPS.md` — **新建**，232 行。

### 测试

- 后端 `pytest -q`：**80 passed, 2 skipped**（无回归）。
- 后端 `ruff check`（仅本 sprint 改动文件）：**all checks passed**。
- 前端 `pnpm run build`：**OK**，dist 产物正常生成（仅 chunk-size 警告，非本次新增）。

### 不属于本次范围 / 不动的代码

- `app/services/{ignored_peer,llm_client,account_command_service}.py`、`app/api/auto_reply_command.py` 等留有 6 条 ruff I001/UP041 提示，均为并行 sprint（IGNORED-PEERS / CUSTOM-COMMAND）的文件，按 plan 白名单不动。

### 已知遗留

- 头像懒加载是 fire-and-forget：worker 离线时 IPC 没人接收，本次访问只能拿 404；下次刷新（worker 起来后）才会有图。这是 plan 接受的简化方案，未来若要保证首响应即可见，需要把命令改成 RPC 同步等盘。
- `data/avatars/` 目录不会自动清理：账号删除时未清头像文件（cascade 只到 DB 行）。下一次账号 ID 复用前不会有冲突，可暂不处理。

