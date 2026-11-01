import SwiftUI
import TelePilotCore

// MARK: - 服务器参数（第 19 轮 iOS 0.20.0 原生表单；第 21 轮重构）
//
// 第 21 轮：
// - 每个参数都是一眼能看出可改的「带标题的输入行」：标题（中文名）+ 英文原名 + 一句大白话说明，
//   数字是带边框的输入框 + 单位 + 步进器，开关、选择器原生；改过的标「已修改」，和默认值不同时可「恢复默认」。
// - 中文名、说明、单位、范围、默认值来自服务器 `GET /api/system/settings/meta`（单一来源，Web 同源）；
//   旧服务器没有该接口时，常用参数用 App 内置的说明（`ServerParamCatalog.builtin`），其余参数按原键名显示。
// - 已在专门页面可改的参数（AI 全局设置、软件更新、Sudo、验证器、紧急停止等）不在常用页里重复，
//   集中在「在别处修改」一组，只读显示当前值并跳到对应页面；「全部参数」页保留它们，标注「也在 xx」。
// 读写接口不变：`GET`／`PATCH /api/system/settings`，服务器按“未传字段保持不变”局部合并（嵌套对象也只合并传入的子键），
// 所以保存时只提交改过的键；写操作只提交一次、不自动重试，结果未知时提示下拉核对。

/// 参数「也在别处可改」的位置（服务器元数据 `also_in`）。
struct ServerParamLocation: Hashable {
    let label: String
    let webRoute: String?
    let iosTarget: String?
    let iosLabel: String?
}

/// 一个服务器参数的说明（服务器元数据或 App 内置回退）。`key` 用点号表示嵌套，例如 `log_retention.trace_enabled`。
struct ServerParamMeta: Identifiable, Hashable {
    enum Kind: String { case bool, integer, string, enumeration = "enum", timezone, list, unknown }
    struct Option: Hashable { let value: String; let label: String }

    let key: String
    var label: String
    var detail: String
    var kind: Kind
    var group: String
    var defaultValue: JSONValue = .null
    var unit: String? = nil
    var min: Int? = nil
    var max: Int? = nil
    var maxLength: Int? = nil
    var options: [Option] = []
    var editable = true
    var readOnlyReason: String? = nil
    var alsoIn: [ServerParamLocation] = []
    var advanced = false
    var zeroMeans: String? = nil

    var id: String { key }
    var topKey: String { String(key.split(separator: ".", maxSplits: 1).first ?? Substring(key)) }
    var subKey: String? { key.contains(".") ? String(key.split(separator: ".", maxSplits: 1)[1]) : nil }
    /// 英文原名（最后一段键名），显示在中文名旁边。
    var originalName: String { subKey ?? key }
    /// 界面标识：`log_retention` 里的参数沿用第 19 轮的短名（如 `trace_enabled`），其它嵌套参数用完整键。
    var identifier: String { topKey == ServerParamCatalog.logGroup ? (subKey ?? key) : key }
    /// iOS 上有专门页面的位置（有就不在常用页重复显示）。
    var iosLocation: ServerParamLocation? { alsoIn.first { $0.iosTarget != nil } }
    var isEditableHere: Bool { editable && kind != .list && kind != .unknown }

    init(key: String, label: String, detail: String, kind: Kind, group: String, defaultValue: JSONValue = .null,
         unit: String? = nil, min: Int? = nil, max: Int? = nil, maxLength: Int? = nil, options: [Option] = [],
         editable: Bool = true, readOnlyReason: String? = nil, alsoIn: [ServerParamLocation] = [],
         advanced: Bool = false, zeroMeans: String? = nil) {
        self.key = key; self.label = label; self.detail = detail; self.kind = kind; self.group = group
        self.defaultValue = defaultValue; self.unit = unit; self.min = min; self.max = max; self.maxLength = maxLength
        self.options = options; self.editable = editable; self.readOnlyReason = readOnlyReason; self.alsoIn = alsoIn
        self.advanced = advanced; self.zeroMeans = zeroMeans
    }

    init?(json: JSONValue) {
        guard let key = json["key"]?.stringValue, !key.isEmpty else { return nil }
        self.key = key
        label = json["label"]?.stringValue ?? key
        detail = json["description"]?.stringValue ?? ""
        kind = Kind(rawValue: json["type"]?.stringValue ?? "") ?? .unknown
        group = json["group"]?.stringValue ?? "other"
        defaultValue = json["default"] ?? .null
        unit = json["unit"]?.stringValue
        min = json["min"]?.intValue
        max = json["max"]?.intValue
        maxLength = json["max_length"]?.intValue
        options = (json["options"]?.arrayValue ?? []).compactMap { item in
            guard let value = item["value"]?.stringValue else { return nil }
            return Option(value: value, label: item["label"]?.stringValue ?? value)
        }
        editable = json["editable"]?.boolValue ?? true
        readOnlyReason = json["read_only_reason"]?.stringValue
        // 第 23 轮：转账通知判定开关在 App「插件 › 转账通知判定」里可拨（带确认）；服务器元数据的只读原因仍写着
        // “App 里只显示状态”（为 0.35.0 之前的 App 写的），这里换成指向那个页面的说明。全部参数页仍不直接改，避免绕过确认。
        if key == PaymentSourceSwitchView.settingKey { readOnlyReason = PaymentSourceSwitchView.serverSettingsHint }
        alsoIn = (json["also_in"]?.arrayValue ?? []).compactMap { item in
            guard let label = item["label"]?.stringValue else { return nil }
            return ServerParamLocation(label: label, webRoute: item["web_route"]?.stringValue,
                                       iosTarget: item["ios_target"]?.stringValue, iosLabel: item["ios_label"]?.stringValue)
        }
        advanced = json["advanced"]?.boolValue ?? false
        zeroMeans = json["zero_means"]?.stringValue
    }
}

struct ServerParamGroup: Identifiable, Hashable {
    let id: String
    let label: String
    let detail: String?
}

/// 一份参数说明：来自服务器（`fromServer`）或 App 内置回退。
struct ServerParamsMeta {
    var groups: [ServerParamGroup]
    var params: [ServerParamMeta]
    var fromServer: Bool

    func param(_ key: String) -> ServerParamMeta? { params.first { $0.key == key } }

    static func parse(_ json: JSONValue) -> ServerParamsMeta? {
        let params = (json["params"]?.arrayValue ?? []).compactMap(ServerParamMeta.init(json:))
        guard !params.isEmpty else { return nil }
        let groups = (json["groups"]?.arrayValue ?? []).compactMap { item -> ServerParamGroup? in
            guard let id = item["id"]?.stringValue else { return nil }
            return ServerParamGroup(id: id, label: item["label"]?.stringValue ?? id, detail: item["description"]?.stringValue)
        }
        return ServerParamsMeta(groups: groups, params: params, fromServer: true)
    }
}

enum ServerParamCatalog {
    static let logGroup = "log_retention"

    struct CommonSection: Identifiable {
        let title: String
        let footer: String?
        let keys: [String]
        var id: String { title }
    }

    /// 常用页的分组与顺序（键名固定；说明取服务器元数据，旧服务器用 `builtin`）。
    static let commonSections: [CommonSection] = [
        CommonSection(title: "指令", footer: "修改后各账号的 worker 自动热加载，不需要重启。",
                      keys: ["command_prefix", "command_prefix_required", "ai_context_messages", "command_echo_guard_previous_messages"]),
        CommonSection(title: "时区", footer: nil, keys: ["timezone"]),
        CommonSection(title: "运行日志", footer: "日志等级保存后立即影响新日志落库。",
                      keys: ["log_retention.runtime_log_min_level", "log_retention.runtime_log_retention_days",
                             "log_retention.runtime_log_max_message_chars", "log_retention.runtime_log_max_detail_chars"]),
        CommonSection(title: "调用链与事件投递", footer: "调用链（Trace）记录每条消息在系统内部怎么走；事件投递（Event Bus）把消息事件交给订阅它的插件。",
                      keys: ["log_retention.trace_enabled", "log_retention.event_bus_delivery_enabled",
                             "log_retention.inline_updates_enabled", "log_retention.guest_updates_enabled",
                             "log_retention.trace_retention_days", "log_retention.trace_payload_snapshot_retention_days",
                             "log_retention.native_raw_persist_enabled", "log_retention.native_raw_retention_days",
                             "log_retention.payment_source_plugin_authoritative"]),
        CommonSection(title: "其它保留期限", footer: nil,
                      keys: ["log_retention.llm_usage_retention_days", "log_retention.rate_limit_event_retention_days"]),
        CommonSection(title: "插件更新检查", footer: "iOS 没有单独的插件更新设置页，在这里改；Web 在「插件 › 已安装」。",
                      keys: ["remote_plugin_update_check.enabled", "remote_plugin_update_check.interval_minutes"])
    ]

    static let symbols: [String: String] = [
        "command_prefix": "terminal", "command_prefix_required": "textformat", "ai_context_messages": "text.bubble",
        "command_echo_guard_previous_messages": "arrow.uturn.left.circle", "timezone": "globe.asia.australia",
        "log_retention.runtime_log_min_level": "line.3.horizontal.decrease.circle",
        "log_retention.runtime_log_retention_days": "calendar.badge.clock",
        "log_retention.runtime_log_max_message_chars": "text.alignleft",
        "log_retention.runtime_log_max_detail_chars": "curlybraces",
        "log_retention.trace_enabled": "point.topleft.down.to.point.bottomright.curvepath",
        "log_retention.event_bus_delivery_enabled": "arrow.triangle.branch",
        "log_retention.inline_updates_enabled": "rectangle.and.text.magnifyingglass",
        "log_retention.guest_updates_enabled": "person.crop.circle.badge.questionmark",
        "log_retention.trace_retention_days": "calendar", "log_retention.trace_payload_snapshot_retention_days": "doc.text.magnifyingglass",
        "log_retention.native_raw_persist_enabled": "externaldrive", "log_retention.native_raw_retention_days": "calendar",
        "log_retention.payment_source_plugin_authoritative": "lock",
        "log_retention.llm_usage_retention_days": "chart.bar", "log_retention.rate_limit_event_retention_days": "gauge.with.dots.needle.33percent",
        "remote_plugin_update_check.enabled": "arrow.triangle.2.circlepath", "remote_plugin_update_check.interval_minutes": "timer"
    ]

    private static let aiGlobal = ServerParamLocation(label: "服务器设置 › 平台与模块", webRoute: "/settings?tab=platform",
                                                      iosTarget: "ai_global_settings", iosLabel: "AI › AI 全局设置")
    private static let platform = ServerParamLocation(label: "服务器设置 › 平台与模块", webRoute: "/settings?tab=platform",
                                                      iosTarget: nil, iosLabel: nil)

    private static func days(_ key: String, _ label: String, _ detail: String, group: String, fallback: Int, max: Int = 3650,
                             alsoIn: [ServerParamLocation] = [platform]) -> ServerParamMeta {
        ServerParamMeta(key: key, label: label, detail: detail, kind: .integer, group: group, defaultValue: .integer(Int64(fallback)),
                        unit: "天", min: 0, max: max, alsoIn: alsoIn, zeroMeans: max >= 3650 ? "不自动删除" : nil)
    }

    /// 旧服务器（没有元数据接口）时常用页的说明，与服务器 `system_settings_meta.py` 同文。
    static let builtin: ServerParamsMeta = ServerParamsMeta(groups: [], params: [
        ServerParamMeta(key: "command_prefix", label: "指令前缀", detail: "在 Telegram 里发指令时开头的符号，比如“,ai”里的“,”。最多 3 个字符。",
                        kind: .string, group: "command", defaultValue: ",", maxLength: 3, alsoIn: [platform]),
        ServerParamMeta(key: "command_prefix_required", label: "账号本人必须带前缀",
                        detail: "关掉后，只有你这个账号本人发的消息可以不带前缀直接用指令名触发；群里其他人仍然不能用裸指令触发。",
                        kind: .bool, group: "command", defaultValue: true, alsoIn: [platform]),
        ServerParamMeta(key: "ai_context_messages", label: "AI 指令上下文条数", detail: "用 AI 指令时顺带把最近几条聊天记录发给模型，让它看懂前因后果。0 = 不带。",
                        kind: .integer, group: "command", defaultValue: 0, unit: "条", min: 0, max: 20, alsoIn: [aiGlobal], zeroMeans: "不带上下文"),
        ServerParamMeta(key: "command_echo_guard_previous_messages", label: "命令回声防误触",
                        detail: "群里有人原样复读一条指令时，往前看这么多条消息，避免被复读误触发。0 = 关闭。",
                        kind: .integer, group: "command", defaultValue: 8, unit: "条", min: 0, max: 50, zeroMeans: "关闭"),
        ServerParamMeta(key: "timezone", label: "服务器时区", detail: "IANA 时区名，例如 Asia/Shanghai。定时任务的下次 / 上次触发时间都按它算。",
                        kind: .timezone, group: "time", defaultValue: "Asia/Shanghai", alsoIn: [platform]),
        ServerParamMeta(key: "log_retention.runtime_log_min_level", label: "运行日志等级",
                        detail: "比这个等级低的日志不保存。排查问题时可以临时调到“调试”，平时用“信息”。",
                        kind: .enumeration, group: "logs", defaultValue: "info",
                        options: [.init(value: "debug", label: "调试（最详细）"), .init(value: "info", label: "信息（默认）"),
                                  .init(value: "warn", label: "警告（只留告警和错误）"), .init(value: "error", label: "错误（只留错误）")],
                        alsoIn: [platform]),
        days("log_retention.runtime_log_retention_days", "运行日志保留天数", "运行日志保存多少天后自动删除。", group: "logs", fallback: 30),
        ServerParamMeta(key: "log_retention.runtime_log_max_message_chars", label: "日志正文最多字符", detail: "一条日志的正文太长时，超出部分截掉再保存。",
                        kind: .integer, group: "logs", defaultValue: 2000, unit: "字", min: 200, max: 20000, alsoIn: [platform]),
        ServerParamMeta(key: "log_retention.runtime_log_max_detail_chars", label: "日志附加详情最多字符",
                        detail: "日志附带的结构化详情（排查用的额外字段）最多保存多少字。0 = 不保存详情。",
                        kind: .integer, group: "logs", defaultValue: 8000, unit: "字", min: 0, max: 50000, alsoIn: [platform], zeroMeans: "不保存详情"),
        ServerParamMeta(key: "log_retention.trace_enabled", label: "记录调用链（Trace）",
                        detail: "记录每条消息在系统里经过了哪些步骤，方便排查。只有调用链存储出问题时才需要关。",
                        kind: .bool, group: "trace", defaultValue: true, alsoIn: [platform], advanced: true),
        ServerParamMeta(key: "log_retention.event_bus_delivery_enabled", label: "事件投递给插件（Event Bus）",
                        detail: "把收到的消息事件交给订阅它的插件处理。关掉会退回旧的交互规则链路，只在回滚观察时用。",
                        kind: .bool, group: "trace", defaultValue: true, alsoIn: [platform], advanced: true),
        ServerParamMeta(key: "log_retention.inline_updates_enabled", label: "接收内联查询（Inline）",
                        detail: "接收别人在输入框里 @Bot 发起的内联查询和选中结果。关掉后 Bot 不再响应内联查询。",
                        kind: .bool, group: "trace", defaultValue: true, alsoIn: [platform]),
        ServerParamMeta(key: "log_retention.guest_updates_enabled", label: "接收访客消息（Guest Mode）",
                        detail: "Bot 没进的群里有人 @它时也能收到并回复。关掉后不再接收这类消息。",
                        kind: .bool, group: "trace", defaultValue: true, alsoIn: [platform]),
        days("log_retention.trace_retention_days", "调用链保留天数", "调用链记录保存多少天后自动删除。", group: "trace", fallback: 30),
        days("log_retention.trace_payload_snapshot_retention_days", "消息内容快照保留天数",
             "调用链里附带的消息原文快照保存多少天；到期只清快照，调用链本身还在。", group: "trace", fallback: 7),
        ServerParamMeta(key: "log_retention.native_raw_persist_enabled", label: "保存 Telegram 原始数据（native_raw）",
                        detail: "把 Telegram 推来的原始更新完整存下来，用于深度排查，会占更多空间。平时保持关闭。",
                        kind: .bool, group: "trace", defaultValue: false, alsoIn: [platform], advanced: true),
        days("log_retention.native_raw_retention_days", "原始数据保留天数", "Telegram 原始数据保存多少天。", group: "trace", fallback: 1, max: 30),
        ServerParamMeta(key: "log_retention.payment_source_plugin_authoritative", label: "转账通知以插件结果为准",
                        detail: "关 = 以系统自带的旧解析为准，转账通知插件只做对照；开 = 以转账通知插件的结果为准，插件出错或超时自动回退旧解析。随时可关，30 秒内生效，不影响结算规则本身。",
                        kind: .bool, group: "trace", defaultValue: false, editable: false,
                        readOnlyReason: PaymentSourceSwitchView.serverSettingsHint,
                        alsoIn: [platform], advanced: true),
        days("log_retention.llm_usage_retention_days", "AI 用量记录保留天数", "每次 AI 调用的用量记录保存多少天。", group: "retention",
             fallback: 30, alsoIn: [aiGlobal]),
        days("log_retention.rate_limit_event_retention_days", "风控事件保留天数", "限流 / 风控拦截记录保存多少天。", group: "retention", fallback: 30),
        ServerParamMeta(key: "remote_plugin_update_check.enabled", label: "自动检查插件更新", detail: "定期去插件仓库看看已安装的插件有没有新版本。",
                        kind: .bool, group: "updates", defaultValue: true,
                        alsoIn: [ServerParamLocation(label: "插件 › 已安装", webRoute: "/plugins/manage", iosTarget: nil, iosLabel: nil)]),
        ServerParamMeta(key: "remote_plugin_update_check.interval_minutes", label: "插件更新检查间隔", detail: "多久检查一次插件更新。",
                        kind: .integer, group: "updates", defaultValue: 360, unit: "分钟", min: 30, max: 10080,
                        alsoIn: [ServerParamLocation(label: "插件 › 已安装", webRoute: "/plugins/manage", iosTarget: nil, iosLabel: nil)])
    ], fromServer: false)

    /// 某个键的说明：优先服务器元数据，其次内置。
    static func meta(_ key: String, in meta: ServerParamsMeta?) -> ServerParamMeta? {
        meta?.param(key) ?? builtin.param(key)
    }

    /// 从服务器返回值取出某个键的当前值（嵌套键按点号取）。缺失时返回默认值。
    static func value(_ param: ServerParamMeta, in settings: JSONValue) -> JSONValue {
        let raw: JSONValue? = param.subKey.map { settings[param.topKey]?[$0] } ?? settings[param.key]
        if let raw, !raw.isNull { return raw }
        return param.defaultValue
    }

    static func values(for params: [ServerParamMeta], from settings: JSONValue) -> [String: JSONValue] {
        var result: [String: JSONValue] = [:]
        for param in params { result[param.key] = value(param, in: settings) }
        return result
    }

    /// 只含改动键的 PATCH 请求体；嵌套对象只带改动的子键（服务器局部合并）。只读参数从不提交。
    static func patch(params: [ServerParamMeta], baseline: [String: JSONValue], draft: [String: JSONValue]) -> JSONValue? {
        var body: [String: JSONValue] = [:]
        var nested: [String: [String: JSONValue]] = [:]
        for param in params where param.isEditableHere {
            guard let value = draft[param.key], value != baseline[param.key] else { continue }
            var sent = value
            if param.kind == .string || param.kind == .timezone, let text = value.stringValue {
                sent = .string(text.trimmingCharacters(in: .whitespacesAndNewlines))
            }
            if let sub = param.subKey { nested[param.topKey, default: [:]][sub] = sent } else { body[param.key] = sent }
        }
        for (group, values) in nested { body[group] = .object(values) }
        return body.isEmpty ? nil : .object(body)
    }

    /// 提交前的本地校验（只校验改过的项，与服务器规则一致）；返回第一条错误。
    static func validate(params: [ServerParamMeta], baseline: [String: JSONValue], draft: [String: JSONValue]) -> String? {
        for param in params where param.isEditableHere {
            guard let value = draft[param.key], value != baseline[param.key] else { continue }
            switch param.kind {
            case .string:
                let text = value.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                if text.isEmpty { return "\(param.label)不能为空" }
                if let maxLength = param.maxLength, text.count > maxLength { return "\(param.label)最多 \(maxLength) 个字符" }
            case .integer:
                guard let number = value.intValue else { return "\(param.label)请填写数字" }
                if let min = param.min, number < min { return rangeMessage(param) }
                if let max = param.max, number > max { return rangeMessage(param) }
            case .timezone:
                let text = value.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines) ?? ""
                if !text.isEmpty, TimeZone(identifier: text) == nil { return "无效时区：\(text)" }
            case .bool, .enumeration, .list, .unknown:
                break
            }
        }
        return nil
    }

    static func rangeMessage(_ param: ServerParamMeta) -> String {
        switch (param.min, param.max) {
        case let (min?, max?): return "\(param.label)必须在 \(min)～\(max) 之间"
        case let (min?, nil): return "\(param.label)不能小于 \(min)"
        case let (nil, max?): return "\(param.label)不能大于 \(max)"
        default: return "\(param.label)超出范围"
        }
    }

    /// 只读显示的值：开关“开启／关闭”，数字带单位，选项显示中文，列表显示项数。
    static func display(_ value: JSONValue, _ param: ServerParamMeta?) -> String {
        switch value {
        case .bool(let flag): return flag ? "开启" : "关闭"
        case .array(let items):
            if items.isEmpty { return "（空 = 默认）" }
            return items.compactMap { OpsFormat.text($0) }.joined(separator: "、")
        case .object(let object): return object.isEmpty ? "（空）" : "\(object.count) 项设置"
        case .null: return "未设置"
        default:
            let text = OpsFormat.text(value) ?? "（空）"
            if let param, let option = param.options.first(where: { $0.value == text }) { return option.label }
            if let param, let number = value.intValue {
                if number == 0, let zero = param.zeroMeans { return "0（\(zero)）" }
                if let unit = param.unit { return "\(number) \(unit)" }
            }
            return text
        }
    }

    /// 旧服务器、没有元数据时，把返回值拍平成「键 → 值」（嵌套键用点号），按原键名显示。
    static func flatten(_ value: JSONValue, prefix: String = "") -> [(key: String, value: JSONValue)] {
        guard let object = value.objectValue else { return [] }
        var result: [(key: String, value: JSONValue)] = []
        for (key, item) in object.sorted(by: { $0.key < $1.key }) {
            let path = prefix + key
            if let nestedObject = item.objectValue, !nestedObject.isEmpty, prefix.isEmpty {
                result.append(contentsOf: flatten(item, prefix: path + "."))
            } else {
                result.append((key: path, value: item))
            }
        }
        return result
    }
}

/// 服务器参数页：`common` 是常用参数（「更多 › 服务器设置 › 服务器参数」），`all` 是全部参数（原「原始编辑全部参数」）。
struct ServerSettingsView: View {
    enum Mode { case common, all }

    var mode: Mode = .common
    @Environment(AppModel.self) private var model
    @State private var settings: JSONValue?
    @State private var meta: ServerParamsMeta?
    @State private var baseline: [String: JSONValue] = [:]
    @State private var draft: [String: JSONValue] = [:]
    @State private var loading = false
    @State private var loadError: String?
    @State private var saving = false
    @State private var saveMessage: String?
    @State private var saveError: String?
    @State private var uncertain = false
    @State private var confirmingDiscard = false

    private var dirty: Bool { settings != nil && draft != baseline }
    private var title: String { mode == .common ? "服务器参数" : "全部服务器参数" }

    /// 本页可编辑的参数（决定读取、校验、提交哪些键）。
    private var params: [ServerParamMeta] {
        switch mode {
        case .common:
            return commonSections.flatMap(\.params)
        case .all:
            if let meta, meta.fromServer { return meta.params }
            return ServerParamCatalog.builtin.params
        }
    }

    /// 常用页的分组：已在 iOS 专门页面可改的参数不在这里重复（集中到「在别处修改」）。
    private struct CommonGroup: Identifiable {
        let section: ServerParamCatalog.CommonSection
        let params: [ServerParamMeta]
        var id: String { section.id }
    }

    private var commonSections: [CommonGroup] {
        ServerParamCatalog.commonSections.map { section in
            CommonGroup(section: section,
                        params: section.keys.compactMap { ServerParamCatalog.meta($0, in: meta) }.filter { $0.iosLocation == nil })
        }.filter { !$0.params.isEmpty }
    }

    var body: some View {
        List {
            Section {
                Text(mode == .common
                     ? "常用的服务器参数。每一行都可以直接改：点输入框输入、拨开关或点选项；改过的行标「已修改」，点右上角「保存」只提交改过的项。"
                     : "服务器上的全部参数，每个都写了中文名、英文原名和说明。在别的页面也能改的，标了「也在」哪里；改完点右上角「保存」。")
                    .font(.subheadline).foregroundStyle(.secondary)
                if let meta, !meta.fromServer, settings != nil {
                    Label("这个服务器版本还没有参数说明，部分参数按原键名显示；升级服务器后会显示中文名。", systemImage: "info.circle")
                        .font(.footnote).foregroundStyle(.secondary)
                        .accessibilityIdentifier("serverSettings.metaFallback")
                }
            }
            if let settings {
                if mode == .common { commonContent(settings) } else { allContent(settings) }
            } else if let loadError {
                Section {
                    NoticeView(title: "服务器参数读取失败", message: loadError)
                    Button("重试", systemImage: "arrow.clockwise") { Task { await load() } }
                }
            } else {
                Section { ProgressView("正在读取服务器参数…") }
            }
        }
        .symbolRenderingMode(.hierarchical)
        .scrollDismissesKeyboard(.immediately)
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier(mode == .common ? "serverSettings.screen" : "serverSettings.all.screen")
        .toolbar {
            ToolbarItem(placement: .confirmationAction) {
                if saving {
                    ProgressView()
                } else {
                    Button("保存") { Task { await save() } }
                        .disabled(!dirty || uncertain)
                        .accessibilityIdentifier("serverSettings.save")
                }
            }
        }
        // 保存结果与未保存提示固定在底部（随键盘上移），不论滚到哪里、键盘是否弹出都看得到。
        .safeAreaInset(edge: .bottom, spacing: 0) { statusBar }
        .animation(.default, value: saveMessage)
        .animation(.default, value: saveError)
        .animation(.default, value: dirty)
        .refreshable { if !dirty { await load() } }
        .task { if settings == nil { await load() } }
    }

    // MARK: 常用页

    @ViewBuilder private func commonContent(_ settings: JSONValue) -> some View {
        ForEach(commonSections) { entry in
            Section {
                ForEach(entry.params) { param in
                    row(param, settings: settings)
                }
            } header: {
                Text(entry.section.title)
            } footer: {
                if let footer = entry.section.footer { Text(footer) }
            }
        }
        elsewhereSection(settings)
        Section {
            NavigationLink { ServerSettingsView(mode: .all) } label: {
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text("全部参数（原始编辑）")
                        Text("每个参数的中文名、英文原名和说明，包括在别的页面也能改的").font(.caption).foregroundStyle(.secondary)
                    }
                } icon: { Image(systemName: "slider.horizontal.3") }
            }
            .accessibilityIdentifier("serverSettings.raw")
        } header: {
            Text("其它参数")
        }
    }

    /// 已在 iOS 专门页面可改的参数：按页面归组，只读显示当前值，点按去那个页面改。
    @ViewBuilder private func elsewhereSection(_ settings: JSONValue) -> some View {
        let source = meta?.fromServer == true ? (meta?.params ?? []) : ServerParamCatalog.builtin.params
        let grouped = Dictionary(grouping: source.filter { $0.iosLocation != nil }) { $0.iosLocation?.iosTarget ?? "" }
        let targets = grouped.keys.sorted { lhs, rhs in
            (source.firstIndex { $0.iosLocation?.iosTarget == lhs } ?? 0) < (source.firstIndex { $0.iosLocation?.iosTarget == rhs } ?? 0)
        }
        if !targets.isEmpty {
            Section {
                ForEach(targets, id: \.self) { target in
                    let items = grouped[target] ?? []
                    ServerParamElsewhereRow(target: target, label: items.first?.iosLocation?.iosLabel ?? target,
                                            items: items.map { ($0.label, ServerParamCatalog.display(ServerParamCatalog.value($0, in: settings), $0)) })
                }
            } header: {
                Text("在别处修改")
            } footer: {
                Text("这些参数有专门的页面，这里只显示当前值，避免两处改法不一致；「全部参数」里仍可直接改。")
            }
        }
    }

    // MARK: 全部参数页

    @ViewBuilder private func allContent(_ settings: JSONValue) -> some View {
        if let meta, meta.fromServer {
            ForEach(meta.groups) { group in
                let items = meta.params.filter { $0.group == group.id }
                if !items.isEmpty {
                    Section {
                        ForEach(items) { param in row(param, settings: settings, showLocations: true) }
                    } header: {
                        Text(group.label)
                    } footer: {
                        if let detail = group.detail { Text(detail) }
                    }
                }
            }
            let known = Set(meta.groups.map(\.id))
            let orphans = meta.params.filter { !known.contains($0.group) }
            if !orphans.isEmpty {
                Section("其它") { ForEach(orphans) { param in row(param, settings: settings, showLocations: true) } }
            }
        } else {
            Section("常用参数") {
                ForEach(ServerParamCatalog.builtin.params) { param in row(param, settings: settings, showLocations: true) }
            }
            let covered = Set(ServerParamCatalog.builtin.params.map(\.key))
            let rest = ServerParamCatalog.flatten(settings).filter { !covered.contains($0.key) }
            if !rest.isEmpty {
                Section {
                    ForEach(rest, id: \.key) { entry in
                        VStack(alignment: .leading, spacing: 2) {
                            Text(entry.key).font(.body.monospaced())
                            Text(ServerParamCatalog.display(entry.value, nil)).font(.caption).foregroundStyle(.secondary).lineLimit(3)
                        }
                        .accessibilityElement(children: .combine)
                        .accessibilityIdentifier("serverSettings.rawKey.\(entry.key)")
                    }
                } header: {
                    Text("其余参数（原键名）")
                } footer: {
                    Text("旧服务器没有这些参数的说明，按原键名列出；需要修改请用下面的「按键名原始编辑」。")
                }
            }
        }
        Section {
            NavigationLink {
                ManagementDocumentView(title: "按键名原始编辑", path: "/api/system/settings",
                                       actions: [.init(title: "编辑服务器参数", path: "/api/system/settings", method: "PATCH",
                                                       explanation: "按键名直接编辑服务器参数。每一项显示「中文名 · 英文原名」；只提交填写的项，未填的保持不变。")])
            } label: {
                Label("按键名原始编辑", systemImage: "curlybraces")
            }
            .accessibilityIdentifier("serverSettings.rawJSON")
        } footer: {
            Text("兜底入口：直接查看服务器返回的原始数据并按键名提交。")
        }
    }

    // MARK: 行

    @ViewBuilder private func row(_ param: ServerParamMeta, settings: JSONValue, showLocations: Bool = false) -> some View {
        let saved = ServerParamCatalog.value(param, in: settings)
        ServerParamRow(param: param, symbol: ServerParamCatalog.symbols[param.key], value: binding(param, saved: saved),
                       saved: saved, showLocations: showLocations)
    }

    private func binding(_ param: ServerParamMeta, saved: JSONValue) -> Binding<JSONValue> {
        Binding(get: { draft[param.key] ?? saved }, set: { newValue in
            draft[param.key] = newValue
            saveMessage = nil
        })
    }

    /// 底部状态条：保存失败 / 保存成功 / 有未保存修改（可放弃），没有内容时不占位置。
    @ViewBuilder private var statusBar: some View {
        if saveError != nil || saveMessage != nil || dirty {
            VStack(alignment: .leading, spacing: 8) {
                if let saveError {
                    Label(saveError, systemImage: "exclamationmark.triangle.fill")
                        .foregroundStyle(.red)
                        .accessibilityElement(children: .combine)
                        .accessibilityIdentifier("serverSettings.saveError")
                } else if let saveMessage {
                    Label(saveMessage, systemImage: "checkmark.circle.fill")
                        .foregroundStyle(.green)
                        .accessibilityElement(children: .combine)
                        .accessibilityIdentifier("serverSettings.saved")
                }
                if dirty {
                    HStack {
                        Text("\(changedCount) 项未保存，点右上角「保存」提交。")
                            .font(.caption).foregroundStyle(.secondary)
                            .accessibilityIdentifier("serverSettings.dirtyCount")
                        Spacer(minLength: 8)
                        Button("放弃修改", systemImage: "arrow.uturn.backward", role: .destructive) { confirmingDiscard = true }
                            .font(.caption.weight(.semibold))
                            .buttonStyle(.borderless)
                            .accessibilityIdentifier("serverSettings.discard")
                            .confirmationDialog("放弃未保存的修改？", isPresented: $confirmingDiscard, titleVisibility: .visible) {
                                Button("放弃修改", role: .destructive) {
                                    draft = baseline
                                    saveError = nil
                                }
                                Button("继续编辑", role: .cancel) {}
                            }
                    }
                }
            }
            .font(.subheadline)
            .frame(maxWidth: .infinity, alignment: .leading)
            .padding(.horizontal, 16)
            .padding(.vertical, 10)
            .background(.bar)
            .overlay(alignment: .top) { Divider() }
            .accessibilityElement(children: .contain)
            .accessibilityIdentifier("serverSettings.statusBar")
            .transition(.move(edge: .bottom).combined(with: .opacity))
        }
    }

    private var changedCount: Int { draft.filter { baseline[$0.key] != $0.value }.count }

    // MARK: 读写

    private func load() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            let value = try await model.request(path: "/api/system/settings")
            // 元数据读取失败（旧服务器 404、网络抖动）不影响编辑：回退到 App 内置说明与原键名。
            var nextMeta = ServerParamsMeta(groups: [], params: [], fromServer: false)
            if let raw = try? await model.request(path: "/api/system/settings/meta"), let parsed = ServerParamsMeta.parse(raw) {
                nextMeta = parsed
            }
            meta = nextMeta
            apply(value)
            loadError = nil
            uncertain = false
        } catch {
            loadError = OpsFormat.errorMessage(error)
        }
    }

    private func apply(_ value: JSONValue) {
        settings = value
        let values = ServerParamCatalog.values(for: params, from: value)
        baseline = values
        draft = values
    }

    private func save() async {
        guard !saving, !uncertain else { return }
        if let problem = ServerParamCatalog.validate(params: params, baseline: baseline, draft: draft) {
            saveError = problem
            saveMessage = nil
            return
        }
        guard let body = ServerParamCatalog.patch(params: params, baseline: baseline, draft: draft) else { return }
        saving = true
        saveError = nil
        saveMessage = nil
        defer { saving = false }
        do {
            let response = try await model.request(path: "/api/system/settings", method: "PATCH", body: body)
            if response.objectValue != nil, response["command_prefix"] != nil {
                apply(response)
            } else {
                baseline = draft
            }
            saveMessage = "已保存到服务器"
        } catch {
            if (error as? APIError)?.isOutcomeUncertain == true {
                uncertain = true
                saveError = "保存结果未知：请下拉刷新，确认服务器上的实际值后再修改。"
            } else {
                saveError = "保存失败：" + error.localizedDescription
            }
        }
    }
}

/// 「在别处修改」的一行：页面名、这里面有哪些参数与当前值，点按跳到那个页面。
struct ServerParamElsewhereRow: View {
    let target: String
    let label: String
    let items: [(String, String)]

    var body: some View {
        if let destination = destination {
            NavigationLink { destination } label: { content(navigable: true) }
                .accessibilityIdentifier("serverSettings.elsewhere.\(target)")
        } else {
            content(navigable: false)
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("serverSettings.elsewhere.\(target)")
        }
    }

    private func content(navigable: Bool) -> some View {
        Label {
            VStack(alignment: .leading, spacing: 4) {
                Text(navigable ? "去「\(label)」修改" : "在「\(label)」修改")
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(item.0).font(.caption).foregroundStyle(.secondary)
                        Spacer(minLength: 8)
                        Text(item.1).font(.caption.monospacedDigit()).foregroundStyle(.secondary).multilineTextAlignment(.trailing)
                    }
                }
            }
        } icon: {
            Image(systemName: Self.symbol(target))
        }
    }

    static func symbol(_ target: String) -> String {
        switch target {
        case "ai_global_settings": "cpu"
        case "runtime_tree": "point.3.filled.connected.trianglepath.dotted"
        case "software_update": "arrow.down.circle"
        case "sudo": "person.badge.shield.checkmark"
        case "totp": "lock.shield"
        case "payment_source": "scalemass"
        case "emergency_stop": "exclamationmark.octagon"
        default: "arrow.forward.circle"
        }
    }

    /// iOS 页面标识 → 页面；不认识的标识（新服务器新增）只显示文字。紧急停止在「更多」顶部，不推入新页面。
    private var destination: AnyView? {
        switch target {
        case "ai_global_settings": AnyView(AIGlobalSettingsView())
        case "runtime_tree": AnyView(PluginManagementView(isTabRoot: false, initialMode: .runtime))
        case "software_update": AnyView(SoftwareUpdateView())
        case "sudo":
            AnyView(ManagementCollectionView(resource: .init(
                title: "Sudo 权限", path: "/api/sudo", kind: .editable,
                create: ManagementAction(title: "添加 Sudo 用户", path: "/api/sudo", explanation: ManagementText.defaultActionExplanation),
                itemPath: "/api/sudo/{id}")))
        case "totp": AnyView(ManagementTOTPView())
        case "payment_source": AnyView(PaymentSourceStatusPage())
        default: nil
        }
    }
}

/// 一行参数：标题（中文名）+ 英文原名 + 明显可编辑的控件（带边框输入框 + 单位 + 步进器 / 开关 / 选择器）+ 一句说明；
/// 改过标「已修改」，和默认值不同时可「恢复默认」。只读参数显示锁和原因。
struct ServerParamRow: View {
    let param: ServerParamMeta
    var symbol: String? = nil
    @Binding var value: JSONValue
    let saved: JSONValue
    var showLocations = false

    private var modified: Bool { value != saved }
    private var canReset: Bool { param.isEditableHere && !param.defaultValue.isNull && value != param.defaultValue }
    private var localZone: String { TimeZone.current.identifier }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            if param.isEditableHere {
                editor
            } else {
                readOnly
            }
            if !param.detail.isEmpty {
                Text(param.detail).font(.footnote).foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
            }
            hints
            if showLocations, !param.alsoIn.isEmpty { locations }
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("serverSettings.row.\(param.identifier)")
    }

    // MARK: 标题

    private var titleBlock: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack(spacing: 6) {
                Text(param.label).font(.body.weight(.medium))
                if modified {
                    Text("已修改")
                        .font(.caption2.weight(.semibold))
                        .foregroundStyle(.orange)
                        .padding(.horizontal, 6).padding(.vertical, 2)
                        .background(Color.orange.opacity(0.15), in: Capsule())
                        .accessibilityIdentifier("serverSettings.modified.\(param.identifier)")
                }
            }
            Text(param.originalName)
                .font(.caption.monospaced())
                .foregroundStyle(.tertiary)
        }
    }

    @ViewBuilder private var titleWithIcon: some View {
        if let symbol {
            Label { titleBlock } icon: { Image(systemName: symbol) }
        } else {
            titleBlock
        }
    }

    // MARK: 控件

    @ViewBuilder private var editor: some View {
        switch param.kind {
        case .bool:
            Toggle(isOn: Binding(get: { value.boolValue ?? (param.defaultValue.boolValue ?? false) }, set: { value = .bool($0) })) {
                titleWithIcon
            }
            .accessibilityIdentifier("serverSettings.toggle.\(param.identifier)")
        case .enumeration:
            HStack(alignment: .center, spacing: 8) {
                titleWithIcon
                Spacer(minLength: 8)
                Picker(param.label, selection: Binding(get: { value.stringValue ?? (param.defaultValue.stringValue ?? "") },
                                                       set: { value = .string($0) })) {
                    ForEach(param.options, id: \.value) { option in Text(option.label).tag(option.value) }
                    if let current = value.stringValue, !param.options.contains(where: { $0.value == current }) {
                        Text(current).tag(current)
                    }
                }
                .labelsHidden()
                .pickerStyle(.menu)
                .accessibilityIdentifier("serverSettings.picker.\(param.identifier)")
            }
        case .integer:
            titleWithIcon
            integerEditor
        case .string, .timezone:
            titleWithIcon
            textEditor
        case .list, .unknown:
            readOnly
        }
    }

    private var integerRange: ClosedRange<Int> {
        let lower = param.min ?? 0
        let upper = Swift.max(lower, param.max ?? 1_000_000_000)
        return lower...upper
    }

    /// 步进器每点一下加减多少：天数按 1；范围越大步子越大。
    private var step: Int {
        if param.unit == "天" { return 1 }
        guard let max = param.max else { return 10 }
        let span = max - (param.min ?? 0)
        if span <= 100 { return 1 }
        if span <= 2000 { return 10 }
        if span <= 20000 { return 100 }
        return 500
    }

    private var integerEditor: some View {
        HStack(spacing: 10) {
            // 用字符串绑定而不是 `TextField(value:format:)`：后者要按回车才写回，数字键盘没有回车，「保存」会一直不可用。
            // 输入非数字或清空时暂存为字符串，保存前的校验会提示范围。
            TextField(param.defaultValue.intValue.map(String.init) ?? "0",
                      text: Binding(get: { value.intValue.map(String.init) ?? (value.stringValue ?? "") },
                                    set: { text in
                                        let digits = text.filter(\.isNumber)
                                        value = Int(digits).map { JSONValue.integer(Int64($0)) } ?? .string(digits)
                                    }))
                .keyboardType(.numberPad)
                .textFieldStyle(.roundedBorder)
                .multilineTextAlignment(.trailing)
                .monospacedDigit()
                .frame(width: 112)
                .accessibilityLabel(param.label)
                .accessibilityIdentifier("serverSettings.field.\(param.identifier)")
            if let unit = param.unit {
                Text(unit).foregroundStyle(.secondary)
            }
            Spacer(minLength: 8)
            Stepper("调整\(param.label)",
                    value: Binding(get: { Swift.min(Swift.max(value.intValue ?? param.defaultValue.intValue ?? integerRange.lowerBound,
                                                              integerRange.lowerBound), integerRange.upperBound) },
                                   set: { value = .integer(Int64($0)) }),
                    in: integerRange, step: step)
                .labelsHidden()
                .accessibilityIdentifier("serverSettings.stepper.\(param.identifier)")
        }
    }

    @ViewBuilder private var textEditor: some View {
        TextField(param.defaultValue.stringValue ?? "", text: Binding(get: { value.stringValue ?? "" }, set: { value = .string($0) }))
            .textInputAutocapitalization(.never)
            .autocorrectionDisabled()
            .keyboardType(.asciiCapable)
            .textFieldStyle(.roundedBorder)
            .font(param.kind == .string ? Font.body.monospaced() : Font.body)
            .accessibilityLabel(param.label)
            .accessibilityIdentifier("serverSettings.field.\(param.identifier)")
        if param.kind == .timezone && value.stringValue != localZone {
            Button("使用本机时区（\(localZone)）", systemImage: "location") { value = .string(localZone) }
                .font(.caption)
                .buttonStyle(.borderless)
                .accessibilityIdentifier("serverSettings.useLocalTimezone")
        }
    }

    private var readOnly: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                titleWithIcon
                Spacer(minLength: 8)
                Text(ServerParamCatalog.display(value, param))
                    .foregroundStyle(.secondary)
                    .multilineTextAlignment(.trailing)
            }
            Label(param.readOnlyReason ?? "这里只读。", systemImage: "lock")
                .font(.caption).foregroundStyle(.secondary)
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("serverSettings.readonly.\(param.identifier)")
    }

    // MARK: 提示

    @ViewBuilder private var hints: some View {
        let range: String? = param.kind == .integer ? rangeText : (param.maxLength.map { "最多 \($0) 个字符" })
        if range != nil || canReset {
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let range { Text(range).font(.caption).foregroundStyle(.secondary) }
                Spacer(minLength: 8)
                if canReset {
                    Button {
                        value = param.defaultValue
                    } label: {
                        Label("恢复默认（\(ServerParamCatalog.display(param.defaultValue, param))）", systemImage: "arrow.uturn.backward")
                    }
                    .font(.caption)
                    .buttonStyle(.borderless)
                    .accessibilityIdentifier("serverSettings.reset.\(param.identifier)")
                }
            }
        }
    }

    private var rangeText: String? {
        var parts: [String] = []
        switch (param.min, param.max) {
        case let (min?, max?): parts.append("范围 \(min)～\(max)")
        case let (min?, nil): parts.append("最小 \(min)")
        default: break
        }
        if let zero = param.zeroMeans { parts.append("0 = \(zero)") }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    private var locations: some View {
        VStack(alignment: .leading, spacing: 2) {
            ForEach(Array(param.alsoIn.enumerated()), id: \.offset) { _, location in
                Label {
                    Text(location.iosLabel.map { "也在 App「\($0)」· Web「\(location.label)」" } ?? "也在 Web「\(location.label)」")
                } icon: {
                    Image(systemName: "arrow.triangle.turn.up.right.diamond")
                }
                .font(.caption)
                .foregroundStyle(location.iosLabel != nil ? Color.accentColor : Color.secondary)
            }
        }
        .accessibilityElement(children: .combine)
        .accessibilityIdentifier("serverSettings.alsoIn.\(param.identifier)")
    }
}
