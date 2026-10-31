import Foundation

/// 第 22 轮：「转账通知以插件结果为准」开关（`log_retention.payment_source_plugin_authoritative`）的展示规则。
/// 数据来自 `GET /api/payment-sources/status`（旧服务器没有该接口 → 不显示）；生效与否完全以服务器当下配置为准，
/// App 只显示状态和说明，修改请在 Web。文案与 Web `frontend/src/lib/paymentSource.ts` 同一套。
public struct PaymentSourceSwitchView: Equatable, Sendable {
    public enum Tone: String, Sendable { case neutral, success, warning }

    public struct Source: Equatable, Sendable {
        public let pluginKey: String
        public let displayName: String
        public let active: Bool
        public let warning: String?
    }

    public let checked: Bool
    public let stateLabel: String
    public let tone: Tone
    /// 某个插件此刻没参与解析的原因（只在单个插件详情里用）。
    public let warning: String?
    public let sources: [Source]

    public static let settingKey = "log_retention.payment_source_plugin_authoritative"
    public static let explanation = [
        "关：以系统自带的旧解析为准，这个插件只在旁边对照，结果不用于结算。",
        "开：以这个插件读出的付款人、收款人和金额为准；旧格式认不出的可信 Bot 消息也交给插件判断。",
        "插件出错或超时会自动回退旧解析；随时可以关掉，30 秒内生效，结算规则本身不变。",
    ]
    public static let editHint = "App 里只显示状态；要改请在 Web「服务器设置 › 平台与模块」或 Web 插件详情里改。转账金额是群内游戏币。"

    /// - Parameters:
    ///   - status: `GET /api/payment-sources/status` 的结果。
    ///   - pluginKey: 插件详情里传插件 key；不是转账通知插件时返回 nil（不显示）。传 nil 表示汇总页，显示全部转账通知插件。
    public static func build(_ status: JSONValue?, pluginKey: String?) -> PaymentSourceSwitchView? {
        guard let status, status.objectValue != nil else { return nil }
        let sources: [Source] = (status["sources"]?.arrayValue ?? []).compactMap { item in
            guard let key = item["plugin_key"]?.stringValue, !key.isEmpty else { return nil }
            let active = item["active"]?.boolValue ?? false
            let reason = item["inactive_reason"]?.stringValue
            return Source(pluginKey: key, displayName: item["display_name"]?.stringValue ?? key, active: active,
                          warning: active ? nil : ((reason?.isEmpty == false ? reason : nil) ?? "这个插件此刻没有参与转账通知解析"))
        }
        let checked = status["authoritative"]?.boolValue ?? false
        let effectivePlugin = status["effective"]?.stringValue == "plugin"
        if let pluginKey {
            guard let source = sources.first(where: { $0.pluginKey == pluginKey }) else { return nil }
            let (label, tone) = state(checked: checked, working: effectivePlugin && source.active)
            return PaymentSourceSwitchView(checked: checked, stateLabel: label, tone: tone, warning: source.warning, sources: [source])
        }
        let (label, tone) = state(checked: checked, working: effectivePlugin && sources.contains { $0.active })
        return PaymentSourceSwitchView(checked: checked, stateLabel: label, tone: tone, warning: nil, sources: sources)
    }

    private static func state(checked: Bool, working: Bool) -> (String, Tone) {
        if !checked { return ("以旧解析为准（插件只做对照）", .neutral) }
        if working { return ("以插件为准（生效中）", .success) }
        return ("已打开，但这个插件没在工作，仍按旧解析", .warning)
    }
}
