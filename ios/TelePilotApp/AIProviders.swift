import SwiftUI
import TelePilotCore

struct AIProviderListSection: View {
    @Bindable var workspace: AIWorkspaceStore
    /// 搜索内容由 AI 页顶部（分段控件下方）的统一搜索栏提供。
    var search = ""

    private var providers: [JSONValue] {
        let search = self.search.trimmingCharacters(in: .whitespaces)
        guard !search.isEmpty else { return workspace.providers }
        return workspace.providers.filter { provider in
            [provider["name"]?.stringValue, provider["default_model"]?.stringValue, provider["provider"]?.stringValue]
                .contains { $0?.localizedCaseInsensitiveContains(search) == true }
        }
    }

    var body: some View {
        Section {
            ForEach(providers, id: \.self) { provider in
                NavigationLink { AIProviderDetailView(provider: provider, workspace: workspace) } label: {
                    AIProviderRow(provider: provider, workspace: workspace)
                }
                .accessibilityIdentifier("ai.provider.\(provider["id"]?.intValue ?? 0)")
            }
            if workspace.loaded, workspace.providers.isEmpty {
                Text("点右上角“+”先添加一个模型供应商，再获取或添加模型。Ollama 本地服务可以不配置 API Key。")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else if workspace.loaded, providers.isEmpty {
                Text("没有匹配的供应商").foregroundStyle(.secondary)
            }
        } header: {
            if workspace.loaded { Text("\(providers.count) 个供应商") }
        } footer: {
            Text("“可调用”表示已配好主密钥或启用的密钥池（Ollama 本地模型无需密钥）。只有模型清单里启用的模型，才能被 AI 指令和测活使用。")
        }
    }
}

struct AIProviderRow: View {
    let provider: JSONValue
    let workspace: AIWorkspaceStore

    var body: some View {
        let health = AIPresentation.health(provider, in: workspace.health)
        let issues = health.filter(AIPresentation.hasIssue)
        VStack(alignment: .leading, spacing: 7) {
            HStack(alignment: .firstTextBaseline, spacing: 10) {
                Text(AIPresentation.providerName(provider)).font(.headline).lineLimit(2)
                Spacer(minLength: 4)
                if !issues.isEmpty {
                    AIStatusLabel(title: "\(issues.count) 项异常", symbol: "exclamationmark.circle.fill", color: .orange)
                } else if AIPresentation.ready(provider) {
                    AIStatusLabel(title: "可调用", symbol: "checkmark.circle.fill", color: .green)
                } else {
                    AIStatusLabel(title: "缺少有效密钥", symbol: "key.slash", color: .orange)
                }
            }
            Text(provider["default_model"]?.stringValue ?? "未设置默认模型")
                .font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            HStack(spacing: 12) {
                Text(AIPresentation.providerType(provider["provider"]?.stringValue))
                Text("\(AIPresentation.enabledModels(provider).count) 个启用模型")
                if let keys = provider["pool_keys"]?.arrayValue, !keys.isEmpty {
                    Text("密钥池 \(keys.filter { $0["enabled"]?.boolValue == true }.count)/\(keys.count)")
                }
            }
            .font(.caption).foregroundStyle(.secondary)
        }
        .padding(.vertical, 4)
        .accessibilityElement(children: .combine)
    }
}

struct AIProviderDetailView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Bindable var workspace: AIWorkspaceStore
    @State private var provider: JSONValue

    init(provider: JSONValue, workspace: AIWorkspaceStore) {
        self.workspace = workspace
        _provider = State(initialValue: provider)
    }

    private var base: String { "/api/commands/llm-providers/\(provider["id"]?.intValue ?? 0)" }

    var body: some View {
        List {
            Section {
                LabeledContent("类型", value: AIPresentation.providerType(provider["provider"]?.stringValue))
                LabeledContent("默认模型", value: provider["default_model"]?.stringValue ?? "未设置")
                LabeledContent("接口协议", value: AIPresentation.apiFormat(provider["api_format"]?.stringValue))
                LabeledContent("调用密钥") {
                    Text(AIPresentation.ready(provider) ? "已配置" : "未配置有效密钥")
                        .foregroundStyle(AIPresentation.ready(provider) ? .green : .orange)
                }
                if provider["provider"]?.stringValue == "ollama" {
                    Text("Ollama 本地模型不需要密钥，能否调用取决于本地服务是否在运行。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            Section {
                NavigationLink {
                    AIProviderModelsView(provider: provider, workspace: workspace)
                } label: {
                    LabeledContent("模型清单") {
                        Text("\(AIPresentation.enabledModels(provider).count) 启用 / \(provider["models"]?.arrayValue?.count ?? 0) 全部")
                    }
                }
                .accessibilityIdentifier("ai.provider.models")
            } header: {
                Text("模型")
            } footer: {
                Text("在模型清单顶部可以从供应商同步模型，新发现的模型会并入清单，默认不启用。")
            }

            Section("密钥") {
                NavigationLink {
                    ManagementCollectionView(resource: .init(title: "密钥池", path: base + "/keys", kind: .keys, itemsKey: "keys",
                        create: .init(title: "添加密钥", path: base + "/keys"), itemPath: base + "/keys/{id}"))
                } label: {
                    LabeledContent("密钥池") {
                        let keys = provider["pool_keys"]?.arrayValue ?? []
                        let enabledCount = keys.filter { $0["enabled"]?.boolValue == true }.count
                        Text(keys.isEmpty ? "未配置" : "\(enabledCount) 个启用")
                    }
                }
                .accessibilityIdentifier("ai.provider.keys")
                if provider["has_api_key"]?.boolValue == true {
                    NavigationLink { AIProviderSecretView(path: base + "/api-key") } label: {
                        Label("验证后查看主密钥", systemImage: "key")
                    }
                } else {
                    Text("主密钥未配置；可添加池内密钥，或在编辑供应商时替换主密钥。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            Section {
                NavigationLink { AILivenessView(workspace: workspace, providerID: provider["id"]?.intValue) } label: {
                    Label("测活此供应商", systemImage: "waveform.path.ecg")
                }
                NavigationLink { AIModelQuickTestView(provider: provider) } label: {
                    Label("单个模型快速测试", systemImage: "bolt.horizontal")
                }
                .accessibilityIdentifier("ai.provider.quicktest")
                NavigationLink { AIModelChatTestView(provider: provider) } label: {
                    Label("多模型对话测试", systemImage: "bubble.left.and.bubble.right")
                }
                .accessibilityIdentifier("ai.provider.chattest")
            } header: {
                Text("连通性")
            } footer: {
                Text("“测活此供应商”最直观：会逐个检查已启用的模型，并显示耗时和失败原因。")
            }

            Section("高级配置") {
                NavigationLink {
                    ManagementFormView(action: .init(title: "编辑供应商", path: base, method: "PATCH",
                        explanation: "仅提交选择编辑的字段。已有密钥与敏感请求头不会回填；留空表示保持不变。", seed: provider)) { value in
                        provider = value; workspace.replaceProvider(value)
                    }
                } label: { Label("编辑连接、路由与输出", systemImage: "square.and.pencil") }
                .accessibilityIdentifier("ai.provider.edit")
                NavigationLink { AIProviderTechnicalView(provider: provider) } label: {
                    Label("服务地址与传输详情", systemImage: "network")
                }
            }

            Section {
                NavigationLink {
                    ManagementFormView(action: .init(title: "删除供应商", path: base, method: "DELETE", destructive: true,
                        explanation: "将删除此供应商及其密钥池配置。关联的 AI 指令可能无法继续调用，请先确认路由和备用模型。")) { _ in
                        Task { await workspace.refresh(model) }
                        dismiss()
                    }
                } label: { Label("删除供应商", systemImage: "trash") }
                .foregroundStyle(.red)
            }
        }
        .navigationTitle(AIPresentation.providerName(provider)).navigationBarTitleDisplayMode(.inline)
        .onChange(of: workspace.providers) { _, providers in
            if let id = provider["id"]?.intValue, let current = providers.first(where: { $0["id"]?.intValue == id }) { provider = current }
        }
    }
}

private struct AIProviderTechnicalView: View {
    let provider: JSONValue
    var body: some View {
        List {
            Section("服务") {
                LabeledContent("服务地址", value: provider["base_url"]?.stringValue ?? "供应商默认地址")
                LabeledContent("调用方式", value: AIPresentation.executionBackend(provider["execution_backend"]?.stringValue))
                LabeledContent("协议配置", value: AIPresentation.protocolProfile(provider["protocol_profile"]?.stringValue))
                LabeledContent("客户端身份", value: AIPresentation.clientIdentity(provider["client_identity_profile"]?.stringValue))
                LabeledContent("联网搜索接口", value: AIPresentation.apiFormat(provider["web_search_api_format"]?.stringValue ?? "auto"))
                LabeledContent("出口代理", value: provider["proxy_id"]?.intValue.map { "代理 #\($0)" } ?? "直连")
            }
            Section {
                LabeledContent("能处理的内容", value: AIPresentation.modality(provider["modality"]?.stringValue))
                LabeledContent("成本等级", value: AIPresentation.costTier(provider["cost_tier"]?.intValue))
                let tags = (provider["tags"]?.arrayValue ?? []).compactMap(\.stringValue).map(AIPresentation.tag)
                LabeledContent("擅长", value: tags.isEmpty ? "未设置" : tags.joined(separator: "、"))
            } header: {
                Text("自动路由参考信息")
            } footer: {
                Text("开启“自动路由”的 AI 指令会参考这些信息挑选模型：例如翻译类问题优先选标为“翻译”“低成本”的模型。")
            }
            if let notes = provider["notes"]?.stringValue, !notes.isEmpty {
                Section("备注") { Text(notes) }
            }
            if let headers = provider["request_headers"]?.arrayValue, !headers.isEmpty {
                Section {
                    ForEach(Array(headers.enumerated()), id: \.offset) { _, header in
                        LabeledContent(header["name"]?.stringValue ?? "未命名") {
                            Text((header["scopes"]?.arrayValue ?? []).compactMap(\.stringValue).joined(separator: "、"))
                        }
                    }
                } header: { Text("请求头") } footer: { Text("仅显示服务器返回的请求头名称与作用域，不显示敏感值。") }
            }
        }
        .navigationTitle("传输详情").navigationBarTitleDisplayMode(.inline)
    }
}

private struct AIProviderSecretView: View {
    let path: String
    @Environment(AppModel.self) private var model
    @State private var key: String?
    @State private var error: String?

    var body: some View {
        List {
            if let key { Section("主密钥") { SensitiveText(value: key) } }
            else if let error { NoticeView(title: "无法读取主密钥", message: error) }
            else { ProgressView("正在验证并读取…") }
        }
        .navigationTitle("主密钥").navigationBarTitleDisplayMode(.inline)
        .task {
            do { key = try await model.request(path: path)["api_key"]?.stringValue }
            catch { self.error = error.localizedDescription }
        }
    }
}

struct AIProviderModelsView: View {
    @Bindable var workspace: AIWorkspaceStore
    @Environment(AppModel.self) private var model
    @State private var provider: JSONValue
    @State private var baseline: [JSONValue] = []
    @State private var models: [JSONValue] = []
    @State private var newModel = ""
    @State private var busy = false
    @State private var confirming = false
    @State private var error: String?
    @State private var saved = false
    @State private var uncertain = false
    @State private var syncing = false
    @State private var syncMessage: String?
    @State private var syncError: String?
    @State private var syncUncertain = false

    init(provider: JSONValue, workspace: AIWorkspaceStore) {
        self.workspace = workspace
        _provider = State(initialValue: provider)
    }

    private var path: String { "/api/commands/llm-providers/\(provider["id"]?.intValue ?? 0)" }
    private var dirty: Bool { models != baseline }
    private var anthropic: Bool {
        (provider["api_format"]?.stringValue ?? (provider["provider"]?.stringValue == "anthropic" ? "anthropic_messages" : "")) == "anthropic_messages"
    }

    var body: some View {
        List {
            Section {
                Button {
                    Task { await sync() }
                } label: {
                    HStack {
                        Label(syncing ? "正在从供应商读取…" : "从供应商同步模型", systemImage: "arrow.triangle.2.circlepath")
                        if syncing { Spacer(); ProgressView() }
                    }
                }
                .disabled(syncing || busy || dirty || anthropic)
                .frame(minHeight: 44)
                .accessibilityIdentifier("ai.models.sync")
                if let syncMessage {
                    Label(syncMessage, systemImage: "checkmark.circle").foregroundStyle(.green).font(.subheadline)
                }
                if let syncError {
                    NoticeView(title: syncUncertain ? "同步结果未知" : "同步失败", message: syncError)
                }
            } footer: {
                if anthropic {
                    Text("Claude 消息接口（Messages）没有列出模型的接口，请在下方手动添加模型 ID。")
                } else if dirty {
                    Text("有未保存的修改，请先保存模型选择再同步。")
                } else {
                    Text("读取供应商的模型列表并并入下方清单：已有模型的启用状态保持不变，新发现的模型默认不启用。只有启用的模型才能被 AI 指令选择和参与测活。")
                }
            }
            Section {
                ForEach(Array(models.enumerated()), id: \.offset) { index, item in
                    VStack(alignment: .leading, spacing: 7) {
                        Toggle(isOn: enabled(index)) {
                            Text(item["label"]?.stringValue?.nilIfEmpty ?? item["id"]?.stringValue ?? "未命名模型")
                                .font(.subheadline.weight(.medium))
                        }
                        HStack {
                            Text(item["id"]?.stringValue ?? "").font(.caption).foregroundStyle(.secondary).lineLimit(2)
                            Spacer()
                            if item["custom"]?.boolValue == true { Text("手动添加").font(.caption2).foregroundStyle(.secondary) }
                        }
                        Text(AIModelCapabilities(item).summary)
                            .font(.caption).foregroundStyle(.secondary)
                            .accessibilityLabel("能力：" + AIModelCapabilities(item).summary)
                        NavigationLink { AIModelDetailView(provider: provider, item: item) } label: {
                            Label("模型详情与测试", systemImage: "info.circle")
                        }
                        .font(.caption)
                    }
                    .accessibilityIdentifier("ai.model.\(item["id"]?.stringValue ?? "unknown")")
                }
                if models.isEmpty { Text("还没有模型。可以从供应商同步，或在下方手动添加。").foregroundStyle(.secondary) }
            } header: {
                Text("模型清单（\(models.filter { $0["enabled"]?.boolValue == true }.count) 启用 / \(models.count) 全部）")
            } footer: {
                Text("能力依据服务器记录的模型信息显示，“未知”表示没有这项数据；可在“编辑供应商”中修改。")
            }
            Section("手动添加") {
                TextField("模型 ID", text: $newModel).textInputAutocapitalization(.never).autocorrectionDisabled()
                Button("添加模型", systemImage: "plus") { addModel() }
                    .disabled(newModel.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || busy)
            }
            if let error { Section { NoticeView(title: uncertain ? "保存结果待确认" : "模型清单未保存", message: error) } }
            if saved { Section { Label("模型清单已保存，服务器已返回最新配置。", systemImage: "checkmark.circle").foregroundStyle(.green) } }
            Section {
                Button("保存模型选择") { confirming = true }
                    .disabled(!dirty || busy || uncertain || models.isEmpty)
                    .frame(minHeight: 44)
                    .accessibilityIdentifier("ai.models.save")
                    .confirmationDialog("保存模型选择？", isPresented: $confirming, titleVisibility: .visible) {
                        Button("确认保存") { Task { await save() } }
                        Button("取消", role: .cancel) {}
                    } message: { Text("将启用 \(models.filter { $0["enabled"]?.boolValue == true }.count) 个模型，停用其余候选模型。") }
                if busy { ProgressView("正在保存到服务器…") }
                if uncertain { Text("请返回并重新读取供应商，确认服务器实际状态后再保存。").font(.caption) }
            } footer: { Text("保存时会整体更新模型清单，各模型已有的能力信息会原样保留。") }
        }
        .navigationTitle("模型清单").navigationBarTitleDisplayMode(.inline)
        .task {
            guard baseline.isEmpty, models.isEmpty else { return }
            baseline = provider["models"]?.arrayValue ?? []
            models = baseline
        }
    }

    /// POST fetch-models：服务器合并后返回最新供应商，直接替换本页清单。不自动重试；
    /// 网络中断时只做只读刷新，确认服务器实际状态。
    private func sync() async {
        guard !syncing, !dirty else { return }
        syncing = true; syncMessage = nil; syncError = nil; syncUncertain = false; saved = false
        defer { syncing = false }
        let before = Set(baseline.compactMap { $0["id"]?.stringValue })
        do {
            let response = try await model.request(path: path + "/fetch-models", method: "POST")
            guard let updated = response["provider"] else { throw APIError.incompatibleResponse }
            apply(updated)
            let added = (updated["models"]?.arrayValue ?? []).compactMap { $0["id"]?.stringValue }.filter { !before.contains($0) }
            let fetched = response["fetched"]?.intValue ?? 0
            syncMessage = added.isEmpty
                ? "已读取 \(fetched) 个模型，没有新模型。"
                : "已读取 \(fetched) 个模型，新增 \(added.count) 个（默认不启用）。"
        } catch {
            syncError = error.localizedDescription
            syncUncertain = (error as? APIError)?.isOutcomeUncertain == true
            if syncUncertain {
                await workspace.refresh(model)
                if let id = provider["id"]?.intValue, let current = workspace.providers.first(where: { $0["id"]?.intValue == id }) {
                    apply(current)
                    syncError = (syncError ?? "") + "\n已重新读取服务器上的模型清单，请确认是否已同步。"
                }
            }
        }
    }

    private func apply(_ updated: JSONValue) {
        provider = updated
        workspace.replaceProvider(updated)
        baseline = updated["models"]?.arrayValue ?? []
        models = baseline
    }

    private func enabled(_ index: Int) -> Binding<Bool> {
        Binding(get: { models[index]["enabled"]?.boolValue == true }, set: { value in
            var item = models[index].objectValue ?? [:]
            item["enabled"] = .bool(value)
            models[index] = .object(item)
            saved = false
        })
    }

    private func addModel() {
        let id = newModel.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !id.isEmpty, !models.contains(where: { $0["id"]?.stringValue == id }) else {
            error = "这个模型 ID 已在清单中。"; return
        }
        models.append(["id": .string(id), "enabled": true, "custom": true, "label": .null])
        newModel = ""; saved = false; error = nil
    }

    private func save() async {
        guard !busy, !uncertain else { return }
        busy = true; error = nil; saved = false
        defer { busy = false }
        do {
            let response = try await model.request(path: path, method: "PATCH", body: ["models": .array(models)])
            apply(response)
            saved = true
        } catch {
            self.error = error.localizedDescription
            uncertain = (error as? APIError)?.isOutcomeUncertain == true
        }
    }
}

private extension String {
    var nilIfEmpty: String? { isEmpty ? nil : self }
}

// MARK: - 添加供应商

/// 原生“添加供应商”表单，字段与 Web 新建 Provider 页对齐：
/// 常用项（名称、地址、密钥、接口协议、模型）直接展示；
/// 服务类型、调用方式、客户端身份、协议档案、联网搜索接口、出口代理与自动路由信息收进“高级设置”，
/// 默认值与 Web 一致。Web 新建时不展示的密钥池策略、模型能力原始字段不在这里出现；
/// 兼容请求头属于少见需求，请在 Web 管理台配置。
struct AIProviderCreateView: View {
    @Bindable var workspace: AIWorkspaceStore
    @Environment(AppModel.self) private var model

    // 常用
    @State private var name = ""
    @State private var baseURL = ""
    @State private var apiKey = ""
    @State private var apiFormat = "responses"
    @State private var defaultModel = ""

    // 模型列表
    @State private var fetchedModels: [JSONValue] = []
    @State private var enabledModelIDs: Set<String> = []
    @State private var modelSearch = ""
    @State private var fetching = false
    @State private var fetchError: String?
    @State private var fetchedOnce = false

    // 高级
    @State private var showAdvanced = false
    @State private var providerKind = "openai"
    @State private var executionBackend = "direct"
    @State private var clientIdentity = "auto"
    @State private var protocolProfile = "standard"
    @State private var webSearchFormat = "auto"
    @State private var proxyID: Int?
    @State private var proxies: [JSONValue] = []
    @State private var proxiesError: String?
    @State private var modality = "text"
    @State private var costTier = 2
    @State private var tags: Set<String> = ["chat"]
    @State private var notes = ""

    // 提交
    @State private var confirming = false
    @State private var saving = false
    @State private var validationError: String?
    @State private var saveError: String?
    @State private var uncertain = false
    @State private var created: JSONValue?

    private static let apiFormats: [AIProviderChoice] = [
        .init("responses", "新版响应接口（Responses）", "OpenAI 推荐的新接口，DeepSeek 等也支持。不确定时先用它，读取模型或测活失败再换“对话补全接口”。"),
        .init("chat_completions", "对话补全接口（Chat Completions）", "最常见、兼容性最好的接口，大多数中转站和本地服务（如 Ollama）都支持。"),
        .init("anthropic_messages", "Claude 消息接口（Messages）", "Anthropic 官方或兼容 Claude 的中转站使用。选择后服务类型会自动改为 Anthropic。")
    ]
    private static let providerKinds: [AIProviderChoice] = [
        .init("openai", "OpenAI 兼容服务"), .init("anthropic", "Anthropic"), .init("ollama", "Ollama 本地服务")
    ]
    private static let defaultBaseURLs = [
        "openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com/v1", "ollama": "http://localhost:11434/v1"
    ]
    private static let identities: [AIProviderChoice] = [
        .init("auto", hint: "推荐。按接口协议自动选择合适的身份，普通供应商无需改动。"),
        .init("minimal", hint: "只发送接口必需的信息，不模拟任何客户端。上游不检查客户端时使用。"),
        .init("openai_sdk", hint: "模拟 OpenAI 官方 Python SDK，适用于标准 OpenAI 接口。"),
        .init("codex_tui", hint: "模拟 Codex 命令行客户端的基础身份。只在中转站明确要求 Codex 客户端时使用。"),
        .init("codex_desktop", hint: "模拟 Codex 桌面版的基础身份。只在中转站明确要求时使用。"),
        .init("claude_code", hint: "模拟 Claude Code 命令行，适用于 Claude 消息接口的中转站。"),
        .init("grok_cli", hint: "模拟 Grok 命令行，适用于要求 Grok 客户端的 Responses 服务。")
    ]
    private static let profiles: [String: [AIProviderChoice]] = [
        "chat_completions": [.init("standard", hint: "使用标准字段。")],
        "responses": [
            .init("standard", hint: "通用：适合大多数兼容服务。"),
            .init("openai_responses", hint: "OpenAI 官方：启用 OpenAI 官方专有字段。"),
            .init("deepseek_responses", hint: "DeepSeek：按 DeepSeek V4 的要求调整字段，支持联网搜索和图片。"),
            .init("codex_responses", hint: "Codex：只用于明确要求 Codex 身份的服务。")
        ],
        "anthropic_messages": [
            .init("standard", hint: "标准 Anthropic 字段。"),
            .init("claude_code_proxy", hint: "只用于明确要求 Claude Code 语义的中转站。")
        ]
    ]
    private static let webSearchFormats: [AIProviderChoice] = [
        .init("auto", hint: "推荐。平时按上面的接口协议调用；需要联网搜索时自动改用 Responses。"),
        .init("responses", hint: "联网搜索固定使用 Responses，适合 OpenAI 官方或同时支持两种接口的服务。"),
        .init("chat_completions", hint: "只有你的服务在对话补全接口里自己实现了搜索时才选。"),
        .init("anthropic_messages", hint: "预留，目前一般不建议用于联网搜索。")
    ]
    private static let modalities = ["text", "vision", "audio", "multimodal"]
    private static let allTags = ["chat", "code", "math", "translate", "vision", "long_context", "reason", "smart", "cheap", "fast", "classify"]

    private var gatewayMode: Bool { executionBackend == "codex_gateway" }
    private var trimmedName: String { name.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var trimmedDefault: String { defaultModel.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var trimmedBaseURL: String { baseURL.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var trimmedKey: String { apiKey.trimmingCharacters(in: .whitespacesAndNewlines) }
    private var effectiveName: String { trimmedName.isEmpty ? inferredName : trimmedName }
    private var formatHint: String { Self.apiFormats.first { $0.value == apiFormat }?.hint ?? "" }
    private var profileOptions: [AIProviderChoice] { Self.profiles[apiFormat] ?? [.init("standard", hint: "使用标准字段。")] }
    private var supportedProxies: [JSONValue] {
        proxies.filter { ["socks5", "http", "https"].contains(($0["type"]?.stringValue ?? "").lowercased()) }
    }
    private var visibleModels: [JSONValue] {
        let query = modelSearch.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !query.isEmpty else { return fetchedModels }
        return fetchedModels.filter { ($0["id"]?.stringValue ?? "").localizedCaseInsensitiveContains(query) }
    }
    private var locked: Bool { saving || created != nil }

    var body: some View {
        Form {
            Section {
                Text("填写接入地址和密钥，读取模型列表并选好默认模型即可。其余选项都有合适的默认值，普通供应商不需要展开“高级设置”。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            connectionSection
            protocolSection
            modelSection
            advancedSection
            submitSection
            if let created { resultSection(created) }
        }
        .navigationTitle("添加供应商").navigationBarTitleDisplayMode(.inline)
        .disabled(saving)
        .task { await loadProxies() }
    }

    // MARK: - 连接

    private var connectionSection: some View {
        Section {
            TextField("名称（可留空，自动生成）", text: $name)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .accessibilityIdentifier("ai.providerCreate.name")
            TextField(Self.defaultBaseURLs[providerKind] ?? "https://api.example.com/v1", text: $baseURL)
                .keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                .accessibilityIdentifier("ai.providerCreate.baseURL")
            SecretField(
                title: "API Key",
                text: $apiKey,
                placeholder: providerKind == "ollama" ? "本地 Ollama 可留空" : "sk-…",
                identifier: "ai.providerCreate.apiKey")
        } header: {
            Text("连接信息")
        } footer: {
            Text("接入地址留空表示使用该服务类型的官方默认地址（\(Self.defaultBaseURLs[providerKind] ?? "")）；使用中转站时填写中转站给出的地址，通常以 /v1 结尾。API Key 加密保存在服务器，之后列表中不会显示明文。")
        }
        .disabled(locked)
    }

    private var protocolSection: some View {
        Section {
            Picker(selection: Binding(get: { apiFormat }, set: { setAPIFormat($0) })) {
                ForEach(Self.apiFormats) { Text($0.title).tag($0.value) }
            } label: {
                Label("接口协议", systemImage: "arrow.left.arrow.right")
            }
            .disabled(gatewayMode || locked)
            Text(gatewayMode ? "Codex 兼容模式固定使用 Responses 接口。" : formatHint)
                .font(.caption).foregroundStyle(.secondary)
        } header: {
            Text("接口协议")
        } footer: {
            Text("接口协议决定 TelePilot 用哪种格式和供应商通信。不确定时，可在添加后到“高级：接口协议与客户端身份”里自动检测。")
        }
    }

    // MARK: - 模型

    private var modelSection: some View {
        Section {
            Button {
                Task { await fetchModels() }
            } label: {
                HStack(spacing: 8) {
                    if fetching { ProgressView() }
                    Label(fetching ? "正在读取模型列表…" : (fetchedOnce ? "重新读取模型列表" : "读取模型列表"), systemImage: "arrow.triangle.2.circlepath")
                }
            }
            .disabled(fetching || locked || (trimmedKey.isEmpty && providerKind != "ollama"))
            .accessibilityIdentifier("ai.providerCreate.fetchModels")
            if trimmedKey.isEmpty && providerKind != "ollama" {
                Text("先填写 API Key，才能读取模型列表。").font(.caption).foregroundStyle(.secondary)
            }
            if let fetchError { NoticeView(title: "读取模型列表失败", message: fetchError) }
            if fetchedOnce && fetchedModels.isEmpty && fetchError == nil {
                Text("供应商没有返回模型。可以直接在下方手动填写默认模型。").font(.caption).foregroundStyle(.secondary)
            }
            if fetchedModels.count > 12 {
                TextField("搜索模型", text: $modelSearch)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            }
            ForEach(visibleModels, id: \.self) { item in
                if let id = item["id"]?.stringValue {
                    Toggle(isOn: Binding(get: { enabledModelIDs.contains(id) }, set: { toggleModel(id, $0) })) {
                        HStack {
                            Text(id).font(.subheadline).lineLimit(2)
                            if id == trimmedDefault {
                                Spacer(minLength: 4)
                                Text("默认").font(.caption2).foregroundStyle(.secondary)
                            }
                        }
                    }
                    .disabled(locked)
                }
            }
            if !enabledModelIDs.isEmpty {
                Picker(selection: $defaultModel) {
                    if trimmedDefault.isEmpty || !enabledModelIDs.contains(trimmedDefault) {
                        Text(trimmedDefault.isEmpty ? "未选择" : trimmedDefault).tag(defaultModel)
                    }
                    ForEach(enabledModelIDs.sorted(), id: \.self) { Text($0).tag($0) }
                } label: {
                    Label("默认模型", systemImage: "star")
                }
                .disabled(locked)
            }
            TextField("默认模型 ID，例如 gpt-5.4-mini", text: $defaultModel)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .disabled(locked)
                .accessibilityIdentifier("ai.providerCreate.defaultModel")
        } header: {
            Text("模型")
        } footer: {
            Text("读取模型列表只查询供应商有哪些模型，不会发起对话或产生费用。打开开关的模型会被启用，可在 AI 指令和测活中使用；默认模型是未指定模型时使用的那一个。读取失败时也可以直接手动填写默认模型。")
        }
    }

    // MARK: - 高级设置

    private var advancedSection: some View {
        Section {
            DisclosureGroup(isExpanded: $showAdvanced) {
                Picker(selection: Binding(get: { providerKind }, set: { setProviderKind($0) })) {
                    ForEach(Self.providerKinds) { Text($0.title).tag($0.value) }
                } label: {
                    AISettingLabel(title: "服务类型", detail: "决定默认地址和鉴权方式。通常跟随接口协议自动设置；只有本地 Ollama 需要手动选择。")
                }
                .disabled(gatewayMode)

                Picker(selection: Binding(get: { executionBackend }, set: { setExecutionBackend($0) })) {
                    Text("标准 API 直连").tag("direct")
                    Text("Codex 兼容模式（内置网关）").tag("codex_gateway")
                } label: {
                    AISettingLabel(title: "调用方式", detail: "保持“标准 API 直连”即可。只有中转站要求完整的 Codex 客户端请求格式时才选兼容模式，它必须填写 API Key，并固定使用 Responses 接口。")
                }

                Picker(selection: $clientIdentity) {
                    ForEach(Self.identities) { Text(AIPresentation.clientIdentity($0.value)).tag($0.value) }
                } label: {
                    AISettingLabel(title: "客户端身份", detail: "部分中转站只接受特定官方客户端的请求，这里决定 TelePilot 在请求中声明自己是哪个客户端。")
                }
                .disabled(gatewayMode)
                Text(gatewayMode ? "兼容模式由内置网关补齐 Codex 请求信息，此项会保留，切回直连后继续使用。" : (Self.identities.first { $0.value == clientIdentity }?.hint ?? ""))
                    .font(.caption).foregroundStyle(.secondary)

                if apiFormat != "chat_completions" {
                    Picker(selection: $protocolProfile) {
                        ForEach(profileOptions) { Text(AIPresentation.protocolProfile($0.value)).tag($0.value) }
                    } label: {
                        AISettingLabel(title: "协议细节", detail: "同一种接口在不同厂商之间有细微差别，选择对应厂商可避免字段不兼容。")
                    }
                    .disabled(gatewayMode)
                    Text(gatewayMode ? "兼容模式固定使用 Codex 协议细节。" : (profileOptions.first { $0.value == protocolProfile }?.hint ?? ""))
                        .font(.caption).foregroundStyle(.secondary)
                }

                Picker(selection: $webSearchFormat) {
                    ForEach(Self.webSearchFormats) { Text($0.value == "auto" ? "自动（推荐）" : AIPresentation.apiFormat($0.value)).tag($0.value) }
                } label: {
                    AISettingLabel(title: "联网搜索接口", detail: "AI 指令使用联网搜索时调用的接口。")
                }
                .disabled(gatewayMode)
                Text(gatewayMode ? "兼容模式的联网请求固定使用 Responses。" : (Self.webSearchFormats.first { $0.value == webSearchFormat }?.hint ?? ""))
                    .font(.caption).foregroundStyle(.secondary)

                Picker(selection: $proxyID) {
                    Text("直连，不走代理").tag(Int?.none)
                    ForEach(supportedProxies, id: \.self) { proxy in
                        if let id = proxy["id"]?.intValue {
                            Text("#\(id) · \(proxy["type"]?.stringValue ?? "") · \(proxy["host"]?.stringValue ?? ""):\(proxy["port"]?.intValue.map(String.init) ?? "")")
                                .tag(Optional(id))
                        }
                    }
                } label: {
                    AISettingLabel(title: "出口代理", detail: "服务器访问这个供应商时经过的网络代理。服务器能直接访问时保持直连。")
                }
                if let proxiesError { Text("代理列表读取失败：\(proxiesError)").font(.caption).foregroundStyle(.orange) }

                Picker(selection: $modality) {
                    ForEach(Self.modalities, id: \.self) { Text(AIPresentation.modality($0)).tag($0) }
                } label: {
                    AISettingLabel(title: "能处理的内容", detail: "告诉自动路由这个供应商能否看图、听语音。选错不会报错，但看图类请求可能分配不到它。")
                }

                Picker(selection: $costTier) {
                    ForEach(1...3, id: \.self) { Text(AIPresentation.costTier($0)).tag($0) }
                } label: {
                    AISettingLabel(title: "成本等级", detail: "自动路由在同类供应商中挑选时参考的价格档位。")
                }

                NavigationLink {
                    AIProviderTagPicker(tags: $tags, allTags: Self.allTags)
                } label: {
                    AISettingLabel(title: "擅长的任务（自动路由标签）",
                                   detail: tags.isEmpty ? "未设置：只作为备用供应商" : Self.allTags.filter(tags.contains).map(AIPresentation.tag).joined(separator: "、"))
                }

                VStack(alignment: .leading, spacing: 4) {
                    Text("备注（仅自己可见，不影响调用）").font(.caption).foregroundStyle(.secondary)
                    TextField("例如：公司中转站，月底重置额度", text: $notes, axis: .vertical)
                        .lineLimit(1...4)
                }
            } label: {
                Label("高级设置", systemImage: "slider.horizontal.3")
            }
            .disabled(locked)
            .accessibilityIdentifier("ai.providerCreate.advanced")
        } footer: {
            Text("服务类型、调用方式、客户端身份、协议细节、联网搜索接口、出口代理和自动路由信息。普通接入保持默认即可，添加后也可以在“编辑供应商”中修改。兼容请求头等少见配置请在 Web 管理台设置。")
        }
    }

    // MARK: - 提交与结果

    private var submitSection: some View {
        Section {
            Button {
                if let message = validate() { validationError = message; return }
                validationError = nil
                confirming = true
            } label: {
                HStack(spacing: 8) {
                    Spacer(minLength: 0)
                    if saving { ProgressView() }
                    Label(saving ? "正在添加…" : "添加供应商", systemImage: "plus.circle").font(.headline)
                    Spacer(minLength: 0)
                }
                .frame(minHeight: 44)
            }
            .disabled(saving || uncertain || created != nil)
            .accessibilityIdentifier("ai.providerCreate.submit")
            .confirmationDialog("添加供应商？", isPresented: $confirming, titleVisibility: .visible) {
                Button("确认添加") { Task { await save() } }
                Button("取消", role: .cancel) {}
            } message: { Text(confirmationMessage) }
            if let validationError { Text(validationError).font(.caption).foregroundStyle(.red) }
            if let saveError { NoticeView(title: uncertain ? "添加结果待确认" : "添加失败", message: saveError) }
            if uncertain { Text("请返回供应商列表下拉刷新，确认是否已添加，避免重复创建。").font(.caption).foregroundStyle(.orange) }
        } footer: {
            Text("添加后建议立即做一次测活，确认模型可以正常回复。")
        }
    }

    @ViewBuilder private func resultSection(_ provider: JSONValue) -> some View {
        Section {
            Label("已添加“\(AIPresentation.providerName(provider))”", systemImage: "checkmark.circle").foregroundStyle(.green)
            NavigationLink {
                AIProviderDetailView(provider: provider, workspace: workspace)
            } label: {
                Label("打开供应商详情", systemImage: "server.rack")
            }
            if provider["id"]?.intValue != nil {
                NavigationLink {
                    AILivenessView(workspace: workspace, providerID: provider["id"]?.intValue)
                } label: {
                    Label("测活此供应商", systemImage: "waveform.path.ecg")
                }
            }
        }
    }

    private var confirmationMessage: String {
        var text = "将添加“\(effectiveName)”，默认模型 \(trimmedDefault)"
        if !enabledModelIDs.isEmpty { text += "，启用 \(enabledModelIDs.union([trimmedDefault]).count) 个模型" }
        text += "。"
        if trimmedName.isEmpty { text += "名称未填写，将自动命名。" }
        if gatewayMode { text += "将使用 Codex 兼容模式，服务器会验证并按需启动内置网关。" }
        return text
    }

    // MARK: - 逻辑

    private var inferredName: String {
        let modelPart = trimmedDefault.split(separator: "-").prefix(2).joined(separator: "-")
        if let host = URL(string: trimmedBaseURL)?.host(), !host.isEmpty {
            let brand = host.replacingOccurrences(of: "api.", with: "").split(separator: ".").first.map(String.init) ?? host
            let firstModelPart = trimmedDefault.split(separator: "-").first.map(String.init) ?? "provider"
            return String("\(brand)-\(firstModelPart.isEmpty ? "provider" : firstModelPart)".prefix(64))
        }
        return String("\(modelPart.isEmpty ? "model" : modelPart)-provider".prefix(64))
    }

    private func setAPIFormat(_ value: String) {
        guard !gatewayMode else { return }
        apiFormat = value
        if value == "anthropic_messages" { providerKind = "anthropic" }
        else if providerKind == "anthropic" { providerKind = "openai" }
        if !(Self.profiles[value] ?? []).contains(where: { $0.value == protocolProfile }) { protocolProfile = "standard" }
        clientIdentity = "auto"
        resetFetchedModels()
    }

    private func setProviderKind(_ value: String) {
        providerKind = value
        if value == "anthropic" { apiFormat = "anthropic_messages" }
        else if apiFormat == "anthropic_messages" { apiFormat = value == "ollama" ? "chat_completions" : "responses" }
        if !(Self.profiles[apiFormat] ?? []).contains(where: { $0.value == protocolProfile }) { protocolProfile = "standard" }
        resetFetchedModels()
    }

    private func setExecutionBackend(_ value: String) {
        guard value != executionBackend else { return }
        executionBackend = value
        if value == "codex_gateway" {
            if providerKind != "openai" { providerKind = "openai" }
            apiFormat = "responses"
            protocolProfile = "codex_responses"
            webSearchFormat = "responses"
        } else {
            apiFormat = "responses"
            protocolProfile = "standard"
            webSearchFormat = "auto"
        }
        resetFetchedModels()
    }

    private func resetFetchedModels() {
        fetchedModels = []
        enabledModelIDs = []
        fetchedOnce = false
        fetchError = nil
    }

    private func toggleModel(_ id: String, _ enabled: Bool) {
        if enabled {
            enabledModelIDs.insert(id)
            if trimmedDefault.isEmpty { defaultModel = id }
        } else {
            enabledModelIDs.remove(id)
            if trimmedDefault == id { defaultModel = enabledModelIDs.sorted().first ?? "" }
        }
    }

    private func validate() -> String? {
        if trimmedDefault.isEmpty { return "请填写或选择默认模型。" }
        if trimmedDefault.count > 64 { return "默认模型 ID 最多 64 个字符。" }
        if effectiveName.count > 64 { return "名称最多 64 个字符。" }
        if trimmedBaseURL.count > 255 { return "接入地址最多 255 个字符。" }
        if !trimmedBaseURL.isEmpty {
            let scheme = URL(string: trimmedBaseURL)?.scheme?.lowercased() ?? ""
            if scheme != "http" && scheme != "https" { return "接入地址需要以 http:// 或 https:// 开头。" }
        }
        if trimmedKey.count > 512 { return "API Key 最多 512 个字符。" }
        if gatewayMode && trimmedKey.isEmpty { return "Codex 兼容模式必须填写 API Key。" }
        if notes.count > 500 { return "备注最多 500 个字符。" }
        if enabledModelIDs.union([trimmedDefault]).count > 200 { return "一次最多启用 200 个模型，请减少选择。" }
        return nil
    }

    private var connectionBody: [String: JSONValue] {
        var body: [String: JSONValue] = [
            "provider": .string(providerKind),
            "api_format": .string(apiFormat),
            "protocol_profile": .string(protocolProfile),
            "execution_backend": .string(executionBackend),
            "base_url": trimmedBaseURL.isEmpty ? .null : .string(trimmedBaseURL),
            "api_key": trimmedKey.isEmpty ? .null : .string(trimmedKey)
        ]
        if let proxyID { body["proxy_id"] = .integer(Int64(proxyID)) } else { body["proxy_id"] = .null }
        return body
    }

    private func fetchModels() async {
        guard !fetching else { return }
        fetching = true; fetchError = nil
        defer { fetching = false }
        do {
            let response = try await model.request(path: "/api/commands/llm-providers/fetch-models-preview", method: "POST",
                                                   body: .object(connectionBody))
            var items = response["models"]?.arrayValue ?? []
            if items.isEmpty {
                items = (response["ids"]?.arrayValue ?? []).compactMap(\.stringValue).map { JSONValue.object(["id": .string($0)]) }
            }
            fetchedModels = items.filter { $0["id"]?.stringValue?.isEmpty == false }
            let available = Set(fetchedModels.compactMap { $0["id"]?.stringValue })
            enabledModelIDs = enabledModelIDs.intersection(available)
            fetchedOnce = true
        } catch {
            fetchError = error.localizedDescription
        }
    }

    private func loadProxies() async {
        do {
            let value = try await model.request(path: "/api/proxies")
            proxies = value.arrayValue ?? value["items"]?.arrayValue ?? []
            proxiesError = nil
        } catch {
            proxiesError = error.localizedDescription
        }
    }

    private var modelsPayload: [JSONValue] {
        var result: [JSONValue] = []
        var seen: Set<String> = []
        for item in fetchedModels {
            guard let id = item["id"]?.stringValue, enabledModelIDs.contains(id) || id == trimmedDefault, !seen.contains(id) else { continue }
            var object = item.objectValue ?? [:]
            object["enabled"] = true
            object["custom"] = false
            result.append(.object(object))
            seen.insert(id)
        }
        if !seen.contains(trimmedDefault) {
            result.append(.object(["id": .string(trimmedDefault), "enabled": true, "custom": true]))
        }
        return result
    }

    private func save() async {
        guard !saving, !uncertain, created == nil, validate() == nil else { return }
        saving = true; saveError = nil
        defer { saving = false }
        var body = connectionBody
        body["name"] = .string(effectiveName)
        body["default_model"] = .string(trimmedDefault)
        body["client_identity_profile"] = .string(clientIdentity)
        body["web_search_api_format"] = .string(webSearchFormat)
        body["modality"] = .string(modality)
        body["cost_tier"] = .integer(Int64(costTier))
        body["tags"] = .array(Self.allTags.filter(tags.contains).map(JSONValue.string))
        let trimmedNotes = notes.trimmingCharacters(in: .whitespacesAndNewlines)
        body["notes"] = trimmedNotes.isEmpty ? .null : .string(trimmedNotes)
        body["models"] = .array(modelsPayload)
        do {
            let response = try await model.request(path: "/api/commands/llm-providers", method: "POST", body: .object(body))
            workspace.replaceProvider(response)
            created = response
            apiKey = ""
        } catch {
            saveError = error.localizedDescription
            uncertain = (error as? APIError)?.isOutcomeUncertain == true
        }
    }
}

private struct AIProviderTagPicker: View {
    @Binding var tags: Set<String>
    let allTags: [String]

    var body: some View {
        List {
            Section {
                ForEach(allTags, id: \.self) { tag in
                    Toggle(AIPresentation.tag(tag), isOn: Binding(get: { tags.contains(tag) }, set: { enabled in
                        if enabled { tags.insert(tag) } else { tags.remove(tag) }
                    }))
                }
            } footer: {
                Text("只影响开启“自动路由”的 AI 指令：例如翻译类问题会优先分配给标记了“翻译”的供应商。固定模型的指令不读取这些标签。全部不选表示只作为备用供应商。")
            }
        }
        .navigationTitle("擅长的任务").navigationBarTitleDisplayMode(.inline)
    }
}

private struct AIProviderChoice: Identifiable, Hashable {
    let value: String
    let title: String
    let hint: String
    var id: String { value }

    init(_ value: String, _ title: String = "", hint: String = "") {
        self.value = value
        self.title = title
        self.hint = hint
    }

    init(_ value: String, _ title: String, _ hint: String) {
        self.value = value
        self.title = title
        self.hint = hint
    }
}
