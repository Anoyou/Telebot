import CryptoKit
import Foundation
import ImageIO
import SwiftUI
import TelePilotCore
import UIKit

// MARK: - 预处理后的消息

/// 预处理好的 Markdown 块：行内样式已经解析成 AttributedString，渲染时不再重复解析。
enum AssistantRenderedBlock: Hashable, Sendable {
    case paragraph(AttributedString)
    case heading(level: Int, text: AttributedString)
    case bullet(depth: Int, checked: Bool?, text: AttributedString)
    case ordered(number: String, depth: Int, text: AttributedString)
    case quote(AttributedString)
    case code(language: String, code: String)
    case table([[AttributedString]])
    case rule
}

/// 一条消息的显示数据：脱敏后的正文、推理与预解析的 Markdown 块。
/// 在后台线程生成（`AssistantRendered.make` 不依赖主线程），结果按消息 ID 与内容缓存。
struct AssistantRendered: Hashable, Sendable {
    let key: String
    let text: String
    let reasoning: String
    let blocks: [AssistantRenderedBlock]

    static func key(for item: AssistantItem) -> String {
        let raw = item["content"]?.assistantText("text") ?? ""
        let reasoning = item["content"]?["reasoning"]?.stringValue ?? ""
        return "\(item.id)#\(raw.count)#\(raw.hashValue)#\(reasoning.hashValue)"
    }

    static func make(_ item: AssistantItem) -> AssistantRendered {
        let raw = item["content"]?.assistantText("text") ?? ""
        let text = AssistantRedaction.text(raw)
        let reasoning = AssistantRedaction.text(item["content"]?["reasoning"]?.stringValue ?? "")
        let role = item["role"]?.stringValue ?? ""
        let blocks = role == "assistant" ? AssistantMarkdown.rendered(text) : []
        return AssistantRendered(key: key(for: item), text: text, reasoning: reasoning, blocks: blocks)
    }
}

extension AssistantMarkdown {
    static func rendered(_ source: String) -> [AssistantRenderedBlock] {
        blocks(source).map { block in
            switch block {
            case .paragraph(let value): .paragraph(inline(value))
            case .heading(let level, let value): .heading(level: level, text: inline(value))
            case .bullet(let depth, let checked, let value): .bullet(depth: depth, checked: checked, text: inline(value))
            case .ordered(let number, let depth, let value): .ordered(number: number, depth: depth, text: inline(value))
            case .quote(let value): .quote(inline(value))
            case .code(let language, let code): .code(language: language, code: code)
            case .table(let rows): .table(rows.map { $0.map(inline) })
            case .rule: .rule
            }
        }
    }
}

// MARK: - 正在生成的回答（第十一轮）

/// 正在执行任务的流式状态，从 `AssistantStore` 拆出来的独立 Observable：只有回答位置的 `AssistantLiveReplyView`
/// 读取它，流式输出不再让整个对话页（消息列表、待处理气泡、输入栏）重新计算。
///
/// - 写入去重：值没有变化时不写（Observation 不比较新旧值，写入即失效）。
/// - 平滑打字（第 23 轮）：`assistant_delta` 先进 `TelePilotCore.StreamingTextReveal` 的积压，按帧（约 30 帧/秒，
///   最后一块很长时约 12 帧/秒）逐字显示，速度随积压自适应（`StreamingTextPacer`），流结束后约 0.3 秒内追平；
///   每帧把新显示的几个字拼到 `text` 后再走下面的增量解析，Markdown 结果与整段解析相同。
///   用户滚动、停止生成、切走（`revealAll`）立即显示全部；系统「减弱动态效果」打开或界面测试中不做逐字动画，
///   沿用原来约 12 Hz（80 ms）整块提交。
/// - 增量解析：已闭合的块（`StreamingMarkdownBoundary`）脱敏、解析一次后缓存在 `stableBlocks`，每次提交只重新脱敏、
///   解析最后一块（`tailBlocks`）。
@MainActor @Observable
final class AssistantLiveState {
    /// 已经有正文（父视图只读这个布尔值，决定是否显示“空会话”提示）。
    private(set) var hasText = false
    /// 推理过程（原文，显示时再脱敏；推理折叠时不做任何处理）。
    private(set) var reasoning = ""
    /// 已闭合、已脱敏并预解析的块；只追加，`stableVersion` 随之递增。
    private(set) var stableBlocks: [AssistantRenderedBlock] = []
    private(set) var stableVersion = 0
    /// 最后一块（尚未闭合）的预解析结果，每次提交重算。
    private(set) var tailBlocks: [AssistantRenderedBlock] = []
    /// 每次提交递增：回答视图据此节流跟随滚动。
    private(set) var revision = 0
    /// 当前任务最近一个进度事件（run_started／provider_selected／tool_started／assistant_delta…）；
    /// 状态行据此显示真实阶段，还没有事件时用服务器 run.phase。
    private(set) var progressType = ""
    /// 最近一次调用的工具（tool_started 的 tool_description，没有时为 tool_name）。
    private(set) var progressTool = ""
    /// 第 24 轮：思考计时（只在开窗 / 收窗时写入；「正在思考… 12s」的秒数由视图按秒自己刷新）。
    private(set) var reasoningTimer = ReasoningTimer()
    /// 服务器时间与本机时间的差（取本轮观察到的最小值：重放的旧事件差值更大，不会拉偏）。
    @ObservationIgnored private var clockOffset: Double?

    /// 已显示的正文（= `reveal.received` 的前缀）。
    @ObservationIgnored private(set) var text = ""
    @ObservationIgnored private var pendingText = ""
    @ObservationIgnored private var pendingReasoning = ""
    @ObservationIgnored private var stableEnd = 0
    @ObservationIgnored private var flushTask: Task<Void, Never>?
    @ObservationIgnored private var lastCommit: ContinuousClock.Instant?
    /// 第 23 轮：已收到 / 已显示与逐字节奏。
    @ObservationIgnored private var reveal = StreamingTextReveal()
    @ObservationIgnored private var revealTask: Task<Void, Never>?
    @ObservationIgnored private var lastTick: ContinuousClock.Instant?

    /// 提交间隔：约 12 Hz（不做逐字动画时）。
    static let interval: Duration = .milliseconds(80)
    /// 界面测试中关闭逐字动画（直接显示全文），避免测试变慢变脆。
    nonisolated static let pacingDisabledForTests = ProcessInfo.processInfo.arguments.contains("--ui-testing")

    /// 这一刻是否做逐字动画：系统「减弱动态效果」打开或界面测试中直接显示。
    private var pacingEnabled: Bool {
        !Self.pacingDisabledForTests && !UIAccessibility.isReduceMotionEnabled
    }

    func appendText(_ delta: String) {
        guard !delta.isEmpty else { return }
        reveal.append(delta)
        if pacingEnabled {
            startRevealLoop()
        } else {
            stopRevealLoop()
            pendingText += reveal.revealAll()
            scheduleCommit()
        }
    }

    func appendReasoning(_ delta: String) {
        guard !delta.isEmpty else { return }
        pendingReasoning += delta
        scheduleCommit()
    }

    /// 最终回答（`assistant_message`）：是已显示内容的延续时接着逐字追平（约 0.3 秒内）；
    /// 否则（服务器改写过，或不做动画）整段替换，立即提交。
    func replace(text value: String, reasoning newReasoning: String) {
        if pacingEnabled, reveal.finish(final: value) {
            flushTask?.cancel()
            flushTask = nil
            pendingReasoning = ""
            // 刚从「不做动画」切过来时可能还有没提交的整块文字：先按原顺序接上。
            if !pendingText.isEmpty {
                text += pendingText
                pendingText = ""
                render()
            }
            if reasoning != newReasoning {
                reasoning = newReasoning
                revision &+= 1
            }
            startRevealLoop()
            return
        }
        cancelPending()
        stopRevealLoop()
        reveal.reset()
        reveal.append(value)
        _ = reveal.revealAll()
        text = value
        stableEnd = 0
        if !stableBlocks.isEmpty { stableBlocks = []; stableVersion += 1 }
        if reasoning != newReasoning { reasoning = newReasoning }
        render()
    }

    /// 清空正文与推理（进入工具阶段、任务结束、切换会话）。
    func clearText() {
        cancelPending()
        stopRevealLoop()
        reveal.reset()
        text = ""
        stableEnd = 0
        if !stableBlocks.isEmpty { stableBlocks = []; stableVersion += 1 }
        if !tailBlocks.isEmpty { tailBlocks = [] }
        if !reasoning.isEmpty { reasoning = "" }
        if hasText { hasText = false }
    }

    func setProgress(type: String) {
        if progressType != type { progressType = type }
    }

    func setProgress(tool: String) {
        if progressTool != tool { progressTool = tool }
    }

    func resetProgress() {
        setProgress(type: "")
        setProgress(tool: "")
        resetReasoningTimer()
    }

    /// 第 24 轮：按事件推进思考计时。事件带服务器时间 `ts` 时按它算（重连后重放的事件也是真实时间），
    /// 换算到本机时钟；没有时按收到的时间。
    func noteReasoningTiming(type: String, timestamp: String?) {
        let effect = ReasoningTimer.effect(of: type)
        // 只在开窗 / 收窗的那一刻需要时间：已在思考时的推理增量、没在思考时的正文增量直接跳过（流式时每秒好几次）。
        switch effect {
        case .ignore: return
        case .open where reasoningTimer.isThinking: return
        case .close where !reasoningTimer.isThinking: return
        default: break
        }
        let now = Date()
        var date = now
        if let timestamp, let server = ReasoningDuration.timestamp(timestamp) {
            let offset = now.timeIntervalSince(server)
            let best = min(clockOffset ?? offset, offset)
            clockOffset = best
            date = min(now, server.addingTimeInterval(best))
        }
        var next = reasoningTimer
        switch effect {
        case .open: next.reasoning(at: date)
        case .close: next.stop(at: date)
        case .ignore: return
        }
        if next != reasoningTimer { reasoningTimer = next }
    }

    func resetReasoningTimer() {
        clockOffset = nil
        if reasoningTimer != ReasoningTimer() { reasoningTimer = ReasoningTimer() }
    }

    /// 把缓冲区与积压立即全部显示（停止生成、用户滚动、离开页面）。
    func flush() {
        revealAll()
    }

    /// 立即显示已收到的全部文字（第 23 轮：用户滚动、停止生成、切走时调用）。没有积压时什么也不做。
    func revealAll() {
        stopRevealLoop()
        let rest = reveal.revealAll()
        if !rest.isEmpty { pendingText += rest }
        flushTask?.cancel()
        flushTask = nil
        commit()
    }

    /// 进度流结束：不做动画时立即提交；做动画时剩下的文字约 0.3 秒内追平（推理立即提交）。
    func finishStream() {
        guard pacingEnabled else {
            revealAll()
            return
        }
        reveal.finish()
        flushTask?.cancel()
        flushTask = nil
        commit()
        if !reveal.isCaughtUp { startRevealLoop() }
    }

    // MARK: 逐字显示（第 23 轮）

    private func startRevealLoop() {
        guard revealTask == nil, !reveal.isCaughtUp else { return }
        lastTick = nil
        revealTask = Task { @MainActor [weak self] in
            while !Task.isCancelled {
                guard let self else { return }
                let caughtUp = self.revealStep()
                if caughtUp {
                    // 没被取消（取消方会自己清空并可能已换了新任务），这里收尾。
                    if !Task.isCancelled { self.revealTask = nil }
                    return
                }
                let tail = self.text.utf8.count - self.stableEnd
                try? await Task.sleep(for: .milliseconds(Int(StreamingTextPacer.tickInterval(tailUTF8: tail) * 1000)))
            }
        }
    }

    private func stopRevealLoop() {
        revealTask?.cancel()
        revealTask = nil
        lastTick = nil
    }

    /// 走一帧：按距上一帧的真实时间算这帧显示几个字，拼到正文后重新解析最后一块。返回是否已经追平。
    private func revealStep() -> Bool {
        let now = ContinuousClock.now
        let elapsed = lastTick.map { Self.seconds(now - $0) } ?? StreamingTextPacer.tickInterval(tailUTF8: 0)
        lastTick = now
        let chunk = reveal.tick(elapsed: elapsed)
        if !chunk.isEmpty {
            // 「不做动画」时攒下、还没提交的整块文字在前（它们先显示），保证顺序与收到的一致。
            if !pendingText.isEmpty {
                text += pendingText
                pendingText = ""
            }
            text += chunk
            lastCommit = now
            render()
        }
        return reveal.isCaughtUp
    }

    private static func seconds(_ duration: Duration) -> Double {
        let parts = duration.components
        return Double(parts.seconds) + Double(parts.attoseconds) / 1e18
    }

    private func scheduleCommit() {
        guard flushTask == nil else { return }
        let now = ContinuousClock.now
        if let lastCommit, now - lastCommit < Self.interval {
            let wait = Self.interval - (now - lastCommit)
            flushTask = Task { @MainActor [weak self] in
                try? await Task.sleep(for: wait)
                guard let self, !Task.isCancelled else { return }
                self.flushTask = nil
                self.commit()
            }
        } else {
            commit()
        }
    }

    private func cancelPending() {
        flushTask?.cancel()
        flushTask = nil
        pendingText = ""
        pendingReasoning = ""
    }

    private func commit() {
        lastCommit = .now
        var changed = false
        if !pendingReasoning.isEmpty {
            reasoning += pendingReasoning
            pendingReasoning = ""
            changed = true
        }
        if !pendingText.isEmpty {
            text += pendingText
            pendingText = ""
            render()
            return
        }
        if changed { revision &+= 1 }
    }

    private func render() {
        let end = StreamingMarkdownBoundary.stableEnd(in: text, from: stableEnd)
        if end > stableEnd {
            let chunk = StreamingMarkdownBoundary.slice(text, from: stableEnd, to: end)
            stableBlocks.append(contentsOf: AssistantMarkdown.rendered(AssistantRedaction.text(chunk)))
            stableVersion += 1
            stableEnd = end
        }
        let tail = StreamingMarkdownBoundary.slice(text, from: stableEnd)
        let blocks = tail.isEmpty ? [] : AssistantMarkdown.rendered(AssistantRedaction.text(tail))
        if blocks != tailBlocks { tailBlocks = blocks }
        let nonEmpty = !text.isEmpty
        if hasText != nonEmpty { hasText = nonEmpty }
        revision &+= 1
    }
}

// MARK: - 对话分组

/// 一个显示条目：普通消息（附带同一轮的工具结果），或尚无最终回答时单独显示的工具调用。
struct AssistantDisplayEntry: Identifiable, Hashable, Sendable {
    let id: String
    let item: AssistantItem?
    let tools: [AssistantItem]
    /// 该消息的预处理结果，分组时一并取好：渲染时按行直接读取，不再在 body 里对全文求哈希查缓存。
    var rendered: AssistantRendered? = nil
}

/// 打开会话时的“先显示最近一屏、其余增量追加”。
///
/// 一次把几十条带 Markdown、图片的消息塞进底部锚定的 `LazyVStack`，第一帧要为大量行估算并测量高度，
/// 主线程忙到导航栏、输入栏都来不及画完。现在先显示最后 `firstScreen` 条，其余每帧向上追加 `step` 条。
enum AssistantReveal {
    static let firstScreen = 12
    static let step = 12

    /// 新的“从第几条开始显示”。原来没有内容（刚打开、切换会话）时只显示最后一屏；
    /// 原来已有内容（缓存换成服务器数据、追加新消息、读取更早消息）时保持当前进度。
    static func start(wasEmpty: Bool, count: Int, current: Int) -> Int {
        if wasEmpty { return max(0, count - firstScreen) }
        return min(current, count)
    }

    /// 下一步的起点。
    static func next(_ start: Int) -> Int { max(0, start - step) }
}

enum AssistantTurnGrouping {
    /// 以用户消息切分轮次：服务器在回答之后才写入工具消息，所以同一轮内的工具结果归到这一轮最后一条助手回答。
    /// 没有助手回答的轮次（执行中、失败）把工具结果放在第一条工具消息的位置单独显示。
    static func entries(_ messages: [AssistantItem]) -> [AssistantDisplayEntry] {
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

// MARK: - Markdown 渲染

/// 渲染预解析的 Markdown 块（不在 body 中解析文本）。
struct AssistantRenderedMarkdownView: View {
    let blocks: [AssistantRenderedBlock]

    var body: some View {
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
    private func blockView(_ block: AssistantRenderedBlock) -> some View {
        switch block {
        case .paragraph(let value):
            Text(value).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
        case .heading(let level, let value):
            Text(value)
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
                Text(value).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * 16)
        case .ordered(let number, let depth, let value):
            HStack(alignment: .firstTextBaseline, spacing: 6) {
                Text(number + ".").monospacedDigit().foregroundStyle(.secondary)
                Text(value).textSelection(.enabled).fixedSize(horizontal: false, vertical: true)
            }
            .padding(.leading, CGFloat(depth) * 16)
        case .quote(let value):
            HStack(alignment: .top, spacing: 10) {
                RoundedRectangle(cornerRadius: 1.5).fill(.tertiary).frame(width: 3)
                Text(value).foregroundStyle(.secondary).textSelection(.enabled)
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
                                Text(cell)
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

// MARK: - 图片

/// 用 ImageIO 按需要的像素尺寸直接生成缩略图（不先解码全尺寸位图）。第十一轮。
enum AssistantImageDownsampler {
    /// 显示用：消息里的图片最高 300pt、最宽为内容宽度（≤ 860pt），按长边需要的像素降采样。
    nonisolated static func displayImage(data: Data, scale: CGFloat) -> UIImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary) else { return nil }
        var aspect: CGFloat = 1
        if let properties = CGImageSourceCopyPropertiesAtIndex(source, 0, nil) as? [CFString: Any],
           let width = (properties[kCGImagePropertyPixelWidth] as? NSNumber)?.doubleValue,
           let height = (properties[kCGImagePropertyPixelHeight] as? NSNumber)?.doubleValue, width > 0, height > 0 {
            let orientation = (properties[kCGImagePropertyOrientation] as? NSNumber)?.intValue ?? 1
            let rotated = (5...8).contains(orientation)
            let landscape = rotated ? height / width : width / height
            aspect = CGFloat(max(landscape, 1))
        }
        let points = min(860, 300 * aspect)
        guard let image = thumbnail(source: source, maxPixelSize: Int((points * max(scale, 1)).rounded(.up))) else { return nil }
        return UIImage(cgImage: image)
    }

    nonisolated static func thumbnail(data: Data, maxPixelSize: Int) -> CGImage? {
        guard let source = CGImageSourceCreateWithData(data as CFData, [kCGImageSourceShouldCache: false] as CFDictionary) else { return nil }
        return thumbnail(source: source, maxPixelSize: maxPixelSize)
    }

    private nonisolated static func thumbnail(source: CGImageSource, maxPixelSize: Int) -> CGImage? {
        let options: [CFString: Any] = [
            kCGImageSourceCreateThumbnailFromImageAlways: true,
            kCGImageSourceCreateThumbnailWithTransform: true,
            kCGImageSourceShouldCacheImmediately: true,
            kCGImageSourceThumbnailMaxPixelSize: max(1, maxPixelSize),
        ]
        return CGImageSourceCreateThumbnailAtIndex(source, 0, options as CFDictionary)
    }

    /// 选图附件：在后台把原图缩到最长边 2400px、JPEG 0.82 编码并拼成 data URL，同时生成输入栏预览用的小缩略图。
    nonisolated static func attachment(from data: Data) -> AssistantPreparedAttachment? {
        guard let full = thumbnail(data: data, maxPixelSize: 2400), full.width > 0, full.height > 0 else { return nil }
        guard let bytes = UIImage(cgImage: full).jpegData(compressionQuality: 0.82), bytes.count <= 6_000_000 else { return nil }
        let preview = thumbnail(data: bytes, maxPixelSize: 360).map { UIImage(cgImage: $0) } ?? UIImage(cgImage: full)
        return AssistantPreparedAttachment(preview: preview, dataURL: "data:image/jpeg;base64," + bytes.base64EncodedString())
    }
}

/// 后台处理好的附件：预览图（UIImage 不可变，跨线程只读）与 data URL。
struct AssistantPreparedAttachment: @unchecked Sendable {
    let preview: UIImage
    let dataURL: String
}

/// 解码后的对话图片缓存：data URL 只解码一次，滚动或重绘时直接复用。
/// 第十一轮：按位图实际字节数（bytesPerRow × height）计成本，总上限 64 MB，不再只限张数。
@MainActor
final class AssistantImageCache {
    static let shared = AssistantImageCache()
    static let totalCostLimit = 64 * 1024 * 1024
    private let cache = NSCache<NSString, UIImage>()
    private init() {
        cache.countLimit = 120
        cache.totalCostLimit = Self.totalCostLimit
    }
    func image(_ key: String) -> UIImage? { cache.object(forKey: key as NSString) }
    func store(_ image: UIImage, key: String) {
        let cost = image.cgImage.map { $0.bytesPerRow * $0.height } ?? Int(image.size.width * image.size.height * image.scale * image.scale * 4)
        cache.setObject(image, forKey: key as NSString, cost: cost)
    }
}

/// data URL 图片：Base64 解码与 ImageIO 降采样都在后台进行（按显示尺寸生成位图，不解码全尺寸原图）。
struct AssistantDataImageView: View {
    let cacheKey: String
    let dataURL: String
    @Environment(\.displayScale) private var displayScale
    @State private var image: UIImage?
    @State private var failed = false

    var body: some View {
        Group {
            if let image = image ?? AssistantImageCache.shared.image(cacheKey) {
                Image(uiImage: image).resizable().scaledToFit().frame(maxHeight: 300).clipShape(.rect(cornerRadius: 12))
            } else if failed {
                Label("图片无法显示", systemImage: "photo").font(.caption).foregroundStyle(.secondary)
            } else {
                RoundedRectangle(cornerRadius: 12).fill(Color(.secondarySystemBackground))
                    .frame(height: 160)
                    .overlay { ProgressView() }
            }
        }
        .task(id: cacheKey) {
            guard image == nil, AssistantImageCache.shared.image(cacheKey) == nil else { return }
            let source = dataURL
            let scale = displayScale
            let decoded = await Task.detached(priority: .userInitiated) { () -> AssistantDecodedImage? in
                guard let encoded = source.split(separator: ",", maxSplits: 1).last,
                      let data = Data(base64Encoded: String(encoded)),
                      let image = AssistantImageDownsampler.displayImage(data: data, scale: scale) else { return nil }
                return AssistantDecodedImage(image: image)
            }.value
            guard let decoded else { failed = true; return }
            AssistantImageCache.shared.store(decoded.image, key: cacheKey)
            image = decoded.image
        }
    }
}

/// 后台生成的位图（不可变），交回主线程显示。
struct AssistantDecodedImage: @unchecked Sendable {
    let image: UIImage
}

// MARK: - 本机历史缓存

/// 最近一次打开的会话列表与最近消息的本机缓存：进入助手页先显示缓存，再从服务器刷新。
/// 按服务器与用户隔离，存放在 Caches（系统可随时清理），文件启用完整数据保护；退出登录或更换服务器时删除。
enum AssistantHistoryCache {
    struct Snapshot: Codable, Sendable {
        var sessions: [JSONValue]
        var sessionID: String?
        var session: JSONValue?
        var messages: [JSONValue]
        var hasOlderMessages: Bool
        var savedAt: Date
    }

    private static var directory: URL? {
        FileManager.default.urls(for: .cachesDirectory, in: .userDomainMask).first?
            .appendingPathComponent("assistant-history", isDirectory: true)
    }

    private static func url(_ key: String) -> URL? {
        let digest = SHA256.hash(data: Data(key.utf8)).map { String(format: "%02x", $0) }.joined()
        return directory?.appendingPathComponent("\(digest).json")
    }

    static func load(_ key: String) async -> Snapshot? {
        guard let url = url(key) else { return nil }
        return await Task.detached(priority: .userInitiated) { () -> Snapshot? in
            guard let data = try? Data(contentsOf: url) else { return nil }
            let decoder = JSONDecoder()
            decoder.dateDecodingStrategy = .secondsSince1970
            return try? decoder.decode(Snapshot.self, from: data)
        }.value
    }

    static func save(_ snapshot: Snapshot, key: String) {
        guard let url = url(key), let directory else { return }
        Task.detached(priority: .utility) {
            // 第十一轮：缓存只负责“先出文字”，写盘前去掉内联图片（data URL 动辄 MB 级），
            // 换成 `data_url_omitted` 标记，显示时提示“图片未缓存”，服务器刷新后恢复。
            var snapshot = snapshot
            snapshot.messages = snapshot.messages.map(strippingInlineImages)
            snapshot.session = snapshot.session.map(strippingInlineImages)
            let encoder = JSONEncoder()
            encoder.dateEncodingStrategy = .secondsSince1970
            guard let data = try? encoder.encode(snapshot) else { return }
            try? FileManager.default.createDirectory(at: directory, withIntermediateDirectories: true)
            try? data.write(to: url, options: [.atomic, .completeFileProtection])
        }
    }

    static func removeAll() {
        guard let directory else { return }
        try? FileManager.default.removeItem(at: directory)
    }

    /// 递归去掉 `data_url`（以 `data:` 开头的内联数据），保留其他字段并标记 `data_url_omitted`。
    nonisolated static func strippingInlineImages(_ value: JSONValue) -> JSONValue {
        switch value {
        case .object(var fields):
            if let inline = fields["data_url"]?.stringValue, inline.hasPrefix("data:") {
                fields["data_url"] = nil
                fields["data_url_omitted"] = true
            }
            for (key, field) in fields where key != "data_url" {
                switch field {
                case .object, .array: fields[key] = strippingInlineImages(field)
                default: break
                }
            }
            return .object(fields)
        case .array(let values):
            return .array(values.map(strippingInlineImages))
        default:
            return value
        }
    }
}

// MARK: - 选择文本（第九轮）

/// 「选择文本」面板（参考 ChatGPT／Claude iOS）：SwiftUI `Text` 的 `.textSelection(.enabled)` 在 iOS 上只能整段复制，
/// 块级 Markdown 又被拆成多个 `Text`，无法拖选部分或跨块选择。这里把整条消息（已脱敏的正文）转成一段富文本，
/// 放进只读、可选择的 `UITextView`：可以自由拖选任意片段，并使用系统的复制／查找／翻译等菜单。
/// 原生 sheet（中等／大两档），右上角「完成」。
struct AssistantSelectTextSheet: View {
    let text: String
    /// 助手回答按 Markdown 排版；我的消息按纯文本显示（与气泡一致）。
    let markdown: Bool
    @Environment(\.dismiss) private var dismiss

    var body: some View {
        NavigationStack {
            AssistantSelectableTextView(attributed: markdown ? AssistantSelectableText.attributed(markdown: text) : AssistantSelectableText.plain(text))
                .privacySensitive()
                .ignoresSafeArea(.container, edges: .bottom)
                .navigationTitle("选择文本")
                .navigationBarTitleDisplayMode(.inline)
                .toolbar {
                    ToolbarItem(placement: .confirmationAction) {
                        Button("完成") { dismiss() }.accessibilityIdentifier("assistant.selectText.done")
                    }
                }
        }
        .presentationDetents([.medium, .large])
        .presentationDragIndicator(.visible)
    }
}

/// 只读、可选择的 `UITextView`（isEditable = false、isSelectable = true），自带滚动；字体来自 Dynamic Type。
struct AssistantSelectableTextView: UIViewRepresentable {
    let attributed: NSAttributedString

    func makeUIView(context: Context) -> UITextView {
        let view = UITextView()
        view.isEditable = false
        view.isSelectable = true
        view.isScrollEnabled = true
        view.alwaysBounceVertical = true
        view.backgroundColor = .clear
        view.adjustsFontForContentSizeCategory = true
        view.dataDetectorTypes = []
        view.textContainerInset = UIEdgeInsets(top: 12, left: 16, bottom: 24, right: 16)
        view.textContainer.lineFragmentPadding = 0
        view.linkTextAttributes = [.foregroundColor: UIColor.tintColor]
        view.accessibilityIdentifier = "assistant.selectText.textView"
        view.attributedText = attributed
        return view
    }

    func updateUIView(_ view: UITextView, context: Context) {
        if view.attributedText != attributed { view.attributedText = attributed }
    }
}

/// Markdown → `NSAttributedString`：与气泡里同一套块级解析（`AssistantMarkdown.blocks`），保留标题、列表、引用、
/// 代码块、表格与行内粗体／斜体／代码／链接的基本样式；字体用 `UIFont.preferredFont`（Dynamic Type）。
enum AssistantSelectableText {
    static func plain(_ text: String) -> NSAttributedString {
        NSAttributedString(string: text, attributes: [.font: UIFont.preferredFont(forTextStyle: .body), .foregroundColor: UIColor.label])
    }

    static func attributed(markdown source: String) -> NSAttributedString {
        let result = NSMutableAttributedString()
        let blocks = AssistantMarkdown.blocks(source)
        for (index, block) in blocks.enumerated() {
            let last = index == blocks.count - 1
            switch block {
            case .paragraph(let value):
                result.append(inline(value, style: .body))
            case .heading(let level, let value):
                let style: UIFont.TextStyle = level <= 1 ? .title3 : level == 2 ? .headline : .subheadline
                result.append(inline(value, style: style, bold: true))
            case .bullet(let depth, let checked, let value):
                let marker = checked.map { $0 ? "☑︎ " : "☐ " } ?? (depth == 0 ? "• " : "◦ ")
                result.append(listItem(marker: marker, depth: depth, body: inline(value, style: .body)))
            case .ordered(let number, let depth, let value):
                result.append(listItem(marker: number + ". ", depth: depth, body: inline(value, style: .body)))
            case .quote(let value):
                let quote = NSMutableAttributedString(attributedString: inline(value, style: .body, color: .secondaryLabel))
                quote.addAttribute(.paragraphStyle, value: indented(12, first: 12), range: NSRange(location: 0, length: quote.length))
                result.append(quote)
            case .code(_, let code):
                let font = UIFontMetrics(forTextStyle: .footnote).scaledFont(for: .monospacedSystemFont(ofSize: 13, weight: .regular))
                result.append(NSAttributedString(string: code, attributes: [
                    .font: font, .foregroundColor: UIColor.label, .backgroundColor: UIColor.secondarySystemBackground,
                    .paragraphStyle: indented(8, first: 8),
                ]))
            case .table(let rows):
                let table = NSMutableAttributedString()
                for (row, cells) in rows.enumerated() {
                    if row > 0 { table.append(NSAttributedString(string: "\n")) }
                    for (column, cell) in cells.enumerated() {
                        if column > 0 { table.append(NSAttributedString(string: "\t", attributes: [.font: UIFont.preferredFont(forTextStyle: .footnote)])) }
                        table.append(inline(cell, style: .footnote, bold: row == 0))
                    }
                }
                result.append(table)
            case .rule:
                result.append(NSAttributedString(string: "———", attributes: [.font: UIFont.preferredFont(forTextStyle: .body), .foregroundColor: UIColor.tertiaryLabel]))
            }
            if !last { result.append(NSAttributedString(string: "\n\n", attributes: [.font: UIFont.preferredFont(forTextStyle: .footnote)])) }
        }
        return result
    }

    private static func indented(_ head: CGFloat, first: CGFloat) -> NSParagraphStyle {
        let style = NSMutableParagraphStyle()
        style.firstLineHeadIndent = first
        style.headIndent = head
        return style
    }

    private static func listItem(marker: String, depth: Int, body: NSAttributedString) -> NSAttributedString {
        let font = UIFont.preferredFont(forTextStyle: .body)
        let item = NSMutableAttributedString(string: marker, attributes: [.font: font, .foregroundColor: UIColor.secondaryLabel])
        item.append(body)
        let indent = CGFloat(depth) * 16
        let markerWidth = (marker as NSString).size(withAttributes: [.font: font]).width
        item.addAttribute(.paragraphStyle, value: indented(indent + markerWidth, first: indent), range: NSRange(location: 0, length: item.length))
        return item
    }

    /// 行内 Markdown（粗体／斜体／行内代码／删除线／链接）→ UIKit 属性。
    private static func inline(_ source: String, style: UIFont.TextStyle, bold: Bool = false, color: UIColor = .label) -> NSAttributedString {
        let base = UIFont.preferredFont(forTextStyle: style)
        let value = AssistantMarkdown.inline(source)
        let output = NSMutableAttributedString()
        for run in value.runs {
            let piece = String(value[run.range].characters)
            var traits: UIFontDescriptor.SymbolicTraits = bold ? [.traitBold] : []
            var font = base
            var attributes: [NSAttributedString.Key: Any] = [.foregroundColor: color]
            if let intent = run.inlinePresentationIntent {
                if intent.contains(.stronglyEmphasized) { traits.insert(.traitBold) }
                if intent.contains(.emphasized) { traits.insert(.traitItalic) }
                if intent.contains(.strikethrough) { attributes[.strikethroughStyle] = NSUnderlineStyle.single.rawValue }
                if intent.contains(.code) {
                    font = .monospacedSystemFont(ofSize: base.pointSize * 0.92, weight: .regular)
                    attributes[.backgroundColor] = UIColor.secondarySystemFill
                }
            }
            if !traits.isEmpty, let descriptor = font.fontDescriptor.withSymbolicTraits(font.fontDescriptor.symbolicTraits.union(traits)) {
                font = UIFont(descriptor: descriptor, size: 0)
            }
            attributes[.font] = font
            if let link = run.link { attributes[.link] = link }
            output.append(NSAttributedString(string: piece, attributes: attributes))
        }
        return output
    }
}
