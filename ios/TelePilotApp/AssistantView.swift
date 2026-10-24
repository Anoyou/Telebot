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
    @State private var pinnedToBottom = true
    @State private var sidebarVisible = true
    @FocusState private var composerFocused: Bool
    @AppStorage("telepilot.assistant.minimizeOnLeading") private var minimizeOnLeading = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    private let initialID: String?
    private let embedded: Bool
    private let minimize: (() -> Void)?

    /// - Parameter minimize: 嵌入 Agent 标签页时隐藏底部 Dock，由悬浮按钮调用此闭包返回上一个标签页。
    init(sessionID: String? = nil, embedded: Bool = false, minimize: (() -> Void)? = nil) {
        initialID = sessionID
        self.embedded = embedded
        self.minimize = minimize
    }

    private enum AssistantPanel: String, Identifiable {
        case sessions, settings, tasks, memory, inbox, trace
        var id: String { rawValue }
    }

    var body: some View {
        HStack(spacing: 0) {
            if sizeClass == .regular && sidebarVisible {
                AssistantSessionsView(store: store) { id in
                    Task { await store.select(id, model: model) }
                }
                .frame(minWidth: 240, idealWidth: 280, maxWidth: 320)
                .transition(.move(edge: .leading))
                Divider()
            }
            NavigationStack {
                conversation
                    .navigationTitle(AssistantRedaction.text(store.session?.assistantText("title").nilIfEmpty ?? "助手"))
                    .navigationBarTitleDisplayMode(.inline)
                    .toolbar {
                        ToolbarItem(placement: .topBarLeading) {
                            if sizeClass == .regular {
                                Button(sidebarVisible ? "隐藏会话列表" : "显示会话列表", systemImage: "sidebar.left") {
                                    withAnimation(reduceMotion ? nil : .snappy) { sidebarVisible.toggle() }
                                }
                                .accessibilityIdentifier("assistant.sessions.toggle")
                            } else {
                                Button("会话列表", systemImage: "list.bullet") { panel = .sessions }
                                    .accessibilityIdentifier("assistant.sessions")
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
        .task {
            let requested = embedded ? model.consumeAssistantSessionRequest() : nil
            await store.start(model, initialID: initialID ?? requested)
        }
        .onChange(of: model.assistantSessionRequest) { _, value in
            guard embedded, value != nil, let id = model.consumeAssistantSessionRequest() else { return }
            Task { await store.select(id, model: model) }
        }
        .onDisappear { store.pauseSubscription() }
        .onChange(of: scenePhase) { _, phase in
            if phase == .active { Task { await store.restoreRun(model); if store.unresolvedSend { await store.reconcileSend(model) } } }
            else if phase == .background { store.pauseSubscription() }
        }
        .sheet(item: $panel) { selected in
            switch selected {
            case .sessions:
                AssistantSessionsView(store: store, showsClose: true) { id in
                    panel = nil
                    Task { await store.select(id, model: model) }
                }
                .presentationDetents([.medium, .large])
                .presentationDragIndicator(.visible)
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
                            Button("读取更早的消息", systemImage: "clock.arrow.circlepath") { Task { await store.refreshMessages(model, older: true) } }
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
                        // 工具结果不再单独成块：并入同一轮回答底部的“本轮详情”。
                        ForEach(AssistantTurnGrouping.entries(store.messages)) { entry in
                            if let item = entry.item {
                                AssistantMessageView(item: item, tools: entry.tools) {
                                    Task { await store.retry(item, model: model) }
                                } regenerate: {
                                    if let user = latestUser {
                                        Task { await store.regenerate(user: user, assistant: item, edited: nil, model: model) }
                                    }
                                } edit: {
                                    if let user = latestUser { editingPair = AssistantEditPair(user: user, assistant: item) }
                                }
                                .environment(\.assistantMayRegenerate, item.id == latestAssistant?.id && !store.runActive && !store.busy)
                            } else {
                                // 本轮还没有最终回答（执行中或失败）时，工具调用仍可在折叠详情里查看。
                                AssistantTurnDetailsView(usage: nil, tools: entry.tools)
                            }
                        }
                        if !store.liveText.isEmpty || !store.liveReasoning.isEmpty {
                            VStack(alignment: .leading, spacing: 10) {
                                Label("助手", systemImage: "sparkles").font(.caption.weight(.medium)).foregroundStyle(.secondary)
                                if !store.liveReasoning.isEmpty {
                                    DisclosureGroup("推理过程") { Text(AssistantRedaction.text(store.liveReasoning)).font(.footnote).foregroundStyle(.secondary).textSelection(.enabled).privacySensitive() }
                                }
                                AssistantMarkdownView(text: AssistantRedaction.text(store.liveText)).privacySensitive()
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
                // 用户停留在底部附近时才跟随；向上翻阅历史时不打断。
                .onScrollGeometryChange(for: Bool.self) { geometry in
                    geometry.visibleRect.maxY >= geometry.contentSize.height - 80
                } action: { _, nearBottom in pinnedToBottom = nearBottom }
                .onChange(of: store.messages.count) { _, _ in
                    pinnedToBottom = true
                    withAnimation { proxy.scrollTo("assistant.bottom", anchor: .bottom) }
                }
                .onChange(of: store.liveText) { _, _ in
                    if pinnedToBottom { proxy.scrollTo("assistant.bottom", anchor: .bottom) }
                }
                .onChange(of: store.liveReasoning) { _, _ in
                    if pinnedToBottom { proxy.scrollTo("assistant.bottom", anchor: .bottom) }
                }
            }
            .overlay(alignment: minimizeOnLeading ? .bottomLeading : .bottomTrailing) {
                if minimize != nil && !composerFocused { minimizeButton }
            }
            if store.runActive { runBar }
        }
        .safeAreaInset(edge: .bottom) { composer }
        // Agent 页隐藏底部 Dock，把空间留给对话；用悬浮的最小化按钮返回。
        .toolbarVisibility(embedded ? .hidden : .automatic, for: .tabBar)
    }

    private var minimizeButton: some View {
        Button {
            composerFocused = false
            minimize?()
        } label: {
            Image(systemName: "arrow.down.right.and.arrow.up.left")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.primary)
                .frame(width: 50, height: 50)
                .contentShape(.circle)
                .glassEffect(.regular.interactive(), in: .circle)
        }
        .buttonStyle(.plain)
        .padding(16)
        .simultaneousGesture(
            DragGesture(minimumDistance: 24).onEnded { value in
                let width = value.translation.width
                guard abs(width) > 40 else { return }
                withAnimation(reduceMotion ? nil : .snappy) { minimizeOnLeading = width < 0 }
            }
        )
        .accessibilityLabel("最小化助手")
        .accessibilityHint("返回上一个页面。左右拖动可以把按钮移到另一侧。")
        .accessibilityAction(named: "移到另一侧") { minimizeOnLeading.toggle() }
        .accessibilityIdentifier("assistant.minimize")
        .transition(.scale.combined(with: .opacity))
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
                Button("审批", systemImage: "checkmark.shield") { panel = .tasks }
                    .buttonStyle(.bordered).controlSize(.small)
            }
            if !store.streaming {
                Button("恢复进度", systemImage: "arrow.clockwise") { Task { await store.restoreRun(model) } }
                    .buttonStyle(.bordered).controlSize(.small)
            }
        }
        .padding(.horizontal, 20).padding(.vertical, 12)
        .background(.bar)
    }

    private var composer: some View {
        VStack(alignment: .leading, spacing: 8) {
            if !attachments.isEmpty {
                ScrollView(.horizontal) {
                    HStack {
                        ForEach(attachments) { attachment in
                            Image(uiImage: attachment.image).resizable().scaledToFill()
                                .frame(width: 66, height: 66).clipShape(.rect(cornerRadius: 12))
                                .overlay(alignment: .topTrailing) {
                                    Button("移除图片", systemImage: "xmark.circle.fill") { attachments.removeAll { $0.id == attachment.id } }
                                        .labelStyle(.iconOnly).symbolRenderingMode(.palette).foregroundStyle(.white, .black.opacity(0.7))
                                        .frame(minWidth: 44, minHeight: 44, alignment: .topTrailing)
                                }
                        }
                    }.padding(.top, 4)
                }
                .scrollIndicators(.hidden)
            }
            HStack(alignment: .bottom, spacing: 4) {
                Menu {
                    PhotosPicker(selection: $photos, maxSelectionCount: max(1, 4 - attachments.count), matching: .images) { Label("从照片选择", systemImage: "photo") }
                    Button("从文件选择图片", systemImage: "folder") { importingImage = true }
                } label: {
                    Image(systemName: "plus")
                        .font(.system(size: 17, weight: .semibold))
                        .frame(width: 34, height: 34)
                        .background(Color(.tertiarySystemFill), in: .circle)
                        .frame(width: 44, height: 44)
                }
                .disabled(attachments.count >= 4 || loadingImage || composerReadOnlyReason != nil)
                .accessibilityLabel("添加图片")
                TextField(composerPlaceholder, text: $draft, axis: .vertical)
                    .lineLimit(1...8)
                    .focused($composerFocused)
                    // 请求进行中也允许继续输入，发送按钮会等待服务器确认后再可用。
                    .disabled(composerReadOnlyReason != nil)
                    .padding(.vertical, 12)
                    .accessibilityIdentifier("assistant.composer")
                if store.runActive { stopButton }
                sendButton
            }
            .padding(.leading, 2).padding(.trailing, 2)
            .background(Color(.systemBackground), in: .rect(cornerRadius: 24))
            .overlay {
                RoundedRectangle(cornerRadius: 24)
                    .strokeBorder(composerFocused ? Color.accentColor.opacity(0.6) : Color(.separator), lineWidth: composerFocused ? 1 : 0.5)
            }
            HStack(spacing: 8) {
                Menu {
                    Picker("本轮模型", selection: $store.modelIndex) {
                        Text("自动，使用服务器配置").tag(-1)
                        ForEach(Array(store.models.enumerated()), id: \.offset) { index, item in
                            Text("\(item.assistantText("provider_name")) · \(item.assistantText("model"))").tag(index)
                        }
                    }
                } label: {
                    composerChip(modelLabel, systemImage: "cpu")
                }
                .accessibilityLabel("本轮模型：\(modelLabel)")
                if store.runActive {
                    Menu {
                        Section("任务执行中，新消息如何处理") {
                            Picker("发送方式", selection: $sendMode) {
                                Label("加入队列（当前任务结束后执行）", systemImage: "text.badge.plus").tag("message")
                                Label("插入调整说明（当前任务参考）", systemImage: "arrow.turn.down.right").tag("steer")
                                if store.runStatus == "waiting_input" { Label("补充信息（回答助手的提问）", systemImage: "text.bubble").tag("input") }
                                Label("停止并替换（需确认）", systemImage: "arrow.triangle.2.circlepath").tag("replace")
                            }
                        }
                    } label: {
                        composerChip(sendModeTitle, systemImage: sendModeSymbol)
                    }
                    .accessibilityLabel("发送方式：\(sendModeTitle)")
                }
                Spacer(minLength: 0)
                if draft.count > 30_000 {
                    Text("\(draft.count)/32000").monospacedDigit().foregroundStyle(draft.count > 32_000 ? .red : .secondary)
                }
            }
            .font(.caption).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 12).padding(.top, 8).padding(.bottom, 6)
        .frame(maxWidth: 900)
        .frame(maxWidth: .infinity)
        .background(.bar)
        .onChange(of: store.runActive) { _, active in if !active { sendMode = "message" } }
    }

    private func composerChip(_ title: String, systemImage: String) -> some View {
        Label(title, systemImage: systemImage)
            .lineLimit(1)
            .padding(.horizontal, 10).padding(.vertical, 6)
            .background(Color(.tertiarySystemFill), in: .capsule)
            .frame(minHeight: 44)
            .contentShape(.rect)
    }

    private var sendButton: some View {
        Button(action: submit) {
            ZStack {
                if store.busy || loadingImage {
                    ProgressView().controlSize(.small).tint(.white)
                } else {
                    Image(systemName: sendMode == "replace" && store.runActive ? "arrow.triangle.2.circlepath" : "arrow.up")
                        .font(.system(size: 16, weight: .bold))
                        .foregroundStyle(.white)
                }
            }
            .frame(width: 34, height: 34)
            .background(sendDisabled && !store.busy ? Color(.systemGray3) : Color.accentColor, in: .circle)
            .frame(width: 44, height: 44)
            .contentShape(.circle)
        }
        .buttonStyle(.plain)
        .disabled(sendDisabled)
        .keyboardShortcut(.return, modifiers: .command)
        .accessibilityLabel(store.busy ? "正在提交" : sendMode == "replace" && store.runActive ? "停止并替换" : "发送消息")
        .accessibilityIdentifier("assistant.send")
    }

    private var stopButton: some View {
        let unavailable = store.busy || store.activeRun?["cancel_requested"]?.boolValue == true
        return Button { confirmStop = true } label: {
            Image(systemName: "stop.fill")
                .font(.system(size: 13, weight: .bold))
                .foregroundStyle(.white)
                .frame(width: 34, height: 34)
                .background(Color.red, in: .circle)
                .frame(width: 44, height: 44)
                .contentShape(.circle)
        }
        .buttonStyle(.plain)
        .disabled(unavailable)
        .opacity(unavailable ? 0.45 : 1)
        .accessibilityLabel("停止当前任务")
        .accessibilityIdentifier("assistant.stop")
    }

    private var trimmedDraft: String { draft.trimmingCharacters(in: .whitespacesAndNewlines) }

    private var sendDisabled: Bool {
        !store.canSend || store.sessionID == nil || loadingImage
            || (trimmedDraft.isEmpty && attachments.isEmpty) || draft.count > 32_000
            || (sendMode == "input" && trimmedDraft.isEmpty)
    }

    private var composerReadOnlyReason: String? {
        if store.session?.assistantText("channel") == "bot" { return "Telegram 会话为只读，请在 Telegram 中继续" }
        if store.session?.assistantText("status") == "archived" { return "已归档的会话为只读，恢复后可继续" }
        return nil
    }

    private var composerPlaceholder: String {
        if let reason = composerReadOnlyReason { return reason }
        if store.sessionID == nil { return "请先新建会话" }
        guard store.runActive else { return "询问或安排任务…" }
        switch sendMode {
        case "steer": return "补充调整说明，当前任务会参考…"
        case "input": return "回答助手的问题…"
        case "replace": return "输入替代消息…"
        default: return "加入队列，当前任务结束后执行…"
        }
    }

    private var sendModeTitle: String {
        switch sendMode {
        case "steer": "插入调整"
        case "input": "补充信息"
        case "replace": "停止并替换"
        default: "加入队列"
        }
    }

    private var sendModeSymbol: String {
        switch sendMode {
        case "steer": "arrow.turn.down.right"
        case "input": "text.bubble"
        case "replace": "arrow.triangle.2.circlepath"
        default: "text.badge.plus"
        }
    }

    private func submit() {
        guard !sendDisabled else { return }
        if sendMode == "replace" && store.runActive { confirmReplace = true } else { performSend() }
    }

    private var modelLabel: String {
        store.models.indices.contains(store.modelIndex) ? store.models[store.modelIndex].assistantText("model") : "自动模型"
    }

    private func performSend() {
        let original = draft
        let text = original.trimmingCharacters(in: .whitespacesAndNewlines)
        let sent = attachments
        let images = sent.map(\.value)
        let mode = sendMode
        let sessionID = store.sessionID
        Task {
            if await store.send(text, attachments: images, model: model, mode: mode) {
                guard store.sessionID == sessionID else { return }
                // 等待服务器确认期间允许继续输入：只移除已发出的内容，保留新输入的部分。
                if draft == original { draft = "" }
                else if draft.hasPrefix(original) { draft = String(draft.dropFirst(original.count)) }
                let sentIDs = Set(sent.map(\.id))
                attachments.removeAll { sentIDs.contains($0.id) }
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
    /// 同一轮中的工具结果消息，只在“本轮详情”展开后显示。
    var tools: [AssistantItem] = []
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
                // 正常情况下工具消息已并入回答详情；保留兜底展示，避免未知顺序的数据被吞掉。
                AssistantToolResultView(content: item["content"] ?? [:])
                let images = item["content"]?["images"]?.arrayValue ?? []
                if !images.isEmpty { AssistantImagesView(images: images) }
            } else {
                if !text.isEmpty {
                    Group {
                        if role == "user" {
                            Text(text).textSelection(.enabled)
                        } else {
                            AssistantMarkdownView(text: text)
                        }
                    }
                        .privacySensitive()
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
                // 工具产出的图片（例如截图）属于回答内容，直接显示在回答里。
                let toolImages = tools.flatMap { $0["content"]?["images"]?.arrayValue ?? [] }
                if !toolImages.isEmpty { AssistantImagesView(images: toolImages) }
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
            if role != "user" {
                AssistantTurnDetailsView(usage: item["usage"], tools: tools)
            }
        }
        .padding(role == "user" ? 14 : 0)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(role == "user" ? Color.accentColor.opacity(0.08) : .clear, in: .rect(cornerRadius: 16))
    }
}

// MARK: - 本轮详情

/// 一个显示条目：普通消息（附带同一轮的工具结果），或尚无最终回答时单独显示的工具调用。
private struct AssistantDisplayEntry: Identifiable {
    let id: String
    let item: AssistantItem?
    let tools: [AssistantItem]
}

enum AssistantTurnGrouping {
    /// 以用户消息切分轮次：服务器在回答之后才写入工具消息，所以同一轮内的工具结果归到这一轮最后一条助手回答。
    /// 没有助手回答的轮次（执行中、失败）把工具结果放在第一条工具消息的位置单独显示。
    fileprivate static func entries(_ messages: [AssistantItem]) -> [AssistantDisplayEntry] {
        var result: [AssistantDisplayEntry] = []
        var segment: [AssistantItem] = []
        func flush() {
            guard !segment.isEmpty else { return }
            let tools = segment.filter { $0.text("role") == "tool" }
            let ownerID = segment.last { $0.text("role") == "assistant" }?.id
            var orphanPlaced = false
            for item in segment {
                if item.text("role") == "tool" {
                    if ownerID == nil && !orphanPlaced {
                        result.append(AssistantDisplayEntry(id: "tools-\(item.id)", item: nil, tools: tools))
                        orphanPlaced = true
                    }
                } else {
                    result.append(AssistantDisplayEntry(id: item.id, item: item, tools: item.id == ownerID ? tools : []))
                }
            }
            segment.removeAll()
        }
        for item in messages {
            if item.text("role") == "user" {
                flush()
                result.append(AssistantDisplayEntry(id: item.id, item: item, tools: []))
            } else {
                segment.append(item)
            }
        }
        flush()
        return result
    }
}

/// 与 Web 端 ModelRunMeta 对齐的本轮执行信息（不显示费用）。
struct AssistantRunMeta {
    let usage: [String: JSONValue]
    let toolMessages: Int

    init(usage: JSONValue?, toolMessages: Int) {
        self.usage = usage?.objectValue ?? [:]
        self.toolMessages = toolMessages
    }

    private func string(_ key: String) -> String? {
        guard let value = usage[key]?.stringValue?.trimmingCharacters(in: .whitespacesAndNewlines), !value.isEmpty else { return nil }
        return value
    }
    private static func number(_ value: JSONValue?) -> Int? {
        switch value {
        case .integer(let v)?: return Int(exactly: v)
        case .number(let v)? where v.isFinite && abs(v) < 1e15: return Int(v.rounded())
        case .string(let v)?: return Int(v.trimmingCharacters(in: .whitespaces))
        default: return nil
        }
    }

    /// 实际作答的“供应商 · 模型”。
    var model: String? {
        let parts = [string("provider_name") ?? string("requested_provider_name"), string("model") ?? string("requested_model")].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
    var requestedModel: String? {
        let parts = [string("requested_provider_name"), string("requested_model")].compactMap { $0 }
        return parts.isEmpty ? nil : parts.joined(separator: " · ")
    }
    var usedFallback: Bool { usage["used_fallback"]?.boolValue == true }
    var streamFallback: Bool { usage["stream_fallback"]?.boolValue == true }
    var pinned: Bool { string("selection_mode") == "pinned" }
    var apiFormat: String? { string("api_format") }
    var inputTokens: Int? { Self.number(usage["input_tokens"]) }
    var outputTokens: Int? { Self.number(usage["output_tokens"]) }
    var retries: Int? { Self.number(usage["retry_count"]).flatMap { $0 > 0 ? $0 : nil } }
    var elapsedMS: Int? { Self.number(usage["elapsed_ms"]) }
    /// 工具调用数：优先实际调用 tool_calls；旧消息兼容 tool_count（schema_version 2 起它表示暴露数量，不再采用）；都没有时按工具结果条数。
    var toolCalls: Int? {
        if let actual = Self.number(usage["tool_calls"]) { return actual }
        if Self.number(usage["schema_version"]) != 2, let legacy = Self.number(usage["tool_count"]) { return legacy }
        return toolMessages > 0 ? toolMessages : nil
    }
    var stages: [(String, Int)] {
        guard let timings = usage["stage_timings"]?.objectValue else { return [] }
        return [("探测", "verify_ms"), ("路由", "route_ms"), ("首个词元", "first_token_ms")].compactMap { title, key in
            Self.number(timings[key]).map { (title, $0) }
        }
    }
    var hasUsage: Bool { !usage.isEmpty }

    /// 折叠时的一行摘要：模型 · 输入/输出词元 · 工具 · 耗时。
    var summaryLine: String {
        var bits: [String] = []
        if let model { bits.append(model) }
        if inputTokens != nil || outputTokens != nil {
            bits.append("输入 \(inputTokens.map(AIPresentation.tokens) ?? "–") / 输出 \(outputTokens.map(AIPresentation.tokens) ?? "–")")
        }
        if let toolCalls { bits.append("工具 \(toolCalls)") }
        if let retries { bits.append("重试 \(retries)") }
        if let elapsedMS, elapsedMS > 0 { bits.append(AIPresentation.latency(elapsedMS)) }
        return bits.isEmpty ? "本轮详情" : bits.joined(separator: " · ")
    }
}

/// 回答底部的本轮详情：默认只占一行，点按展开模型、词元、耗时、工具调用与脱敏后的原始数据。
private struct AssistantTurnDetailsView: View {
    let usage: JSONValue?
    let tools: [AssistantItem]
    @State private var expanded = false

    var body: some View {
        let meta = AssistantRunMeta(usage: usage, toolMessages: tools.count)
        if meta.hasUsage || !tools.isEmpty {
            DisclosureGroup(isExpanded: $expanded) {
                details(meta)
                    .padding(.top, 6)
            } label: {
                Label {
                    Text(meta.summaryLine)
                        .lineLimit(1)
                        .truncationMode(.middle)
                        .monospacedDigit()
                } icon: {
                    Image(systemName: meta.hasUsage ? "chart.bar.doc.horizontal" : "wrench.and.screwdriver")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                .accessibilityLabel("本轮详情：\(meta.summaryLine)")
            }
            .font(.caption)
            .tint(Color.secondary)
            .accessibilityIdentifier("assistant.turnDetails")
        }
    }

    @ViewBuilder private func details(_ meta: AssistantRunMeta) -> some View {
        VStack(alignment: .leading, spacing: 10) {
            if meta.hasUsage {
                VStack(alignment: .leading, spacing: 6) {
                    if let model = meta.model { row("实际模型", model) }
                    if meta.usedFallback, let requested = meta.requestedModel, requested != meta.model {
                        row("原模型", requested)
                    }
                    if let format = meta.apiFormat { row("接口格式", format) }
                    if let input = meta.inputTokens { row("输入词元", AIPresentation.tokens(input)) }
                    if let output = meta.outputTokens { row("输出词元", AIPresentation.tokens(output)) }
                    if let calls = meta.toolCalls { row("工具调用", "\(calls) 次") }
                    if let retries = meta.retries { row("重试", "\(retries) 次") }
                    if let elapsed = meta.elapsedMS, elapsed > 0 { row("总耗时", AIPresentation.latency(elapsed)) }
                    ForEach(meta.stages, id: \.0) { stage in row(stage.0, AIPresentation.latency(stage.1)) }
                }
                let flags = [meta.usedFallback ? "已改用备用模型" : nil, meta.streamFallback ? "完整响应（未流式返回）" : nil, meta.pinned ? "本轮固定模型" : nil].compactMap { $0 }
                if !flags.isEmpty {
                    Text(flags.joined(separator: " · ")).font(.caption2).foregroundStyle(.secondary)
                }
                Text("“词元”是模型计费与长度单位；“首个词元”为开始返回第一段内容的等待时间。")
                    .font(.caption2).foregroundStyle(.tertiary)
                    .fixedSize(horizontal: false, vertical: true)
            }
            if !tools.isEmpty {
                VStack(alignment: .leading, spacing: 8) {
                    Label("工具调用（\(tools.count)）", systemImage: "wrench.and.screwdriver")
                        .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    ForEach(tools) { tool in
                        AssistantToolResultView(content: tool["content"] ?? [:])
                    }
                    if !meta.hasUsage {
                        // 没有最终回答时，工具产出的图片也在这里显示。
                        let images = tools.flatMap { $0["content"]?["images"]?.arrayValue ?? [] }
                        if !images.isEmpty { AssistantImagesView(images: images) }
                    }
                }
            }
            if let usage, meta.hasUsage {
                DisclosureGroup("原始数据（已脱敏）") { AssistantJSONView(value: usage) }
                    .font(.caption).foregroundStyle(.secondary)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func row(_ title: String, _ value: String) -> some View {
        LabeledContent {
            Text(value).monospacedDigit().textSelection(.enabled).multilineTextAlignment(.trailing)
        } label: {
            Text(title)
        }
        .font(.caption)
        .foregroundStyle(.secondary)
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
    var showsClose = false
    let select: (String) -> Void
    @State private var query = ""
    @State private var pendingDelete: AssistantItem?

    private var filtered: [AssistantItem] {
        store.sessions.filter { query.isEmpty || $0.text("title").localizedCaseInsensitiveContains(query) }
    }

    var body: some View {
        NavigationStack {
            List {
                if let error = store.error {
                    Section { Label(AssistantRedaction.text(error), systemImage: "exclamationmark.circle").foregroundStyle(.red) }
                }
                Section {
                    Button("新建会话", systemImage: "square.and.pencil") { Task { await store.createSession(model); if let id = store.sessionID { select(id) } } }.disabled(store.busy)
                    Toggle(isOn: $store.includeArchived) { Label("只看归档会话", systemImage: "archivebox") }
                    Toggle(isOn: $store.includeBot) { Label("包含 Telegram 会话", systemImage: "paperplane") }
                }
                Section {
                    ForEach(filtered) { item in
                        sessionRow(item)
                    }
                    if store.sessions.isEmpty && !store.loading {
                        Label("没有会话", systemImage: "tray").foregroundStyle(.secondary)
                    }
                } header: {
                    Text("会话")
                } footer: {
                    Text("向左轻扫或长按会话可以删除。Telegram 会话只能在 Telegram 中管理；正在执行任务的会话需要先停止任务。")
                }
            }
            .navigationTitle("会话")
            .navigationBarTitleDisplayMode(.inline)
            .searchable(text: $query, prompt: "搜索会话标题")
            .refreshable { await store.loadSessions(model) }
            .onChange(of: store.includeArchived) { _, _ in Task { await store.loadSessions(model) } }
            .onChange(of: store.includeBot) { _, _ in Task { await store.loadSessions(model) } }
            .toolbar {
                if showsClose {
                    ToolbarItem(placement: .topBarTrailing) {
                        Button("关闭", systemImage: "xmark") { dismiss() }
                            .accessibilityIdentifier("assistant.sessions.close")
                    }
                }
            }
            .confirmationDialog(
                "删除这个会话？",
                isPresented: Binding(get: { pendingDelete != nil }, set: { if !$0 { pendingDelete = nil } }),
                titleVisibility: .visible,
                presenting: pendingDelete
            ) { item in
                Button("删除会话", role: .destructive) {
                    Task { await store.deleteSession(id: item.id, model: model) }
                }
            } message: { item in
                Text("“\(AssistantRedaction.text(item.text("title").nilIfEmpty ?? "新会话"))”及其全部消息会从服务器删除，此操作不可撤销。")
            }
        }
    }

    private func canDelete(_ item: AssistantItem) -> Bool {
        item.text("channel") != "bot" && !(item.id == store.sessionID && store.runActive)
    }

    @ViewBuilder
    private func sessionRow(_ item: AssistantItem) -> some View {
        let deleting = store.deletingSessionID == item.id
        Button { select(item.id) } label: {
            HStack(spacing: 12) {
                Label {
                    VStack(alignment: .leading, spacing: 5) {
                        Text(AssistantRedaction.text(item.text("title").nilIfEmpty ?? "新会话")).foregroundStyle(.primary).lineLimit(2)
                        Text(item.text("channel") == "bot" ? "Telegram · 只读历史" : item.text("origin") == "scheduled" ? "定时任务" : item.text("status") == "archived" ? "已归档" : "对话")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                } icon: {
                    Image(systemName: item.text("channel") == "bot" ? "paperplane" : item.text("origin") == "scheduled" ? "clock" : "bubble.left")
                }
                Spacer()
                if deleting { ProgressView().accessibilityLabel("正在删除") }
                else if item.id == store.sessionID { Image(systemName: "checkmark").foregroundStyle(.tint).accessibilityLabel("当前会话") }
            }
        }
        .disabled(store.busy)
        .swipeActions(edge: .trailing, allowsFullSwipe: false) {
            if canDelete(item) {
                Button("删除", systemImage: "trash") { pendingDelete = item }
                    .tint(.red)
                    .disabled(store.busy)
            }
        }
        .contextMenu {
            if canDelete(item) {
                Button("删除会话", systemImage: "trash", role: .destructive) { pendingDelete = item }
                    .disabled(store.busy)
            }
        }
        .accessibilityIdentifier("assistant.session." + item.id)
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
                    Button(store.session?.assistantText("status") == "archived" ? "恢复会话" : "归档会话", systemImage: store.session?.assistantText("status") == "archived" ? "tray.and.arrow.up" : "archivebox") {
                        Task {
                            await store.updateSession(["status": .string(store.session?.assistantText("status") == "archived" ? "active" : "archived")], model: model)
                            dismiss()
                        }
                    }.disabled(store.busy || store.runActive)
                    Button("删除会话", systemImage: "trash", role: .destructive) { confirmDelete = true }.disabled(store.busy || store.runActive)
                } footer: { Text("运行中的任务结束后才能归档或删除会话。") }
                if let error = store.error { Section { Text(error).foregroundStyle(.red) } }
            }
            .navigationTitle("会话设置").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
            .onAppear { title = AssistantRedaction.text(originalTitle) }
            .confirmationDialog("删除此会话及历史？", isPresented: $confirmDelete, titleVisibility: .visible) {
                Button("删除会话", role: .destructive) {
                    Task {
                        guard let id = store.sessionID else { return }
                        if await store.deleteSession(id: id, model: model) { dismiss() }
                    }
                }
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

// MARK: - 块级 Markdown

/// 助手回答的轻量块级 Markdown：代码块、标题、列表、引用、表格与分隔线；行内样式交给系统 AttributedString。
enum AssistantMarkdownBlock: Hashable {
    case paragraph(String)
    case heading(level: Int, text: String)
    case bullet(depth: Int, checked: Bool?, text: String)
    case ordered(number: String, depth: Int, text: String)
    case quote(String)
    case code(language: String, code: String)
    case table([[String]])
    case rule
}

enum AssistantMarkdown {
    static func inline(_ source: String) -> AttributedString {
        (try? AttributedString(markdown: source, options: .init(interpretedSyntax: .inlineOnlyPreservingWhitespace))) ?? AttributedString(source)
    }

    static func blocks(_ source: String) -> [AssistantMarkdownBlock] {
        var result: [AssistantMarkdownBlock] = []
        var paragraph: [String] = []
        var quote: [String] = []
        var table: [[String]] = []
        var code: [String]?
        var language = ""

        func flushText() {
            if !paragraph.isEmpty { result.append(.paragraph(paragraph.joined(separator: "\n"))); paragraph = [] }
            if !quote.isEmpty { result.append(.quote(quote.joined(separator: "\n"))); quote = [] }
            if !table.isEmpty { result.append(.table(table)); table = [] }
        }

        for raw in source.components(separatedBy: "\n") {
            let line = raw.hasSuffix("\r") ? String(raw.dropLast()) : raw
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            if var lines = code {
                if trimmed.hasPrefix("```") {
                    result.append(.code(language: language, code: lines.joined(separator: "\n")))
                    code = nil
                } else {
                    lines.append(line)
                    code = lines
                }
                continue
            }
            if trimmed.hasPrefix("```") {
                flushText()
                language = String(trimmed.dropFirst(3)).trimmingCharacters(in: .whitespaces)
                code = []
                continue
            }
            if trimmed.isEmpty { flushText(); continue }
            if trimmed.hasPrefix("|") {
                if !paragraph.isEmpty || !quote.isEmpty {
                    let pending = table
                    table = []
                    flushText()
                    table = pending
                }
                let cells = trimmed.trimmingCharacters(in: CharacterSet(charactersIn: "|"))
                    .components(separatedBy: "|")
                    .map { $0.trimmingCharacters(in: .whitespaces) }
                let separator = cells.allSatisfy { !$0.isEmpty && $0.allSatisfy { ":-".contains($0) } }
                if !separator { table.append(cells) }
                continue
            }
            if !table.isEmpty { flushText() }
            if trimmed.count >= 3, let first = trimmed.first, "-*_".contains(first),
               trimmed.allSatisfy({ $0 == first || $0 == " " }), trimmed.filter({ $0 == first }).count >= 3 {
                flushText(); result.append(.rule); continue
            }
            if trimmed.hasPrefix("#") {
                let level = trimmed.prefix { $0 == "#" }.count
                let rest = trimmed.dropFirst(level)
                if level <= 6, rest.hasPrefix(" ") {
                    flushText()
                    result.append(.heading(level: level, text: rest.trimmingCharacters(in: .whitespaces)))
                    continue
                }
            }
            if trimmed.hasPrefix(">") {
                if !paragraph.isEmpty { let pending = quote; quote = []; flushText(); quote = pending }
                quote.append(String(trimmed.dropFirst()).trimmingCharacters(in: .whitespaces))
                continue
            }
            let indent = line.prefix { $0 == " " || $0 == "\t" }.reduce(0) { $0 + ($1 == "\t" ? 4 : 1) }
            let depth = min(indent / 2, 4)
            if let marker = trimmed.first, "-*+".contains(marker), trimmed.dropFirst().hasPrefix(" ") {
                flushText()
                var body = String(trimmed.dropFirst(2))
                var checked: Bool?
                if body.hasPrefix("[ ] ") { checked = false; body = String(body.dropFirst(4)) }
                else if body.lowercased().hasPrefix("[x] ") { checked = true; body = String(body.dropFirst(4)) }
                result.append(.bullet(depth: depth, checked: checked, text: body))
                continue
            }
            let digits = trimmed.prefix { $0.isASCII && $0.isNumber }
            if !digits.isEmpty, digits.count <= 4 {
                let rest = trimmed.dropFirst(digits.count)
                if rest.hasPrefix(". ") || rest.hasPrefix(") ") {
                    flushText()
                    result.append(.ordered(number: String(digits), depth: depth, text: String(rest.dropFirst(2))))
                    continue
                }
            }
            if !quote.isEmpty { flushText() }
            paragraph.append(line)
        }
        // 流式输出时代码块可能尚未闭合，按已收到的内容显示。
        if let code { result.append(.code(language: language, code: code.joined(separator: "\n"))) }
        flushText()
        return result
    }
}

struct AssistantMarkdownView: View {
    let text: String

    var body: some View {
        let blocks = AssistantMarkdown.blocks(text)
        VStack(alignment: .leading, spacing: 10) {
            ForEach(Array(blocks.enumerated()), id: \.offset) { _, block in
                blockView(block)
            }
        }
        .frame(maxWidth: .infinity, alignment: .leading)
    }

    private func headingFont(_ level: Int) -> Font {
        switch level {
        case ...1: Font.title3.weight(.semibold)
        case 2: Font.headline
        default: Font.subheadline.weight(.semibold)
        }
    }

    @ViewBuilder
    private func blockView(_ block: AssistantMarkdownBlock) -> some View {
        switch block {
        case .paragraph(let value):
            Text(AssistantMarkdown.inline(value)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        case .heading(let level, let value):
            Text(AssistantMarkdown.inline(value))
                .font(headingFont(level))
                .textSelection(.enabled)
                .padding(.top, 4)
                .accessibilityAddTraits(.isHeader)
        case .bullet(let depth, let checked, let value):
            HStack(alignment: .firstTextBaseline, spacing: 8) {
                if let checked {
                    Image(systemName: checked ? "checkmark.square" : "square").foregroundStyle(.secondary)
                        .accessibilityLabel(checked ? "已完成" : "未完成")
                } else {
                    Text(depth == 0 ? "•" : "◦").foregroundStyle(.secondary)
                }
                Text(AssistantMarkdown.inline(value)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * 16)
        case .ordered(let number, let depth, let value):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(number + ".").monospacedDigit().foregroundStyle(.secondary)
                Text(AssistantMarkdown.inline(value)).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * 16)
        case .quote(let value):
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 1.5).fill(.tertiary).frame(width: 3)
                Text(AssistantMarkdown.inline(value)).foregroundStyle(.secondary).textSelection(.enabled)
                    .fixedSize(horizontal: false, vertical: true)
            }
            .fixedSize(horizontal: false, vertical: true)
        case .code(let language, let code):
            AssistantCodeBlockView(language: language, code: code)
        case .table(let rows):
            ScrollView(.horizontal) {
                Grid(alignment: .leading, horizontalSpacing: 16, verticalSpacing: 8) {
                    ForEach(Array(rows.enumerated()), id: \.offset) { index, row in
                        GridRow {
                            ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                                Text(AssistantMarkdown.inline(cell))
                                    .font(index == 0 ? Font.footnote.weight(.semibold) : Font.footnote)
                                    .textSelection(.enabled)
                            }
                        }
                        if index == 0 && rows.count > 1 { Divider() }
                    }
                }
                .padding(12)
            }
            .background(Color(.secondarySystemBackground), in: .rect(cornerRadius: 12))
        case .rule:
            Divider()
        }
    }
}

private struct AssistantCodeBlockView: View {
    let language: String
    let code: String
    @State private var copied = false

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label(language.isEmpty ? "代码" : language, systemImage: "chevron.left.forwardslash.chevron.right")
                    .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                Spacer()
                Button(copied ? "已复制" : "复制代码", systemImage: copied ? "checkmark" : "doc.on.doc") {
                    UIPasteboard.general.setItems([[UIPasteboard.typeAutomatic: code]], options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(120)])
                    copied = true
                }
                .labelStyle(.iconOnly).font(.caption)
                .frame(minWidth: 44, minHeight: 36)
            }
            .padding(.leading, 12).padding(.trailing, 4)
            Divider()
            ScrollView(.horizontal) {
                Text(code)
                    .font(.system(.footnote, design: .monospaced))
                    .textSelection(.enabled)
                    .padding(12)
            }
        }
        .background(Color(.secondarySystemBackground), in: .rect(cornerRadius: 12))
        .task(id: copied) {
            guard copied else { return }
            try? await Task.sleep(for: .seconds(2))
            copied = false
        }
    }
}

// MARK: - 工具结果

/// 工具消息默认显示状态与简短摘要，原始数据（已脱敏）折叠在下方。
private struct AssistantToolResultView: View {
    let content: JSONValue

    private var failed: Bool { content["is_error"]?.boolValue == true }
    private var name: String { content.assistantText("tool_name") }

    var body: some View {
        let lines = Self.summary(content["result_summary"])
        VStack(alignment: .leading, spacing: 8) {
            Label {
                Text(name.isEmpty ? "系统能力" : name).font(.subheadline.weight(.medium)).lineLimit(2)
            } icon: {
                Image(systemName: failed ? "exclamationmark.triangle.fill" : "checkmark.circle.fill")
                    .foregroundStyle(failed ? Color.orange : Color.green)
            }
            .accessibilityValue(failed ? "调用失败" : "调用完成")
            Text(failed ? "调用失败" : "调用完成").font(.caption).foregroundStyle(.secondary)
            if !lines.isEmpty {
                VStack(alignment: .leading, spacing: 4) {
                    ForEach(Array(lines.enumerated()), id: \.offset) { _, line in
                        Text(line).font(.footnote).foregroundStyle(.secondary).lineLimit(3).textSelection(.enabled)
                    }
                }
            }
            if content["result_summary"] != nil {
                DisclosureGroup("查看原始数据") {
                    AssistantJSONView(value: content["result_summary"] ?? .null)
                }
                .font(.caption).foregroundStyle(.secondary)
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(Color(.secondarySystemBackground), in: .rect(cornerRadius: 12))
    }

    static func summary(_ value: JSONValue?) -> [String] {
        guard let value else { return [] }
        let safe = AssistantPresentation.safeJSON(value)
        switch safe {
        case .string(let text):
            return text.components(separatedBy: .newlines)
                .map { $0.trimmingCharacters(in: .whitespaces) }
                .filter { !$0.isEmpty }
                .prefix(4).map { String($0.prefix(200)) }
        case .array(let values):
            return ["共 \(values.count) 项结果"]
        case .object(let fields):
            let preferred = ["message", "summary", "preview", "detail", "error", "status", "ok", "count", "total"]
            let keys = preferred.filter { fields[$0] != nil } + fields.keys.sorted().filter { !preferred.contains($0) }
            var lines: [String] = []
            for key in keys {
                guard lines.count < 5, let field = fields[key] else { continue }
                switch field {
                case .string(let text) where !text.isEmpty: lines.append("\(key)：\(String(text.prefix(160)))")
                case .integer(let number): lines.append("\(key)：\(number)")
                case .number(let number): lines.append("\(key)：\(number.formatted())")
                case .bool(let flag): lines.append("\(key)：\(flag ? "是" : "否")")
                case .array(let values): lines.append("\(key)：\(values.count) 项")
                default: continue
                }
            }
            return lines
        default:
            return []
        }
    }
}

extension String {
    fileprivate var nilIfEmpty: String? { isEmpty ? nil : self }
}
