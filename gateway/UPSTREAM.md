# Upstream baseline

- 审查基线：`github.com/router-for-me/CLIProxyAPI/v7`
- 固定提交：`ffdb9c9fbc78a6235d59c9ccbdc4243ba35ecdcd`
- 核对日期：2026-08-03
- 使用范围：只吸收 Codex Responses 的请求身份、SSE、工具调用与错误行为；TelePilot 不复制管理面、账号池、OAuth 或桌面能力。

Gateway 当前使用 Go 标准库重新实现经审查的必要执行契约，不直接 vendoring CLIProxyAPI 管理面或账号运行时。固定基线中的 `codex_executor_request.go` 是身份适配事实源：Gateway 生成稳定 `prompt_cache_key` / `Session_id`，同步 `session/thread/turn/window/installation` client metadata，并固定 Codex UA、`Originator`、`Version` 与对应兼容头。所有字段都有本地出站契约测试，Provider 自定义头不能覆盖它们。

这些公开传输字段不是 OAuth 或设备证明。Gateway 不读取 `~/.codex/auth.json`，不生成 ChatGPT Account ID、Cookie、attestation 或 Agent Identity；若上游实际绑定官方账号凭据，仍必须返回 `official_account_required`。

## 运行时基础镜像（WP-B14）

`gateway/Dockerfile` 的运行时阶段使用 `gcr.io/distroless/static-debian12:nonroot`，**不用 `FROM scratch`**。

原因：Gateway 通过 Go `net/http` 访问 HTTPS 上游，需要系统 CA 根证书完成校验。`scratch` 不带 `/etc/ssl/certs`，同一个静态二进制在其中的 HTTPS 请求会以

```
Get "https://api.openai.com/v1/models": tls: failed to verify certificate: x509: certificate signed by unknown authority
```

失败；换成该 distroless 镜像后同一请求返回上游真实响应（未带凭据时为 401）。生产路径（`backend/Dockerfile` 内嵌由 Python 基础镜像提供 CA）不受影响。

该基础镜像只比 `scratch` 多出 CA 根证书与 `/etc/passwd` 等最小内容，仍无 shell、无包管理器，"只复制二进制"的最小运行时约束保持不变。构建或改回 `scratch` 前请先确认 Gateway 已改用自管证书池（例如内嵌 CA 包）——否则任意真实上游调用都会失败。

### 复现步骤

本机 Docker + `GOOS=linux GOARCH=amd64 CGO_ENABLED=0` 下的最小探针：

```bash
# 1. 任一 Go HTTPS 客户端静态编译为 linux/amd64
# 2. FROM scratch + 只 COPY 二进制 → 请求 HTTPS 报 x509 证书未知
# 3. FROM gcr.io/distroless/static-debian12:nonroot + 同样 COPY → 正常拿到上游响应
# 4. docker build -f gateway/Dockerfile . 后直接 ENTRYPOINT 运行：
#    /healthz → 200 {"ok":true}
#    /version → 200（含 gateway_protocol_version）
#    POST /v1/responses（无 Provider 快照）→ 503 gateway_unavailable
```

第 2、3 步的差异就是本次改动的验收证据。

## 上游连接池（WP-B15）

Gateway 的上游 `http.Client` 按 **(provider, proxy, baseURL, snapshot revision)** 缓存复用，不再逐请求新建连接（`gateway/internal/codex/pool.go::upstreamPool`）。

先前实现每个上游请求都设 `Request.Close = true` 并 `defer client.CloseIdleConnections()`，等于每次调用都新建 TCP + TLS 连接。System Agent 的长任务会连着打多轮工具调用，握手开销被全额付在每一次请求上。

### key 只取影响连接的字段

`poolKey` 只组合 `ProviderID`、`Revision()`、`BaseURL`、`ProxyURL`。模型名、`TimeoutSeconds`、`MaxConcurrency`、兼容请求头都不改变复用资格，放进去只会打出多个池、把复用抵消掉。总超时仍由请求 context 控制（请求级 `X-TelePilot-Timeout-Seconds` 优先），`http.Client.Timeout` 保持 0，不在 Client 层再压一层。

### revision 是退役信号

Provider 改了代理、Base URL 或凭据后，新 snapshot 的 revision 单调递增；`acquire` 发现 revision 变化即 `retireLocked()`——关闭旧池全部空闲连接并清空 map，再为新 revision 建池。回退到旧 revision 也重新建池，不复活已退役的 Client。进程退出时 `Handler.Close()` 收尾（`gateway/cmd/telepilot-gateway/main.go` 里 `defer codexHandler.Close()`）。

### 复用边界与安全语义不变

- 未显式选择 Provider 代理时 `Transport.Proxy` 仍为 `nil`，不继承进程 `HTTP_PROXY` / `HTTPS_PROXY`。
- 上游重定向仍不自动跟随（`http.ErrUseLastResponse`），避免自定义认证头跨 origin 泄露。
- 单 Provider 并发上限（`acquire` semaphore）与全局并发上限不变。

### 相关测试

`gateway/internal/codex/pool_test.go`：同路由复用同一 Client、provider/proxy/baseURL 分池、revision 变化整体退役、`close()` 清空、并发 acquire 稳定、`IdleConnTimeout > 0` 与 `MaxIdleConnsPerHost >= 2` 保底复用、坏代理字符串退化为直连，以及一条端到端用例——连续 3 次 `/v1/responses` 上游侧只建立 1 条连接。
