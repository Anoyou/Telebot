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
    var title: String {
        switch self {
        case .overview: "概览"
        case .providers: "供应商"
        case .usage: "用量"
        }
    }
}

struct AIWorkspaceView: View {
    @Environment(AppModel.self) private var model
    @State private var workspace = AIWorkspaceStore()
    @State private var section: AISection = .overview
    @State private var showLiveness = false

    var body: some View {
        List {
            switch section {
            case .overview: AIOverviewSection(workspace: workspace, showUsage: { section = .usage }, showLiveness: { showLiveness = true })
            case .providers: AIProviderListSection(workspace: workspace)
            case .usage: AIUsageSection(workspace: workspace)
            }
        }
        // 顶部分段切换放进系统安全区栏：内容从下方滚过时由系统的滚动边缘效果承托，
        // 分段控件本身是 iOS 26 原生的 Liquid Glass 控件，不再套白色卡片背景。
        .safeAreaBar(edge: .top) {
            Picker("AI 工作区", selection: $section) {
                ForEach(AISection.allCases) { item in Text(item.title).tag(item) }
            }
            .pickerStyle(.segmented)
            .labelsHidden()
            .controlSize(.large)
            .padding(.horizontal, 16)
            .padding(.vertical, 8)
            .accessibilityIdentifier("ai.sections")
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
                NavigationLink {
                    AIGlobalSettingsView(workspace: workspace)
                } label: {
                    Label("打开 AI 全局设置", systemImage: "power.circle")
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
                AIMetric(title: "可调用供应商", value: "\(workspace.readyProviders.count)/\(workspace.providers.count)", detail: "已配置密钥")
                AIMetric(title: "近期调用", value: AIPresentation.tokens(workspace.usageSummary["request_count"]?.intValue ?? 0), detail: "最近 100 条以内")
                let failed = workspace.usageSummary["failed_count"]?.intValue ?? 0
                AIMetric(title: "调用失败", value: String(failed), detail: "历史记录", color: failed > 0 ? .orange : .primary)
            }
            .padding(.vertical, 4)
        } footer: {
            Text("“可调用”只表示已配好密钥，不代表模型此刻一定正常；想确认能不能用，请做一次模型测活。")
        }
    }

    private var actions: some View {
        Section("常用操作") {
            Button {
                showLiveness()
            } label: {
                Label("模型批量测活", systemImage: "waveform.path.ecg")
                    .accessibilityHint("检查所选模型现在能否正常回复")
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
                NavigationLink { AIRuntimeHealthView(workspace: workspace) } label: {
                    Label("查看全部运行状态", systemImage: "list.bullet.clipboard")
                }
            }
        } header: {
            HStack {
                Text("运行健康")
                Spacer()
                AIHelpButton(title: "运行健康", message: "这里记录真实业务调用（AI 指令、插件、助手）中出错的模型。连续失败的模型会进入“冷却”，暂停一段时间不再被调用，到时自动恢复。手动测活不会改变这里的状态。")
            }
        } footer: { Text("来自实际业务调用。手动测活不会修改冷却状态。") }
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
                Button(action: showUsage) {
                    Label("查看全部调用与插件用量", systemImage: "chart.bar")
                }
                .font(.subheadline)
            }
        }
    }

    private var configuration: some View {
        Section("配置") {
            NavigationLink {
                AIGlobalSettingsView(workspace: workspace)
            } label: {
                AINavigationRow(title: "AI 全局设置", detail: "总开关、聊天上下文条数、每个账号的调用预算", symbol: "slider.horizontal.3")
            }
            .accessibilityIdentifier("ai.settings.global")
            NavigationLink {
                AICommandEnablementView()
            } label: {
                AINavigationRow(title: "AI 指令与账号启用", detail: "哪些 AI 指令在哪些账号生效、指令别名、模型与回复格式", symbol: "text.bubble")
            }
            .accessibilityIdentifier("ai.settings.commands")
            NavigationLink {
                AIAdvancedSettingsView(workspace: workspace)
            } label: {
                AINavigationRow(title: "高级：接口协议与客户端身份", detail: "对接特殊中转站时才需要调整，普通使用保持默认", symbol: "gearshape.2")
            }
            .accessibilityIdentifier("ai.settings.advanced")
            NavigationLink {
                AIGlossaryView()
            } label: {
                AINavigationRow(title: "AI 术语说明", detail: "供应商、词元、测活、冷却等名词的通俗解释", symbol: "book")
            }
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
                        Text(workspace.providers.first { $0["id"]?.intValue == row["provider_id"]?.intValue }.map(AIPresentation.providerName)
                             ?? "供应商 #\(row["provider_id"]?.intValue ?? 0)").font(.caption).foregroundStyle(.secondary)
                        if let failures = row["consecutive_failures"]?.intValue, failures > 0 {
                            Text("连续失败 \(failures) 次").font(.caption).foregroundStyle(.secondary)
                        }
                        if let error = row["last_error_message"]?.stringValue, !error.isEmpty {
                            Text(AssistantRedaction.text(error)).font(.footnote).foregroundStyle(.red)
                        }
                        if let category = row["last_error_class"]?.stringValue {
                            Text("错误分类：\(AIPresentation.status(category))").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if workspace.health.isEmpty { Text("暂无运行健康记录").foregroundStyle(.secondary) }
            } footer: { Text("“冷却中”表示该模型连续出错，系统暂时不再调用它，到时自动恢复。手动测活结果不会写入此列表。") }
        }
        .navigationTitle("运行健康").navigationBarTitleDisplayMode(.inline)
    }
}

// MARK: - AI 全局设置（替代原“AI 路由与输出配置”原始文档）

private struct AISettingsDraft: Equatable {
    var aiEnabled = true
    var contextMessages = 0
    var perMinute = 0
    var dailyRequests = 0
    var dailyTokens = 0
    var premiumDaily = 0
    var usageRetentionDays = 30

    init() {}

    init(_ value: JSONValue) {
        aiEnabled = value["ai_enabled"]?.boolValue ?? true
        contextMessages = value["ai_context_messages"]?.intValue ?? 0
        let limits = value["llm_limits"]
        perMinute = limits?["per_minute"]?.intValue ?? 0
        dailyRequests = limits?["daily_requests"]?.intValue ?? 0
        dailyTokens = limits?["daily_tokens"]?.intValue ?? 0
        premiumDaily = limits?["premium_daily"]?.intValue ?? 0
        usageRetentionDays = value["log_retention"]?["llm_usage_retention_days"]?.intValue ?? 30
    }

    /// 只提交改动过的字段；服务器对未提交字段保持不变。
    func patch(from original: AISettingsDraft) -> [String: JSONValue] {
        var body: [String: JSONValue] = [:]
        if aiEnabled != original.aiEnabled { body["ai_enabled"] = .bool(aiEnabled) }
        if contextMessages != original.contextMessages { body["ai_context_messages"] = .integer(Int64(contextMessages)) }
        var limits: [String: JSONValue] = [:]
        if perMinute != original.perMinute { limits["per_minute"] = .integer(Int64(perMinute)) }
        if dailyRequests != original.dailyRequests { limits["daily_requests"] = .integer(Int64(dailyRequests)) }
        if dailyTokens != original.dailyTokens { limits["daily_tokens"] = .integer(Int64(dailyTokens)) }
        if premiumDaily != original.premiumDaily { limits["premium_daily"] = .integer(Int64(premiumDaily)) }
        if !limits.isEmpty { body["llm_limits"] = .object(limits) }
        if usageRetentionDays != original.usageRetentionDays {
            body["log_retention"] = .object(["llm_usage_retention_days": .integer(Int64(usageRetentionDays))])
        }
        return body
    }

    var validationError: String? {
        if [perMinute, dailyRequests, dailyTokens, premiumDaily].contains(where: { $0 < 0 }) { return "预算不能为负数，填 0 表示不限制。" }
        if !(0...20).contains(contextMessages) { return "上下文条数必须在 0～20 之间。" }
        if !(0...3650).contains(usageRetentionDays) { return "保留天数必须在 0～3650 之间。" }
        return nil
    }
}

private struct AILimitField: View {
    let title: String
    let unit: String
    @Binding var value: Int
    let hint: String

    var body: some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(title)
                Spacer(minLength: 8)
                TextField("不限制", value: $value, format: .number)
                    .keyboardType(.numberPad)
                    .multilineTextAlignment(.trailing)
                    .monospacedDigit()
                    .frame(maxWidth: 130)
                Text(unit).foregroundStyle(.secondary)
            }
            Text(value == 0 ? "当前不限制。\(hint)" : hint)
                .font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 2)
    }
}

struct AIGlobalSettingsView: View {
    var workspace: AIWorkspaceStore? = nil
    @Environment(AppModel.self) private var model
    @State private var original: AISettingsDraft?
    @State private var draft = AISettingsDraft()
    @State private var prefix = ","
    @State private var loading = false
    @State private var loadError: String?
    @State private var saving = false
    @State private var saveError: String?
    @State private var uncertain = false
    @State private var saved = false
    @State private var confirmingDisable = false

    private var dirty: Bool { original != nil && original != draft }
    private var retentionOptions: [Int] {
        var options = [7, 14, 30, 60, 90, 180, 365, 0]
        if !options.contains(draft.usageRetentionDays) { options.insert(draft.usageRetentionDays, at: 0) }
        return options
    }

    var body: some View {
        Form {
            if loading && original == nil { Section { ProgressView("正在读取设置…") } }
            if let loadError { Section { NoticeView(title: "设置读取失败", message: loadError) } }
            if original != nil {
                switchSection
                contextSection
                budgetSection
                retentionSection
                routingSection
                saveSection
            }
        }
        .navigationTitle("AI 全局设置").navigationBarTitleDisplayMode(.inline)
        .task { if original == nil { await load() } }
        .refreshable { await load() }
        .onChange(of: draft) { saved = false }
        .confirmationDialog("关闭 AI 能力？", isPresented: $confirmingDisable, titleVisibility: .visible) {
            Button("关闭 AI 并保存", role: .destructive) { Task { await save() } }
            Button("取消", role: .cancel) {}
        } message: {
            Text("所有 AI 指令、插件中的 AI 调用和助手会立即停止。供应商、模型和指令配置都会保留，之后重新打开即可恢复。")
        }
    }

    private var switchSection: some View {
        Section {
            Toggle(isOn: $draft.aiEnabled) {
                AISettingLabel(title: "启用 AI 能力", detail: draft.aiEnabled ? "AI 指令、插件 AI 和助手正常工作" : "保存后所有 AI 调用都会停止")
            }
            .accessibilityIdentifier("ai.settings.enabled")
        } header: {
            Text("总开关")
        } footer: {
            Text("关闭 AI 不会删除任何供应商、模型或指令配置。")
        }
    }

    private var contextSection: some View {
        Section {
            Stepper(value: $draft.contextMessages, in: 0...20) {
                AISettingLabel(title: draft.contextMessages == 0 ? "不带聊天记录" : "带上最近 \(draft.contextMessages) 条聊天记录",
                               detail: "AI 指令回答时一起参考的上文条数")
            }
            .accessibilityIdentifier("ai.settings.context")
        } header: {
            HStack {
                Text("AI 指令的聊天上下文")
                Spacer()
                AIHelpButton(title: "上下文", message: AIGlossary.terms.first { $0.title == "上下文" }?.detail ?? "")
            }
        } footer: {
            Text("例如设为 5：在群里发送 \(prefix)ai 他说得对吗？ 时，会连同此前 5 条消息一起发给模型，模型才知道“他”说了什么。0 表示只发送当前这条消息。条数越多回答越贴合语境，但每次消耗的词元也越多。")
        }
    }

    private var budgetSection: some View {
        Section {
            AILimitField(title: "每分钟最多调用", unit: "次", value: $draft.perMinute, hint: "建议 20～60，防止循环触发或插件异常反复调用。")
            AILimitField(title: "每天最多调用", unit: "次", value: $draft.dailyRequests, hint: "建议 500～2000，按账号活跃度调整。")
            AILimitField(title: "每天最多消耗", unit: "词元", value: $draft.dailyTokens, hint: "建议 20 万～100 万。语音转文字的词元为估算值。")
            AILimitField(title: "高价模型每天最多调用", unit: "次", value: $draft.premiumDaily, hint: "只统计“旗舰档”模型，建议 20～100。")
        } header: {
            HStack {
                Text("每个账号的调用预算")
                Spacer()
                AIHelpButton(title: "调用预算", message: "预算按每个 Telegram 账号分别计算。账号在 AI 指令、插件中发起调用前会先占用预算，超过上限后，这个账号在该时段内的 AI 调用会被拒绝，避免意外刷爆费用。模型测活只记录用量，不占用预算。")
            }
        } footer: {
            Text("填 0 表示不限制。保存后对新的调用立即生效。")
        }
    }

    private var retentionSection: some View {
        Section {
            Picker(selection: $draft.usageRetentionDays) {
                ForEach(retentionOptions, id: \.self) { days in
                    Text(days == 0 ? "永久保留" : "\(days) 天").tag(days)
                }
            } label: {
                AISettingLabel(title: "AI 调用记录保留", detail: "超过期限的调用明细会自动删除")
            }
        } footer: {
            Text("调用记录用于“用量”页的统计和排错。保留越久，占用的服务器存储越多。")
        }
    }

    private var routingSection: some View {
        Section {
            NavigationLink { AICommandEnablementView() } label: {
                AINavigationRow(title: "设置某条指令用哪个模型、怎样回复", detail: "在“AI 指令与账号启用”中打开对应指令", symbol: "arrow.triangle.branch")
            }
        } header: {
            Text("模型路由与回复格式在哪里设置？")
        } footer: {
            Text("“路由”是指一条 AI 指令把问题交给哪个模型：可以固定使用一个模型，也可以开启“自动路由”让系统按问题类型挑选，并设置失败时改用的备用模型。回复格式（富文本、Markdown、纯文本）和发送方式同样在每条指令里单独设置。")
        }
    }

    private var saveSection: some View {
        Section {
            Button {
                requestSave()
            } label: {
                HStack(spacing: 8) {
                    Spacer(minLength: 0)
                    if saving { ProgressView() }
                    Label("保存修改", systemImage: "checkmark.circle").font(.headline)
                    Spacer(minLength: 0)
                }
                .frame(minHeight: 44)
            }
            .disabled(!dirty || saving || uncertain)
            .accessibilityIdentifier("ai.settings.save")
            if dirty && !saving, let original {
                Button(role: .destructive) { draft = original } label: {
                    Label("放弃修改", systemImage: "arrow.uturn.backward")
                }
            }
            if let message = draft.validationError { Text(message).font(.caption).foregroundStyle(.red) }
            if let saveError { NoticeView(title: uncertain ? "保存结果待确认" : "保存失败", message: saveError) }
            if uncertain { Text("请下拉刷新，确认服务器上的实际设置后再修改。").font(.caption).foregroundStyle(.orange) }
            if saved { Label("已保存，服务器已返回最新设置。", systemImage: "checkmark.circle").foregroundStyle(.green) }
        } footer: {
            Text("只会提交你改动过的项目。")
        }
    }

    private func requestSave() {
        guard let original, dirty, draft.validationError == nil else { return }
        if original.aiEnabled && !draft.aiEnabled { confirmingDisable = true }
        else { Task { await save() } }
    }

    private func apply(_ value: JSONValue) {
        let next = AISettingsDraft(value)
        original = next
        draft = next
        if let current = value["command_prefix"]?.stringValue, !current.isEmpty { prefix = current }
    }

    private func load() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            apply(try await model.request(path: "/api/system/settings"))
            loadError = nil; saveError = nil; uncertain = false
        } catch { loadError = error.localizedDescription }
    }

    private func save() async {
        guard let original, !saving, !uncertain, draft.validationError == nil else { return }
        let body = draft.patch(from: original)
        guard !body.isEmpty else { return }
        saving = true; saveError = nil
        defer { saving = false }
        do {
            let response = try await model.request(path: "/api/system/settings", method: "PATCH", body: .object(body))
            apply(response)
            workspace?.settings = response
            saved = true
        } catch {
            saveError = error.localizedDescription
            uncertain = (error as? APIError)?.isOutcomeUncertain == true
        }
    }
}

// MARK: - AI 指令与账号启用（原生视图，替代原始模板集合与“启用概览”JSON）

@MainActor @Observable
final class AICommandEnablementStore {
    var templates: [JSONValue] = []
    var providers: [JSONValue] = []
    var summary: JSONValue?
    var prefix = ","
    /// 账号编号 → 已启用的指令模板编号。
    var enabled: [Int: Set<Int>] = [:]
    var accountErrors: [Int: String] = [:]
    var loadError: String?
    var loading = false
    var loaded = false
    var pending: Set<String> = []
    var uncertain: Set<String> = []
    var actionError: String?

    func key(_ account: Int, _ template: Int) -> String { "\(account):\(template)" }

    func refresh(_ model: AppModel) async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        if !model.accountsLoaded { await model.refresh() }
        do {
            let all = try await model.request(path: "/api/commands/templates")
            templates = (all.arrayValue ?? all["items"]?.arrayValue ?? []).filter { $0["type"]?.stringValue == "ai" }
            loadError = nil
        } catch {
            loadError = error.localizedDescription
            return
        }
        if let value = try? await model.request(path: "/api/commands/llm-providers") {
            providers = value.arrayValue ?? value["items"]?.arrayValue ?? providers
        }
        summary = try? await model.request(path: "/api/commands/ai/enablement-summary")
        if let settings = try? await model.request(path: "/api/system/settings"),
           let current = settings["command_prefix"]?.stringValue, !current.isEmpty {
            prefix = current
        }
        var next: [Int: Set<Int>] = [:]
        var errors: [Int: String] = [:]
        for account in model.accounts {
            do {
                let rows = try await model.request(path: "/api/accounts/\(account.id)/commands")
                next[account.id] = Set((rows.arrayValue ?? []).compactMap { row in
                    row["enabled"]?.boolValue == true ? row["template"]?["id"]?.intValue : nil
                })
            } catch { errors[account.id] = error.localizedDescription }
        }
        enabled = next
        accountErrors = errors
        uncertain = []
        actionError = nil
        loaded = true
    }

    func setEnabled(_ model: AppModel, account: Int, template: Int, on: Bool) async {
        let itemKey = key(account, template)
        guard !pending.contains(itemKey), !uncertain.contains(itemKey) else { return }
        pending.insert(itemKey); actionError = nil
        defer { pending.remove(itemKey) }
        do {
            _ = try await model.request(path: "/api/accounts/\(account)/commands/\(template)", method: on ? "POST" : "DELETE")
            if on { enabled[account, default: []].insert(template) }
            else { enabled[account, default: []].remove(template) }
            summary = try? await model.request(path: "/api/commands/ai/enablement-summary")
        } catch {
            if (error as? APIError)?.isOutcomeUncertain == true {
                uncertain.insert(itemKey)
                actionError = "网络中断，无法确认是否已\(on ? "启用" : "停用")。请下拉刷新查看实际状态，不要重复操作。"
            } else {
                actionError = error.localizedDescription
            }
        }
    }

    /// 合并服务器返回的模板字段（兼容只返回部分字段的情况）。
    func mergeTemplate(id: Int, _ value: JSONValue) {
        guard let index = templates.firstIndex(where: { $0["id"]?.intValue == id }) else { return }
        var current = templates[index].objectValue ?? [:]
        for (key, item) in value.objectValue ?? [:] { current[key] = item }
        templates[index] = .object(current)
    }

    func templateName(_ template: JSONValue) -> String {
        prefix + (template["name"]?.stringValue ?? "未命名")
    }

    func aliases(_ template: JSONValue) -> [String] {
        (template["aliases"]?.arrayValue ?? []).compactMap(\.stringValue)
    }

    func providerName(_ id: Int?) -> String? {
        guard let id else { return nil }
        return providers.first { $0["id"]?.intValue == id }.map(AIPresentation.providerName)
    }

    func modelText(_ template: JSONValue) -> String {
        let config = template["config"]
        let mode = config?["mode"]?.stringValue ?? "chat"
        if mode == "image", (config?["image_backend"]?.stringValue ?? "codex_image") == "codex_image" { return "codex_image 生图插件" }
        if mode == "video" { return "视频插件（\(config?["video_plugin_key"]?.stringValue ?? "video_bridge")）" }
        guard let providerID = config?["provider_id"]?.intValue else { return "未选择模型" }
        guard let provider = providers.first(where: { $0["id"]?.intValue == providerID }) else { return "供应商 #\(providerID)（已不存在）" }
        let modelID = config?["model"]?.stringValue.flatMap { $0.isEmpty ? nil : $0 } ?? provider["default_model"]?.stringValue ?? "默认模型"
        return "\(AIPresentation.providerName(provider)) · \(modelID)"
    }

    func enabledTemplates(for account: Int) -> [JSONValue] {
        let ids = enabled[account] ?? []
        return templates.filter { ids.contains($0["id"]?.intValue ?? -1) }
    }

    func enabledAccountCount(template: Int) -> Int {
        enabled.values.filter { $0.contains(template) }.count
    }
}

struct AICommandEnablementView: View {
    @Environment(AppModel.self) private var model
    @State private var store = AICommandEnablementStore()

    private var enabledAccounts: Int {
        if store.accountErrors.isEmpty {
            let ids = Set(store.templates.compactMap { $0["id"]?.intValue })
            return model.accounts.filter { !(store.enabled[$0.id] ?? []).isDisjoint(with: ids) }.count
        }
        return store.summary?["enabled_accounts"]?.intValue ?? 0
    }

    var body: some View {
        List {
            Section {
                Label("AI 指令是什么？", systemImage: "questionmark.bubble").font(.headline)
                Text("AI 指令是你在 Telegram 聊天里发送的命令。例如发送 \(store.prefix)ai 帮我翻译这段话，TelePilot 会用设定的模型回答并把结果发回聊天。每条指令都要在具体账号上“启用”后，才会在该账号里生效。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if store.loading && !store.loaded { Section { ProgressView("正在读取指令与账号…") } }
            if let error = store.loadError { Section { NoticeView(title: "指令读取失败", message: error) } }
            if let error = store.actionError { Section { NoticeView(title: "操作未完成", message: error) } }
            if store.loaded {
                summarySection
                commandSection
                accountSection
                manageSection
            }
        }
        .navigationTitle("AI 指令与账号启用").navigationBarTitleDisplayMode(.inline)
        .task { if !store.loaded { await store.refresh(model) } }
        .refreshable { await store.refresh(model) }
    }

    private var summarySection: some View {
        Section {
            HStack(alignment: .top, spacing: 20) {
                AIMetric(title: "AI 指令", value: "\(store.templates.count)", detail: "条")
                AIMetric(title: "已启用的账号", value: "\(enabledAccounts)/\(model.accounts.count)", detail: "至少启用了 1 条 AI 指令",
                         color: enabledAccounts == 0 && !store.templates.isEmpty ? .orange : .primary)
            }
            .padding(.vertical, 4)
            .accessibilityIdentifier("ai.commands.summary")
        } footer: {
            if enabledAccounts == 0 && !store.templates.isEmpty {
                Text("目前没有任何账号启用 AI 指令，在 Telegram 里发送指令不会有反应。点进下面的指令即可按账号开启。")
            }
        }
    }

    private var commandSection: some View {
        Section {
            ForEach(store.templates, id: \.self) { template in
                if let id = template["id"]?.intValue {
                    NavigationLink { AICommandDetailView(templateID: id, store: store) } label: {
                        AICommandRow(template: template, store: store, accountTotal: model.accounts.count)
                    }
                    .accessibilityIdentifier("ai.command.\(id)")
                }
            }
            if store.templates.isEmpty {
                Text("还没有 AI 指令。可以在下方“管理全部指令模板”中新建一条类型为 ai 的指令。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
        } header: {
            Text("按指令查看")
        } footer: {
            Text("点进指令可以查看它使用的模型、设置指令别名，并逐个账号开关。")
        }
    }

    private var accountSection: some View {
        Section {
            ForEach(model.accounts) { account in
                NavigationLink { AIAccountCommandsView(account: account, store: store) } label: {
                    VStack(alignment: .leading, spacing: 4) {
                        Label(ManagementText.accountName(account), systemImage: "person.crop.circle")
                        if store.accountErrors[account.id] != nil {
                            Text("启用状态读取失败，下拉可重试").font(.caption).foregroundStyle(.red)
                        } else {
                            let names = store.enabledTemplates(for: account.id).map(store.templateName)
                            Text(names.isEmpty ? "未启用任何 AI 指令" : "已启用：" + names.joined(separator: "  "))
                                .font(.caption).foregroundStyle(.secondary).lineLimit(2)
                        }
                    }
                }
            }
            if model.accounts.isEmpty { Text("还没有 Telegram 账号。").foregroundStyle(.secondary) }
        } header: {
            Text("按账号查看")
        }
    }

    private var manageSection: some View {
        Section {
            NavigationLink {
                ManagementCollectionView(resource: .init(title: "全部指令模板", path: "/api/commands/templates", kind: .editable,
                    create: .init(title: "新建指令模板", path: "/api/commands/templates"), itemPath: "/api/commands/templates/{id}"))
            } label: {
                AINavigationRow(title: "管理全部指令模板", detail: "新建、编辑或删除指令（也包括非 AI 指令）", symbol: "square.and.pencil")
            }
        } footer: {
            Text("新建 AI 指令时，“类型”请选择 ai，并在配置中选择供应商。")
        }
    }
}

private struct AICommandRow: View {
    let template: JSONValue
    let store: AICommandEnablementStore
    let accountTotal: Int

    var body: some View {
        let count = store.enabledAccountCount(template: template["id"]?.intValue ?? -1)
        let aliases = store.aliases(template)
        VStack(alignment: .leading, spacing: 5) {
            HStack(alignment: .firstTextBaseline) {
                Text(store.templateName(template)).font(.headline.monospaced()).lineLimit(1)
                Spacer(minLength: 6)
                AIStatusLabel(title: count == 0 ? "未在任何账号启用" : "\(count)/\(accountTotal) 个账号",
                              symbol: count == 0 ? "pause.circle" : "checkmark.circle.fill",
                              color: count == 0 ? .secondary : .green)
            }
            if let description = template["description"]?.stringValue, !description.isEmpty {
                Text(description).font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            }
            Text("\(AIPresentation.commandMode(template["config"]?["mode"]?.stringValue)) · \(store.modelText(template))")
                .font(.caption).foregroundStyle(.secondary).lineLimit(2)
            if !aliases.isEmpty {
                Text("别名：" + aliases.map { store.prefix + $0 }.joined(separator: "  "))
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }
        }
        .padding(.vertical, 3)
        .accessibilityElement(children: .combine)
    }
}

private struct AIEnablementToggle: View {
    let accountID: Int
    let templateID: Int
    let title: String
    let symbol: String
    @Bindable var store: AICommandEnablementStore
    @Environment(AppModel.self) private var model

    var body: some View {
        let itemKey = store.key(accountID, templateID)
        let busy = store.pending.contains(itemKey)
        let unknown = store.uncertain.contains(itemKey)
        let readFailed = store.accountErrors[accountID] != nil
        Toggle(isOn: Binding(get: { store.enabled[accountID]?.contains(templateID) == true },
                             set: { value in Task { await store.setEnabled(model, account: accountID, template: templateID, on: value) } })) {
            VStack(alignment: .leading, spacing: 2) {
                Label(title, systemImage: symbol)
                if busy { Text("正在提交…").font(.caption).foregroundStyle(.secondary) }
                else if unknown { Text("结果待确认，请下拉刷新").font(.caption).foregroundStyle(.orange) }
                else if readFailed { Text("启用状态读取失败").font(.caption).foregroundStyle(.red) }
            }
        }
        .disabled(busy || unknown || readFailed)
    }
}

struct AIAccountCommandsView: View {
    let account: Account
    @Bindable var store: AICommandEnablementStore
    @Environment(AppModel.self) private var model

    var body: some View {
        List {
            if let error = store.actionError { Section { NoticeView(title: "操作未完成", message: error) } }
            Section {
                ForEach(store.templates, id: \.self) { template in
                    if let id = template["id"]?.intValue {
                        AIEnablementToggle(accountID: account.id, templateID: id, title: store.templateName(template), symbol: "text.bubble", store: store)
                    }
                }
                if store.templates.isEmpty { Text("还没有 AI 指令。").foregroundStyle(.secondary) }
            } header: {
                Text("在此账号启用的 AI 指令")
            } footer: {
                Text("打开后，这个账号在聊天中收到对应指令时会调用 AI 回答。开关会立即提交到服务器。")
            }
        }
        .navigationTitle(ManagementText.accountName(account)).navigationBarTitleDisplayMode(.inline)
        .refreshable { await store.refresh(model) }
    }
}

struct AICommandDetailView: View {
    let templateID: Int
    @Bindable var store: AICommandEnablementStore
    @Environment(AppModel.self) private var model
    @State private var aliases: [String] = []
    @State private var newAlias = ""
    @State private var aliasError: String?
    @State private var saving = false
    @State private var saveError: String?
    @State private var uncertain = false
    @State private var saved = false
    @State private var initialized = false

    private var template: JSONValue? { store.templates.first { $0["id"]?.intValue == templateID } }
    private var originalAliases: [String] { template.map(store.aliases) ?? [] }

    var body: some View {
        List {
            if let template {
                if let error = store.actionError { Section { NoticeView(title: "操作未完成", message: error) } }
                configSection(template)
                aliasSection(template)
                accountSection
            } else {
                Text("这条指令已不存在，请返回后下拉刷新。").foregroundStyle(.secondary)
            }
        }
        .navigationTitle(template.map(store.templateName) ?? "AI 指令").navigationBarTitleDisplayMode(.inline)
        .task {
            guard !initialized else { return }
            aliases = originalAliases
            initialized = true
        }
        .refreshable {
            await store.refresh(model)
            if !saving { aliases = originalAliases; uncertain = false; saveError = nil }
        }
    }

    @ViewBuilder private func configSection(_ template: JSONValue) -> some View {
        let config = template["config"]
        let routing = config?["routing_mode"]?.stringValue ?? "fixed"
        Section {
            LabeledContent("指令", value: store.templateName(template))
            if let description = template["description"]?.stringValue, !description.isEmpty {
                LabeledContent("说明", value: description)
            }
            LabeledContent("用途", value: AIPresentation.commandMode(config?["mode"]?.stringValue))
            LabeledContent("使用模型", value: store.modelText(template))
            LabeledContent("模型选择方式", value: routing == "auto" ? "自动路由（按问题挑选模型）" : "固定使用上面的模型")
            if routing == "auto", let fallback = store.providerName(config?["routing_fallback_provider_id"]?.intValue) {
                LabeledContent("备用供应商", value: fallback)
            }
            LabeledContent("联网搜索", value: config?["web_search"]?.boolValue == true || config?["mode"]?.stringValue == "search" ? "开启" : "关闭")
            LabeledContent("回复格式", value: AIPresentation.outputFormat(config?["output_format"]?.stringValue))
            LabeledContent("发送方式", value: config?["send_mode"]?.stringValue == "send_new" ? "删除指令后发送新消息" : "直接把指令消息改成回答")
            NavigationLink {
                ManagementFormView(action: .init(title: "编辑指令", path: "/api/commands/templates/\(templateID)", method: "PATCH",
                    explanation: "修改模型、自动路由、备用模型、回复格式等设置。保存后对所有启用此指令的账号生效。", seed: template)) { value in
                    store.mergeTemplate(id: templateID, value)
                }
            } label: {
                Label("编辑模型、路由与回复格式", systemImage: "square.and.pencil")
            }
        } header: {
            HStack {
                Text("指令配置")
                Spacer()
                AIHelpButton(title: "自动路由", message: AIGlossary.terms.first { $0.title == "自动路由" }?.detail ?? "")
            }
        } footer: {
            Text("用法示例：在聊天中发送 \(store.templateName(template)) 你的问题。")
        }
    }

    @ViewBuilder private func aliasSection(_ template: JSONValue) -> some View {
        let name = store.templateName(template)
        Section {
            ForEach(aliases, id: \.self) { alias in
                HStack {
                    Text(store.prefix + alias).font(.body.monospaced())
                    Spacer()
                    Text("等同于 \(name)").font(.caption).foregroundStyle(.secondary)
                }
            }
            .onDelete { offsets in
                aliases.remove(atOffsets: offsets)
                saved = false
            }
            if aliases.isEmpty { Text("还没有别名。").foregroundStyle(.secondary) }
            HStack {
                TextField("新别名，例如 a", text: $newAlias)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .onSubmit { addAlias(template) }
                Button("添加", systemImage: "plus.circle.fill") { addAlias(template) }
                    .buttonStyle(.borderless)
                    .disabled(newAlias.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || saving)
            }
            if let aliasError { Text(aliasError).font(.caption).foregroundStyle(.red) }
            Button { Task { await saveAliases() } } label: {
                HStack(spacing: 8) {
                    if saving { ProgressView() }
                    Label("保存修改", systemImage: "checkmark.circle")
                }
            }
            .disabled(aliases == originalAliases || saving || uncertain)
            .accessibilityIdentifier("ai.command.aliases.save")
            if let saveError { NoticeView(title: uncertain ? "保存结果待确认" : "别名未保存", message: saveError) }
            if saved { Label("别名已保存。", systemImage: "checkmark.circle").foregroundStyle(.green) }
        } header: {
            HStack {
                Text("指令别名")
                Spacer()
                AIHelpButton(title: "指令别名", message: "别名就是这条指令的简短叫法，发送别名和发送原指令效果完全一样，适合把长指令缩短，或给同一功能起几个好记的名字。")
            }
        } footer: {
            Text("例如给 \(name) 添加别名 a 和 ask 后，发送 \(store.prefix)a 你好 或 \(store.prefix)ask 你好，都等同于 \(name) 你好。别名只能用英文字母、数字或下划线，最长 16 个字符，每条指令最多 32 个，不能与其他指令或别名重名。左滑可删除。")
        }
    }

    private var accountSection: some View {
        Section {
            ForEach(model.accounts) { account in
                AIEnablementToggle(accountID: account.id, templateID: templateID, title: ManagementText.accountName(account),
                                   symbol: "person.crop.circle", store: store)
            }
            if model.accounts.isEmpty { Text("还没有 Telegram 账号。").foregroundStyle(.secondary) }
        } header: {
            Text("在哪些账号启用")
        } footer: {
            Text("打开后，该账号在聊天中收到这条指令时会调用 AI 回答。开关会立即提交到服务器。")
        }
    }

    private func addAlias(_ template: JSONValue) {
        var alias = newAlias.trimmingCharacters(in: .whitespacesAndNewlines)
        if !store.prefix.isEmpty, alias.hasPrefix(store.prefix) { alias.removeFirst(store.prefix.count) }
        guard !alias.isEmpty else { return }
        guard alias.wholeMatch(of: #/[A-Za-z0-9_]{1,16}/#) != nil else {
            aliasError = "别名只能包含英文字母、数字或下划线，长度 1～16 个字符。"; return
        }
        guard alias != template["name"]?.stringValue else { aliasError = "别名不能与指令本身同名。"; return }
        guard !aliases.contains(alias) else { aliasError = "这个别名已经添加过了。"; return }
        guard aliases.count < 32 else { aliasError = "每条指令最多 32 个别名。"; return }
        aliases.append(alias)
        newAlias = ""; aliasError = nil; saved = false
    }

    private func saveAliases() async {
        guard !saving, !uncertain else { return }
        saving = true; saveError = nil; saved = false
        defer { saving = false }
        do {
            let response = try await model.request(path: "/api/commands/templates/\(templateID)", method: "PATCH",
                                                   body: .object(["aliases": .array(aliases.map(JSONValue.string))]))
            store.mergeTemplate(id: templateID, response)
            aliases = originalAliases
            saved = true
        } catch {
            saveError = error.localizedDescription
            uncertain = (error as? APIError)?.isOutcomeUncertain == true
        }
    }
}

// MARK: - 高级：接口协议与客户端身份

struct AIAdvancedSettingsView: View {
    @Bindable var workspace: AIWorkspaceStore

    var body: some View {
        List {
            Section {
                Label("什么时候需要这里？", systemImage: "lightbulb").font(.headline)
                Text("普通使用不需要改动这里。只有在对接某些模型中转站时，测活提示“协议不匹配”“客户端受限”或“版本过旧”，才需要检测接口协议或更新客户端身份版本。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            Section {
                NavigationLink { AIProtocolDetectView(workspace: workspace) } label: {
                    AINavigationRow(title: "检测接口协议", detail: "自动试探供应商支持哪种接口，并给出推荐配置", symbol: "antenna.radiowaves.left.and.right")
                }
                .accessibilityIdentifier("ai.advanced.protocols")
                NavigationLink { AIIdentityVersionsView() } label: {
                    AINavigationRow(title: "客户端身份版本", detail: "更新模拟 Codex、Claude Code 等官方客户端时使用的版本号", symbol: "person.badge.key")
                }
                .accessibilityIdentifier("ai.advanced.identity")
            } footer: {
                Text("“从供应商发现模型”和单模型测试在各供应商的详情页中。")
            }
            Section {
                ForEach(AIGlossary.terms.filter { ["接口协议", "客户端身份", "内置网关"].contains($0.title) }) { term in
                    VStack(alignment: .leading, spacing: 4) {
                        Text("\(term.title)（\(term.original)）").font(.subheadline.weight(.medium))
                        Text(term.detail).font(.caption).foregroundStyle(.secondary)
                    }
                }
            } header: { Text("名词解释") }
        }
        .navigationTitle("高级设置").navigationBarTitleDisplayMode(.inline)
    }
}

struct AIProtocolDetectView: View {
    @Bindable var workspace: AIWorkspaceStore
    @Environment(AppModel.self) private var model
    @State private var providerID: Int?
    @State private var modelID = ""
    @State private var result: JSONValue?
    @State private var detecting = false
    @State private var error: String?
    @State private var confirmingApply = false
    @State private var applying = false
    @State private var applyError: String?
    @State private var applyUncertain = false
    @State private var applied = false

    private static let recommendationFields: [(String, String)] = [
        ("recommended_api_format", "api_format"),
        ("recommended_protocol_profile", "protocol_profile"),
        ("recommended_client_identity_profile", "client_identity_profile"),
        ("recommended_web_search_api_format", "web_search_api_format")
    ]

    private var provider: JSONValue? { workspace.providers.first { $0["id"]?.intValue == providerID } }
    private var candidateModels: [String] {
        guard let provider else { return [] }
        var list = AIPresentation.enabledModels(provider)
        if let fallback = provider["default_model"]?.stringValue, !fallback.isEmpty, !list.contains(fallback) { list.insert(fallback, at: 0) }
        return list
    }

    /// 推荐配置中与当前供应商不同的字段。
    private var changes: [String: JSONValue] {
        guard let result, let provider else { return [:] }
        var body: [String: JSONValue] = [:]
        for (source, target) in Self.recommendationFields {
            guard let value = result[source]?.stringValue, !value.isEmpty else { continue }
            if provider[target]?.stringValue != value { body[target] = .string(value) }
        }
        return body
    }

    var body: some View {
        Form {
            Section {
                Text("服务器会用这个供应商已保存的地址和密钥，向几种接口各发送一次很短的请求，看看哪种能用。检测本身不会修改任何配置，但可能产生极少量费用。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            Section("检测对象") {
                Picker(selection: $providerID) {
                    Text("请选择").tag(Int?.none)
                    ForEach(workspace.providers, id: \.self) { item in
                        if let id = item["id"]?.intValue { Text(AIPresentation.providerName(item)).tag(Int?.some(id)) }
                    }
                } label: {
                    Label("供应商", systemImage: "server.rack")
                }
                if !candidateModels.isEmpty {
                    Picker(selection: $modelID) {
                        ForEach(candidateModels, id: \.self) { Text($0).tag($0) }
                    } label: {
                        Label("用哪个模型来试", systemImage: "cpu")
                    }
                }
                if let provider {
                    LabeledContent("当前接口协议", value: AIPresentation.apiFormat(provider["api_format"]?.stringValue))
                    LabeledContent("当前客户端身份", value: AIPresentation.clientIdentity(provider["client_identity_profile"]?.stringValue))
                    if provider["execution_backend"]?.stringValue == "codex_gateway" {
                        Text("此供应商使用 Codex 兼容模式（内置网关），接口协议由网关固定管理，检测结果仅供参考。")
                            .font(.caption).foregroundStyle(.orange)
                    }
                }
            }
            Section {
                Button {
                    Task { await detect() }
                } label: {
                    HStack(spacing: 8) {
                        Spacer(minLength: 0)
                        if detecting { ProgressView() }
                        Label(detecting ? "正在检测…" : "开始检测", systemImage: "play.fill").font(.headline)
                        Spacer(minLength: 0)
                    }
                    .frame(minHeight: 44)
                }
                .disabled(provider == nil || detecting)
                .accessibilityIdentifier("ai.protocols.detect")
                if let error { NoticeView(title: "检测未完成", message: error) }
            }
            if let result {
                Section {
                    protocolRow("对话补全接口（Chat Completions）", detail: "最通用的接口，绝大多数供应商和中转站都支持。", probe: result["chat_completions"])
                    protocolRow("新版响应接口（Responses）", detail: "OpenAI 的新接口，联网搜索等能力需要它。", probe: result["responses"])
                    protocolRow("Claude 消息接口（Messages）", detail: "Anthropic（Claude）官方使用的接口。", probe: result["anthropic_messages"])
                    protocolRow("模型列表接口", detail: "用于“从供应商发现模型”。不可用时仍可手动添加模型。", probe: result["models"])
                } header: { Text("检测结果") }
                recommendationSection(result)
            }
        }
        .navigationTitle("检测接口协议").navigationBarTitleDisplayMode(.inline)
        .task {
            if workspace.providers.isEmpty { await workspace.refresh(model) }
            if providerID == nil { providerID = workspace.providers.first?["id"]?.intValue }
        }
        .onChange(of: providerID) { _, _ in
            result = nil; error = nil; applied = false; applyError = nil; applyUncertain = false
            modelID = candidateModels.first ?? ""
        }
        .confirmationDialog("应用推荐配置？", isPresented: $confirmingApply, titleVisibility: .visible) {
            Button("应用到此供应商") { Task { await applyRecommendation() } }
            Button("取消", role: .cancel) {}
        } message: {
            Text("将修改该供应商的接口协议、协议配置、客户端身份或联网搜索方式。密钥和模型清单保持不变。")
        }
    }

    @ViewBuilder private func protocolRow(_ title: String, detail: String, probe: JSONValue?) -> some View {
        let ok = probe?["ok"]?.boolValue == true
        let code = probe?["upstream_status_code"]?.intValue ?? probe?["status_code"]?.intValue
        VStack(alignment: .leading, spacing: 4) {
            HStack(alignment: .firstTextBaseline) {
                Text(title).font(.subheadline.weight(.medium))
                Spacer(minLength: 6)
                AIStatusLabel(title: probe == nil ? "未检测" : (ok ? "可用" : "不可用"),
                              symbol: ok ? "checkmark.circle.fill" : "xmark.circle.fill",
                              color: probe == nil ? Color.secondary : (ok ? Color.green : Color.red))
            }
            Text(detail).font(.caption).foregroundStyle(.secondary)
            if let probe {
                HStack(spacing: 10) {
                    Text("耗时 \(AIPresentation.latency(probe["latency_ms"]?.intValue))")
                    if let code { Text("HTTP \(code)") }
                    if let identity = probe["client_identity_profile"]?.stringValue, !identity.isEmpty {
                        Text("身份：\(AIPresentation.clientIdentity(identity))")
                    }
                }
                .font(.caption2).foregroundStyle(.secondary).monospacedDigit()
            }
            if !ok, let message = probe?["error"]?.stringValue, !message.isEmpty {
                Text(AssistantRedaction.text(message)).font(.caption).foregroundStyle(.red).lineLimit(3)
            }
            if !ok, let suggestion = probe?["suggestion"]?.stringValue, !suggestion.isEmpty, suggestion != probe?["error"]?.stringValue {
                Text("建议：" + AssistantRedaction.text(suggestion)).font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
        .accessibilityElement(children: .combine)
    }

    @ViewBuilder private func recommendationSection(_ result: JSONValue) -> some View {
        let hasRecommendation = result["recommended_api_format"]?.stringValue?.isEmpty == false
        Section {
            LabeledContent("推荐接口协议", value: hasRecommendation ? AIPresentation.apiFormat(result["recommended_api_format"]?.stringValue) : "无法判断")
            if let profile = result["recommended_protocol_profile"]?.stringValue, !profile.isEmpty {
                LabeledContent("推荐协议配置", value: AIPresentation.protocolProfile(profile))
            }
            if let identity = result["recommended_client_identity_profile"]?.stringValue, !identity.isEmpty {
                LabeledContent("推荐客户端身份", value: AIPresentation.clientIdentity(identity))
            }
            if let search = result["recommended_web_search_api_format"]?.stringValue, !search.isEmpty {
                LabeledContent("联网搜索使用", value: AIPresentation.apiFormat(search))
            }
            if let note = result["note"]?.stringValue, !note.isEmpty {
                Text(AssistantRedaction.text(note)).font(.caption).foregroundStyle(.secondary)
            }
            if hasRecommendation {
                if changes.isEmpty {
                    Label("当前配置已与推荐一致，无需修改。", systemImage: "checkmark.seal").foregroundStyle(.green)
                } else {
                    Button { confirmingApply = true } label: {
                        Label("把推荐配置应用到此供应商", systemImage: "checkmark.seal")
                    }
                    .disabled(applying || applyUncertain)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("ai.protocols.apply")
                }
            }
            if applying { ProgressView("正在保存到服务器…") }
            if let applyError { NoticeView(title: applyUncertain ? "保存结果待确认" : "应用失败", message: applyError) }
            if applied { Label("已应用，建议再做一次测活确认。", systemImage: "checkmark.circle").foregroundStyle(.green) }
        } header: {
            Text("推荐配置")
        } footer: {
            Text(hasRecommendation ? "应用后只修改上述几项，密钥与模型不变。" : "所有接口都不可用时无法给出推荐，请先检查服务地址、密钥和网络代理。")
        }
    }

    private func detect() async {
        guard let provider, let id = providerID, let type = provider["provider"]?.stringValue, !detecting else { return }
        detecting = true; error = nil; result = nil; applied = false; applyError = nil; applyUncertain = false
        defer { detecting = false }
        var body: [String: JSONValue] = ["provider": .string(type), "pid": .integer(Int64(id))]
        if let base = provider["base_url"]?.stringValue, !base.isEmpty { body["base_url"] = .string(base) }
        if let proxy = provider["proxy_id"]?.intValue { body["proxy_id"] = .integer(Int64(proxy)) }
        if !modelID.isEmpty { body["model"] = .string(modelID) }
        do {
            result = try await model.request(path: "/api/commands/llm-providers/detect-protocols", method: "POST", body: .object(body))
        } catch { self.error = error.localizedDescription }
    }

    private func applyRecommendation() async {
        guard let id = providerID, !applying, !applyUncertain else { return }
        let body = changes
        guard !body.isEmpty else { return }
        applying = true; applyError = nil; applied = false
        defer { applying = false }
        do {
            let response = try await model.request(path: "/api/commands/llm-providers/\(id)", method: "PATCH", body: .object(body))
            if response["id"] != nil { workspace.replaceProvider(response) } else { await workspace.refresh(model) }
            applied = true
        } catch {
            applyError = error.localizedDescription
            applyUncertain = (error as? APIError)?.isOutcomeUncertain == true
        }
    }
}

struct AIIdentityVersionsView: View {
    @Environment(AppModel.self) private var model
    @State private var items: [JSONValue] = []
    @State private var profiles: [JSONValue] = []
    @State private var values: [String: String] = [:]
    @State private var latest: [String: JSONValue] = [:]
    @State private var loading = false
    @State private var loaded = false
    @State private var loadError: String?
    @State private var detecting = false
    @State private var detectError: String?
    @State private var saving = false
    @State private var saveError: String?
    @State private var uncertain = false
    @State private var saved = false
    @State private var confirmingSave = false

    private var originalValues: [String: String] {
        Dictionary(items.compactMap { (item: JSONValue) -> (String, String)? in
            guard let key = item["key"]?.stringValue else { return nil }
            return (key, item["current"]?.stringValue ?? "")
        },
                   uniquingKeysWith: { first, _ in first })
    }
    private var defaults: [String: String] {
        Dictionary(items.compactMap { (item: JSONValue) -> (String, String)? in
            guard let key = item["key"]?.stringValue else { return nil }
            return (key, item["default"]?.stringValue ?? "")
        },
                   uniquingKeysWith: { first, _ in first })
    }
    private var dirty: Bool { !items.isEmpty && trimmedValues != originalValues }
    private var trimmedValues: [String: String] { values.mapValues { $0.trimmingCharacters(in: .whitespacesAndNewlines) } }
    private var invalidLabels: [String] {
        items.compactMap { item in
            guard let key = item["key"]?.stringValue, let value = trimmedValues[key], !Self.isValidVersion(value) else { return nil }
            return item["label"]?.stringValue ?? key
        }
    }
    private var upgradable: [String: String] {
        var result: [String: String] = [:]
        for (key, item) in latest {
            if let version = item["latest"]?.stringValue, Self.isValidVersion(version), trimmedValues[key] != version { result[key] = version }
        }
        return result
    }

    static func isValidVersion(_ value: String) -> Bool {
        value.wholeMatch(of: #/\d+(?:\.\d+){1,3}(?:-[A-Za-z]+(?:\.\d+)*)?/#) != nil
    }

    var body: some View {
        Form {
            Section {
                Label("这是做什么的？", systemImage: "questionmark.circle").font(.headline)
                Text("有些中转站只接受官方客户端（例如 Codex、Claude Code）发来的请求，会检查请求里声明的客户端名称和版本号。TelePilot 会模拟这些客户端的“身份”。官方客户端升级后，中转站可能拒绝旧版本，这时在这里更新版本号即可。只有测活提示“客户端受限”或“版本过旧”时才需要修改。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            if loading && !loaded { Section { ProgressView("正在读取版本…") } }
            if let loadError { Section { NoticeView(title: "读取失败", message: loadError) } }
            if loaded {
                Section {
                    ForEach(items, id: \.self) { item in versionRow(item) }
                    if items.isEmpty { Text("服务器没有返回可配置的客户端版本。").foregroundStyle(.secondary) }
                } header: {
                    Text("模拟的客户端版本")
                } footer: {
                    Text("版本号格式如 1.2.3 或 1.2.3-beta.1。保持“内置默认”表示使用 TelePilot 已验证过的版本。")
                }
                Section {
                    Button { Task { await detect() } } label: {
                        HStack(spacing: 8) {
                            if detecting { ProgressView() }
                            Label(detecting ? "正在查询最新版本…" : "查询官方最新版本", systemImage: "arrow.down.circle")
                        }
                    }
                    .disabled(detecting || items.isEmpty)
                    if !upgradable.isEmpty {
                        Button {
                            for (key, version) in upgradable { values[key] = version }
                            saved = false
                        } label: {
                            Label("全部填入最新版本（\(upgradable.count) 项）", systemImage: "arrow.up.circle")
                        }
                    }
                    if let detectError { NoticeView(title: "查询失败", message: detectError) }
                } footer: {
                    Text("只从公开的软件仓库查询版本号，不会修改设置，也不消耗模型额度。填入后还需点“保存修改”。")
                }
                Section {
                    Button { confirmingSave = true } label: {
                        HStack(spacing: 8) {
                            Spacer(minLength: 0)
                            if saving { ProgressView() }
                            Label("保存修改", systemImage: "checkmark.circle").font(.headline)
                            Spacer(minLength: 0)
                        }
                        .frame(minHeight: 44)
                    }
                    .disabled(!dirty || saving || uncertain || !invalidLabels.isEmpty)
                    .accessibilityIdentifier("ai.identity.save")
                    if !invalidLabels.isEmpty {
                        Text("版本号格式不正确：\(invalidLabels.joined(separator: "、"))").font(.caption).foregroundStyle(.red)
                    }
                    if dirty && !saving {
                        Button(role: .destructive) { values = originalValues } label: {
                            Label("放弃修改", systemImage: "arrow.uturn.backward")
                        }
                    }
                    if let saveError { NoticeView(title: uncertain ? "保存结果待确认" : "保存失败", message: saveError) }
                    if uncertain { Text("请下拉刷新，确认服务器上的实际版本后再修改。").font(.caption).foregroundStyle(.orange) }
                    if saved { Label("已保存，新请求将使用新版本号。", systemImage: "checkmark.circle").foregroundStyle(.green) }
                } footer: {
                    Text("保存后立即对新请求生效，所有账号会自动重新加载配置。")
                }
                if !profiles.isEmpty {
                    Section {
                        ForEach(profiles, id: \.self) { profile in
                            NavigationLink { AIIdentityProfileView(profile: profile) } label: {
                                AINavigationRow(title: profile["label"]?.stringValue ?? AIPresentation.clientIdentity(profile["profile"]?.stringValue),
                                                detail: profile["description"]?.stringValue ?? "", symbol: "person.text.rectangle")
                            }
                        }
                    } header: {
                        Text("各客户端身份会发送什么")
                    } footer: {
                        Text("仅供查看，帮助理解每种身份在请求中附带了哪些信息。")
                    }
                }
            }
        }
        .navigationTitle("客户端身份版本").navigationBarTitleDisplayMode(.inline)
        .task { if !loaded { await load() } }
        .refreshable { await load() }
        .confirmationDialog("保存客户端身份版本？", isPresented: $confirmingSave, titleVisibility: .visible) {
            Button("保存修改") { Task { await save() } }
            Button("取消", role: .cancel) {}
        } message: {
            Text("保存后，所有使用这些身份的 AI 请求会改用新版本号，所有账号会重新加载配置。")
        }
    }

    @ViewBuilder private func versionRow(_ item: JSONValue) -> some View {
        let key = item["key"]?.stringValue ?? ""
        let defaultVersion = item["default"]?.stringValue ?? ""
        let current = trimmedValues[key] ?? ""
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(item["label"]?.stringValue ?? key).font(.subheadline.weight(.medium))
                Spacer(minLength: 8)
                TextField("版本号", text: Binding(get: { values[key] ?? "" }, set: { values[key] = $0; saved = false }))
                    .keyboardType(.numbersAndPunctuation)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                    .multilineTextAlignment(.trailing)
                    .monospacedDigit()
                    .frame(maxWidth: 150)
            }
            HStack(spacing: 10) {
                Text("内置默认 \(defaultVersion)")
                if let detected = latest[key] {
                    if let version = detected["latest"]?.stringValue {
                        Text(version == current ? "已是最新" : "官方最新 \(version)")
                            .foregroundStyle(version == current ? Color.green : Color.orange)
                    } else if detected["error"] != nil {
                        Text("最新版本查询失败")
                    }
                }
                Spacer(minLength: 0)
            }
            .font(.caption).foregroundStyle(.secondary)
            HStack(spacing: 16) {
                if current != defaultVersion, !defaultVersion.isEmpty {
                    Button("恢复默认", systemImage: "arrow.counterclockwise") { values[key] = defaultVersion; saved = false }
                }
                if let version = latest[key]?["latest"]?.stringValue, version != current, Self.isValidVersion(version) {
                    Button("填入最新", systemImage: "arrow.up.circle") { values[key] = version; saved = false }
                }
            }
            .buttonStyle(.borderless)
            .font(.caption)
            if item["detectable"]?.boolValue != true {
                Text("此项无法自动查询最新版本，只能手动填写。").font(.caption2).foregroundStyle(.secondary)
            }
        }
        .padding(.vertical, 2)
    }

    private func apply(_ value: JSONValue) {
        items = value["items"]?.arrayValue ?? []
        profiles = value["profiles"]?.arrayValue ?? []
        values = originalValues
    }

    private func load() async {
        guard !loading else { return }
        loading = true
        defer { loading = false }
        do {
            apply(try await model.request(path: "/api/commands/llm-providers/identity-versions"))
            loaded = true; loadError = nil; saveError = nil; uncertain = false
        } catch { loadError = error.localizedDescription }
    }

    private func detect() async {
        guard !detecting else { return }
        detecting = true; detectError = nil
        defer { detecting = false }
        do {
            let response = try await model.request(path: "/api/commands/llm-providers/identity-versions/detect", method: "POST")
            var next: [String: JSONValue] = [:]
            for item in response["items"]?.arrayValue ?? [] {
                if let key = item["key"]?.stringValue { next[key] = item }
            }
            latest = next
        } catch { detectError = error.localizedDescription }
    }

    private func save() async {
        guard dirty, invalidLabels.isEmpty, !saving, !uncertain else { return }
        let defaults = self.defaults
        var overrides: [String: JSONValue] = [:]
        for (key, value) in trimmedValues where !value.isEmpty && value != defaults[key] { overrides[key] = .string(value) }
        saving = true; saveError = nil; saved = false
        defer { saving = false }
        do {
            let response = try await model.request(path: "/api/commands/llm-providers/identity-versions", method: "PUT",
                                                   body: .object(["overrides": .object(overrides)]))
            apply(response)
            saved = true
        } catch {
            saveError = error.localizedDescription
            uncertain = (error as? APIError)?.isOutcomeUncertain == true
        }
    }
}

private struct AIIdentityProfileView: View {
    let profile: JSONValue

    private func management(_ raw: String?) -> String {
        ["fixed": "固定值", "runtime": "每次请求生成", "protocol": "接口必需", "transport": "网络层处理", "excluded": "不发送"][raw ?? ""] ?? "固定值"
    }

    var body: some View {
        List {
            Section {
                if let description = profile["description"]?.stringValue, !description.isEmpty { Text(description) }
                let formats = (profile["api_formats"]?.arrayValue ?? []).compactMap(\.stringValue).map { AIPresentation.apiFormat($0) }
                if !formats.isEmpty { LabeledContent("适用接口", value: formats.joined(separator: "、")) }
                if let source = profile["source"]?.stringValue, !source.isEmpty { LabeledContent("信息来源", value: source) }
            }
            Section {
                ForEach(Array((profile["headers"]?.arrayValue ?? []).enumerated()), id: \.offset) { _, header in
                    VStack(alignment: .leading, spacing: 4) {
                        HStack(alignment: .firstTextBaseline) {
                            Text(header["name"]?.stringValue ?? "未命名").font(.caption.monospaced())
                            Spacer(minLength: 6)
                            Text(management(header["management"]?.stringValue)).font(.caption2).foregroundStyle(.secondary)
                        }
                        if let value = header["value"]?.stringValue, !value.isEmpty {
                            Text(AssistantRedaction.text(value)).font(.caption2.monospaced()).foregroundStyle(.secondary).lineLimit(2)
                        }
                        if let description = header["description"]?.stringValue, !description.isEmpty {
                            Text(description).font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
            } header: {
                Text("请求头")
            } footer: {
                Text("“固定值”每次相同；“每次请求生成”由系统自动生成且不含原始业务编号；“接口必需”是接口本身要求的；“网络层处理”由网络库负责；“不发送”表示有意不附带。")
            }
        }
        .navigationTitle(profile["label"]?.stringValue ?? "客户端身份").navigationBarTitleDisplayMode(.inline)
    }
}
