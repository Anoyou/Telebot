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
    func request(path: String, method: String, body: JSONValue?) async throws -> JSONValue
    func download(path: String, method: String, body: JSONValue?) async throws -> Data
    func upload(path: String, file: Data, filename: String, fields: [String: String]) async throws -> JSONValue
    func stream(path: String, onEvent: @escaping @Sendable (JSONValue) async -> Void) async throws
}

extension TelePilotClient: AppServing {}

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
