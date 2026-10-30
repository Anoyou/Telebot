import PhotosUI
import SwiftUI
import TelePilotCore
import UniformTypeIdentifiers

struct AssistantView: View {
    @Environment(AppModel.self) private var model
    @Environment(\.dismiss) private var dismiss
    @Environment(\.horizontalSizeClass) private var sizeClass
    @Environment(\.scenePhase) private var scenePhase
    @State private var store = AssistantStore()
    @State private var panel: AssistantPanel?
    @State private var abandonPending = false
    @State private var editingPair: AssistantEditPair?
    @State private var pinnedToBottom = true
    /// 用户正在拖动或惯性滚动；只有用户自己滚离底部才取消贴底，内容高度变化不会。
    @State private var userScrolling = false
    @State private var sidebarVisible = true
    @FocusState private var composerFocused: Bool
    @State private var composerPanel = AssistantComposerPanelState()
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
        case sessions, settings, tasks, memory, skills, inbox, trace
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
                            // Skills 单独放在工具栏，不再藏在“助手菜单”里。
                            Button("Skills", systemImage: "wand.and.stars") { panel = .skills }
                                .accessibilityIdentifier("assistant.skills")
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
            case .skills: AgentSkillsSheet()
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
        .accessibilityIdentifier("assistant.screen")
    }

    private var conversation: some View {
        VStack(spacing: 0) {
            if store.capabilities["enabled"]?.boolValue == false || store.capabilities["ai_enabled"]?.boolValue == false {
                AssistantNotice(text: "服务器尚未启用系统助手或 AI，请先在服务器设置中启用并配置模型。")
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
                            .confirmationDialog("放弃待核对的发送记录？", isPresented: $abandonPending, titleVisibility: .visible) {
                                Button("放弃本机记录", role: .destructive) { store.clearPending() }
                            } message: { Text("这不会停止服务器任务。请先检查任务历史，重新发送可能产生重复任务。") }
                    }.font(.subheadline)
                }.padding().frame(maxWidth: .infinity, alignment: .leading).background(.orange.opacity(0.1))
            }
            ScrollViewReader { proxy in
                // 最新一轮的用户消息与回答在这里算一次，不在每一行里重复遍历全部消息。
                let latestUser = self.latestUser
                let latestAssistant = self.latestAssistant(after: latestUser)
                let idle = !store.runActive && !store.busy
                ScrollView {
                    LazyVStack(alignment: .leading, spacing: 24) {
                        if store.hasOlderMessages && !store.messages.isEmpty && store.revealStart == 0 {
                            olderMessagesRow(proxy)
                        }
                        if store.messages.isEmpty && !store.initialLoadFinished {
                            // 首次读取尚未结束：固定高度的占位，不闪“空会话”提示，也不改变滚动区域。
                            ProgressView("正在读取对话…")
                                .frame(maxWidth: .infinity, minHeight: 160)
                                .accessibilityIdentifier("assistant.loading")
                        } else if store.messages.isEmpty && !store.live.hasText && !store.loading {
                            ContentUnavailableView("有什么需要处理？", systemImage: "sparkles", description: Text(store.sessionID == nil ? "新建会话，开始管理你的 TelePilot。" : "可以查询运行状态、排查问题或安排任务。涉及写入的操作会请求确认。"))
                                .padding(.top, 48)
                            if store.sessionID == nil {
                                Button("新建会话", systemImage: "square.and.pencil") { Task { await store.createSession(model) } }
                                    .buttonStyle(.borderedProminent).frame(maxWidth: .infinity)
                            }
                        }
                        // 分组与 Markdown 预处理已在后台完成（store.entries / store.rendered）；
                        // 消息行是 Equatable 视图，输入框打字或流式输出时不会重新计算历史消息。
                        // 打开会话时先显示最近一屏（store.revealStart 之后），其余逐帧向上追加。
                        ForEach(store.entries[min(store.revealStart, store.entries.count)...]) { entry in
                            if let item = entry.item {
                                // 消息操作：我的消息下「复制消息／编辑消息」，回答下「复制回复／重新生成」。
                                // 编辑与重新生成只针对最新一轮（服务器 regenerate 接口的语义），任务执行中不提供。
                                AssistantMessageView(item: item, rendered: entry.rendered, tools: entry.tools,
                                                     mayRegenerate: idle && item.id == latestAssistant?.id,
                                                     mayEdit: idle && latestAssistant != nil && item.id == latestUser?.id) {
                                    Task { await store.retry(item, model: model) }
                                } regenerate: {
                                    // 行是 Equatable 视图，闭包可能是旧的：点按时再从 store 读取最新一轮。
                                    if let user = self.latestUser {
                                        Task { await store.regenerate(user: user, assistant: item, edited: nil, model: model) }
                                    }
                                } edit: {
                                    if let assistant = self.latestAssistant(after: self.latestUser) {
                                        editingPair = AssistantEditPair(user: item, assistant: assistant)
                                    }
                                }
                                .equatable()
                                .id(entry.id)
                            } else {
                                // 本轮还没有最终回答（执行中或失败）时，工具调用仍可在折叠详情里查看。
                                AssistantTurnDetailsView(usage: nil, tools: entry.tools)
                                    .id(entry.id)
                            }
                        }
                        // 正在执行的任务：回答位置显示真实阶段（流光状态行），有正文后缩为末尾的小指示。
                        // 第十一轮：流式状态在独立的 AssistantLiveState 里，只有这个视图读取；跟随滚动由它节流后回调。
                        AssistantLiveReplyView(store: store, follow: {
                            if pinnedToBottom { scrollToBottomAfterLayout(proxy) }
                        }) {
                            panel = .tasks
                        } resume: {
                            Task { await store.restoreRun(model) }
                        }
                        // 上一轮未结束时发出的消息：虚线待处理气泡，下方选择排队／插入调整／停止并替换。
                        AssistantPendingMessagesView(store: store)
                        Color.clear.frame(height: 1).id("assistant.bottom")
                    }
                    .padding(.horizontal, 20).padding(.top, 20).padding(.bottom, 8)
                    .frame(maxWidth: 860)
                    .frame(maxWidth: .infinity)
                }
                // 初次打开与内容不满一屏时贴底；内容高度变化（行测量、图片解码、读取更早消息）只在用户停留在底部时
                // 保持贴底。原来对所有情况都贴底：加载期间每次高度变化都把正在上翻的用户拉回底部，看起来“只能滚一小段”。
                .defaultScrollAnchor(.bottom, for: .initialOffset)
                .defaultScrollAnchor(.bottom, for: .alignment)
                .defaultScrollAnchor(pinnedToBottom ? .bottom : nil, for: .sizeChanges)
                .scrollDismissesKeyboard(.interactively)
                .refreshable { await store.refreshMessages(model); await store.restoreRun(model) }
                // 用户停留在底部附近时才跟随；向上翻阅历史时不打断。只有用户手势把视图带离底部才取消贴底：
                // 加载期间内容变高、视图暂时不在底部时仍保持贴底，随后由锚点或下方的补滚回到底部。
                .onScrollPhaseChange { _, phase in
                    userScrolling = phase == .tracking || phase == .interacting || phase == .decelerating
                }
                .onScrollGeometryChange(for: Bool.self) { geometry in
                    geometry.visibleRect.maxY >= geometry.contentSize.height - 80
                } action: { _, nearBottom in
                    if nearBottom { pinnedToBottom = true } else if userScrolling { pinnedToBottom = false }
                }
                // 只在出现新的最后一条消息时滚到底部；在顶部插入更早的消息不触发。
                // 首次出现内容时不做动画（由初始锚点负责），避免整页从顶部滑到底部。
                .onChange(of: store.messages.last?.id) { old, _ in
                    pinnedToBottom = true
                    if old == nil {
                        scrollToBottomAfterLayout(proxy)
                    } else {
                        withAnimation { proxy.scrollTo("assistant.bottom", anchor: .bottom) }
                    }
                }
                // 第八轮：任务开始、出现新的待处理消息时滚到底部，让回答位置的状态行与待处理气泡可见。
                .onChange(of: store.runID) { _, id in
                    guard id != nil else { return }
                    pinnedToBottom = true
                    scrollToBottomAfterLayout(proxy)
                }
                .onChange(of: store.pendingQueue.count + store.steered.count) { old, new in
                    guard new > old else { return }
                    pinnedToBottom = true
                    scrollToBottomAfterLayout(proxy)
                }
                // 首屏之后逐帧向上追加更早的消息：仍贴底时补滚一次，保证停在最新一条。
                .onChange(of: store.revealStart) { _, _ in
                    if pinnedToBottom { scrollToBottomAfterLayout(proxy) }
                }
            }
            .overlay(alignment: minimizeOnLeading ? .bottomLeading : .bottomTrailing) {
                if minimize != nil && !composerFocused { minimizeButton }
            }
        }
        // 输入栏（「+」附件、模型选择用系统原生 popover 锚定在各自按钮上，第九轮）。
        .safeAreaInset(edge: .bottom, spacing: 0) {
            AssistantComposer(store: store, panel: composerPanel, focused: $composerFocused)
        }
        .onChange(of: store.sessionID) { _, _ in composerPanel.close() }
        // Agent 页隐藏底部 Dock，把空间留给对话；用悬浮的最小化按钮返回。
        .toolbarVisibility(embedded ? .hidden : .automatic, for: .tabBar)
    }

    /// 顶部的“更早的消息”：滚动到这里时自动读取上一页，读取后保持当前阅读位置。
    @ViewBuilder private func olderMessagesRow(_ proxy: ScrollViewProxy) -> some View {
        Group {
            if store.loadingOlder {
                HStack(spacing: 8) { ProgressView(); Text("正在读取更早的消息…").font(.footnote).foregroundStyle(.secondary) }
            } else {
                Button("读取更早的消息", systemImage: "clock.arrow.circlepath") { Task { await loadOlder(proxy) } }
                    .font(.footnote)
            }
        }
        .frame(maxWidth: .infinity)
        // 只在首屏读取结束、显示的是服务器数据后才自动读取更早的消息；加载期间顶部行短暂可见不会连锁触发分页。
        .onAppear {
            guard store.initialLoadFinished, !store.showingCached else { return }
            Task { await loadOlder(proxy) }
        }
        .accessibilityIdentifier("assistant.older")
    }

    /// 等新内容完成一次布局后再滚到底部（同一事务里 scrollTo 拿到的是旧布局）。用户已经上翻时不滚。
    private func scrollToBottomAfterLayout(_ proxy: ScrollViewProxy) {
        Task { @MainActor in
            await Task.yield()
            try? await Task.sleep(for: .milliseconds(16))
            guard pinnedToBottom, !userScrolling else { return }
            proxy.scrollTo("assistant.bottom", anchor: .bottom)
        }
    }

    private func loadOlder(_ proxy: ScrollViewProxy) async {
        guard !store.loadingOlder, store.hasOlderMessages, store.revealStart == 0, let anchor = store.entries.first?.id else { return }
        await store.refreshMessages(model, older: true)
        proxy.scrollTo(anchor, anchor: .top)
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
    private func latestAssistant(after user: AssistantItem?) -> AssistantItem? {
        guard let id = user?["id"]?.intValue else { return nil }
        return store.messages.last { $0.text("role") == "assistant" && ($0["id"]?.intValue ?? 0) > id }
    }

}

private struct AssistantEditPair: Identifiable {
    let id = UUID()
    let user: AssistantItem
    let assistant: AssistantItem
}

/// 一条消息。Equatable：只有消息内容、预处理结果、工具结果或“可重新生成”变化时才重绘，
/// 输入框打字、流式输出等其他状态变化不会让历史消息重新计算布局。
private struct AssistantMessageView: View, Equatable {
    let item: AssistantItem
    let rendered: AssistantRendered?
    /// 同一轮中的工具结果消息，只在“本轮详情”展开后显示。
    var tools: [AssistantItem] = []
    var mayRegenerate = false
    var mayEdit = false
    let retry: () -> Void
    let regenerate: () -> Void
    let edit: () -> Void
    private var role: String { item.text("role") }
    private var text: String { rendered?.text ?? AssistantRedaction.text(item["content"]?.assistantText("text") ?? "") }

    /// nonisolated：比较的都是 Sendable 的存储属性，Equatable 一致性不跨入主 actor（Swift 6 ConformanceIsolation）。
    nonisolated static func == (lhs: AssistantMessageView, rhs: AssistantMessageView) -> Bool {
        // 预处理结果按 key（消息 ID + 内容哈希）比较，不逐个比较 AttributedString。
        lhs.item == rhs.item && lhs.rendered?.key == rhs.rendered?.key && lhs.tools == rhs.tools
            && lhs.mayRegenerate == rhs.mayRegenerate && lhs.mayEdit == rhs.mayEdit
    }

    @State private var copied = false
    @State private var copyCount = 0
    @State private var confirmRegenerate = false
    @State private var selectingText = false

    var body: some View {
        bubble
            // 第九轮：「选择文本」面板——整条消息放进可自由拖选的原生文本视图。
            .sheet(isPresented: $selectingText) {
                AssistantSelectTextSheet(text: text, markdown: role == "assistant")
            }
    }

    @ViewBuilder private var bubble: some View {
        if role == "user" {
            // 我的消息（参考 Codex）：角色标签在上，气泡贴右，操作行在气泡外下方、与气泡右边缘对齐。
            VStack(alignment: .trailing, spacing: 6) {
                header
                VStack(alignment: .leading, spacing: 10) { content }
                    .padding(14)
                    .background(Color.accentColor.opacity(0.08), in: .rect(cornerRadius: 16))
                    .accessibilityElement(children: .contain)
                    .accessibilityIdentifier("assistant.message.bubble")
                messageActions
                    .padding(.top, 2)
            }
            .padding(.leading, 32)
            .frame(maxWidth: .infinity, alignment: .trailing)
        } else {
            // 回答：正文从左边缘开始，操作行在正文（与本轮详情）下方、与正文左边缘对齐。
            VStack(alignment: .leading, spacing: 10) {
                header
                content
                // 第八轮：操作行紧跟正文，模型名／本轮用量在它下方。
                if role == "assistant" { messageActions }
                if role != "user" {
                    AssistantTurnDetailsView(usage: item["usage"], tools: tools)
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
        }
    }

    private var header: some View {
        HStack {
            if role == "user" {
                if item.text("run_status") == "failed" {
                    Text("未成功").font(.caption).foregroundStyle(.red)
                }
                Spacer(minLength: 0)
            }
            Label(AssistantPresentation.status(role), systemImage: role == "user" ? "person.crop.circle" : role == "tool" ? "wrench.and.screwdriver" : "sparkles")
                .font(.caption.weight(.semibold)).foregroundStyle(.secondary)
            if role != "user" {
                Spacer()
                if item.text("run_status") == "failed" {
                    Text("未成功").font(.caption).foregroundStyle(.red)
                }
            }
        }
    }

    @ViewBuilder private var content: some View {
        if role == "tool" {
            // 正常情况下工具消息已并入回答详情；保留兜底展示，避免未知顺序的数据被吞掉。
            AssistantToolResultView(content: item["content"] ?? [:])
            let images = item["content"]?["images"]?.arrayValue ?? []
            if !images.isEmpty { AssistantImagesView(images: images, owner: item.id) }
        } else {
            if !text.isEmpty {
                Group {
                    if role == "user" {
                        Text(text).textSelection(.enabled)
                    } else if let blocks = rendered?.blocks {
                        AssistantRenderedMarkdownView(blocks: blocks)
                    } else {
                        AssistantMarkdownView(text: text)
                    }
                }
                    .privacySensitive()
                    .contextMenu {
                        Button("复制", systemImage: "doc.on.doc") { UIPasteboard.general.setItems([[UIPasteboard.typeAutomatic: text]], options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(120)]) }
                        // 第九轮：长按菜单里也能进入「选择文本」，拖选任意片段。
                        Button("选择文本", systemImage: "text.cursor") { selectingText = true }
                        ShareLink(item: text) { Label("分享", systemImage: "square.and.arrow.up") }
                    }
            }
            let reasoning = rendered?.reasoning ?? AssistantRedaction.text(item["content"]?["reasoning"]?.stringValue ?? "")
            if !reasoning.isEmpty {
                DisclosureGroup("推理过程") { Text(reasoning).font(.footnote).foregroundStyle(.secondary).textSelection(.enabled).privacySensitive() }
            }
            let images = item["content"]?["images"]?.arrayValue ?? item["content"]?["attachments"]?.arrayValue ?? []
            if !images.isEmpty { AssistantImagesView(images: images, owner: item.id) }
            // 工具产出的图片（例如截图）属于回答内容，直接显示在回答里。
            let toolImages = tools.flatMap { $0["content"]?["images"]?.arrayValue ?? [] }
            if !toolImages.isEmpty { AssistantImagesView(images: toolImages, owner: item.id + "-tools") }
        }
        if role == "user" && item.text("run_status") == "failed" {
            Text("这条消息执行失败，可以查看任务轨迹了解原因。").font(.caption).foregroundStyle(.secondary)
            Button("重试这条消息", systemImage: "arrow.clockwise", action: retry).font(.caption)
        }
    }

    /// 消息下方的操作行（第七轮，参考 Codex）：图标＋文字的原生 `Button`＋`Label`，统一 `.buttonStyle(.plain)`、
    /// 次要色、无边框无背景；每个按钮至少 44pt 高，按钮之间间距 20pt；不放进气泡里。
    /// - 我的消息：复制、编辑（编辑后重新生成最新一轮），整行与气泡右边缘对齐。
    /// - 助手回答：复制（正文 Markdown，不含本轮详情与工具卡片）、重新生成（需确认），整行与正文左边缘对齐。
    /// 正在生成的回答在 `AssistantLiveReplyView` 里，不显示这些按钮。
    @ViewBuilder private var messageActions: some View {
        let isUser = role == "user"
        HStack(spacing: AssistantMessageActionStyle.spacing) {
            if !text.isEmpty {
                // 复制后只把图标短暂换成对勾（文字不变，按钮行不跳动），并有轻触反馈；VoiceOver 读“已复制”。
                Button(isUser ? "复制消息" : "复制回复", systemImage: copied ? "checkmark" : "doc.on.doc") { copy() }
                    .accessibilityLabel(copied ? "已复制" : isUser ? "复制消息" : "复制回复")
                    .accessibilityIdentifier(isUser ? "assistant.message.copy" : "assistant.reply.copy")
            }
            if !text.isEmpty {
                // 第九轮：打开「选择文本」面板，可拖选任意片段（系统复制／查找／翻译菜单）。
                Button("选择文本", systemImage: "text.cursor") { selectingText = true }
                    .accessibilityLabel("选择文本")
                    .accessibilityIdentifier(isUser ? "assistant.message.selectText" : "assistant.reply.selectText")
            }
            if isUser && mayEdit {
                Button("编辑消息", systemImage: "pencil", action: edit)
                    .accessibilityLabel("编辑消息")
                    .accessibilityIdentifier("assistant.message.edit")
            }
            if !isUser && mayRegenerate {
                Button("重新生成", systemImage: "arrow.clockwise") { confirmRegenerate = true }
                    .accessibilityLabel("重新生成")
                    .accessibilityIdentifier("assistant.reply.regenerate")
                    .confirmationDialog("重新生成这一轮回答？", isPresented: $confirmRegenerate, titleVisibility: .visible) {
                        Button("重新生成", action: regenerate)
                    } message: { Text("会用当前选择的模型重新回答最新一轮问题并替换现有回答；已经执行的外部操作不会撤销，需要确认的操作仍会请求确认。") }
            }
        }
        .labelStyle(AssistantMessageActionStyle())
        .buttonStyle(.plain)
        .font(.caption.weight(.medium))
        // 第八轮：强调色（图标与文字），比正文小一档，但触控区仍至少 44pt 高。
        .foregroundStyle(.tint)
        .sensoryFeedback(.success, trigger: copyCount)
    }

    private func copy() {
        UIPasteboard.general.setItems([[UIPasteboard.typeAutomatic: text]], options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(120)])
        copyCount += 1
        copied = true
        let token = copyCount
        Task { @MainActor in
            try? await Task.sleep(for: .seconds(1.5))
            if copyCount == token { copied = false }
        }
    }
}

/// 消息操作按钮的统一标签样式：SF Symbol（小一号）＋ 文字，间距 4pt，整块至少 44pt 高、可点；
/// 按钮自身没有水平内边距，所以按钮行的左右边缘就是第一个／最后一个按钮的图标或文字边缘，能与气泡、正文精确对齐。
struct AssistantMessageActionStyle: LabelStyle {
    static let spacing: CGFloat = 20

    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 4) {
            configuration.icon.imageScale(.small).frame(width: 16)
            configuration.title.lineLimit(1)
        }
        .frame(minHeight: 44)
        .contentShape(.rect)
    }
}

/// 正在执行的任务（第八轮）：单独的视图，只有它随流式输出重绘；需要时跟随滚动到底部。
/// - 没有正文时，回答位置显示一行轻量状态（小图标＋次要色文字＋流光），文字来自服务器的任务状态、phase 与进度事件
///   （排队中 · 前面 N 个／正在开始／连接模型／思考中／调用工具：名称／生成回复／等待补充信息／等待审批），阶段切换时淡入淡出；
///   没有数据的阶段不显示，也不会在已开始执行后停留在“排队中”。
/// - 有正文后状态行缩为正文末尾的一行小字指示。
/// - 等待审批时状态行旁有“审批”，进度订阅中断时有“恢复进度”（原来输入栏上方独立状态条里的操作）。
/// - 第十一轮：正文来自 `AssistantLiveState`（约 12 Hz 提交）；已闭合的块是预解析好的 `stableBlocks`（Equatable 子视图，
///   不变就不重算），每次只重绘最后一块。不再对每个增量 `scrollTo`，改为最多每 250 ms 跟随一次（贴底时）。
private struct AssistantLiveReplyView: View {
    let store: AssistantStore
    let follow: () -> Void
    let approve: () -> Void
    let resume: () -> Void
    @State private var followScheduled = false

    var body: some View {
        let live = store.live
        Group {
            let status = store.liveStatus
            let hasText = live.hasText
            if status != nil || hasText || !live.reasoning.isEmpty {
                VStack(alignment: .leading, spacing: 10) {
                    Label("助手", systemImage: "sparkles").font(.caption.weight(.semibold)).foregroundStyle(.secondary)
                    if let status, !hasText { statusRow(status, compact: false) }
                    if !live.reasoning.isEmpty {
                        DisclosureGroup("推理过程") { Text(AssistantRedaction.text(live.reasoning)).font(.footnote).foregroundStyle(.secondary).textSelection(.enabled).privacySensitive() }
                    }
                    if hasText {
                        VStack(alignment: .leading, spacing: 10) {
                            if !live.stableBlocks.isEmpty {
                                AssistantStableBlocksView(blocks: live.stableBlocks, version: live.stableVersion)
                                    .equatable()
                            }
                            if !live.tailBlocks.isEmpty {
                                AssistantRenderedMarkdownView(blocks: live.tailBlocks)
                            }
                        }
                        .privacySensitive()
                        if let status { statusRow(status, compact: true) }
                    }
                }
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("assistant.live")
            }
        }
        .onChange(of: live.revision) { _, _ in scheduleFollow() }
        .onChange(of: store.liveStatus?.text) { _, _ in follow() }
    }

    /// 节流跟随：一段时间内的多次提交只滚动一次（尾沿触发，保证最后一次提交后也会贴底）。
    private func scheduleFollow() {
        guard !followScheduled else { return }
        followScheduled = true
        Task { @MainActor in
            try? await Task.sleep(for: .milliseconds(250))
            followScheduled = false
            follow()
        }
    }

    @ViewBuilder private func statusRow(_ status: AssistantProgress.Status, compact: Bool) -> some View {
        HStack(spacing: 8) {
            ZStack(alignment: .leading) {
                Label(status.text, systemImage: status.symbol)
                    .labelStyle(AssistantStatusLabelStyle())
                    .font(compact ? .caption2 : .subheadline)
                    .foregroundStyle(compact ? .tertiary : .secondary)
                    .lineLimit(1)
                    .truncationMode(.tail)
                    .assistantShimmer()
                    .id(status.text)
                    .transition(.opacity)
            }
            .animation(.easeInOut(duration: 0.25), value: status.text)
            // 朗读与界面测试读取的是同一段状态文字。
            .accessibilityElement(children: .ignore)
            .accessibilityLabel(status.text)
            .accessibilityAddTraits(.updatesFrequently)
            .accessibilityIdentifier("assistant.live.status")
            if !compact {
                if store.runStatus == "waiting_approval" {
                    Button("审批", systemImage: "checkmark.shield", action: approve)
                        .font(.caption).buttonStyle(.bordered).controlSize(.small)
                }
                if !store.streaming && store.runStatus != "queued" {
                    Button("恢复进度", systemImage: "arrow.clockwise", action: resume)
                        .font(.caption).buttonStyle(.bordered).controlSize(.small)
                }
            }
        }
        .frame(minHeight: compact ? 0 : 28)
    }
}

/// 流式回答里已闭合的块：按 `version` 比较，块没有增加时整段跳过重算（第十一轮）。
private struct AssistantStableBlocksView: View, Equatable {
    let blocks: [AssistantRenderedBlock]
    let version: Int

    nonisolated static func == (lhs: AssistantStableBlocksView, rhs: AssistantStableBlocksView) -> Bool {
        lhs.version == rhs.version
    }

    var body: some View { AssistantRenderedMarkdownView(blocks: blocks) }
}

/// 状态行的图标与文字：图标小一号、与文字间距 6pt。
private struct AssistantStatusLabelStyle: LabelStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 6) {
            configuration.icon.imageScale(.small).symbolEffect(.pulse, options: .repeating, isActive: AssistantShimmer.animated)
            configuration.title
        }
    }
}

/// 流光：一条斜向高光从左到右扫过文字（只作用于文字本身的形状）。减少动态效果时不动。
/// 界面测试中关闭（无限循环的动画会让 XCTest 一直等待界面空闲）。
struct AssistantShimmer: ViewModifier {
    nonisolated static let animated = !ProcessInfo.processInfo.arguments.contains("--ui-testing")
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    @State private var phase: CGFloat = 0

    @ViewBuilder func body(content: Content) -> some View {
        if reduceMotion || !Self.animated {
            content
        } else {
            content
                .overlay {
                    GeometryReader { geometry in
                        let width = geometry.size.width
                        LinearGradient(colors: [.clear, .white.opacity(0.75), .clear], startPoint: .leading, endPoint: .trailing)
                            .frame(width: max(40, width * 0.45))
                            .offset(x: -width * 0.5 + phase * width * 1.5)
                    }
                    .mask(content)
                    .allowsHitTesting(false)
                    .accessibilityHidden(true)
                }
                .onAppear {
                    withAnimation(.linear(duration: 1.6).repeatForever(autoreverses: false)) { phase = 1 }
                }
        }
    }
}

extension View {
    func assistantShimmer() -> some View { modifier(AssistantShimmer()) }
}

// MARK: - 待处理消息（第八轮）

/// 上一轮未结束时发出的消息：虚线描边的待处理气泡（贴右，与我的消息同一位置），下方一行次要色小字显示真实状态，
/// 再下方三个小胶囊选择处理方式——「排队」（默认、已选中）、「插入调整」、「停止并替换」（需确认）。
/// - 排队：服务器排队项（GET /queue）；插入调整：POST /queue/{id}/steer（原子地取消排队并作为当前任务的调整说明）；
///   停止并替换：POST /queue/{id}/replace（原子地请求停止当前任务并把这条排到队首）。每次只提交一次、不自动重试，
///   失败时重新读取队列显示真实状态。改为插入调整／停止并替换后不能再改回（服务器已执行）。
/// - 轮到这条消息执行时它离开队列、进入正式消息历史（实线气泡），按钮消失；插入调整的内容被服务器并入当前任务
///   （收到 steer_applied 后变为实线），不单独存为历史消息，当前任务结束后不再单独显示。
private struct AssistantPendingMessagesView: View {
    @Environment(AppModel.self) private var model
    let store: AssistantStore

    var body: some View {
        let pending = store.pendingQueue
        let ahead = store.runActive ? 1 : 0
        ForEach(store.steered) { item in
            AssistantPendingBubble(text: item.content, pending: !item.applied,
                                   status: item.applied ? "已插入当前任务" : "将插入当前任务",
                                   mode: .adjust, busy: false, canAdjust: false, canReplace: false,
                                   choose: { _ in })
                .id("steered-" + item.id)
        }
        ForEach(Array(pending.enumerated()), id: \.element.id) { index, item in
            let replacing = store.replacingTurnIDs.contains(item.id)
            let paused = item.text("status") == "paused"
            AssistantPendingBubble(
                text: item.text("content"), pending: true,
                status: replacing ? "等待停止当前任务…" : paused ? "排队已暂停，当前任务结束后需要继续" : "排队中 · 前面 \(index + ahead) 条",
                mode: replacing ? .replace : .queue,
                busy: store.queueBusyID == item.id,
                canAdjust: !replacing && store.runStatus == "running" && store.activeRun?["cancel_requested"]?.boolValue != true,
                canReplace: !replacing && store.runActive && store.activeRun?["cancel_requested"]?.boolValue != true
            ) { mode in
                Task {
                    switch mode {
                    case .adjust: await store.steerQueued(item, model: model)
                    case .replace: await store.replaceWithQueued(item, model: model)
                    case .queue: break
                    }
                }
            }
            .id("queued-" + item.id)
        }
    }
}

enum AssistantPendingMode: String {
    case queue, adjust, replace

    var title: String {
        switch self {
        case .queue: "排队"
        case .adjust: "插入调整"
        case .replace: "停止并替换"
        }
    }

    var symbol: String {
        switch self {
        case .queue: "text.badge.plus"
        case .adjust: "arrow.turn.down.right"
        case .replace: "arrow.triangle.2.circlepath"
        }
    }
}

private struct AssistantPendingBubble: View {
    let text: String
    /// 待处理（虚线描边）；开始执行或已并入当前任务后为实线、没有选择按钮。
    let pending: Bool
    let status: String
    let mode: AssistantPendingMode
    let busy: Bool
    let canAdjust: Bool
    let canReplace: Bool
    let choose: (AssistantPendingMode) -> Void
    @State private var confirmReplace = false

    var body: some View {
        VStack(alignment: .trailing, spacing: 6) {
            Text(AssistantRedaction.text(text))
                .textSelection(.enabled)
                .privacySensitive()
                .padding(14)
                .background {
                    let shape = RoundedRectangle(cornerRadius: 16)
                    if pending {
                        shape.strokeBorder(Color.accentColor.opacity(0.55), style: StrokeStyle(lineWidth: 1.2, dash: [5, 4]))
                    } else {
                        shape.fill(Color.accentColor.opacity(0.08))
                    }
                }
                .accessibilityElement(children: .combine)
                .accessibilityValue(pending ? "待处理" : "")
                .accessibilityIdentifier(pending ? "assistant.message.pending" : "assistant.message.bubble")
            HStack(spacing: 6) {
                if busy { ProgressView().controlSize(.mini) }
                Text(status)
            }
            .font(.caption)
            .foregroundStyle(.secondary)
            .accessibilityElement(children: .combine)
            .accessibilityIdentifier("assistant.queue.status")
            if pending {
                HStack(spacing: 8) {
                    chip(.queue, enabled: false)
                    chip(.adjust, enabled: canAdjust && !busy)
                    chip(.replace, enabled: canReplace && !busy)
                }
            }
        }
        .padding(.leading, 32)
        .frame(maxWidth: .infinity, alignment: .trailing)
        .animation(.easeInOut(duration: 0.2), value: pending)
        .animation(.easeInOut(duration: 0.2), value: status)
    }

    private func chip(_ option: AssistantPendingMode, enabled: Bool) -> some View {
        let selected = option == mode
        return Button {
            if option == .replace { confirmReplace = true } else { choose(option) }
        } label: {
            Label(option.title, systemImage: option.symbol)
                .labelStyle(AssistantMessageActionStyle.Chip())
                .font(.caption.weight(selected ? .semibold : .regular))
                .foregroundStyle(selected ? AnyShapeStyle(.tint) : AnyShapeStyle(.secondary))
                .padding(.horizontal, 10)
                .frame(height: 28)
                .background(Capsule().fill(selected ? AnyShapeStyle(Color.accentColor.opacity(0.14)) : AnyShapeStyle(Color(uiColor: .tertiarySystemFill))))
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        // 已选中的那一项保持可读（不置灰），其余在不可用时置灰。
        .disabled(!enabled)
        .opacity(enabled || selected ? 1 : 0.45)
        .accessibilityValue(selected ? "已选中" : "")
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier("assistant.queue.\(option.rawValue)")
        .confirmationDialog("停止当前任务并改为执行这条消息？", isPresented: option == .replace ? $confirmReplace : .constant(false), titleVisibility: .visible) {
            Button("确认停止并替换", role: .destructive) { choose(.replace) }
        } message: { Text("服务器会停止当前任务，并接着执行这条消息。已经执行的操作不会撤销。") }
    }
}

extension AssistantMessageActionStyle {
    /// 待处理气泡下的小胶囊：小图标＋文字。
    struct Chip: LabelStyle {
        func makeBody(configuration: Configuration) -> some View {
            HStack(spacing: 4) {
                configuration.icon.imageScale(.small)
                configuration.title.lineLimit(1)
            }
        }
    }
}

// MARK: - 本轮详情

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

    /// 折叠时的第一行：供应商名 · 模型名（没有用量数据时为“本轮详情”）。
    var modelLine: String { model ?? (hasUsage ? "本轮用量" : "本轮详情") }

    /// 折叠时的第二行：输入/输出词元 · 工具调用 · 重试 · 耗时；没有任何数字时为 nil。
    var metricsLine: String? {
        var bits: [String] = []
        if inputTokens != nil || outputTokens != nil {
            bits.append("输入 \(inputTokens.map(AIPresentation.tokens) ?? "–") / 输出 \(outputTokens.map(AIPresentation.tokens) ?? "–") 词元")
        }
        if let toolCalls { bits.append("工具 \(toolCalls) 次") }
        if let retries { bits.append("重试 \(retries) 次") }
        if let elapsedMS, elapsedMS > 0 { bits.append("耗时 " + AIPresentation.latency(elapsedMS)) }
        return bits.isEmpty ? nil : bits.joined(separator: " · ")
    }

    /// 无障碍朗读用的完整摘要。
    var summaryLine: String { [modelLine, metricsLine].compactMap { $0 }.joined(separator: "，") }
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
                // 两行：第一行供应商 · 模型；第二行词元、工具调用次数与耗时。
                Label {
                    VStack(alignment: .leading, spacing: 2) {
                        Text(meta.modelLine)
                            .lineLimit(1)
                            .truncationMode(.middle)
                            .accessibilityIdentifier("assistant.turnDetails.model")
                        if let metrics = meta.metricsLine {
                            Text(metrics)
                                .lineLimit(1)
                                .truncationMode(.tail)
                                .monospacedDigit()
                                .foregroundStyle(.tertiary)
                                .accessibilityIdentifier("assistant.turnDetails.metrics")
                        }
                    }
                } icon: {
                    Image(systemName: meta.hasUsage ? "chart.bar.doc.horizontal" : "wrench.and.screwdriver")
                }
                .font(.caption)
                .foregroundStyle(.secondary)
                .accessibilityElement(children: .ignore)
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
                        if !images.isEmpty { AssistantImagesView(images: images, owner: "tools-\(tools.first?.id ?? "")") }
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
    /// 所属消息，用作解码缓存的键。
    var owner = "live"
    var body: some View {
        ForEach(Array(images.enumerated()), id: \.offset) { index, item in
            if let dataURL = item["data_url"]?.stringValue, dataURL.hasPrefix("data:") {
                AssistantDataImageView(cacheKey: "\(owner)#\(index)#\(dataURL.count)", dataURL: dataURL).privacySensitive()
            } else if item["data_url_omitted"]?.boolValue == true {
                // 本机缓存不保存内联图片（第十一轮）：如实说明，读取到服务器数据后显示原图。
                Label("图片未缓存，连接服务器后显示", systemImage: "photo")
                    .font(.caption).foregroundStyle(.secondary)
                    .accessibilityIdentifier("assistant.image.notCached")
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
    @State private var searching = false
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
            .pageSearch(text: $query, isActive: $searching, prompt: "搜索会话标题", identifier: "assistant.sessions.search")
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
        // 确认框挂在被删除的那一行上（iOS 26+ 以 popover 锚定在所修饰的视图）；
        // 行在 ForEach 内重复实例化，只让 pendingDelete 对应的那一行绑定为真。
        .confirmationDialog(
            "删除这个会话？",
            isPresented: Binding(get: { pendingDelete?.id == item.id }, set: { if !$0 { pendingDelete = nil } }),
            titleVisibility: .visible,
            presenting: pendingDelete
        ) { _ in
            // 用本行自己的会话 ID：点按时 SwiftUI 会先把 pendingDelete 置空，不依赖呈现数据。
            Button("删除会话", role: .destructive) {
                let id = item.id
                pendingDelete = nil
                Task { await store.deleteSession(id: id, model: model) }
            }
        } message: { item in
            Text("“\(AssistantRedaction.text(item.text("title").nilIfEmpty ?? "新会话"))”及其全部消息会从服务器删除，此操作不可撤销。")
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
                    Button(store.session?.assistantText("status") == "archived" ? "恢复会话" : "归档会话", systemImage: store.session?.assistantText("status") == "archived" ? "tray.and.arrow.up" : "archivebox") {
                        Task {
                            await store.updateSession(["status": .string(store.session?.assistantText("status") == "archived" ? "active" : "archived")], model: model)
                            dismiss()
                        }
                    }.disabled(store.busy || store.runActive)
                    Button("删除会话", systemImage: "trash", role: .destructive) { confirmDelete = true }.disabled(store.busy || store.runActive)
                        .confirmationDialog("删除此会话及历史？", isPresented: $confirmDelete, titleVisibility: .visible) {
                            Button("删除会话", role: .destructive) {
                                Task {
                                    guard let id = store.sessionID else { return }
                                    if await store.deleteSession(id: id, model: model) { dismiss() }
                                }
                            }
                        } message: { Text("此操作不可撤销。") }
                } footer: { Text("运行中的任务结束后才能归档或删除会话。") }
                if let error = store.error { Section { Text(error).foregroundStyle(.red) } }
            }
            .navigationTitle("会话设置").navigationBarTitleDisplayMode(.inline)
            .toolbar { ToolbarItem(placement: .confirmationAction) { Button("完成") { dismiss() } } }
            .onAppear { title = AssistantRedaction.text(originalTitle) }
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

struct AssistantCodeBlockView: View {
    let language: String
    let code: String
    @State private var copied = false

    /// 「已复制」提示停留时长。界面测试里放长：XCTest 点按后要先等界面空闲（长会话里实测 5 秒以上）
    /// 才开始查找，2 秒的提示在查找前就已恢复成「复制」，断言会落空。
    nonisolated static let copiedFeedback: Duration =
        ProcessInfo.processInfo.arguments.contains("--ui-testing") ? .seconds(15) : .seconds(2)

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            HStack {
                Label(language.isEmpty ? "代码" : language, systemImage: "chevron.left.forwardslash.chevron.right")
                    .font(.caption.weight(.medium)).foregroundStyle(.secondary)
                Spacer()
                // 第九轮：右上角小「复制」按钮（图标＋文字，触控至少 44pt）。
                Button(copied ? "已复制" : "复制", systemImage: copied ? "checkmark" : "doc.on.doc") {
                    UIPasteboard.general.setItems([[UIPasteboard.typeAutomatic: code]], options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(120)])
                    copied = true
                }
                .labelStyle(.titleAndIcon).font(.caption.weight(.medium))
                .buttonStyle(.plain).foregroundStyle(.tint)
                .padding(.horizontal, 8)
                .frame(minWidth: 44, minHeight: 44)
                .contentShape(.rect)
                .accessibilityLabel(copied ? "已复制代码" : "复制代码")
                .accessibilityIdentifier("assistant.code.copy")
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
            try? await Task.sleep(for: Self.copiedFeedback)
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
