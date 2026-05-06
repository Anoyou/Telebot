# Code Review Result (Codex)

## Round 1 — Backend

### Critical

1. **Severity: Critical**  
   **File:Line:** `backend/app/worker/plugins/sandbox.py:109-113`  
   **Description:** 第三方插件沙箱可被直接绕过。当前逻辑对 `_` 前缀属性默认放行，插件可通过 `client._sender`、`client._call`、`client.__class__` 等路径访问底层能力，等价绕过 `manifest.permissions`。文件注释也明确写了 raw API 未拦截，这意味着“恶意插件”可执行超权限调用。  
   **Fix:** 改为默认拒绝模型：仅允许白名单方法 + 少量只读属性；禁止所有 `_` 私有属性和 `__call__` raw path；将第三方插件放入独立进程并做 IPC 能力代理（真正隔离）。

### Major

2. **Severity: Major**  
   **File:Line:** `backend/app/services/login_service.py:152-192`  
   **Description:** 登录状态机缺少验证码/2FA 尝试次数上限。`confirm_code` 与 `confirm_2fa` 可在 token TTL 内重复试错，存在暴力猜码窗口。  
   **Fix:** 在 `_PendingLogin` 增加尝试计数与锁定时间；达到阈值后立即 `_cleanup(token)` 并返回统一错误（如 `LOGIN_ATTEMPTS_EXCEEDED`）。

3. **Severity: Major**  
   **File:Line:** `backend/app/deps.py:29-35`  
   **Description:** 未登录/过期/用户不存在错误直接返回字符串 `detail`，与项目统一错误契约 `{"error":{"code","message"}}` 不一致，前端错误处理会退化为通用文案，且行为不统一。  
   **Fix:** 统一抛 `HTTPException(detail={"code":...,"message":...})`，并复用全局异常包装。

4. **Severity: Major**  
   **File:Line:** `docker-compose.yml:74` + `backend/app/settings.py:66` + `backend/app/main.py:67-68`  
   **Description:** 迁移执行重复：容器启动命令先 `alembic upgrade head`，应用启动又默认 `auto_migrate_on_startup=true` 再跑一次。单实例虽常“看起来没问题”，但会增加启动时长；多副本滚动时更容易产生迁移竞争和锁等待。  
   **Fix:** 二选一保留迁移入口。推荐生产只保留外层一次迁移，设置 `AUTO_MIGRATE_ON_STARTUP=false`。

## Round 2 — Frontend + Deploy

### Major

1. **Severity: Major**  
   **File:Line:** `frontend/src/lib/auth.ts:19-21` + `frontend/src/pages/Login.tsx:73-79`  
   **Description:** 注册接口实际会返回 `LoginResponse` 且后端已写入登录 cookie，但前端把 `register()` 声明为 `Promise<void>`，注册成功后又主动触发一次 `loginMut.mutate()`。这会导致多余一次鉴权请求，且在启用登录限速/TOTP 场景下可能带来不必要失败。  
   **Fix:** 让 `register()` 返回 `LoginResponse` 并在 `onSuccess` 直接跳转首页；移除“注册后再登录一次”的重复调用。

2. **Severity: Major**  
   **File:Line:** `frontend/nginx.conf:20` + `backend/app/settings.py:25`  
   **Description:** nginx 已转发 `X-Forwarded-For`，但后端默认 `trust_forwarded_for=false`。上线后如果希望按真实客户端 IP 做登录限速，会退化为按反向代理 IP 计数，容易误伤所有用户。  
   **Fix:** 生产环境显式设置 `TRUST_FORWARDED_FOR=true`，并确保服务仅暴露在可信反代后面。

### Minor

3. **Severity: Minor**  
   **File:Line:** `frontend/src/main.tsx:34-35` + `frontend/src/pwa.ts:29`  
   **Description:** 注释写明“开发环境也启用 SW”，`immediate: true` 会在开发调试中更容易出现缓存干扰（页面逻辑与最新代码不一致）。这不是安全漏洞，但会增加排障成本。  
   **Fix:** 仅在生产环境注册 SW（`if (import.meta.env.PROD) registerPWA()`），开发环境关闭。

---

## Residual Risks / Gaps

- 本轮未执行端到端安全测试（如真实恶意插件 PoC、CSRF 流程、并发迁移压测），以上结论基于静态代码审查与配置审查。  
- 插件体系属于高风险边界，建议后续补充“恶意插件测试集”和最小权限回归测试。
