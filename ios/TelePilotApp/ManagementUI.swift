import SwiftUI
import TelePilotCore

enum ManagementText {
    static let defaultActionExplanation = "修改会提交到当前连接的服务器。"
    /// 「登录设备」（第 20 轮前叫「设备配置」，接口仍是 /api/device-profiles）列表与编辑表单顶部的说明。
    static let deviceProfileExplanation = "登录 Telegram 时上报的设备信息，会显示在 Telegram「设置 › 设备」列表里，例如 iPhone 15 Pro · iOS 18。修改只对之后新登录的账号生效。"
    static let labels: [String: String] = [
        "name": "名称", "display_name": "显示名称", "title": "标题", "description": "说明",
        "enabled": "启用", "status": "状态", "state": "运行状态", "id": "编号", "key": "标识",
        "phone": "手机号", "notes": "备注", "tags": "标签", "config": "配置", "features": "功能",
        "api_id": "Telegram API ID", "api_hash": "Telegram API Hash", "api_key": "API 密钥",
        "api_keys": "API 密钥", "token": "Token", "bot_token": "Bot Token", "password": "密码",
        "proxy_id": "代理编号", "device_profile_id": "登录设备编号",
        "device_model": "机型", "system_version": "系统版本", "app_version": "App 版本", "lang_code": "语言代码",
        "system_lang_code": "系统语言代码", "is_default": "默认", "template_id": "模板编号",
        "account_id": "账号编号", "account_ids": "账号编号", "from_account_id": "来源账号编号",
        "feature_key": "插件", "plugin_key": "插件标识", "rule_ids": "规则编号", "target_account_ids": "目标账号编号",
        "priority": "优先级", "per_day": "每日上限", "per_hour": "每小时上限", "per_minute": "每分钟上限",
        "per_second": "每秒上限", "same_peer_per_minute": "同一会话每分钟上限", "action": "动作",
        "provider": "供应商类型", "base_url": "服务地址", "default_model": "默认模型", "model": "模型",
        "models": "模型列表", "api_format": "接口协议", "execution_backend": "执行方式", "cost_tier": "成本等级",
        "key_strategy": "密钥分配策略", "modality": "支持内容", "request_headers": "请求头",
        "protocol_profile": "协议配置", "client_identity_profile": "客户端身份", "web_search_api_format": "搜索协议",
        "label": "显示名称", "daily_request_limit": "每日请求上限", "daily_token_limit": "每日 Token 上限",
        "source": "来源", "source_label": "来源名称", "source_url": "来源地址", "url": "地址",
        "version": "版本", "latest_version": "最新版本", "signature_ok": "签名验证", "last_error": "最近错误",
        "last_update_check_error": "检查更新错误", "last_update_check_at": "上次更新检查", "update_available": "有可用更新",
        "orphan": "插件来源缺失", "is_builtin": "内置插件", "experimental": "实验功能", "category": "分类",
        "runtime_availability": "运行可用性", "permissions": "权限", "usage": "使用说明",
        "message": "消息", "text": "文本", "content": "内容", "output": "输出", "detail": "详情",
        "created_at": "创建时间", "updated_at": "更新时间", "level": "等级", "event_type": "事件类型",
        "chat_id": "会话 ID", "chat_ids": "会话 ID", "user_id": "用户 ID", "username": "用户名",
        "peer_id": "对象 ID", "peer_type": "对象类型", "amount": "金额", "currency": "币种",
        "total": "合计", "items": "记录", "rows": "记录", "entries": "明细", "summary": "汇总",
        "compensations": "补偿记录", "reason": "原因", "paid_at": "支付时间", "manual_paid": "人工支付",
        "confirmation": "确认口令", "include_sensitive": "包含敏感配置", "categories": "导出类别",
        "totp_code": "验证器验证码", "old_password": "当前密码", "new_password": "新密码", "code": "验证码",
        "recovery_code": "恢复码", "recovery_codes": "恢复码", "secret": "验证器密钥", "otpauth_url": "验证器设置地址",
        "notify_otp_enabled": "通知验证码", "totp_enabled": "验证器保护", "totp_mode": "验证器模式",
        "sample_message": "模拟消息", "sample_chat_id": "模拟会话 ID", "sample_chat_type": "模拟会话类型",
        "matched": "规则命中", "rendered": "渲染结果", "template": "模板", "variables": "变量",
        "schedule": "执行计划", "cron": "Cron 时间表达式", "timezone": "时区", "command": "指令",
        "trigger": "触发方式", "trigger_text": "触发词", "response_template": "回复模板", "parse_mode": "文本格式",
        "last_run_at": "上次执行", "next_run_at": "下次执行", "timeout_seconds": "超时秒数",
        "progress": "进度", "result": "结果", "error": "错误", "errors": "错误", "warnings": "提示",
        "key_hint": "密钥尾号", "health": "健康状态", "today_requests": "今日请求", "today_tokens": "今日 Token",
        "host": "主机", "port": "端口", "scheme": "协议", "type": "类型", "default_enabled": "安装后默认启用",
        "replace_existing": "替换已有插件", "repo_id": "仓库编号", "branch": "分支", "ref": "版本引用",
        "rules": "规则", "mode": "模式", "policy": "处理策略", "strict": "严格限流", "config_actions": "配置操作",
        "successful": "成功", "failed": "失败", "running": "运行中", "paused": "已暂停", "active": "已启用",
        "pending": "待处理", "completed": "已完成", "cancelled": "已取消", "healthy": "正常", "cooling": "冷却中",
        "ready": "就绪", "partial": "部分可用", "transitioning": "切换中", "true": "是", "false": "否",
        "kind": "触发方式", "fire_at": "单次触发时间", "interval_sec": "间隔秒数", "allowed_command_whitelist": "自动指令白名单",
        "send_message": "发送消息", "run_command": "执行指令", "call_llm": "调用模型", "agent_prompt": "交给助手",
        "once": "单次", "interval": "固定间隔", "target_chat_id": "目标会话",
        "provider_id": "供应商编号", "prompt": "提示词", "system_prompt": "系统提示词", "max_tokens": "最大输出 Token",
        "delete_after": "发送后自动删除秒数", "patterns": "匹配内容", "match": "匹配方式", "scope": "作用范围",
        "reply": "回复内容", "cooldown_seconds": "冷却秒数", "cooldown_scope": "冷却范围", "group_ids": "群组 ID",
        "source_kind": "来源匹配", "source_peers": "来源会话", "keyword": "关键词",
        "duplicate_window": "重复检测窗口秒数", "duplicate_threshold": "不同用户数阈值", "include_media": "包含媒体", "header": "消息前缀",
        "forward_native": "原生转发", "copy_text": "复制文本", "quote": "引用包装", "link_only": "仅发送链接",
        "date-time": "时间", "since": "起始时间", "until": "结束时间", "limit": "最多读取条数", "offset": "跳过条数", "page": "页码"
    ]

    static func label(_ key: String) -> String { labels[key] ?? key.replacingOccurrences(of: "_", with: " ") }
    static func fieldLabel(_ key: String, schema: JSONValue) -> String {
        if let label = labels[key] { return label }
        if let title = schema["title"]?.stringValue, title.unicodeScalars.contains(where: { $0.value >= 0x4E00 && $0.value <= 0x9FFF }) { return title }
        return label(key)
    }
    static func accountName(_ account: Account) -> String {
        account.displayName.flatMap { $0.isEmpty ? nil : $0 } ?? "账号 \(account.id)"
    }
    static func scalar(_ value: JSONValue) -> String {
        if let text = value.stringValue { return labels[text] ?? AssistantRedaction.text(text) }
        if let value = value.boolValue { return value ? "是" : "否" }
        if let value = value.intValue { return String(value) }
        if let value = value.doubleValue { return String(value) }
        if let values = value.arrayValue { return "\(values.count) 项" }
        if let values = value.objectValue { return "\(values.count) 项" }
        return "未设置"
    }
    static func rawScalar(_ value: JSONValue) -> String {
        if let text = value.stringValue { return text }
        if let bool = value.boolValue { return bool ? "true" : "false" }
        if let integer = value.intValue { return String(integer) }
        if let number = value.doubleValue { return String(number) }
        return ""
    }
    static func secret(_ key: String) -> Bool {
        let key = key.lowercased().replacingOccurrences(of: "-", with: "_")
        return ["password", "token", "secret", "api_key", "api_hash", "credential", "authorization", "recovery_code", "otpauth", "private_key", "access_key"].contains { key.contains($0) }
            && !["has_", "_hint", "_count", "_limit", "_tokens", "_enabled", "_ttl", "_attempt"].contains { key.contains($0) }
    }
    static func title(_ value: JSONValue) -> String {
        for key in ["display_name", "name", "label", "title", "feature_key", "plugin_key", "key", "phone", "action", "event_type", "id"] {
            if let v = value[key], !v.isNull { return scalar(v) }
        }
        return value.objectValue == nil ? scalar(value) : "记录详情"
    }
    static func items(_ value: JSONValue, key: String? = nil) -> [JSONValue] {
        if let key { return value[key]?.arrayValue ?? [] }
        if let array = value.arrayValue { return array }
        for key in ["items", "rows", "entries", "logs", "providers", "templates", "plugins", "keys", "compensations", "jobs", "results", "events"] {
            if let array = value[key]?.arrayValue { return array }
        }
        return []
    }
    static func sanitizedExport(_ value: JSONValue) -> JSONValue {
        switch value {
        case .object(let object):
            let secretHeader = secret(object["name"]?.stringValue ?? "")
            return .object(object.filter { !secret($0.key) && $0.key != "preview_signature" && !(secretHeader && $0.key == "value") }.mapValues(sanitizedExport))
        case .array(let items): return .array(items.map(sanitizedExport))
        case .string(let text): return .string(AssistantRedaction.text(text))
        default: return value
        }
    }
    static func pathSegment(_ value: String) -> String {
        value.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-_~."))) ?? ""
    }
}

struct ManagementRecordRow: View {
    let value: JSONValue
    var body: some View {
        VStack(alignment: .leading, spacing: 5) {
            Text(ManagementText.title(value)).font(.headline).lineLimit(2)
            if let status = value["status"] ?? value["state"] ?? value["runtime_availability"] {
                Label(ManagementText.scalar(status), systemImage: "circle.fill").font(.caption).foregroundStyle(.secondary)
            } else if let enabled = value["enabled"]?.boolValue {
                Label(enabled ? "已启用" : "已停用", systemImage: enabled ? "checkmark.circle" : "pause.circle").font(.caption).foregroundStyle(.secondary)
            }
            if let detail = value["message"] ?? value["notes"] ?? value["description"] {
                Text(ManagementText.scalar(detail)).font(.subheadline).foregroundStyle(.secondary).lineLimit(3)
            }
            if let error = value["last_error"], !error.isNull, error.stringValue != "" {
                Text(ManagementText.scalar(error)).font(.caption).foregroundStyle(.red).lineLimit(3)
            }
        }.padding(.vertical, 3)
    }
}

struct ManagementValueView: View {
    let value: JSONValue
    var key: String = ""

    var body: some View {
        Group {
            if ManagementText.secret(key), !value.isNull {
                SensitiveText(value: value.arrayValue?.map(ManagementText.rawScalar).joined(separator: "\n") ?? ManagementText.rawScalar(value))
            } else if let object = value.objectValue {
                ForEach(object.keys.sorted(), id: \.self) { field in
                    if let fieldValue = object[field], !fieldValue.isNull, field != "preview_signature" {
                        if fieldValue.objectValue != nil || fieldValue.arrayValue != nil {
                            NavigationLink {
                                List { ManagementValueView(value: fieldValue, key: field) }.navigationTitle(ManagementText.label(field)).navigationBarTitleDisplayMode(.inline)
                            } label: {
                                LabeledContent(ManagementText.label(field), value: ManagementText.scalar(fieldValue))
                            }
                        } else if ManagementText.secret(field) || (field == "value" && ManagementText.secret(object["name"]?.stringValue ?? "")) {
                            VStack(alignment: .leading) {
                                Text(ManagementText.label(field)).font(.caption).foregroundStyle(.secondary)
                                ManagementValueView(value: fieldValue, key: field == "value" ? (object["name"]?.stringValue ?? field) : field)
                            }
                        } else {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(ManagementText.label(field)).font(.caption).foregroundStyle(.secondary)
                                Text(ManagementText.scalar(fieldValue)).textSelection(.enabled)
                            }
                        }
                    }
                }
            } else if let array = value.arrayValue {
                if array.isEmpty { Text("暂无记录").foregroundStyle(.secondary) }
                ForEach(Array(array.enumerated()), id: \.offset) { index, item in
                    if item.objectValue != nil || item.arrayValue != nil {
                        NavigationLink {
                            List { ManagementValueView(value: item) }.navigationTitle(ManagementText.title(item)).navigationBarTitleDisplayMode(.inline)
                        } label: { ManagementRecordRow(value: item) }
                    } else { Text(ManagementText.scalar(item)).textSelection(.enabled) }
                }
            } else { Text(ManagementText.scalar(value)).textSelection(.enabled) }
        }
    }
}

struct ManagementAction: Identifiable, Hashable {
    let title: String
    let path: String
    var method: String = "POST"
    var destructive = false
    var explanation: String = ManagementText.defaultActionExplanation
    var schema: JSONValue? = nil
    var seed: JSONValue? = nil
    var envelope: String? = nil
    var supplementalBody: [String: JSONValue] = [:]
    var id: String { method + path + title }
}

struct ManagementPluginLookupView: View {
    let key: String
    @Environment(AppModel.self) private var model
    @State private var feature: JSONValue?
    @State private var error: String?
    var body: some View {
        Group {
            if let feature { ManagementPluginDetailView(feature: feature) }
            else {
                List {
                    if let error { NoticeView(title: "插件读取失败", message: error) }
                    else { ProgressView("正在读取插件…") }
                }.navigationTitle("插件详情").navigationBarTitleDisplayMode(.inline)
            }
        }.task {
            do {
                let value = try await model.request(path: "/api/feature-matrix")
                feature = value["features"]?.arrayValue?.first { $0["key"]?.stringValue == key }
                if feature == nil { error = "当前服务器没有找到这个插件，请刷新插件目录。" }
            } catch { self.error = error.localizedDescription }
        }
    }
}

struct ManagementActionLink: View {
    let action: ManagementAction
    /// 未指定时按操作类型取默认图标：危险操作 / 修改 / 执行。
    var symbol: String? = nil
    var body: some View {
        NavigationLink { ManagementFormView(action: action) } label: {
            // 文字与图标分开着色：普通操作文字用主色、图标沿用强调色（与同组 collection／NavigationLink 行一致）；
            // 此前整个 Label 设 Color.primary，图标也被染成白色（「修改管理员密码」等行与同组橙色图标不一致）。
            Label {
                Text(action.title).foregroundStyle(action.destructive ? Color.red : Color.primary)
            } icon: {
                Image(systemName: symbol ?? Self.defaultSymbol(action))
                    .foregroundStyle(action.destructive ? Color.red : Color.accentColor)
            }
        }
    }
    static func defaultSymbol(_ action: ManagementAction) -> String {
        if action.destructive || action.method == "DELETE" { return "exclamationmark.triangle" }
        if ["PATCH", "PUT"].contains(action.method) { return "square.and.pencil" }
        return "play.circle"
    }
}

struct ManagementDocumentView: View {
    let title: String
    let path: String
    var actions: [ManagementAction] = []
    @Environment(AppModel.self) private var model
    @State private var value: JSONValue?
    @State private var error: String?
    @State private var busy = false
    @State private var exporting = false
    @State private var exportedFile = ManagementFile(data: Data())

    var body: some View {
        List {
            if busy && value == nil { ProgressView("正在读取…") }
            if let error { NoticeView(title: "读取失败", message: error) }
            if let value { ManagementValueView(value: value) }
            if !actions.isEmpty {
                Section("操作") {
                    ForEach(actions) { action in
                        ManagementActionLink(action: withSeed(action, value: value))
                    }
                }
            }
        }
        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
        .task { await load() }.refreshable { await load() }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("导出记录", systemImage: "square.and.arrow.up") {
                    guard let value, let data = try? ManagementText.sanitizedExport(value).data() else { return }
                    exportedFile = ManagementFile(data: data)
                    exporting = true
                }.disabled(value == nil)
            }
        }
        .fileExporter(isPresented: $exporting, document: exportedFile, contentType: .json, defaultFilename: "TelePilot-\(title)") { result in
            if case .failure(let failure) = result { error = failure.localizedDescription }
        }
    }

    private func withSeed(_ action: ManagementAction, value: JSONValue?) -> ManagementAction {
        var result = action
        if ["PATCH", "PUT"].contains(action.method), result.seed == nil { result.seed = value }
        return result
    }
    private func load() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do { value = try await model.request(path: path); error = nil }
        catch { self.error = error.localizedDescription }
    }
}

/// 账号页 / 插件页「问 Agent」（第 22 轮，对齐 Web #118 的 `AskAgentButton`）：切到 Agent 标签页，新建关联当前账号的会话；
/// 插件页再把插件名预填进输入栏。服务器关闭 AI 模块时不显示。
struct AskAgentButton: View {
    @Environment(AppModel.self) private var model
    let accountID: Int?
    let source: AgentAskSource
    var pluginName: String? = nil
    var pluginKey: String? = nil
    var identifier = "askAgent"
    /// 放在导航栏时只显示图标（系统工具栏样式），无障碍标签仍是「问 Agent」。
    var inToolbar = false

    var body: some View {
        if model.canAskAgent {
            if inToolbar {
                Button("问 Agent", systemImage: "sparkles", action: ask)
                    .accessibilityIdentifier(identifier)
                    .accessibilityHint("打开 Agent 并新建会话，插件名会填进输入栏")
            } else {
                Button(action: ask) {
                    Label("问 Agent", systemImage: "sparkles")
                        .font(.subheadline.weight(.medium))
                        .frame(minHeight: 32)
                }
                .buttonStyle(.bordered)
                .accessibilityIdentifier(identifier)
                .accessibilityHint("打开 Agent 并新建关联当前账号的会话")
            }
        }
    }

    private func ask() {
        model.askAgent(AgentAskRequest(accountID: accountID, source: source, pluginName: pluginName, pluginKey: pluginKey))
    }
}
