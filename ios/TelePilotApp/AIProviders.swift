import SwiftUI
import TelePilotCore

struct AIProviderListSection: View {
    @Bindable var workspace: AIWorkspaceStore
    var showSearch = false
    @State private var search = ""

    private var providers: [JSONValue] {
        guard !search.isEmpty else { return workspace.providers }
        return workspace.providers.filter { provider in
            [provider["name"]?.stringValue, provider["default_model"]?.stringValue, provider["provider"]?.stringValue]
                .contains { $0?.localizedCaseInsensitiveContains(search) == true }
        }
    }

    var body: some View {
        if showSearch, !workspace.providers.isEmpty {
            Section {
                TextField("搜索供应商、类型或模型", text: $search)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
            }
        }
        Section {
            ForEach(providers, id: \.self) { provider in
                NavigationLink { AIProviderDetailView(provider: provider, workspace: workspace) } label: {
                    AIProviderRow(provider: provider, workspace: workspace)
                }
                .accessibilityIdentifier("ai.provider.\(provider["id"]?.intValue ?? 0)")
            }
            NavigationLink {
                ManagementFormView(action: .init(title: "添加供应商", path: "/api/commands/llm-providers",
                    explanation: "填写供应商连接与默认模型。密钥只在提交时发送，服务器不会在列表中返回明文。")) { value in
                    workspace.replaceProvider(value)
                }
            } label: { Label("添加供应商", systemImage: "plus") }
            .accessibilityIdentifier("ai.provider.add")
            if workspace.loaded, workspace.providers.isEmpty {
                Text("先添加一个模型供应商，再获取或添加模型。Ollama 本地服务可以不配置 API Key。")
                    .font(.subheadline).foregroundStyle(.secondary)
            } else if workspace.loaded, providers.isEmpty {
                Text("没有匹配的供应商").foregroundStyle(.secondary)
            }
        } header: {
            if workspace.loaded { Text("\(providers.count) 个供应商") }
        } footer: {
            Text("可调用以主密钥或启用的密钥池为准。模型是否被下游使用，由模型清单中的启用状态决定。")
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
                    AIStatusLabel(title: "凭据就绪", symbol: "checkmark.circle.fill", color: .green)
                } else {
                    AIStatusLabel(title: "缺少有效密钥", symbol: "key.slash", color: .orange)
                }
            }
            Text(provider["default_model"]?.stringValue ?? "未设置默认模型")
                .font(.subheadline).foregroundStyle(.secondary).lineLimit(2)
            HStack(spacing: 12) {
                Text(provider["provider"]?.stringValue?.capitalized ?? "未知类型")
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
                LabeledContent("类型", value: provider["provider"]?.stringValue?.capitalized ?? "未知")
                LabeledContent("默认模型", value: provider["default_model"]?.stringValue ?? "未设置")
                LabeledContent("接口协议", value: provider["api_format"]?.stringValue ?? "chat_completions")
                LabeledContent("调用凭据") {
                    Text(AIPresentation.ready(provider) ? "已就绪" : "未配置有效密钥")
                        .foregroundStyle(AIPresentation.ready(provider) ? .green : .orange)
                }
                if provider["provider"]?.stringValue == "ollama" {
                    Text("Ollama 本地服务不需要 API Key，可调用状态取决于服务与模型。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            Section("模型") {
                NavigationLink {
                    AIProviderModelsView(provider: provider, workspace: workspace)
                } label: {
                    LabeledContent("模型清单") {
                        Text("\(AIPresentation.enabledModels(provider).count) 启用 / \(provider["models"]?.arrayValue?.count ?? 0) 全部")
                    }
                }
                .accessibilityIdentifier("ai.provider.models")
                NavigationLink {
                    ManagementFormView(action: .init(title: "发现模型", path: base + "/fetch-models",
                        explanation: "从当前供应商读取模型列表并合并到服务器，保留已有模型的启用状态。Anthropic 可能不支持此操作。")) { value in
                        if let updated = value["provider"] { provider = updated; workspace.replaceProvider(updated) }
                    }
                } label: { Label("从供应商发现模型", systemImage: "arrow.triangle.2.circlepath") }
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
                    NavigationLink("验证后查看主密钥") { AIProviderSecretView(path: base + "/api-key") }
                } else {
                    Text("主密钥未配置；可添加池内密钥，或在编辑供应商时替换主密钥。")
                        .font(.caption).foregroundStyle(.secondary)
                }
            }

            Section("连通性") {
                NavigationLink { AILivenessView(workspace: workspace, providerID: provider["id"]?.intValue) } label: {
                    Label("测活此供应商", systemImage: "waveform.path.ecg")
                }
                ManagementActionLink(action: .init(title: "单模型连通性测试", path: base + "/test-model",
                    explanation: "服务器将使用最小输出向上游发送一次真实请求，可能产生用量。"))
                ManagementActionLink(action: .init(title: "真实对话批量测试", path: base + "/chat-test-models",
                    explanation: "用自定义测试语和上下文检查多个模型，可能产生实际上游用量。"))
            }

            Section("高级配置") {
                NavigationLink("编辑连接、路由与输出") {
                    ManagementFormView(action: .init(title: "编辑供应商", path: base, method: "PATCH",
                        explanation: "仅提交选择编辑的字段。已有密钥与敏感请求头不会回填；留空表示保持不变。", seed: provider)) { value in
                        provider = value; workspace.replaceProvider(value)
                    }
                }
                .accessibilityIdentifier("ai.provider.edit")
                NavigationLink("服务地址与传输详情") { AIProviderTechnicalView(provider: provider) }
            }

            Section {
                NavigationLink("删除供应商") {
                    ManagementFormView(action: .init(title: "删除供应商", path: base, method: "DELETE", destructive: true,
                        explanation: "将删除此供应商及其密钥池配置。关联的 AI 指令可能无法继续调用，请先确认路由和备用模型。")) { _ in
                        Task { await workspace.refresh(model) }
                        dismiss()
                    }
                }
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
                LabeledContent("执行方式", value: provider["execution_backend"]?.stringValue ?? "direct")
                LabeledContent("协议配置", value: provider["protocol_profile"]?.stringValue ?? "standard")
                LabeledContent("客户端身份", value: provider["client_identity_profile"]?.stringValue ?? "auto")
                LabeledContent("出口代理", value: provider["proxy_id"]?.intValue.map { "代理 #\($0)" } ?? "直连")
            }
            Section("路由元数据") {
                LabeledContent("内容模态", value: provider["modality"]?.stringValue ?? "text")
                LabeledContent("成本等级", value: String(provider["cost_tier"]?.intValue ?? 2))
                LabeledContent("标签", value: (provider["tags"]?.arrayValue ?? []).compactMap(\.stringValue).joined(separator: "、"))
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
    let provider: JSONValue
    @Bindable var workspace: AIWorkspaceStore
    @Environment(AppModel.self) private var model
    @State private var models: [JSONValue] = []
    @State private var newModel = ""
    @State private var busy = false
    @State private var confirming = false
    @State private var error: String?
    @State private var saved = false
    @State private var uncertain = false

    private var path: String { "/api/commands/llm-providers/\(provider["id"]?.intValue ?? 0)" }
    private var dirty: Bool { models != (provider["models"]?.arrayValue ?? []) }

    var body: some View {
        List {
            Section {
                Text("只有启用的模型会出现在下游模型选择与批量测活范围中。修改模型能力元数据请使用编辑供应商，避免覆盖上游返回的信息。")
                    .font(.subheadline).foregroundStyle(.secondary)
            }
            Section("候选模型") {
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
                        NavigationLink("查看模型元数据") { List { ManagementValueView(value: item) }.navigationTitle(item["id"]?.stringValue ?? "模型详情") }
                            .font(.caption)
                    }
                    .accessibilityIdentifier("ai.model.\(item["id"]?.stringValue ?? "unknown")")
                }
                if models.isEmpty { Text("还没有模型。请从供应商发现模型，或手动添加。").foregroundStyle(.secondary) }
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
                if busy { ProgressView("正在保存到服务器…") }
                if uncertain { Text("请返回并重新读取供应商，确认服务器实际状态后再保存。").font(.caption) }
            } footer: { Text("服务器将整体替换模型清单，现有模型 ID、能力、推理与上下文元数据会原样保留。") }
        }
        .navigationTitle("模型清单").navigationBarTitleDisplayMode(.inline)
        .task { models = provider["models"]?.arrayValue ?? [] }
        .confirmationDialog("保存模型选择？", isPresented: $confirming, titleVisibility: .visible) {
            Button("确认保存") { Task { await save() } }
            Button("取消", role: .cancel) {}
        } message: { Text("将启用 \(models.filter { $0["enabled"]?.boolValue == true }.count) 个模型，停用其余候选模型。") }
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
            workspace.replaceProvider(response)
            models = response["models"]?.arrayValue ?? models
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
