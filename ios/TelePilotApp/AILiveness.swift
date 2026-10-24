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
    var error: String?
    var pollError: String?
    var scopeReady = false
    /// 非空表示“单个供应商测活”：范围只限这一个供应商，状态与“全部供应商巡检”分开保存。
    private(set) var scopedProviderID: Int?

    @ObservationIgnored private var previewKey: String?
    @ObservationIgnored private var defaultsKey = ""
    @ObservationIgnored private var restored = false
    @ObservationIgnored private var pollToken = UUID()
    private var skipPersistence: Bool { ProcessInfo.processInfo.arguments.contains("--ui-testing") }

    var running: Bool { runID != nil || ["queued", "running"].contains(result?["status"]?.stringValue ?? "") }
    var canPreview: Bool { !running && !starting && !selectedProviders.isEmpty && selectedModelCount > 0 }
    var selectedModelCount: Int { selectedProviders.reduce(0) { $0 + (selectedModels[$1]?.count ?? 0) } }
    var hasSelectedScope: Bool { scopeReady && !selectedProviders.isEmpty }

    func start(_ model: AppModel, providers: [JSONValue], providerID: Int?) {
        guard !restored else { return }
        restored = true
        scopedProviderID = providerID
        // 全部供应商巡检沿用原有键；单个供应商测活使用独立的键，互不覆盖范围与结果。
        defaultsKey = "telepilot.ai.liveness.\(model.serverURL?.absoluteString ?? "").\(model.user?.id ?? 0)"
            + (providerID.map { ".provider.\($0)" } ?? "")
        if !skipPersistence, let data = UserDefaults.standard.data(forKey: defaultsKey),
           let retained = try? JSONDecoder().decode(AIRetainedLiveness.self, from: data) {
            preview = retained.preview
            result = retained.result
            runID = retained.runID
            selectedProviders = Set(retained.providers ?? [])
            selectedModels = (retained.models ?? [:]).reduce(into: [:]) { $0[$1.key] = Set($1.value) }
            previewKey = retained.previewKey
        }
        if let providerID, !running {
            selectedProviders = [providerID]
            if selectedModels[providerID]?.isEmpty != false {
                selectedModels[providerID] = Set(AIPresentation.enabledModels(providers.first { $0["id"]?.intValue == providerID } ?? [:]))
            }
            invalidatePreview()
        }
        reconcile(providers)
        scopeReady = true
        persist()
    }

    func reconcile(_ providers: [JSONValue]) {
        guard !running, !starting, !loadingPreview else { return }
        let previousKey = currentPreviewKey
        var valid = Set(providers.compactMap { $0["id"]?.intValue })
        if let scopedProviderID { valid = valid.intersection([scopedProviderID]) }
        selectedProviders = selectedProviders.intersection(valid)
        for provider in providers {
            guard let id = provider["id"]?.intValue, valid.contains(id) else { continue }
            let enabled = Set(AIPresentation.enabledModels(provider))
            if let current = selectedModels[id] { selectedModels[id] = current.intersection(enabled) }
            else { selectedModels[id] = enabled }
        }
        selectedModels = selectedModels.filter { valid.contains($0.key) }
        if selectedProviders.isEmpty, !scopeReady {
            selectedProviders = valid
        }
        selectedProviders = selectedProviders.filter { selectedModels[$0]?.isEmpty == false }
        if previousKey != currentPreviewKey { invalidatePreview() }
    }

    func toggleProvider(_ id: Int, enabled: Bool, providers: [JSONValue]) {
        guard !running && !starting else { return }
        if enabled { selectedProviders.insert(id) } else { selectedProviders.remove(id) }
        if enabled, selectedModels[id]?.isEmpty != false, let provider = providers.first(where: { $0["id"]?.intValue == id }) {
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

    /// 选中全部供应商及其全部已启用模型。
    func selectAll(_ providers: [JSONValue]) {
        guard !running && !starting else { return }
        for provider in providers {
            guard let id = provider["id"]?.intValue, scopedProviderID == nil || scopedProviderID == id else { continue }
            let enabled = Set(AIPresentation.enabledModels(provider))
            guard !enabled.isEmpty else { continue }
            selectedModels[id] = enabled
            selectedProviders.insert(id)
        }
        invalidatePreview()
    }

    func clearSelection() {
        guard !running && !starting else { return }
        selectedProviders = []
        invalidatePreview()
    }

    func setAllModels(_ providerID: Int, provider: JSONValue, enabled: Bool) {
        guard !running && !starting else { return }
        if enabled {
            selectedModels[providerID] = Set(AIPresentation.enabledModels(provider))
            selectedProviders.insert(providerID)
        } else {
            selectedModels[providerID] = []
            selectedProviders.remove(providerID)
        }
        invalidatePreview()
    }

    /// 结果中“失败”的项：已实际发起、不是正常也不是取消。
    var failedItems: [JSONValue] {
        (result?["results"]?.arrayValue ?? []).filter(AILivenessStore.isFailure)
    }

    nonisolated static func isFailure(_ item: JSONValue) -> Bool {
        let status = item["status"]?.stringValue ?? ""
        return item["skipped"]?.boolValue != true && status != "healthy" && status != "cancelled" && !status.hasPrefix("skipped")
    }

    /// 与 Web 端结果筛选一致的分类：healthy / failed / skipped / cancelled。
    nonisolated static func category(_ item: JSONValue) -> String {
        let status = item["status"]?.stringValue ?? ""
        if status == "healthy" { return "healthy" }
        if status == "cancelled" { return "cancelled" }
        if item["skipped"]?.boolValue == true || status.hasPrefix("skipped") { return "skipped" }
        return "failed"
    }

    /// 把测试范围收窄到给定结果项（失败重测 / 单项重测）。返回是否有可测模型。
    @discardableResult
    func prepareRetry(_ items: [JSONValue]) -> Bool {
        guard !running && !starting else { return false }
        var scope: [Int: Set<String>] = [:]
        for item in items {
            guard let id = item["provider_id"]?.intValue, let modelID = item["model_id"]?.stringValue else { continue }
            scope[id, default: []].insert(modelID)
        }
        guard !scope.isEmpty else { return false }
        selectedProviders = Set(scope.keys)
        for (id, models) in scope { selectedModels[id] = models }
        invalidatePreview()
        return true
    }

    func clearResult() {
        guard !running && !starting else { return }
        result = nil
        pollError = nil
        persist()
    }

    func invalidatePreview() {
        preview = nil
        previewKey = nil
        error = nil
        persist()
    }

    /// 请服务器核对测试范围（只读，不发起上游请求）。成功返回 true。
    @discardableResult
    func loadPreview(_ model: AppModel) async -> Bool {
        guard canPreview, !loadingPreview else { return false }
        loadingPreview = true; error = nil
        defer { loadingPreview = false }
        let requestKey = currentPreviewKey
        do {
            let value = try await model.request(path: "/api/commands/llm-providers/liveness/preview", method: "POST", body: .object(scopeBody))
            guard requestKey == currentPreviewKey else { return false }
            preview = value; previewKey = requestKey
            persist()
            return true
        } catch {
            self.error = error.localizedDescription
            return false
        }
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
        guard let id = runID, !Task.isCancelled else { return }
        let token = UUID()
        pollToken = token
        pollError = nil
        var failures = 0
        while runID == id, pollToken == token, !Task.isCancelled {
            do {
                let value = try await model.request(path: "/api/commands/llm-providers/liveness/\(ManagementText.pathSegment(id))")
                guard runID == id, pollToken == token, !Task.isCancelled else { return }
                result = value; pollError = nil; failures = 0
                if !["queued", "running"].contains(value["status"]?.stringValue ?? "") {
                    runID = nil; persist(); return
                }
                persist()
                try await Task.sleep(for: .milliseconds(800))
            } catch is CancellationError { return }
            catch {
                guard runID == id, pollToken == token, !Task.isCancelled else { return }
                if case APIError.server(status: 404, code: _) = error {
                    var current = result?.objectValue ?? ["run_id": .string(id), "task_total": 0]
                    current["status"] = "cancelled"
                    current["error"] = "测活运行不存在或已过期，已停止恢复进度。"
                    result = .object(current); runID = nil; persist(); return
                }
                failures += 1
                pollError = "进度暂时读取失败，将自动重试：\(error.localizedDescription)"
                do { try await Task.sleep(for: .seconds(1 << min(4, failures))) }
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
            guard runID == id else { return }
            result = value
            if !["queued", "running"].contains(value["status"]?.stringValue ?? "") { runID = nil }
            persist()
        } catch { self.error = error.localizedDescription }
    }

    func pollOnce(_ model: AppModel) async {
        guard let id = runID else { return }
        do {
            let value = try await model.request(path: "/api/commands/llm-providers/liveness/\(ManagementText.pathSegment(id))")
            guard runID == id, !Task.isCancelled else { return }
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

private enum AILivenessSort: String, CaseIterable, Identifiable {
    case provider, latency, failuresFirst
    var id: String { rawValue }
    var title: String {
        switch self {
        case .provider: "按供应商分组"
        case .latency: "按耗时（快→慢）"
        case .failuresFirst: "失败优先"
        }
    }
}

private struct AILivenessGroup: Identifiable {
    let id: Int
    let name: String
    let items: [JSONValue]
    let healthy: Int
    let total: Int
    let statusCodes: [Int]
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
    @State private var sort: AILivenessSort = .provider
    @State private var showAdvanced = false
    @State private var providerSearch = ""

    /// 未指定供应商时为“全部供应商巡检”（对应 Web 的“全部 Provider 巡检”）。
    private var isPatrol: Bool { providerID == nil }
    private var scopedProvider: JSONValue? {
        guard let providerID else { return nil }
        return workspace.providers.first { $0["id"]?.intValue == providerID }
    }
    /// 有已启用模型、可以参与巡检的供应商。
    private var patrolCandidates: [JSONValue] {
        workspace.providers.filter { !AIPresentation.enabledModels($0).isEmpty }
    }
    private var scopeProviders: [JSONValue] {
        if let providerID { return workspace.providers.filter { $0["id"]?.intValue == providerID } }
        let query = providerSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return workspace.providers }
        return workspace.providers.filter { provider in
            [provider["name"]?.stringValue, provider["provider"]?.stringValue]
                .contains { $0?.localizedCaseInsensitiveContains(query) == true }
        }
    }
    private var allPatrolSelected: Bool {
        let candidates = patrolCandidates
        return !candidates.isEmpty && candidates.allSatisfy { provider in
            guard let id = provider["id"]?.intValue, liveness.selectedProviders.contains(id) else { return false }
            return (liveness.selectedModels[id]?.count ?? 0) == AIPresentation.enabledModels(provider).count
        }
    }
    private var pageTitle: String { isPatrol ? "全部供应商巡检" : "供应商测活" }

    private var editingLocked: Bool { liveness.running || liveness.starting }
    private var messageEmpty: Bool { liveness.message.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var promptEmpty: Bool { liveness.systemPrompt.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty }
    private var canStart: Bool { liveness.canPreview && !liveness.loadingPreview && !messageEmpty && !promptEmpty }
    private var activePollID: String? { scenePhase == .active ? liveness.runID : nil }

    var body: some View {
        ScrollViewReader { proxy in
            List {
                if workspace.loading && workspace.providers.isEmpty { Section { ProgressView("正在读取供应商…") } }
                intro
                scopeSection
                contentSection
                startSection
                if let preview = liveness.preview { skippedSection(preview) }
                if let result = liveness.result {
                    progressSection(result)
                    resultSections(result)
                }
                Section {
                    NavigationLink { ManagementRunLookupView() } label: {
                        Label("恢复其他设备发起的测活", systemImage: "arrow.uturn.backward.circle")
                    }
                } footer: { Text("仅用于查看 Web 端或其他设备发起的测活进度。本 App 发起的测活会自动保留并恢复进度。") }
            }
            .navigationTitle(pageTitle).navigationBarTitleDisplayMode(.inline)
            .task {
                if workspace.providers.isEmpty { await workspace.refresh(model) }
                liveness.start(model, providers: workspace.providers, providerID: providerID)
            }
            .onChange(of: workspace.providers) { _, providers in liveness.reconcile(providers) }
            .task(id: activePollID) {
                guard activePollID != nil else { return }
                await liveness.poll(model)
            }
            .onChange(of: liveness.runID) { _, id in
                guard id != nil else { return }
                withAnimation { proxy.scrollTo("ai.liveness.progressAnchor", anchor: .top) }
            }
            .refreshable {
                await workspace.refresh(model)
                if liveness.runID != nil { await liveness.pollOnce(model) }
            }
            .confirmationDialog(confirmationTitle, isPresented: $confirming, titleVisibility: .visible) {
                Button("确认并开始") { Task { await liveness.startRun(model) } }
                Button("取消", role: .cancel) {}
            } message: {
                Text(confirmationMessage)
            }
            .confirmationDialog("停止本次测活？", isPresented: $cancelling, titleVisibility: .visible) {
                Button("停止测活", role: .destructive) { Task { await liveness.cancel(model) } }
                Button("继续测活", role: .cancel) {}
            } message: { Text("将不再发起新的请求，并中断正在进行的请求。已完成的结果会保留，已产生的上游费用不会退回。") }
        }
    }

    // MARK: - 说明与范围

    private var intro: some View {
        Section {
            HStack(alignment: .top) {
                Label(liveness.running ? "服务器正在测活" : isPatrol ? "一次检查所有供应商" : "检查这个供应商的模型",
                      systemImage: liveness.running ? "waveform.path.ecg" : isPatrol ? "checklist" : "stethoscope")
                    .font(.headline)
                Spacer(minLength: 4)
                AIHelpButton(title: "什么是测活", message: AIGlossary.terms.first { $0.title == "测活" }?.detail ?? "")
            }
            Text(isPatrol
                 ? "全部供应商巡检会对所选供应商里每个已启用的模型，并发发送同一句测试语，按供应商分组显示是否正常、耗时多久、失败原因。默认选中全部供应商，也可以只挑其中几个。"
                 : "只检查“\(scopedProvider.map(AIPresentation.providerName) ?? "当前供应商")”的已启用模型。想一次检查所有供应商，请使用下方“全部供应商巡检”。")
                .font(.caption).foregroundStyle(.secondary)
            Text("测活不会自动启用或停用模型，也不会清除业务调用的冷却状态。")
                .font(.caption).foregroundStyle(.secondary)
            if !isPatrol {
                NavigationLink {
                    AILivenessView(workspace: workspace)
                } label: {
                    Label("改为全部供应商巡检", systemImage: "checklist")
                }
                .disabled(liveness.starting)
                .accessibilityIdentifier("ai.liveness.openPatrol")
            }
        }
    }

    private var scopeSection: some View {
        Section {
            if isPatrol {
                LabeledContent {
                    Text("\(liveness.selectedProviders.count)/\(patrolCandidates.count) 个供应商 · \(liveness.selectedModelCount) 个模型")
                        .monospacedDigit()
                } label: {
                    Label(allPatrolSelected ? "已选中全部供应商" : "部分供应商", systemImage: allPatrolSelected ? "checkmark.circle.fill" : "circle.lefthalf.filled")
                }
                .accessibilityIdentifier("ai.liveness.scopeSummary")
                HStack {
                    Button("选中全部供应商", systemImage: "checkmark.circle") { liveness.selectAll(workspace.providers) }
                        .disabled(allPatrolSelected)
                    Spacer()
                    Button("全不选", systemImage: "circle") { liveness.clearSelection() }
                        .disabled(liveness.selectedProviders.isEmpty)
                }
                .buttonStyle(.borderless)
                .disabled(editingLocked || patrolCandidates.isEmpty)
                if workspace.providers.count > 5 {
                    TextField("搜索供应商名称或类型", text: $providerSearch)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                }
            }
            ForEach(scopeProviders, id: \.self) { provider in
                if let id = provider["id"]?.intValue {
                    AILivenessProviderScope(provider: provider, id: id, liveness: liveness, providers: workspace.providers,
                                            locked: editingLocked, initiallyExpanded: !isPatrol)
                }
            }
            if workspace.providers.isEmpty, workspace.loaded {
                Text("还没有供应商。请先在“供应商”里添加供应商并启用模型。").foregroundStyle(.secondary)
            } else if isPatrol, scopeProviders.isEmpty, workspace.loaded {
                Text("没有匹配的供应商").foregroundStyle(.secondary)
            } else if !isPatrol, scopedProvider == nil, workspace.loaded {
                Text("这个供应商已不存在，请返回供应商列表刷新。").foregroundStyle(.secondary)
            }
        } header: {
            Text(isPatrol ? "巡检范围" : "测试范围")
        } footer: {
            Text("已选 \(liveness.selectedProviders.count) 个供应商、\(liveness.selectedModelCount) 个模型。这里只列出模型清单中已启用的模型；没有启用模型的供应商不能参与。")
        }
    }

    private var contentSection: some View {
        Section {
            VStack(alignment: .leading, spacing: 4) {
                Text("测试语").font(.caption).foregroundStyle(.secondary)
                TextField("发送给每个模型的一句话", text: $liveness.message, axis: .vertical)
                    .lineLimit(1...4)
                    .disabled(editingLocked)
            }
            DisclosureGroup("高级参数", isExpanded: $showAdvanced) {
                Stepper(value: Binding(get: { liveness.maxTokens }, set: { liveness.maxTokens = $0; liveness.updateSettings() }), in: 64...8000, step: 64) {
                    AISettingLabel(title: "最大回复长度：\(liveness.maxTokens) 词元", detail: "限制每个模型最多回复多长。越大越能看出完整回复，也可能花费更多。")
                }
                .disabled(editingLocked)
                Stepper(value: $liveness.timeoutSeconds, in: 10...600, step: 10) {
                    AISettingLabel(title: "超时：\(liveness.timeoutSeconds) 秒", detail: "超过这个时间还没有回复，就记为“超时”。")
                }
                .disabled(editingLocked)
                Picker(selection: Binding(get: { liveness.concurrency }, set: { liveness.concurrency = $0; liveness.updateSettings() })) {
                    ForEach([2, 4, 8, 12], id: \.self) { Text("\($0) 个").tag($0) }
                } label: {
                    AISettingLabel(title: "同时测试的模型数", detail: "越大越快，但更容易触发供应商限流。")
                }
                .disabled(editingLocked)
                VStack(alignment: .leading, spacing: 4) {
                    Text("系统提示词（告诉模型该如何回答）").font(.caption).foregroundStyle(.secondary)
                    TextField("系统提示词", text: $liveness.systemPrompt, axis: .vertical)
                        .lineLimit(2...8)
                        .disabled(editingLocked)
                }
            }
        } header: {
            Text("测试内容")
        } footer: {
            Text("所有模型收到完全相同的测试语，不带任何聊天历史，方便横向比较。")
        }
    }

    private var startTitle: String {
        if liveness.loadingPreview { return "正在核对测试范围…" }
        if liveness.starting { return "正在创建测活任务…" }
        if liveness.running { return "测活进行中" }
        return isPatrol ? "开始巡检（\(liveness.selectedModelCount) 个模型）" : "开始测活（\(liveness.selectedModelCount) 个模型）"
    }

    private var startSection: some View {
        Section {
            Button {
                requestStart()
            } label: {
                HStack(spacing: 8) {
                    Spacer(minLength: 0)
                    if liveness.loadingPreview || liveness.starting { ProgressView() }
                    Label(startTitle, systemImage: "play.fill").font(.headline)
                    Spacer(minLength: 0)
                }
                .frame(minHeight: 44)
            }
            .disabled(!canStart)
            .accessibilityIdentifier("ai.liveness.start")
            if messageEmpty || promptEmpty {
                Text(messageEmpty ? "请先填写测试语。" : "请先填写系统提示词。").font(.caption).foregroundStyle(.orange)
            }
            if let error = liveness.error { NoticeView(title: "测活未能开始", message: error) }
        } footer: {
            Text("点按后服务器会先核对实际可测的模型数量和最大花费，你确认后才会发送真实请求，可能产生上游费用。")
        }
    }

    private var confirmationTitle: String {
        liveness.preview?["needs_confirmation"]?.boolValue == true ? "确认执行大规模巡检？" : (isPatrol ? "开始全部供应商巡检？" : "开始模型测活？")
    }

    private var confirmationMessage: String {
        let preview = liveness.preview
        let providers = preview?["executable_provider_total"]?.intValue ?? liveness.selectedProviders.count
        var text = "将向 \(providers) 个供应商的 \(preview?["task_total"]?.intValue ?? 0) 个模型各发送一次真实请求（最多同时 \(preview?["global_concurrency"]?.intValue ?? liveness.concurrency) 个"
        if let perProvider = preview?["provider_concurrency"]?.intValue, perProvider > 0 { text += "，每个供应商最多同时 \(perProvider) 个" }
        text += "），全部回复合计最多约 \(AIPresentation.tokens(preview?["max_output_tokens"]?.intValue ?? 0)) 词元，会产生上游费用。"
        if preview?["needs_confirmation"]?.boolValue == true { text += "\n本次任务较多，会消耗较多真实上游额度，请确认范围和回复长度上限无误。" }
        return text
    }

    private func requestStart() {
        guard !liveness.loadingPreview, !editingLocked else { return }
        guard liveness.canPreview else {
            liveness.error = "所选模型已不在启用列表中，请重新选择测试范围。"
            return
        }
        Task {
            guard await liveness.loadPreview(model) else { return }
            if (liveness.preview?["task_total"]?.intValue ?? 0) > 0 { confirming = true }
            else { liveness.error = "服务器核对后没有可以测试的模型。请检查供应商密钥是否有效、模型是否已启用。" }
        }
    }

    private func retry(_ items: [JSONValue]) {
        guard !editingLocked, !liveness.loadingPreview else { return }
        guard liveness.prepareRetry(items) else { return }
        resultFilter = "all"
        requestStart()
    }

    @ViewBuilder private func skippedSection(_ preview: JSONValue) -> some View {
        let skipped = (preview["providers"]?.arrayValue ?? []).filter { $0["executable"]?.boolValue != true }
        if !skipped.isEmpty {
            Section {
                ForEach(skipped, id: \.self) { plan in
                    Label {
                        VStack(alignment: .leading, spacing: 3) {
                            Text(plan["provider_name"]?.stringValue ?? "供应商").font(.subheadline.weight(.medium))
                            Text(plan["skipped_reason"]?.stringValue.map(AIPresentation.status) ?? "没有可执行的启用模型")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                    } icon: {
                        Image(systemName: "exclamationmark.triangle").foregroundStyle(.orange)
                    }
                }
            } header: { Text("将被跳过的供应商") } footer: { Text("这些供应商缺少可用密钥或没有启用的模型，本次不会发送请求。") }
        }
    }

    // MARK: - 进度与结果

    @ViewBuilder private func progressSection(_ result: JSONValue) -> some View {
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
                    Label("正常 \(result["healthy"]?.intValue ?? 0)", systemImage: "checkmark.circle").foregroundStyle(.green)
                    Label("失败 \(result["failed"]?.intValue ?? 0)", systemImage: "xmark.circle")
                        .foregroundStyle((result["failed"]?.intValue ?? 0) > 0 ? Color.red : Color.secondary)
                    Label("跳过 \(result["skipped"]?.intValue ?? 0)", systemImage: "minus.circle").foregroundStyle(.secondary)
                    if (result["cancelled"]?.intValue ?? 0) > 0 {
                        Label("取消 \(result["cancelled"]?.intValue ?? 0)", systemImage: "stop.circle").foregroundStyle(.secondary)
                    }
                }
                .font(.caption).monospacedDigit()
            }
            .id("ai.liveness.progressAnchor")
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("ai.liveness.progress")
            if liveness.running {
                Button(role: .destructive) { cancelling = true } label: {
                    Label("停止测活", systemImage: "stop.circle")
                }
                .disabled(liveness.cancelling || liveness.runID == nil)
                .frame(minHeight: 44)
                .accessibilityIdentifier("ai.liveness.cancel")
                if liveness.cancelling { ProgressView("正在请求服务器停止…") }
            }
        } header: {
            Text("执行进度")
        } footer: {
            Text("测活在服务器上执行，离开页面或切到后台不会中断。回到前台后自动刷新进度；网络中断时只会重新读取进度，不会重复发起测活。")
        }
    }

    private func matches(_ item: JSONValue) -> Bool {
        resultFilter == "all" || AILivenessStore.category(item) == resultFilter
    }

    private func ordered(_ items: [JSONValue]) -> [JSONValue] {
        switch sort {
        case .provider:
            return items
        case .latency:
            return items.sorted { lhs, rhs in
                let left = lhs["latency_ms"]?.intValue ?? 0, right = rhs["latency_ms"]?.intValue ?? 0
                if (left > 0) != (right > 0) { return left > 0 }
                return left < right
            }
        case .failuresFirst:
            return items.sorted { AILivenessStore.isFailure($0) && !AILivenessStore.isFailure($1) }
        }
    }

    private func groups(_ items: [JSONValue], all: [JSONValue]) -> [AILivenessGroup] {
        var order: [Int] = []
        var names: [Int: String] = [:]
        for item in items {
            let id = item["provider_id"]?.intValue ?? 0
            if names[id] == nil { order.append(id); names[id] = item["provider_name"]?.stringValue ?? "未知供应商" }
        }
        return order.map { id in
            let providerAll = all.filter { ($0["provider_id"]?.intValue ?? 0) == id }
            let codes = Set(providerAll.compactMap(AILivenessResultRow.httpStatus)).sorted()
            return AILivenessGroup(id: id, name: names[id] ?? "未知供应商",
                items: items.filter { ($0["provider_id"]?.intValue ?? 0) == id },
                healthy: providerAll.filter { $0["status"]?.stringValue == "healthy" }.count,
                total: providerAll.count, statusCodes: codes)
        }
    }

    @ViewBuilder private func resultSections(_ result: JSONValue) -> some View {
        let results = result["results"]?.arrayValue ?? []
        let failed = results.filter(AILivenessStore.isFailure)
        let skippedCount = results.filter { AILivenessStore.category($0) == "skipped" }.count
        let cancelledCount = results.filter { AILivenessStore.category($0) == "cancelled" }.count
        let filtered = ordered(results.filter(matches))
        Section {
            if results.isEmpty {
                Text(liveness.running ? "正在等待第一项结果…" : "本次测活没有返回逐项结果。").foregroundStyle(.secondary)
            } else {
                Picker("结果筛选", selection: $resultFilter) {
                    Text("全部").tag("all")
                    Text("正常").tag("healthy")
                    Text("失败 \(failed.count)").tag("failed")
                    if skippedCount > 0 || resultFilter == "skipped" { Text("跳过 \(skippedCount)").tag("skipped") }
                    if cancelledCount > 0 || resultFilter == "cancelled" { Text("取消 \(cancelledCount)").tag("cancelled") }
                }
                .pickerStyle(.segmented)
                Picker(selection: $sort) {
                    ForEach(AILivenessSort.allCases) { Text($0.title).tag($0) }
                } label: {
                    Label("排序", systemImage: "arrow.up.arrow.down")
                }
                if !failed.isEmpty {
                    Button { retry(failed) } label: {
                        Label("重新测试失败的 \(failed.count) 个模型", systemImage: "arrow.clockwise")
                    }
                    .disabled(editingLocked || liveness.loadingPreview)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("ai.liveness.retryFailed")
                }
            }
            if !liveness.running && !liveness.starting {
                Button(role: .destructive) {
                    liveness.clearResult()
                    resultFilter = "all"
                } label: {
                    Label("清空本次结果", systemImage: "trash")
                }
                .accessibilityIdentifier("ai.liveness.clearResult")
            }
        } header: {
            Text("逐项结果")
        } footer: {
            if !results.isEmpty { Text("左滑某一项可以单独重测。重测会新建一次只包含所选模型的测活，并替换当前结果。") }
        }

        if !results.isEmpty {
            if filtered.isEmpty {
                Section { Text("当前筛选下没有结果。").foregroundStyle(.secondary) }
            } else if sort == .provider {
                ForEach(groups(filtered, all: results)) { group in
                    Section {
                        ForEach(Array(group.items.enumerated()), id: \.offset) { _, item in resultRow(item) }
                    } header: {
                        HStack {
                            Text(group.name)
                            Spacer()
                            if !group.statusCodes.isEmpty {
                                Text(group.statusCodes.map { "HTTP \($0)" }.joined(separator: " "))
                            }
                            Text("正常 \(group.healthy)/\(group.total)")
                                .foregroundStyle(group.healthy == group.total ? Color.green : Color.orange)
                        }
                        .monospacedDigit()
                    }
                }
            } else {
                Section {
                    ForEach(Array(filtered.enumerated()), id: \.offset) { _, item in resultRow(item) }
                }
            }
        }
    }

    private func resultRow(_ item: JSONValue) -> some View {
        NavigationLink {
            AILivenessResultView(item: item, onRetry: { retry([item]) })
        } label: {
            AILivenessResultRow(item: item)
        }
        .swipeActions(edge: .trailing) {
            Button { retry([item]) } label: { Label("重测", systemImage: "arrow.clockwise") }
                .tint(Color.accentColor)
                .disabled(editingLocked)
        }
        .contextMenu {
            Button { retry([item]) } label: { Label("单独重测此模型", systemImage: "arrow.clockwise") }
                .disabled(editingLocked)
        }
    }
}

/// 标题 + 灰色解释的组合标签，用于设置项。
struct AISettingLabel: View {
    let title: String
    let detail: String
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            Text(title)
            Text(detail).font(.caption).foregroundStyle(.secondary)
        }
    }
}

private struct AILivenessProviderScope: View {
    let provider: JSONValue
    let id: Int
    @Bindable var liveness: AILivenessStore
    let providers: [JSONValue]
    let locked: Bool
    @State private var expanded: Bool

    init(provider: JSONValue, id: Int, liveness: AILivenessStore, providers: [JSONValue], locked: Bool, initiallyExpanded: Bool = false) {
        self.provider = provider
        self.id = id
        _liveness = Bindable(liveness)
        self.providers = providers
        self.locked = locked
        _expanded = State(initialValue: initiallyExpanded)
    }

    var body: some View {
        let models = AIPresentation.enabledModels(provider)
        let isSelected = liveness.selectedProviders.contains(id)
        let selectedCount = isSelected ? (liveness.selectedModels[id]?.count ?? 0) : 0
        Toggle(isOn: Binding(get: { isSelected }, set: { liveness.toggleProvider(id, enabled: $0, providers: providers) })) {
            VStack(alignment: .leading, spacing: 3) {
                Text(AIPresentation.providerName(provider)).font(.subheadline.weight(.medium))
                Text(models.isEmpty ? "没有启用的模型" : "\(AIPresentation.providerType(provider["provider"]?.stringValue)) · 已选 \(selectedCount)/\(models.count) 个模型")
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .disabled(locked || models.isEmpty)
        .accessibilityIdentifier("ai.liveness.provider.\(id)")
        if isSelected, models.count > 1 {
            DisclosureGroup(isExpanded: $expanded) {
                HStack {
                    Button("全部选中", systemImage: "checkmark.circle") { liveness.setAllModels(id, provider: provider, enabled: true) }
                    Spacer()
                    Button("全部取消", systemImage: "circle") { liveness.setAllModels(id, provider: provider, enabled: false) }
                }
                .buttonStyle(.borderless)
                .font(.caption)
                .disabled(locked)
                ForEach(models, id: \.self) { item in
                    Toggle(item, isOn: Binding(get: { liveness.selectedModels[id]?.contains(item) == true }, set: { liveness.toggleModel(id, item, enabled: $0) }))
                        .font(.caption)
                        .disabled(locked)
                }
            } label: {
                Label("单独挑选模型", systemImage: "slider.horizontal.3").font(.caption)
            }
            .padding(.leading, 16)
        }
    }
}

struct AILivenessResultRow: View {
    let item: JSONValue

    /// 只展示后端已结构化确认的 HTTP 状态码，不从错误文案中猜测。
    static func httpStatus(_ item: JSONValue) -> Int? {
        guard let code = item["upstream_status_code"]?.intValue ?? item["status_code"]?.intValue, (100...599).contains(code) else { return nil }
        return code
    }

    var body: some View {
        let status = item["status"]?.stringValue ?? "unknown"
        let skipped = item["skipped"]?.boolValue == true || status == "cancelled" || status.hasPrefix("skipped")
        let code = Self.httpStatus(item)
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline) {
                Text(item["model_id"]?.stringValue ?? "未知模型").font(.subheadline.weight(.medium)).lineLimit(2)
                Spacer(minLength: 6)
                AIStatusLabel(title: AIPresentation.status(status) + (code.map { " · \($0)" } ?? ""),
                    symbol: status == "healthy" ? "checkmark.circle.fill" : skipped ? "minus.circle" : "exclamationmark.circle.fill",
                    color: AIPresentation.statusColor(status))
            }
            HStack(spacing: 8) {
                Text(item["provider_name"]?.stringValue ?? "未知供应商").lineLimit(1)
                Spacer(minLength: 4)
                if !skipped {
                    Label(AIPresentation.latency(item["latency_ms"]?.intValue), systemImage: "timer").monospacedDigit()
                }
            }
            .font(.caption).foregroundStyle(.secondary)
            if let format = item["effective_api_format"]?.stringValue, !format.isEmpty {
                Text("接口：\(AIPresentation.apiFormat(format)) · 身份：\(AIPresentation.clientIdentity(item["client_identity_profile"]?.stringValue))")
                    .font(.caption2).foregroundStyle(.secondary).lineLimit(2)
            } else if skipped {
                Text("未发起请求").font(.caption2).foregroundStyle(.secondary)
            }
            if let error = item["error"]?.stringValue, !error.isEmpty {
                Text(AssistantRedaction.text(error)).font(.caption).foregroundStyle(.red).lineLimit(2)
            }
        }
        .accessibilityElement(children: .combine)
    }
}

struct AILivenessResultView: View {
    let item: JSONValue
    var onRetry: (() -> Void)? = nil
    @Environment(\.dismiss) private var dismiss

    private static let diagnosticLabels: [(String, String)] = [
        ("status_code", "HTTP 状态码"), ("upstream_status_code", "上游 HTTP 状态码"),
        ("error_category", "错误分类"), ("upstream_error_code", "上游错误码"),
        ("upstream_error_message", "上游错误信息"), ("upstream_request_id", "上游请求编号"),
        ("client_request_id", "本地请求编号"), ("effective_api_format", "实际接口协议"),
        ("client_identity_profile", "实际客户端身份"), ("execution_backend", "实际调用方式"),
        ("gateway_version", "内置网关版本"), ("gateway_request_id", "内置网关请求编号"), ("gateway_stage", "内置网关阶段")
    ]

    private func display(_ key: String, _ value: JSONValue) -> String {
        let raw = ManagementText.rawScalar(value)
        switch key {
        case "effective_api_format": return AIPresentation.apiFormat(raw)
        case "client_identity_profile": return AIPresentation.clientIdentity(raw)
        case "execution_backend": return AIPresentation.executionBackend(raw)
        case "error_category": return AIPresentation.status(raw)
        default: return raw
        }
    }

    var body: some View {
        let status = item["status"]?.stringValue ?? "unknown"
        List {
            Section {
                LabeledContent("供应商", value: item["provider_name"]?.stringValue ?? "未知")
                LabeledContent("模型", value: item["model_id"]?.stringValue ?? "未知")
                LabeledContent("结果") { Text(AIPresentation.status(status)).foregroundStyle(AIPresentation.statusColor(status)) }
                LabeledContent("耗时", value: AIPresentation.latency(item["latency_ms"]?.intValue))
                LabeledContent("词元用量", value: "输入 \(item["input_tokens"]?.intValue ?? 0) · 输出 \(item["output_tokens"]?.intValue ?? 0)")
            }
            if let preview = item["preview"]?.stringValue, !preview.isEmpty {
                Section("模型回复") { Text(AssistantRedaction.text(preview)).textSelection(.enabled) }
            }
            if let error = item["error"]?.stringValue, !error.isEmpty {
                Section("错误原因") { Text(AssistantRedaction.text(error)).foregroundStyle(.red).textSelection(.enabled) }
            }
            if let suggestion = item["suggestion"]?.stringValue, !suggestion.isEmpty, suggestion != item["error"]?.stringValue {
                Section("处理建议") { Text(AssistantRedaction.text(suggestion)) }
            }
            if let onRetry {
                Section {
                    Button {
                        onRetry()
                        dismiss()
                    } label: { Label("单独重测此模型", systemImage: "arrow.clockwise") }
                } footer: { Text("会新建一次只包含此模型的测活，开始前仍会请你确认。") }
            }
            Section {
                ForEach(Array(Self.diagnosticLabels.enumerated()), id: \.offset) { _, pair in
                    if let value = item[pair.0], !value.isNull, !ManagementText.rawScalar(value).isEmpty {
                        LabeledContent(pair.1) {
                            Text(AssistantRedaction.text(display(pair.0, value))).multilineTextAlignment(.trailing).textSelection(.enabled)
                        }
                    }
                }
            } header: { Text("诊断详情") } footer: { Text("向供应商或中转站反馈问题时，可以附上请求编号。") }
        }
        .navigationTitle("测活详情").navigationBarTitleDisplayMode(.inline)
    }
}
