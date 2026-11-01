import Foundation

/// 插件市场的一个版本通道（服务器 0.117.0+ 的 `available_channels` 条目）。
public struct PluginMarketChannel: Equatable, Hashable, Sendable, Identifiable {
    public var name: String
    public var ref: String?
    public var version: String?
    public var id: String { name }

    public init(name: String, ref: String? = nil, version: String? = nil) {
        self.name = name
        self.ref = ref
        self.version = version
    }
}

/// 插件市场目录条目里“像应用商店”的那部分内容（服务器 0.117.0 起才有）。
///
/// 全部字段可选：旧服务器（0.113.0–0.116.x）的目录没有这些字段，解析结果就是空的，界面对应区块不显示。
/// 链接只接受 https（与服务器 `parse_registry` 的过滤一致），防止索引里写 `javascript:` 之类的地址。
public struct PluginMarketDetails: Equatable, Sendable {
    public var description: String?
    public var usage: String?
    public var commands: [String]
    public var releaseNotes: String?
    public var releasedAt: String?
    public var homepage: URL?
    public var license: String?
    public var iconURL: URL?
    public var screenshots: [URL]
    public var summaryEnglish: String?
    /// 已安装插件当前所在通道；未安装或来自别的仓库时为 nil。
    public var channel: String?
    public var channels: [PluginMarketChannel]
    /// 所在通道有新版本时的目标版本（旧服务器没有，用 `version` 兜底）。
    public var updateVersion: String?

    public init(_ item: JSONValue) {
        description = Self.text(item["description"])
        usage = Self.text(item["usage"])
        commands = Self.strings(item["commands"])
        releaseNotes = Self.text(item["release_notes"])
        releasedAt = Self.text(item["released_at"])
        homepage = Self.httpsURL(item["homepage"]?.stringValue)
        license = Self.text(item["license"])
        iconURL = Self.httpsURL(item["icon_url"]?.stringValue)
        screenshots = (item["screenshots"]?.arrayValue ?? []).compactMap { Self.httpsURL($0.stringValue) }
        summaryEnglish = Self.text(item["summary_i18n"]?["en"])
        channel = Self.text(item["channel"])
        channels = (item["available_channels"]?.arrayValue ?? []).compactMap { value in
            guard let name = Self.text(value["name"]) else { return nil }
            return PluginMarketChannel(name: name, ref: Self.text(value["ref"]), version: Self.text(value["version"]))
        }
        updateVersion = Self.text(item["update_version"])
    }

    /// 插件真有第二个通道时才显示通道界面（官方插件库目前只有稳定通道；旧服务器没有这个字段）。
    public var hasMultipleChannels: Bool { channels.count > 1 }

    public func channel(named name: String) -> PluginMarketChannel? {
        channels.first { $0.name == name }
    }

    /// 详情页“怎么用”区块是否有内容。
    public var hasUsagePreview: Bool { usage != nil || !commands.isEmpty }

    static func text(_ value: JSONValue?) -> String? {
        guard let raw = value?.stringValue else { return nil }
        let trimmed = raw.trimmingCharacters(in: .whitespacesAndNewlines)
        return trimmed.isEmpty ? nil : trimmed
    }

    static func strings(_ value: JSONValue?) -> [String] {
        (value?.arrayValue ?? []).compactMap { text($0) }
    }

    public static func httpsURL(_ raw: String?) -> URL? {
        guard let raw = raw?.trimmingCharacters(in: .whitespacesAndNewlines), !raw.isEmpty,
              let url = URL(string: raw), url.scheme?.lowercased() == "https", url.host?.isEmpty == false
        else { return nil }
        return url
    }
}

/// 插件市场的文案与纯规则（与 Web `frontend/src/lib/pluginMarket.ts` 对齐）。
public enum PluginMarketInfo {
    /// 分类标签的固定顺序；未知类型排在最后（按名称）。
    public static let kindOrder = ["interactive", "automation", "utility", "middleware", "payment_source", "ui"]

    public struct KindCount: Equatable, Sendable, Identifiable {
        public var kind: String
        public var count: Int
        public var id: String { kind }
        public init(kind: String, count: Int) {
            self.kind = kind
            self.count = count
        }
    }

    /// 每个类型的插件数（分类标签上的数字）。
    public static func kindCounts(_ kinds: [String]) -> [KindCount] {
        var counts: [String: Int] = [:]
        for kind in kinds { counts[kind, default: 0] += 1 }
        func rank(_ kind: String) -> Int { kindOrder.firstIndex(of: kind) ?? kindOrder.count }
        return counts.map { KindCount(kind: $0.key, count: $0.value) }.sorted { lhs, rhs in
            let left = rank(lhs.kind)
            let right = rank(rhs.kind)
            return left != right ? left < right : lhs.kind < rhs.kind
        }
    }

    public static func trustDescription(_ trust: String?) -> String {
        switch trust {
        case "official": "TelePilot 官方插件库发布，随服务器版本做过兼容检查。"
        case "community": "来自你添加的社区来源，由来源维护者负责，官方没有审核。"
        default: "没有人审核过这个插件的代码。插件在服务器里运行、能用它声明的权限，安装前请确认来源可信。"
        }
    }

    /// 安装“未审核”插件前的二次确认说明。
    public static let unreviewedInstallWarning =
        "没有人审核过这个插件的代码。它会在你的服务器里运行，并能使用它声明的权限（如发消息、访问外部网站）。只在你信任来源时安装。"

    public static func needsInstallConfirmation(trust: String?) -> Bool {
        trust != "official" && trust != "community"
    }

    public static func channelLabel(_ name: String?) -> String {
        switch name {
        case nil: ""
        case "stable"?: "稳定版"
        case "beta"?: "测试版"
        case let other?: other
        }
    }

    /// 插件权限的大白话说明；未知权限返回 nil（界面原样显示权限名）。
    public static func permissionDescription(_ permission: String) -> String? {
        switch permission {
        case "send_message": "以你的账号或 Bot 发消息"
        case "read_chat": "读取它被启用的群里的消息"
        case "edit_message": "编辑它自己发出的消息"
        case "delete_message": "删除消息（如自动清理指令和提示）"
        case "payout": "按游戏规则派发群内游戏币（如魔力值）"
        case "send_file": "发送图片或文件"
        case "external_http": "访问外部网站（只限它声明的域名）"
        case "resolve_entity": "查询用户或群的名字与 ID"
        case "ai_text", "ai": "调用你配置的 AI 模型（会消耗 AI 额度）"
        case "ai_vision": "调用 AI 识别图片（会消耗 AI 额度）"
        case "moderate_chat": "群管理操作（禁言、踢人等）"
        case "agent_skills": "读取 Agent Skills"
        case "click_bot_button": "替你点击其它 Bot 消息上的按钮"
        default: nil
        }
    }

    /// 使用说明里的 `{prefix}` 换成指令前缀（默认 `,`）。
    public static func renderUsage(_ text: String?, prefix: String = ",") -> String {
        (text ?? "").replacingOccurrences(of: "{prefix}", with: prefix)
    }

    /// 切换通道是否会降到更低的版本：先比纯数字段（缺位补 0），相同时正式版高于预发布版。看不出来时返回 false。
    public static func isChannelDowngrade(installed: String?, target: String?) -> Bool {
        guard let installed, let target, !installed.isEmpty, !target.isEmpty else { return false }
        func parse(_ value: String) -> (numbers: [Int], pre: String?) {
            let parts = value.split(separator: "-", maxSplits: 1, omittingEmptySubsequences: false)
            let numbers = parts[0].split(separator: ".", omittingEmptySubsequences: false).map { Int($0) ?? 0 }
            return (numbers, parts.count > 1 ? String(parts[1]) : nil)
        }
        let current = parse(installed)
        let next = parse(target)
        for index in 0..<max(current.numbers.count, next.numbers.count) {
            let left = index < current.numbers.count ? current.numbers[index] : 0
            let right = index < next.numbers.count ? next.numbers[index] : 0
            if left != right { return right < left }
        }
        if current.pre == next.pre { return false }
        guard let currentPre = current.pre else { return true }  // 1.0.0 → 1.0.0-beta.1 是降级
        guard let nextPre = next.pre else { return false }
        return nextPre < currentPre
    }

    /// 搜索匹配的文本：名称、简介、完整简介、作者、标签与指令。
    public static func searchFields(_ item: JSONValue) -> [String] {
        var fields: [String] = []
        for value in [item["key"], item["name"]?["zh"], item["name"]?["en"], item["summary"], item["description"], item["author"]] {
            if let text = value?.stringValue, !text.isEmpty { fields.append(text) }
        }
        fields += (item["tags"]?.arrayValue ?? []).compactMap(\.stringValue)
        fields += (item["commands"]?.arrayValue ?? []).compactMap(\.stringValue)
        return fields
    }

    public static func matches(_ item: JSONValue, query: String) -> Bool {
        let needle = query.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !needle.isEmpty else { return true }
        return searchFields(item).contains { $0.localizedCaseInsensitiveContains(needle) }
    }

    /// 安装请求体：只在选了非稳定通道时才带 `channel`（旧服务器不认识这个字段，只装稳定版）。
    public static func installBody(sourceID: JSONValue, key: String, channel: String?) -> JSONValue {
        var body: [String: JSONValue] = ["source_id": sourceID, "key": .string(key)]
        if let channel, channel != "stable" { body["channel"] = .string(channel) }
        return .object(body)
    }

    /// 切换通道请求体（`POST /api/plugin-market/channel`）。
    public static func channelBody(key: String, channel: String, sourceID: String?) -> JSONValue {
        var body: [String: JSONValue] = ["key": .string(key), "channel": .string(channel)]
        if let sourceID { body["source_id"] = .string(sourceID) }
        return .object(body)
    }
}
