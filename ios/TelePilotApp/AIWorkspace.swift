import SwiftUI
import TelePilotCore

@MainActor @Observable
final class AIWorkspaceStore {
    var settings: JSONValue?
    var providers: [JSONValue] = []
    var health: [JSONValue] = []
    var usage: JSONValue?
    var pluginUsage: [JSONValue] = []
    var enablement: JSONValue?
    var errors: [String: String] = [:]
    var loaded = false
    var loading = false

    var aiEnabled: Bool { settings?["ai_enabled"]?.boolValue != false }
    var readyProviders: [JSONValue] { providers.filter(AIPresentation.ready) }
    var unhealthy: [JSONValue] { health.filter(AIPresentation.hasIssue) }
    var usageItems: [JSONValue] {
        usage?.arrayValue ?? usage?["items"]?.arrayValue ?? []
    }
    var usageSummary: JSONValue {
        if let summary = usage?["summary"] { return summary }
        let items = usageItems
        let success = items.filter { $0["success"]?.boolValue == true }.count
        let input = items.reduce(0) { $0 + ($1["input_tokens"]?.intValue ?? 0) }
        let output = items.reduce(0) { $0 + ($1["output_tokens"]?.intValue ?? 0) }
        let latency = items.reduce(0) { $0 + ($1["latency_ms"]?.intValue ?? 0) }
        return .object([
            "request_count": .integer(Int64(items.count)),
            "success_count": .integer(Int64(success)),
            "failed_count": .integer(Int64(items.count - success)),
            "total_tokens": .integer(Int64(input + output)),
            "fallback_count": .integer(Int64(items.filter { $0["used_fallback"]?.boolValue == true }.count)),
            "avg_latency_ms": .integer(Int64(items.isEmpty ? 0 : latency / items.count))
        ])
    }

    func refresh(_ model: AppModel) async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        async let settingsResult = fetch(model, "/api/system/settings")
        async let providersResult = fetch(model, "/api/commands/llm-providers")
        async let healthResult = fetch(model, "/api/commands/llm-providers/runtime-health")
        async let usageResult = fetch(model, "/api/llm/usage/recent?limit=100")
        async let pluginResult = fetch(model, "/api/llm/usage/plugins/summary?limit=100")
        async let enablementResult = fetch(model, "/api/commands/ai/enablement-summary")
        let results = await (settingsResult, providersResult, healthResult, usageResult, pluginResult, enablementResult)
        apply(results.0, key: "settings") { settings = $0 }
        apply(results.1, key: "providers") { providers = $0.arrayValue ?? $0["items"]?.arrayValue ?? [] }
        apply(results.2, key: "health") { health = $0.arrayValue ?? [] }
        apply(results.3, key: "usage") { usage = $0 }
        apply(results.4, key: "pluginUsage") { pluginUsage = $0.arrayValue ?? $0["items"]?.arrayValue ?? [] }
        apply(results.5, key: "enablement") { enablement = $0 }
        loaded = true
    }

    func replaceProvider(_ value: JSONValue) {
        guard let id = value["id"]?.intValue else { return }
        if let index = providers.firstIndex(where: { $0["id"]?.intValue == id }) { providers[index] = value }
        else { providers.append(value) }
    }

    private func apply(_ result: Result<JSONValue, Error>, key: String, update: (JSONValue) -> Void) {
        switch result {
        case .success(let value): update(value); errors[key] = nil
        case .failure(let error): errors[key] = error.localizedDescription
        }
    }

    private func fetch(_ model: AppModel, _ path: String) async -> Result<JSONValue, Error> {
        do { return .success(try await model.request(path: path)) }
        catch { return .failure(error) }
    }
}

private enum AISection: String, CaseIterable, Identifiable {
    case overview, providers, usage
    var id: String { rawValue }
    var title: String { ["overview": "概览", "providers": "供应商", "usage": "用量"][rawValue]! }
}

struct AIWorkspaceView: View {
    @Environment(AppModel.self) private var model
    @State private var workspace = AIWorkspaceStore()
    @State private var section: AISection = .overview
    @State private var showLiveness = false

    var body: some View {
        List {
            Section {
                Picker("AI 工作区", selection: $section) {
                    ForEach(AISection.allCases) { item in Text(item.title).tag(item) }
                }
                .pickerStyle(.segmented)
                .accessibilityIdentifier("ai.sections")
            }
            .listRowInsets(EdgeInsets(top: 12, leading: 16, bottom: 8, trailing: 16))
            .listRowSeparator(.hidden)

            switch section {
            case .overview: AIOverviewSection(workspace: workspace, showUsage: { section = .usage }, showLiveness: { showLiveness = true })
            case .providers: AIProviderListSection(workspace: workspace)
            case .usage: AIUsageSection(workspace: workspace)
            }
        }
        .navigationTitle("AI")
        .navigationDestination(isPresented: $showLiveness) {
            AILivenessView(workspace: workspace)
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("刷新", systemImage: "arrow.clockwise") { Task { await workspace.refresh(model) } }
                    .disabled(workspace.loading)
                    .accessibilityIdentifier("ai.refresh")
            }
        }
        .task { await workspace.refresh(model) }
        .refreshable { await workspace.refresh(model) }
    }
}

private struct AIOverviewSection: View {
    @Bindable var workspace: AIWorkspaceStore
    let showUsage: () -> Void
    let showLiveness: () -> Void

    var body: some View {
        if workspace.loading && !workspace.loaded { Section { ProgressView("正在读取 AI 状态…") } }
        AIWorkspaceErrors(workspace: workspace)
        if workspace.loaded, !workspace.aiEnabled {
            Section {
                Label("AI 能力已关闭", systemImage: "power")
                    .font(.headline).foregroundStyle(.orange)
                Text("模型调用、AI 指令和插件 AI 已停用。已有供应商与模板仍保留，重新启用后可以继续使用。")
                    .font(.subheadline).foregroundStyle(.secondary)
                NavigationLink("检查平台与 AI 开关") {
                    ManagementDocumentView(title: "服务器设置", path: "/api/system/settings", actions: [
                        .init(title: "修改服务器设置", path: "/api/system/settings", method: "PATCH")
                    ])
                }
            }
        } else if workspace.loaded {
            status
            actions
            runtime
            recent
            configuration
        }
    }

    private var status: some View {
        Section {
            HStack(alignment: .top, spacing: 20) {
                AIMetric(title: "凭据就绪", value: "\(workspace.readyProviders.count)/\(workspace.providers.count)", detail: "供应商")
                AIMetric(title: "近期调用", value: AIPresentation.tokens(workspace.usageSummary["request_count"]?.intValue ?? 0), detail: "最近 100 条以内")
                let failed = workspace.usageSummary["failed_count"]?.intValue ?? 0
                AIMetric(title: "调用失败", value: String(failed), detail: "历史记录", color: failed > 0 ? .orange : .primary)
            }
            .padding(.vertical, 4)
        } footer: {
            Text("凭据就绪表示具备调用配置，不代表当前测活成功；运行故障与历史调用分别显示。")
        }
    }

    private var actions: some View {
        Section("常用操作") {
            Button {
                showLiveness()
            } label: {
                Label("模型批量测活", systemImage: "waveform.path.ecg")
            }
            NavigationLink {
                AIProvidersDestination(workspace: workspace)
            } label: {
                Label("管理供应商与模型", systemImage: "server.rack")
            }
        }
    }

    @ViewBuilder private var runtime: some View {
        Section {
            if workspace.unhealthy.isEmpty {
                Label(workspace.health.isEmpty ? "暂无业务调用健康记录" : "当前没有冷却或故障记录", systemImage: "checkmark.circle")
                    .foregroundStyle(workspace.health.isEmpty ? Color.secondary : Color.green)
            } else {
                ForEach(Array(workspace.unhealthy.prefix(3).enumerated()), id: \.offset) { _, row in
                    VStack(alignment: .leading, spacing: 5) {
                        HStack {
                            Text(providerName(row["provider_id"]?.intValue)).font(.subheadline.weight(.medium))
                            Spacer()
                            AIStatusLabel(title: AIPresentation.runtimeState(row), symbol: "exclamationmark.circle.fill", color: .orange)
                        }
                        Text(row["model"]?.stringValue ?? "未指定模型").font(.caption).foregroundStyle(.secondary)
                        if let error = row["last_error_message"]?.stringValue, !error.isEmpty {
                            Text(AssistantRedaction.text(error)).font(.caption).foregroundStyle(.red).lineLimit(3)
                        }
                    }
                }
                NavigationLink("查看全部运行状态") { AIRuntimeHealthView(workspace: workspace) }
            }
        } header: { Text("运行健康") } footer: { Text("这里来自实际业务调用。手工测活不会修改生产冷却状态。") }
    }

    @ViewBuilder private var recent: some View {
        Section("最近调用") {
            if workspace.usageItems.isEmpty {
                Text("暂无调用记录。执行一次 AI 指令或模型测活后会显示在这里。")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else {
                ForEach(Array(workspace.usageItems.prefix(3).enumerated()), id: \.offset) { _, item in
                    NavigationLink { AIUsageDetailView(item: item) } label: { AIUsageRow(item: item) }
                }
                Button("查看全部与插件用量", action: showUsage)
                    .font(.subheadline)
            }
        }
    }

    private var configuration: some View {
        Section("配置") {
            NavigationLink("AI 路由与输出") {
                ManagementDocumentView(title: "AI 路由与输出配置", path: "/api/system/settings", actions: [
                    .init(title: "修改路由与输出", path: "/api/system/settings", method: "PATCH")
                ])
            }
            NavigationLink("AI 指令与账号启用") {
                ManagementCollectionView(resource: .init(title: "指令模板", path: "/api/commands/templates", kind: .editable,
                    create: .init(title: "新建指令模板", path: "/api/commands/templates"), itemPath: "/api/commands/templates/{id}"))
            }
            NavigationLink("高级协议与客户端设置") { AIAdvancedSettingsView() }
        }
    }

    private func providerName(_ id: Int?) -> String {
        workspace.providers.first { $0["id"]?.intValue == id }.map(AIPresentation.providerName) ?? "供应商 \(id.map(String.init) ?? "未知")"
    }
}

struct AIWorkspaceErrors: View {
    let workspace: AIWorkspaceStore
    var body: some View {
        let names = ["settings": "平台设置", "providers": "供应商", "health": "运行健康",
                     "usage": "调用记录", "pluginUsage": "插件用量", "enablement": "指令状态"]
        ForEach(workspace.errors.keys.sorted(), id: \.self) { key in
            Section { NoticeView(title: "\(names[key] ?? key)读取失败", message: workspace.errors[key]) }
        }
    }
}

private struct AIProvidersDestination: View {
    @Bindable var workspace: AIWorkspaceStore
    var body: some View {
        List { AIProviderListSection(workspace: workspace, showSearch: true) }
            .navigationTitle("供应商").navigationBarTitleDisplayMode(.inline)
    }
}

struct AIRuntimeHealthView: View {
    let workspace: AIWorkspaceStore
    var body: some View {
        List {
            Section {
                ForEach(Array(workspace.health.enumerated()), id: \.offset) { _, row in
                    VStack(alignment: .leading, spacing: 7) {
                        HStack {
                            Text(row["model"]?.stringValue ?? "未指定模型").font(.headline)
                            Spacer()
                            AIStatusLabel(title: AIPresentation.runtimeState(row), color: AIPresentation.hasIssue(row) ? .orange : .green)
                        }
                        Text("供应商编号 \(row["provider_id"]?.intValue ?? 0)").font(.caption).foregroundStyle(.secondary)
                        if let error = row["last_error_message"]?.stringValue, !error.isEmpty {
                            Text(AssistantRedaction.text(error)).font(.footnote).foregroundStyle(.red)
                        }
                        if let category = row["last_error_class"]?.stringValue {
                            Text("错误分类：\(AIPresentation.status(category))").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if workspace.health.isEmpty { Text("暂无运行健康记录").foregroundStyle(.secondary) }
            } footer: { Text("手工测活结果不会写入此列表。连续失败次数、冷却剩余时间与错误详情来自服务器运行时。") }
        }
        .navigationTitle("运行健康").navigationBarTitleDisplayMode(.inline)
    }
}

struct AIAdvancedSettingsView: View {
    var body: some View {
        List {
            Section("客户端身份") {
                NavigationLink("客户端身份版本") {
                    ManagementDocumentView(title: "客户端身份版本", path: "/api/commands/llm-providers/identity-versions", actions: [
                        .init(title: "修改身份版本", path: "/api/commands/llm-providers/identity-versions", method: "PUT"),
                        .init(title: "检测身份版本", path: "/api/commands/llm-providers/identity-versions/detect")
                    ])
                }
            }
            Section("诊断") {
                ManagementActionLink(action: .init(title: "协议发现", path: "/api/commands/llm-providers/detect-protocols",
                    explanation: "服务器将尝试模型服务协议，可能产生请求用量。"))
                ManagementActionLink(action: .init(title: "预览可用模型", path: "/api/commands/llm-providers/fetch-models-preview"))
            }
        }
        .navigationTitle("高级设置").navigationBarTitleDisplayMode(.inline)
    }
}
