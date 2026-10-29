import SwiftUI
import TelePilotCore

struct ManagementCollection: Hashable {
    let title: String
    let path: String
    var kind: Kind = .records
    var itemsKey: String? = nil
    var create: ManagementAction? = nil
    var editMethod = "PATCH"
    var itemPath: String? = nil
    var idField = "id"
    var context: String? = nil
    var ruleSchema: JSONValue? = nil
    var explanation: String? = nil
    enum Kind: String, Hashable { case records, editable, providers, keys, repos, repoPlugins, remotePlugins, rules, features, compensations, jobs, traces, capabilities, rateTemplates, accountCommands, botUsers, deadLetters }
}

struct ManagementCollectionView: View {
    let resource: ManagementCollection
    @Environment(AppModel.self) private var model
    @State private var response: JSONValue?
    @State private var error: String?
    @State private var loading = false
    @State private var search = ""
    @State private var searching = false
    @State private var filters: JSONValue = .object([:])
    @State private var filtering = false
    @State private var exporting = false
    @State private var exportFile = ManagementFile(data: Data())

    private var items: [JSONValue] {
        let rawItems = response.map { ManagementText.items($0, key: resource.itemsKey) } ?? []
        let items = resource.kind == .accountCommands ? rawItems.map { item in
            JSONValue.object((item["template"]?.objectValue ?? [:]).merging(["enabled": item["enabled"] ?? .bool(false)]) { _, new in new })
        } : rawItems
        guard !search.isEmpty else { return items }
        return items.filter { value in
            (value.objectValue.map { Array($0.values) } ?? [value]).contains { item in
                item.stringValue?.localizedCaseInsensitiveContains(search) ?? false
            }
        }
    }
    var body: some View {
        List {
            if let explanation = resource.explanation { Section { Text(explanation).font(.subheadline).foregroundStyle(.secondary) } }
            if let error { Section { NoticeView(title: "读取失败", message: error) } }
            if loading && response == nil { ProgressView("正在读取…") }
            if let response, resource.itemsKey != nil, response[resource.itemsKey!] == nil {
                Section { ManagementValueView(value: response) }
            }
            Section {
                ForEach(Array(items.enumerated()), id: \.offset) { _, item in
                    NavigationLink { ManagementItemView(resource: resource, item: item) } label: { rowLabel(item) }
                }
                if !loading, response != nil, items.isEmpty {
                    ContentUnavailableView("暂无匹配记录", systemImage: "tray", description: Text("可调整搜索或筛选条件后重新读取。"))
                }
            } header: { if response != nil { Text("本页 \(items.count) 条") } }
            if let total = response?["total"]?.intValue { Section { Text("服务器共 \(total) 条记录").foregroundStyle(.secondary) } }
            if response != nil, hasPagination {
                Section("翻页") {
                    HStack {
                        Button("上一页") { movePage(-1) }.disabled(currentPage <= 1)
                        Spacer(); Text("第 \(currentPage) 页").foregroundStyle(.secondary); Spacer()
                        Button("下一页") { movePage(1) }.disabled(items.isEmpty)
                    }.buttonStyle(.borderless)
                }
            }
        }
        .navigationTitle(resource.title).navigationBarTitleDisplayMode(.inline)
        .pageSearch(text: $search, isActive: $searching, prompt: "搜索本页记录", identifier: "collection.search")
        .task { await load() }.refreshable { await load() }
        .toolbar {
            ToolbarItemGroup(placement: .topBarTrailing) {
                if !queryParameters.isEmpty { Button("筛选", systemImage: "line.3.horizontal.decrease") { filtering = true } }
                Button("导出本页记录", systemImage: "square.and.arrow.up") {
                    if let data = try? ManagementText.sanitizedExport(.array(items)).data() {
                        exportFile = ManagementFile(data: data); exporting = true
                    }
                }.disabled(response == nil)
                if let action = resource.create { NavigationLink { ManagementFormView(action: action) } label: { Label("添加", systemImage: "plus") } }
            }
        }
        .sheet(isPresented: $filtering) {
            NavigationStack {
                Form { ManagementFieldsView(schema: filterSchema, value: $filters) }
                    .navigationTitle("筛选" + resource.title).navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .cancellationAction) { Button("清空") { filters = .object([:]) } }
                        ToolbarItem(placement: .confirmationAction) { Button("应用") { filtering = false; Task { await load() } } }
                    }
            }
        }
        .fileExporter(isPresented: $exporting, document: exportFile, contentType: .json, defaultFilename: "TelePilot-\(resource.title)") { result in
            if case .failure(let failure) = result { error = failure.localizedDescription }
        }
    }
    /// 规则模式插件（契约 2）的规则按 `x-rule-summary` 显示摘要；其余沿用通用记录行。
    @ViewBuilder private func rowLabel(_ item: JSONValue) -> some View {
        if resource.kind == .rules, let schema = resource.ruleSchema,
           schema["properties"]?["config"]?["x-telepilot-kind"]?.stringValue == ManagementRuleSchemas.pluginRuleKind {
            PluginRuleRow(item: item, requestSchema: schema)
        } else {
            ManagementRecordRow(value: item)
        }
    }
    private var queryParameters: [JSONValue] {
        model.catalog.operation(path: resource.path, method: "GET")?.parameters.filter { $0["in"]?.stringValue == "query" } ?? []
    }
    private var filterSchema: JSONValue {
        .object(["type": .string("object"), "properties": .object(Dictionary(uniqueKeysWithValues: queryParameters.compactMap { field in
            guard let name = field["name"]?.stringValue, let schema = field["schema"] else { return nil }
            return (name, schema)
        }))])
    }
    private var hasPagination: Bool { queryParameters.contains { ["page", "offset"].contains($0["name"]?.stringValue ?? "") } }
    private var currentPage: Int {
        if queryParameters.contains(where: { $0["name"]?.stringValue == "offset" }) { return ((filters["offset"]?.intValue ?? 0) / pageSize) + 1 }
        return filters["page"]?.intValue ?? 1
    }
    private var pageSize: Int {
        max(1, filters["limit"]?.intValue ?? filters["page_size"]?.intValue
            ?? queryParameters.first(where: { ["limit", "page_size"].contains($0["name"]?.stringValue ?? "") })?["schema"]?["default"]?.intValue ?? 50)
    }
    private func movePage(_ delta: Int) {
        var object = filters.objectValue ?? [:]
        if queryParameters.contains(where: { $0["name"]?.stringValue == "offset" }) {
            object["offset"] = .integer(Int64(max(0, (filters["offset"]?.intValue ?? 0) + delta * pageSize)))
        } else { object["page"] = .integer(Int64(max(1, currentPage + delta))) }
        filters = .object(object); Task { await load() }
    }
    private func load() async {
        guard !loading else { return }
        loading = true; defer { loading = false }
        do {
            var components = URLComponents(string: resource.path)!
            let query = (filters.objectValue ?? [:]).sorted { $0.key < $1.key }.flatMap { name, value -> [URLQueryItem] in
                if let array = value.arrayValue { return array.map { URLQueryItem(name: name, value: ManagementText.rawScalar($0)) } }
                let raw: String
                if let bool = value.boolValue { raw = bool ? "true" : "false" } else { raw = ManagementText.rawScalar(value) }
                return value.isNull ? [] : [URLQueryItem(name: name, value: raw)]
            }
            if !query.isEmpty { components.queryItems = (components.queryItems ?? []) + query }
            response = try await model.request(path: components.string ?? resource.path); error = nil
        } catch { self.error = error.localizedDescription }
    }
}

struct ManagementItemView: View {
    let resource: ManagementCollection
    let item: JSONValue
    @Environment(AppModel.self) private var model
    private var identifier: String? { item[resource.idField].map(ManagementText.rawScalar) }
    private var itemPath: String? {
        guard let identifier, let template = resource.itemPath else { return nil }
        return template.replacingOccurrences(of: "{id}", with: ManagementText.pathSegment(identifier))
    }
    var body: some View {
        List {
            Section { ManagementValueView(value: item) }
            details
            if let identifier {
                Section {
                    if resource.path == "/api/proxies" {
                        ManagementActionLink(action: .init(title: "测试代理连通性", path: "/api/proxies/\(identifier)/test"))
                        NavigationLink("代理使用情况") { ManagementDocumentView(title: "代理使用情况", path: "/api/proxies/\(identifier)/usage") }
                    }
                    if resource.path == "/api/notify-bots" {
                        ManagementActionLink(action: .init(title: "发送测试通知", path: "/api/notify-bots/\(identifier)/test", explanation: "向所配置的通知接收方发送真实测试消息。"))
                    }
                    if resource.path == "/api/device-profiles" {
                        ManagementActionLink(action: .init(title: "设为默认设备配置", path: "/api/device-profiles/\(identifier)/default"))
                    }
                }
            }
            if let itemPath {
                Section("管理") {
                    if model.catalog.operation(path: itemPath, method: resource.editMethod) != nil {
                        ManagementActionLink(action: .init(title: "编辑", path: itemPath, method: resource.editMethod, schema: resource.ruleSchema, seed: item))
                    }
                    if resource.kind != .accountCommands, model.catalog.operation(path: itemPath, method: "DELETE") != nil {
                        ManagementActionLink(action: .init(title: "删除", path: itemPath, method: "DELETE", destructive: true,
                            explanation: "将删除这条服务器记录。请确认关联任务与配置已经处理。"))
                    }
                }
            }
        }.navigationTitle(ManagementText.title(item)).navigationBarTitleDisplayMode(.inline)
    }
    @ViewBuilder private var details: some View {
        if let identifier {
            switch resource.kind {
            case .providers:
                providerLinks(identifier)
            case .repos:
                repoLinks(identifier)
            case .repoPlugins:
                if let repo = resource.context {
                    Section("安装") {
                        ManagementActionLink(action: .init(title: "安装此插件", path: "/api/plugin-repos/\(repo)/plugins/\(ManagementText.pathSegment(identifier))/install",
                                                           explanation: "修改会提交到当前连接的服务器。打开「安装后默认启用」会全局启用插件并为所有账号开启；关闭则安装后保持全局停用，需要到插件详情的服务器总开关中启用。"))
                    }
                }
            case .remotePlugins:
                Section("插件操作") {
                    ForEach([("启用", "enable"), ("停用", "disable"), ("更新", "update"), ("启用全部账号", "enable-accounts"), ("停用全部账号", "disable-accounts")], id: \.1) { title, operation in
                        ManagementActionLink(action: .init(title: title, path: "/api/remote-plugins/\(ManagementText.pathSegment(identifier))/\(operation)", destructive: operation.contains("disable")))
                    }
                }
            case .rules:
                if let itemPath {
                    let dryRun = PluginRuleForm.dryRunSpec(resource.ruleSchema?["x-dry-run"])
                    Section("规则验证") {
                        if dryRun.enabled {
                            NavigationLink { PluginRuleDryRunView(path: itemPath + "/dry-run", spec: dryRun) } label: {
                                Label(dryRun.title, systemImage: "play.rectangle")
                            }
                            .accessibilityIdentifier("plugin.rule.dryRun")
                        }
                        ManagementActionLink(action: .init(title: "立即执行", path: itemPath + "/execute", explanation: "此操作会实际发送或执行规则，请确认目标账号与会话。"))
                    }
                }
            case .compensations:
                Section("支付") {
                    ManagementActionLink(action: .init(title: "标记人工已支付", path: "/api/ledger/compensations/\(identifier)/manual-paid", explanation: "仅在你已经通过其他方式实际完成支付后确认，避免重复付款。"))
                }
            case .jobs:
                NavigationLink("刷新任务进度") {
                    ManagementJobView(id: identifier)
                }
            case .traces:
                NavigationLink("查看完整调用链") { ManagementDocumentView(title: "调用链", path: "/api/logs/trace/events/\(ManagementText.pathSegment(identifier))") }
            case .capabilities:
                Section("能力开关") {
                    ManagementActionLink(action: .init(title: "修改平台能力", path: "/api/system/capabilities/\(ManagementText.pathSegment(identifier))", method: "PATCH", seed: item))
                }
            case .rateTemplates:
                NavigationLink("动作限流规则") {
                    ManagementCollectionView(resource: .init(title: "动作限流规则", path: "/api/rate-templates/\(identifier)/rules", kind: .editable, itemPath: "/api/rate-templates/\(identifier)/rules/{id}", idField: "action"))
                }
            case .accountCommands:
                if let itemPath {
                    Section("账号指令") {
                        ManagementActionLink(action: .init(title: "启用指令", path: itemPath))
                        ManagementActionLink(action: .init(title: "停用指令", path: itemPath, method: "DELETE", destructive: true))
                    }
                }
            case .deadLetters:
                if let base = resource.context, let loop = item["loop"]?.stringValue {
                    let path = base + "/\(ManagementText.pathSegment(loop))/\(identifier)"
                    Section("失败恢复") {
                        ManagementActionLink(action: .init(title: "重新投递", path: path + "/replay"))
                        ManagementActionLink(action: .init(title: "丢弃记录", path: path, method: "DELETE", destructive: true))
                    }
                }
            default: EmptyView()
            }
        }
    }
    @ViewBuilder private func providerLinks(_ id: String) -> some View {
        let base = "/api/commands/llm-providers/\(id)"
        Section("模型与密钥") {
            NavigationLink("密钥池") {
                ManagementCollectionView(resource: .init(title: "密钥池", path: base + "/keys", kind: .keys, itemsKey: "keys", create: .init(title: "添加密钥", path: base + "/keys"), itemPath: base + "/keys/{id}"))
            }
            ManagementActionLink(action: .init(title: "从供应商同步模型", path: base + "/fetch-models"), symbol: "arrow.triangle.2.circlepath")
            NavigationLink { AIModelQuickTestView(provider: item) } label: { Label("单个模型快速测试", systemImage: "bolt.horizontal") }
            NavigationLink { AIModelChatTestView(provider: item) } label: { Label("多模型对话测试", systemImage: "bubble.left.and.bubble.right") }
        }
    }
    @ViewBuilder private func repoLinks(_ id: String) -> some View {
        let base = "/api/plugin-repos/\(id)"
        Section("仓库") {
            NavigationLink("仓库插件") {
                ManagementCollectionView(resource: .init(title: "仓库插件", path: base + "/plugins", kind: .repoPlugins, idField: "name", context: id))
            }
            ManagementActionLink(action: .init(title: "刷新仓库", path: base + "/refresh"))
            ManagementActionLink(action: .init(title: "更新已安装插件", path: base + "/update-installed"))
            ManagementActionLink(action: .init(title: "修改仓库凭据", path: base + "/credential", method: "PUT"))
        }
    }
}
