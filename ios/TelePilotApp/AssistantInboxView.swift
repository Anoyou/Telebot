import SwiftUI
import TelePilotCore

struct AssistantInboxView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var actions: [AssistantItem] = []
    @State private var status = "pending"
    @State private var loading = false
    @State private var error: String?
    @State private var selected: AssistantItem?
    private let initialActionID: String?

    init(actionID: String? = nil) { initialActionID = actionID }

    var body: some View {
        NavigationStack {
            List {
                if let error { Section { AssistantNotice(text: error, isError: true) } }
                Section {
                    ForEach(actions) { item in
                        Button { selected = item } label: { AssistantActionRow(item: item) }
                    }
                    if actions.isEmpty && !loading {
                        ContentUnavailableView("没有待处理的操作", systemImage: "checkmark.shield", description: Text("助手需要写入配置或执行操作时，会在这里请求确认。"))
                    }
                    if loading { ProgressView("正在读取…") }
                }
            }
            // 此页为 sheet 内的行内标题，没有大标题折叠，安全区栏不会与导航栏形成反馈循环，可保留 safeAreaBar。
            .safeAreaBar(edge: .top) {
                Picker("状态", selection: $status) {
                    Text("待确认").tag("pending")
                    Text("全部").tag("")
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .padding(.horizontal, 16)
                .padding(.vertical, 8)
            }
            .navigationTitle("待确认操作").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
            .refreshable { await load() }
            .onChange(of: status) { _, _ in Task { await load() } }
            .task {
                await load()
                if let initialActionID {
                    do { selected = AssistantItem(value: try await model.request(path: AssistantPresentation.root + "/actions/\(AssistantPresentation.path(initialActionID))")) }
                    catch { self.error = AssistantPresentation.error(error) }
                }
            }
            .sheet(item: $selected, onDismiss: { Task { await load() } }) { AssistantActionDetailView(actionID: $0.id) }
        }
    }

    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let query = status.isEmpty ? "" : "&status=\(status)"
            actions = try await model.request(path: AssistantPresentation.root + "/actions?limit=200\(query)").assistantItems
            error = nil
        } catch { self.error = AssistantPresentation.error(error) }
    }
}

private struct AssistantActionRow: View {
    let item: AssistantItem
    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            Text(AssistantRedaction.text(item.text("summary").isEmpty ? item.text("tool_name") : item.text("summary"))).font(.body.weight(.medium)).foregroundStyle(.primary)
            HStack {
                Text(AssistantPresentation.status(item.text("status")))
                if item.text("risk") == "dangerous" { Label("危险操作", systemImage: "exclamationmark.triangle").foregroundStyle(.red) }
            }.font(.caption).foregroundStyle(.secondary)
            if !item.text("session_title").isEmpty { Text(AssistantRedaction.text(item.text("session_title"))).font(.caption).foregroundStyle(.secondary) }
        }.padding(.vertical, 4)
    }
}

private struct AssistantActionDetailView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.scenePhase) private var scenePhase
    let actionID: String
    @State private var action: JSONValue?
    @State private var secrets: [String: String] = [:]
    @State private var busy = false
    @State private var error: String?
    @State private var notice: String?
    @State private var confirmation: String?
    @State private var confirming = false

    private var pending: Bool { action?.assistantText("status") == "pending" }
    private var needsAPIKey: Bool {
        guard let action else { return false }
        let code = action.assistantText("error_code")
        if ["API_KEY_REQUIRED", "API_KEY_DECRYPT_FAILED", "API_KEY_REJECTED"].contains(code) { return true }
        let tool = action.assistantText("tool_name")
        let mode = action["preview"]?.assistantText("mode") ?? ""
        return (tool == "providers.verify" && mode == "draft") || (tool == "providers.save" && (mode == "create" || (mode == "update" && action["preview"]?["current"]?["has_effective_key"]?.boolValue == false)))
    }
    private var secretFields: [String] {
        if needsAPIKey { return ["api_key"] }
        return action?["secret_fields"]?.arrayValue?.compactMap(\.stringValue) ?? []
    }
    private var requiresSecret: Bool { needsAPIKey || (action?["has_secret"]?.boolValue != true && !secretFields.isEmpty) }

    var body: some View {
        NavigationStack {
            Form {
                if let error { Section { AssistantNotice(text: error, isError: true) } }
                if let notice { Section { AssistantNotice(text: notice) } }
                if let action {
                    Section("操作") {
                        Text(AssistantRedaction.text(action.assistantText("summary"))).font(.headline)
                        LabeledContent("工具", value: action.assistantText("tool_name"))
                        LabeledContent("状态", value: AssistantPresentation.status(action.assistantText("status")))
                        LabeledContent("风险", value: action.assistantText("risk") == "dangerous" ? "危险操作" : AssistantPresentation.status(action.assistantText("risk")))
                        if let expiry = action["expires_at"]?.stringValue { LabeledContent("过期时间", value: expiry) }
                    }
                    Section("执行预览") { AssistantJSONView(value: action["preview"] ?? [:]) }
                    if requiresSecret && pending {
                        Section {
                            ForEach(secretFields, id: \.self) { field in
                                SecureField(field, text: secretBinding(field)).textInputAutocapitalization(.never).autocorrectionDisabled().privacySensitive()
                            }
                            Button("加密保存密钥") { Task { await saveSecrets() } }
                                .disabled(busy || !secretFields.contains(where: { !(secrets[$0] ?? "").isEmpty }))
                        } header: { Text("补填密钥") } footer: { Text("密钥仅提交到当前服务器加密暂存，确认操作后才使用，不会回显。") }
                    }
                    if action["has_secret"]?.boolValue == true { Section { Label("已有加密暂存的敏感字段", systemImage: "lock.shield") } }
                    if !action.assistantText("error_code").isEmpty {
                        Section("执行反馈") { Text("执行尚未完成（\(action.assistantText("error_code"))），请检查操作状态与预览。").foregroundStyle(.red) }
                    }
                    if pending {
                        Section {
                            Button("确认执行", role: action.assistantText("risk") == "dangerous" ? .destructive : nil) { ask("confirm") }.disabled(busy || requiresSecret)
                                .modifier(confirmationFor("confirm"))
                            Button("拒绝操作", role: .destructive) { ask("reject") }.disabled(busy)
                                .modifier(confirmationFor("reject"))
                        } footer: { Text("只有确认后服务器才会执行此操作。") }
                    }
                    if action.assistantText("runtime_sync_status") == "failed" {
                        Section {
                            Text("配置已保存，但运行时同步失败。请先核对实际状态。").foregroundStyle(.orange)
                            if action["runtime_retryable"]?.boolValue != false {
                                Button("重新同步运行时") { ask("retry-runtime-sync") }.disabled(busy)
                                    .modifier(confirmationFor("retry-runtime-sync"))
                            } else { Text("此操作可能已产生外部影响，不能直接重试。").font(.footnote).foregroundStyle(.secondary) }
                        }
                    }
                    if let result = action["result"], !result.isNull { Section("服务器返回结果") { AssistantJSONView(value: result) } }
                    if !action.assistantText("session_id").isEmpty {
                        Section { NavigationLink("查看来源会话") { AssistantView(sessionID: action.assistantText("session_id")) } }
                    }
                } else if busy { ProgressView("正在读取操作…") }
            }
            .navigationTitle("确认操作").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
            .refreshable { await load() }
            .task { await load() }
            .onChange(of: scenePhase) { _, phase in if phase != .active { secrets = [:] } }
            .onDisappear { secrets = [:] }
        }
    }

    private func secretBinding(_ field: String) -> Binding<String> { Binding(get: { secrets[field] ?? "" }, set: { secrets[field] = $0 }) }
    private func ask(_ action: String) { confirmation = action; confirming = true }
    /// iOS 26+ 确认框以 popover 锚定在所修饰的视图上：三个触发按钮各自挂一个，
    /// 共用 `confirming`/`confirmation` 状态，只有与本次请求匹配的那个按钮弹出。
    private func confirmationFor(_ kind: String) -> AssistantActionConfirmation {
        AssistantActionConfirmation(
            isPresented: Binding(get: { confirming && confirmation == kind }, set: { if !$0 { confirming = false } }),
            kind: kind,
            summary: AssistantRedaction.text(action?.assistantText("summary") ?? "请核对执行预览。"),
            perform: { Task { await mutate(kind) } })
    }
    private func load() async {
        busy = true
        defer { busy = false }
        do { action = try await model.request(path: AssistantPresentation.root + "/actions/\(AssistantPresentation.path(actionID))"); error = nil }
        catch { self.error = AssistantPresentation.error(error) }
    }
    private func saveSecrets() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        let fields = secrets.filter { !$0.value.isEmpty }.mapValues { JSONValue.string($0) }
        var failure: String?
        do {
            let response = try await model.request(path: AssistantPresentation.root + "/actions/\(AssistantPresentation.path(actionID))/secret-input", method: "POST", body: ["fields": .object(fields)])
            secrets = [:]
            guard response["has_secret"]?.boolValue == true else { throw APIError.incompatibleResponse }
            notice = "密钥已加密保存，尚未执行操作。"
        } catch { secrets = [:]; failure = AssistantPresentation.error(error) }
        await load()
        if let failure { error = failure }
    }
    private func mutate(_ endpoint: String) async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        error = nil
        var failure: String?
        do {
            let response = try await model.request(path: AssistantPresentation.root + "/actions/\(AssistantPresentation.path(actionID))/\(endpoint)", method: "POST")
            if endpoint == "reject" { action = response; notice = response.assistantText("status") == "rejected" ? "已拒绝。" : "服务器已返回操作状态。" }
            else {
                if let row = response["action"], !row.isNull { action = row }
                if response["ok"]?.boolValue == true { notice = endpoint == "confirm" ? "服务器已确认执行结果。" : "服务器已确认同步结果。" }
                else { failure = "服务器未确认操作成功，请检查最新状态与返回结果。" }
            }
        } catch { failure = AssistantPresentation.error(error) }
        await load()
        if let failure { error = failure }
    }
}

/// 待确认操作详情的二次确认框，挂在触发它的按钮上。
private struct AssistantActionConfirmation: ViewModifier {
    @Binding var isPresented: Bool
    let kind: String
    let summary: String
    let perform: () -> Void

    func body(content: Content) -> some View {
        content.confirmationDialog(kind == "reject" ? "拒绝这个操作？" : kind == "retry-runtime-sync" ? "重新同步运行时？" : "确认执行此操作？", isPresented: $isPresented, titleVisibility: .visible) {
            Button(kind == "reject" ? "拒绝" : kind == "retry-runtime-sync" ? "重新同步" : "确认执行", role: .destructive) { perform() }
        } message: { Text(summary) }
    }
}

struct AssistantTasksView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let sessionID: String?
    @State private var runs: [AssistantItem] = []
    @State private var filter = ""
    @State private var includeBot = true
    @State private var error: String?
    @State private var loading = false
    var body: some View {
        NavigationStack {
            List {
                if let error { Section { AssistantNotice(text: error, isError: true) } }
                Section {
                    NavigationLink { AssistantQueueView(sessionID: sessionID) } label: { Label("任务队列", systemImage: "list.number") }
                    NavigationLink { AssistantInboxView() } label: { Label("待确认操作", systemImage: "checkmark.shield") }
                    Picker("任务状态", selection: $filter) {
                        Text("全部").tag("")
                        ForEach(["queued", "running", "waiting_input", "waiting_approval", "succeeded", "failed", "cancelled"], id: \.self) { Text(AssistantPresentation.status($0)).tag($0) }
                    }
                    Toggle("包含 Telegram 任务", isOn: $includeBot)
                }
                Section("最近任务") {
                    ForEach(runs) { run in
                        NavigationLink { AssistantTraceView(runID: run.id) } label: {
                            VStack(alignment: .leading, spacing: 5) {
                                Text(AssistantPresentation.status(run.text("status"))).font(.headline)
                                Text(run.text("channel") == "bot" ? "Telegram 任务" : "App / Web 任务").font(.caption).foregroundStyle(.secondary)
                                Text(run.text("created_at")).font(.caption).foregroundStyle(.secondary)
                                if !run.text("error_code").isEmpty { Text(run.text("error_code")).font(.caption).foregroundStyle(.red) }
                            }
                        }
                    }
                    if loading { ProgressView("正在读取任务…") }
                    else if runs.isEmpty { ContentUnavailableView("没有任务", systemImage: "checklist") }
                }
            }
            .navigationTitle("任务中心").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
            .task { await load() }
            .refreshable { await load() }
            .onChange(of: filter) { _, _ in Task { await load() } }
            .onChange(of: includeBot) { _, _ in Task { await load() } }
        }
    }
    private func load() async {
        loading = true
        defer { loading = false }
        do {
            let query = filter.isEmpty ? "" : "&status=\(filter)"
            runs = try await model.request(path: AssistantPresentation.root + "/runs?limit=500&include_bot=\(includeBot)\(query)").assistantItems
            error = nil
        } catch { self.error = AssistantPresentation.error(error) }
    }
}

struct AssistantTraceView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    let runID: String
    @State private var run: JSONValue?
    @State private var events: [JSONValue] = []
    @State private var cursor = 0
    @State private var busy = false
    @State private var error: String?
    @State private var notice: String?
    @State private var content = ""
    @State private var selectedTools: Set<String> = []
    @State private var confirmingApproval = false
    @State private var confirmingStop = false
    @State private var onlyTools = false
    @State private var fallbackProvider = ""
    @State private var canLoadMore = false
    private var status: String { run?.assistantText("status") ?? "" }
    private var approval: JSONValue? {
        events.reversed().compactMap { $0["tool_approval"] ?? $0["usage"]?["tool_approval"] }.first
    }
    private var tools: [JSONValue] { approval?["tools"]?.arrayValue ?? [] }
    private var switching: [JSONValue] {
        events.reversed().compactMap { $0["provider_switch"] ?? $0["usage"]?["provider_switch"] }.first?["candidates"]?.arrayValue ?? []
    }
    private var visibleEvents: [JSONValue] {
        events.filter {
            let type = $0.assistantText("type")
            return !["heartbeat", "assistant_delta", "assistant_reasoning_delta"].contains(type) && (!onlyTools || ["tool_started", "tool_finished", "action_proposed"].contains(type))
        }
    }
    var body: some View {
        NavigationStack {
            List {
                if let error { Section { AssistantNotice(text: error, isError: true) } }
                if let notice { Section { AssistantNotice(text: notice) } }
                Section("任务状态") {
                    LabeledContent("状态", value: AssistantPresentation.status(status))
                    if let run {
                        if run["cancel_requested"]?.boolValue == true { Text("停止请求已提交，等待服务器确认。").foregroundStyle(.orange) }
                        if !run.assistantText("paused_reason").isEmpty { LabeledContent("暂停原因", value: AssistantRedaction.text(run.assistantText("paused_reason"))) }
                        if !run.assistantText("error_code").isEmpty { LabeledContent("错误代码", value: run.assistantText("error_code")) }
                        if let elapsed = run["elapsed_ms"]?.intValue { LabeledContent("用时", value: String(format: "%.1f 秒", Double(elapsed) / 1000)) }
                        if let usage = run["usage"], !usage.isNull { DisclosureGroup("用量") { AssistantJSONView(value: usage) } }
                        NavigationLink("查看会话") { AssistantView(sessionID: run.assistantText("session_id")) }
                    }
                    if !status.isEmpty && !AssistantPresentation.terminal.contains(status) {
                        Button("停止任务", role: .destructive) { confirmingStop = true }.disabled(busy)
                            .confirmationDialog("停止当前任务？", isPresented: $confirmingStop, titleVisibility: .visible) {
                                Button("提交停止请求", role: .destructive) { Task { await stop() } }
                            } message: { Text("已经执行的外部操作不会撤销。") }
                    }
                }
                if status == "waiting_approval" { approvalSection }
                if status == "waiting_input" { inputSection }
                if status == "running" {
                    Section("调整任务") {
                        TextField("补充说明", text: $content, axis: .vertical).lineLimit(2...6)
                        Button("插入调整说明") { Task { await sendInput("steer", payload: ["content": .string(content)]) } }.disabled(busy || content.isEmpty || content.count > 32_000)
                    }
                }
                Section {
                    Toggle("只看工具与操作", isOn: $onlyTools)
                    ForEach(Array(visibleEvents.enumerated()), id: \.offset) { _, event in
                        DisclosureGroup {
                            AssistantJSONView(value: event)
                            if let action = event["action"], let id = action["id"]?.stringValue {
                                NavigationLink("查看并确认操作") { AssistantActionDetailView(actionID: id) }
                            }
                        } label: {
                            VStack(alignment: .leading, spacing: 4) {
                                Text(AssistantPresentation.eventTitle(event)).font(.subheadline.weight(.medium))
                                let description = event.assistantText("tool_description").isEmpty ? event.assistantText("tool_name") : event.assistantText("tool_description")
                                if !description.isEmpty { Text(AssistantRedaction.text(description)).font(.caption).foregroundStyle(.secondary) }
                                if event["is_error"]?.boolValue == true { Text("工具执行失败").font(.caption).foregroundStyle(.red) }
                            }
                        }
                    }
                    if canLoadMore { Button("读取后续轨迹") { Task { await load() } }.disabled(busy) }
                    if visibleEvents.isEmpty { Text("暂无轨迹").foregroundStyle(.secondary) }
                } header: { Text("运行轨迹") } footer: { Text("生成过程中的细小文本片段已折叠，完整回答保存在会话历史中。敏感字段默认遮盖。") }
            }
            .navigationTitle("任务详情").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
            .refreshable { await load() }
            .task {
                await load()
                while !Task.isCancelled && !status.isEmpty && !AssistantPresentation.terminal.contains(status) {
                    do { try await Task.sleep(for: .seconds(4)) } catch { break }
                    if !busy { await load() }
                }
            }
        }
    }

    private var approvalSection: some View {
        Section {
            ForEach(Array(tools.enumerated()), id: \.offset) { _, tool in
                VStack(alignment: .leading, spacing: 8) {
                    Toggle(isOn: Binding(get: { selectedTools.contains(tool.assistantText("name")) }, set: { enabled in
                        if enabled { selectedTools.insert(tool.assistantText("name")) } else { selectedTools.remove(tool.assistantText("name")) }
                    })) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(AssistantRedaction.text(tool.assistantText("description").isEmpty ? tool.assistantText("name") : tool.assistantText("description")))
                            Text("\(tool["read_only"]?.boolValue == true ? "读取" : "写入") · \(AssistantPresentation.status(tool.assistantText("risk")))").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                    if let arguments = tool["arguments"] { DisclosureGroup("参数") { AssistantJSONView(value: arguments) } }
                }
            }
            if tools.isEmpty { Text("正在读取审批请求。下拉刷新以核对完整工具信息。").foregroundStyle(.secondary) }
            Button("批准选中的工具") { confirmingApproval = true }.disabled(busy || selectedTools.isEmpty)
                .confirmationDialog("批准选中的工具调用？", isPresented: $confirmingApproval, titleVisibility: .visible) {
                    Button("批准调用", role: .destructive) {
                        Task { await sendInput("approval", payload: ["approved": true, "approved_tools": .array(selectedTools.sorted().map(JSONValue.string))]) }
                    }
                } message: { Text("批准后助手可以继续调用这些工具。请核对每个工具的风险和参数。") }
            Button("拒绝本次调用", role: .destructive) { Task { await sendInput("approval", payload: ["approved": false, "approved_tools": []]) } }.disabled(busy)
        } header: { Text("工具审批") }
    }

    private var inputSection: some View {
        Section {
            TextField("补充信息", text: $content, axis: .vertical).lineLimit(2...6)
            if !switching.isEmpty {
                Picker("备用模型", selection: $fallbackProvider) {
                    Text("保持当前模型").tag("")
                    ForEach(Array(switching.enumerated()), id: \.offset) { _, candidate in
                        Text("\(candidate.assistantText("provider_name")) · \(candidate.assistantText("model"))").tag(candidate["provider_id"]?.intValue.map(String.init) ?? "")
                    }
                }
            }
            Button("补充并继续") {
                var payload: [String: JSONValue] = [:]
                if !content.isEmpty { payload["content"] = .string(content) }
                if let id = Int(fallbackProvider) { payload["fallback_provider_id"] = .number(Double(id)) }
                Task { await sendInput("input", payload: payload) }
            }.disabled(busy || (content.isEmpty && fallbackProvider.isEmpty) || content.count > 32_000)
        } header: { Text("等待补充信息") } footer: { Text("API Key 等机密信息请通过待确认操作的密钥表单提交。") }
    }

    private func load() async {
        guard !busy else { return }
        busy = true
        defer { busy = false }
        do {
            run = try await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))")
            let knownEnd = run?["last_seq"]?.intValue ?? 0
            repeat {
                let rows = try await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))/events?after_seq=\(cursor)&limit=1000").arrayValue ?? []
                let previousCursor = cursor
                for row in rows {
                    if let seq = row["seq"]?.intValue, seq > cursor, var event = row["event"]?.objectValue {
                        event["seq"] = .number(Double(seq))
                        let type = event["type"]?.stringValue ?? ""
                        if !["heartbeat", "assistant_delta", "assistant_reasoning_delta"].contains(type) { events.append(.object(event)) }
                        cursor = seq
                    }
                }
                canLoadMore = rows.count == 1000 && cursor > previousCursor
                if Task.isCancelled || !canLoadMore || cursor >= knownEnd { break }
            } while cursor < knownEnd
            error = nil
        } catch { self.error = AssistantPresentation.error(error) }
    }
    private func sendInput(_ endpoint: String, payload: [String: JSONValue]) async {
        guard !busy else { return }
        busy = true
        var body = payload
        body["client_request_id"] = .string(UUID().uuidString)
        var failure: String?
        do {
            _ = try await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))/\(endpoint)", method: "POST", body: .object(body))
            content = ""
            selectedTools = []
            notice = "服务器已接收，后续状态以任务进度为准。"
        } catch { failure = AssistantPresentation.error(error) }
        busy = false
        await load()
        if let failure { error = failure }
    }
    private func stop() async {
        guard !busy else { return }
        busy = true
        var failure: String?
        do {
            run = try await model.request(path: AssistantPresentation.root + "/runs/\(AssistantPresentation.path(runID))/cancel", method: "POST")
            notice = "已提交停止请求，请等待服务器确认。"
        } catch { failure = AssistantPresentation.error(error) }
        busy = false
        await load()
        if let failure { error = failure }
    }
}

struct AssistantQueueView: View {
    @Environment(AppModel.self) private var model
    let sessionID: String?
    @State private var rows: [AssistantItem] = []
    @State private var busy = false
    @State private var error: String?
    @State private var editing: AssistantItem?
    @State private var deleting: AssistantItem?
    @State private var confirmation = false
    @State private var clearSession: String?
    @State private var editMode = EditMode.inactive
    private var groupedIDs: [String] { Array(Set(rows.map { $0.text("session_id") })).sorted() }
    var body: some View {
        List {
            if let error { Section { AssistantNotice(text: error, isError: true) } }
            if rows.isEmpty { ContentUnavailableView("队列为空", systemImage: "list.number") }
            ForEach(groupedIDs, id: \.self) { id in
                Section {
                    let items = rows.filter { $0.text("session_id") == id }.sorted { ($0["position"]?.intValue ?? 0) < ($1["position"]?.intValue ?? 0) }
                    ForEach(items) { item in
                        VStack(alignment: .leading, spacing: 8) {
                            Text(AssistantRedaction.text(item.text("content"))).lineLimit(5).privacySensitive()
                            Text(AssistantPresentation.status(item.text("status"))).font(.caption).foregroundStyle(.secondary)
                            if !item.text("blocked_reason").isEmpty { Text(AssistantRedaction.text(item.text("blocked_reason"))).font(.caption).foregroundStyle(.orange) }
                            deleteConfirmation(Menu("操作", systemImage: "ellipsis.circle") {
                                Button("编辑", systemImage: "pencil") { editing = item }
                                Button("置顶", systemImage: "pin") { Task { await mutate("/queue/\(AssistantPresentation.path(item.id))", method: "PATCH", body: ["pinned": true]) } }
                                Button("转为当前任务的调整说明", systemImage: "arrow.turn.up.right") {
                                    Task { await mutate("/queue/\(AssistantPresentation.path(item.id))/steer", method: "POST", body: ["client_request_id": .string(UUID().uuidString)]) }
                                }
                                Button("删除", systemImage: "trash", role: .destructive) { deleting = item; confirmation = true }
                            }.font(.caption).disabled(busy)) { clearSession == nil && deleting?.id == item.id }
                        }.padding(.vertical, 4)
                    }
                    .onMove { source, destination in
                        var ids = items.map(\.id)
                        ids.move(fromOffsets: source, toOffset: destination)
                        Task { await mutate("/sessions/\(AssistantPresentation.path(id))/queue/reorder", method: "POST", body: ["turn_ids": .array(ids.map(JSONValue.string))]) }
                    }
                    HStack {
                        Button("恢复队列") { Task { await mutate("/sessions/\(AssistantPresentation.path(id))/queue/resume", method: "POST") } }
                        Spacer()
                        deleteConfirmation(Button("清空", role: .destructive) { clearSession = id; confirmation = true }) { clearSession == id }
                    }.buttonStyle(.borderless).disabled(busy)
                } header: { Text(id == sessionID ? "当前会话" : "会话 \(id.prefix(8))") }
            }
        }
        .navigationTitle("任务队列").navigationBarTitleDisplayMode(.inline)
        .environment(\.editMode, $editMode)
        .toolbar { ToolbarItem(placement: .topBarTrailing) { EditButton().disabled(busy || rows.isEmpty) } }
        .task { await load() }
        .refreshable { await load() }
        .sheet(item: $editing) { item in
            AssistantQueueEditView(item: item) { content in
                await mutate("/queue/\(AssistantPresentation.path(item.id))", method: "PATCH", body: ["content": .string(content)])
            }
        }
    }
    /// iOS 26+ 确认框以 popover 锚定在所修饰的视图上，因此挂在触发它的那一行/按钮上；
    /// 行在 ForEach 内会重复实例化，用 `matches` 只让本次点按的那一个绑定为真。
    private func deleteConfirmation(_ content: some View, matches: @escaping () -> Bool) -> some View {
        content.confirmationDialog(clearSession == nil ? "删除这个排队任务？" : "清空这个会话的队列？",
                                   isPresented: Binding(get: { confirmation && matches() }, set: { if !$0 { confirmation = false } }),
                                   titleVisibility: .visible) {
            Button("确认删除", role: .destructive) {
                if let id = clearSession {
                    Task { await mutate("/sessions/\(AssistantPresentation.path(id))/queue", method: "DELETE") }
                } else if let deleting { Task { await mutate("/queue/\(AssistantPresentation.path(deleting.id))", method: "DELETE") } }
                clearSession = nil
                deleting = nil
            }
            Button("取消", role: .cancel) { clearSession = nil; deleting = nil }
        } message: { Text("删除后不会自动恢复。正在执行的任务不受影响。") }
    }
    private func load() async {
        do {
            let query = sessionID.map { "&session_id=\(AssistantPresentation.path($0))" } ?? ""
            rows = try await model.request(path: AssistantPresentation.root + "/queue?include_bot=true\(query)").assistantItems
            error = nil
        } catch { self.error = AssistantPresentation.error(error) }
    }
    @discardableResult private func mutate(_ path: String, method: String, body: JSONValue? = nil) async -> Bool {
        guard !busy else { return false }
        busy = true
        defer { busy = false }
        var failure: String?
        do { _ = try await model.request(path: AssistantPresentation.root + path, method: method, body: body) }
        catch { failure = AssistantPresentation.error(error) }
        await load()
        if let failure { error = failure }
        return failure == nil
    }
}

private struct AssistantQueueEditView: View {
    @Environment(\.dismiss) private var dismiss
    let item: AssistantItem
    let save: (String) async -> Bool
    @State private var content = ""
    @State private var saving = false
    @State private var failed = false
    @State private var revealed = false
    private var protected: Bool { AssistantRedaction.containsCredential(item.text("content")) && !revealed }
    var body: some View {
        NavigationStack {
            Form {
                TextEditor(text: $content).frame(minHeight: 220).disabled(saving || protected).privacySensitive()
                if protected {
                    Text("此消息包含敏感信息，默认已遮盖。").font(.footnote).foregroundStyle(.secondary)
                    Button("显示原文并编辑", systemImage: "eye") { content = item.text("content"); revealed = true }
                }
                if failed { Text("未能确认保存结果，已保留编辑内容。请先刷新队列核对。").foregroundStyle(.red) }
            }
                .navigationTitle("编辑排队消息").navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(saving) }
                    ToolbarItem(placement: .confirmationAction) {
                        Button("保存") {
                            saving = true
                            Task { if await save(content) { dismiss() } else { failed = true }; saving = false }
                        }.disabled(saving || failed || protected || content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || content.count > 32_000)
                    }
                }
                .onAppear { content = AssistantRedaction.text(item.text("content")) }
        }
    }
}

struct AssistantMemoryView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @State private var rows: [AssistantItem] = []
    @State private var editing: AssistantItem?
    @State private var creating = false
    @State private var deleting: AssistantItem?
    @State private var confirmingDelete = false
    @State private var error: String?
    @State private var busy = false
    var body: some View {
        NavigationStack {
            List {
                if let error { Section { AssistantNotice(text: error, isError: true) } }
                Section {
                    ForEach(rows) { row in
                        VStack(alignment: .leading, spacing: 10) {
                            Text(AssistantRedaction.text(row.text("content"))).privacySensitive()
                            HStack {
                                Text(row["enabled"]?.boolValue == true ? "启用" : "停用").font(.caption).foregroundStyle(.secondary)
                                Spacer()
                                Button("编辑") { editing = row }.font(.caption)
                            }
                        }
                        // 确认框挂在被左滑的那一行上；行在 ForEach 内重复实例化，只让被删除的那一行绑定为真。
                        .confirmationDialog("删除这条长期记忆？", isPresented: Binding(get: { confirmingDelete && deleting?.id == row.id }, set: { if !$0 { confirmingDelete = false } }), titleVisibility: .visible) {
                            Button("删除", role: .destructive) { if let deleting { Task { await mutate(id: deleting.id, body: nil) } } }
                        }
                        .swipeActions {
                            Button("删除", role: .destructive) { deleting = row; confirmingDelete = true }
                            Button(row["enabled"]?.boolValue == true ? "停用" : "启用") {
                                Task { await mutate(id: row.id, body: ["enabled": .bool(row["enabled"]?.boolValue != true)]) }
                            }.tint(.orange)
                        }
                    }
                    if rows.isEmpty { ContentUnavailableView("还没有长期记忆", systemImage: "brain", description: Text("保存你的习惯和偏好，让之后的对话继续使用。每条最多 200 字。")) }
                }
            }
            .navigationTitle("长期记忆").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .topBarLeading) { Button("添加", systemImage: "plus") { creating = true }.disabled(busy) }
                ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } }
            }
            .refreshable { await load() }
            .task { await load() }
            .sheet(item: $editing) { item in AssistantMemoryEditor(item: item) { content, enabled in await mutate(id: item.id, body: ["content": .string(content), "enabled": .bool(enabled)]) } }
            .sheet(isPresented: $creating) { AssistantMemoryEditor(item: nil) { content, enabled in await mutate(id: nil, body: ["content": .string(content), "enabled": .bool(enabled)]) } }
        }
    }
    private func load() async {
        do { rows = try await model.request(path: AssistantPresentation.root + "/memory").assistantItems; error = nil }
        catch { self.error = AssistantPresentation.error(error) }
    }
    @discardableResult private func mutate(id: String?, body: JSONValue?) async -> Bool {
        guard !busy else { return false }
        busy = true
        defer { busy = false }
        var failure: String?
        let suffix = id.map { "/\(AssistantPresentation.path($0))" } ?? ""
        do { _ = try await model.request(path: AssistantPresentation.root + "/memory" + suffix, method: body == nil ? "DELETE" : id == nil ? "POST" : "PATCH", body: body) }
        catch { failure = AssistantPresentation.error(error) }
        await load()
        if let failure { error = failure }
        return failure == nil
    }
}

private struct AssistantMemoryEditor: View {
    @Environment(\.dismiss) private var dismiss
    let item: AssistantItem?
    let save: (String, Bool) async -> Bool
    @State private var content = ""
    @State private var enabled = true
    @State private var saving = false
    @State private var failed = false
    @State private var revealed = false
    private var protected: Bool { AssistantRedaction.containsCredential(item?.text("content") ?? "") && !revealed }
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    TextEditor(text: $content).frame(minHeight: 140).disabled(saving || protected).privacySensitive()
                    if protected {
                        Text("此记忆包含敏感信息，默认已遮盖。").font(.footnote).foregroundStyle(.secondary)
                        Button("显示原文并编辑", systemImage: "eye") { content = item?.text("content") ?? ""; revealed = true }
                    }
                    Toggle("启用", isOn: $enabled).disabled(saving)
                } footer: { Text("\(content.count) / 200 字。请勿在长期记忆中保存密钥或密码。") }
                if failed { Text("未能确认保存结果，已保留编辑内容。请先刷新长期记忆核对。").foregroundStyle(.red) }
            }
            .navigationTitle(item == nil ? "添加记忆" : "编辑记忆").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() }.disabled(saving) }
                ToolbarItem(placement: .confirmationAction) {
                    Button("保存") {
                        saving = true
                        Task { if await save(content, enabled) { dismiss() } else { failed = true }; saving = false }
                    }.disabled(saving || failed || protected || content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || content.count > 200)
                }
            }
            .onAppear { content = AssistantRedaction.text(item?.text("content") ?? ""); enabled = item?["enabled"]?.boolValue ?? true }
        }
    }
}
