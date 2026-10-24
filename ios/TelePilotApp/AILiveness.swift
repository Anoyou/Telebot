import Foundation
import Observation
import SwiftUI
import TelePilotCore

private struct AIRetainedLiveness: Codable {
    var preview: JSONValue?
    var result: JSONValue?
    var runID: String?
    var providers: [Int]?
    var models: [Int: [String]]?
    var previewKey: String?
}

@MainActor @Observable
final class AILivenessStore {
    var selectedProviders: Set<Int> = []
    var selectedModels: [Int: Set<String>] = [:]
    var preview: JSONValue?
    var result: JSONValue?
    var runID: String?
    var maxTokens = 256
    var timeoutSeconds = 90
    var concurrency = 8
    var systemPrompt = "你是一个自然、简洁的中文聊天助手。请像真实聊天一样直接回复用户，不要只返回 ping/pong。"
    var message = "你怎么又不行啦？"
    var loadingPreview = false
    var starting = false
    var cancelling = false
    var polling = false
    var error: String?
    var pollError: String?
    var scopeReady = false

    @ObservationIgnored private var previewKey: String?
    @ObservationIgnored private var defaultsKey = ""
    @ObservationIgnored private var restored = false
    private var skipPersistence: Bool { ProcessInfo.processInfo.arguments.contains("--ui-testing") }

    var running: Bool { runID != nil || ["queued", "running"].contains(result?["status"]?.stringValue ?? "") }
    var canPreview: Bool { !running && !starting && !selectedProviders.isEmpty && selectedModelCount > 0 }
    var selectedModelCount: Int { selectedProviders.reduce(0) { $0 + (selectedModels[$1]?.count ?? 0) } }
    var hasSelectedScope: Bool { scopeReady && !selectedProviders.isEmpty }

    func start(_ model: AppModel, providers: [JSONValue], providerID: Int?) {
        guard !restored else { return }
        restored = true
        defaultsKey = "telepilot.ai.liveness.\(model.serverURL?.absoluteString ?? "").\(model.user?.id ?? 0)"
        if !skipPersistence, let data = UserDefaults.standard.data(forKey: defaultsKey),
           let retained = try? JSONDecoder().decode(AIRetainedLiveness.self, from: data) {
            preview = retained.preview
            result = retained.result
            runID = retained.runID
            selectedProviders = Set(retained.providers ?? [])
            selectedModels = (retained.models ?? [:]).reduce(into: [:]) { $0[$1.key] = Set($1.value) }
            previewKey = retained.previewKey
        }
        if let providerID {
            selectedProviders = [providerID]
            selectedModels[providerID] = Set(AIPresentation.enabledModels(providers.first { $0["id"]?.intValue == providerID } ?? [:]))
            invalidatePreview()
        }
        reconcile(providers)
        scopeReady = true
        persist()
    }

    func reconcile(_ providers: [JSONValue]) {
        let valid = Set(providers.compactMap { $0["id"]?.intValue })
        selectedProviders = selectedProviders.intersection(valid)
        for provider in providers {
            guard let id = provider["id"]?.intValue else { continue }
            let enabled = Set(AIPresentation.enabledModels(provider))
            if let current = selectedModels[id] { selectedModels[id] = current.intersection(enabled) }
            else { selectedModels[id] = enabled }
        }
        selectedModels = selectedModels.filter { valid.contains($0.key) }
        if selectedProviders.isEmpty, !scopeReady {
            selectedProviders = valid
            selectedModels = selectedModels.mapValues { $0 }
        }
    }

    func toggleProvider(_ id: Int, enabled: Bool, providers: [JSONValue]) {
        guard !running && !starting else { return }
        if enabled { selectedProviders.insert(id) } else { selectedProviders.remove(id) }
        if selectedModels[id] == nil, let provider = providers.first(where: { $0["id"]?.intValue == id }) {
            selectedModels[id] = Set(AIPresentation.enabledModels(provider))
        }
        invalidatePreview()
    }

    func toggleModel(_ providerID: Int, _ model: String, enabled: Bool) {
        guard !running && !starting else { return }
        if enabled { selectedModels[providerID, default: []].insert(model) }
        else { selectedModels[providerID, default: []].remove(model) }
        if selectedModels[providerID]?.isEmpty == true { selectedProviders.remove(providerID) }
        else { selectedProviders.insert(providerID) }
        invalidatePreview()
    }

    func updateSettings() { invalidatePreview() }

    func invalidatePreview() {
        preview = nil
        previewKey = nil
        error = nil
        persist()
    }

    func loadPreview(_ model: AppModel) async {
        guard canPreview, !loadingPreview else { return }
        loadingPreview = true; error = nil
        defer { loadingPreview = false }
        let requestKey = currentPreviewKey
        do {
            let value = try await model.request(path: "/api/commands/llm-providers/liveness/preview", method: "POST", body: .object(scopeBody))
            guard requestKey == currentPreviewKey else { return }
            preview = value; previewKey = requestKey
            persist()
        } catch { self.error = error.localizedDescription }
    }

    func startRun(_ model: AppModel) async {
        guard !running, !starting, preview != nil, previewKey == currentPreviewKey else { return }
        starting = true; error = nil
        defer { starting = false }
        var body = scopeBody
        body["system_prompt"] = .string(systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines))
        body["message"] = .string(message.trimmingCharacters(in: .whitespacesAndNewlines))
        body["timeout_seconds"] = .integer(Int64(timeoutSeconds))
        body["confirm_large_run"] = .bool(preview?["needs_confirmation"]?.boolValue == true)
        do {
            let response = try await model.request(path: "/api/commands/llm-providers/liveness/run", method: "POST", body: .object(body))
            guard let id = response["run_id"]?.stringValue else { throw APIError.incompatibleResponse }
            runID = id
            result = ["run_id": .string(id), "status": response["status"] ?? "queued",
                      "task_total": response["task_total"] ?? .integer(Int64(preview?["task_total"]?.intValue ?? selectedModelCount)),
                      "completed": 0, "healthy": 0, "failed": 0, "skipped": 0, "cancelled": 0, "results": []]
            persist()
        } catch {
            self.error = (error as? APIError)?.isOutcomeUncertain == true
                ? "连接中断，无法确认服务器是否已创建测活。请刷新运行状态，不要重复启动。"
                : error.localizedDescription
        }
    }

    func poll(_ model: AppModel) async {
        guard runID != nil, !polling else { return }
        polling = true; pollError = nil
        defer { polling = false }
        var failures = 0
        while let id = runID, !Task.isCancelled {
            do {
                let value = try await model.request(path: "/api/commands/llm-providers/liveness/\(ManagementText.pathSegment(id))")
                guard runID == id else { return }
                result = value; pollError = nil; failures = 0
                if !["queued", "running"].contains(value["status"]?.stringValue ?? "") {
                    runID = nil; persist(); return
                }
                persist()
                try await Task.sleep(for: .milliseconds(800))
            } catch is CancellationError { return }
            catch {
                if case APIError.server(status: 404, code: _) = error {
                    var current = result?.objectValue ?? ["run_id": .string(id), "task_total": 0]
                    current["status"] = "cancelled"
                    current["error"] = "测活运行不存在或已过期，已停止恢复进度。"
                    result = .object(current); runID = nil; persist(); return
                }
                failures += 1
                pollError = "进度暂时读取失败，将自动重试：\(error.localizedDescription)"
                do { try await Task.sleep(for: .seconds(min(15, Int(pow(2.0, Double(failures)))))) }
                catch { return }
            }
        }
    }

    func cancel(_ model: AppModel) async {
        guard let id = runID, !cancelling else { return }
        cancelling = true; error = nil
        defer { cancelling = false }
        do {
            let value = try await model.request(path: "/api/commands/llm-providers/liveness/\(ManagementText.pathSegment(id))/cancel", method: "POST")
            result = value
            if !["queued", "running"].contains(value["status"]?.stringValue ?? "") { runID = nil }
            persist()
        } catch { self.error = error.localizedDescription }
    }

    func pollOnce(_ model: AppModel) async {
        guard let id = runID else { return }
        do {
            let value = try await model.request(path: "/api/commands/llm-providers/liveness/\(ManagementText.pathSegment(id))")
            result = value
            if !["queued", "running"].contains(value["status"]?.stringValue ?? "") { runID = nil }
            persist()
        } catch { pollError = error.localizedDescription }
    }

    private var currentPreviewKey: String {
        let scope = selectedProviders.sorted().map { "\($0):\((selectedModels[$0] ?? []).sorted().joined(separator: ","))" }.joined(separator: "|")
        return "\(scope)#\(maxTokens)#\(concurrency)"
    }

    private var scopeBody: [String: JSONValue] {
        var models: [String: JSONValue] = [:]
        for id in selectedProviders.sorted() {
            models[String(id)] = .array((selectedModels[id] ?? []).sorted().map(JSONValue.string))
        }
        return ["max_tokens": .integer(Int64(maxTokens)), "global_concurrency": .integer(Int64(concurrency)),
                "only_provider_ids": .array(selectedProviders.sorted().map { .integer(Int64($0)) }),
                "models_by_provider": .object(models)]
    }

    private func persist() {
        guard !skipPersistence, !defaultsKey.isEmpty else { return }
        let retained = AIRetainedLiveness(preview: preview, result: result, runID: runID,
            providers: selectedProviders.sorted(), models: selectedModels.reduce(into: [:]) { $0[$1.key] = $1.value.sorted() },
            previewKey: previewKey)
        if let data = try? JSONEncoder().encode(retained) { UserDefaults.standard.set(data, forKey: defaultsKey) }
    }
}

struct AILivenessView: View {
    @Bindable var workspace: AIWorkspaceStore
    var providerID: Int?
    @Environment(AppModel.self) private var model
    @Environment(\.scenePhase) private var scenePhase
    @State private var liveness = AILivenessStore()
    @State private var confirming = false
    @State private var cancelling = false
    @State private var resultFilter = "all"

    var body: some View {
        List {
            if workspace.loading && workspace.providers.isEmpty { Section { ProgressView("正在读取供应商…") } }
            Section {
                Label(liveness.running ? "服务器正在执行测活" : "选择要测试的模型", systemImage: liveness.running ? "waveform.path.ecg" : "checklist")
                    .font(.headline)
                Text("测活会发送真实上游请求，不会自动启停模型，也不会清除业务调用的冷却状态。")
                    .font(.caption).foregroundStyle(.secondary)
            }

            Section {
                ForEach(workspace.providers, id: \.self) { provider in
                    if let id = provider["id"]?.intValue {
                        AILivenessProviderScope(provider: provider, id: id, liveness: liveness, providers: workspace.providers)
                    }
                }
                if workspace.providers.isEmpty, workspace.loaded { Text("请先添加供应商和模型。").foregroundStyle(.secondary) }
            } header: {
                Text("测试范围")
            } footer: {
                Text("已选 \(liveness.selectedProviders.count) 个供应商、\(liveness.selectedModelCount) 个模型。只包含已启用的模型。")
            }

            Section("测试设置") {
                Stepper("单模型最大输出：\(liveness.maxTokens) Token", value: Binding(get: { liveness.maxTokens }, set: { liveness.maxTokens = $0; liveness.updateSettings() }), in: 64...8000, step: 64)
                    .disabled(liveness.running || liveness.starting)
                Stepper("超时：\(liveness.timeoutSeconds) 秒", value: $liveness.timeoutSeconds, in: 10...600, step: 10)
                    .disabled(liveness.running || liveness.starting)
                Picker("全局并发", selection: Binding(get: { liveness.concurrency }, set: { liveness.concurrency = $0; liveness.updateSettings() })) {
                    ForEach([2, 4, 8, 12], id: \.self) { Text("\($0) 路").tag($0) }
                }
                .disabled(liveness.running || liveness.starting)
                TextField("测试语", text: $liveness.message, axis: .vertical).lineLimit(1...4)
                    .disabled(liveness.running || liveness.starting)
                DisclosureGroup("自定义系统提示词") {
                    TextField("系统提示词", text: $liveness.systemPrompt, axis: .vertical).lineLimit(2...8)
                        .disabled(liveness.running || liveness.starting)
                }
            }

            Section("操作") {
                Button(liveness.preview == nil ? "预览测试范围与消耗" : "重新预览当前范围", systemImage: "list.clipboard") {
                    Task { await liveness.loadPreview(model) }
                }
                .accessibilityIdentifier("ai.liveness.preview")
                .disabled(!liveness.canPreview || liveness.loadingPreview || liveness.message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .frame(minHeight: 44)
                .accessibilityHint(liveness.preview == nil ? "预览后会显示消耗与可执行范围" : "重新预览当前范围")
                if liveness.preview == nil { Text("预览后会显示消耗与可执行范围").font(.caption).foregroundStyle(.secondary) }
                if liveness.loadingPreview { ProgressView("服务器正在核对测试范围…") }
            }

            if let error = liveness.error { Section { NoticeView(title: "测活操作未完成", message: error) } }

            if let preview = liveness.preview { previewSection(preview) }
            if let result = liveness.result { resultSection(result) }

            Section {
                NavigationLink("恢复其他测活任务") { ManagementRunLookupView() }
            } footer: { Text("仅用于恢复其他设备或 Web 发起的任务。本 App 创建的测活会自动保留并恢复进度。") }
        }
        .navigationTitle("模型测活").navigationBarTitleDisplayMode(.inline)
        .task {
            if workspace.providers.isEmpty { await workspace.refresh(model) }
            liveness.start(model, providers: workspace.providers, providerID: providerID)
        }
        .onChange(of: workspace.providers) { _, providers in liveness.reconcile(providers) }
        .task(id: scenePhase) {
            guard scenePhase == .active, liveness.runID != nil else { return }
            await liveness.poll(model)
        }
        .onChange(of: liveness.runID) { _, id in
            guard id != nil, scenePhase == .active else { return }
            Task { await liveness.poll(model) }
        }
        .refreshable {
            await workspace.refresh(model)
            if liveness.runID != nil { await liveness.pollOnce(model) }
        }
        .confirmationDialog("开始模型测活？", isPresented: $confirming, titleVisibility: .visible) {
            Button(liveness.preview?["needs_confirmation"]?.boolValue == true ? "确认消耗并开始测活" : "开始测活") {
                Task { await liveness.startRun(model) }
            }
            Button("取消", role: .cancel) {}
        } message: {
            Text("将向 \(liveness.preview?["task_total"]?.intValue ?? 0) 个模型发送真实请求，最大可能输出约 \(AIPresentation.tokens(liveness.preview?["max_output_tokens"]?.intValue ?? 0)) Token。测试期间可能产生上游费用。")
        }
        .confirmationDialog("取消本次测活？", isPresented: $cancelling, titleVisibility: .visible) {
            Button("取消测活", role: .destructive) { Task { await liveness.cancel(model) } }
            Button("继续测活", role: .cancel) {}
        } message: { Text("将停止发起新任务并中断在途请求。已完成的结果会保留，已消耗的上游额度不会退回。") }
    }


    @ViewBuilder private func previewSection(_ preview: JSONValue) -> some View {
        Section("服务器预览") {
            HStack(spacing: 20) {
                AIMetric(title: "测试任务", value: String(preview["task_total"]?.intValue ?? 0))
                AIMetric(title: "输出上限", value: AIPresentation.tokens(preview["max_output_tokens"]?.intValue ?? 0), detail: "Token")
                AIMetric(title: "并发", value: "\(preview["global_concurrency"]?.intValue ?? 0) 路", detail: "单供应商最多 \(preview["provider_concurrency"]?.intValue ?? 0) 路")
            }
            ForEach(preview["providers"]?.arrayValue ?? [], id: \.self) { plan in
                VStack(alignment: .leading, spacing: 4) {
                    Text(plan["provider_name"]?.stringValue ?? "供应商").font(.subheadline.weight(.medium))
                    if plan["executable"]?.boolValue == true {
                        Text("\(plan["enabled_models"]?.arrayValue?.count ?? 0) 个模型可执行").font(.caption).foregroundStyle(.secondary)
                    } else {
                        Text(plan["skipped_reason"]?.stringValue.map(AIPresentation.status) ?? "没有可执行的启用模型")
                            .font(.caption).foregroundStyle(.orange)
                    }
                }
            }
            if preview["needs_confirmation"]?.boolValue == true {
                Label("任务数较多，开始时会再次明确确认上游消耗。", systemImage: "exclamationmark.triangle")
                    .font(.caption).foregroundStyle(.orange)
            }
            Button("按此范围开始测活", systemImage: "play.fill") { confirming = true }
                .disabled(liveness.running || liveness.starting || (preview["task_total"]?.intValue ?? 0) == 0
                    || liveness.message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty
                    || liveness.systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                .frame(minHeight: 44)
                .accessibilityIdentifier("ai.liveness.start")
            if liveness.starting { ProgressView("正在创建服务器测活任务…") }
        }
    }

    @ViewBuilder private func resultSection(_ result: JSONValue) -> some View {
        let results = result["results"]?.arrayValue ?? []
        let filtered = results.filter { item in
            switch resultFilter {
            case "healthy": return item["status"]?.stringValue == "healthy"
            case "failed": return item["skipped"]?.boolValue != true && item["status"]?.stringValue != "healthy" && item["status"]?.stringValue != "cancelled"
            case "skipped": return item["skipped"]?.boolValue == true || item["status"]?.stringValue == "cancelled"
            default: return true
            }
        }
        Section {
            if let error = result["error"]?.stringValue { NoticeView(title: "测活未完成", message: error) }
            if let pollError = liveness.pollError { NoticeView(title: "进度连接中断", message: pollError) }
            VStack(alignment: .leading, spacing: 10) {
                HStack {
                    Text(AIPresentation.status(result["status"]?.stringValue ?? "queued")).font(.headline)
                    Spacer()
                    Text("\(result["completed"]?.intValue ?? 0) / \(result["task_total"]?.intValue ?? 0)")
                        .monospacedDigit().foregroundStyle(.secondary)
                }
                ProgressView(value: Double(result["completed"]?.intValue ?? 0), total: Double(max(1, result["task_total"]?.intValue ?? 0)))
                HStack(spacing: 16) {
                    Text("正常 \(result["healthy"]?.intValue ?? 0)").foregroundStyle(.green)
                    Text("失败 \(result["failed"]?.intValue ?? 0)").foregroundStyle(result["failed"]?.intValue ?? 0 > 0 ? .red : .secondary)
                    Text("跳过 \(result["skipped"]?.intValue ?? 0)").foregroundStyle(.secondary)
                }.font(.caption).monospacedDigit()
            }
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("ai.liveness.progress")
            if liveness.running {
                Button("取消测活", role: .destructive) { cancelling = true }
                    .disabled(liveness.cancelling || liveness.runID == nil)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("ai.liveness.cancel")
                if liveness.cancelling { ProgressView("正在请求服务器取消…") }
            }
        } header: {
            Text("执行进度")
        } footer: {
            Text("任务在服务器执行，离开页面不会中断。前台自动刷新进度，断线后自动重试读取，不会重复启动任务。")
        }

        Section {
            Picker("结果筛选", selection: $resultFilter) {
                Text("全部 \(results.count)").tag("all")
                Text("正常 \(result["healthy"]?.intValue ?? 0)").tag("healthy")
                Text("失败 \(result["failed"]?.intValue ?? 0)").tag("failed")
                Text("跳过 \(result["skipped"]?.intValue ?? 0)").tag("skipped")
            }
            ForEach(filtered, id: \.self) { item in
                NavigationLink { AILivenessResultView(item: item) } label: { AILivenessResultRow(item: item) }
            }
            if filtered.isEmpty { Text(results.isEmpty ? "正在等待第一项结果。" : "当前筛选没有结果。").foregroundStyle(.secondary) }
        } header: { Text("逐项结果") }
    }
}

private struct AILivenessProviderScope: View {
    let provider: JSONValue
    let id: Int
    @Bindable var liveness: AILivenessStore
    let providers: [JSONValue]

    var body: some View {
        Toggle(isOn: Binding(get: { liveness.selectedProviders.contains(id) }, set: { liveness.toggleProvider(id, enabled: $0, providers: providers) })) {
            VStack(alignment: .leading, spacing: 3) {
                Text(AIPresentation.providerName(provider)).font(.subheadline.weight(.medium))
                Text("\(liveness.selectedModels[id]?.count ?? 0) / \(AIPresentation.enabledModels(provider).count) 个模型")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .disabled(liveness.running || liveness.starting)
        if liveness.selectedProviders.contains(id) {
            ForEach(AIPresentation.enabledModels(provider), id: \.self) { item in
                Toggle(item, isOn: Binding(get: { liveness.selectedModels[id]?.contains(item) == true }, set: { liveness.toggleModel(id, item, enabled: $0) }))
                    .font(.caption).padding(.leading, 16)
                    .disabled(liveness.running || liveness.starting)
            }
        }
    }
}

private struct AILivenessResultRow: View {
    let item: JSONValue
    var body: some View {
        let status = item["status"]?.stringValue ?? "unknown"
        VStack(alignment: .leading, spacing: 5) {
            HStack {
                Text(item["model_id"]?.stringValue ?? "未知模型").font(.subheadline.weight(.medium)).lineLimit(2)
                Spacer()
                AIStatusLabel(title: AIPresentation.status(status), symbol: status == "healthy" ? "checkmark.circle.fill" : "exclamationmark.circle.fill", color: AIPresentation.statusColor(status))
            }
            HStack {
                Text(item["provider_name"]?.stringValue ?? "未知供应商")
                Spacer()
                if let latency = item["latency_ms"]?.intValue, latency > 0 { Text("\(latency) ms").monospacedDigit() }
            }.font(.caption).foregroundStyle(.secondary)
            if let error = item["error"]?.stringValue, !error.isEmpty {
                Text(AssistantRedaction.text(error)).font(.caption).foregroundStyle(.red).lineLimit(2)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

struct AILivenessResultView: View {
    let item: JSONValue
    var body: some View {
        let status = item["status"]?.stringValue ?? "unknown"
        List {
            Section {
                LabeledContent("供应商", value: item["provider_name"]?.stringValue ?? "未知")
                LabeledContent("模型", value: item["model_id"]?.stringValue ?? "未知")
                LabeledContent("结果") { Text(AIPresentation.status(status)).foregroundStyle(AIPresentation.statusColor(status)) }
                LabeledContent("耗时", value: "\(item["latency_ms"]?.intValue ?? 0) ms")
                LabeledContent("Token", value: "\(item["input_tokens"]?.intValue ?? 0) 输入 / \(item["output_tokens"]?.intValue ?? 0) 输出")
            }
            if let preview = item["preview"]?.stringValue, !preview.isEmpty {
                Section("返回预览") { Text(AssistantRedaction.text(preview)).textSelection(.enabled) }
            }
            if let error = item["error"]?.stringValue, !error.isEmpty {
                Section("错误") { Text(AssistantRedaction.text(error)).foregroundStyle(.red).textSelection(.enabled) }
            }
            if let suggestion = item["suggestion"]?.stringValue, !suggestion.isEmpty {
                Section("建议") { Text(AssistantRedaction.text(suggestion)) }
            }
            Section("诊断详情") {
                ForEach(["status_code", "upstream_status_code", "upstream_error_code", "upstream_error_message",
                         "upstream_request_id", "client_request_id", "effective_api_format", "client_identity_profile",
                         "execution_backend", "gateway_version", "gateway_request_id", "gateway_stage"], id: \.self) { key in
                    if let value = item[key], !value.isNull, !ManagementText.rawScalar(value).isEmpty {
                        LabeledContent(ManagementText.label(key)) {
                            Text(ManagementText.rawScalar(value)).multilineTextAlignment(.trailing).textSelection(.enabled)
                        }
                    }
                }
            }
        }
        .navigationTitle("测活详情").navigationBarTitleDisplayMode(.inline)
    }
}
