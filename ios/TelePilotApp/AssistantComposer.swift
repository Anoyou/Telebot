import PhotosUI
import SwiftUI
import TelePilotCore
import UniformTypeIdentifiers

struct AssistantAttachment: Identifiable {
    let id = UUID()
    let image: UIImage
    let value: JSONValue
}

/// 助手底部输入栏（参考“信息”App 的底部输入栏）：
/// - 本轮模型选择器在输入框上沿左侧，显示当前模型名，点开选择；任务执行中旁边显示“发送方式”。
/// - 输入框贴近底部安全区与键盘，背景用系统 `.bar` 材质延伸到底部，不额外留白。
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
        VStack(alignment: .leading, spacing: 6) {
            if !attachments.isEmpty { attachmentStrip }
            controlsRow
            inputRow
        }
        .padding(.horizontal, 12).padding(.top, 6).padding(.bottom, 6)
        .frame(maxWidth: 900)
        .frame(maxWidth: .infinity)
        .background(.bar)
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

    // MARK: 输入框上方：模型与发送方式

    private var controlsRow: some View {
        HStack(spacing: 8) {
            Menu {
                Picker("本轮模型", selection: $store.modelIndex) {
                    Text("自动，使用服务器配置").tag(-1)
                    ForEach(Array(store.models.enumerated()), id: \.offset) { index, item in
                        Text("\(item.assistantText("provider_name")) · \(item.assistantText("model"))").tag(index)
                    }
                }
            } label: {
                compactChip(modelLabel, systemImage: "cpu", showsChevron: true)
            }
            .accessibilityLabel("本轮模型：\(modelLabel)")
            .accessibilityIdentifier("assistant.model")
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
                    compactChip(sendModeTitle, systemImage: sendModeSymbol, showsChevron: true)
                }
                .accessibilityLabel("发送方式：\(sendModeTitle)")
            }
            Spacer(minLength: 0)
            if draft.count > 30_000 {
                Text("\(draft.count)/32000").monospacedDigit().foregroundStyle(draft.count > 32_000 ? .red : .secondary)
            }
        }
        .font(.caption)
        .foregroundStyle(.secondary)
        .padding(.leading, 4)
    }

    private func compactChip(_ title: String, systemImage: String, showsChevron: Bool) -> some View {
        HStack(spacing: 4) {
            Image(systemName: systemImage).imageScale(.small)
            Text(title).lineLimit(1).truncationMode(.middle)
            if showsChevron { Image(systemName: "chevron.up.chevron.down").imageScale(.small) }
        }
        .font(.caption.weight(.medium))
        .padding(.horizontal, 10).padding(.vertical, 6)
        .background(Color(.tertiarySystemFill), in: .capsule)
        .frame(minHeight: 32)
        .contentShape(.capsule)
    }

    // MARK: 输入框

    private var inputRow: some View {
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
            .disabled(attachments.count >= 4 || loadingImage || readOnlyReason != nil)
            .accessibilityLabel("添加图片")
            TextField(placeholder, text: $draft, axis: .vertical)
                .lineLimit(1...8)
                .focused(focused)
                // 请求进行中也允许继续输入，发送按钮会等待服务器确认后再可用。
                .disabled(readOnlyReason != nil)
                .padding(.vertical, 11)
                .accessibilityIdentifier("assistant.composer")
            if store.runActive { stopButton }
            sendButton
        }
        .padding(.horizontal, 2)
        .background(Color(.systemBackground), in: .rect(cornerRadius: 22))
        .overlay {
            RoundedRectangle(cornerRadius: 22)
                .strokeBorder(focused.wrappedValue ? Color.accentColor.opacity(0.6) : Color(.separator), lineWidth: focused.wrappedValue ? 1 : 0.5)
        }
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

    private var modelLabel: String {
        store.models.indices.contains(store.modelIndex) ? store.models[store.modelIndex].assistantText("model") : "自动模型"
    }

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
