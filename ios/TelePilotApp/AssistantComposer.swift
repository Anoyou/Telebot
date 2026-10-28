import PhotosUI
import SwiftUI
import TelePilotCore
import UniformTypeIdentifiers

/// 后台读取选中文件的结果。
enum AssistantFileRead: Sendable {
    case data(Data)
    case tooLarge
    case failed
}

struct AssistantAttachment: Identifiable {
    let id = UUID()
    let image: UIImage
    let value: JSONValue
}

/// 助手底部输入栏（第八轮恢复 iOS 26 原生 Liquid Glass）。
/// - 上方是多行 `TextField`（1–8 行自动增高）；下方一行工具栏：左下「+」添加附件，紧挨着的是模型胶囊
///   （显示「供应商 · 模型」全名，过长时中间省略，可变宽），右下发送按钮（任务执行中且输入为空时变为停止）。
/// - 「+」与模型都是普通 `Button`，点按后弹出系统原生 popover（第九轮）：`.popover(isPresented:attachmentAnchor:arrowEdge: .bottom)`
///   ＋`.presentationCompactAdaptation(.popover)`，iPhone 上也是原生气泡（iOS 26 自带 Liquid Glass、系统锚定动画）。
///   第十轮：popover 不再锚定在按钮本身，而是锚定在输入栏卡片**顶边**上、按钮正上方的一个 2pt 隐形锚点
///   （`popoverAnchor`）：气泡整体在卡片之上，不再压住输入框；「+」贴近屏幕左缘，锚点 x 至少离屏幕左缘
///   `arrowMinScreenX`，让箭头落在气泡底边的平直段，而不是落进气泡左下的大圆角里（真机上箭头与圆角连接处扭曲）。不用系统 `Menu`（Menu 会从玻璃里的标签“变形”弹出／收回，第六、七轮的根因），
///   也不再自绘卡片、水滴尖、模糊缩放动画和 40% 半透明按钮。popover 由系统在独立的呈现层显示，输入栏布局不受影响。
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
    /// 附件 popover 里选了来源：等 popover 收起后再弹系统照片／文件选择器（同时呈现会被系统拒绝）。
    @State private var pendingAttach: AssistantAttachSource?

    nonisolated static let cornerRadius: CGFloat = 26
    /// popover 箭头离屏幕左右缘的最小距离：系统 popover 版边（约 10pt）＋ iOS 26 气泡圆角（约 30pt）＋ 箭头半宽（约 14pt），再留余量。
    /// 「+」中心离屏幕左缘只有约 42pt，直接锚在「+」上时箭头会落进气泡的圆角区，导致形状扭曲（第十轮真机截图）。
    nonisolated static let arrowMinScreenX: CGFloat = 64
    nonisolated static let cardSpace = "assistant.composerCard"
    /// 输入栏卡片在窗口中的位置、「+」与模型胶囊在卡片内的中心 x：用来放 popover 的隐形锚点。
    @State private var cardFrame: CGRect = .zero
    @State private var attachMidX: CGFloat = 0
    @State private var modelMidX: CGFloat = 0
    /// 输入栏所在列的右缘（窗口坐标；iPhone 上即屏幕右缘）。
    @State private var containerMaxX: CGFloat = 0

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
        .coordinateSpace(.named(Self.cardSpace))
        // 第八轮：恢复原生 Liquid Glass（透出后面的对话）。输入栏里没有系统 Menu，浮层也不在这个玻璃容器里，
        // 所以不会再出现菜单从玻璃标签“变形”弹出／收回的错位。
        .glassEffect(.regular, in: .rect(cornerRadius: Self.cornerRadius, style: .continuous))
        // 第十轮：两个 popover 锚定在卡片顶边的隐形锚点上（见类型说明），气泡在卡片之上、箭头在平直边。
        .background(alignment: .topLeading) {
            ZStack(alignment: .topLeading) {
                popoverAnchor(id: "attach")
                    .popover(isPresented: panel.binding(.attach), attachmentAnchor: .rect(.bounds), arrowEdge: .bottom) {
                        AssistantAttachPopover { source in
                            pendingAttach = source
                            panel.close()
                        }
                        .presentationCompactAdaptation(.popover)
                    }
                    .padding(.leading, max(0, anchorX(attachMidX) - 1))
                popoverAnchor(id: "model")
                    .popover(isPresented: panel.binding(.model), attachmentAnchor: .rect(.bounds), arrowEdge: .bottom) {
                        AssistantModelPopover(store: store, panel: panel, maxHeight: popoverMaxHeight)
                            .presentationCompactAdaptation(.popover)
                    }
                    .padding(.leading, max(0, anchorX(modelMidX) - 1))
            }
        }
        .onGeometryChange(for: CGRect.self) { $0.frame(in: .global) } action: { cardFrame = $0 }
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("assistant.composerBar")
        .padding(.horizontal, 12).padding(.top, 6).padding(.bottom, 8)
        .frame(maxWidth: 900)
        .frame(maxWidth: .infinity)
        .onGeometryChange(for: CGFloat.self) { $0.frame(in: .global).maxX } action: { containerMaxX = $0 }
        // 点进输入框（开始打字）时收起 popover。
        .onChange(of: focused.wrappedValue) { _, isFocused in
            if isFocused && panel.open != nil { panel.close() }
        }
        // 附件 popover 收起后再打开系统选择器。
        .onChange(of: panel.open) { _, open in
            guard open == nil, let source = pendingAttach else { return }
            pendingAttach = nil
            Task { @MainActor in
                try? await Task.sleep(for: .milliseconds(350))
                switch source {
                case .photos: pickingPhotos = true
                case .file: importingImage = true
                }
            }
        }
        .photosPicker(isPresented: $pickingPhotos, selection: $photos, maxSelectionCount: max(1, 4 - attachments.count), matching: .images)
        .onChange(of: photos) { _, items in
            guard !items.isEmpty else { return }
            Task {
                loadingImage = true
                defer { loadingImage = false; photos = [] }
                for item in items.prefix(4 - attachments.count) {
                    do {
                        guard let data = try await item.loadTransferable(type: Data.self) else { continue }
                        try await addImage(data)
                    } catch { store.error = "无法读取图片，请选择其他图片。" }
                }
            }
        }
        .fileImporter(isPresented: $importingImage, allowedContentTypes: [.image], allowsMultipleSelection: true) { result in
            guard let urls = try? result.get() else { store.error = "无法读取图片文件，请重新选择。"; return }
            Task {
                loadingImage = true
                defer { loadingImage = false }
                for url in urls.prefix(4 - attachments.count) {
                    // 第十一轮：读文件、缩放、编码都在后台进行，不卡输入栏。
                    let loaded = await Task.detached(priority: .userInitiated) { () -> AssistantFileRead in
                        let access = url.startAccessingSecurityScopedResource()
                        defer { if access { url.stopAccessingSecurityScopedResource() } }
                        guard let size = try? url.resourceValues(forKeys: [.fileSizeKey]).fileSize else { return .failed }
                        guard size <= 25_000_000 else { return .tooLarge }
                        guard let data = try? Data(contentsOf: url) else { return .failed }
                        return .data(data)
                    }.value
                    switch loaded {
                    case .tooLarge: store.error = "单张原始图片不能超过 25 MB。"
                    case .failed: store.error = "无法读取图片文件，请重新选择。"
                    case .data(let data):
                        do { try await addImage(data) } catch { store.error = "无法读取图片文件，请重新选择。" }
                    }
                }
            }
        }
    }

    // MARK: popover 锚点

    /// 卡片顶边上的 2×2pt 隐形视图，popover 以它为锚（箭头指向卡片顶边、按钮正上方）。
    /// 界面测试时暴露为 `assistant.popoverAnchor.<attach|model>`，用于断言箭头离气泡两侧圆角足够远。
    /// 注意 `.popover` 要挂在 2×2 视图本身（`.rect(.bounds)` 即这 2pt），定位用的 `padding` 加在 popover 之后。
    private func popoverAnchor(id: String) -> some View {
        Color.clear
            .frame(width: 2, height: 2)
            .allowsHitTesting(false)
            .accessibilityElement()
            .accessibilityIdentifier("assistant.popoverAnchor.\(id)")
            .accessibilityHidden(!AssistantModelPopover.exposesLogo)
    }

    /// 模型 popover 的最大高度：卡片顶边以上可用的空间（减去状态栏／导航栏与箭头的余量）。
    /// 键盘弹起时卡片上移、上方空间变小，7 行（406pt）放不下，系统会把气泡往下推、盖住卡片顶边几 pt；
    /// 限高后列表在气泡内滚动，气泡始终完整落在卡片之上（第十轮 CI：键盘弹起时气泡底边 450.7 > 卡片顶边 446.5）。
    private var popoverMaxHeight: CGFloat {
        guard cardFrame.height > 0 else { return 440 }
        return max(170, cardFrame.minY - 130)
    }

    /// 锚点 x（卡片坐标）：按钮中心，但离屏幕左右缘至少 `arrowMinScreenX`。
    private func anchorX(_ buttonMidX: CGFloat) -> CGFloat {
        guard cardFrame.width > 0 else { return buttonMidX }
        let minX = Self.arrowMinScreenX - cardFrame.minX
        let maxX = containerMaxX - Self.arrowMinScreenX - cardFrame.minX
        return min(max(buttonMidX, minX, 0), max(minX, min(maxX, cardFrame.width)))
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
                .onGeometryChange(for: CGFloat.self) { $0.frame(in: .named(Self.cardSpace)).midX } action: { attachMidX = $0 }
            AssistantModelMenu(store: store, panel: panel)
                .onGeometryChange(for: CGFloat.self) { $0.frame(in: .named(Self.cardSpace)).midX } action: { modelMidX = $0 }
            Spacer(minLength: 8)
            if draft.count > 30_000 {
                Text("\(draft.count)/32000").font(.caption).monospacedDigit()
                    .foregroundStyle(draft.count > 32_000 ? .red : .secondary)
            }
            if showsStop { stopButton } else { sendButton }
        }
    }

    /// 左下角「+」：点按弹出原生 popover（箭头指向「+」），只列出服务器真实支持的附件：图片，最多 4 张。
    private var attachMenu: some View {
        Button { panel.toggle(.attach) } label: {
            Image(systemName: "plus")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.primary)
                .frame(width: 34, height: 34)
                .background(Circle().fill(.fill.tertiary))
                .frame(width: 44, height: 44)
                .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .fixedSize()
        .disabled(attachments.count >= 4 || loadingImage || readOnlyReason != nil)
        .accessibilityLabel("添加图片")
        .accessibilityHint("可从照片或文件选择图片，最多 4 张")
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

    /// 第十一轮：解码、缩放（ImageIO 缩略图，最长边 2400px，不先解码全尺寸原图）、JPEG 编码与 base64 都在后台线程，
    /// 主线程只追加结果；输入栏预览用 360px 小图。
    private func addImage(_ data: Data) async throws {
        guard attachments.count < 4 else { throw APIError.incompatibleResponse }
        let prepared = await Task.detached(priority: .userInitiated) { AssistantImageDownsampler.attachment(from: data) }.value
        guard let prepared, attachments.count < 4 else { throw APIError.incompatibleResponse }
        attachments.append(AssistantAttachment(image: prepared.preview, value: ["kind": "image", "source": "data_url", "mime_type": "image/jpeg", "name": "image.jpg", "data_url": .string(prepared.dataURL)]))
    }
}

/// 输入栏的本轮模型胶囊：家族 logo ＋「供应商 · 模型」全名（与回答末尾的模型名同一格式），过长时中间省略；
/// 点按弹出原生 popover（第九轮），列表每行前是模型家族 logo（与 Web 同一套映射）。
///
/// - 选中值先落在本视图的 `selection`，界面只读它；写回 store 的 `modelIndex` 不会让其它视图重绘
///   （输入栏不读取 `modelIndex`，发送时才通过 `selectedModel` 读取）。
struct AssistantModelMenu: View {
    @Bindable var store: AssistantStore
    @Bindable var panel: AssistantComposerPanelState
    @State private var selection = -1

    var body: some View {
        let models = store.models
        Button { panel.toggle(.model) } label: {
            HStack(spacing: 6) {
                AssistantModelLogo(brand: Self.brand(models: models, index: selection), size: 14)
                    .foregroundStyle(.secondary)
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
            .frame(minHeight: 44)
            .contentShape(.rect)
            .transaction { $0.animation = nil }
        }
        .buttonStyle(.plain)
        // popover 由 AssistantComposer 挂在输入栏卡片顶边的锚点上（第十轮）。
        .accessibilityLabel("本轮模型：\(Self.fullName(models: models, index: selection))")
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

    /// 模型家族（与 Web `resolveModelBrand` 同一套规则，`TelePilotCore.ModelBrand`）；自动时为“自动路由”。
    static func brand(models: [JSONValue], index: Int) -> ModelBrand {
        guard models.indices.contains(index) else { return ModelBrand.resolve(model: nil, auto: true) }
        return ModelBrand.resolve(model: models[index].assistantText("model"), providerName: models[index].assistantText("provider_name"))
    }
}

// MARK: - 输入栏 popover（第九轮：系统原生 popover）

/// 输入栏的两个 popover：「+」附件、本轮模型。
enum AssistantComposerPanel: Hashable {
    case attach, model
}

enum AssistantAttachSource {
    case photos, file
}

/// popover 状态：由 `AssistantView` 持有（切换会话时收起），输入栏两个按钮共享，同一时间只开一个。
@MainActor @Observable
final class AssistantComposerPanelState {
    var open: AssistantComposerPanel?

    func toggle(_ panel: AssistantComposerPanel) {
        open = open == panel ? nil : panel
    }

    func close() { open = nil }

    /// 给 `.popover(isPresented:)` 用的绑定：系统点外部收起时写回 nil。
    func binding(_ panel: AssistantComposerPanel) -> Binding<Bool> {
        Binding(get: { self.open == panel }, set: { presented in
            if presented { self.open = panel } else if self.open == panel { self.open = nil }
        })
    }
}

/// 「+」popover（目前服务器只支持图片附件；以后新增类型在这里加一行）。
/// 第十轮：不再用固定 250pt 宽的 `List`（比文字宽出一截、气泡显得空），改为两行按钮的 `VStack`，
/// 宽度按内容理想宽度（`fixedSize`，随动态字号变化），每行 50pt 高。
struct AssistantAttachPopover: View {
    let choose: (AssistantAttachSource) -> Void

    var body: some View {
        VStack(alignment: .leading, spacing: 0) {
            row("从照片选择", systemImage: "photo", identifier: "assistant.attach.photos") { choose(.photos) }
            Divider().padding(.leading, 52)
            row("从文件选择图片", systemImage: "folder", identifier: "assistant.attach.file") { choose(.file) }
        }
        .fixedSize()
        .foregroundStyle(.primary)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier("assistant.panel.attach")
    }

    private func row(_ title: String, systemImage: String, identifier: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                Image(systemName: systemImage)
                    .font(.body)
                    .frame(width: 24)
                Text(title).font(.body).lineLimit(1)
                Spacer(minLength: 0)
            }
            .padding(.horizontal, 16)
            .frame(minWidth: 200, maxWidth: .infinity, minHeight: AssistantModelPopover.compactRowHeight, alignment: .leading)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .accessibilityLabel(title)
        .accessibilityIdentifier(identifier)
    }
}

/// 模型 popover：原生 `List`，每行「家族 logo ＋ 模型短名（主色加粗）＋「供应商 · 模型」全名」，当前项右侧对勾；
/// “自动”的副标题是服务器当前路由（能力接口的 `provider_name` · `resolved_model`，没有时“使用服务器配置”）。
struct AssistantModelPopover: View {
    @Bindable var store: AssistantStore
    @Bindable var panel: AssistantComposerPanelState
    /// 由输入栏按卡片上方可用空间给出（见 `AssistantComposer.popoverMaxHeight`）。
    var maxHeight: CGFloat = 440

    static let rowHeight: CGFloat = 58
    static let rowVerticalInset: CGFloat = 7
    static let compactRowHeight: CGFloat = 50
    /// 界面测试时把每行的 logo 作为单独的无障碍元素暴露（按钮会合并子元素），便于断言家族；VoiceOver 下行的提示里读家族名。
    nonisolated static let exposesLogo = ProcessInfo.processInfo.arguments.contains("--ui-testing")

    var body: some View {
        let models = store.models
        let current = store.modelIndex
        let routed = Self.routedModel(store.capabilities)
        List {
            row(title: "自动", subtitle: routed ?? "使用服务器配置", brand: ModelBrand.resolve(model: nil, auto: true),
                selected: !models.indices.contains(current),
                label: "自动，\(routed.map { "当前路由到 \($0)" } ?? "使用服务器配置")",
                identifier: "assistant.model.option.auto") { select(-1) }
            ForEach(Array(models.enumerated()), id: \.offset) { index, item in
                let full = AssistantModelMenu.providerModel(item)
                row(title: AssistantModelMenu.shortName(models: models, index: index), subtitle: full,
                    brand: AssistantModelMenu.brand(models: models, index: index),
                    selected: current == index, label: full,
                    identifier: "assistant.model.option.\(index)") { select(index) }
            }
        }
        .listStyle(.plain)
        .scrollContentBackground(.hidden)
        .scrollBounceBehavior(.basedOnSize)
        .contentMargins(.vertical, 0, for: .scrollContent)
        .environment(\.defaultMinListRowHeight, Self.rowHeight)
        .frame(width: 330, height: min(CGFloat(models.count + 1) * Self.rowHeight, 440, maxHeight))
        .accessibilityIdentifier("assistant.panel.model")
    }

    @ViewBuilder private func row(title: String, subtitle: String, brand: ModelBrand, selected: Bool, label: String,
                                  identifier: String, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            HStack(spacing: 12) {
                AssistantModelLogo(brand: brand, size: 20)
                    .frame(width: 32, height: 32)
                    .background(Circle().fill(.fill.tertiary))
                    .accessibilityHidden(true)
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.body.weight(.semibold)).foregroundStyle(.primary).lineLimit(1).truncationMode(.middle)
                    Text(subtitle).font(.footnote).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                }
                Spacer(minLength: 8)
                if selected {
                    Image(systemName: "checkmark").font(.body.weight(.semibold)).foregroundStyle(.tint)
                }
            }
            // 行高固定为 rowHeight（内容 44 ＋ 上下 7 的行内边距）：列表高度按行数计算，模型不多时全部可见、无需滚动。
            .frame(height: Self.rowHeight - 2 * Self.rowVerticalInset)
            .contentShape(.rect)
        }
        .buttonStyle(.plain)
        .listRowInsets(EdgeInsets(top: Self.rowVerticalInset, leading: 16, bottom: Self.rowVerticalInset, trailing: 16))
        .accessibilityLabel(label)
        .accessibilityHint("模型家族：\(brand.label)")
        .accessibilityValue(selected ? "已选中" : "")
        .accessibilityAddTraits(selected ? .isSelected : [])
        .accessibilityIdentifier(identifier)
        .overlay(alignment: .leading) {
            if Self.exposesLogo {
                Color.clear.frame(width: 32, height: 32)
                    .allowsHitTesting(false)
                    .accessibilityElement()
                    .accessibilityLabel(brand.label)
                    .accessibilityIdentifier("assistant.model.logo.\(brand.id)")
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
        panel.close()
    }
}

/// 模型家族 logo：矢量单色图标（`Assets.xcassets/ModelBrands/<家族>`，由 sync-model-brands.py 从 Web 同一份数据生成，
/// 模板渲染，随前景色适配深浅色）；“自动”用 `sparkles`，有家族但没有开源图标或无法识别时用中性的 `cpu`。
struct AssistantModelLogo: View {
    let brand: ModelBrand
    var size: CGFloat = 16

    var body: some View {
        Group {
            if brand.id == ModelBrand.autoID {
                Image(systemName: "sparkles").resizable().scaledToFit()
            } else if UIImage(named: "ModelBrands/\(brand.id)") != nil {
                Image("ModelBrands/\(brand.id)").renderingMode(.template).resizable().scaledToFit()
            } else {
                Image(systemName: "cpu").resizable().scaledToFit()
            }
        }
        .frame(width: size, height: size)
        .accessibilityLabel(brand.label)
    }
}
