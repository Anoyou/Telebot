# iOS 推送通知配置教程（APNs）

本文手把手说明如何让 TelePilot iOS App 在后台或关闭时也能收到通知：助手待确认、助手任务结果、Telegram 账号告警。通道是 Apple Push Notification service（APNs，Apple 推送服务），由你自己的 TelePilot 服务器直接连接 Apple 发送，不经过任何第三方。

> 推送是可选功能，默认关闭。不配置也能正常使用 App 的全部前台功能。接口契约和后台投递语义见 [APNS.md](./APNS.md)。

## 一、你需要准备什么

| 项目 | 说明 |
| --- | --- |
| 付费 Apple Developer 账号 | 推送依赖 App ID 的 Push Notifications 能力和 APNs 密钥，只有加入 Apple Developer Program 的账号才能创建。免费 Apple ID 的个人签名**没有推送权限**。创建密钥需要 Account Holder 或 Admin 角色。 |
| 带推送权限的 App 签名 | App ID 开启 Push Notifications，描述文件（provisioning profile）里包含 `aps-environment`。 |
| APNs 密钥 `.p8` | 一个 ES256（P-256）私钥文件，配合 Key ID、Team ID 签发访问 Apple 的 JWT。 |
| 服务器可访问 Apple | 后端需能 HTTPS（HTTP/2）出站到 `api.push.apple.com` 或 `api.sandbox.push.apple.com` 的 443 端口。后端依赖 `httpx[socks,http2]` 已在 `backend/pyproject.toml` 中声明，官方镜像已包含。 |
| 一台真机 | 建议真机测试。 |

## 二、在 Apple Developer 后台操作

以下入口都在 [Certificates, Identifiers & Profiles](https://developer.apple.com/account/resources)。

### 1. 给 App ID 开启 Push Notifications

1. 打开 **Identifiers**，找到与你安装包 Bundle ID 相同的 App ID（仓库默认是 `com.telepilot.personal`，自签时通常会改成你自己的前缀，例如 `com.yourname.telepilot`）。
2. 在 Capabilities 中勾选 **Push Notifications**，保存。
3. 重新生成描述文件。使用 Xcode「Automatically manage signing」时，Xcode 会自动刷新。

### 2. 让工程带上推送 entitlement

仓库默认不强制推送 entitlement，方便普通自签。需要推送时二选一：

- 在 Xcode 的 Build Settings 里把自定义设置 `TELEPILOT_PUSH_ENTITLEMENTS` 设为 `TelePilotApp/TelePilot.entitlements`（文件里已有 `aps-environment`）；或
- 在 Signing & Capabilities 中点 **+ Capability → Push Notifications**。

使用第三方自签工具时，也必须注入与描述文件一致的 `aps-environment`。

### 3. 创建 APNs 密钥（.p8）

1. 打开 **Keys**，点左上角 **+**。
2. 填写 Key Name（例如 `TelePilot APNs`）。
3. 勾选 **Apple Push Notification service (APNs)**，点旁边的 **Configure**。
4. 选择**环境**（Sandbox、Production，或两者都可）和**密钥类型**（Team Scoped：可用于整个 Team 的所有 App；Topic Specific：只用于选定的 Bundle ID）。个人使用选 Team Scoped 最省事。
5. 点 Continue → Confirm，然后点 **Download** 下载 `AuthKey_XXXXXXXXXX.p8`。

> ⚠️ `.p8` **只能下载一次**，Apple 不会替你保存。请放在安全的地方；泄露后应立刻在 Keys 页面撤销并重新创建。
>
> 新版 Apple 后台允许密钥只限某一个环境使用。如果你的密钥只勾了 Sandbox，就不能给 TestFlight / Ad Hoc（production）安装包发推送，反之亦然；这种不匹配会返回 `BadEnvironmentKeyIdInToken`。

### 4. 找到 Key ID 和 Team ID

- **Key ID**：在 Keys 列表点开刚才的密钥，详情页显示的 10 位字符（也出现在下载的文件名 `AuthKey_<KeyID>.p8` 里）。
- **Team ID**：在开发者账号的 **Membership details（会员资格详情）** 中，10 位大写字母和数字。

## 三、在 TelePilot 中配置

服务端 APNs 配置**没有环境变量**，统一保存在数据库（`SystemSetting` 中的 `apns:config`，`.p8` 经 `MASTER_KEY` 加密为 `auth_key_enc`，并纳入 `app.scripts.rekey`）。目前 Web 管理台没有 APNs 配置页，通过 iOS App 或 API 配置：

### 方式 A：在 iOS App 中配置（推荐）

1. 打开 App → **设置 → Apple 推送通知**。
2. 在「服务器推送通道」中填写：
   - **Team ID**、**Key ID**：上一步拿到的 10 位字符；
   - **Bundle ID**：必须与当前安装包一致，可点「填入本机 Bundle ID」；
   - **推送环境**：见下一节，可点「改为本机环境」；
   - 点「选择 .p8 私钥文件」，从「文件」App 中选中 `AuthKey_XXXXXXXXXX.p8`；
   - 打开「启用 APNs 服务」。
3. 点「保存服务器配置」并确认。私钥保存后不会再显示，只显示「服务器已保存」。

### 方式 B：调用 API

需要已登录的管理员会话（Cookie + CSRF）：

```http
PATCH /api/apns/config
{
  "enabled": true,
  "team_id": "ABCDE12345",
  "key_id": "FGHIJ67890",
  "bundle_id": "com.yourname.telepilot",
  "environment": "sandbox",
  "auth_key_p8": "-----BEGIN PRIVATE KEY-----\n...\n-----END PRIVATE KEY-----"
}
```

`environment` 只能是 `sandbox` 或 `production`（默认 `production`）。只接受 ES256 / P-256 私钥；字段缺任何一项都不能启用。

## 四、sandbox 还是 production？

APNs 有两套独立环境，设备令牌也互不通用：

| 安装方式 | 描述文件中的 `aps-environment` | TelePilot 中选择 |
| --- | --- | --- |
| Xcode 直接运行到真机（Debug）、开发描述文件签名 | `development` | **sandbox** |
| Ad Hoc、TestFlight、App Store 分发 | `production` | **production** |

App 不根据 Debug / Release 猜测，而是读取安装包内实际嵌入的描述文件；TestFlight / App Store 安装包没有嵌入描述文件，按 production 处理。设置页的「推送环境匹配」检查项会直接显示本机环境与服务器环境是否一致。注册设备时，服务器会拒绝 Bundle ID 或环境与配置不一致的设备（HTTP 409）。

> 同一台服务器同一时间只能配置一个环境。如果你一边用 Xcode 调试、一边用 TestFlight，需要按当前使用的那份安装包切换。

## 五、在 App 中启用并测试

1. 在「Apple 推送通知」页面点「**授权并注册设备**」，在系统弹窗中选「允许」。
2. 查看顶部「推送状态」四项检查：
   - **系统通知权限**：已允许；被拒绝时可点「打开系统通知设置」；
   - **设备已注册**：服务器上已有本机记录及有效期；
   - **服务器 APNs 已配置**：四项凭据齐全且已启用；
   - **推送环境匹配**：本机与服务器环境、Bundle ID 一致。
3. 在「通知类别」中选择要接收的通知：助手待确认、助手任务结果、账号告警（只影响本机）。
4. 点「**发送测试通知**」。显示「Apple 已接受测试通知」后几秒内应收到「TelePilot 推送测试」横幅；失败时会显示 Apple 返回的错误代码，按下表排查。

> 「Apple 已接受」说明服务器、密钥和设备令牌都有效；是否弹出横幅仍受专注模式、通知摘要和系统通知设置影响。

设备注册有 7 天租约，App 每次回到前台都会自动续期并上传最新的设备令牌；退出登录会先注销本机设备；修改管理员密码后旧设备立即失效，需要重新注册。

## 六、常见错误排查

测试结果或设备的「最近一次投递失败」会显示 Apple 返回的 `reason`。对照下表处理（依据 Apple《Handling notification responses from APNs》）：

| 错误（HTTP 状态） | 含义 | 怎么修 | TelePilot 自动处理 |
| --- | --- | --- | --- |
| `BadDeviceToken`（400） | 设备令牌无效，或与当前环境不符 | 检查服务器推送环境是否与本机签名一致；改正后在 App 中重新注册 | 暂停该设备、不再重试，重新注册即恢复 |
| `DeviceTokenNotForTopic`（400） | 令牌不属于该 Bundle ID | 把服务器 Bundle ID 改成安装包的 Bundle ID | 同上 |
| `BadTopic` / `TopicDisallowed`（400） | Bundle ID 无效或不允许推送 | 确认 App ID 已开启 Push Notifications，且与密钥属于同一 Team | 不重试 |
| `InvalidProviderToken`（403） | JWT 无法验证：Team ID、Key ID 与 `.p8` 不匹配，或密钥已撤销 | 核对三项，必要时重新上传 `.p8` | 有限次重试，等待你修正配置 |
| `BadEnvironmentKeyIdInToken`（403） | 密钥只允许另一个环境使用 | 换用对应环境的密钥，或切换推送环境 | 重建连接后有限次重试 |
| `ExpiredProviderToken`（403） | JWT 超过 1 小时 | 一般无需处理；若反复出现请校准服务器时间（NTP） | 立即重新签发并重试一次 |
| `TooManyProviderTokenUpdates`（429） | JWT 刷新过于频繁（每连接 20 分钟内多次） | 一般无需处理 | JWT 缓存 30 分钟 |
| `Unregistered` / `ExpiredToken`（410） | App 已删除或设备令牌已失效 | 重新打开 App 即会重新注册 | 立即删除该设备 |
| `PayloadTooLarge`（413） | 内容超过 4 KB | 不应出现（模板固定），请反馈 | 不重试 |
| `TooManyRequests`（429） | 短时间内向同一设备发送过多 | 稍后再试 | 退避 30 / 60 秒重试 |
| `InternalServerError`（500）/ `ServiceUnavailable`、`Shutdown`（503） | Apple 服务端暂时故障 | 无需处理 | 按 Apple 建议 15 分钟后重试 |
| `无法连接 APNs` | 服务器连不上 Apple 或缺少 HTTP/2 依赖 | 检查出站网络、防火墙、代理与 DNS；确认后端已安装 `httpx[http2]` | 重建连接并有限次重试 |
| 注册时提示「Bundle ID 或推送环境与服务端配置不匹配」 | 服务器拒绝了本机注册（409） | 按「推送环境匹配」检查项修改服务器配置 | — |
| 「当前签名未声明推送环境」 | 安装包没有推送 entitlement | 按第二节开启 Push Notifications 后重新签名安装 | — |
| 「Apple 尚未提供推送设备令牌」 | iOS 没有返回令牌 | 检查网络、签名中的推送权限，换真机重试 | — |
| Apple 已接受但没看到横幅 | 被专注模式、通知摘要或系统设置拦截 | 在系统设置 → 通知 → TelePilot 中检查横幅与专注模式 | — |

每个业务通知最多尝试 3 次，24 小时后过期；Apple 不接受重试的错误（`BadDeviceToken`、`DeviceTokenNotForTopic`、`Forbidden`、`ExpiredToken`、`Unregistered`、`PayloadTooLarge` 等）不会重试。

## 七、隐私说明

通知只包含固定的通用文案（例如「助手有一项操作等待你确认。打开 App 查看详情。」）和导航标识，不包含手机号、账号名称、聊天内容、审批摘要、工具参数、密钥或日志。点开通知时，App 会校验通知来自当前登录的服务器，并在解锁后才跳转到对应页面。

## 参考

- [Establishing a token-based connection to APNs](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns)
- [Sending notification requests to APNs](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns)
- [Handling notification responses from APNs](https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns)
- [Create a private key to access a service](https://developer.apple.com/help/account/manage-keys/create-a-private-key/)
