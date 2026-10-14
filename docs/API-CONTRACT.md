# TelePilot 内部 API 契约

TelePilot 当前只为自身 Web/PWA 提供内部 HTTP API，不承诺第三方公共平台兼容性，也不增加 `/api/v1`、OAuth、PAT 或 MCP 入口。接口仍统一位于 `/api/*`。

## 认证与写请求保护

- 登录态：HttpOnly Cookie `auth_token`。
- 写请求来源头：`X-Requested-With: telepilot-ui`。
- CSRF double-submit：`csrf_token` Cookie 与 `X-CSRF-Token` Header 必须相同。
- 外部 Webhook 投递：只使用 `X-TelePilot-Webhook-Token`，不使用登录 Cookie；查询参数 Token 兼容默认关闭。
- 完全公开的读取入口只有 CSRF 签发、liveness、readiness 和版本元数据；注册、登录、注销虽不要求登录 Cookie，仍受 CSRF 保护。

具体安全矩阵以生成的 [`openapi/telepilot.openapi.json`](../openapi/telepilot.openapi.json) 为准。新增未鉴权路由必须同时更新固定白名单测试，不能依赖“开发者记得加鉴权”。

## 错误响应

业务与 HTTP 错误保持现有结构：

```json
{
  "error": {
    "code": "AUTH_REQUIRED",
    "message": "未登录"
  }
}
```

请求参数校验错误继续使用 FastAPI 的 `HTTPValidationError`（422 `detail` 数组）。前端错误解析必须同时兼容这两种既有格式；本阶段不为统一外观而破坏运行时兼容性。

## 异步受理语义

`POST /api/webhooks/{account_id}/{hook_key}` 的 `delivered=true` 表示"已受理（accepted）"，即投递已进入账号 worker 队列，不代表插件动作已执行成功。处理结果以 worker 侧 `trace_id` 为准：响应同时返回 `matched` / `invoked` / `failed` 统计（旧版本 worker 不返回这些字段时为 `null`），调用方需要追踪执行细节时应记录 `trace_id`，并在 `failed > 0` 时按失败处理排查。只有 worker 离线或明确拒绝时才返回 503 `WEBHOOK_WORKER_OFFLINE`。

## 运行观测事件的投递语义：at-least-once

`runtime_log` 与 `rate_limit_event` 两类事件流是**at-least-once（至少一次）**投递，不是 exactly-once。调用方可以默认"事件最终会落库"，但**不能假设每条事件只出现一次，也不能假设前后端看到的是同一批内容**。

### 链路

生产方（账号 worker 子进程 / `account_bot_runtime` 等）把 JSON payload `RPUSH` 进 Redis list，主进程的可靠消费者循环 `LMOVE` 到 `<stream>:inflight`、批量 `db.add_all` + `commit`、成功后逐条 `LREM` ack（`backend/app/worker/supervisor.py::_consume_stream_reliable`）。DB 提交失败时**不 ack**，inflight 里的条目下一轮原样重试。

- `RuntimeLogPayload`：`account_id` / `level` / `source` / `message` / `detail` / `ts`（毫秒 epoch，生产方生成）
- `RateLimitEventPayload`：`account_id` / `action` / `outcome` / `detail` / `ts`（毫秒 epoch，生产方生成）
- 生产方写入统一走 `backend/app/worker/runtime_log.py::push_runtime_log_bounded`（`RPUSH` + `LTRIM -5000 -1` 同管道）；`rate_limit_event` 侧是 `RPUSH` + `LTRIM -2000 -1`（`backend/app/worker/ratelimit/engine.py::_emit`）。漏掉 `ltrim` 会让 `noeviction` 键无限增长，拖垮主进程。

### 为什么可能重复

ack 发生在 DB 提交**之后**，"提交已成功但 ack 前进程被杀"这个窗口内的条目会在重启后被重新读取并再次落库；inflight 里的条目同理。主进程崩溃重启后，消费者会从 inflight 继续重放。因此同一条日志/事件在极端情况下可能出现两行。

除此之外，`ts` 是生产方时间而非数据库入库时间：落库行再叠一层 `server_default now()`，所以**列表顺序≠事件发生顺序**（乱序只影响观测展示，不影响风控执行本身）。

### 调用方约定

- **不要把这两张表当计数基线**。统计、告警阈值、累计类指标（"今天触发了几次 FloodWait"）需要按 `ts` + 业务字段自行去重，或改用其他事实源。
- **列表查询结果可能波动**。`GET /api/logs/runtime`、`GET /api/accounts/{aid}/rate-limit/events` 返回的是"已落库"的快照；正在 inflight 或还在 Redis list 里的事件查不到，重放期间可能多出重复行。分页游标不要假设集合稳定不变。
- **同一事件的实时广播与落库是两条独立路径**。`EVT_RATELIMIT` 走 Redis pub/sub 给在线前端，list 落库给历史查询；广播可能成功而落库失败，也可能反过来。需要"最终可见"语义的一方应轮询列表接口，不能只依赖推送。
- **坏数据直接丢弃**：JSON 解析失败、缺必填字段的行不会进 inflight 重试（避免毒消息永久阻塞队列），这类事件永久缺失，不重放。

### 本轮不加 `event_id`

`RuntimeLog` / `RateLimitEvent`（`backend/app/db/models/log.py:46`、`backend/app/db/models/rate_limit.py:111`）暂不加 `event_id` 之类去重列，也**不加表结构改动或迁移**。理由：这两张表的用途是观测与排障，不是计费、配额或任何需要 exactly-once 的事实源；加列要付一次全表迁移成本，而收益仅覆盖"极端崩溃窗口出现重复行"这一小概率情况。

若后续某类消费真的需要去重（例如对外暴露统计 API），届时再连同迁移一起加去重键，并把本节的 at-least-once 说明改为该新键的语义。

### 生产方队列长度上限（会静默丢旧事件）

`noeviction` 策略下两条 list 都有 `LTRIM` 上限（runtime_log 5000、rate_limit_event 2000）。**主进程消费停滞时，超出上限的旧事件会被 Redis 直接丢弃且不通知任何调用方**。队列长时间积压意味着"最近若干条之后的观测数据不可信"，这时应优先排查消费者本身，而不是相信列表接口的完整性。

---

## 修改接口后的固定动作

从仓库根目录执行：

```bash
make codegen
pnpm --dir frontend typecheck
```

`make codegen` 会离线执行以下动作，不需要先启动后端：

1. 调用 `scripts/export-openapi.py` 生成 `openapi/telepilot.openapi.json`。
2. 使用 `openapi-typescript` 生成 `frontend/src/api/schema.ts`。

两个生成文件必须与 API 代码一同提交。CI 会重新生成并执行 `git diff --exit-code`；契约未更新、operationId 重复、安全矩阵漂移、公开路由增加或成功响应缺少 schema 都会阻止合并。

新增或修改的前端 API 类型优先引用 `frontend/src/api/schema.ts`；现有 `frontend/src/api/types.ts` 按模块逐步迁移，不做一次性重写。
