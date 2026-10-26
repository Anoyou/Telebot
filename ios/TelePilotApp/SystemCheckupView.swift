import SwiftUI
import TelePilotCore

/// 一键体检：概览导航栏右上角 `stethoscope` 打开。
///
/// 直接调用服务器 `GET /api/system-agent/checkup`（与助手“体检一下”同一个只读工具
/// `diagnostics.system_checkup`），不经过模型、不修改任何配置；约 25 秒内顺序检查 10 项，
/// 结果显示总体结论、每项状态、原因与修复建议，并给出可跳转的处理入口。
struct SystemCheckupView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var result: SystemCheckupResult?
    @State private var running = false
    @State private var error: String?

    var body: some View {
        NavigationStack {
            List {
                if running && result == nil {
                    Section {
                        VStack(alignment: .leading, spacing: 10) {
                            ProgressView("正在体检…")
                            Text("顺序检查基础设施、版本、总闸、账号与 Worker、错误日志、模型供应商、系统助手、定时任务、插件、通知与推送，最长约 25 秒。只读，不会修改任何配置。")
                                .font(.footnote).foregroundStyle(.secondary)
                                .fixedSize(horizontal: false, vertical: true)
                        }
                        .padding(.vertical, 6)
                        .accessibilityIdentifier("checkup.running")
                    }
                }
                if let error {
                    Section {
                        NoticeView(title: "体检未能完成", message: error)
                        Button("重新体检", systemImage: "arrow.clockwise") { Task { await run() } }
                            .disabled(running)
                    }
                }
                if let result {
                    summarySection(result)
                    if !result.nextSteps.isEmpty { nextStepsSection(result) }
                    Section {
                        ForEach(result.sections) { section in
                            NavigationLink {
                                SystemCheckupSectionDetail(section: section, open: open)
                            } label: {
                                SystemCheckupSectionRow(section: section)
                            }
                            .accessibilityIdentifier("checkup.section.\(section.key)")
                        }
                    } header: { Text("各项检查") } footer: {
                        Text(result.note ?? "只读体检，未修改任何配置、未调用上游模型。")
                    }
                }
            }
            .navigationTitle("一键体检")
            .navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .confirmationAction) {
                    Button("完成") { dismiss() }
                }
                ToolbarItem(placement: .topBarLeading) {
                    Button("重新体检", systemImage: "arrow.clockwise") { Task { await run() } }
                        .disabled(running)
                        .accessibilityIdentifier("checkup.rerun")
                }
            }
            .task { if result == nil && !running { await run() } }
            .symbolRenderingMode(.hierarchical)
            .accessibilityIdentifier("checkup.screen")
        }
    }

    // MARK: 结论

    private func summarySection(_ result: SystemCheckupResult) -> some View {
        Section {
            VStack(alignment: .leading, spacing: 8) {
                Label(result.verdict.headline, systemImage: result.verdict.symbol)
                    .font(.title3.weight(.semibold))
                    .foregroundStyle(result.verdict.color)
                Text(result.verdictText)
                    .font(.subheadline).foregroundStyle(.secondary)
                    .fixedSize(horizontal: false, vertical: true)
                HStack(spacing: 12) {
                    countBadge(result.counts.ok, title: "正常", status: .ok)
                    countBadge(result.counts.warn, title: "需关注", status: .warn)
                    countBadge(result.counts.error, title: "异常", status: .error)
                    countBadge(result.counts.unknown, title: "未能检查", status: .unknown)
                }
                .font(.caption)
                if running {
                    ProgressView("正在重新体检…").font(.footnote)
                }
            }
            .padding(.vertical, 4)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("checkup.verdict")
        } footer: {
            Text("检查时间 \(result.checkedAtText) · 统计最近 \(result.windowHours) 小时 · 用时 \(AIPresentation.latency(result.elapsedMS))")
        }
    }

    private func countBadge(_ count: Int, title: String, status: SystemCheckupStatus) -> some View {
        Label("\(title) \(count)", systemImage: status.symbol)
            .foregroundStyle(count > 0 ? status.color : Color.secondary)
            .monospacedDigit()
            .lineLimit(1)
    }

    private func nextStepsSection(_ result: SystemCheckupResult) -> some View {
        Section {
            ForEach(result.nextSteps) { step in
                SystemCheckupStepRow(step: step, open: open)
            }
        } header: { Text("建议下一步") } footer: {
            Text("标注“需确认”的处理会修改配置或重启服务：可以前往对应页面操作，或交给助手执行（执行前会请求你确认）。")
        }
    }

    // MARK: 动作

    private func open(_ route: AppRoute) {
        dismiss()
        model.requestedRoute = route
    }

    private func run() async {
        guard !running else { return }
        running = true
        defer { running = false }
        do {
            let value = try await model.request(path: "/api/system-agent/checkup")
            result = SystemCheckupResult(value)
            error = nil
        } catch is CancellationError {
            return
        } catch {
            self.error = error.localizedDescription
        }
    }
}

// MARK: - 模型

enum SystemCheckupStatus: String, Sendable {
    case ok, warn, error, unknown

    init(_ raw: String?) { self = SystemCheckupStatus(rawValue: raw ?? "") ?? .unknown }

    var title: String {
        switch self {
        case .ok: "正常"
        case .warn: "需关注"
        case .error: "异常"
        case .unknown: "未能检查"
        }
    }
    var headline: String {
        switch self {
        case .ok: "未发现问题"
        case .warn: "基本可用，有需关注项"
        case .error: "发现异常"
        case .unknown: "无法完成体检"
        }
    }
    var symbol: String {
        switch self {
        case .ok: "checkmark.circle.fill"
        case .warn: "exclamationmark.triangle.fill"
        case .error: "xmark.octagon.fill"
        case .unknown: "questionmark.circle"
        }
    }
    var color: Color {
        switch self {
        case .ok: .green
        case .warn: .orange
        case .error: .red
        case .unknown: .secondary
        }
    }
}

struct SystemCheckupStep: Identifiable, Sendable {
    let id: String
    let severity: SystemCheckupStatus
    let text: String
    let tool: String?
    let requiresConfirmation: Bool
    let section: String?

    init(_ value: JSONValue, index: Int, section fallback: String?) {
        severity = SystemCheckupStatus(value["severity"]?.stringValue)
        text = value["text"]?.stringValue ?? ""
        tool = value["tool"]?.stringValue
        requiresConfirmation = value["requires_confirmation"]?.boolValue == true
        let owner = value["section"]?.stringValue ?? fallback
        section = owner
        id = "\(owner ?? "-")-\(index)"
    }

    /// 可跳转的处理入口：优先按建议使用的助手工具判断，其次按所属检查项。
    var route: (title: String, route: AppRoute)? {
        SystemCheckupRoutes.route(tool: tool, section: section)
    }
}

struct SystemCheckupSection: Identifiable, Sendable {
    let key: String
    let title: String
    let status: SystemCheckupStatus
    let finding: String
    let reason: String?
    let steps: [SystemCheckupStep]
    var id: String { key }

    init(_ value: JSONValue) {
        let sectionKey = value["key"]?.stringValue ?? UUID().uuidString
        key = sectionKey
        title = value["title"]?.stringValue ?? sectionKey
        status = SystemCheckupStatus(value["status"]?.stringValue)
        finding = value["finding"]?.stringValue ?? ""
        reason = value["reason"]?.stringValue
        steps = (value["next_steps"]?.arrayValue ?? []).enumerated().map { SystemCheckupStep($0.element, index: $0.offset, section: sectionKey) }
    }
}

struct SystemCheckupResult: Sendable {
    struct Counts: Sendable { var ok = 0, warn = 0, error = 0, unknown = 0 }
    let verdict: SystemCheckupStatus
    let verdictText: String
    let counts: Counts
    let sections: [SystemCheckupSection]
    let nextSteps: [SystemCheckupStep]
    let checkedAt: Date?
    let windowHours: Int
    let elapsedMS: Int
    let note: String?

    init(_ value: JSONValue) {
        verdict = SystemCheckupStatus(value["verdict"]?.stringValue)
        verdictText = value["verdict_text"]?.stringValue ?? verdict.headline
        let raw = value["counts"]
        counts = Counts(ok: raw?["ok"]?.intValue ?? 0, warn: raw?["warn"]?.intValue ?? 0,
                        error: raw?["error"]?.intValue ?? 0, unknown: raw?["unknown"]?.intValue ?? 0)
        sections = (value["sections"]?.arrayValue ?? []).map(SystemCheckupSection.init)
        nextSteps = (value["next_steps"]?.arrayValue ?? []).enumerated().map { SystemCheckupStep($0.element, index: $0.offset, section: nil) }
        checkedAt = OpsFormat.date(value["checked_at"])
        windowHours = value["window_hours"]?.intValue ?? 24
        elapsedMS = value["elapsed_ms"]?.intValue ?? 0
        note = value["note"]?.stringValue
    }

    var checkedAtText: String {
        checkedAt.map { $0.formatted(date: .omitted, time: .standard) } ?? "刚刚"
    }
}

/// 检查项／助手工具 → App 内处理入口。只映射 App 里真实存在的页面。
enum SystemCheckupRoutes {
    static func route(tool: String?, section: String?) -> (title: String, route: AppRoute)? {
        let domain = tool?.split(separator: ".").first.map(String.init)
        switch domain ?? section ?? "" {
        case "accounts", "humanize", "rate_limits", "dispatch", "features": return ("打开账号", .accounts)
        case "plugins": return ("打开插件", .plugins)
        case "providers", "usage", "routing": return ("打开 AI", .ai)
        case "logs", "errors": return ("打开日志", .logs)
        case "scheduler": return ("打开指令与任务", .operations)
        case "safety", "health": return ("打开备份与维护", .maintenance)
        case "notifications", "system", "versions", "capabilities": return ("打开设置", .settings)
        case "system_agent": return ("打开助手", .assistant())
        default:
            switch section ?? "" {
            case "accounts": return ("打开账号", .accounts)
            case "plugins": return ("打开插件", .plugins)
            case "providers": return ("打开 AI", .ai)
            case "errors": return ("打开日志", .logs)
            default: return nil
            }
        }
    }
}

// MARK: - 行

private struct SystemCheckupSectionRow: View {
    let section: SystemCheckupSection
    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: section.status.symbol)
                .foregroundStyle(section.status.color)
                .frame(width: 26)
                .accessibilityLabel(section.status.title)
            VStack(alignment: .leading, spacing: 3) {
                HStack {
                    Text(section.title).font(.subheadline.weight(.medium))
                    Spacer(minLength: 4)
                    Text(section.status.title).font(.caption).foregroundStyle(section.status.color)
                }
                Text(section.finding).font(.caption).foregroundStyle(.secondary).lineLimit(3)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

private struct SystemCheckupStepRow: View {
    let step: SystemCheckupStep
    let open: (AppRoute) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            HStack(alignment: .top, spacing: 10) {
                Image(systemName: step.severity == .error ? SystemCheckupStatus.error.symbol : step.severity == .warn ? SystemCheckupStatus.warn.symbol : "info.circle.fill")
                    .foregroundStyle(step.severity == .error ? Color.red : step.severity == .warn ? Color.orange : Color.blue)
                    .frame(width: 26)
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 4) {
                    Text(step.text).font(.subheadline).fixedSize(horizontal: false, vertical: true)
                    if step.requiresConfirmation {
                        Label("需确认", systemImage: "checkmark.shield").font(.caption).foregroundStyle(.secondary)
                    }
                }
            }
            if let target = step.route {
                Button(target.title, systemImage: "arrow.forward.circle") { open(target.route) }
                    .font(.subheadline)
                    .buttonStyle(.borderless)
                    .padding(.leading, 36)
                    .frame(minHeight: 44)
            }
        }
        .padding(.vertical, 2)
    }
}

private struct SystemCheckupSectionDetail: View {
    let section: SystemCheckupSection
    let open: (AppRoute) -> Void

    var body: some View {
        List {
            Section {
                Label(section.status.title, systemImage: section.status.symbol)
                    .foregroundStyle(section.status.color)
                    .font(.headline)
                Text(section.finding).font(.subheadline).textSelection(.enabled)
                if let reason = section.reason {
                    LabeledContent("未能检查的原因", value: reason).font(.footnote)
                }
            } header: { Text("结论") }
            Section {
                if section.steps.isEmpty {
                    Text(section.status == .ok ? "这一项无需处理。" : "服务器没有给出具体建议。").foregroundStyle(.secondary)
                }
                ForEach(section.steps) { step in
                    SystemCheckupStepRow(step: step, open: open)
                }
                if let target = SystemCheckupRoutes.route(tool: nil, section: section.key), section.status != .ok {
                    Button(target.title, systemImage: "arrow.forward.circle") { open(target.route) }
                }
            } header: { Text("原因与修复建议") }
        }
        .navigationTitle(section.title)
        .navigationBarTitleDisplayMode(.inline)
    }
}
