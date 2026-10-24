import SwiftUI
import TelePilotCore

// MARK: - 日志类型

/// 四类日志，各自解释“它记录什么、什么时候看”。
enum LogKind: String, CaseIterable, Identifiable, Sendable {
    case messages, runtime, audit, traces

    var id: String { rawValue }
    var title: String {
        switch self {
        case .messages: "消息日志"
        case .runtime: "运行日志"
        case .audit: "审计日志"
        case .traces: "调用链（Trace）"
        }
    }
    var shortTitle: String {
        switch self {
        case .messages: "消息"
        case .runtime: "运行"
        case .audit: "审计"
        case .traces: "调用链"
        }
    }
    var symbol: String {
        switch self {
        case .messages: "bubble.left.and.text.bubble.right"
        case .runtime: "gearshape.2"
        case .audit: "person.badge.shield.checkmark"
        case .traces: "point.topleft.down.to.point.bottomright.curvepath"
        }
    }
    var explanation: String {
        switch self {
        case .messages: "每条 Telegram 消息的处理结果：收到 → 匹配 → 处理 → 发送。用来回答“为什么没回复”。"
        case .runtime: "后台进程和插件写下的运行记录，例如启动、断线、报错。出现异常时最常看这里。"
        case .audit: "谁在什么时候改了什么：登录、暂停账号、安装插件、修改设置等。"
        case .traces: "一次事件在系统内部的完整处理时间线，适合深入排查某一条消息。"
        }
    }
    var path: String {
        switch self {
        case .messages: "/api/logs/messages"
        case .runtime: "/api/logs/runtime"
        case .audit: "/api/logs/audit"
        case .traces: "/api/logs/trace/events"
        }
    }
}

enum LogTimeRange: Int, CaseIterable, Identifiable {
    case hour = 1, day = 24, week = 168, month = 720
    var id: Int { rawValue }
    var title: String {
        switch self {
        case .hour: "1 小时"
        case .day: "24 小时"
        case .week: "7 天"
        case .month: "30 天"
        }
    }
    var since: Date { Date.now.addingTimeInterval(-Double(rawValue) * 3_600) }
}

enum LogLevelFilter: String, CaseIterable, Identifiable {
    case all, warning, error
    var id: String { rawValue }
    var title: String {
        switch self {
        case .all: "全部等级"
        case .warning: "告警和错误"
        case .error: "仅错误"
        }
    }
    var minimum: OpsSeverity {
        switch self {
        case .all: .debug
        case .warning: .warning
        case .error: .error
        }
    }
    var runtimeQuery: String? {
        switch self {
        case .all: nil
        case .warning: "warning"
        case .error: "error"
        }
    }
}

// MARK: - 统一日志条目

struct LogEntry: Identifiable, Sendable {
    let id: String
    let kind: LogKind
    let date: Date?
    let severity: OpsSeverity
    let title: String
    let subtitle: String?
    let accountID: Int?
    let traceID: String?
    let raw: JSONValue

    static func runtime(_ value: JSONValue) -> LogEntry {
        let message = OpsFormat.text(value["message"]) ?? "（空消息）"
        let source = LogText.runtimeSource(value["source"]?.stringValue)
        let plugin = OpsFormat.text(value["detail"]?["plugin_key"]) ?? OpsFormat.text(value["plugin_key"])
        return LogEntry(
            id: "runtime:" + (OpsFormat.text(value["id"]) ?? UUID().uuidString),
            kind: .runtime,
            date: OpsFormat.date(value["ts"]) ?? OpsFormat.date(value["created_at"]),
            severity: OpsSeverity.runtime(value["level"]?.stringValue),
            title: String(message.prefix(300)),
            subtitle: [source, plugin.map { "插件 \($0)" }].compactMap { $0 }.joined(separator: " · "),
            accountID: value["account_id"]?.intValue,
            traceID: OpsFormat.text(value["detail"]?["trace_id"]) ?? OpsFormat.text(value["trace_id"]),
            raw: value)
    }

    static func message(_ value: JSONValue) -> LogEntry {
        let verdict = LogText.verdict(value["verdict"]?.stringValue ?? value["funel"]?["verdict"]?.stringValue)
        let reason = OpsFormat.text(value["reason_text"]) ?? OpsFormat.text(value["funel"]?["reason_text"])
        return LogEntry(
            id: "message:" + (OpsFormat.text(value["trace_id"]) ?? OpsFormat.text(value["id"]) ?? UUID().uuidString),
            kind: .messages,
            date: OpsFormat.date(value["started_at"]),
            severity: verdict.severity,
            title: verdict.title + (reason.map { "：" + $0 } ?? ""),
            subtitle: [LogText.conversation(value), OpsFormat.text(value["text_preview"]).map { "“\($0.prefix(80))”" }]
                .compactMap { $0 }.joined(separator: " · "),
            accountID: value["account_id"]?.intValue,
            traceID: OpsFormat.text(value["trace_id"]),
            raw: value)
    }

    static func trace(_ value: JSONValue) -> LogEntry {
        let status = LogText.traceStatus(value["status"]?.stringValue)
        let event = LogText.eventType(value["event_type"]?.stringValue)
        let channel = LogText.channel(value["source_channel"]?.stringValue)
        return LogEntry(
            id: "trace:" + (OpsFormat.text(value["trace_id"]) ?? OpsFormat.text(value["id"]) ?? UUID().uuidString),
            kind: .traces,
            date: OpsFormat.date(value["started_at"]),
            severity: status.severity,
            title: "\(event)（\(channel)）· \(status.title)",
            subtitle: [LogText.conversation(value), OpsFormat.text(value["text_preview"]).map { "“\($0.prefix(80))”" }]
                .compactMap { $0 }.joined(separator: " · "),
            accountID: value["account_id"]?.intValue,
            traceID: OpsFormat.text(value["trace_id"]),
            raw: value)
    }

    static func audit(_ value: JSONValue) -> LogEntry {
        let action = value["action"]?.stringValue ?? ""
        let target = OpsFormat.text(value["target"])
        return LogEntry(
            id: "audit:" + (OpsFormat.text(value["id"]) ?? UUID().uuidString),
            kind: .audit,
            date: OpsFormat.date(value["ts"]),
            severity: LogText.auditIsSensitive(action) ? .warning : .info,
            title: LogText.auditAction(action),
            subtitle: [target.map { "对象：" + LogText.auditTarget($0) }, value["user_id"]?.intValue.map { "操作人：管理员 \($0)" } ?? "操作人：系统"]
                .compactMap { $0 }.joined(separator: " · "),
            accountID: LogText.auditAccountID(value),
            traceID: nil,
            raw: value)
    }

    /// 给人或助手看的诊断文本，已在复制出口统一脱敏。
    var diagnosisLines: [String] {
        var lines = ["[\(severity.title)] \(kind.title) · \(OpsFormat.fullTime(date))", title]
        if let subtitle, !subtitle.isEmpty { lines.append(subtitle) }
        if let accountID { lines.append("账号编号：\(accountID)") }
        if kind == .messages {
            if let step = OpsFormat.text(raw["next_step"]) { lines.append("建议：\(step)") }
            if let code = OpsFormat.text(raw["reason_code"]) { lines.append("原因代码：\(code)") }
            let funel = raw["funel"]
            let stages = LogText.stages.map { stage in "\(stage.title)=\(LogText.stage(funel?[stage.key]?.stringValue, key: stage.key).title)" }
            lines.append("处理过程：" + stages.joined(separator: "，"))
        }
        if kind == .runtime, let detail = raw["detail"], let text = try? ManagementText.sanitizedExport(detail).prettyText(), detail.objectValue?.isEmpty == false {
            lines.append("附加信息：" + text)
        }
        if let traceID { lines.append("链路 ID：\(traceID)") }
        return lines
    }
}

// MARK: - 文案翻译

enum LogText {
    struct Stage: Sendable { let key: String; let title: String; let help: String }
    static let stages: [Stage] = [
        Stage(key: "received", title: "收到", help: "系统收到了这条消息或事件"),
        Stage(key: "routed", title: "匹配", help: "判断有没有插件、规则或指令要处理它"),
        Stage(key: "ran", title: "处理", help: "插件或规则开始运行"),
        Stage(key: "sent", title: "发送", help: "把结果发回 Telegram")
    ]

    static func stage(_ raw: String?, key: String) -> (title: String, symbol: String, color: Color) {
        switch raw ?? "none" {
        case "pass":
            let titles = ["received": "已收到", "routed": "已匹配", "ran": "已处理", "sent": "已发送"]
            return (titles[key] ?? "通过", "checkmark.circle.fill", .green)
        case "skip": return ("跳过", "minus.circle", .secondary)
        case "stuck": return ("卡住", "clock.badge.exclamationmark", .orange)
        case "fail": return ("失败", "xmark.circle.fill", .red)
        default: return (key == "sent" ? "无发送动作" : "未到达", "circle.dashed", .secondary)
        }
    }

    static func verdict(_ raw: String?) -> (title: String, severity: OpsSeverity, help: String) {
        switch raw ?? "" {
        case "responded": ("已回复", .success, "消息已被处理，并发出了回复或动作。")
        case "no_response_normal": ("正常未回复", .info, "没有插件或规则需要处理这条消息，属于正常情况。")
        case "stuck": ("卡住", .warning, "消息停在某个步骤没有继续，可能仍在处理或进程异常。")
        case "failed": ("处理失败", .error, "处理过程中出错，按下方建议排查。")
        default: ("未判定", .info, "服务器没有给出这条消息的结论。")
        }
    }

    static func traceStatus(_ raw: String?) -> (title: String, severity: OpsSeverity) {
        switch (raw ?? "").lowercased() {
        case "ok", "success", "sent", "done", "completed": ("完成", .success)
        case "skipped", "skip": ("已跳过", .info)
        case "warning", "warn": ("告警", .warning)
        case "failed", "error": ("失败", .error)
        case "running", "pending", "received", "normalized", "matched", "delivered": ("处理中", .info)
        default: ((raw ?? "").isEmpty ? "未知" : raw ?? "未知", .info)
        }
    }

    static func channel(_ raw: String?) -> String {
        switch raw ?? "" {
        case "userbot": "UserBot 账号"
        case "userbot_reply": "UserBot 回复"
        case "interaction_bot": "交互 Bot"
        case "interaction_bot_reply": "交互 Bot 回复"
        case "account_bot": "管理 Bot"
        case "scheduler": "定时任务"
        case "external_payment_notice": "收款通知"
        case "": "未知来源"
        default: raw ?? "未知来源"
        }
    }

    static func eventType(_ raw: String?) -> String {
        let labels = [
            "message": "普通消息", "command": "指令消息", "callback_query": "按钮点击", "inline_query": "Inline 查询",
            "chosen_inline_result": "Inline 选择结果", "payment_confirmed": "收款确认", "session_close": "会话关闭",
            "session_expired": "会话过期", "message_edited": "编辑消息", "keyword": "关键词触发", "scheduler_fire": "定时触发",
            "all_messages": "全部消息", "all_events": "全部事件"
        ]
        let value = (raw ?? "").lowercased()
        return labels[value] ?? (value.isEmpty ? "未知事件" : raw ?? "未知事件")
    }

    static func runtimeSource(_ raw: String?) -> String {
        switch (raw ?? "").lowercased() {
        case "system", "worker", "runtime": "系统进程"
        case "event": "消息链路"
        case "plugin": "插件"
        case "": "运行时"
        default: raw ?? "运行时"
        }
    }

    static func phase(_ raw: String?) -> String {
        let value = (raw ?? "").lowercased()
        if value.contains("receive") { return "收到消息" }
        if value.contains("subscription") || value.contains("route") { return "判断由谁处理" }
        if value.contains("command") { return "指令匹配" }
        if value.contains("plugin_invoke") { return "插件开始执行" }
        if value.contains("plugin_return") { return "插件执行结束" }
        if value.contains("contract") { return "结果格式检查" }
        if value.contains("delivery") || value.contains("send") { return "发送处理" }
        if value.contains("settlement") { return "收付款处理" }
        if value.contains("session") { return "会话处理" }
        return raw?.isEmpty == false ? raw! : "处理步骤"
    }

    static func actionType(_ raw: String?) -> String {
        switch (raw ?? "").lowercased() {
        case "send_message": "发送消息"
        case "edit_message": "编辑消息"
        case "delete_message": "删除消息"
        case "answer_inline_query": "回答 Inline 查询"
        case "answer_callback_query": "回应按钮点击"
        case "payout": "结算付款"
        case "start_session": "开启会话"
        case "close_session": "关闭会话"
        case "": "动作"
        default: raw ?? "动作"
        }
    }

    static let reasons: [String: String] = [
        "account_not_matched": "账号不匹配", "account_bot_user_unauthorized": "管理 Bot 用户未授权",
        "action_failed": "动作执行失败", "action_limit_exceeded": "动作数量超限", "already_acked": "已确认过的按钮回调",
        "bot_not_configured": "交互 Bot 未配置", "bot_self_message": "忽略 Bot 自己发的消息", "bot_token_missing": "Bot Token 缺失",
        "callback_query": "按钮回调", "callback_query_id_missing": "按钮回调 ID 缺失", "command_matched": "指令已命中",
        "command_not_matched": "未命中指令", "command_unauthorized": "指令权限不足", "contract_failed": "插件返回格式不合规",
        "contract_warning": "插件返回格式有告警", "empty_message_text": "消息文本为空", "entry_key_missing": "插件入口缺失",
        "event_bus_delivery_disabled": "事件投递已关闭", "event_type_not_subscribed": "插件没有订阅这种事件",
        "filter_not_matched": "过滤条件未命中", "handler_error": "处理器异常", "inline_disabled": "Inline 已关闭",
        "inline_query_answer_failed": "Inline 回答失败", "interaction_rule_owned": "交互规则属于其他账号",
        "interaction_bot_not_in_chat": "交互 Bot 不在目标会话中", "manifest_invalid": "插件清单不合法", "matched": "已命中",
        "media_payload_empty": "媒体内容为空", "media_payload_invalid": "媒体内容格式无效", "media_payload_missing": "媒体内容缺失",
        "native_raw_not_allowed": "插件未声明原生数据能力", "native_raw_skipped": "原生数据未下发", "payout_failed": "结算付款失败",
        "permission_denied": "权限不足", "plugin_disabled": "插件未启用", "plugin_load_failed": "插件加载失败",
        "plugin_not_installed": "插件未安装", "plugin_declared_failed": "插件声明处理失败", "plugin_runtime_error": "插件运行异常",
        "rate_limited": "触发频率限制", "scope_not_matched": "作用范围不匹配", "send_channel_deprecated": "发送通道已废弃",
        "session_control_action": "会话控制动作", "session_expired": "会话已过期", "session_not_found": "会话不存在",
        "settlement_requires_userbot": "结算需要 UserBot 账号", "source_not_subscribed": "来源会话不在订阅范围内",
        "subscription_load_failed": "订阅配置加载失败", "subscription_not_matched": "订阅条件未命中",
        "synthetic_callback": "模拟按钮回调", "target_message_id_missing": "目标消息 ID 缺失",
        "telegram_api_error": "Telegram 接口报错", "trace_write_failed": "调用链记录降级", "transfer_rule_not_matched": "转账规则未匹配",
        "unsupported_send_via": "不支持的发送通道", "usage_limited": "用户次数或冷却限制", "usage_pending": "用户上一局仍在处理",
        "userbot_command_message": "UserBot 指令消息已让路", "userbot_offline": "UserBot 账号离线"
    ]

    static func reason(_ code: String?) -> String? {
        guard let code, !code.isEmpty else { return nil }
        return reasons[code].map { "\($0)（\(code)）" } ?? code
    }

    static func conversation(_ value: JSONValue) -> String? {
        if let chatID = value["chat_id"]?.intValue {
            let kind = chatID < 0 ? "群" : "私聊"
            if let title = OpsFormat.text(value["chat_title"]) { return "\(kind)“\(title)”" }
            return "\(kind) \(chatID)"
        }
        if let sender = OpsFormat.text(value["sender_name"]) { return sender }
        return value["sender_user_id"]?.intValue.map { "用户 \($0)" }
    }

    static func sender(_ value: JSONValue) -> String? {
        let parts = [OpsFormat.text(value["sender_name"]), value["sender_user_id"]?.intValue.map { "ID \($0)" }].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }

    // 审计动作：常见动作整句翻译，其余按“对象 + 动词”拼出中文。
    private static let auditWhole: [String: String] = [
        "auth.register": "创建管理员账号", "auth.change_password": "修改管理员密码", "auth.totp.enable": "开启验证器保护",
        "auth.totp.disable": "关闭验证器保护", "auth.login": "登录管理台", "auth.logout": "退出管理台",
        "account.login.start": "开始登录 Telegram 账号", "account.login.finalize": "完成 Telegram 账号登录",
        "account.clone_config": "复制账号配置", "account.config_bundle.confirm": "导入账号配置",
        "ledger.reset": "重置资金台账", "ledger.compensation.manual_paid": "人工核销待补付",
        "llm_provider.api_key_reveal": "查看 AI 密钥明文", "message_template.test_send": "测试发送消息模板",
        "plugin.ai_quota_release_failed": "插件 AI 配额释放失败", "account_bot.transfer_notice_matched": "匹配到转账通知",
        "account_bot.polling_dlq_replay": "重新投递失败消息", "account_bot.polling_dlq_discard": "丢弃失败消息",
        "set_default_device_profile": "设置默认设备配置"
    ]
    private static let auditSubjects: [String: String] = [
        "account": "账号", "account_bot": "管理 Bot", "account_bot_user": "管理 Bot 用户", "account_command": "账号指令",
        "alias": "指令别名", "apns": "推送通知", "apns.config": "推送配置", "apns.device": "推送设备", "auth": "登录与安全",
        "command_template": "指令模板", "feature": "插件功能", "feature.config": "插件配置", "feature.global_config": "插件全局配置",
        "feature.direct_passthrough": "直通转发", "ignored_peer": "忽略会话", "ledger": "资金台账", "llm_provider": "AI 供应商",
        "message_template": "消息模板", "notify_bot": "通知 Bot", "plugin": "插件", "rule": "规则", "sudo": "Sudo 权限",
        "proxy": "代理", "device_profile": "设备配置", "system": "系统", "webhook": "Webhook"
    ]
    private static let auditVerbs: [String: String] = [
        "create": "新建", "update": "修改", "delete": "删除", "add": "添加", "remove": "移除", "enable": "启用",
        "disable": "停用", "pause": "暂停", "resume": "恢复", "toggle": "切换", "install": "安装", "uninstall": "卸载",
        "reload": "重新加载", "test": "测试", "reset": "重置", "register": "注册", "unregister": "移除", "copy": "复制",
        "reorder": "调整顺序", "restart": "重启", "save": "保存", "execute": "执行", "start": "开始", "action": "执行操作",
        "account_pause": "暂停账号", "account_resume": "恢复账号", "account_restart": "重启账号", "restart_runtime": "重启运行进程",
        "command_toggle": "切换指令", "feature_toggle": "切换插件", "rule_toggle": "切换规则", "rule_execute": "执行规则",
        "plugin_install": "安装插件", "plugin_uninstall": "卸载插件", "plugin_update": "更新插件",
        "install_upload": "上传安装", "install_enable": "启用", "install_disable": "停用", "install_uninstall": "卸载",
        "install_batch_enable": "批量启用", "install_batch_disable": "批量停用", "key_create": "添加密钥", "key_update": "修改密钥",
        "key_delete": "删除密钥", "fetch_models": "获取模型列表", "fetch_models_preview": "预览模型列表", "detect_protocols": "检测协议",
        "transfer_notice_update": "修改转账通知", "interaction_composite_save": "保存交互配置"
    ]

    static func auditAction(_ raw: String) -> String {
        if let whole = auditWhole[raw] { return whole }
        if raw.contains(".") {
            let parts = raw.split(separator: ".").map(String.init)
            for split in stride(from: parts.count - 1, through: 1, by: -1) {
                let subject = parts[..<split].joined(separator: ".")
                let verb = parts[split...].joined(separator: "_")
                if let subjectText = auditSubjects[subject], let verbText = auditVerbs[verb] {
                    return verb.contains("_") ? subjectText + "：" + verbText : verbText + subjectText
                }
                if let subjectText = auditSubjects[subject], split == parts.count - 1 { return subjectText + " · " + verb }
            }
        } else if let underscore = raw.firstIndex(of: "_") {
            let verb = String(raw[..<underscore])
            let subject = String(raw[raw.index(after: underscore)...])
            if let verbText = auditVerbs[verb], let subjectText = auditSubjects[subject] { return verbText + subjectText }
        }
        return raw.isEmpty ? "未命名操作" : "操作：\(raw)"
    }

    static func auditIsSensitive(_ raw: String) -> Bool {
        ["delete", "reset", "uninstall", "disable", "remove", "reveal", "discard", "change_password", "totp"].contains { raw.contains($0) }
    }

    static func auditTarget(_ raw: String) -> String {
        let parts = raw.split(separator: ":", maxSplits: 1).map(String.init)
        guard parts.count == 2, let subject = auditSubjects[parts[0]] else { return raw }
        return "\(subject) \(parts[1])"
    }

    static func auditAccountID(_ value: JSONValue) -> Int? {
        if let target = value["target"]?.stringValue, target.hasPrefix("account:") { return Int(target.dropFirst(8)) }
        return value["detail"]?["account_id"]?.intValue
    }

    static func spanSeverity(_ raw: String?) -> OpsSeverity { traceStatus(raw).severity }
}

// MARK: - 日志首页

struct LogsView: View {
    @Environment(AppModel.self) private var model
    @State private var stats: JSONValue?
    @State private var problems: [LogEntry] = []
    @State private var failedMessages = 0
    @State private var error: String?
    @State private var loading = false
    @State private var loaded = false

    var body: some View {
        List {
            Section {
                Text("出问题时按这个顺序看：先看“最近问题”，再按类型深入。所有日志默认隐藏密码、Token 等敏感信息，复制出去也一样。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            recentSection
            Section {
                NavigationLink { LogListView(kinds: [.messages, .runtime, .audit]) } label: {
                    LogKindLabel(title: "全部日志", symbol: "list.bullet.rectangle.portrait",
                                 detail: "把消息、运行、审计三类日志合成一个列表，按时间排序，可筛选和搜索。")
                }
                .accessibilityIdentifier("logs.unified")
            }
            Section {
                ForEach(LogKind.allCases) { kind in
                    NavigationLink { LogListView(kinds: [kind]) } label: {
                        LogKindLabel(title: kind.title, symbol: kind.symbol, detail: kind.explanation)
                    }
                    .accessibilityIdentifier("logs.kind.\(kind.rawValue)")
                }
            } header: { Text("按类型查看") }
            Section {
                NavigationLink {
                    ManagementFormView(action: .init(title: "查看系统控制台", path: "/api/logs/system-console",
                        explanation: "读取服务器进程的原始输出（类似 docker compose logs），用于排查启动失败、异常堆栈等。内容较技术化。"))
                } label: {
                    LogKindLabel(title: "系统控制台", symbol: "terminal", detail: "服务器进程的原始输出，适合把内容复制给开发者。")
                }
                NavigationLink {
                    ManagementFormView(action: .init(title: "模拟消息路由", path: "/api/dispatch/simulate", explanation: "模拟一条消息会被哪些插件和规则处理，不会真的发到 Telegram。"))
                } label: {
                    LogKindLabel(title: "模拟消息路由", symbol: "arrow.triangle.branch", detail: "输入一条示例消息，看它会命中哪些插件或规则。")
                }
                NavigationLink {
                    ManagementDocumentView(title: "投递统计", path: "/api/dispatch/router-delivery-stats")
                } label: {
                    LogKindLabel(title: "投递统计", symbol: "chart.bar.doc.horizontal", detail: "消息分发到各插件的次数与失败数。")
                }
                NavigationLink {
                    ManagementFormView(action: .init(title: "开启路由诊断记录", path: "/api/dispatch/router-debug-trace", explanation: "临时记录更详细的路由过程，用完记得关闭，避免日志过多。"))
                } label: {
                    LogKindLabel(title: "开启路由诊断记录", symbol: "record.circle", detail: "临时打开更详细的路由记录，排查完关闭。")
                }
            } header: { Text("高级诊断") }
        }
        .navigationTitle("日志与诊断")
        .navigationBarTitleDisplayMode(.inline)
        .task { if !loaded { await load() } }
        .refreshable { await load() }
        .accessibilityIdentifier("logs.screen")
    }

    @ViewBuilder private var recentSection: some View {
        Section {
            if let error { NoticeView(title: "最近问题读取失败", message: error) }
            OpsMetricGrid {
                OpsMetricTile(title: "错误", value: stats.map { OpsFormat.integer($0["error"]?.intValue ?? 0) } ?? "—",
                              symbol: OpsSeverity.error.symbol, color: (stats?["error"]?.intValue ?? 0) > 0 ? .red : .primary, detail: "运行日志")
                OpsMetricTile(title: "告警", value: stats.map { OpsFormat.integer($0["warn"]?.intValue ?? 0) } ?? "—",
                              symbol: OpsSeverity.warning.symbol, color: (stats?["warn"]?.intValue ?? 0) > 0 ? .orange : .primary, detail: "运行日志")
                OpsMetricTile(title: "消息处理失败", value: loaded ? OpsFormat.integer(failedMessages) : "—",
                              symbol: "bubble.left.and.exclamationmark.bubble.right", color: failedMessages > 0 ? .red : .primary, detail: "含卡住的消息")
            }
            if loading && !loaded { ProgressView("正在读取最近问题…") }
            if loaded && problems.isEmpty && error == nil {
                Label("最近 24 小时没有错误或告警", systemImage: "checkmark.circle").foregroundStyle(.green)
            }
            ForEach(problems.prefix(5)) { entry in
                NavigationLink { LogDetailView(entry: entry) } label: { LogEntryRow(entry: entry) }
            }
            if !problems.isEmpty {
                NavigationLink { LogListView(kinds: [.messages, .runtime, .audit], level: .warning, range: .day, title: "最近问题") } label: {
                    Label("查看全部问题", systemImage: "exclamationmark.bubble")
                }
                .accessibilityIdentifier("logs.allProblems")
            }
        } header: { Text("最近 24 小时") }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        let model = self.model
        let since = OpsFormat.iso(LogTimeRange.day.since)
        async let statsResult = OpsFetch.result(model, OpsFormat.path("/api/logs/runtime/stats", [("since", since)]))
        async let runtimeResult = OpsFetch.result(model, OpsFormat.path("/api/logs/runtime", [("since", since), ("level", "warning"), ("limit", "20")]))
        async let failedResult = OpsFetch.result(model, OpsFormat.path("/api/logs/messages", [("since", since), ("verdict", "failed"), ("limit", "20")]))
        async let stuckResult = OpsFetch.result(model, OpsFormat.path("/api/logs/messages", [("since", since), ("verdict", "stuck"), ("limit", "20")]))
        let results = await (statsResult, runtimeResult, failedResult, stuckResult)
        guard !Task.isCancelled else { return }
        var collected: [LogEntry] = []
        var failures: [String] = []
        switch results.0 {
        case .success(let value): stats = value.objectValue == nil ? nil : value
        case .failure(let failure): failures.append(OpsFormat.errorMessage(failure))
        }
        if case .success(let value) = results.1 {
            collected += ManagementText.items(value).map(LogEntry.runtime).filter { $0.severity >= .warning }
        } else if case .failure(let failure) = results.1 { failures.append(OpsFormat.errorMessage(failure)) }
        var messageCount = 0
        for result in [results.2, results.3] {
            if case .success(let value) = result {
                let items = ManagementText.items(value).map(LogEntry.message).filter { $0.severity >= .warning }
                messageCount += items.count
                collected += items
            }
        }
        failedMessages = messageCount
        problems = collected.sorted { ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
        error = failures.first
        loaded = true
    }
}

struct LogKindLabel: View {
    let title: String
    let symbol: String
    let detail: String
    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                Text(detail).font(.caption).foregroundStyle(.secondary)
            }
        } icon: { Image(systemName: symbol) }
        .padding(.vertical, 2)
    }
}

// MARK: - 统一列表

struct LogListView: View {
    let availableKinds: [LogKind]
    let title: String
    @Environment(AppModel.self) private var model
    @State private var kindFilter: LogKind?
    @State private var level: LogLevelFilter
    @State private var range: LogTimeRange
    @State private var accountID: Int?
    @State private var search = ""
    @State private var keyword = ""
    @State private var entries: [LogEntry] = []
    @State private var errors: [String] = []
    @State private var loading = false
    @State private var loadedAt: Date?

    init(kinds: [LogKind], level: LogLevelFilter = .all, range: LogTimeRange = .day, title: String? = nil) {
        availableKinds = kinds
        self.title = title ?? (kinds.count == 1 ? kinds[0].title : "全部日志")
        _level = State(initialValue: level)
        _range = State(initialValue: range)
    }

    private var activeKinds: [LogKind] { kindFilter.map { [$0] } ?? availableKinds }
    private var loadKey: String {
        "\(activeKinds.map(\.rawValue).joined(separator: ","))|\(level.rawValue)|\(range.rawValue)|\(accountID ?? 0)|\(keyword)"
    }

    private var visible: [LogEntry] {
        entries.filter { entry in
            guard entry.severity >= level.minimum else { return false }
            if let accountID, entry.kind == .audit, entry.accountID != accountID { return false }
            guard !search.isEmpty else { return true }
            return [entry.title, entry.subtitle ?? "", entry.traceID ?? "", accountName(entry.accountID) ?? ""]
                .contains { $0.localizedCaseInsensitiveContains(search) }
        }
    }

    var body: some View {
        let rows = visible
        List {
            Section {
                Text(availableKinds.count == 1 ? availableKinds[0].explanation
                     : "按时间合并显示多类日志。左侧图标表示严重程度：红色 ✕ 为错误，橙色 ⚠︎ 为告警，蓝色 ⓘ 为普通信息，绿色 ✓ 为处理成功。")
                    .font(.footnote).foregroundStyle(.secondary)
            }
            Section {
                Picker("时间", selection: $range) {
                    ForEach(LogTimeRange.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                Picker(selection: $level) {
                    ForEach(LogLevelFilter.allCases) { Text($0.title).tag($0) }
                } label: { Label("严重程度", systemImage: "exclamationmark.triangle") }
                if availableKinds.count > 1 {
                    Picker(selection: $kindFilter) {
                        Text("全部类型").tag(LogKind?.none)
                        ForEach(availableKinds) { Text($0.title).tag(LogKind?.some($0)) }
                    } label: { Label("日志类型", systemImage: "square.stack.3d.up") }
                }
                if activeKinds != [.audit] {
                    Picker(selection: $accountID) {
                        Text("全部账号").tag(Int?.none)
                        ForEach(model.accounts) { Text(AccountPresentation.name($0)).tag(Int?.some($0.id)) }
                    } label: { Label("账号", systemImage: "person.crop.circle") }
                }
            } footer: {
                if !keyword.isEmpty { Text("服务器已按关键词“\(keyword)”筛选。清空搜索框即可取消。") }
            }
            ForEach(errors, id: \.self) { message in
                Section { NoticeView(title: "部分日志读取失败", message: message) }
            }
            Section {
                if loading && entries.isEmpty { ProgressView("正在读取日志…") }
                ForEach(rows) { entry in
                    NavigationLink { LogDetailView(entry: entry) } label: { LogEntryRow(entry: entry, accountName: accountName(entry.accountID)) }
                }
                if !loading, loadedAt != nil, rows.isEmpty {
                    ContentUnavailableView("没有符合条件的日志", systemImage: "tray", description: Text("可以放宽时间范围或严重程度，或清空搜索。"))
                }
            } header: {
                if loadedAt != nil {
                    let errorCount = rows.filter { $0.severity == .error }.count
                    let warningCount = rows.filter { $0.severity == .warning }.count
                    Text("共 \(rows.count) 条 · 错误 \(errorCount) · 告警 \(warningCount)")
                }
            } footer: {
                if loadedAt != nil { Text("每类最多读取最近 100～200 条。需要更早的记录请缩小时间范围或使用关键词。") }
            }
        }
        .navigationTitle(title)
        .navigationBarTitleDisplayMode(.inline)
        .searchable(text: $search, prompt: "搜索内容、群名、插件或链路 ID")
        .onSubmit(of: .search) { keyword = search.trimmingCharacters(in: .whitespacesAndNewlines) }
        .onChange(of: search) { _, value in if value.isEmpty, !keyword.isEmpty { keyword = "" } }
        .task(id: loadKey) { await load() }
        .refreshable { await load() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("复制诊断信息", systemImage: "doc.on.doc") {
                    var lines = ["日志：\(title)，范围 \(range.title)，\(level.title)，共 \(rows.count) 条"]
                    for entry in rows.prefix(30) { lines.append(""); lines += entry.diagnosisLines }
                    OpsFormat.copyForDiagnosis(lines)
                }
                .disabled(rows.isEmpty)
                .accessibilityIdentifier("logs.copy")
            }
        }
        .accessibilityIdentifier("logs.list")
    }

    private func accountName(_ id: Int?) -> String? {
        guard let id else { return nil }
        return model.accounts.first { $0.id == id }.map(AccountPresentation.name) ?? "账号 \(id)"
    }

    private func load() async {
        loading = true
        defer { loading = false }
        let model = self.model
        let since = OpsFormat.iso(range.since)
        let account = accountID.map(String.init)
        let keyword = self.keyword.isEmpty ? nil : self.keyword
        let kinds = activeKinds
        let runtimePath = OpsFormat.path(LogKind.runtime.path, [("since", since), ("account_id", account), ("level", level.runtimeQuery), ("keyword", keyword), ("limit", "200")])
        let messagesPath = OpsFormat.path(LogKind.messages.path, [("since", since), ("account_id", account), ("keyword", keyword), ("limit", "100")])
        let auditPath = OpsFormat.path(LogKind.audit.path, [("since", since), ("keyword", keyword), ("limit", "100")])
        let tracesPath = OpsFormat.path(LogKind.traces.path, [("since", since), ("account_id", account), ("keyword", keyword), ("limit", "100")])
        async let runtimeResult = OpsFetch.result(model, runtimePath, enabled: kinds.contains(.runtime))
        async let messagesResult = OpsFetch.result(model, messagesPath, enabled: kinds.contains(.messages))
        async let auditResult = OpsFetch.result(model, auditPath, enabled: kinds.contains(.audit))
        async let tracesResult = OpsFetch.result(model, tracesPath, enabled: kinds.contains(.traces))
        let results = await (runtimeResult, messagesResult, auditResult, tracesResult)
        guard !Task.isCancelled else { return }
        var collected: [LogEntry] = []
        var failures: [String] = []
        let pairs: [(LogKind, Result<JSONValue, Error>, (JSONValue) -> LogEntry)] = [
            (.runtime, results.0, LogEntry.runtime), (.messages, results.1, LogEntry.message),
            (.audit, results.2, LogEntry.audit), (.traces, results.3, LogEntry.trace)
        ]
        for (kind, result, transform) in pairs {
            switch result {
            case .success(let value): collected += ManagementText.items(value).map(transform)
            case .failure(let failure): failures.append("\(kind.title)：\(OpsFormat.errorMessage(failure))")
            }
        }
        entries = collected.sorted { ($0.date ?? .distantPast) > ($1.date ?? .distantPast) }
        errors = failures
        loadedAt = Date.now
    }
}

struct LogEntryRow: View {
    let entry: LogEntry
    var accountName: String?

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            OpsSeverityIcon(severity: entry.severity)
            VStack(alignment: .leading, spacing: 3) {
                Text(entry.title)
                    .font(.subheadline)
                    .lineLimit(3)
                if let subtitle = entry.subtitle, !subtitle.isEmpty {
                    Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                }
                HStack(spacing: 6) {
                    Label(entry.kind.shortTitle, systemImage: entry.kind.symbol).labelStyle(.titleAndIcon)
                    if let accountName { Text(accountName).lineLimit(1) }
                    Text(OpsFormat.relative(entry.date))
                }
                .font(.caption2)
                .foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }
}

// MARK: - 详情

struct LogDetailView: View {
    let entry: LogEntry
    @Environment(AppModel.self) private var model
    @State private var copied = false

    private var accountName: String? {
        guard let id = entry.accountID else { return nil }
        return model.accounts.first { $0.id == id }.map(AccountPresentation.name) ?? "账号 \(id)"
    }

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 8) {
                    OpsBadge(title: entry.severity.title, symbol: entry.severity.symbol, color: entry.severity.color)
                    Text(entry.title).font(.headline).textSelection(.enabled)
                    Text("这是一条\(entry.kind.title)：\(entry.kind.explanation)")
                        .font(.footnote).foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }
            if entry.kind == .messages { messageSections }
            Section("详细信息") { fields }
            if let traceID = entry.traceID {
                Section {
                    NavigationLink { LogTraceTimelineView(traceID: traceID) } label: {
                        Label("查看处理时间线", systemImage: "point.topleft.down.to.point.bottomright.curvepath")
                    }
                    .accessibilityIdentifier("logs.openTrace")
                } footer: { Text("按时间顺序列出这条消息在系统内部经过的每一步，以及每步花了多久。") }
            }
            Section {
                NavigationLink {
                    List { ManagementValueView(value: entry.raw) }.navigationTitle("原始数据")
                } label: { Label("原始数据（高级）", systemImage: "curlybraces") }
                Button {
                    OpsFormat.copyForDiagnosis(entry.diagnosisLines)
                    copied = true
                } label: { Label(copied ? "已复制，可粘贴给助手或开发者" : "复制诊断信息", systemImage: copied ? "checkmark" : "doc.on.doc") }
                .accessibilityIdentifier("logs.detail.copy")
            } footer: { Text("复制内容已自动隐藏密码、Token 等敏感信息。") }
        }
        .navigationTitle(entry.kind.title)
        .navigationBarTitleDisplayMode(.inline)
    }

    @ViewBuilder private var messageSections: some View {
        let verdict = LogText.verdict(entry.raw["verdict"]?.stringValue ?? entry.raw["funel"]?["verdict"]?.stringValue)
        Section {
            ForEach(LogText.stages, id: \.key) { stage in
                let state = LogText.stage(entry.raw["funel"]?[stage.key]?.stringValue, key: stage.key)
                LabeledContent {
                    Label(state.title, systemImage: state.symbol).foregroundStyle(state.color).labelStyle(.titleAndIcon)
                } label: {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(stage.title)
                        Text(stage.help).font(.caption2).foregroundStyle(.secondary)
                    }
                }
            }
        } header: { Text("处理过程") } footer: { Text(verdict.help) }
        if OpsFormat.text(entry.raw["reason_text"]) != nil || OpsFormat.text(entry.raw["next_step"]) != nil {
            Section("结论与建议") {
                OpsFieldRow(title: "原因", value: OpsFormat.text(entry.raw["reason_text"]), symbol: "questionmark.circle")
                OpsFieldRow(title: "建议下一步", value: OpsFormat.text(entry.raw["next_step"]), symbol: "lightbulb")
                OpsFieldRow(title: "卡在哪一步", value: entry.raw["stuck_at"]?.stringValue.flatMap { key in LogText.stages.first { $0.key == key }?.title }, symbol: "mappin.and.ellipse")
                OpsFieldRow(title: "原因代码", value: LogText.reason(entry.raw["reason_code"]?.stringValue), symbol: "number", monospaced: true)
            }
        }
    }

    @ViewBuilder private var fields: some View {
        OpsFieldRow(title: "时间", value: OpsFormat.fullTime(entry.date), symbol: "clock")
        OpsFieldRow(title: "账号", value: accountName, symbol: "person.crop.circle")
        switch entry.kind {
        case .runtime:
            OpsFieldRow(title: "等级", value: entry.severity.title + "（\(entry.raw["level"]?.stringValue ?? "info")）", symbol: entry.severity.symbol)
            OpsFieldRow(title: "来源", value: LogText.runtimeSource(entry.raw["source"]?.stringValue), symbol: "shippingbox")
            OpsFieldRow(title: "插件", value: OpsFormat.text(entry.raw["detail"]?["plugin_key"]), symbol: "puzzlepiece.extension")
            OpsFieldRow(title: "完整内容", value: OpsFormat.text(entry.raw["message"]), symbol: "text.alignleft")
            if let detail = entry.raw["detail"], detail.objectValue?.isEmpty == false {
                NavigationLink {
                    List { ManagementValueView(value: detail) }.navigationTitle("附加信息")
                } label: { Label("附加信息", systemImage: "info.square") }
            }
        case .messages, .traces:
            OpsFieldRow(title: "来源", value: LogText.channel(entry.raw["source_channel"]?.stringValue), symbol: "antenna.radiowaves.left.and.right")
            OpsFieldRow(title: "事件类型", value: LogText.eventType(entry.raw["event_type"]?.stringValue), symbol: "tag")
            OpsFieldRow(title: "会话", value: LogText.conversation(entry.raw), symbol: "bubble.left.and.bubble.right")
            OpsFieldRow(title: "发送者", value: LogText.sender(entry.raw), symbol: "person")
            OpsFieldRow(title: "消息内容", value: OpsFormat.text(entry.raw["text_preview"]), symbol: "text.quote")
            OpsFieldRow(title: "涉及插件", value: (entry.raw["plugin_keys"]?.arrayValue ?? []).compactMap(\.stringValue).joined(separator: "、"), symbol: "puzzlepiece.extension")
            OpsFieldRow(title: "动作 / 错误", value: "\(entry.raw["action_count"]?.intValue ?? 0) 个动作，\(entry.raw["error_count"]?.intValue ?? 0) 个错误", symbol: "bolt")
            OpsFieldRow(title: "耗时", value: OpsFormat.duration(milliseconds: entry.raw["duration_ms"]?.intValue), symbol: "timer")
            OpsFieldRow(title: "链路 ID", value: entry.traceID, symbol: "number", monospaced: true)
        case .audit:
            OpsFieldRow(title: "操作", value: LogText.auditAction(entry.raw["action"]?.stringValue ?? ""), symbol: "hand.tap")
            OpsFieldRow(title: "操作代码", value: entry.raw["action"]?.stringValue, symbol: "number", monospaced: true)
            OpsFieldRow(title: "操作对象", value: OpsFormat.text(entry.raw["target"]).map(LogText.auditTarget), symbol: "scope")
            OpsFieldRow(title: "操作人", value: entry.raw["user_id"]?.intValue.map { "管理员 \($0)" } ?? "系统自动", symbol: "person.badge.key")
            if let detail = entry.raw["detail"], detail.objectValue?.isEmpty == false {
                NavigationLink {
                    List { ManagementValueView(value: detail) }.navigationTitle("变更内容")
                } label: { Label("变更内容", systemImage: "list.bullet.clipboard") }
            }
        }
    }
}

// MARK: - 调用链时间线

private struct TraceStep: Identifiable {
    let id: String
    let date: Date?
    let title: String
    let detail: String?
    let error: String?
    let duration: String?
    let severity: OpsSeverity
    let symbol: String
}

struct LogTraceTimelineView: View {
    let traceID: String
    @Environment(AppModel.self) private var model
    @State private var trace: JSONValue?
    @State private var error: String?
    @State private var loading = false

    private var steps: [TraceStep] {
        guard let trace else { return [] }
        var result: [TraceStep] = []
        for (index, span) in (trace["spans"]?.arrayValue ?? []).enumerated() {
            let plugin = [OpsFormat.text(span["plugin_key"]), OpsFormat.text(span["entry_key"])].compactMap { $0 }.joined(separator: " › ")
            let status = LogText.traceStatus(span["status"]?.stringValue)
            result.append(TraceStep(
                id: "span-\(index)",
                date: OpsFormat.date(span["started_at"]),
                title: LogText.phase(span["phase"]?.stringValue) + (plugin.isEmpty ? "" : " · " + plugin),
                detail: [LogText.reason(span["reason_code"]?.stringValue), OpsFormat.text(span["message"])].compactMap { $0 }.joined(separator: "\n"),
                error: nil,
                duration: OpsFormat.duration(milliseconds: span["duration_ms"]?.intValue),
                severity: status.severity,
                symbol: status.severity == .success ? "checkmark.circle.fill" : status.severity.symbol))
        }
        for (index, action) in (trace["actions"]?.arrayValue ?? []).enumerated() {
            let status = LogText.traceStatus(action["status"]?.stringValue)
            let failed = OpsFormat.text(action["error_code"]) != nil || OpsFormat.text(action["error_message"]) != nil
            let target = action["target_chat_id"]?.intValue.map { "目标会话 \($0)" }
            result.append(TraceStep(
                id: "action-\(index)",
                date: OpsFormat.date(action["created_at"]),
                title: LogText.actionType(action["action_type"]?.stringValue) + (OpsFormat.text(action["plugin_key"]).map { " · " + $0 } ?? ""),
                detail: [target, LogText.channel(action["actual_send_via"]?.stringValue ?? action["requested_send_via"]?.stringValue).nilIfUnknown].compactMap { $0 }.joined(separator: " · "),
                error: [OpsFormat.text(action["error_code"]), OpsFormat.text(action["error_message"])].compactMap { $0 }.joined(separator: "：").nilIfEmpty,
                duration: nil,
                severity: failed ? .error : status.severity,
                symbol: failed ? OpsSeverity.error.symbol : "paperplane.circle.fill"))
        }
        return result.sorted { ($0.date ?? .distantPast) < ($1.date ?? .distantPast) }
    }

    var body: some View {
        List {
            if let error { Section { NoticeView(title: "调用链读取失败", message: error) } }
            if loading && trace == nil { Section { ProgressView("正在读取处理时间线…") } }
            if let trace {
                let status = LogText.traceStatus(trace["status"]?.stringValue)
                Section {
                    VStack(alignment: .leading, spacing: 6) {
                        OpsBadge(title: status.title, symbol: status.severity.symbol, color: status.severity.color)
                        Text("\(LogText.eventType(trace["event_type"]?.stringValue))，来自\(LogText.channel(trace["source_channel"]?.stringValue))")
                            .font(.headline)
                        if let preview = OpsFormat.text(trace["text_preview"]) {
                            Text("“\(preview)”").font(.subheadline).foregroundStyle(.secondary).lineLimit(4).textSelection(.enabled)
                        }
                    }
                    .padding(.vertical, 4)
                    OpsFieldRow(title: "会话", value: LogText.conversation(trace), symbol: "bubble.left.and.bubble.right")
                    OpsFieldRow(title: "发送者", value: LogText.sender(trace), symbol: "person")
                    OpsFieldRow(title: "开始时间", value: OpsFormat.fullTime(OpsFormat.date(trace["started_at"])), symbol: "clock")
                    OpsFieldRow(title: "总耗时", value: OpsFormat.duration(milliseconds: trace["duration_ms"]?.intValue), symbol: "timer")
                }
                Section {
                    let start = OpsFormat.date(trace["started_at"])
                    let items = steps
                    if items.isEmpty { Text("没有记录到处理步骤。").foregroundStyle(.secondary) }
                    ForEach(Array(items.enumerated()), id: \.element.id) { index, step in
                        TraceStepRow(step: step, offset: offset(step.date, from: start), isLast: index == items.count - 1)
                            .listRowSeparator(.hidden)
                    }
                } header: { Text("处理时间线") } footer: {
                    Text("从上到下按时间排列。“+120 毫秒”表示距离收到消息过了多久。红色步骤就是出问题的地方。")
                }
                let related = (trace["related_runtime_logs"]?.arrayValue ?? []).map(LogEntry.runtime)
                if !related.isEmpty {
                    Section("同一时间段的运行日志") {
                        ForEach(related) { entry in
                            NavigationLink { LogDetailView(entry: entry) } label: { LogEntryRow(entry: entry) }
                        }
                    }
                }
                Section {
                    Button { copy() } label: { Label("复制诊断信息", systemImage: "doc.on.doc") }
                    NavigationLink {
                        List { ManagementValueView(value: trace) }.navigationTitle("原始数据")
                    } label: { Label("原始数据（高级）", systemImage: "curlybraces") }
                }
            }
        }
        .navigationTitle("处理时间线")
        .navigationBarTitleDisplayMode(.inline)
        .task { if trace == nil { await load() } }
        .refreshable { await load() }
        .accessibilityIdentifier("logs.trace")
    }

    private func offset(_ date: Date?, from start: Date?) -> String? {
        guard let date, let start else { return nil }
        let ms = max(0, Int(date.timeIntervalSince(start) * 1_000))
        return "+" + (OpsFormat.duration(milliseconds: ms) ?? "0 毫秒")
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            trace = try await model.request(path: "/api/logs/trace/events/" + ManagementText.pathSegment(traceID))
            error = nil
        } catch { self.error = OpsFormat.errorMessage(error) }
    }

    private func copy() {
        guard let trace else { return }
        var lines = [
            "调用链 \(traceID)",
            "\(LogText.eventType(trace["event_type"]?.stringValue))，来自\(LogText.channel(trace["source_channel"]?.stringValue))，结果：\(LogText.traceStatus(trace["status"]?.stringValue).title)",
            "会话：\(LogText.conversation(trace) ?? "未知")"
        ]
        let start = OpsFormat.date(trace["started_at"])
        for step in steps {
            var line = "\(offset(step.date, from: start) ?? "") [\(step.severity.title)] \(step.title)"
            if let detail = step.detail, !detail.isEmpty { line += " — " + detail.replacingOccurrences(of: "\n", with: "；") }
            if let error = step.error { line += " — 错误：" + error }
            lines.append(line)
        }
        OpsFormat.copyForDiagnosis(lines)
    }
}

private struct TraceStepRow: View {
    let step: TraceStep
    let offset: String?
    let isLast: Bool

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            VStack(spacing: 0) {
                Image(systemName: step.symbol)
                    .foregroundStyle(step.severity.color)
                    .font(.body)
                    .accessibilityLabel(step.severity.title)
                if !isLast {
                    Rectangle().fill(Color.secondary.opacity(0.3)).frame(width: 2).frame(maxHeight: .infinity)
                }
            }
            .frame(width: 24)
            VStack(alignment: .leading, spacing: 3) {
                HStack(alignment: .firstTextBaseline) {
                    Text(step.title).font(.subheadline.weight(.medium))
                    Spacer(minLength: 6)
                    if let offset { Text(offset).font(.caption2).monospacedDigit().foregroundStyle(.secondary) }
                }
                if let detail = step.detail, !detail.isEmpty {
                    Text(detail).font(.caption).foregroundStyle(.secondary).textSelection(.enabled)
                }
                if let error = step.error {
                    Label(error, systemImage: "exclamationmark.triangle.fill").font(.caption).foregroundStyle(.red)
                }
                if let duration = step.duration {
                    Label("耗时 \(duration)", systemImage: "timer").font(.caption2).foregroundStyle(.secondary)
                }
            }
            .padding(.bottom, isLast ? 0 : 8)
        }
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
    var nilIfUnknown: String? { self == "未知来源" ? nil : self }
}
