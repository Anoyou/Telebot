# 最终修复清单（opus + codex 整合版 v2）

> 整合 `opus-review.md` 与 `codex-review.md` 两份评审结果，含两轮追加补充。
> 按 **P0 上线前必修 → P1 下个迭代 → P2 下个季度** 三档排序。
> 来源：**O** = opus 独有 / **C** = codex 独有 / **B** = 两边共识 / **D** = codex 二轮追加 / **X** = opus 遗漏补遗。
> 工时：XS<2h / S<1d / M=1–3d / L>3d。

---

## P0 — 阻塞性，建议立刻开 PR

| # | 源 | 议题 | 关键位置 | 工时 | 验收标准 |
|---|---|---|---|---|---|
| 1 | O | **签名校验移到 `manifest.py` 执行之前**。把 `parse_zip` 的 `exec_module` 换到验签之后；未签名/签名失败的 zip 永不解压、永不 import | `services/plugin_install_service.py:install_zip` | S | 上传恶意 zip（manifest 里 `raise`）时，不触发任何 `exec_module`，且目录无落盘 |
| 2 | O | **`/login` 用 sentinel Argon2 hash 补齐"用户不存在"分支耗时**，消除用户名枚举侧信道 | `api/auth.py:login` (~line 454) | S | 用户存在/不存在的响应时间差 < 5ms |
| 3 | O | **runtime_log / ratelimit_event 改 LMOVE → 处理 → LREM**，避免 DB 故障静默丢数据；顺手把两个 consumer 抽成一个泛型函数 | `worker/supervisor.py:_consume_runtime_log / _consume_ratelimit_event` | M | 模拟 DB 挂掉 30 秒，重启后 Redis in-flight 列表中的事件全部消费成功、零丢失 |
| 4 | B | **第三方插件 sandbox 加固**：`_` 私有属性默认拒绝、`__call__` 拦截、`__class__` 不放行；UI 上把"沙箱"改名"权限白名单"，明示不防恶意插件 | `worker/plugins/sandbox.py:SandboxClient.__getattr__` | M | 插件内 `client._real` / `client.__call__` / `client.__class__` 均抛 `PermissionError` |
| 5 | B | **登录验证码 / 2FA 尝试次数上限**：`_PendingLogin` 加 `attempts` 字段，阈值锁定；同 IP 复用 `_enforce_login_rate_limit` | `services/login_service.py:confirm_code/confirm_2fa` | S | 连续 5 次错误验证码后返回 `LOGIN_ATTEMPTS_EXCEEDED`，`_cleanup` 释放 Telethon client |
| 6 | D | **插件安装链路回归测试**（**硬项**）：新增 `tests/test_plugin_install_security.py`，覆盖"先验签后执行""路径穿越""超大 zip""伪签名"四类场景 | `tests/` | S | CI 全绿，恶意 zip 场景覆盖率 100% |

## P1 — 下个迭代，安全治理与契约一致

> ⚠️ **依赖约束**：#14（关闭自动迁移）依赖 #12（迁移锁/策略明确），否则部分环境可能失去自动迁移保障。建议先完成 #12 再执行 #14。

| # | 源 | 议题 | 关键位置 | 工时 | 验收标准 |
|---|---|---|---|---|---|
| 7 | O | **JWT 加 `pwd_v` claim**：改密时 `WebUser.pwd_version += 1`；`decode_jwt_token` 比对，吊销其它 session | `services/auth_service.py` + `db/models/user.py` | S | 改密码后，旧 token 请求 `/api/auth/me` 返回 401；新 token 正常 |
| 8 | O | **CSRF 自定义 header 中间件**：所有非 GET 路由要求 `X-Requested-With: telebot-ui` | `main.py` | S | 无 header 的 POST 返回 403；浏览器正常请求通过 |
| 9 | C | **`deps.py` 401/403 改结构化 `detail`**：`{"code":"AUTH_REQUIRED","message":...}` 等，对齐其它端点契约 | `deps.py:get_current_user` | XS | 401 响应体含 `code` 字段，前端 `getErrCode()` 正确解析 |
| 10 | C | **前端 `register()` 返回 `Promise<LoginResponse>`**，注册成功直接跳首页，删 `Login.tsx` 里的二次 `loginMut.mutate()` | `lib/auth.ts` + `pages/Login.tsx` | XS | 注册后 Network 面板无多余 `/login` 请求 |
| 11 | O | **`pending_totp` cookie → 服务端 Redis 临时态**（key 按 `user.id`，TTL 5min），不再让 base32 secret 走 cookie | `api/auth.py:totp_enable/verify` | S | Cookie 中不含 base32 字符串 |
| 12 | O | **`alembic upgrade` 加 `pg_advisory_lock`**，或生产环境默认 `auto_migrate_on_startup=false` | `main.py:_run_alembic_upgrade` | S | 两个并发容器仅一个执行迁移 |
| 13 | B | **生产 `TRUST_FORWARDED_FOR=true`**；启动期检查配置错配并 WARN | `settings.py:10359` + `main.py` | XS | Docker 环境日志输出 WARN |
| 14 | C | **去掉迁移重复执行**（⚠️ 依赖 #12）：保留 docker-compose 那一次 `alembic upgrade head`，应用启动关闭自动迁移 | `docker-compose.yml:93` + `.env.example` | XS | 启动日志只出现一次 `alembic upgrade head` |
| 15 | O | **`update_account` 改 `proxy_id`/`template_id` 后通知 worker 重载**：发 `CMD_RELOAD_NETWORK` IPC，或 pause+resume | `services/account_service.py:update_account` | S | 修改代理后 worker 日志出现 "proxy updated"，无需手动重启 |
| 16 | X | **RequireAuth 增加 fallback redirect**：`<Navigate to="/login" replace />` 兜底，不完全依赖 axios interceptor | `components/layout/RequireAuth.tsx` | XS | 手动注释掉 interceptor 401 逻辑后仍能跳转登录页 |
| 17 | X | **React 树加 ErrorBoundary**：`<AppShell>` 外包一层，render 出错显示"页面出错，请刷新"而非白屏 | `App.tsx` / `main.tsx` | XS | 人为抛出 render error 显示兜底 UI |
| 18 | D | **`_PENDING` 内存上限保护**：`MAX_PENDING_LOGINS = 100`（可配），超限返回 429；防止批量 `start_login` 不走 `confirm_code` 的内存 DoS | `services/login_service.py` | XS | 循环调用 `start_login` 101 次后第 101 次返回 429 |
| 19 | D | **安全头阶段化**：nginx 先加 `X-Frame-Options: DENY` + `Referrer-Policy: same-origin`（低风险高收益）；CSP 留 P2 | `frontend/nginx.conf` | XS | `curl -I` 输出包含两个安全头 |

## P2 — 下个季度，深层加固与架构

| # | 源 | 议题 | 工时 | 验收标准 |
|---|---|---|---|---|
| 20 | O | **`MultiFernet` 支持多代密钥**，文档化轮换 SOP（含 lazy re-encrypt 工具） | M | 新旧密钥并存期间所有加密/解密正常；轮换 SOP 文档可执行 |
| 21 | O | **JWT 改 RS256 / EdDSA**，签发与校验密钥分离 | M | 公钥泄露不影响签发；旧 token 在密钥切换后自动失效 |
| 22 | O | **第三方插件搬到独立子进程**，IPC 能力代理（capability token，详见后文优化建议 #4） | L | 恶意插件 crash 不影响主进程；权限超限调用被拒绝 |
| 23 | O | **容器硬化**：non-root、`cap_drop:[ALL]`、`read_only:true` + 受控 tmpfs、镜像 digest pinning | M | `docker exec web id` 返回非 root；`docker inspect` 无特权 |
| 24 | O | **nginx 加 CSP / HSTS**（X-Frame-Options / Referrer-Policy 已在 P1 #19 完成） | XS | `curl -I` 输出含 CSP + HSTS 头 |
| 25 | O | **PWA `vite.config.ts` 审计**：所有 `/api/*` 强制 `NetworkOnly`；登出时 `registration.unregister()` | S | 离线状态下刷新不会显示旧 API 数据 |
| 26 | O | **`api.ts` 401 用 `react-router` navigate** 替代 `location.href`，并加并发 401 合并 | XS | 5 个并发 401 只触发一次跳转；React Query 缓存不丢失 |
| 27 | X | **Supervisor `_listen_global` pubsub 超时保护**：`asyncio.wait_for(..., timeout=2)` 包裹 `unsubscribe`/`close`，避免 Redis 断连时 `stop_all_workers()` 卡死 | `worker/supervisor.py:_listen_global` | S | 模拟 Redis 断连后 `stop_all_workers()` 在 3 秒内返回 |
| 28 | X | **`redis_client.get_pool()` 移到 FastAPI lifespan 初始化**，消除冷启动竞争泄漏 | `redis_client.py` + `main.py` | XS | 并发 10 个请求不创建多余 pool |
| 29 | D | **上线前演练清单**：升级脚本 / 回滚脚本 / 插件安装失败回滚 / 登录流程（含 2FA）冒烟测试 | `deploy/` | S | 演练文档可执行，含每步的预期输出和回滚条件 |

> **争议项**：codex 主张"PWA 仅生产注册"。opus 倾向于**整个删掉 PWA**——这是后台管理工具，离线场景价值低；维护 SW 缓存失效 / 版本提示 / 登出清理的成本大于收益。建议团队拍板。
>
> **P2 说明**：P1 #19 已将 `X-Frame-Options` 和 `Referrer-Policy` 提前，P2 #24 仅保留 CSP / HSTS（CSP 需要前端资源策略配合，HSTS 需要 HTTPS 就绪）。

---

# 第二阶段优化建议（修完之后）

Codex 给出 8 个方向（安全基线自动化 / 插件分级 / 可观测性 / 可靠性 / 密钥生命周期 / 性能 / 前端工程化 / 发布回滚），方向都对，但偏抽象。下面是 opus 的 8 条**补充或不同打法**，最后给速查表对照。

## 1. 写一份 SECURITY.md，把威胁模型显式化

代码 docstring 已经诚实地写了"沙箱不防恶意插件"、"多副本会 race"等等，但散落在十几个文件里，新人容易当真信任。建议和 `agent-plans/` 平级新建：

- **防御边界**：cookie 劫持、暴力破解、SQL 注入、XSS、CSRF（修完之后）、首次部署枚举
- **不防御**：本地特权升级、容器越狱、操作侧 SSH、第三方插件作恶（即使签名通过）
- **单租户假设**：只有一个 super-admin；任何能写 DB 的人都能拿到管理员权限
- **密钥假设**：`MASTER_KEY` 泄漏 = 所有 TG session 失效；`JWT_SECRET` 泄漏 = 全员可仿冒

每次 PR 触动 `crypto.py` / `auth_service.py` / `plugin_install_service.py` / `sandbox.py` 强制 review 这份文档。

## 2. 把 MASTER_KEY 搬出环境变量，接 KMS / Secrets Manager

Codex 提了"密钥轮换"，但**密钥存储本身**先得解决。当前 `MASTER_KEY` 走 `.env` → `os.environ` → `Settings`，能 `docker inspect` 或 `cat /proc/<pid>/environ` 的人就拿到了。

- **首选**：AWS KMS / GCP KMS / HashiCorp Vault Transit。进程持短期 IAM token，加解密走 RPC
- **退一步**：docker / k8s secret 只读挂载 `/run/secrets/master_key`，启动时一次性读到内存

落地后**轮换就只是 KMS 后台版本切换**，应用代码完全不动。也是 P2 第 14 条 `MultiFernet` 的天然底座。

## 3. API ↔ worker ↔ 插件契约写成 contracts.md + 合同测试

Worker docstring 说"只读 DB"，但 `runtime.py` 实际偷写 `tg_user_id`。multi-process 系统的慢性病。具体做法：

- `CONTRACTS.md` 列出每条 IPC 消息（`CMD_PAUSE` / `CMD_RELOAD_CONFIG` / `EVT_LOGIN_REQUIRED`...）的语义、发起方、接收方、payload schema
- 每条消息一个 dataclass + 一个合同测试：随便构造消息，跨进程发→收，schema 不变
- IPC schema 改动必须先改 `CONTRACTS.md` → review → 改实现，当作轻量 ADR

## 4. Capability token 替换权限字符串

Codex 提了"内置 / 受信 / 第三方"三档。粒度还是粗。当前 `manifest.permissions = ["send_message", ...]` 是字符串列表，**插件代码自己能读自己的 permission**——设计上无意义但是 attack surface。

更干净：安装时主进程颁发短期 **HMAC capability token**（绑定 `plugin_key + account_id + cap_set + exp`），IPC 发到 worker；worker 在 sandbox 层调 `client.send_message` 时校验 token，**插件永远拿不到 token 本身**。从"信任 manifest"变成"信任主进程颁发的凭证"。配合 P2 第 16 条独立进程化做。

## 5. LLM provider 加预算 / 用量告警

Codex 提了 `cost_tier`（schema 里已有），但**没提实际成本控制**。失控的 auto_reply + `,ai` 循环 24h 内能烧掉账单。建议：

- `LLMProvider` 加 `monthly_budget_usd` / `alert_at_pct`
- 每次调用累加到 Redis `llm:usage:{provider_id}:{YYYY-MM}`
- 阈值告警走现有 `NotificationChannel`（推 TG / Webhook）
- 超预算自动 `enabled=false`，附 audit log

成本是被忽视的运维维度，至少做到"烧超了能立刻被知道"。

## 6. 日志 polling 改 SSE / WebSocket

`Logs.tsx` 现在 5s 轮询；多 tab 多账号下负载放大很快，前端体验也滞后。

后端 `/api/logs/stream` 用 FastAPI 原生 `StreamingResponse(media_type="text/event-stream")`；那两个 Redis list consumer 在 commit 后顺手 publish 到 `logs:{account_id}` 频道，SSE handler 转发给前端。带宽降，延迟从 ~2.5s 降到 <100ms，前端代码反而更简单（一个 `EventSource` 替代 `useQuery({ refetchInterval })` 状态机）。

## 7. 灾备演练，不是只备份

Codex 写了"备份脚本 + 一键回滚"。但**没验证过的备份等于没有备份**：

- 每周自动 cron：从最新备份 restore 到 scratch DB → `alembic upgrade head` → 拉账号列表、解密一个 session 验证 → 丢弃
- 每季度 human-in-the-loop 演练一次主库挂掉的完整恢复，记 RTO / RPO
- 备份文件本身加密；**`MASTER_KEY` 备份必须和 DB 备份分开存放**，否则备份就是脱敏后的明文

## 8. 直接上 OpenTelemetry，别只上 Prometheus

Codex 列了"加 trace_id + Prometheus 指标"。建议一步到位 OTel：

- Prometheus 只能 metrics；trace 还得另接 Jaeger / Tempo
- OTel 是开放标准，metrics + traces + logs 同一套语义。后端 `opentelemetry-instrumentation-fastapi` + `-sqlalchemy` 自动埋点，Telethon 那边手工打 span
- 收集端 OTel Collector → Prometheus + Tempo + Loki；想换 vendor（DataDog / Honeycomb / SigNoz）只换 Collector exporter

避免"先 Prometheus、半年后又上 Jaeger、再半年后想加 logs"的重复改造。

---

## 速查：与 codex 八条建议的对照

| codex 方向 | 同意？ | opus 的补充 / 差异 |
|---|---|---|
| 安全基线自动化（pre-commit / CI SAST / 依赖扫描） | ✅ | 加 Trivy 扫镜像；加自定义 Semgrep 规则查"X 在 Y 之前"反模式（如本次 manifest-exec-before-sig） |
| 插件分级治理（三档） | ✅ 但粒度更细 | **#4：capability token**，不是字符串 permission |
| 可观测性（trace_id + Prometheus） | ✅ | **#8：直接 OTel**，避免分阶段重写 |
| 可靠性工程（at-least-once + 故障注入） | ✅ | + **outbox 模式**让 DB / Redis 不再各执一词；登录态用 saga 替代内存 dict |
| 配置与密钥生命周期 | ✅ | **#2：先搬出 env var**，再谈轮换；**#7：备份验证**而不只是备份 |
| 性能与扩展性 | ✅ | **#5：LLM 用量预算**；**#6：日志 SSE 替 polling** |
| 前端工程化 | ✅ | OpenAPI codegen 之前 review 也提过；建议加 `react-hook-form`（现在的 raw `useState` 表单不可持续） |
| 发布与回滚机制 | ✅ | 见 **#7** |
| — | — | **#1：先写 SECURITY.md**，让威胁模型对外可见（codex 没提） |
| — | — | **#3：API / worker 契约文档 + 合同测试**（codex 没提） |

---

## 推荐的执行节奏

1. **本周内（P0，6 条）**：~8–10 人天。建议两人并行——一人攻 #1 / #2 / #5（auth 与 plugin install 的硬漏洞），另一人攻 #3 / #4 / #6（worker 数据通路、sandbox、回归测试）。
2. **下个迭代（P1，13 条）**：~8–10 人天，单人可拆两个迭代完成。第一周重点 #7 / #8 / #9 / #10 / #11（auth 子系统安全治理）；第二周 #15 / #16 / #17 / #18 / #19（前端健壮性 + 部署配置）。
3. **下个季度（P2，10 条）**：挑 **#22 插件独立进程**、**#23 容器硬化**、**#29 上线前演练清单** 作为主线；#27 / #28 可随手修（XS 工时）。
4. **第二阶段优化主题**：从 8 条里选 3 条作为本季度技术债主题。**强烈推荐 SECURITY.md + KMS + OTel**——这三条是后续所有改动的底座，越早做收益越长。
