import SwiftUI
import TelePilotCore

// MARK: - 日志补充两类（第 19 轮，iOS 0.20.0，对齐 Web 日志页「系统控制台」「Agent 运行」分页）
//
// - 系统控制台：`POST /api/logs/system-console`（服务、关键词、行数），显示容器 stdout/stderr 原始行，可复制（脱敏）。
// - Agent 运行：`GET /api/system-agent/runs`（状态、时间、条数），点开看单轮详情与事件轨迹
//   `GET /api/system-agent/runs/{id}/events`（隐藏心跳与流式增量，与 Web 一致）。

// MARK: 系统控制台

struct SystemConsoleLogView: View {
    static let services: [(value: String, title: String)] = [
        ("all", "全部服务"), ("web", "后端 Web/Worker"), ("frontend", "前端 Nginx"),
        ("postgres", "PostgreSQL"), ("redis", "Redis"), ("updater", "更新器")
    ]

    @Environment(AppModel.self) private var model
    @State private var service = "all"
    @State private var tail = 100
    @State private var keyword = ""
    @State private var appliedKeyword = ""
    @State private var response: JSONValue?
    @State private var loading = false
    @State private var error: String?

    private var lines: [String] { (response?["lines"]?.arrayValue ?? []).compactMap(\.stringValue) }

    var body: some View {
        List {
            Section {
                Text("服务器容器的原始输出（类似在服务器上执行 docker compose logs），用于排查启动失败、异常堆栈。内容较技术化，适合复制给开发者；按账号、插件查结构化记录请看“运行日志”。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            Section {
                Picker(selection: $service) {
                    ForEach(Self.services, id: \.value) { item in Text(item.title).tag(item.value) }
                } label: {
                    Label("服务", systemImage: "server.rack")
                }
                .pickerStyle(.menu)
                .accessibilityIdentifier("logs.console.service")
                Picker("行数", selection: $tail) {
                    Text("100 行").tag(100)
                    Text("300 行").tag(300)
                    Text("500 行").tag(500)
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("logs.console.tail")
                HStack {
                    Image(systemName: "magnifyingglass").foregroundStyle(.secondary)
                    TextField("搜索原始日志行", text: $keyword)
                        .textInputAutocapitalization(.never)
                        .autocorrectionDisabled()
                        .submitLabel(.search)
                        .onSubmit { appliedKeyword = keyword.trimmingCharacters(in: .whitespacesAndNewlines) }
                        .accessibilityIdentifier("logs.console.keyword")
                }
            } header: {
                Text("筛选")
            }
            Section {
                if loading && response == nil { ProgressView("正在读取控制台输出…") }
                if let error { NoticeView(title: "控制台读取失败", message: error) }
                if let response, response["ok"]?.boolValue == false {
                    NoticeView(title: "服务器没能读取控制台", message: OpsFormat.text(response["error"]) ?? "服务器未返回原因。")
                        .accessibilityIdentifier("logs.console.serverError")
                }
                if response != nil && lines.isEmpty && response?["ok"]?.boolValue != false {
                    Label("当前条件下没有输出", systemImage: "checkmark.circle").foregroundStyle(.secondary)
                }
                ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                    Text(AssistantRedaction.text(line))
                        .font(.caption.monospaced())
                        .textSelection(.enabled)
                        .foregroundStyle(Self.lineColor(line))
                }
            } header: {
                if let response {
                    Text("来源 \(OpsFormat.text(response["source"]) ?? service) · \(lines.count) 行")
                } else {
                    Text("输出")
                }
            }
            .accessibilityIdentifier("logs.console.lines")
        }
        .navigationTitle("系统控制台")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("logs.console.screen")
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("复制已加载日志", systemImage: "doc.on.doc") {
                    OpsFormat.copyForDiagnosis(["系统控制台 · \(OpsFormat.text(response?["source"]) ?? service)"] + lines)
                }
                .disabled(lines.isEmpty)
                .accessibilityIdentifier("logs.console.copy")
            }
        }
        .refreshable { await load() }
        .task(id: "\(service)|\(tail)|\(appliedKeyword)") { await load() }
    }

    static func lineColor(_ line: String) -> Color {
        let lower = line.lowercased()
        if lower.contains("error") || lower.contains("traceback") || lower.contains("exception") || lower.contains("critical") { return .red }
        if lower.contains("warn") { return .orange }
        return .primary
    }

    private func load() async {
        loading = true
        defer { loading = false }
        var payload: [String: JSONValue] = ["service": .string(service), "tail": .integer(Int64(tail))]
        if !appliedKeyword.isEmpty { payload["keyword"] = .string(appliedKeyword) }
        do {
            response = try await model.request(path: "/api/logs/system-console", method: "POST", body: .object(payload))
            error = nil
        } catch {
            self.error = OpsFormat.errorMessage(error)
        }
    }
}

// MARK: Agent 运行

enum AgentRunText {
    static let statuses: [(value: String, title: String)] = [
        ("", "全部状态"), ("queued", "排队中"), ("running", "运行中"), ("waiting_input", "等待补充"),
        ("waiting_approval", "等待审批"), ("succeeded", "已完成"), ("failed", "失败"), ("cancelled", "已取消")
    ]

    static func status(_ raw: String?) -> String {
        statuses.first { $0.value == (raw ?? "") && !$0.value.isEmpty }?.title ?? (raw ?? "未知")
    }

    static func symbol(_ raw: String?) -> String {
        switch raw ?? "" {
        case "queued": "clock"
        case "running": "arrow.triangle.2.circlepath"
        case "waiting_input": "text.bubble"
        case "waiting_approval": "checkmark.shield"
        case "succeeded": "checkmark.circle.fill"
        case "failed": "xmark.octagon.fill"
        case "cancelled": "stop.circle"
        default: "questionmark.circle"
        }
    }

    static func color(_ raw: String?) -> Color {
        switch raw ?? "" {
        case "succeeded": .green
        case "failed": .red
        case "running", "queued": .blue
        case "waiting_input", "waiting_approval": .orange
        default: .secondary
        }
    }

    static func kind(_ raw: String?) -> String {
        switch raw ?? "" {
        case "retry": "重试"
        case "regenerate": "重新生成"
        default: "问答"
        }
    }

    static func elapsed(_ run: JSONValue) -> String? {
        if let ms = run["elapsed_ms"]?.intValue { return OpsFormat.duration(milliseconds: ms) }
        if let start = OpsFormat.date(run["started_at"]), let end = OpsFormat.date(run["finished_at"]) {
            return OpsFormat.duration(milliseconds: Int(end.timeIntervalSince(start) * 1_000))
        }
        return nil
    }

    static let hiddenEventTypes: Set<String> = ["heartbeat", "assistant_delta", "assistant_reasoning_delta"]

    /// 事件轨迹的一行说明，与 Web `RunTrace` 的 `labelEvent` 对齐。
    static func eventLabel(_ event: JSONValue) -> String {
        let type = event["type"]?.stringValue ?? ""
        func text(_ key: String) -> String { OpsFormat.text(event[key]) ?? "?" }
        func tool() -> String { OpsFormat.text(event["tool_description"]) ?? OpsFormat.text(event["tool_name"]) ?? "工具" }
        switch type {
        case "route_selected":
            let domains = (event["domains"]?.arrayValue ?? []).compactMap(\.stringValue).joined(separator: ", ")
            return "路由 → " + (domains.isEmpty ? "无工具" : domains)
        case "skill_selected": return "技能 → " + (OpsFormat.text(event["skill"]) ?? "已选")
        case "tool_started": return "工具开始 · " + tool()
        case "tool_finished": return (event["is_error"]?.boolValue == true ? "工具失败 · " : "工具完成 · ") + tool()
        case "provider_selected": return "模型供应商 · \(text("provider_name")) · \(text("model"))"
        case "model_capability_check": return "能力检查 · \(text("provider_name")) · \(text("model"))"
        case "model_capability_provisional": return "能力待确认 · \(text("provider_name")) · \(text("model"))"
        case "model_attempt": return "尝试 · \(text("provider_name")) · \(text("model")) #\(event["attempt"]?.intValue ?? 1)"
        case "retry_scheduled": return "计划重试 · 第 \(text("retry_number"))/\(text("max_retries")) 次"
        case "model_exhausted": return "模型耗尽 · \(text("provider_name")) · \(text("model"))"
        case "assistant_delta_reset": return "清空流式草稿（准备调用工具）"
        case "assistant_message": return "最终回答已生成"
        case "action_proposed": return "提出待确认操作"
        case "error": return ("错误 · " + [OpsFormat.text(event["code"]), OpsFormat.text(event["message"])].compactMap { $0 }.joined(separator: " ")).trimmingCharacters(in: .whitespaces)
        case "done": return event["ok"]?.boolValue == false ? "结束（未成功）" : "完成"
        default: return type.isEmpty ? "事件" : type
        }
    }

    static func eventIsIssue(_ event: JSONValue) -> Bool {
        let type = event["type"]?.stringValue ?? ""
        if ["error", "model_exhausted", "retry_scheduled"].contains(type) { return true }
        if type == "tool_finished", event["is_error"]?.boolValue == true { return true }
        if type == "done", event["ok"]?.boolValue == false { return true }
        return false
    }
}

struct AgentRunLogView: View {
    @Environment(AppModel.self) private var model
    @State private var status = ""
    @State private var range: LogTimeRange = .day
    @State private var runs: [JSONValue] = []
    @State private var loading = false
    @State private var loaded = false
    @State private var error: String?

    private func count(_ value: String) -> Int { runs.filter { $0["status"]?.stringValue == value }.count }

    var body: some View {
        List {
            Section {
                Text("每一轮系统助手（Agent）请求的状态与事件轨迹。失败时先看错误代码和最后几步。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            Section {
                Picker(selection: $status) {
                    ForEach(AgentRunText.statuses, id: \.value) { item in Text(item.title).tag(item.value) }
                } label: {
                    Label("状态", systemImage: "line.3.horizontal.decrease.circle")
                }
                .pickerStyle(.menu)
                .accessibilityIdentifier("logs.agent.status")
                Picker("时间", selection: $range) {
                    ForEach(LogTimeRange.allCases) { item in Text(item.title).tag(item) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("logs.agent.range")
            } header: {
                Text("筛选")
            }
            Section {
                OpsMetricGrid {
                    OpsMetricTile(title: "运行中", value: OpsFormat.integer(count("running") + count("queued")),
                                  symbol: "arrow.triangle.2.circlepath", color: .blue)
                    OpsMetricTile(title: "完成", value: OpsFormat.integer(count("succeeded")), symbol: "checkmark.circle.fill", color: .green)
                    OpsMetricTile(title: "失败", value: OpsFormat.integer(count("failed")), symbol: "xmark.octagon.fill",
                                  color: count("failed") > 0 ? .red : .primary)
                }
                if loading && !loaded { ProgressView("正在读取 Agent 运行记录…") }
                if let error { NoticeView(title: "Agent 运行记录读取失败", message: error) }
                if loaded && runs.isEmpty && error == nil {
                    Label("当前条件下没有 Agent 运行记录", systemImage: "checkmark.circle").foregroundStyle(.secondary)
                }
                ForEach(Array(runs.enumerated()), id: \.offset) { _, run in
                    NavigationLink { AgentRunDetailView(run: run) } label: { AgentRunRow(run: run) }
                        .accessibilityIdentifier("logs.agentRun.\(run["id"]?.stringValue ?? "")")
                }
            } header: {
                Text("最近 \(OpsFormat.sampledCount(runs.count, limit: 100)) 轮")
            }
        }
        .navigationTitle("Agent 运行")
        .navigationBarTitleDisplayMode(.inline)
        .accessibilityIdentifier("logs.agent.screen")
        .refreshable { await load() }
        .task(id: "\(status)|\(range.rawValue)") { await load() }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        let path = OpsFormat.path("/api/system-agent/runs", [
            ("status", status.isEmpty ? nil : status), ("since", OpsFormat.iso(range.since)), ("limit", "100")
        ])
        do {
            let value = try await model.request(path: path)
            runs = value.arrayValue ?? value["items"]?.arrayValue ?? []
            error = nil
        } catch {
            self.error = OpsFormat.errorMessage(error)
        }
        loaded = true
    }
}

struct AgentRunRow: View {
    let run: JSONValue

    var body: some View {
        let status = run["status"]?.stringValue
        VStack(alignment: .leading, spacing: 3) {
            HStack(spacing: 6) {
                OpsBadge(title: AgentRunText.status(status), symbol: AgentRunText.symbol(status), color: AgentRunText.color(status))
                Text(AgentRunText.kind(run["kind"]?.stringValue)).font(.caption).foregroundStyle(.secondary)
                Spacer(minLength: 4)
                Text(OpsFormat.time(OpsFormat.date(run["created_at"]) ?? OpsFormat.date(run["started_at"])))
                    .font(.caption).foregroundStyle(.secondary).monospacedDigit()
            }
            if let message = OpsFormat.text(run["error_message"]) ?? OpsFormat.text(run["error_code"]) {
                Text(message).font(.subheadline).foregroundStyle(.red).lineLimit(3)
            }
            Text([OpsFormat.text(run["session_id"]).map { "会话 " + String($0.prefix(12)) }, AgentRunText.elapsed(run)]
                .compactMap { $0 }.joined(separator: " · "))
                .font(.caption2).foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }
}

struct AgentRunDetailView: View {
    let run: JSONValue
    @Environment(AppModel.self) private var model
    @State private var events: [JSONValue] = []
    @State private var folded = 0
    @State private var loading = false
    @State private var loaded = false
    @State private var error: String?

    private var runID: String { run["id"]?.stringValue ?? run["run_id"]?.stringValue ?? "" }

    var body: some View {
        List {
            Section {
                let status = run["status"]?.stringValue
                Label {
                    Text(AgentRunText.status(status)).font(.headline)
                } icon: {
                    Image(systemName: AgentRunText.symbol(status)).foregroundStyle(AgentRunText.color(status))
                }
                OpsFieldRow(title: "类型", value: AgentRunText.kind(run["kind"]?.stringValue))
                OpsFieldRow(title: "开始", value: OpsFormat.fullTime(OpsFormat.date(run["started_at"]) ?? OpsFormat.date(run["created_at"])))
                if run["finished_at"]?.isNull == false {
                    OpsFieldRow(title: "结束", value: OpsFormat.fullTime(OpsFormat.date(run["finished_at"])))
                }
                OpsFieldRow(title: "耗时", value: AgentRunText.elapsed(run))
                OpsFieldRow(title: "当前阶段", value: OpsFormat.text(run["phase"]))
                OpsFieldRow(title: "错误代码", value: OpsFormat.text(run["error_code"]), monospaced: true)
                OpsFieldRow(title: "错误信息", value: OpsFormat.text(run["error_message"]))
                OpsFieldRow(title: "模型", value: [OpsFormat.text(run["usage"]?["provider_name"]), OpsFormat.text(run["usage"]?["model"])]
                    .compactMap { $0 }.joined(separator: " · "))
                OpsFieldRow(title: "运行 ID", value: runID, monospaced: true)
                OpsFieldRow(title: "会话", value: OpsFormat.text(run["session_id"]), monospaced: true)
            } header: {
                Text("本轮概况")
            }
            Section {
                if loading && !loaded { ProgressView("正在读取事件轨迹…") }
                if let error { NoticeView(title: "事件轨迹读取失败", message: error) }
                if loaded && events.isEmpty && error == nil {
                    Text("没有可显示的事件").foregroundStyle(.secondary)
                }
                ForEach(Array(events.enumerated()), id: \.offset) { _, row in
                    let event = row["event"] ?? row
                    let issue = AgentRunText.eventIsIssue(event)
                    VStack(alignment: .leading, spacing: 2) {
                        Label {
                            Text(AgentRunText.eventLabel(event)).foregroundStyle(issue ? Color.red : Color.primary)
                        } icon: {
                            Image(systemName: issue ? "exclamationmark.triangle.fill" : "circle.fill")
                                .font(issue ? Font.body : Font.system(size: 6))
                                .foregroundStyle(issue ? Color.red : Color.secondary)
                        }
                        Text((["#\(row["seq"]?.intValue ?? 0)", OpsFormat.date(row["created_at"]).map { OpsFormat.fullTime($0) }] as [String?])
                            .compactMap { $0 }.joined(separator: " · "))
                            .font(.caption2).foregroundStyle(.secondary).monospacedDigit()
                    }
                    .accessibilityElement(children: .combine)
                }
            } header: {
                Text("事件轨迹")
            } footer: {
                if folded > 0 { Text("已隐藏 \(folded) 条心跳与流式增量事件。") }
            }
            .accessibilityIdentifier("logs.agentRun.events")
        }
        .navigationTitle("Agent 运行详情")
        .navigationBarTitleDisplayMode(.inline)
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("复制诊断信息", systemImage: "doc.on.doc") { copy() }
            }
        }
        .refreshable { await load() }
        .task { if !loaded { await load() } }
    }

    private func copy() {
        var lines = ["Agent 运行 \(runID) · \(AgentRunText.status(run["status"]?.stringValue))"]
        if let code = OpsFormat.text(run["error_code"]) { lines.append("错误代码：" + code) }
        if let message = OpsFormat.text(run["error_message"]) { lines.append("错误信息：" + message) }
        for row in events {
            lines.append("#\(row["seq"]?.intValue ?? 0) " + AgentRunText.eventLabel(row["event"] ?? row))
        }
        OpsFormat.copyForDiagnosis(lines)
    }

    private func load() async {
        guard !runID.isEmpty else { loaded = true; return }
        loading = true
        defer { loading = false }
        do {
            let value = try await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))/events?after_seq=0&limit=500")
            let rows = value.arrayValue ?? []
            let visible = rows.filter { row in
                !AgentRunText.hiddenEventTypes.contains((row["event"] ?? row)["type"]?.stringValue ?? "")
            }
            folded = rows.count - visible.count
            events = visible
            error = nil
        } catch {
            self.error = OpsFormat.errorMessage(error)
        }
        loaded = true
    }
}
