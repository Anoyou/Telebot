# 原生 iOS 推送（可选）

APNs 是 Apple Push Notification service。App 在后台或关闭时，服务器仍可通过 Apple 向设备显示通知。此功能默认关闭，未配置不会连接 Apple，也不影响现有 Web/PWA 或 Telegram 通知。

## 自签与配置

需要支持 Push Notifications 的 Apple Developer App ID、与 Bundle ID 相符且包含 `aps-environment` 权限的签名描述文件，以及 Apple 签发的 APNs `.p8` P-256 密钥、Team ID 和 Key ID。普通免费自签不能保证具备推送权限；没有权限仍可使用 App 的全部前台功能。开发签名用 sandbox，Ad Hoc/正式分发通常用 production，以实际签名 entitlement 为准。

在 App 通知设置中配置服务端凭据，开启 APNs，再允许系统通知并注册当前设备。使用“发送测试通知”检查 Apple 是否接受请求；Apple 接受不代表设备必定展示，仍受系统通知权限和专注模式影响。后端运行环境须安装项目声明的 `httpx[socks,http2]` 依赖并允许 HTTPS 出站到 `api.push.apple.com` 或 `api.sandbox.push.apple.com`。

服务端 `.p8` 和设备令牌分别保存为 `apns:config.auth_key_enc`、`apns:device:*.device_token_enc`，均经 MASTER_KEY 加密且受 `app.scripts.rekey` 覆盖。凭据、设备 metadata 和内部队列从配置导入/导出中全部排除，即使选择包含敏感配置也不导出。备份数据库和 MASTER_KEY 是另一条现有维护路径。

## API 契约

全部接口使用现有登录 Cookie、CSRF 和管理员鉴权：

- `GET /api/apns/config`：返回状态、Team/Key/Bundle ID、环境与 `has_auth_key`，不返回密钥。
- `PATCH /api/apns/config`：`enabled`、`team_id`、`key_id`、`bundle_id`、`environment`、`auth_key_p8`、`clear_auth_key`；只接受 ES256 P-256 私钥。
- `GET /api/apns/devices`：只列当前用户设备，不返回令牌。
- `POST /api/apns/devices`：`device_token`（64–128 位偶数长度十六进制）、`device_id`（稳定安装 ID）、`bundle_id`、`environment`、`server_origin_sha256`（当前规范化 HTTPS origin 的 UTF-8 SHA-256、小写64位）、`categories`。
- `categories` 支持 `assistant_approval`、`assistant_result`、`account_alert`，空数组为不订阅业务通知；测试通知仍可发送。
- `DELETE /api/apns/devices/{device_id}`：只移除当前用户绑定。
- `POST /api/apns/test`：请求 `device_id`，返回 `ok/attempted/accepted/failed/unregistered/message/event_id/device_id`。

设备最多20台；注册会续期7天租约，App 每次回到前台应重新注册续期。退出登录先注销当前设备；离线注销失败仍可本地退出，租约到期后服务器停止投递。修改管理员密码或删除用户后设备立即失去投递资格。Bundle ID 或环境与配置不匹配时拒绝注册。没有客户端任意触发业务事件的 API。

推送 `userInfo` 根字段契约（不使用嵌套 telepilot 对象）：

```json
{
  "aps": {"alert": {"title": "TelePilot 待确认", "body": "助手有一项操作等待你确认。打开 App 查看详情。"}, "sound": "default", "thread-id": "assistant_approval"},
  "kind": "assistant_approval",
  "event_id": "action UUID",
  "destination": "action",
  "action_id": "action UUID",
  "server_origin_sha256": "registered origin SHA-256"
}
```

账号异常：`destination=account`、`account_id`；助手任务终态：`destination=assistant`、`session_id`；测试：`destination=overview`。App 只在 origin hash 匹配当前服务器、登录和设备解锁后处理路由，否则忽略。通知仅含固定通用文案与导航标识，不包含手机号、账号名称、聊天正文、审批摘要、工具参数、密钥、日志或错误详情。

## 后台投递语义

待确认 Action 创建、助手 Run 进入成功/失败/取消、账号进入 dead/login_required/floodwait 时，在同一个业务事务写入 `apns:outbox:*`。事务回滚不会留下推送事件。ORM监听器在主进程与worker均注册；现有 bulk 更新账号的路径显式写入队列。此过程只写数据库，不等待网络。

单个后台任务每5秒扫描最多5条，PostgreSQL使用 `FOR UPDATE SKIP LOCKED` 协调多个Web进程，每次每事件最多发送一台设备并持久记录剩余设备；每设备最多3次尝试（30/60秒退避）。已成功设备不参与常规重试，事件24小时到期，队列达到1000条时跳过新通知（并发写入允许短暂超出阈值）；过期订阅会定期删除。处理前重新检查审批仍待确认且未过期。进程关闭取消并等待后台任务，数据库队列供重启接续。

Apple接受与本地提交之间进程崩溃时可能重复投递，采用稳定 collapse ID 合并尚未显示的同事件通知，不承诺严格恰好一次。`403 ExpiredProviderToken` 只刷新一次JWT重试；普通错误不禁用设备；HTTP410才清理设备，需重新注册。错误日志仅记录异常类型，不输出令牌URL、Authorization或私钥。

参考：[Apple APNs请求](https://developer.apple.com/documentation/usernotifications/sending-notification-requests-to-apns)、[响应处理](https://developer.apple.com/documentation/usernotifications/handling-notification-responses-from-apns)、[Token鉴权](https://developer.apple.com/documentation/usernotifications/establishing-a-token-based-connection-to-apns)。
