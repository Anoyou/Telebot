import CryptoKit
import Foundation
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

// MARK: - 对话分组

/// 一个显示条目：普通消息（附带同一轮的工具结果），或尚无最终回答时单独显示的工具调用。
struct AssistantDisplayEntry: Identifiable, Hashable, Sendable {
    let id: String
    let item: AssistantItem?
    let tools: [AssistantItem]
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

/// 解码后的对话图片缓存：data URL 只解码一次，滚动或重绘时直接复用。
@MainActor
final class AssistantImageCache {
    static let shared = AssistantImageCache()
    private let cache = NSCache<NSString, UIImage>()
    private init() { cache.countLimit = 60 }
    func image(_ key: String) -> UIImage? { cache.object(forKey: key as NSString) }
    func store(_ image: UIImage, key: String) { cache.setObject(image, forKey: key as NSString) }
}

/// data URL 图片：Base64 解码在后台进行，并用系统的 `byPreparingForDisplay()` 预先解压，避免滚动时卡顿。
struct AssistantDataImageView: View {
    let cacheKey: String
    let dataURL: String
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
            let data = await Task.detached(priority: .userInitiated) { () -> Data? in
                guard let encoded = source.split(separator: ",", maxSplits: 1).last else { return nil }
                return Data(base64Encoded: String(encoded))
            }.value
            guard let data, let decoded = UIImage(data: data) else { failed = true; return }
            let prepared = await decoded.byPreparingForDisplay() ?? decoded
            AssistantImageCache.shared.store(prepared, key: cacheKey)
            image = prepared
        }
    }
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
}
