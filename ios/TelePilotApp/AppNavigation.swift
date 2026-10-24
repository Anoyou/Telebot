import Foundation

/// Notification payloads only carry navigation IDs, never server secrets.
enum AppRoute: Hashable, Sendable {
    case overview
    case accounts
    case account(Int)
    case plugins
    case operations
    case ai
    case assistant(sessionID: String? = nil)
    case action(String)
    case logs
    case ledger
    case webhooks
    case settings
    case maintenance

    init?(notificationUserInfo: [AnyHashable: Any]) {
        guard let destination = notificationUserInfo["destination"] as? String else { return nil }
        switch destination {
        case "overview": self = .overview
        case "accounts": self = .accounts
        case "account":
            guard let raw = notificationUserInfo["account_id"],
                  let id = Int(String(describing: raw)), id > 0 else { return nil }
            self = .account(id)
        case "plugins": self = .plugins
        case "operations": self = .operations
        case "ai": self = .ai
        case "assistant":
            let session = notificationUserInfo["session_id"] as? String
            self = .assistant(sessionID: session?.isEmpty == true ? nil : session)
        case "action":
            guard let id = notificationUserInfo["action_id"] as? String, !id.isEmpty else { return nil }
            self = .action(id)
        case "logs": self = .logs
        case "ledger": self = .ledger
        case "webhooks": self = .webhooks
        case "maintenance": self = .maintenance
        case "settings": self = .settings
        default: return nil
        }
    }
}
