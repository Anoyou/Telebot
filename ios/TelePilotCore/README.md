# TelePilotCore

原生 App 的网络、会话、NDJSON 与表单契约层。业务由 TelePilot 服务器提供，APNs 是默认关闭的服务端可选扩展。

## 验证与生成

在仓库根目录运行：

```sh
swift test --package-path ios/TelePilotCore
sh ios/TelePilotCore/scripts/generate.sh --check
```

修改 App 消费的 API 后运行 `sh ios/TelePilotCore/scripts/generate.sh`，提交快照与 `Sources/TelePilotCore/Generated/`。生成器固定为 Apple Swift OpenAPI Generator 1.14.2，工具依赖独立放在 `Codegen/`；普通 App 构建只编译已生成 Swift，不执行构建插件或要求用户信任插件。

`sync-openapi.py` 从仓库 OpenAPI 3.1 快照提取所需操作、引用及原生只读字段。选中字段保留原 schema 类型和 requiredness。因生成器会跳过 FastAPI 的 `anyOf: [T, null]`，脚本将其等价规范化为 3.1 `type: [T, null]`，必要时内联 nullable 引用。生成器不支持 Cookie 参数的警告是已知限制，Cookie 与 CSRF 由 `SessionTransport` 实现和测试。

`sync-contract.py` 独立生成原生表单使用的 `contract.json`，包括全部公开 API 的参数与请求 schema，不含服务器实际配置。通用 JSON、文件和 NDJSON 请求复用同一个会话 transport。JSONValue 保留 64 位整数，不把 Telegram ID 转成有精度损失的浮点数。流式响应使用真实字节解析，遇到 done 终止 GET 订阅；EOF 缺少终态则要求重新核对。

没有“服务器版本必须与 App 一致”的检查；版本元数据变化不导致快照 `--check` 失败。新增未知字段会被忽略，账号和 worker 状态保留原字符串。删除必需字段或改变消费字段类型会报告不兼容；新功能需要 App 主动接入。运营计数缺失不会当作零。账号时间戳同时支持 RFC 3339 整秒及小数秒。

## 安全边界

- 仅允许 HTTPS 根地址；拒绝 URL 内账号、密码、路径、查询参数和片段。TLS 使用系统默认验证，拒绝所有重定向。
- 使用无缓存的独立 URLSession；禁用共享 Cookie、URLCredentialStorage。只接收目标域、根路径的 `auth_token`、`csrf_token`。
- 密码、TOTP、OTP、恢复码只随本次请求发送，不持久化、不输出日志。只把带服务器有效期的会话 Token 放入按 origin 隔离的设备钥匙串，不通过 iCloud 同步。
- 每个写请求带 `X-Requested-With: telepilot-ui` 和双提交 CSRF。只有服务器明确返回 `CSRF_TOKEN_REQUIRED` 或 `CSRF_HEADER_REQUIRED` 时刷新并重试一次；不因超时或一般错误自动重试操作。
- 401、退出和主动清理会清除内存与钥匙串；清理错误明确抛出，调用方必须阻止切换服务器并允许重试。清理后晚到的旧请求不能恢复会话。
- 暂停／恢复的网络中断、5xx、格式错误的成功响应标记为“结果未确认”，要求刷新状态，不能把重试当作安全默认行为。

测试通过 URLProtocol 模拟真实请求、Cookie、CSRF、验证码、过期、退出、并发与异常返回，不连接任何实际服务器。钥匙串生产实现由系统 Security API 提供；自签真机仍需验证账号权限、证书及登录过期恢复。
