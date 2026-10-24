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
         "timeout": "超时", "upstream_error": "上游错误", "gateway_unavailable": "Gateway 不可用",
         "gateway_overloaded": "Gateway 过载", "invalid_response": "响应无效",
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
        return ["command": "AI 指令", "agent": "Agent", "assistant": "Agent"][source] ?? source
    }

    static func tokens(_ value: Int) -> String {
        value.formatted(.number.grouping(.automatic))
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
