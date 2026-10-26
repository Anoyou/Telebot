# 在 Cloudflare Access 后面使用 TelePilot iOS

很多人会把 TelePilot 管理台放在 Cloudflare Tunnel + [Cloudflare Access](https://developers.cloudflare.com/cloudflare-one/access-controls/) 后面，例如 Allow 策略 = 邮箱白名单（Include）+ Google 登录方式（Require）。浏览器能正常用，是因为它会跟随 Access 的登录跳转、完成 Google 登录，并带上 `CF_Authorization` Cookie。iOS App 不跟随任何重定向（安全规则：防止登录信息被转发到别的地址），所以需要单独完成一次 Access 验证。

iOS 0.11.0 起，App **直接用 Google 验证 Access**，Cloudflare 后台不需要做任何修改——不用加绕过（Bypass），也不用新增策略。

## 一、用 Google 验证（推荐）

### 怎样操作

- **还没登录**：登录页填写服务器地址后，在「Cloudflare Access（可选）」区点「**使用 Google 验证**」。
- **已经登录**：设置 → 当前连接 → **Cloudflare Access** → 「使用 Google 验证」。
- 系统会询问「“TelePilot”想要使用“你的域名”登录」，选择「继续」，在弹出的系统登录窗里和浏览器一样完成 Google 登录。
- 登录完成后 App 自动取回 Access 登录令牌、关闭窗口，并显示「已验证 · 账号邮箱 · 到期时间」。之后再输入 TelePilot 用户名和密码登录（已登录则直接恢复读取数据）。

### 它是怎样工作的

App 照搬 Cloudflare 官方命令行工具 `cloudflared access login` 的流程（[cloudflare/cloudflared](https://github.com/cloudflare/cloudflared) 的 `token` 包）：

1. 向你的服务器发一次不带任何凭证的 `HEAD /`（请求头 `cf-access-metadata-request: true`，不跟随重定向），读出 Access 应用的 AUD。
2. 本机生成一次性 X25519 密钥对，用系统登录窗（`ASWebAuthenticationSession`）打开 `https://<你的域名>/cdn-cgi/access/cli?aud=…&token=<公钥>&redirect_url=…&send_org_token=true&edge_token_transfer=true`。Google 不允许在 App 内嵌网页（WKWebView）里登录，系统登录窗则可以。
3. 同时长轮询 Cloudflare 的令牌转交服务 `https://login.cloudflareaccess.org/transfer/<公钥>`。登录完成后，Access 把应用令牌（JWT，与浏览器的 `CF_Authorization` 同类）用本机公钥加密（NaCl box）交给转交服务，App 取回后用私钥解密。私钥只在内存里，别人拿不到这个令牌。转交请求使用独立的网络会话，不带任何 TelePilot 凭证。
4. 之后 App 的每个请求（登录、读取、写操作、下载上传、助手实时输出、推送注册等）都带 `cf-access-token: <令牌>` 请求头，与 `cloudflared access curl` 相同。

令牌只保存在本机钥匙串，按服务器地址分别保存，不同步到 iCloud，不写入日志，也不会出现在错误提示里。它的有效期等于 Access 应用的 **Session Duration**（例如通配应用设 720 小时就是 30 天）。

### 到期与重新验证

- 令牌到期后 App 不再发送它；请求被 Access 拦截（302 到 `*.cloudflareaccess.com` 或 `/cdn-cgi/access/`）时，App 会直接弹出 Cloudflare Access 页面，点「重新用 Google 验证」即可。
- 被拦截的请求没有到达 TelePilot，App 不会清除 TelePilot 登录会话；写操作被拦截时明确是“未执行”，重新验证后 **不会自动重新提交**，请确认后再操作一次。
- 想手动作废：设置 → Cloudflare Access → 「移除 Access 登录」只删除本机保存的令牌；在 Cloudflare 后台 **Zero Trust → My Team → Users** 里可以吊销该用户的会话。

### Binding Cookie

如果 Access 应用开启了 [Binding Cookie](https://developers.cloudflare.com/cloudflare-one/access-controls/applications/http-apps/authorization-cookie/#binding-cookie)，Access 会额外要求浏览器专属的绑定 Cookie，App 取回的令牌可能因此被拒（表现为验证成功后请求仍反复被拦截）。遇到这种情况，可以为 TelePilot 所在的应用关闭 Binding Cookie，或改用下面的 Service Token。

## 二、高级：Service Token（可选）

只在无法使用 Google 验证时需要（例如自动化脚本，或应用开启了 Binding Cookie）。它要求在 Access 应用里 **另加一条策略**：

1. Cloudflare 控制台 → **Zero Trust** → **Access** → **Service Auth** → **Service Tokens**（新版控制台可能显示为 Access controls → Service credentials），**Create Service Token**。创建后显示的 **Client Secret 只出现一次**，请立即复制。
2. **Access** → **Applications**，编辑保护 TelePilot 的应用，新增一条策略：**Action** = `Service Auth`，**Include** = `Service Token`（选中刚创建的 Token）。原来的 Google Allow 策略保持不变。
3. App 里：设置 → Cloudflare Access → 「高级：Service Token」，或登录页打开「高级：改用 Service Token」，填写 Client ID 与 Client Secret。

> Action 必须是 **Service Auth**。把 Service Token 加进 Action 为 Allow 的策略，Access 仍会把请求重定向到登录页。

填写了 Service Token 时，App 只带 `CF-Access-Client-Id` / `CF-Access-Client-Secret`，不再带 Google 验证的令牌；清除 Service Token 后自动改回 Google 验证。如果你的 Zero Trust 组织开启了 [严格 Service Token 认证](https://developers.cloudflare.com/cloudflare-one/access-controls/service-credentials/service-tokens/)，Token 无效时 Access 返回 401/403 而不是重定向，App 同样能识别并提示。

## 常见提示

| App 提示 | 原因与处理 |
| --- | --- |
| 这台服务器启用了 Cloudflare Access，请先用 Google 完成 Access 验证。 | 还没有 Access 凭证。点「使用 Google 验证」。 |
| Cloudflare Access 登录已过期，请重新用 Google 验证。 | 令牌超过了 Access 应用的会话时长。点「重新用 Google 验证」。 |
| Cloudflare Access 不再接受当前的登录…… | 令牌在后台被吊销、策略或邮箱白名单变了，或开启了 Binding Cookie。重新验证；仍不行时检查策略或改用 Service Token。 |
| 没有在这台服务器前找到 Cloudflare Access 应用，无需验证。 | 服务器地址没有被 Access 保护（或地址填错）。直接登录 TelePilot 即可。 |
| Cloudflare Access 拒绝了已填写的 Service Token…… | Token 写错、过期或被吊销，或缺少 Action 为 **Service Auth** 的策略。 |
| 服务器返回了重定向。为保护登录信息，请直接填写最终 HTTPS 地址。 | 不是 Access 的重定向（例如 HTTP→HTTPS、换域名）。App 仍拒绝跟随，请填写最终地址。 |

## Web 管理台与其他入口

- Web 管理台由浏览器照常走 Google 登录。Access 会话过期后，页面里的数据请求会被 302 到 Cloudflare 登录域名而跨域失败；Web 前端会把这类失败提示为“Cloudflare Access 登录已过期，请刷新页面重新登录”。
- Access 拦截所有未登录的入站请求。TelePilot 本身对 Telegram 使用 MTProto 长连接、Bot 使用长轮询，都是服务器主动出站，不受影响；如果你自己配置了需要外部服务回调到这台服务器的地址（例如第三方 Webhook 推送），需要为那条具体路径单独配置 Bypass 或 Service Auth。
