import PhotosUI
import SwiftUI
import TelePilotCore
import UniformTypeIdentifiers

struct AssistantAttachment: Identifiable {
    let id = UUID()
    let image: UIImage
    let value: JSONValue
}

/// 助手底部输入栏：原生 Liquid Glass 圆角容器（参考“信息”与 ChatGPT 的底部 composer）。
/// - 上方是多行 `TextField`（1–8 行自动增高）；下方一行工具栏：左下「+」添加附件，
///   右下发送按钮（任务执行中且输入为空时变为停止），发送按钮左侧是本轮模型选择（显示模型短名）。
/// - 任务执行中，工具栏左侧在「+」旁显示“发送方式”（加入队列／插入调整／补充信息／停止并替换）。
/// - 第七轮起「+」、模型、发送方式都是普通 `Button`，点按后由 `AssistantComposerPanelHost` 在输入栏上方弹出浮层卡片
///   （参考 Grok：从按钮一侧向上淡入＋缩放），不再用系统 `Menu`：Menu 收回时会把菜单“变形”回玻璃输入栏里的标签，
///   两轮修补（固定标签尺寸、玻璃移到背景层）都没能消除收回时的错位。
/// - 容器是非玻璃的圆角卡片（系统背景色＋细描边＋淡阴影，参考 Grok 录屏），第七轮起不再用 Liquid Glass；
///   通过 `safeAreaInset(edge: .bottom)` 挂在对话下方，贴底部安全区并随键盘上移。
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
    @State private var confirmReplace = false
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    /// 发送方式放在浮层状态里（浮层里的选择直接写入），输入栏只读取。
    private var sendMode: String {
        get { panel.sendMode }
        nonmutating set { panel.sendMode = newValue }
    }

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
        // 第七轮：参考 Grok 录屏，输入栏是白色（深色模式为系统次级背景）圆角卡片＋细描边＋淡阴影，不再是玻璃。
        // 输入栏里的按钮也不在任何 glassEffect／GlassEffectContainer 内，浮层与它没有几何关联。
        .background {
            RoundedRectangle(cornerRadius: 26, style: .continuous)
                .fill(Color(uiColor: .secondarySystemGroupedBackground))
                .shadow(color: .black.opacity(0.08), radius: 12, y: 2)
                .overlay {
                    RoundedRectangle(cornerRadius: 26, style: .continuous)
                        .strokeBorder(Color(uiColor: .separator).opacity(0.5), lineWidth: 0.5)
                }
        }
        .padding(.horizontal, 12).padding(.top, 6).padding(.bottom, 8)
        .frame(maxWidth: 900)
        .frame(maxWidth: .infinity)
        .onChange(of: store.runActive) { _, active in
            if !active {
                sendMode = "message"
                if panel.open == .sendMode { panel.open = nil }
            }
        }
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

    private var toolbarRow: some View {
        HStack(spacing: 4) {
            attachMenu
            if store.runActive { sendModeMenu }
            Spacer(minLength: 4)
            if draft.count > 30_000 {
                Text("\(draft.count)/32000").font(.caption).monospacedDigit()
                    .foregroundStyle(draft.count > 32_000 ? .red : .secondary)
            }
            modelMenu
            if showsStop { stopButton } else { sendButton }
        }
    }

    /// 左下角「+」：点按在输入栏上方弹出附件浮层（只列出服务器真实支持的附件：图片，最多 4 张）。
    /// 打开时「+」像 Grok 一样模糊淡出（浮层从它的位置展开），收起时再淡入；只改透明度与模糊，不改变尺寸与位置。
    private var attachMenu: some View {
        let isOpen = panel.open == .attach
        return Button { panel.toggle(.attach, animated: !reduceMotion) } label: {
            Image(systemName: "plus")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.primary)
                .composerSourceHidden(isOpen, reduceMotion: reduceMotion)
                .composerMenuLabel(width: 44)
        }
        .buttonStyle(.plain)
        .fixedSize()
        .disabled(attachments.count >= 4 || loadingImage || readOnlyReason != nil)
        .accessibilityLabel("添加图片")
        .accessibilityHint("可从照片或文件选择图片，最多 4 张")
        .accessibilityValue(isOpen ? "已展开" : "")
        .accessibilityIdentifier("assistant.attach")
    }

    /// 本轮模型：独立的小视图（见 `AssistantModelMenu`），选择变化只重绘它自己。
    private var modelMenu: some View {
        AssistantModelMenu(store: store, panel: panel)
    }

    private var sendModeMenu: some View {
        Button { panel.toggle(.sendMode, animated: !reduceMotion) } label: {
            Label(sendModeTitle, systemImage: sendModeSymbol)
                .font(.footnote.weight(.medium))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .composerSourceHidden(panel.open == .sendMode, reduceMotion: reduceMotion)
                .frame(minHeight: 44)
                .contentShape(.rect)
                .transaction { $0.animation = nil }
        }
        .buttonStyle(.plain)
        .accessibilityLabel("发送方式：\(sendModeTitle)")
        .accessibilityIdentifier("assistant.sendMode")
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
        .confirmationDialog("停止当前任务并发送替代消息？", isPresented: $confirmReplace, titleVisibility: .visible) {
            Button("停止并替换", role: .destructive) { performSend() }
        } message: { Text("服务器会停止当前任务，并使用输入框中的内容创建替代任务。已经执行的操作不会撤销。") }
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

    private var sendDisabled: Bool {
        !store.canSend || store.sessionID == nil || loadingImage
            || (trimmedDraft.isEmpty && attachments.isEmpty) || draft.count > 32_000
            || (sendMode == "input" && trimmedDraft.isEmpty)
    }

    private var readOnlyReason: String? {
        if store.session?.assistantText("channel") == "bot" { return "Telegram 会话为只读，请在 Telegram 中继续" }
        if store.session?.assistantText("status") == "archived" { return "已归档的会话为只读，恢复后可继续" }
        return nil
    }

    private var placeholder: String {
        if let reason = readOnlyReason { return reason }
        if store.sessionID == nil { return "请先新建会话" }
        guard store.runActive else { return "询问或安排任务…" }
        switch sendMode {
        case "steer": return "补充调整说明，当前任务会参考…"
        case "input": return "回答助手的问题…"
        case "replace": return "输入替代消息…"
        default: return "加入队列，当前任务结束后执行…"
        }
    }

    private var sendModeTitle: String { AssistantComposerPanelState.sendModeTitle(sendMode) }

    private var sendModeSymbol: String { AssistantComposerPanelState.sendModeSymbol(sendMode) }

    /// 执行中且输入为空时，右下角按钮是“停止”；输入了内容则仍是发送（按“发送方式”处理）。
    private var showsStop: Bool { store.runActive && trimmedDraft.isEmpty && attachments.isEmpty }

    // MARK: 发送

    private func submit() {
        guard !sendDisabled else { return }
        if sendMode == "replace" && store.runActive { confirmReplace = true } else { performSend() }
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

/// 输入栏的本轮模型选择按钮：固定宽度的“模型短名 + 上下箭头”，点按弹出模型浮层（`AssistantComposerPanelHost`）。
///
/// - 标签宽度固定（`labelWidth`），文字单行居中截断，选择变化不改变任何布局；文字切换不做动画。
/// - 选中值先落在本视图的 `selection`，界面只读它；写回 store 的 `modelIndex` 不会让其它视图重绘
///   （输入栏不读取 `modelIndex`，发送时才通过 `selectedModel` 读取）。
struct AssistantModelMenu: View {
    @Bindable var store: AssistantStore
    @Bindable var panel: AssistantComposerPanelState
    @State private var selection = -1
    @Environment(\.accessibilityReduceMotion) private var reduceMotion
    static let labelWidth: CGFloat = 116

    var body: some View {
        let models = store.models
        Button { panel.toggle(.model, animated: !reduceMotion) } label: {
            HStack(spacing: 3) {
                Text(Self.shortName(models: models, index: selection))
                    .lineLimit(1)
                    .truncationMode(.middle)
                Image(systemName: "chevron.up.chevron.down").imageScale(.small)
            }
            .font(.subheadline.weight(.medium))
            .foregroundStyle(.secondary)
            .composerSourceHidden(panel.open == .model, reduceMotion: reduceMotion)
            .composerMenuLabel(width: Self.labelWidth)
        }
        .buttonStyle(.plain)
        .fixedSize()
        .accessibilityLabel("本轮模型：\(Self.fullName(models: models, index: selection))")
        .accessibilityValue(panel.open == .model ? "已展开" : "")
        .accessibilityIdentifier("assistant.model")
        .onAppear { selection = store.modelIndex }
        .onChange(of: store.modelIndex) { _, value in
            if selection != value { selection = value }
        }
    }

    /// 模型短名：去掉 “厂商/” 前缀，例如 `openai/gpt-4o` 显示为 `gpt-4o`。
    static func shortName(models: [JSONValue], index: Int) -> String {
        guard models.indices.contains(index) else { return "自动" }
        let name = models[index].assistantText("model")
        return name.split(separator: "/").last.map(String.init) ?? name
    }

    static func fullName(models: [JSONValue], index: Int) -> String {
        models.indices.contains(index) ? models[index].assistantText("model") : "自动模型"
    }
}

// MARK: - 输入栏浮层（第七轮，参考 Grok）

/// 输入栏上方的浮层：「+」附件、本轮模型、发送方式三个选择器共用。
enum AssistantComposerPanel: Hashable {
    case attach, model, sendMode
}

/// 浮层状态：由 `AssistantView` 持有，输入栏（按钮）与浮层宿主（卡片）共享。
/// 附件动作通过递增请求计数交给输入栏执行（系统照片选择器与文件选择器挂在输入栏上）。
@MainActor @Observable
final class AssistantComposerPanelState {
    var open: AssistantComposerPanel?
    /// 最近一次打开的浮层：收起动画期间用它定位卡片。
    var lastOpened: AssistantComposerPanel = .attach
    var sendMode = "message"
    var photoRequest = 0
    var fileRequest = 0

    static let animation: Animation = .snappy(duration: 0.2)

    func toggle(_ panel: AssistantComposerPanel, animated: Bool) {
        if open != panel { lastOpened = panel }
        withAnimation(animated ? Self.animation : nil) { open = open == panel ? nil : panel }
    }

    func close(animated: Bool) {
        withAnimation(animated ? Self.animation : nil) { open = nil }
    }

    static func sendModeTitle(_ mode: String) -> String {
        switch mode {
        case "steer": "插入调整"
        case "input": "补充信息"
        case "replace": "停止并替换"
        default: "加入队列"
        }
    }

    static func sendModeSymbol(_ mode: String) -> String {
        switch mode {
        case "steer": "arrow.turn.down.right"
        case "input": "text.bubble"
        case "replace": "arrow.triangle.2.circlepath"
        default: "text.badge.plus"
        }
    }
}

/// 浮层宿主：挂在助手页（`safeAreaInset` 之后的 `.overlay`），因此卡片底边正好落在输入栏上方，
/// 且与输入栏没有任何几何关联（不在玻璃容器内、不参与输入栏布局、没有 matchedGeometry），
/// 展开／收起只有卡片自己淡入＋缩放，输入栏、按钮、文本框都不移动。
/// - 点卡片外（对话区域）收起；附件卡片从左下角展开，模型与发送方式卡片从右下／左下按钮一侧展开。
/// - 打开时不收键盘：收键盘会让输入栏随键盘下移。
struct AssistantComposerPanelHost: View {
    @Bindable var store: AssistantStore
    @Bindable var panel: AssistantComposerPanelState
    @Environment(\.accessibilityReduceMotion) private var reduceMotion

    var body: some View {
        ZStack(alignment: .bottom) {
            if panel.open != nil {
                // 透明的点按区域：点卡片外收起（Grok 录屏里背景不变暗）。
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
            // 卡片边缘与触发按钮对齐：附件／发送方式卡片左缘对齐按钮左缘，模型卡片右缘对齐模型按钮右缘
            // （按钮位置由输入栏的固定内边距与固定尺寸算出，见 `edgeInsets`）。
            // 定位容器始终存在，卡片是其中直接插入／移除的视图，过渡（形变）作用在卡片本身、锚点在按钮一侧。
            // 收起动画期间 `open` 已为 nil，定位沿用 `lastOpened`。
            let shown = panel.open ?? panel.lastOpened
            ZStack(alignment: alignment(shown)) {
                if let open = panel.open {
                    card(open)
                        .id(open)
                        .transition(reduceMotion ? .opacity : .modifier(
                            active: AssistantPanelMorph(progress: 0, anchor: anchor(open)),
                            identity: AssistantPanelMorph(progress: 1, anchor: anchor(open))))
                }
            }
            .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: alignment(shown))
            .padding(edgeInsets(shown))
            .frame(maxWidth: 900)
            .frame(maxWidth: .infinity)
        }
    }

    /// 输入栏外边距 12 ＋ 工具栏内边距 8 = 20：「+」左缘；「+」宽 44 ＋ 间距 4 → 发送方式左缘 68；
    /// 右侧发送／停止按钮宽 44 ＋ 间距 4 ＋ 20 = 68：模型按钮右缘。底部留 6pt 落在输入栏卡片上方。
    private func edgeInsets(_ open: AssistantComposerPanel) -> EdgeInsets {
        switch open {
        case .attach: EdgeInsets(top: 0, leading: 20, bottom: 6, trailing: 12)
        case .sendMode: EdgeInsets(top: 0, leading: 68, bottom: 6, trailing: 12)
        case .model: EdgeInsets(top: 0, leading: 12, bottom: 6, trailing: 68)
        }
    }

    private func alignment(_ open: AssistantComposerPanel) -> Alignment {
        open == .model ? .bottomTrailing : .bottomLeading
    }

    private func anchor(_ open: AssistantComposerPanel) -> UnitPoint {
        open == .model ? .bottomTrailing : .bottomLeading
    }

    @ViewBuilder private func card(_ open: AssistantComposerPanel) -> some View {
        let rows: AnyView = switch open {
        case .attach: AnyView(attachRows)
        case .model: AnyView(modelRows)
        case .sendMode: AnyView(sendModeRows)
        }
        ViewThatFits(in: .vertical) {
            rows
            ScrollView { rows }.frame(maxHeight: 360)
        }
        .padding(6)
        .frame(width: open == .model ? 300 : 260)
        .background(.regularMaterial, in: .rect(cornerRadius: 22, style: .continuous))
        .overlay { RoundedRectangle(cornerRadius: 22, style: .continuous).strokeBorder(.separator.opacity(0.6), lineWidth: 0.5) }
        .shadow(color: .black.opacity(0.16), radius: 20, y: 6)
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(open == .attach ? "assistant.panel.attach" : open == .model ? "assistant.panel.model" : "assistant.panel.sendMode")
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

    private var modelRows: some View {
        let models = store.models
        let current = store.modelIndex
        return VStack(spacing: 0) {
            AssistantPanelRow(title: "自动", subtitle: "使用服务器配置", systemImage: "sparkles",
                              selected: !models.indices.contains(current),
                              accessibilityLabel: "自动，使用服务器配置",
                              identifier: "assistant.model.option.auto") { select(-1) }
            ForEach(Array(models.enumerated()), id: \.offset) { index, item in
                let name = item.assistantText("model")
                let provider = item.assistantText("provider_name")
                AssistantPanelRow(title: AssistantModelMenu.shortName(models: models, index: index),
                                  subtitle: "\(provider) · \(name)", systemImage: "cpu",
                                  selected: current == index,
                                  accessibilityLabel: "\(provider) · \(name)",
                                  identifier: "assistant.model.option.\(index)") { select(index) }
            }
        }
    }

    private func select(_ index: Int) {
        if store.modelIndex != index { store.modelIndex = index }
        panel.close(animated: !reduceMotion)
    }

    // MARK: 发送方式

    private var sendModeRows: some View {
        let modes = ["message", "steer"] + (store.runStatus == "waiting_input" ? ["input"] : []) + ["replace"]
        return VStack(alignment: .leading, spacing: 0) {
            Text("任务执行中，新消息如何处理").font(.footnote).foregroundStyle(.secondary)
                .padding(.horizontal, 16).padding(.top, 8).padding(.bottom, 4)
            ForEach(modes, id: \.self) { mode in
                AssistantPanelRow(title: AssistantComposerPanelState.sendModeTitle(mode), subtitle: Self.sendModeDetail(mode),
                                  systemImage: AssistantComposerPanelState.sendModeSymbol(mode),
                                  selected: panel.sendMode == mode,
                                  identifier: "assistant.sendMode.option.\(mode)") {
                    panel.sendMode = mode
                    panel.close(animated: !reduceMotion)
                }
            }
        }
    }

    private static func sendModeDetail(_ mode: String) -> String {
        switch mode {
        case "steer": "当前任务参考这条说明"
        case "input": "回答助手的提问"
        case "replace": "停止当前任务，需确认"
        default: "当前任务结束后执行"
        }
    }
}

/// 浮层里的一行（参考 Grok）：图标、标题（可带一行灰色说明）；当前项灰底高亮＋右侧对勾。整行可点，高度至少 48pt。
/// 附件行用圆形浅灰底的图标。
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
                    .foregroundStyle(circledIcon ? .primary : .secondary)
                    .frame(width: circledIcon ? 34 : 24, height: circledIcon ? 34 : 24)
                    .background { if circledIcon { Circle().fill(Color(uiColor: .tertiarySystemFill)) } }
                VStack(alignment: .leading, spacing: 2) {
                    Text(title).font(.body).foregroundStyle(.primary).lineLimit(1).truncationMode(.middle)
                    if let subtitle {
                        Text(subtitle).font(.caption).foregroundStyle(.secondary).lineLimit(1).truncationMode(.middle)
                    }
                }
                Spacer(minLength: 8)
                if selected {
                    Image(systemName: "checkmark").font(.body.weight(.semibold)).foregroundStyle(.primary)
                }
            }
            .padding(.horizontal, 12)
            .padding(.vertical, 8)
            .frame(minHeight: 48)
            .background {
                if selected { RoundedRectangle(cornerRadius: 16, style: .continuous).fill(Color(uiColor: .tertiarySystemFill)) }
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

/// 浮层展开／收起的形变（复刻 Grok 录屏里 iOS 原生菜单的观感）：从触发按钮一侧由小放大、由模糊变清晰、淡入；
/// 收起时反过来。只作用于卡片本身，不影响任何布局。
struct AssistantPanelMorph: ViewModifier {
    let progress: CGFloat
    let anchor: UnitPoint

    func body(content: Content) -> some View {
        content
            .scaleEffect(0.55 + 0.45 * progress, anchor: anchor)
            .blur(radius: (1 - progress) * 12)
            .opacity(Double(progress))
    }
}

extension View {
    /// 浮层打开时触发按钮的内容模糊淡出（浮层从它的位置展开），收起后再淡入恢复（Grok 录屏同款）。
    /// 只改透明度与模糊，按钮的尺寸、位置、可点区域都不变。
    func composerSourceHidden(_ hidden: Bool, reduceMotion: Bool) -> some View {
        blur(radius: hidden && !reduceMotion ? 6 : 0)
            // 不用 0：完全透明的视图会从无障碍树里消失，VoiceOver 与界面测试就找不到这个按钮了。
            .opacity(hidden ? 0.02 : 1)
            .animation(reduceMotion ? nil : .easeOut(duration: 0.16), value: hidden)
    }

    /// 输入栏里浮层按钮标签的统一写法（「+」附件与模型选择共用）：固定尺寸、整块可点，
    /// 标签内容变化不做隐式动画，浮层展开／收起期间标签不改变尺寸或位置。
    func composerMenuLabel(width: CGFloat) -> some View {
        frame(width: width, height: 44)
            .contentShape(.rect)
            .transaction { $0.animation = nil }
    }
}
