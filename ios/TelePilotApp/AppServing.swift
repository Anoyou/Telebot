import Foundation
import TelePilotCore

protocol AppServing: Sendable {
    func login(_ request: LoginRequest) async throws -> LoginResult
    func currentUser() async throws -> CurrentUser
    func accounts() async throws -> [Account]
    func account(id: Int) async throws -> Account
    func health() async throws -> HealthOverview
    func resources() async throws -> ResourceDashboard
    func version() async throws -> ServerVersion
    func pauseAccount(id: Int) async throws
    func resumeAccount(id: Int) async throws
    func logout() async throws
    func clearLocalSession() async throws
    /// 钥匙串里是否有这台服务器未过期的登录会话（只读本机）。冷启动据此决定能否先显示概览快照。
    func hasStoredSession() async throws -> Bool
    /// 本服务器保存在钥匙串里的 Cloudflare Access 凭证（Google 验证的登录令牌与高级 Service Token）。
    func cloudflareAccessConfig() async throws -> CloudflareAccessConfig
    /// 保存（nil 为清除）Service Token（高级），之后所有请求立即生效。不影响 Google 验证的登录令牌。
    func setCloudflareAccess(_ credentials: CloudflareAccessCredentials?) async throws
    /// 保存（nil 为清除）App 内 Google 验证取得的 Access 登录令牌。
    func setCloudflareAccessSession(_ session: CloudflareAccessSession?) async throws
    /// 开始 App 内 Google 验证：返回要在系统登录窗打开的地址和等待令牌的操作。
    func startCloudflareAccessLogin() async throws -> CloudflareAccessLoginTicket
    func request(path: String, method: String, body: JSONValue?) async throws -> JSONValue
    func download(path: String, method: String, body: JSONValue?) async throws -> Data
    func upload(path: String, file: Data, filename: String, fields: [String: String]) async throws -> JSONValue
    func stream(path: String, onEvent: @escaping @Sendable (JSONValue) async -> Void) async throws
    /// 第 22 轮：最近一次连接类失败的诊断记录（不含凭证）与最近一次连上服务器的时间，用于「诊断详情」。
    func lastConnectionFailure() async -> ConnectionFailureRecord?
    func lastConnectedAt() async -> Date?
}

/// 一次 App 内 Google 验证 Cloudflare Access 的过程。
struct CloudflareAccessLoginTicket: Sendable {
    /// 在系统登录窗（ASWebAuthenticationSession）打开的地址；界面测试为 nil，不弹出登录窗。
    let url: URL?
    /// 长轮询 Cloudflare 令牌转交服务，直到取回并保存登录令牌；取消所在 Task 即停止。
    let wait: @Sendable () async throws -> CloudflareAccessSession
}

extension TelePilotClient: AppServing {
    func startCloudflareAccessLogin() async throws -> CloudflareAccessLoginTicket {
        let request = try await prepareCloudflareAccessLogin()
        return CloudflareAccessLoginTicket(url: request.authorizationURL) { [self] in
            try await self.completeCloudflareAccessLogin(request)
        }
    }
}

enum AccountPresentation {
    static func name(_ account: Account) -> String {
        account.displayName.flatMap { $0.isEmpty ? nil : $0 } ?? account.phone
    }

    static func status(_ raw: String) -> String {
        switch raw {
        case "active": "已启用"
        case "paused": "已暂停"
        case "floodwait": "限流等待"
        case "dead": "会话失效"
        case "login_required": "需要重新登录"
        default: "未知状态（\(raw)）"
        }
    }

    static func actionLabel(_ account: Account) -> String? {
        switch account.status {
        case "active": "暂停账号"
        case "paused": "恢复账号"
        default: nil
        }
    }

    static func statusSymbol(_ raw: String) -> String {
        switch raw {
        case "active": "checkmark.circle"
        case "paused": "pause.circle"
        default: "exclamationmark.circle"
        }
    }
}
