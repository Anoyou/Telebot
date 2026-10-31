import Foundation

/// 「问 Agent」从哪个页面进入（第 22 轮，对齐 Web #118 的 `agent_from`）。
public enum AgentAskSource: String, Sendable, Equatable {
    case account
    case plugin

    /// 提示文字里的来源页面名（与 Web `AGENT_ACCOUNT_SOURCE_LABEL` 同一叫法）。
    public var label: String {
        switch self {
        case .account: "账号页"
        case .plugin: "插件页"
        }
    }
}

/// 账号页 / 插件页「问 Agent」的请求：打开 Agent 标签页，新建关联该账号的会话；插件页再把插件名预填进输入栏。
public struct AgentAskRequest: Equatable, Sendable, Identifiable {
    public let id: UUID
    public let accountID: Int?
    public let source: AgentAskSource
    public let pluginName: String?
    public let pluginKey: String?

    public init(accountID: Int?, source: AgentAskSource, pluginName: String? = nil, pluginKey: String? = nil, id: UUID = UUID()) {
        self.id = id
        self.accountID = accountID
        self.source = source
        self.pluginName = pluginName
        self.pluginKey = pluginKey
    }
}

public enum AgentAskContext {
    /// Agent 依赖的平台模块（`/api/system/capabilities` 的 `modules[].key`）。
    public static let aiModuleKey = "ai"

    /// 入口是否显示：服务器关闭了 AI 模块时 Agent 无法回答，隐藏按钮。
    /// 旧服务器没有模块信息（`disabledModules` 为空）时照常显示：`POST /api/system-agent/sessions` 的 `account_id`
    /// 自 0.72 起就有，旧服务器同样能带账号新建会话；只有「单账号自动关联」需要 `features.agent_session_default_account`，
    /// 缺这个标志时由 `AgentSessionAccount.newSessionBody` 按账号数代填。
    public static func isAvailable(disabledModules: Set<String>) -> Bool {
        !disabledModules.contains(aiModuleKey)
    }

    /// 插件页进入时预填到输入栏的开头（用户接着写问题再发送；不会自动发送）。
    public static func composerPrefill(pluginName: String?, pluginKey: String?) -> String? {
        let name = pluginName?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        let key = pluginKey?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
        if name.isEmpty && key.isEmpty { return nil }
        if name.isEmpty || name == key { return "关于插件「\(key.isEmpty ? name : key)」：" }
        if key.isEmpty { return "关于插件「\(name)」：" }
        return "关于插件「\(name)」（\(key)）："
    }

    /// 新会话建好后的提示。`linkedAccountName` 为实际关联的账号名（nil = 系统级）。
    public static func notice(request: AgentAskRequest, linkedAccountName: String?, autoLinked: Bool) -> String {
        var parts: [String] = []
        if let linkedAccountName {
            if request.accountID != nil {
                parts.append("已带入账号「\(linkedAccountName)」（来自\(request.source.label)）")
            } else if autoLinked {
                parts.append("只有一个账号，新会话已自动关联「\(linkedAccountName)」")
            } else {
                parts.append("新会话已关联「\(linkedAccountName)」")
            }
        } else {
            parts.append("新会话为系统级（来自\(request.source.label)，未指定账号）")
        }
        if composerPrefill(pluginName: request.pluginName, pluginKey: request.pluginKey) != nil {
            parts.append("插件名已填进输入栏，补充问题后发送")
        }
        return parts.joined(separator: "；") + "。可在会话设置 › 账号上下文修改。"
    }

    /// 当前会话是不是刚建好、还没说过话、且已经关联同一个账号：是就直接复用，避免连点产生一串空会话。
    public static func canReuseCurrentSession(
        requestedAccountID: Int?,
        currentSessionID: String?,
        currentAccountID: Int?,
        hasMessages: Bool,
        runActive: Bool,
        channel: String,
        status: String
    ) -> Bool {
        guard let requestedAccountID, currentSessionID != nil else { return false }
        guard !hasMessages, !runActive, channel != "bot", status != "archived" else { return false }
        return currentAccountID == requestedAccountID
    }
}
