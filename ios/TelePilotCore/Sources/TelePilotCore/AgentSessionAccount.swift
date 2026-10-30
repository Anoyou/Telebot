import Foundation

/// 新建 Agent 会话时的账号上下文（第 20 轮，#111 后续）。
///
/// - 指定了账号（例如从某个账号的页面进入）→ 直接关联；
/// - 否则带 `default_account: true`：新服务器（`features.agent_session_default_account`）只有 1 个账号时自动关联；
///   旧服务器忽略该字段，由 App 在只读到 1 个账号时自己填 `account_id`。多账号保持「全局」。
public enum AgentSessionAccount {
    public static let featureKey = "agent_session_default_account"

    /// `POST /api/system-agent/sessions` 的请求体。`accountIDs` 为 nil 表示账号列表尚未读取。
    public static func newSessionBody(
        requestedAccountID: Int?,
        accountIDs: [Int]?,
        serverSupportsDefault: Bool
    ) -> [String: JSONValue] {
        if let requestedAccountID { return ["account_id": .integer(Int64(requestedAccountID))] }
        var body: [String: JSONValue] = ["default_account": .bool(true)]
        if !serverSupportsDefault, let ids = accountIDs, ids.count == 1 {
            body["account_id"] = .integer(Int64(ids[0]))
        }
        return body
    }

    /// 新会话是否因“只有 1 个账号”而自动关联：新服务器看响应 `account_auto_linked`，旧服务器看是否由 App 代填。
    public static func wasAutoLinked(request: [String: JSONValue], response: JSONValue) -> Bool {
        guard let linked = response["account_id"]?.intValue else { return false }
        if response["account_auto_linked"]?.boolValue == true { return true }
        return request["default_account"]?.boolValue == true && request["account_id"]?.intValue == linked
    }
}
