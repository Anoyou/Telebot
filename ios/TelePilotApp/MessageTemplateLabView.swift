import SwiftUI
import TelePilotCore

/// 消息模板实验室（对齐 Web `/plugins/message-template-lab`）：按账号读取模板目录，
/// 本机实时预览（Telegram 气泡、字数、格式检查、示例值），再由服务器 render 校验，最后可测试发送。
struct MessageTemplateLabView: View {
    @Environment(AppModel.self) private var model
    @State private var accountID: Int?
    @State private var groups: [MessageTemplateLabGroup] = []
    @State private var loading = false
    @State private var error: String?

    var body: some View {
        List {
            Section {
                Picker(selection: $accountID) {
                    ForEach(model.accounts) { account in
                        Text(AccountPresentation.name(account)).tag(Optional(account.id))
                    }
                } label: {
                    Label("账号", systemImage: "person.crop.circle.badge.checkmark")
                }
                .pickerStyle(.menu)
                .accessibilityIdentifier("templateLab.account")
            } footer: {
                Text("模板目录按账号读取（插件模板随账号启用的插件变化）。预览只在本机渲染，不会发送；测试发送只发给当前账号已授权且私聊过 Bot 的用户。")
            }
            if let error {
                Section {
                    NoticeView(title: "模板目录读取失败", message: error)
                    Button("重新读取", systemImage: "arrow.clockwise") { Task { await load() } }
                }
            } else if loading && groups.isEmpty {
                Section { ProgressView("正在读取模板目录…") }
            } else if groups.isEmpty {
                Section { Label("这个账号没有可预览的消息模板", systemImage: "tray").foregroundStyle(.secondary) }
            }
            ForEach(groups) { group in
                Section(group.title) {
                    ForEach(group.items) { item in
                        NavigationLink {
                            MessageTemplateLabDetailView(item: item, accountID: accountID ?? 0)
                        } label: {
                            Label {
                                VStack(alignment: .leading, spacing: 2) {
                                    Text(item.title)
                                    Text(item.subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(2)
                                }
                            } icon: { Image(systemName: "text.bubble") }
                        }
                        .accessibilityIdentifier("templateLab.item.\(item.id)")
                    }
                }
            }
        }
        .navigationTitle("消息模板实验室").navigationBarTitleDisplayMode(.inline)
        .refreshable { await load() }
        .task {
            if accountID == nil { accountID = model.accounts.first?.id }
            if groups.isEmpty { await load() }
        }
        .onChange(of: accountID) { _, _ in Task { await load() } }
    }

    private func load() async {
        guard let accountID else { return }
        loading = true
        defer { loading = false }
        do {
            let value = try await model.request(path: "/api/message-templates/catalog?account_id=\(accountID)")
            groups = MessageTemplateLabGroup.parse(value)
            error = nil
        } catch {
            self.error = error.localizedDescription
        }
    }
}

struct MessageTemplateLabItem: Identifiable, Hashable {
    let id: String
    let title: String
    let description: String
    let featureKey: String?
    let fieldKey: String
    let template: String
    let sampleData: [String: JSONValue]
    let parseMode: String?
    let buttons: [[TelegramPreviewButton]]

    var subtitle: String {
        let source = featureKey.map { "\($0) · \(fieldKey)" } ?? fieldKey
        return description.isEmpty ? source : "\(description) · \(source)"
    }
    var mode: TelegramParseMode { TelegramParseMode(serverValue: parseMode) }
    var samples: [String: String] { sampleData.mapValues { TelegramTemplate.format($0) } }
}

struct MessageTemplateLabGroup: Identifiable {
    let id: String
    let title: String
    let items: [MessageTemplateLabItem]

    static func parse(_ value: JSONValue) -> [MessageTemplateLabGroup] {
        func item(_ raw: JSONValue, group: String) -> MessageTemplateLabItem? {
            guard let id = raw["id"]?.stringValue ?? raw["field_key"]?.stringValue else { return nil }
            return MessageTemplateLabItem(
                id: id,
                title: raw["title"]?.stringValue ?? id,
                description: raw["description"]?.stringValue ?? "",
                featureKey: raw["feature_key"]?.stringValue,
                fieldKey: raw["field_key"]?.stringValue ?? id,
                template: raw["template"]?.stringValue ?? "",
                sampleData: raw["sample_data"]?.objectValue ?? [:],
                parseMode: raw["parse_mode"]?.stringValue,
                buttons: TelegramPreviewButton.rows(from: raw["reply_markup"] ?? raw["buttons"])
            )
        }
        if let groups = value["groups"]?.arrayValue, !groups.isEmpty {
            return groups.enumerated().compactMap { pair -> MessageTemplateLabGroup? in
                let (index, group) = (pair.offset, pair.element)
                let key = group["group"]?.stringValue ?? "group-\(index)"
                let items = (group["items"]?.arrayValue ?? []).compactMap { item($0, group: key) }
                return items.isEmpty ? nil : MessageTemplateLabGroup(id: key, title: group["title"]?.stringValue ?? key, items: items)
            }
        }
        let items = (value["items"]?.arrayValue ?? []).compactMap { item($0, group: "") }
        return items.isEmpty ? [] : [MessageTemplateLabGroup(id: "all", title: "全部模板", items: items)]
    }
}

/// 单个模板：本机实时预览 + 服务器渲染校验 + 测试发送。
struct MessageTemplateLabDetailView: View {
    let item: MessageTemplateLabItem
    let accountID: Int
    @Environment(AppModel.self) private var model
    @State private var draft = ""
    @State private var initialized = false
    @State private var rendering = false
    @State private var renderResult: JSONValue?
    @State private var renderedFrom: String?
    @State private var renderError: String?
    @State private var targetChat = ""
    @State private var sending = false
    @State private var confirmingSend = false
    @State private var sendResult: String?
    @State private var sendError: String?
    @State private var sendUncertain = false

    private var spec: TelegramTemplateSpec {
        TelegramTemplateSpec(botName: item.title, samples: item.samples, mode: item.mode, prompt: nil, buttons: item.buttons)
    }

    var body: some View {
        TelegramTemplateEditorPage(title: item.title, text: $draft, spec: spec, identifier: "templateLab",
                                   footer: item.description.isEmpty ? nil : item.description) { _, values in
            serverSections(values: values)
        }
        .toolbar {
            ToolbarItem(placement: .topBarTrailing) {
                Button("恢复原模板", systemImage: "arrow.uturn.backward") { draft = item.template }
                    .disabled(draft == item.template)
                    .accessibilityIdentifier("templateLab.reset")
            }
        }
        .task {
            guard !initialized else { return }
            draft = item.template
            initialized = true
        }
    }

    @ViewBuilder private func serverSections(values: [String: String]) -> some View {
        let stale = renderedFrom != nil && renderedFrom != draft
        Section {
            Button {
                Task { await render(values: values) }
            } label: {
                HStack(spacing: 8) {
                    if rendering { ProgressView() }
                    Label("由服务器渲染并校验", systemImage: "checkmark.shield")
                }
            }
            .disabled(rendering || draft.isEmpty)
            .accessibilityIdentifier("templateLab.render")
            if let renderError { NoticeView(title: "渲染失败", message: renderError) }
            if let result = renderResult {
                let validation = result["validation"]
                if validation?["ok"]?.boolValue == true {
                    Label(stale ? "服务器校验通过（模板已修改，请重新渲染）" : "服务器校验通过", systemImage: "checkmark.circle.fill")
                        .foregroundStyle(stale ? Color.orange : Color.green)
                        .accessibilityIdentifier("templateLab.render.ok")
                }
                ForEach(Array((validation?["errors"]?.arrayValue ?? []).compactMap(\.stringValue).enumerated()), id: \.offset) { _, message in
                    Label(message, systemImage: "xmark.octagon.fill").foregroundStyle(.red).font(.footnote)
                }
                ForEach(Array((validation?["warnings"]?.arrayValue ?? []).compactMap(\.stringValue).enumerated()), id: \.offset) { _, message in
                    Label(message, systemImage: "exclamationmark.triangle.fill").foregroundStyle(.orange).font(.footnote)
                }
                let text = result["text"]?.stringValue ?? ""
                let mode = TelegramParseMode(serverValue: result["parse_mode"]?.stringValue ?? item.parseMode)
                TelegramChatPreview(model: TelegramMessagePreviewModel(template: text, values: [:], mode: mode),
                                    botName: item.title, buttons: item.buttons, identifier: "templateLab.server")
                    .listRowInsets(EdgeInsets(top: 8, leading: 8, bottom: 8, trailing: 8))
                LabeledContent("Telegram 实体", value: "\((result["entities"]?.arrayValue ?? []).count) 个")
            }
        } header: { Text("服务器渲染") } footer: {
            Text("服务器用与实际发送相同的代码渲染模板（POST /api/message-templates/render），只渲染、不发送。")
        }
        Section {
            TextField("目标用户 ID（私聊过 Bot 的授权用户）", text: $targetChat)
                .keyboardType(.numbersAndPunctuation)
                .accessibilityIdentifier("templateLab.target")
            Button("测试发送", systemImage: "paperplane") { confirmingSend = true }
                .disabled(!canSend(stale: stale))
                .accessibilityIdentifier("templateLab.send")
                .confirmationDialog("发送测试消息？", isPresented: $confirmingSend, titleVisibility: .visible) {
                    Button("确认发送") { Task { await send() } }
                    Button("取消", role: .cancel) {}
                } message: { Text("将通过当前账号向 \(targetChat) 发送一条真实消息（服务器渲染后的内容）。") }
            if sending { ProgressView("正在发送…") }
            if let sendResult { Label(sendResult, systemImage: "checkmark.circle.fill").foregroundStyle(.green).accessibilityIdentifier("templateLab.sent") }
            if let sendError { NoticeView(title: sendUncertain ? "发送结果待确认" : "发送失败", message: sendError) }
        } header: { Text("测试发送") } footer: {
            Text(sendUncertain ? "网络中断，消息可能已发出。请先到 Telegram 里确认，避免重复发送。" : "需要先由服务器渲染且校验通过；发送不会自动重试。")
        }
    }

    private func canSend(stale: Bool) -> Bool {
        guard let result = renderResult, result["validation"]?["ok"]?.boolValue == true, !stale, !sending, !sendUncertain else { return false }
        return Int64(targetChat.trimmingCharacters(in: .whitespaces)) != nil
    }

    private func render(values: [String: String]) async {
        rendering = true
        renderError = nil
        defer { rendering = false }
        var sample = item.sampleData
        for (key, value) in values where TelegramTemplate.format(sample[key]) != value { sample[key] = .string(value) }
        let source = draft
        do {
            let response = try await model.request(path: "/api/message-templates/render", method: "POST", body: .object([
                "template": .string(source), "sample_data": .object(sample), "parse_mode": item.parseMode.map(JSONValue.string) ?? .null,
            ]))
            renderResult = response
            renderedFrom = source
        } catch {
            renderError = error.localizedDescription
        }
    }

    private func send() async {
        guard let result = renderResult, let target = Int64(targetChat.trimmingCharacters(in: .whitespaces)) else { return }
        sending = true
        sendError = nil
        sendResult = nil
        defer { sending = false }
        do {
            let response = try await model.request(path: "/api/message-templates/test-send", method: "POST", body: .object([
                "account_id": .integer(Int64(accountID)), "target_chat_id": .integer(target),
                "text": result["text"] ?? .string(""), "parse_mode": result["parse_mode"] ?? .null,
            ]))
            sendResult = response["message"]?.stringValue ?? "已发送"
        } catch {
            sendError = error.localizedDescription
            sendUncertain = (error as? APIError)?.isOutcomeUncertain == true
        }
    }
}
