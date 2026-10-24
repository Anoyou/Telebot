import SwiftUI
import TelePilotCore

enum AIPresentation {
    static func ready(_ provider: JSONValue) -> Bool {
        provider["has_effective_key"]?.boolValue == true || provider["provider"]?.stringValue == "ollama"
    }

    static func enabledModels(_ provider: JSONValue) -> [String] {
        (provider["models"]?.arrayValue ?? []).compactMap { model in
            model["enabled"]?.boolValue == true ? model["id"]?.stringValue : nil
        }
    }

    static func providerName(_ provider: JSONValue) -> String {
        provider["name"]?.stringValue ?? "未命名供应商"
    }

    static func health(_ provider: JSONValue, in rows: [JSONValue]) -> [JSONValue] {
        guard let id = provider["id"]?.intValue else { return [] }
        return rows.filter { $0["provider_id"]?.intValue == id }
    }

    static func hasIssue(_ row: JSONValue) -> Bool {
        let state = row["state"]?.stringValue ?? "unknown"
        return !["healthy", "normal", "ready"].contains(state)
            || (row["cooldown_remaining_seconds"]?.intValue ?? 0) > 0
    }

    static func runtimeState(_ row: JSONValue) -> String {
        if let seconds = row["cooldown_remaining_seconds"]?.intValue, seconds > 0 {
            return "冷却中 · \(duration(seconds))"
        }
        return ["healthy": "运行正常", "normal": "运行正常", "ready": "运行正常",
                "degraded": "运行降级", "cooling": "冷却中", "failed": "调用失败",
                "unavailable": "暂不可用"][row["state"]?.stringValue ?? ""]
            ?? "状态 " + (row["state"]?.stringValue ?? "未知")
    }

    static func status(_ status: String) -> String {
        ["healthy": "正常", "empty_response": "空响应", "rate_limited": "限流",
         "auth_failed": "鉴权失败", "permission_denied": "权限不足", "client_rejected": "客户端受限",
         "official_account_required": "需要官方账号", "account_policy": "账号策略限制",
         "protocol_rejected": "协议不匹配", "model_missing": "模型不可用", "endpoint_missing": "接口不存在",
         "request_invalid": "请求无效", "context_limit": "超出上下文", "quota_exhausted": "额度耗尽",
         "timeout": "超时", "upstream_error": "上游错误", "gateway_unavailable": "内置网关不可用",
         "gateway_overloaded": "内置网关过载", "invalid_response": "响应无效", "capability_mismatch": "缺少所需能力",
         "no_enabled_models": "没有启用的模型", "unknown": "未知",
         "skipped_disabled": "已跳过", "skipped_provider_missing": "供应商已删除",
         "config_error": "配置错误", "cancelled": "已取消", "network_error": "网络错误",
         "queued": "排队中", "running": "测活中", "completed": "已完成"][status] ?? status
    }

    static func statusColor(_ status: String) -> Color {
        if status == "healthy" { return .green }
        if ["rate_limited", "timeout", "quota_exhausted", "gateway_unavailable", "gateway_overloaded", "empty_response"].contains(status) { return .orange }
        if status.hasPrefix("skipped") || status == "cancelled" { return .secondary }
        return .red
    }

    static func usageSource(_ source: String?) -> String {
        guard let source, !source.isEmpty else { return "未记录来源" }
        if source.hasPrefix("plugin:") { return "插件 · " + source.dropFirst(7) }
        if source.hasPrefix("diagnostic:") { return "模型诊断" }
        return ["command": "AI 指令", "agent": "助手", "assistant": "助手", "system_agent": "助手"][source] ?? source
    }

    static func tokens(_ value: Int) -> String {
        value.formatted(.number.grouping(.automatic))
    }

    /// 毫秒耗时转中文：小于 1 秒显示毫秒，否则显示一位小数的秒。
    static func latency(_ milliseconds: Int?) -> String {
        guard let milliseconds, milliseconds > 0 else { return "未记录" }
        if milliseconds < 1000 { return "\(milliseconds) 毫秒" }
        return (Double(milliseconds) / 1000).formatted(.number.precision(.fractionLength(1))) + " 秒"
    }

    // MARK: - 术语中文化

    /// 优先使用中文映射；服务器返回了未知新值时原样显示，缺失时使用默认说法。
    private static func pick(_ map: [String: String], _ raw: String?, _ fallback: String) -> String {
        guard let raw, !raw.isEmpty else { return fallback }
        return map[raw] ?? raw
    }

    static func providerType(_ raw: String?) -> String {
        pick(["openai": "OpenAI 兼容接口", "anthropic": "Anthropic（Claude）", "ollama": "Ollama 本地模型"], raw, "未知类型")
    }

    static func apiFormat(_ raw: String?) -> String {
        pick(["chat_completions": "对话补全接口（Chat Completions）", "responses": "新版响应接口（Responses）",
         "anthropic_messages": "Claude 消息接口（Messages）", "auto": "自动选择"], raw, "对话补全接口（Chat Completions）")
    }

    static func executionBackend(_ raw: String?) -> String {
        pick(["direct": "标准 API 直连", "codex_gateway": "Codex 兼容模式（内置网关）"], raw, "标准 API 直连")
    }

    static func protocolProfile(_ raw: String?) -> String {
        pick(["standard": "标准协议", "openai_responses": "OpenAI 响应接口", "deepseek_responses": "DeepSeek 响应接口",
         "codex_responses": "Codex 响应接口", "claude_code_proxy": "Claude Code 代理"], raw, "标准协议")
    }

    static func clientIdentity(_ raw: String?) -> String {
        pick(["auto": "自动选择", "minimal": "最小身份（不模拟客户端）", "openai_sdk": "OpenAI SDK（标准 API）",
         "codex_tui": "Codex 命令行客户端", "codex_cli": "Codex 命令行客户端（旧配置）", "codex_exec": "Codex 命令行客户端（旧配置）",
         "codex_desktop": "Codex 桌面版", "claude_code": "Claude Code 命令行", "claude_desktop": "Claude Code 命令行（旧桌面配置）",
         "grok_cli": "Grok 命令行"], raw, "自动选择")
    }

    static func modality(_ raw: String?) -> String {
        pick(["text": "纯文本", "vision": "图文理解", "audio": "语音", "multimodal": "多模态（图片、语音等）"], raw, "纯文本")
    }

    static func costTier(_ tier: Int?) -> String {
        switch tier ?? 2 {
        case ...1: "经济档（便宜、适合量大）"
        case 2: "标准档"
        default: "旗舰档（贵、质量高）"
        }
    }

    static func tag(_ raw: String) -> String {
        ["chat": "通用问答", "code": "代码", "math": "数学", "translate": "翻译", "vision": "看图识图",
         "long_context": "长上下文", "reason": "复杂推理", "smart": "高质量", "cheap": "低成本", "fast": "低延迟",
         "classify": "路由分类"][raw] ?? raw
    }

    static func commandMode(_ raw: String?) -> String {
        ["chat": "对话", "search": "联网搜索", "image": "生成图片", "video": "生成视频"][raw ?? "chat"] ?? (raw ?? "对话")
    }

    static func outputFormat(_ raw: String?) -> String {
        ["html": "富文本（HTML）", "markdown": "Markdown", "plain": "纯文本"][raw ?? "html"] ?? (raw ?? "富文本（HTML）")
    }

    static func duration(_ seconds: Int) -> String {
        if seconds < 60 { return "\(seconds) 秒" }
        if seconds < 3600 { return "\(seconds / 60) 分钟" }
        return "\(seconds / 3600) 小时 \((seconds % 3600) / 60) 分钟"
    }

    static func date(_ raw: String?) -> String {
        guard let raw else { return "未记录" }
        let fractional = ISO8601DateFormatter()
        fractional.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let plain = ISO8601DateFormatter()
        guard let date = fractional.date(from: raw) ?? plain.date(from: raw) else { return raw }
        return date.formatted(date: .abbreviated, time: .shortened)
    }
}

/// AI 区域统一术语表，供“术语说明”页和各处说明按钮复用。
enum AIGlossary {
    struct Term: Identifiable, Hashable {
        let title: String
        let original: String
        let detail: String
        var id: String { title }
    }

    static let terms: [Term] = [
        .init(title: "供应商", original: "Provider", detail: "提供模型的服务方，例如 OpenAI、Anthropic、各类中转站或本机的 Ollama。一个供应商下可以有多个模型。"),
        .init(title: "模型", original: "Model", detail: "真正回答问题的 AI，例如 gpt-5.4-mini、claude-haiku。只有在模型清单里“启用”的模型才能被指令和测活使用。"),
        .init(title: "词元", original: "Token", detail: "模型计量文本长度和费用的单位。一个汉字通常约占 1～2 个词元，一个英文单词约占 1 个词元。输入和输出都会计费。"),
        .init(title: "密钥 / 密钥池", original: "API Key / Key Pool", detail: "调用供应商时使用的凭据。密钥池可以放多把密钥轮流使用，分摊限额。"),
        .init(title: "测活", original: "Liveness Check", detail: "向模型发送一句真实的测试语，确认它现在能否正常回复，并记录耗时和错误。会产生少量真实费用。"),
        .init(title: "冷却", original: "Cooldown", detail: "某个模型连续失败后，系统会让它暂停一段时间不再被调用，避免持续报错。冷却结束后自动恢复。"),
        .init(title: "备用模型", original: "Fallback", detail: "首选模型失败时自动改用的另一个模型，保证指令尽量有回复。"),
        .init(title: "自动路由", original: "Auto Routing", detail: "AI 指令不固定用一个模型，而是先判断问题类型（如写代码、翻译、闲聊），再挑选合适且成本合理的模型。"),
        .init(title: "上下文", original: "Context", detail: "随问题一起发给模型的聊天记录。带上前几条消息，模型更能理解“它”“上面那个”指的是什么，但会多消耗词元。"),
        .init(title: "系统提示词", original: "System Prompt", detail: "预先告诉模型“你是谁、该怎么回答”的说明文字，用户看不到，但会影响回答风格。"),
        .init(title: "接口协议", original: "API Format", detail: "TelePilot 与供应商通信使用的接口格式：对话补全接口（Chat Completions）最通用；新版响应接口（Responses）支持联网搜索等能力；Claude 消息接口（Messages）用于 Anthropic。"),
        .init(title: "客户端身份", original: "Client Identity", detail: "请求里声明的“我是哪个客户端”。部分中转站只接受特定官方客户端（如 Codex、Claude Code）的请求，此时需要模拟对应身份。"),
        .init(title: "内置网关", original: "Gateway", detail: "TelePilot 自带的转发组件，用于以 Codex 客户端兼容方式调用模型。普通供应商使用“标准 API 直连”即可。"),
        .init(title: "指令别名", original: "Alias", detail: "同一条指令的简短叫法。例如给 ,ai 设置别名 a 后，发送 ,a 你好 与 ,ai 你好 效果相同。"),
        .init(title: "模态", original: "Modality", detail: "模型能处理的内容类型：纯文本、图文理解、语音或多模态。"),
        .init(title: "成本等级", original: "Cost Tier", detail: "经济档、标准档、旗舰档。自动路由会参考它，在质量和费用之间取舍；预算中的“高价模型”指旗舰档。")
    ]
}

/// 小号“i”按钮：点按弹出一段解释，避免把长说明堆在列表里。
struct AIHelpButton: View {
    let title: String
    let message: String
    @State private var showing = false

    var body: some View {
        Button {
            showing = true
        } label: {
            Image(systemName: "info.circle")
                .imageScale(.medium)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(Rectangle())
        }
        .buttonStyle(.borderless)
        .accessibilityLabel("\(title)的说明")
        .popover(isPresented: $showing) {
            VStack(alignment: .leading, spacing: 8) {
                Text(title).font(.headline)
                Text(message).font(.subheadline).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .padding()
            .frame(idealWidth: 320, alignment: .leading)
            .presentationCompactAdaptation(.popover)
        }
    }
}

/// 带说明副标题的导航行。
struct AINavigationRow: View {
    let title: String
    let detail: String
    let symbol: String

    var body: some View {
        Label {
            VStack(alignment: .leading, spacing: 3) {
                Text(title)
                Text(detail).font(.caption).foregroundStyle(.secondary).lineLimit(3)
            }
        } icon: {
            Image(systemName: symbol)
        }
    }
}

struct AIGlossaryView: View {
    var body: some View {
        List {
            Section {
                Text("AI 管理里出现的专业名词都在这里用大白话解释。括号里是对应的英文原名，方便和供应商文档对照。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            Section {
                ForEach(AIGlossary.terms) { term in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(term.title).font(.headline)
                            Text(term.original).font(.caption).foregroundStyle(.secondary)
                        }
                        Text(term.detail).font(.subheadline).foregroundStyle(.secondary)
                    }
                    .padding(.vertical, 2)
                    .accessibilityElement(children: .combine)
                }
            }
        }
        .navigationTitle("AI 术语说明").navigationBarTitleDisplayMode(.inline)
    }
}

struct AIStatusLabel: View {
    let title: String
    var symbol = "circle.fill"
    var color: Color = .secondary

    var body: some View {
        Label(title, systemImage: symbol)
            .font(.caption.weight(.medium))
            .foregroundStyle(color)
            .labelStyle(.titleAndIcon)
    }
}

struct AIMetric: View {
    let title: String
    let value: String
    var detail: String?
    var color: Color = .primary

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            Text(title).font(.caption).foregroundStyle(.secondary)
            Text(value).font(.title3.weight(.semibold)).monospacedDigit().foregroundStyle(color)
                .minimumScaleFactor(0.75).lineLimit(1)
            if let detail { Text(detail).font(.caption2).foregroundStyle(.secondary).lineLimit(2) }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .accessibilityElement(children: .combine)
    }
}
