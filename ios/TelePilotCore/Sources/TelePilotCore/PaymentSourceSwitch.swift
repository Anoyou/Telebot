import Foundation

/// 第 22 轮：「转账通知以插件结果为准」开关（`log_retention.payment_source_plugin_authoritative`）的展示规则。
/// 数据来自 `GET /api/payment-sources/status`（旧服务器没有该接口 → 不显示）；生效与否完全以服务器当下配置为准。
/// 第 23 轮起 App 里也能拨：先弹二次确认（文案与 Web `paymentSourceConfirmText` 一致），再写入 Web 同一个接口
/// `PATCH /api/system/settings`（`{"log_retention": {"payment_source_plugin_authoritative": …}}`），不自动重试。
/// 文案与 Web `frontend/src/lib/paymentSource.ts`、`PaymentSourceSwitch.tsx` 同一套。
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
    public static let editHint = "拨动开关会先弹出确认，确认后才写入服务器；Web「服务器设置 › 平台与模块」和 Web 插件详情里也能改。转账金额是群内游戏币。"
    /// 服务器参数页（全部参数）里这一行的只读说明：去专门页面拨，避免绕过确认。
    public static let serverSettingsHint = "去「插件 › 转账通知判定」拨开关（会先弹确认），这里只显示状态。"

    /// 二次确认弹窗的文案（与 Web `paymentSourceConfirmText` 逐字一致）。
    public struct Confirmation: Equatable, Sendable {
        public let title: String
        public let message: String
        public let confirmLabel: String
    }

    public static func confirmation(turningOn next: Bool) -> Confirmation {
        next
            ? Confirmation(title: "改为以插件结果为准？",
                           message: "打开后转账通知以转账通知插件读出的结果为准（30 秒内生效）。插件出错或超时会自动回退旧解析，随时可以关掉。",
                           confirmLabel: "打开")
            : Confirmation(title: "改回以旧解析为准？",
                           message: "关闭后转账通知以系统自带的旧解析为准，插件只做对照（30 秒内生效）。",
                           confirmLabel: "关闭")
    }

    /// 写入请求体：只带这一个子键（与 Web 相同）。
    public static func patchBody(enabled: Bool) -> JSONValue {
        .object(["log_retention": .object(["payment_source_plugin_authoritative": .bool(enabled)])])
    }

    /// 成功提示（与 Web toast 一致）。
    public static func successMessage(enabled: Bool) -> String {
        enabled ? "已改为以插件结果为准，30 秒内生效" : "已改回以旧解析为准，30 秒内生效"
    }

    /// 失败提示：开关保持原样（以服务器为准）。
    public static func failureMessage(_ reason: String) -> String {
        "保存失败：\(reason)（开关没有改动，请刷新确认当前状态）"
    }

    /// 结果未知（超时、断网）：不自动重试，请刷新核对。
    public static let uncertainMessage = "保存结果未知：网络中断，没有自动重试。请稍后刷新，确认服务器上的实际状态。"

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

// MARK: - 第 24 轮：找得到开关（可发现性）

extension PaymentSourceSwitchView {
    /// 插件详情里「转账通知判定」这一段该显示什么。
    public enum Availability: Equatable, Sendable {
        /// 服务器把这个插件列为转账通知来源：显示开关（原样）。
        case available(PaymentSourceSwitchView)
        /// 服务器支持，但这个插件不在来源列表里（插件版本过旧、没声明 `payment_sources`、刚装好还没刷新）：
        /// 显示一行原因；`global` 是汇总状态（开关当前是开是关），读不到时为 nil。
        case notParticipating(global: PaymentSourceSwitchView?)
        /// 服务器没有状态接口（404，旧版本）：显示「需要服务器 0.116.0 以上才能设置」。
        case serverTooOld
        /// 读取失败（断网等）：显示原因，可下拉重试。
        case unreadable(String)
        /// 不是转账通知插件：整段不显示。
        case hidden
    }

    /// 服务器在这个版本起能在 App 里设置（与用户说的版本一致，只用于文案）。
    public static let minimumServerVersion = "0.116.0"

    public static let serverTooOldMessage =
        "需要服务器 \(minimumServerVersion) 以上才能设置「转账通知以插件结果为准」。到「App 设置 › 软件更新」更新服务器后再来。"

    public static let notParticipatingMessage =
        "这个插件没在参与转账通知解析：服务器没把它认作转账通知插件——通常是插件版本过旧（还没声明转账通知解析），到「插件 › 已安装」更新它；刚安装或刚更新的，下拉刷新再看。"

    public static func unreadableMessage(_ reason: String) -> String {
        "没能读取转账通知判定：\(reason)。下拉刷新重试。"
    }

    /// 插件 key 看起来是转账通知插件（官方插件 `payment-source-cn-transfer`，以及同一命名约定的其它解析插件）。
    /// 只用于「服务器没列出它时要不要解释原因」，是否真的参与解析以服务器 `/api/payment-sources/status` 为准。
    public static func isTransferPluginKey(_ key: String) -> Bool {
        let lower = key.lowercased()
        return lower.hasPrefix("payment-source") || lower.hasPrefix("payment_source")
    }

    /// 插件信息里声明了转账通知解析（`payment_sources` 非空，或 tags 含 `payment_source`）。字段缺失时为 false。
    public static func declaresPaymentSource(_ feature: JSONValue?) -> Bool {
        guard let feature else { return false }
        for candidate in [feature, feature["manifest"] ?? .null, feature["manifest_json"] ?? .null] {
            if let sources = candidate["payment_sources"]?.arrayValue, !sources.isEmpty { return true }
            let tags = (candidate["tags"]?.arrayValue ?? []).compactMap(\.stringValue)
            if tags.contains("payment_source") || tags.contains("payment-source") { return true }
        }
        return false
    }

    /// 搜索插件时「转账」「收款」「付款」「transfer」等词也能找到转账通知插件
    /// （官方插件显示名是「转账通知解析（中文）」，「中文转账通知」只是它的解析来源名，直接搜搜不到）。
    public static func matchesSearch(key: String, feature: JSONValue?, query: String) -> Bool {
        let text = query.trimmingCharacters(in: .whitespaces).lowercased()
        guard !text.isEmpty, isTransferPluginKey(key) || declaresPaymentSource(feature) else { return false }
        let words = ["转账", "收款", "付款", "扣款", "扣减", "到账", "transfer", "payment", "中文转账通知", "转账通知判定"]
        return words.contains { text.contains($0) || ($0.contains(text) && text.count >= 2) }
    }

    /// - Parameters:
    ///   - status: 读到的状态（失败时 nil）。
    ///   - notFound: 读取返回 404（旧服务器没有这个接口）。
    ///   - failure: 其它读取失败的原因（大白话）。
    ///   - transferPlugin: 这个插件按 key 或插件信息判断是转账通知插件。
    public static func availability(status: JSONValue?, notFound: Bool, failure: String?, pluginKey: String,
                                    transferPlugin: Bool) -> Availability {
        if let status, let view = build(status, pluginKey: pluginKey) { return .available(view) }
        guard transferPlugin else { return .hidden }
        if notFound { return .serverTooOld }
        if let status, status.objectValue != nil { return .notParticipating(global: build(status, pluginKey: nil)) }
        if let failure, !failure.isEmpty { return .unreadable(failure) }
        return .hidden
    }
}
