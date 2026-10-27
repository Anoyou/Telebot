import PhotosUI
import SwiftUI
import TelePilotCore
import UniformTypeIdentifiers

struct AssistantAttachment: Identifiable {
    let id = UUID()
    let image: UIImage
    let value: JSONValue
}

/// 助手底部输入栏（第八轮恢复 iOS 26 原生 Liquid Glass）。
/// - 上方是多行 `TextField`（1–8 行自动增高）；下方一行工具栏：左下「+」添加附件，紧挨着的是模型胶囊
///   （显示「供应商 · 模型」全名，过长时中间省略，可变宽），右下发送按钮（任务执行中且输入为空时变为停止）。
/// - 「+」与模型都是普通 `Button`，点按后由 `AssistantComposerPanelHost` 在输入栏上方弹出玻璃浮层卡片，
///   不用系统 `Menu`：Menu 收回时会把菜单“变形”回玻璃输入栏里的标签（第六、七轮的根因）。浮层与输入栏布局完全解耦，
///   只读取按钮与输入栏的位置来摆放卡片和水滴尖，展开／收起期间输入栏里的任何控件都不移动。
/// - 第八轮去掉了执行中的“发送方式”按钮：上一轮未结束时发出的消息先进入排队，在对话里的待处理气泡下选择
///   排队／插入调整／停止并替换；任务等待补充信息时，发出的消息自动作为补充信息提交。
/// - 通过 `safeAreaInset(edge: .bottom)` 挂在对话下方，贴底部安全区并随键盘上移。
/// - 草稿与附件是本视图自己的状态：打字只重绘输入栏，不会让上方的对话历史重新计算。
struct AssistantComposer: View {
    @Environment(AppModel.self) private var model
    @Bindable var store: AssistantStore
    @Bindable var panel: AssistantComposerPanelState
    var focused: FocusState<Bool>.Binding
    @State private var draft = ""
    @State private var photos: [PhotosPickerItem] = []
    @State private var pickingPhotos = false
    @State private var attachments: [AssistantAttachment] = []
    @State private var importingImage = false
    @State private var loadingImage = false
    @State private var confirmStop = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    nonisolated static let cornerRadius: CGFloat = 26

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            if !attachments.isEmpty {
                attachmentStrip.padding(.horizontal, 12).padding(.top, 8)
            }
            TextField(placeholder, text: $draft, axis: .vertical)
                .lineLimit(1...8)
                .focused(focused)
                // 请求进行中也允许继续输入，发送按钮会等待服务器确认后再可用。
                .disabled(readOnlyReason != nil)
                .padding(.horizontal, 16).padding(.top, 14).padding(.bottom, 4)
                .accessibilityIdentifier("assistant.composer")
            toolbarRow
                .padding(.horizontal, 8).padding(.bottom, 6)
        }
        // 第八轮：恢复原生 Liquid Glass（透出后面的对话）。输入栏里没有系统 Menu，浮层也不在这个玻璃容器里，
        // 所以不会再出现菜单从玻璃标签“变形”弹出／收回的错位。
        .glassEffect(.regular, in: .rect(cornerRadius: Self.cornerRadius, style: .continuous))
        // 输入栏卡片的位置交给浮层宿主（只读，不参与布局），用于让浮层底边贴近输入栏上沿。
        .onGeometryChange(for: CGRect.self) { $0.frame(in: .named(AssistantComposerPanelState.space)) } action: { panel.composerFrame = $0 }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("assistant.composerBar")
        .padding(.horizontal, 12).padding(.top, 6).padding(.bottom, 8)
        .frame(maxWidth: 900)
        .frame(maxWidth: .infinity)
        // 点进输入框（开始打字）时收起浮层；浮层打开时不收键盘，避免输入栏随键盘移动。
        .onChange(of: focused.wrappedValue) { _, isFocused in
            if isFocused && panel.open != nil { panel.close(animated: !reduceMotion) }
        }
        .onChange(of: panel.photoRequest) { _, _ in pickingPhotos = true }
        .onChange(of: panel.fileRequest) { _, _ in importingImage = true }
        .photosPicker(isPresented: $pickingPhotos, selection: $photos, maxSelectionCount: max(1, 4 - attachments.count), matching: .images)
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
    }

    // MARK: 附件

    private var attachmentStrip: some View {
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

    // MARK: 输入框下方的工具栏

    /// 「+」固定在左下、发送固定在右下；模型胶囊紧挨「+」、可变宽，由中间的 Spacer 吸收宽度变化，
    /// 文字过长时中间省略，不会挤动「+」与发送按钮。
    private var toolbarRow: some View {
        HStack(spacing: 4) {
            attachMenu
            AssistantModelMenu(store: store, panel: panel)
            Spacer(minLength: 8)
            if draft.count > 30_000 {
                Text("\(draft.count)/32000").font(.caption).monospacedDigit()
                    .foregroundStyle(draft.count > 32_000 ? .red : .secondary)
            }
            if showsStop { stopButton } else { sendButton }
        }
    }

    /// 左下角「+」：点按在输入栏上方弹出附件浮层（只列出服务器真实支持的附件：图片，最多 4 张）。
    /// 打开时「+」旋转成“×”并降到约 40% 不透明度保留在原位（再点一次收起），收起后恢复；不改变尺寸与位置。
    private var attachMenu: some View {
        let isOpen = panel.open == .attach
        return Button { panel.toggle(.attach, animated: !reduceMotion) } label: {
            Image(systemName: "plus")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.primary)
                .rotationEffect(.degrees(isOpen ? 45 : 0))
                .frame(width: 34, height: 34)
                .background(Circle().fill(.fill.tertiary))
                .composerSourceDimmed(isOpen, reduceMotion: reduceMotion)
                .composerMenuLabel(width: 44)
        }
        .buttonStyle(.plain)
        .fixedSize()
        .disabled(attachments.count >= 4 || loadingImage || readOnlyReason != nil)
        .onGeometryChange(for: CGRect.self) { $0.frame(in: .named(AssistantComposerPanelState.space)) } action: { panel.anchors[.attach] = $0 }
        .accessibilityLabel(isOpen ? "收起添加图片" : "添加图片")
        .accessibilityHint("可从照片或文件选择图片，最多 4 张")
        .accessibilityValue(isOpen ? "已展开" : "")
        .accessibilityIdentifier("assistant.attach")
    }

    private var sendButton: some View {
        Button(action: submit) {
            ZStack {
                if store.busy || loadingImage {
                    ProgressView().controlSize(.small).tint(.white)
                } else {
                    Image(systemName: "arrow.up")
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
        .accessibilityLabel(store.busy ? "正在提交" : store.runActive && store.runStatus != "waiting_input" ? "发送消息（加入排队）" : "发送消息")
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
        .confirmationDialog("停止当前任务？", isPresented: $confirmStop, titleVisibility: .visible) {
            Button("提交停止请求", role: .destructive) { Task { await store.cancel(model) } }
        } message: { Text("已执行的操作不会撤销。关闭窗口不会停止服务器任务。") }
    }

    // MARK: 状态

    private var trimmedDraft: String { draft.trimmingCharacters(in: .whitespacesAndNewlines) }

    /// 任务等待补充信息时，发出的消息自动作为补充信息提交（不再手选“补充信息”）。
    private var answersInput: Bool { store.runStatus == "waiting_input" }

    private var sendDisabled: Bool {
        !store.canSend || store.sessionID == nil || loadingImage
            || (trimmedDraft.isEmpty && attachments.isEmpty) || draft.count > 32_000
            || (answersInput && trimmedDraft.isEmpty)
    }

    private var readOnlyReason: String? {
        if store.session?.assistantText("channel") == "bot" { return "Telegram 会话为只读，请在 Telegram 中继续" }
        if store.session?.assistantText("status") == "archived" { return "已归档的会话为只读，恢复后可继续" }
        return nil
    }

    private var placeholder: String {
        if let reason = readOnlyReason { return reason }
        if store.sessionID == nil { return "请先新建会话" }
        if answersInput { return "回答助手的问题…" }
        return "询问或安排任务…"
    }

    /// 执行中且输入为空时，右下角按钮是“停止”；输入了内容则仍是发送（加入排队）。
    private var showsStop: Bool { store.runActive && trimmedDraft.isEmpty && attachments.isEmpty }

    // MARK: 发送

    private func submit() {
        guard !sendDisabled else { return }
        let original = draft
        let text = original.trimmingCharacters(in: .whitespacesAndNewlines)
        let sent = attachments
        let images = sent.map(\.value)
        let mode = answersInput ? "input" : "message"
        let sessionID = store.sessionID
        Task {
            if await store.send(text, attachments: images, model: model, mode: mode) {
                guard store.sessionID == sessionID else { return }
                // 等待服务器确认期间允许继续输入：只移除已发出的内容，保留新输入的部分。
                if draft == original { draft = "" }
                else if draft.hasPrefix(original) { draft = String(draft.dropFirst(original.count)) }
                let sentIDs = Set(sent.map(\.id))
                attachments.removeAll { sentIDs.contains($0.id) }
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

/// 输入栏的本轮模型胶囊：显示「供应商 · 模型」全名（与回答末尾的模型名同一格式），过长时中间省略；
/// 点按弹出模型浮层（`AssistantComposerPanelHost`）。浮层打开时降到约 40% 不透明度保留在原位。
///
/// - 选中值先落在本视图的 `selection`，界面只读它；写回 store 的 `modelIndex` 不会让其它视图重绘
///   （输入栏不读取 `modelIndex`，发送时才通过 `selectedModel` 读取）。
struct AssistantModelMenu: View {
    @Bindable var store: AssistantStore
    @Bindable var panel: AssistantComposerPanelState
    @State private var selection = -1
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        let models = store.models
        let isOpen = panel.open == .model
        Button { panel.toggle(.model, animated: !reduceMotion) } label: {
            HStack(spacing: 4) {
                Self.chipText(models: models, index: selection)
                    .lineLimit(1)
                    .truncationMode(.middle)
                Image(systemName: "chevron.up.chevron.down").imageScale(.small).foregroundStyle(.secondary)
            }
            .font(.subheadline)
            .foregroundStyle(.primary)
            .padding(.horizontal, 12)
            .frame(height: 34)
            .background(Capsule().fill(.fill.tertiary))
            .composerSourceDimmed(isOpen, reduceMotion: reduceMotion)
            .frame(minHeight: 44)
            .contentShape(.rect)
            .transaction { $0.animation = nil }
        }
        .buttonStyle(.plain)
        .onGeometryChange(for: CGRect.self) { $0.frame(in: .named(AssistantComposerPanelState.space)) } action: { panel.anchors[.model] = $0 }
        .accessibilityLabel("本轮模型：\(Self.fullName(models: models, index: selection))")
        .accessibilityValue(isOpen ? "已展开" : "")
        .accessibilityIdentifier("assistant.model")
        .onAppear { selection = store.modelIndex }
        .onChange(of: store.modelIndex) { _, value in
            if selection != value { selection = value }
        }
    }

    /// 胶囊文字：供应商加粗＋“ · 完整模型名”；自动时为“自动”。
    static func chipText(models: [JSONValue], index: Int) -> Text {
        guard models.indices.contains(index) else { return Text("自动") }
        let provider = models[index].assistantText("provider_name")
        let name = models[index].assistantText("model")
        guard !provider.isEmpty else { return Text(verbatim: name) }
        return Text("\(Text(verbatim: provider).fontWeight(.semibold)) · \(Text(verbatim: name))")
    }

    /// 模型短名：去掉 “厂商/” 前缀，例如 `openai/gpt-4o` 显示为 `gpt-4o`。
    static func shortName(models: [JSONValue], index: Int) -> String {
        guard models.indices.contains(index) else { return "自动" }
        let name = models[index].assistantText("model")
        return name.split(separator: "/").last.map(String.init) ?? name
    }

    /// 「供应商 · 模型」全名（与回答末尾 `AssistantRunMeta.model` 同一格式）；自动时为“自动”。
    static func fullName(models: [JSONValue], index: Int) -> String {
        guard models.indices.contains(index) else { return "自动" }
        return providerModel(models[index])
    }

    static func providerModel(_ item: JSONValue) -> String {
        [item.assistantText("provider_name"), item.assistantText("model")].filter { !$0.isEmpty }.joined(separator: " · ")
    }
}

// MARK: - 输入栏浮层（第七轮自绘，第八轮改为玻璃卡片＋水滴尖）

/// 输入栏上方的浮层：「+」附件、本轮模型两个选择器共用。
enum AssistantComposerPanel: Hashable {
    case attach, model
}

/// 浮层状态：由 `AssistantView` 持有，输入栏（按钮）与浮层宿主（卡片）共享。
/// 附件动作通过递增请求计数交给输入栏执行（系统照片选择器与文件选择器挂在输入栏上）。
/// `anchors`／`composerFrame` 是按钮与输入栏卡片在助手页坐标空间里的位置，只供浮层摆放卡片与尖角，不参与任何布局。
@MainActor @Observable
final class AssistantComposerPanelState {
    var open: AssistantComposerPanel?
    /// 最近一次打开的浮层：收起动画期间用它定位卡片。
    var lastOpened: AssistantComposerPanel = .attach
    var photoRequest = 0
    var fileRequest = 0
    var anchors: [AssistantComposerPanel: CGRect] = [:]
    var composerFrame: CGRect?

    /// 助手页的命名坐标空间（输入栏与浮层宿主共同的祖先上声明）。
    nonisolated static let space = "assistant.composerSpace"
    static let animation: Animation = .snappy(duration: 0.22)

    func toggle(_ panel: AssistantComposerPanel, animated: Bool) {
        if open != panel { lastOpened = panel }
        withAnimation(animated ? Self.animation : nil) { open = open == panel ? nil : panel }
    }

    func close(animated: Bool) {
        withAnimation(animated ? Self.animation : nil) { open = nil }
    }
}

/// 浮层宿主：挂在助手页对话区域的 `.overlay`（`safeAreaInset` 之前），与输入栏没有任何布局关联
/// （不在玻璃输入栏里、不参与输入栏布局、没有 matchedGeometry／glassEffectID），展开／收起只有卡片自己形变，
/// 输入栏、按钮、文本框都不移动。
/// - 卡片是原生 Liquid Glass，底部带同材质的“水滴尖”指向触发按钮中心；卡片底边贴近输入栏上沿（尖角落在按钮上方），
///   看起来像从按钮处弹出。附件卡片左缘对齐「+」，模型卡片按模型胶囊位置摆放（不超出屏幕边距）。
/// - 点卡片外（对话区域）收起；打开时不收键盘：收键盘会让输入栏随键盘下移。
struct AssistantComposerPanelHost: View {
    @Bindable var store: AssistantStore
    @Bindable var panel: AssistantComposerPanelState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// 水滴尖高度与输入栏上沿之间的留白。
    static let tailHeight: CGFloat = 10
    static let gap: CGFloat = 2
    static let margin: CGFloat = 12

    var body: some View {
        GeometryReader { proxy in
            let host = proxy.frame(in: .named(AssistantComposerPanelState.space))
            ZStack(alignment: .topLeading) {
                if panel.open != nil {
                    // 透明的点按区域：点卡片外收起（背景不变暗）。
                    Rectangle().fill(.clear)
                        .contentShape(.rect)
                        .onTapGesture { panel.close(animated: !reduceMotion) }
                        .accessibilityElement()
                        .accessibilityLabel("收起")
                        .accessibilityAddTraits(.isButton)
                        .accessibilityAction { panel.close(animated: !reduceMotion) }
                        .accessibilityIdentifier("assistant.panel.dismiss")
                        .transition(.opacity)
                }
                // 定位容器始终存在，卡片是其中直接插入／移除的视图，过渡（形变）作用在卡片本身、锚点在尖角处。
                // 收起动画期间 `open` 已为 nil，定位沿用 `lastOpened`。
                let shown = panel.open ?? panel.lastOpened
                let place = layout(for: shown, host: host)
                ZStack(alignment: .bottomLeading) {
                    if let open = panel.open {
                        card(open, width: place.width, tailX: place.tailX)
                            .id(open)
                            .transition(reduceMotion ? .opacity : .modifier(
                                active: AssistantPanelMorph(progress: 0, anchor: UnitPoint(x: place.tailX / place.width, y: 1)),
                                identity: AssistantPanelMorph(progress: 1, anchor: UnitPoint(x: place.tailX / place.width, y: 1))))
                    }
                }
                .frame(width: place.width)
                .frame(maxHeight: .infinity, alignment: .bottom)
                // 底边（尖角尖端）落在输入栏卡片上沿之上 `gap`；可能略低于宿主底边（宿主底边在输入栏外边距之上），负内边距即可。
                .padding(.bottom, place.bottomInset)
                .padding(.leading, place.minX)
            }
            .frame(width: proxy.size.width, height: proxy.size.height, alignment: .topLeading)
        }
    }

    struct Layout {
        var minX: CGFloat
        var width: CGFloat
        var tailX: CGFloat
        var bottomInset: CGFloat
    }

    /// 由按钮与输入栏卡片的位置（只读）算出卡片的左缘、宽度、尖角位置与底部内边距（均为宿主内坐标）。
    /// 还没测到位置时（首帧）退回第七轮的固定边距。
    private func layout(for open: AssistantComposerPanel, host: CGRect) -> Layout {
        let available = max(200, host.width - Self.margin * 2)
        let width = min(open == .model ? 320 : 260, available)
        let radius = AssistantPanelBubble.radius
        let bottomInset: CGFloat = panel.composerFrame.map { host.maxY - $0.minY + Self.gap } ?? -4
        guard let anchor = panel.anchors[open] else {
            let minX = open == .model ? max(Self.margin, host.width - Self.margin - width) : 20
            return Layout(minX: minX, width: width, tailX: open == .model ? width - 40 : 22, bottomInset: bottomInset)
        }
        let anchorMinX = anchor.minX - host.minX
        let anchorMidX = anchor.midX - host.minX
        let preferred = open == .attach ? anchorMinX : anchorMinX - 8
        let minX = min(max(Self.margin, preferred), host.width - Self.margin - width)
        let tailX = min(max(anchorMidX - minX, radius * 0.5 + AssistantPanelBubble.tailHalfWidth), width - radius * 0.5 - AssistantPanelBubble.tailHalfWidth)
        return Layout(minX: minX, width: width, tailX: tailX, bottomInset: bottomInset)
    }

    @ViewBuilder private func card(_ open: AssistantComposerPanel, width: CGFloat, tailX: CGFloat) -> some View {
        let rows: AnyView = switch open {
        case .attach: AnyView(attachRows)
        case .model: AnyView(modelRows)
        }
        let shape = AssistantPanelBubble(tailX: tailX, tailHeight: Self.tailHeight)
        ViewThatFits(in: .vertical) {
            rows
            ScrollView { rows }.frame(maxHeight: 380)
        }
        .padding(6)
        .padding(.bottom, Self.tailHeight)
        .frame(width: width)
        // 原生 Liquid Glass，卡片与水滴尖是同一个形状、同一块玻璃。
        .glassEffect(.regular, in: shape)
        .contentShape(shape)
        .overlay(alignment: .bottomLeading) {
            // 界面测试用的尖角定位标记（不可见、不参与布局；VoiceOver 不读）。
            if AssistantPanelBubble.exposesTail {
                Rectangle().fill(Color.white.opacity(0.001))
                    .frame(width: AssistantPanelBubble.tailHalfWidth * 2, height: Self.tailHeight)
                    .offset(x: tailX - AssistantPanelBubble.tailHalfWidth)
                    .allowsHitTesting(false)
                    .accessibilityElement()
                    .accessibilityLabel("浮层尖角")
                    .accessibilityIdentifier("assistant.panel.tail")
            }
        }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(open == .attach ? "assistant.panel.attach" : "assistant.panel.model")
        .accessibilityAction(.escape) { panel.close(animated: !reduceMotion) }
    }

    // MARK: 附件

    /// 目前服务器只支持图片附件；以后新增类型在这里加一行。
    private var attachRows: some View {
        VStack(spacing: 0) {
            AssistantPanelRow(title: "从照片选择", systemImage: "photo", identifier: "assistant.attach.photos", circledIcon: true) {
                panel.close(animated: !reduceMotion)
                panel.photoRequest += 1
            }
            AssistantPanelRow(title: "从文件选择图片", systemImage: "folder", identifier: "assistant.attach.file", circledIcon: true) {
                panel.close(animated: !reduceMotion)
                panel.fileRequest += 1
            }
        }
    }

    // MARK: 模型

    /// 每行标题是模型短名（主色加粗），副标题一律是「供应商 · 模型」全名；“自动”的副标题是服务器当前路由到的模型（能读到时）。
    private var modelRows: some View {
        let models = store.models
        let current = store.modelIndex
        let routed = Self.routedModel(store.capabilities)
        return VStack(spacing: 0) {
            AssistantPanelRow(title: "自动", subtitle: routed ?? "使用服务器配置", systemImage: "sparkles",
                              selected: !models.indices.contains(current),
                              accessibilityLabel: "自动，\(routed.map { "当前路由到 \($0)" } ?? "使用服务器配置")",
                              identifier: "assistant.model.option.auto", circledIcon: true) { select(-1) }
            ForEach(Array(models.enumerated()), id: \.offset) { index, item in
                let full = AssistantModelMenu.providerModel(item)
                AssistantPanelRow(title: AssistantModelMenu.shortName(models: models, index: index),
                                  subtitle: full, systemImage: "cpu",
                                  selected: current == index,
                                  accessibilityLabel: full,
                                  identifier: "assistant.model.option.\(index)", circledIcon: true) { select(index) }
            }
        }
    }

    /// 服务器能力里的当前路由（provider_name ＋ resolved_model），没有时为 nil。
    static func routedModel(_ capabilities: JSONValue) -> String? {
        let provider = capabilities.assistantText("provider_name")
        let model = capabilities.assistantText("resolved_model")
        guard !model.isEmpty else { return nil }
        return [provider, model].filter { !$0.isEmpty }.joined(separator: " · ")
    }

    private func select(_ index: Int) {
        if store.modelIndex != index { store.modelIndex = index }
        panel.close(animated: !reduceMotion)
    }
}

/// 浮层里的一行：图标、标题（主色加粗，可带一行「供应商 · 模型」副标题）；当前项浅色强调底＋右侧对勾。
/// 整行可点，高度至少 48pt。
struct AssistantPanelRow: View {
    let title: String
    var subtitle: String?
    let systemImage: String
    var selected = false
    var accessibilityLabel: String?
    let identifier: String
    var circledIcon = false
    let action: () -> Void

    var body: some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: systemImage)
                    .font(.body)
                    .foregroundStyle(.primary)
                    .frame(width: circledIcon ? 32 : 24, height: circledIcon ? 32 : 24)
                    .background { if circledIcon { Circle().fill(.fill.tertiary) } }
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.body.weight(.semibold)).foregroundStyle(.primary).lineLimit(1).truncationMode(.middle)
                    if let subtitle {
                        Text(subtitle).font(.footnote).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    }
                }
                Spacer(minLength: 8)
                if selected {
                    Image(systemName: "checkmark").font(.body.weight(.semibold)).foregroundStyle(.tint)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(minHeight: 48)
            .background {
                if selected { RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Color.accentColor.opacity(0.12)) }
            }
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(accessibilityLabel ?? title)
        .accessibilityValue(selected ? "已选中" : "")
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier(identifier)
    }
}

/// 浮层卡片的形状：圆角矩形，底边带一个圆滑的“水滴尖”（`tailX` 处，向下 `tailHeight`）。
/// 尖角的根部略微伸进卡片内部，与圆角过渡自然；卡片与尖角是同一个路径，因此是同一块玻璃。
struct AssistantPanelBubble: Shape {
    var tailX: CGFloat
    var tailHeight: CGFloat
    nonisolated static let radius: CGFloat = 22
    nonisolated static let tailHalfWidth: CGFloat = 11
    /// 界面测试时把尖角作为一个无障碍元素暴露出来，便于校验它对准按钮。
    nonisolated static let exposesTail = ProcessInfo.processInfo.arguments.contains("--ui-testing")

    var animatableData: CGFloat {
        get { tailX }
        set { tailX = newValue }
    }

    func path(in rect: CGRect) -> Path {
        let body = CGRect(x: rect.minX, y: rect.minY, width: rect.width, height: max(0, rect.height - tailHeight))
        var path = Path(roundedRect: body, cornerRadius: Self.radius, style: .continuous)
        let x = rect.minX + tailX
        let base = body.maxY - 4
        let tip = body.maxY + tailHeight
        let half = Self.tailHalfWidth
        var tail = Path()
        tail.move(to: CGPoint(x: x - half - 4, y: base))
        tail.addCurve(to: CGPoint(x: x, y: tip),
                      control1: CGPoint(x: x - half * 0.35, y: base),
                      control2: CGPoint(x: x - half * 0.3, y: tip - tailHeight * 0.15))
        tail.addCurve(to: CGPoint(x: x + half + 4, y: base),
                      control1: CGPoint(x: x + half * 0.3, y: tip - tailHeight * 0.15),
                      control2: CGPoint(x: x + half * 0.35, y: base))
        tail.closeSubpath()
        path.addPath(tail)
        return path
    }
}

/// 浮层展开／收起的形变（iOS 原生菜单的观感）：从水滴尖（触发按钮）处由小放大、由模糊变清晰、淡入；
/// 收起时反过来。只作用于卡片本身，不影响任何布局。
struct AssistantPanelMorph: ViewModifier {
    let progress: CGFloat
    let anchor: UnitPoint

    func body(content: Content) -> some View {
        content
            .scaleEffect(0.5 + 0.5 * progress, anchor: anchor)
            .blur(radius: (1 - progress) * 10)
            .opacity(Double(progress))
    }
}

extension View {
    /// 浮层打开时触发按钮降到约 40% 不透明度、保留在原位（仍可点，再点一次收起），收起后恢复。
    /// 只改透明度，按钮的尺寸、位置、可点区域都不变。
    func composerSourceDimmed(_ dimmed: Bool, reduceMotion: Bool) -> some View {
        opacity(dimmed ? 0.4 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: dimmed)
    }

    /// 输入栏里浮层按钮标签的统一写法：固定尺寸、整块可点，
    /// 标签内容变化不做隐式动画，浮层展开／收起期间标签不改变尺寸或位置。
    func composerMenuLabel(width: CGFloat) -> some View {
        frame(width: width, height: 44)
            .contentShape(.rect)
            .transaction { $0.animation = nil }
    }
}
