import PhotosUI
import SwiftUI
import TelePilotCore
import UniformTypeIdentifiers

private struct AssistantAttachment: Identifiable {
    let id = UUID()
    let image: UIImage
    let value: JSONValue
}

struct AssistantView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.scenePhase) private var scenePhase
    @State private var store = AssistantStore()
    @State private var draft = ""
    @State private var sendMode = "message"
    @State private var photos: [PhotosPickerItem] = []
    @State private var attachments: [AssistantAttachment] = []
    @State private var importingImage = false
    @State private var loadingImage = false
    @State private var panel: AssistantPanel?
    @State private var confirmStop = false
    @State private var confirmReplace = false
    @State private var abandonPending = false
    @State private var editingPair: AssistantEditPair?
    private let initialID: String?
    private let embedded: Bool

    init(sessionID: String? = nil, embedded: Bool = false) {
        initialID = sessionID
        self.embedded = embedded
    }

    private enum AssistantPanel: String, Identifiable {
        case sessions, settings, tasks, memory, inbox, trace
        var id: String { rawValue }
    }

    var body: some View {
        HStack(spacing: 0) {
            if sizeClass == .regular {
                AssistantSessionsView(store: store) { id in
                    Task { await store.select(id, model: model) }
                }
                .frame(minWidth: 240, idealWidth: 280, maxWidth: 320)
                Divider()
            }
            NavigationStack {
                conversation
                    .navigationTitle(AssistantRedaction.text(store.session?.assistantText("title").nilIfEmpty ?? "助手"))
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarLeading) {
                            if sizeClass != .regular {
                                Button("会话", systemImage: "sidebar.left") { panel = .sessions }
                            }
                        }
                        ToolbarItemGroup(placement: .topBarTrailing) {
                            Menu {
                                Button("新建会话", systemImage: "square.and.pencil") { Task { await store.createSession(model) } }
                                Button("会话设置", systemImage: "slider.horizontal.3") { panel = .settings }.disabled(store.sessionID == nil)
                                Button("任务与队列", systemImage: "list.bullet.rectangle") { panel = .tasks }
                                Button("待确认操作", systemImage: "checkmark.shield") { panel = .inbox }
                                Button("长期记忆", systemImage: "brain") { panel = .memory }
                                Button("运行轨迹", systemImage: "point.3.connected.trianglepath.dotted") { panel = .trace }.disabled(store.runID == nil)
                            } label: { Image(systemName: "ellipsis.circle") }
                            .accessibilityLabel("助手菜单")
                            if !embedded {
                                Button("关闭", systemImage: "xmark") { dismiss() }
                            }
                        }
                    }
            }
        }
        .task { await store.start(model, initialID: initialID) }
        .onDisappear { store.pauseSubscription() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await store.restoreRun(model); if store.unresolvedSend { await store.reconcileSend(model) } } }
            else if phase == .background { store.pauseSubscription() }
        }
        .sheet(item: $panel) { selected in
            switch selected {
            case .sessions:
                AssistantSessionsView(store: store) { id in
                    panel = nil
                    Task { await store.select(id, model: model) }
                }
            case .settings: AssistantSessionSettingsView(store: store)
            case .tasks: AssistantTasksView(sessionID: store.sessionID)
            case .memory: AssistantMemoryView()
            case .inbox: AssistantInboxView()
            case .trace:
                if let runID = store.runID { AssistantTraceView(runID: runID) }
            }
        }
        .sheet(item: $editingPair) { pair in
            AssistantEditMessageView(original: pair.user["content"]?.assistantText("text") ?? "") { text in
                Task { await store.regenerate(user: pair.user, assistant: pair.assistant, edited: text, model: model) }
            }
        }
        .confirmationDialog("停止当前任务？", isPresented: $confirmStop, titleVisibility: .visible) {
            Button("提交停止请求", role: .destructive) { Task { await store.cancel(model) } }
        } message: { Text("已执行的操作不会撤销。关闭窗口不会停止服务器任务。") }
        .confirmationDialog("停止当前任务并发送替代消息？", isPresented: $confirmReplace, titleVisibility: .visible) {
            Button("停止并替换", role: .destructive) { performSend() }
        } message: { Text("服务器会停止当前任务，并使用输入框中的内容创建替代任务。已经执行的操作不会撤销。") }
        .confirmationDialog("放弃待核对的发送记录？", isPresented: $abandonPending, titleVisibility: .visible) {
            Button("放弃本机记录", role: .destructive) { store.clearPending() }
        } message: { Text("这不会停止服务器任务。请先检查任务历史，重新发送可能产生重复任务。") }
        .onChange(of: photos) { _, items in
            Task {
                loadingImage = true
                defer { loadingImage = false; photos = [] }
                for item in items.prefix(4 - attachments.count) {
                    do {
                        guard let data = try await item.loadTransferable(type: Data.self) else { continue }
                        try addImage(data)
                    } catch { store.error = "无法读取图片，请选择其他图片。" }
                }
            }
        }
        .fileImporter(isPresented: $importingImage, allowedContentTypes: [.image], allowsMultipleSelection: true) { result in
            do {
                for url in try result.get().prefix(4 - attachments.count) {
                    let access = url.startAccessingSecurityScopedResource()
                    defer { if access { url.stopAccessingSecurityScopedResource() } }
                    guard ((try url.resourceValues(forKeys: [.fileSizeKey])).fileSize ?? 0) <= 25_000_000 else {
                        store.error = "单张原始图片不能超过 25 MB。"; continue
                    }
                    try addImage(Data(contentsOf: url))
                }
            } catch { store.error = "无法读取图片文件，请重新选择。" }
        }
        .accessibilityIdentifier("assistant.screen")
    }

    private var conversation: some View {
        VStack(spacing: 0) {
            if store.capabilities["enabled"]?.boolValue == false || store.capabilities["ai_enabled"]?.boolValue == false {
                AssistantNotice(text: "服务器尚未启用系统助手或 AI，请先在系统设置中启用并配置模型。")
            }
            if store.session?.assistantText("channel") == "bot" {
                AssistantNotice(text: "这是 Telegram 会话，可以查看历史和任务；请在原 Telegram 对话中继续发送。")
            }
            if let error = store.error { AssistantNotice(text: error, isError: true) { store.error = nil } }
            if let notice = store.notice { AssistantNotice(text: notice) { store.notice = nil } }
            if store.unresolvedSend {
                VStack(alignment: .leading, spacing: 8) {
                    Label("有一条发送尚未核实", systemImage: "exclamationmark.arrow.triangle.2.circlepath").font(.subheadline.weight(.medium))
                    Text("网络中断时不会自动重发消息。请先核对服务器任务。").font(.caption)
                    HStack {
                        Button("核对发送结果") { Task { await store.reconcileSend(model) } }
                        Button("放弃记录", role: .destructive) { abandonPending = true }
                    }.font(.subheadline)
                }.padding().frame(maxWidth: .infinity, alignment: .leading).background(.orange.opacity(0.1))
            }
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 24) {
                        if store.hasOlderMessages && !store.messages.isEmpty {
                            Button("读取更早的消息") { Task { await store.refreshMessages(model, older: true) } }
                                .frame(maxWidth: .infinity)
                        }
                        if store.messages.isEmpty && store.liveText.isEmpty {
                            ContentUnavailableView("有什么需要处理？", systemImage: "sparkles", description: Text(store.sessionID == nil ? "新建会话，开始管理你的 TelePilot。" : "可以查询运行状态、排查问题或安排任务。涉及写入的操作会请求确认。"))
                                .padding(.top, 48)
                            if store.sessionID == nil {
                                Button("新建会话", systemImage: "square.and.pencil") { Task { await store.createSession(model) } }
                                    .buttonStyle(.borderedProminent).frame(maxWidth: .infinity)
                            }
                        }
                        ForEach(store.messages) { item in
                            AssistantMessageView(item: item) {
                                Task { await store.retry(item, model: model) }
                            } regenerate: {
                                if let user = latestUser {
                                    Task { await store.regenerate(user: user, assistant: item, edited: nil, model: model) }
                                }
                            } edit: {
                                if let user = latestUser { editingPair = AssistantEditPair(user: user, assistant: item) }
                            }
                            .environment(\.assistantMayRegenerate, item.id == latestAssistant?.id && !store.runActive && !store.busy)
                        }
                        if !store.liveText.isEmpty || !store.liveReasoning.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                Label("助手", systemImage: "sparkles").font(.caption.weight(.medium)).foregroundStyle(.secondary)
                                if !store.liveReasoning.isEmpty {
                                    DisclosureGroup("推理过程") { Text(AssistantRedaction.text(store.liveReasoning)).font(.footnote).foregroundStyle(.secondary).textSelection(.enabled).privacySensitive() }
                                }
                                Text(AssistantRedaction.text(store.liveText)).textSelection(.enabled).privacySensitive()
                            }.frame(maxWidth: .infinity, alignment: .leading)
                        }
                        Color.clear.frame(height: 1).id("assistant.bottom")
                    }
                    .padding(20)
                    .frame(maxWidth: 860)
                    .frame(maxWidth: .infinity)
                }
                .defaultScrollAnchor(.bottom)
                .scrollDismissesKeyboard(.interactively)
                .refreshable { await store.refreshMessages(model); await store.restoreRun(model) }
                .onChange(of: store.messages.count) { _, _ in withAnimation { proxy.scrollTo("assistant.bottom", anchor: .bottom) } }
            }
            if store.runActive { runBar }
        }
        .safeAreaInset(edge: .bottom) { composer }
    }

    private var latestUser: AssistantItem? { store.messages.last { $0.text("role") == "user" } }
    private var latestAssistant: AssistantItem? {
        guard let id = latestUser?["id"]?.intValue else { return nil }
        return store.messages.last { $0.text("role") == "assistant" && ($0["id"]?.intValue ?? 0) > id }
    }

    private var runBar: some View {
        HStack(alignment: .center, spacing: 12) {
            if store.streaming { ProgressView().controlSize(.small) }
            VStack(alignment: .leading, spacing: 3) {
                Text(AssistantPresentation.status(store.runStatus)).font(.subheadline.weight(.medium))
                if store.activeRun?["cancel_requested"]?.boolValue == true { Text("正在停止，请等待服务器确认").font(.caption).foregroundStyle(.secondary) }
            }
            Spacer(minLength: 2)
            if store.runStatus == "waiting_approval" {
                Button("审批") { panel = .tasks }
            }
            if !store.streaming {
                Button("恢复进度") { Task { await store.restoreRun(model) } }
            }
            Button("停止", systemImage: "stop.circle", role: .destructive) { confirmStop = true }
                .labelStyle(.iconOnly).disabled(store.busy)
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.bar)
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 10) {
            if !attachments.isEmpty {
                ScrollView(.horizontal) {
                    HStack {
                        ForEach(attachments) { attachment in
                            Image(uiImage: attachment.image).resizable().scaledToFill()
                                .frame(width: 66, height: 66).clipShape(.rect(cornerRadius: 10))
                                .overlay(alignment: .topTrailing) {
                                    Button("移除图片", systemImage: "xmark.circle.fill") { attachments.removeAll { $0.id == attachment.id } }
                                        .labelStyle(.iconOnly).symbolRenderingMode(.palette).foregroundStyle(.white, .black.opacity(0.7))
                                }
                        }
                    }.padding(.top, 4)
                }
            }
            HStack(alignment: .bottom, spacing: 12) {
                Menu {
                    PhotosPicker(selection: $photos, maxSelectionCount: max(1, 4 - attachments.count), matching: .images) { Label("从照片选择", systemImage: "photo") }
                    Button("从文件选择图片", systemImage: "folder") { importingImage = true }
                } label: { Image(systemName: "plus.circle").font(.title2).frame(minWidth: 32, minHeight: 44) }
                    .disabled(attachments.count >= 4 || loadingImage || store.busy)
                    .accessibilityLabel("添加图片")
                TextField("询问或安排任务…", text: $draft, axis: .vertical)
                    .lineLimit(1...7).padding(.vertical, 10)
                    .disabled(store.busy)
                    .accessibilityIdentifier("assistant.composer")
                Button {
                    if sendMode == "replace" { confirmReplace = true } else { performSend() }
                } label: {
                    if store.busy || loadingImage { ProgressView().frame(width: 44, height: 44) }
                    else { Image(systemName: "arrow.up.circle.fill").font(.system(size: 34)).frame(width: 44, height: 44) }
                }
                .disabled(!store.canSend || loadingImage || (draft.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty && attachments.isEmpty) || draft.count > 32_000 || (sendMode == "input" && draft.isEmpty))
                .accessibilityLabel("发送消息").accessibilityIdentifier("assistant.send")
            }
            HStack {
                Menu {
                    Picker("本轮模型", selection: $store.modelIndex) {
                        Text("自动，使用服务器配置").tag(-1)
                        ForEach(Array(store.models.enumerated()), id: \.offset) { index, item in
                            Text("\(item.assistantText("provider_name")) · \(item.assistantText("model"))").tag(index)
                        }
                    }
                } label: {
                    Label(modelLabel, systemImage: "cpu").lineLimit(1)
                }
                Spacer()
                if store.runActive {
                    Picker("发送方式", selection: $sendMode) {
                        Text("加入队列").tag("message")
                        Text("插入调整说明").tag("steer")
                        if store.runStatus == "waiting_input" { Text("补充信息").tag("input") }
                        Text("停止并替换").tag("replace")
                    }.pickerStyle(.menu)
                }
                if draft.count > 30_000 { Text("\(draft.count)/32000").foregroundStyle(draft.count > 32_000 ? .red : .secondary) }
            }.font(.caption).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 16).padding(.vertical, 10)
        .frame(maxWidth: 900)
        .frame(maxWidth: .infinity)
        .background(.bar)
        .onChange(of: store.runActive) { _, active in if !active { sendMode = "message" } }
    }

    private var modelLabel: String {
        store.models.indices.contains(store.modelIndex) ? store.models[store.modelIndex].assistantText("model") : "自动模型"
    }

    private func performSend() {
        let text = draft.trimmingCharacters(in: .whitespacesAndNewlines)
        let images = attachments.map(\.value)
        let mode = sendMode
        let sessionID = store.sessionID
        Task {
            if await store.send(text, attachments: images, model: model, mode: mode) {
                guard store.sessionID == sessionID else { return }
                draft = ""
                attachments = []
                sendMode = "message"
            }
        }
    }

    private func addImage(_ data: Data) throws {
        guard attachments.count < 4, let image = UIImage(data: data), image.size.width > 0, image.size.height > 0 else { throw APIError.incompatibleResponse }
        let longest = max(image.size.width, image.size.height)
        let scale = min(1, 2400 / longest)
        let format = UIGraphicsImageRendererFormat()
        format.scale = 1
        let size = CGSize(width: image.size.width * scale, height: image.size.height * scale)
        let renderer = UIGraphicsImageRenderer(size: size, format: format)
        let resized = renderer.image { _ in image.draw(in: CGRect(origin: .zero, size: size)) }
        guard let bytes = resized.jpegData(compressionQuality: 0.82), bytes.count <= 6_000_000 else { throw APIError.incompatibleResponse }
        attachments.append(AssistantAttachment(image: resized, value: ["kind": "image", "source": "data_url", "mime_type": "image/jpeg", "name": "image.jpg", "data_url": .string("data:image/jpeg;base64," + bytes.base64EncodedString())]))
    }
}

private struct AssistantEditPair: Identifiable {
    let id = UUID()
    let user: AssistantItem
    let assistant: AssistantItem
}

private struct AssistantMayRegenerateKey: EnvironmentKey { static let defaultValue = false }
private extension EnvironmentValues {
    var assistantMayRegenerate: Bool {
        get { self[AssistantMayRegenerateKey.self] }
        set { self[AssistantMayRegenerateKey.self] = newValue }
    }
}

private struct AssistantMessageView: View {
    @Environment(\.assistantMayRegenerate) private var mayRegenerate
    let item: AssistantItem
    let retry: () -> Void
    let regenerate: () -> Void
    let edit: () -> Void
    private var role: String { item.text("role") }
    private var text: String { AssistantRedaction.text(item["content"]?.assistantText("text") ?? "") }

    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack {
                Label(AssistantPresentation.status(role), systemImage: role == "user" ? "person.crop.circle" : role == "tool" ? "wrench.and.screwdriver" : "sparkles")
                    .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                Spacer()
                if item.text("run_status") == "failed" {
                    Text("未成功").font(.caption).foregroundStyle(.red)
                }
            }
            if role == "tool" {
                DisclosureGroup(item["content"]?.assistantText("tool_name").nilIfEmpty ?? "工具结果") {
                    AssistantJSONView(value: item["content"] ?? [:])
                }.font(.subheadline)
            } else {
                if !text.isEmpty {
                    Text((try? AttributedString(markdown: text, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(text))
                        .textSelection(.enabled).privacySensitive()
                        .contextMenu {
                            Button("复制", systemImage: "doc.on.doc") { UIPasteboard.general.setItems([[UIPasteboard.typeAutomatic: text]], options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(120)]) }
                            ShareLink(item: text) { Label("分享", systemImage: "square.and.arrow.up") }
                        }
                }
                if let reasoning = item["content"]?["reasoning"]?.stringValue, !reasoning.isEmpty {
                    DisclosureGroup("推理过程") { Text(AssistantRedaction.text(reasoning)).font(.footnote).foregroundStyle(.secondary).textSelection(.enabled).privacySensitive() }
                }
                let images = item["content"]?["images"]?.arrayValue ?? item["content"]?["attachments"]?.arrayValue ?? []
                if !images.isEmpty { AssistantImagesView(images: images) }
            }
            if role == "user" && item.text("run_status") == "failed" {
                Text("这条消息执行失败，可以查看任务轨迹了解原因。").font(.caption).foregroundStyle(.secondary)
                Button("重试这条消息", systemImage: "arrow.clockwise", action: retry).font(.caption)
            }
            if role == "assistant" && mayRegenerate {
                HStack(spacing: 18) {
                    Button("重新生成", systemImage: "arrow.trianglehead.2.clockwise", action: regenerate)
                    Button("编辑后重发", systemImage: "pencil", action: edit)
                }.font(.caption)
            }
            if let usage = item["usage"], usage.objectValue?.isEmpty == false {
                DisclosureGroup("本轮用量与详情") { AssistantJSONView(value: usage) }.font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(role == "user" ? 14 : 0)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(role == "user" ? Color.accentColor.opacity(0.08) : .clear, in: .rect(cornerRadius: 16))
    }
}

private struct AssistantImagesView: View {
    let images: [JSONValue]
    var body: some View {
        ForEach(Array(images.enumerated()), id: \.offset) { index, item in
            if let encoded = item["data_url"]?.stringValue?.split(separator: ",", maxSplits: 1).last,
               let data = Data(base64Encoded: String(encoded)), let image = UIImage(data: data) {
                Image(uiImage: image).resizable().scaledToFit().frame(maxHeight: 300).clipShape(.rect(cornerRadius: 12)).privacySensitive()
            } else if let raw = item["url"]?.stringValue, let url = URL(string: raw), url.scheme?.lowercased() == "https", url.user == nil, url.password == nil {
                Link("打开图片 \(index + 1)", destination: url)
            }
        }
    }
}

struct AssistantNotice: View {
    let text: String
    var isError = false
    var dismiss: (() -> Void)?
    var body: some View {
        HStack(alignment: .top, spacing: 12) {
            Image(systemName: isError ? "exclamationmark.circle" : "info.circle")
            Text(AssistantRedaction.text(text)).font(.subheadline).frame(maxWidth: .infinity, alignment: .leading)
            if let dismiss { Button("关闭提示", systemImage: "xmark", action: dismiss).labelStyle(.iconOnly) }
        }.padding().foregroundStyle(isError ? Color.red : .secondary)
            .background(isError ? Color.red.opacity(0.07) : Color.secondary.opacity(0.07))
            .accessibilityElement(children: .contain)
    }
}

struct AssistantJSONView: View {
    let value: JSONValue
    var body: some View {
        Text((try? AssistantPresentation.safeJSON(value).prettyText()) ?? "无法显示此数据")
            .font(.system(.caption, design: .monospaced)).textSelection(.enabled)
            .frame(maxWidth: .infinity, alignment: .leading).privacySensitive()
    }
}

private struct AssistantSessionsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Bindable var store: AssistantStore
    let select: (String) -> Void
    @State private var query = ""

    var body: some View {
        NavigationStack {
            List {
                Section {
                    Button("新建会话", systemImage: "square.and.pencil") { Task { await store.createSession(model); if let id = store.sessionID { select(id) } } }.disabled(store.busy)
                    Toggle("归档会话", isOn: $store.includeArchived)
                    Toggle("包含 Telegram 会话", isOn: $store.includeBot)
                }
                Section("会话") {
                    ForEach(store.sessions.filter { query.isEmpty || $0.text("title").localizedCaseInsensitiveContains(query) }) { item in
                        Button { select(item.id) } label: {
                            HStack {
                                VStack(alignment: .leading, spacing: 5) {
                                    Text(AssistantRedaction.text(item.text("title").nilIfEmpty ?? "新会话")).foregroundStyle(.primary).lineLimit(2)
                                    Text(item.text("channel") == "bot" ? "Telegram · 只读历史" : item.text("origin") == "scheduled" ? "定时任务" : "对话")
                                        .font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                if item.id == store.sessionID { Image(systemName: "checkmark").foregroundStyle(.tint) }
                            }
                        }.disabled(store.busy)
                    }
                    if store.sessions.isEmpty && !store.loading { Text("没有会话").foregroundStyle(.secondary) }
                }
            }
            .navigationTitle("会话")
            .searchable(text: $query, prompt: "搜索会话标题")
            .refreshable { await store.loadSessions(model) }
            .onChange(of: store.includeArchived) { _, _ in Task { await store.loadSessions(model) } }
            .onChange(of: store.includeBot) { _, _ in Task { await store.loadSessions(model) } }
        }
    }
}

private struct AssistantSessionSettingsView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Bindable var store: AssistantStore
    @State private var title = ""
    @State private var confirmDelete = false
    @State private var titleRevealed = false
    private var originalTitle: String { store.session?.assistantText("title") ?? "" }
    private var protectedTitle: Bool { AssistantRedaction.containsCredential(originalTitle) && !titleRevealed }
    var body: some View {
        NavigationStack {
            Form {
                Section("会话名称") {
                    TextField("名称", text: $title).disabled(protectedTitle).privacySensitive()
                    if protectedTitle { Button("显示原名称并编辑", systemImage: "eye") { title = originalTitle; titleRevealed = true } }
                    Button("保存名称") { Task { await store.updateSession(["title": .string(title)], model: model) } }
                        .disabled(protectedTitle || title.count > 64 || store.busy)
                }
                Section("账号上下文") {
                    Picker("关联账号", selection: $store.accountID) {
                        Text("全局").tag(Optional<Int>.none)
                        ForEach(model.accounts) { account in Text(AccountPresentation.name(account)).tag(Optional(account.id)) }
                    }
                    Button("保存账号上下文") {
                        Task { await store.updateSession(["account_id": store.accountID.map { .number(Double($0)) } ?? .null], model: model) }
                    }.disabled(store.busy)
                }
                Section {
                    Button(store.session?.assistantText("status") == "archived" ? "恢复会话" : "归档会话") {
                        Task {
                            await store.updateSession(["status": .string(store.session?.assistantText("status") == "archived" ? "active" : "archived")], model: model)
                            dismiss()
                        }
                    }.disabled(store.busy || store.runActive)
                    Button("删除会话", role: .destructive) { confirmDelete = true }.disabled(store.busy || store.runActive)
                } footer: { Text("运行中的任务结束后才能归档或删除会话。") }
                if let error = store.error { Section { Text(error).foregroundStyle(.red) } }
            }
            .navigationTitle("会话设置").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
            .onAppear { title = AssistantRedaction.text(originalTitle) }
            .confirmationDialog("删除此会话及历史？", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("删除会话", role: .destructive) { Task { await store.deleteSession(model); if store.sessionID == nil { dismiss() } } }
            } message: { Text("此操作不可撤销。") }
        }
    }
}

private struct AssistantEditMessageView: View {
    @Environment(\.dismiss) private var dismiss
    let original: String
    let save: (String) -> Void
    @State private var content = ""
    @State private var revealed = false
    private var protected: Bool { AssistantRedaction.containsCredential(original) && !revealed }
    var body: some View {
        NavigationStack {
            Form {
                Section("编辑最新一轮问题") {
                    TextEditor(text: $content).frame(minHeight: 220).disabled(protected).privacySensitive()
                    if protected {
                        Text("此消息包含敏感信息，默认已遮盖。显示原文后才能编辑重发。").font(.footnote).foregroundStyle(.secondary)
                        Button("显示原文并编辑", systemImage: "eye") { content = original; revealed = true }
                    }
                }
                Section { Text("重新生成会替换最新一轮回答，已执行的外部操作不会撤销。").font(.footnote).foregroundStyle(.secondary) }
            }
            .navigationTitle("编辑后重新生成").navigationBarTitleDisplayMode(.inline)
            .toolbar {
                ToolbarItem(placement: .cancellationAction) { Button("取消") { dismiss() } }
                ToolbarItem(placement: .confirmationAction) {
                    Button("发送") { save(content); dismiss() }.disabled(protected || content.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || content.count > 32_000)
                }
            }
            .onAppear { content = AssistantRedaction.text(original) }
        }
    }
}

extension String {
    fileprivate var nilIfEmpty: String? { isEmpty ? nil : self }
}
