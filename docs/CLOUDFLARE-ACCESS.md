# 在 Cloudflare Access 后面使用 TelePilot iOS

很多人会在 TelePilot 管理台前面套一层 [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/access-controls/)（例如只允许自己的 Google 账号访问）。浏览器能正常用，是因为它会跟随 Access 的登录跳转、完成 Google 登录，并带上 `CF_Authorization` Cookie；iOS App 不会跟随重定向（安全规则：防止登录信息被转发到别的地址），所以会被 Access 挡住。

iOS 0.11.0 起，App 通过 **Service Token** 穿过 Access：每个请求都带上 `CF-Access-Client-Id` 与 `CF-Access-Client-Secret` 两个请求头。Web 管理台不受影响，照常用 Google 登录。

## 一、在 Cloudflare 后台创建 Service Token

1. 打开 Cloudflare 控制台 → **Zero Trust** → **Access** → **Service Auth** → **Service Tokens**（新版控制台路径可能显示为 Access controls → Service credentials → Service Tokens）。
2. 点击 **Create Service Token**，填写名称（如 `TelePilot iOS`），选择有效期（Duration）。
3. 创建完成后页面会显示 **Client ID**（`CF-Access-Client-Id`，形如 `xxxx.access`）和 **Client Secret**（`CF-Access-Client-Secret`）。**Client Secret 只显示这一次**，请立即复制保存。

## 二、在保护 TelePilot 的 Access 应用里放行这个 Token

1. **Zero Trust** → **Access** → **Applications**，找到保护 TelePilot 域名的那个应用，点 **Edit**（编辑）。
2. 在 **Policies** 中新增一条策略：
   - **Action**：`Service Auth`
   - **Include**：选择 `Service Token`，值选中刚创建的 Token
3. 保存。原来的 Allow 策略（Google 账号）**保持不变**，Web 管理台继续用 Google 登录；新的 Service Auth 策略只放行带这个 Token 的请求。

> 注意：Action 必须是 **Service Auth**。如果把 Service Token 加进 Action 为 Allow 的策略，Access 仍会把 App 重定向到登录页。

## 三、在 App 里填写

- **还没登录**：在登录页打开「通过 Cloudflare Access 连接」，填写 Client ID 和 Client Secret，再输入 TelePilot 用户名和密码登录。
- **已经登录**：设置 → 当前连接 → **Cloudflare Access**，填写后点「保存并验证连接」。App 会用一次只读请求验证能否穿过 Access，并显示真实结果。
- 同一页可以随时更换或清除 Token；「更换服务器」也会移除这台服务器的 Token。

Token 只保存在本机钥匙串，按服务器地址分别保存，不同步到 iCloud，不写入日志，也不会出现在错误提示里。

## 常见提示

| App 提示 | 原因与处理 |
| --- | --- |
| 这台服务器启用了 Cloudflare Access，请填写 Service Token。 | 请求被重定向到 `*.cloudflareaccess.com` 或 `/cdn-cgi/access/`，而 App 还没有 Token。按上面的步骤创建并填写。 |
| Cloudflare Access 拒绝了已填写的 Service Token…… | Token 写错、已过期或被吊销，或者 Access 应用里没有包含该 Token 的 **Service Auth** 策略。检查后在设置里更换。 |
| 服务器返回了重定向。为保护登录信息，请直接填写最终 HTTPS 地址。 | 不是 Access 的普通重定向（例如 HTTP→HTTPS、换域名）。App 仍然拒绝跟随，请直接填写最终地址。 |

Access 的拦截发生在 Cloudflare 边缘、请求没有到达 TelePilot，所以 App 不会因此清除登录会话；写操作被拦截时也明确是“未执行”，可以填好 Token 后再操作。

## 为什么 App 不直接用 Google 登录 Access

我们评估过让 App 走 Access 的 Google 登录、拿到 `CF_Authorization` Cookie 的方案，暂不提供：

- **App 内嵌网页（WKWebView）**：Google 禁止在嵌入式 WebView 中登录（会报 `disallowed_useragent`），走不通。
- **系统登录窗（ASWebAuthenticationSession）**：Google 登录可以完成，但 Cookie 留在系统浏览器会话里，App 读不到 `CF_Authorization`。
- **可行但未实现的路线**：参照 `cloudflared access login` 的令牌转交流程——用系统登录窗打开 `https://<你的域名>/cdn-cgi/access/cli?...`，登录完成后由 App 轮询 Cloudflare 的转交服务取回应用令牌（JWT），存入钥匙串，作为 `cf-access-token` 头或 `CF_Authorization` Cookie 附在请求上，并在令牌过期（再次被 Access 拦截）时提示重新验证。这依赖 Cloudflare 未公开文档的接口，且需要真实的 Access 团队环境才能验证，因此先以官方支持的 Service Token 为准。

另外，如果你的 Zero Trust 组织开启了 [严格 Service Token 认证](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)（2026-10-05 之后创建的组织默认开启），带 Token 的请求认证失败时 Access 返回 401/403 而不是重定向，App 同样能识别并提示。
