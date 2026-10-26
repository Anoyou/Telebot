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
///   右下发送按钮（任务执行中且输入为空时变为停止），发送按钮左侧是本轮模型选择（原生 `Menu` + `Picker`，显示模型短名）。
/// - 任务执行中，工具栏左侧在「+」旁显示“发送方式”菜单（加入队列／插入调整／补充信息／停止并替换）。
/// - 容器用 `glassEffect(.regular, in: .rect(cornerRadius:))`，这是 DESIGN.md「材质」里明确允许的例外；
///   通过 `safeAreaInset(edge: .bottom)` 挂在对话下方，贴底部安全区并随键盘上移。
/// - 草稿与附件是本视图自己的状态：打字只重绘输入栏，不会让上方的对话历史重新计算。
struct AssistantComposer: View {
    @Environment(AppModel.self) private var model
    @Bindable var store: AssistantStore
    var focused: FocusState<Bool>.Binding
    @State private var draft = ""
    @State private var sendMode = "message"
    @State private var photos: [PhotosPickerItem] = []
    @State private var attachments: [AssistantAttachment] = []
    @State private var importingImage = false
    @State private var loadingImage = false
    @State private var confirmStop = false
    @State private var confirmReplace = false

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
        // 玻璃画在背景层，而不是用 `.glassEffect` 把整个输入栏（含两个 Menu）包进玻璃层：
        // iOS 26 的 Menu 弹出／收回会从标签“变形”出菜单，标签在玻璃层内部时，收回动画要把菜单变回一个嵌在
        // 另一块玻璃里的标签，约一秒内标签闪烁、错位（第六轮「+」与模型菜单同一个问题）。
        // 背景玻璃与原写法外观一致，Menu 标签则是普通视图，收回动画干净。
        .background {
            Color.clear.glassEffect(.regular, in: .rect(cornerRadius: 26))
        }
        .padding(.horizontal, 12).padding(.top, 6).padding(.bottom, 8)
        .frame(maxWidth: 900)
        .frame(maxWidth: .infinity)
        .onChange(of: store.runActive) { _, active in if !active { sendMode = "message" } }
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

    /// 左下角「+」：只列出服务器真实支持的附件（图片，最多 4 张）；没有的能力不放进菜单。
    private var attachMenu: some View {
        Menu {
            PhotosPicker(selection: $photos, maxSelectionCount: max(1, 4 - attachments.count), matching: .images) { Label("从照片选择", systemImage: "photo") }
            Button("从文件选择图片", systemImage: "folder") { importingImage = true }
        } label: {
            Image(systemName: "plus")
                .font(.system(size: 17, weight: .semibold))
                .foregroundStyle(.primary)
                .composerMenuLabel(width: 44)
        }
        .fixedSize()
        .disabled(attachments.count >= 4 || loadingImage || readOnlyReason != nil)
        .accessibilityLabel("添加图片")
        .accessibilityHint("可从照片或文件选择图片，最多 4 张")
        .accessibilityIdentifier("assistant.attach")
    }

    /// 本轮模型：独立的小视图（见 `AssistantModelMenu`），选择变化只重绘它自己。
    private var modelMenu: some View {
        AssistantModelMenu(store: store)
    }

    private var sendModeMenu: some View {
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
            Label(sendModeTitle, systemImage: sendModeSymbol)
                .font(.footnote.weight(.medium))
                .foregroundStyle(.secondary)
                .lineLimit(1)
                .frame(minHeight: 44)
                .contentShape(.rect)
        }
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

/// 输入栏的本轮模型选择：原生 `Menu { Picker }`，标签是固定宽度的“模型短名 + 上下箭头”。
///
/// 第五轮的写法在菜单收回时会闪烁/错位约一秒：标签宽度随所选模型变化（`maxWidth: 170`），选择在菜单收回动画
/// 期间写入 `AssistantStore`，整个输入栏（玻璃容器）随之重算布局，系统把菜单“收回”到一个正在移动、变宽的标签上。
/// 现在：
/// - 标签宽度固定（`labelWidth`），文字单行居中截断，选择变化不改变任何布局；文字切换不做动画。
/// - 选中值先落在本视图的 `selection`，界面只读它；写回 store 的 `modelIndex` 不会让其它视图重绘
///   （输入栏不再读取 `modelIndex`，发送时才通过 `selectedModel` 读取）。
/// - 选项用“供应商 ID + 模型名”作稳定 ID，服务器能力刷新时不会重建菜单项。
struct AssistantModelMenu: View {
    @Bindable var store: AssistantStore
    @State private var selection = -1
    static let labelWidth: CGFloat = 116

    private struct Option: Identifiable {
        let index: Int
        let id: String
        let title: String
    }

    private var options: [Option] {
        store.models.enumerated().map { index, item in
            Option(index: index,
                   id: "\(item["provider_id"]?.intValue ?? 0)|\(item.assistantText("model"))",
                   title: "\(item.assistantText("provider_name")) · \(item.assistantText("model"))")
        }
    }

    var body: some View {
        let models = store.models
        Menu {
            Picker("本轮模型", selection: $selection) {
                Text("自动，使用服务器配置").tag(-1)
                ForEach(options) { option in
                    Text(option.title).tag(option.index)
                }
            }
        } label: {
            HStack(spacing: 3) {
                Text(Self.shortName(models: models, index: selection))
                    .lineLimit(1)
                    .truncationMode(.middle)
                    .animation(nil, value: selection)
                Image(systemName: "chevron.up.chevron.down").imageScale(.small)
            }
            .font(.subheadline.weight(.medium))
            .foregroundStyle(.secondary)
            .composerMenuLabel(width: Self.labelWidth)
        }
        .fixedSize()
        .accessibilityLabel("本轮模型：\(Self.fullName(models: models, index: selection))")
        .accessibilityIdentifier("assistant.model")
        .onAppear { selection = store.modelIndex }
        .onChange(of: selection) { _, value in
            if store.modelIndex != value { store.modelIndex = value }
        }
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

extension View {
    /// 输入栏里 Menu 标签的统一写法（「+」附件菜单与模型菜单共用）：固定尺寸、整块可点，
    /// 标签内容变化不做隐式动画，避免菜单收回动画期间标签改变尺寸或位置。
    func composerMenuLabel(width: CGFloat) -> some View {
        frame(width: width, height: 44)
            .contentShape(.rect)
            .transaction { $0.animation = nil }
    }
}
