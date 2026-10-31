import SwiftUI
import TelePilotCore

// MARK: - 预览配置

/// 一个模板字段怎样预览：示例值、解析模式、长度上限与默认模板（对应 Web 各页面的 TelegramHtmlPreview 用法）。
struct TelegramTemplateSpec {
    var botName = "TelePilot"
    var samples: [String: String] = [:]
    var mode: TelegramParseMode = .html
    /// 带图消息的说明文字（caption），上限 1024。
    var caption = false
    /// 处理 `{?name}…{/?}` 条件块（AI 输出模板）。
    var conditionals = false
    var missing: TelegramTemplate.Missing = .keep
    /// 留空时实际使用的模板（预览它，但只对用户自己写的内容做格式检查）。
    var defaultTemplate: String?
    /// 字面 `\n` 转换行（插件 schema 默认值）。
    var normalizeEscapes = false
    /// 由这个字段组合出整条消息（如玩法查询：回复模板 + 条目模板）。
    var compose: ((String) -> String)?
    /// 用户消息示例（显示在 Bot 回复上方，与 Web 一致）。
    var prompt: String? = "请根据下面内容回复。"
    var buttons: [[TelegramPreviewButton]] = []

    /// 预览用模型与诊断用模型（诊断只针对用户写的原文，行列位置与输入框一致）。
    func models(for text: String, overrides: [String: String] = [:]) -> (preview: TelegramMessagePreviewModel, diagnostics: TelegramMessagePreviewModel) {
        let values = samples.merging(overrides) { _, new in new }
        var source = text.isEmpty ? (defaultTemplate ?? "") : text
        if normalizeEscapes { source = TelegramTemplate.normalizeEscapes(source) }
        let diagnostics = TelegramMessagePreviewModel(template: text, values: values, mode: mode, caption: caption,
                                                      conditionals: conditionals, missing: missing)
        let composed = compose.map { $0(source) } ?? source
        let preview = TelegramMessagePreviewModel(template: composed, values: values, mode: mode, caption: caption,
                                                  conditionals: conditionals, missing: missing)
        return (preview, diagnostics)
    }
}

struct TelegramPreviewButton: Hashable, Identifiable {
    var text: String
    var url: String?
    var id: String { text + "|" + (url ?? "") }

    /// 读取 `reply_markup.inline_keyboard` / `inline_keyboard` / `buttons`（二维数组，每项 `{text, url?}`）。
    static func rows(from value: JSONValue?) -> [[TelegramPreviewButton]] {
        guard let value else { return [] }
        let raw = value["reply_markup"]?["inline_keyboard"] ?? value["inline_keyboard"] ?? value["buttons"] ?? value
        return (raw.arrayValue ?? []).compactMap { row -> [TelegramPreviewButton]? in
            let buttons = (row.arrayValue ?? [row]).compactMap { item -> TelegramPreviewButton? in
                guard let text = item["text"]?.stringValue, !text.isEmpty else { return nil }
                return TelegramPreviewButton(text: text, url: item["url"]?.stringValue)
            }
            return buttons.isEmpty ? nil : buttons
        }
    }
}

/// 表单里模板字段的预览上下文：由外层页面（插件配置、指令编辑）通过环境传给通用字段控件。
struct TelegramPreviewContext: Equatable {
    enum Kind: Equatable { case generic, plugin(featureKey: String), commandReply, aiOutput }
    var kind: Kind = .generic
    var mode: TelegramParseMode = .html
    /// 插件当前（草稿）配置，示例值里的指令名、超时等取自它。
    var config: [String: JSONValue] = [:]
    var prefix = ","
    /// 除 `*_template` 外也按消息预览的字段（reply_text 指令的 `text`）。
    var extraTemplateKeys: Set<String> = []

    func applies(to key: String) -> Bool {
        !key.isEmpty && (TelegramTemplateSamples.isTemplateKey(key) || extraTemplateKeys.contains(key))
    }

    func spec(for key: String, title: String) -> TelegramTemplateSpec {
        var spec = TelegramTemplateSpec(botName: title, mode: mode)
        switch kind {
        case .aiOutput where key == "output_template":
            spec.botName = "AI 回答"
            spec.samples = TelegramTemplateSamples.aiOutput
            spec.conditionals = true
            spec.missing = .empty
            spec.defaultTemplate = TelegramTemplateSamples.aiDefaultTemplate
        case .commandReply where key == "text":
            spec.botName = prefix + "指令"
            spec.samples = TelegramTemplateSamples.replyText
            spec.defaultTemplate = "hello {args}"
            spec.prompt = prefix + "指令 " + (TelegramTemplateSamples.replyText["args"] ?? "")
        case .plugin(let featureKey):
            spec.samples = TelegramTemplateSamples.plugin(config: config, prefix: prefix)
            spec.normalizeEscapes = true
            spec.prompt = "发送指令并参与竞猜。"
            spec.caption = key.lowercased().contains("caption")
                || (["chatgpt_image", "codex_image"].contains(featureKey) && key == "message_template")
        default:
            break
        }
        return spec
    }
}

private struct TelegramPreviewContextKey: EnvironmentKey { static let defaultValue = TelegramPreviewContext() }
extension EnvironmentValues {
    var telegramPreviewContext: TelegramPreviewContext {
        get { self[TelegramPreviewContextKey.self] }
        set { self[TelegramPreviewContextKey.self] = newValue }
    }
}

// MARK: - 气泡

private struct TelegramPalette {
    let dark: Bool
    var chatBackground: Color { dark ? Color(red: 0.06, green: 0.09, blue: 0.13) : Color(red: 0.78, green: 0.86, blue: 0.74) }
    var incoming: Color { dark ? Color(red: 0.13, green: 0.17, blue: 0.22) : .white }
    var outgoing: Color { dark ? Color(red: 0.17, green: 0.32, blue: 0.47) : Color(red: 0.89, green: 0.98, blue: 0.80) }
    var name: Color { dark ? Color(red: 0.45, green: 0.70, blue: 0.96) : Color(red: 0.16, green: 0.47, blue: 0.78) }
    var meta: Color { dark ? Color.white.opacity(0.5) : Color.black.opacity(0.45) }
    var keyboard: Color { dark ? Color.white.opacity(0.14) : Color.black.opacity(0.22) }
}

/// 外观：跟随系统 / 浅色 / 深色（只影响预览气泡）。
enum TelegramPreviewAppearance: String, CaseIterable, Identifiable {
    case system, light, dark
    var id: String { rawValue }
    var title: String {
        switch self {
        case .system: "跟随系统"
        case .light: "浅色"
        case .dark: "深色"
        }
    }
    var scheme: ColorScheme? {
        switch self {
        case .system: nil
        case .light: .light
        case .dark: .dark
        }
    }
}

/// 仿 Telegram 的聊天预览：可选的用户消息 + Bot 回复气泡（Bot 名、正文、时间）+ 内联按钮。
struct TelegramChatPreview: View {
    struct Message: Identifiable {
        var id: Int
        var botName: String
        var nodes: [TelegramNode]
        var buttons: [[TelegramPreviewButton]] = []
    }

    let messages: [Message]
    var prompt: String?
    var appearance: TelegramPreviewAppearance = .system
    var identifier = "tgPreview"

    init(model: TelegramMessagePreviewModel, botName: String, prompt: String? = nil, buttons: [[TelegramPreviewButton]] = [],
         appearance: TelegramPreviewAppearance = .system, identifier: String = "tgPreview") {
        messages = [Message(id: 0, botName: botName, nodes: model.nodes, buttons: buttons)]
        self.prompt = prompt
        self.appearance = appearance
        self.identifier = identifier
    }

    init(messages: [Message], prompt: String?, appearance: TelegramPreviewAppearance = .system, identifier: String = "tgPreview") {
        self.messages = messages
        self.prompt = prompt
        self.appearance = appearance
        self.identifier = identifier
    }

    var body: some View {
        if let scheme = appearance.scheme {
            content.environment(\.colorScheme, scheme)
        } else {
            content
        }
    }

    private var content: some View { TelegramChatPreviewContent(messages: messages, prompt: prompt, identifier: identifier) }
}

private struct TelegramChatPreviewContent: View {
    let messages: [TelegramChatPreview.Message]
    let prompt: String?
    let identifier: String
    @Environment(\.colorScheme) private var scheme

    var body: some View {
        let palette = TelegramPalette(dark: scheme == .dark)
        VStack(alignment: .leading, spacing: 8) {
            if let prompt, !prompt.isEmpty {
                HStack {
                    Spacer(minLength: 48)
                    VStack(alignment: .leading, spacing: 2) {
                        Text("示例用户").font(.caption2.weight(.semibold)).foregroundStyle(palette.meta)
                        Text(prompt).font(.subheadline)
                    }
                    .padding(.horizontal, 12).padding(.vertical, 8)
                    .background(palette.outgoing, in: .rect(cornerRadius: 16, style: .continuous))
                }
                .accessibilityElement(children: .combine)
                .accessibilityIdentifier("\(identifier).prompt")
            }
            ForEach(messages) { message in
                VStack(alignment: .leading, spacing: 4) {
                    HStack(spacing: 0) {
                        VStack(alignment: .leading, spacing: 4) {
                            Text(message.botName).font(.footnote.weight(.semibold)).foregroundStyle(palette.name)
                                .accessibilityIdentifier("\(identifier).botName")
                            if message.nodes.isEmpty {
                                Text("预览内容为空。").font(.subheadline).foregroundStyle(.secondary)
                            } else {
                                // Telegram 里引用、代码块、链接都用发送者的名字色，不用 App 的强调色。
                                TelegramRichText(nodes: message.nodes, identifier: identifier)
                                    .tint(palette.name)
                                    .environment(\.telegramAccent, palette.name)
                            }
                            Text(timeText(message.id)).font(.caption2.monospacedDigit()).foregroundStyle(palette.meta)
                                .frame(maxWidth: .infinity, alignment: .trailing)
                                .accessibilityLabel("发送时间 \(timeText(message.id))")
                        }
                        .padding(.horizontal, 12).padding(.vertical, 8)
                        .background(palette.incoming, in: .rect(cornerRadius: 16, style: .continuous))
                        Spacer(minLength: 32)
                    }
                    if !message.buttons.isEmpty {
                        TelegramInlineKeyboard(rows: message.buttons, palette: palette, identifier: identifier)
                            .padding(.trailing, 32)
                    }
                }
                .accessibilityElement(children: .contain)
                .accessibilityIdentifier("\(identifier).bubble")
            }
        }
        .padding(12)
        .frame(maxWidth: .infinity, alignment: .leading)
        .background(palette.chatBackground, in: .rect(cornerRadius: 14, style: .continuous))
    }

    private func timeText(_ index: Int) -> String {
        let minute = 30 + index
        return "12:\(minute < 10 ? "0" : "")\(minute)"
    }
}

private struct TelegramInlineKeyboard: View {
    let rows: [[TelegramPreviewButton]]
    let palette: TelegramPalette
    let identifier: String

    var body: some View {
        VStack(spacing: 4) {
            ForEach(Array(rows.enumerated()), id: \.offset) { rowIndex, row in
                HStack(spacing: 4) {
                    ForEach(Array(row.enumerated()), id: \.offset) { column, button in
                        HStack(spacing: 4) {
                            Text(button.text).font(.subheadline.weight(.medium)).lineLimit(1)
                            if button.url != nil {
                                Image(systemName: "arrow.up.right").font(.caption2.weight(.bold)).accessibilityHidden(true)
                            }
                        }
                        .foregroundStyle(.white)
                        .frame(maxWidth: .infinity, minHeight: 36)
                        .padding(.horizontal, 6)
                        .background(palette.keyboard, in: .rect(cornerRadius: 10, style: .continuous))
                        .accessibilityElement(children: .combine)
                        .accessibilityLabel(button.url == nil ? "按钮：\(button.text)" : "链接按钮：\(button.text)")
                        .accessibilityIdentifier("\(identifier).button.\(rowIndex).\(column)")
                    }
                }
            }
        }
    }
}

// MARK: - 富文本

struct TelegramInlineStyle: Equatable {
    var bold = false, italic = false, underline = false, strike = false, code = false, mark = false
    var sub = false, sup = false, variable = false, missing = false, math = false, thinking = false, accent = false
    var link: String?
    var spoiler: Int?
}

struct TelegramInlineRun: Equatable {
    var text: String
    var style: TelegramInlineStyle
}

struct TelegramTableCell: Equatable {
    var header: Bool
    var runs: [TelegramInlineRun]
    var align: String?
}

indirect enum TelegramBlockContent {
    case paragraph([TelegramInlineRun])
    case heading(Int, [TelegramInlineRun])
    case code(language: String?, text: String)
    case quote([TelegramBlock], expandable: Bool, pull: Bool)
    case list(ordered: Bool, start: Int, items: [[TelegramBlock]])
    case table(rows: [[TelegramTableCell]], bordered: Bool, striped: Bool)
    case details(summary: [TelegramInlineRun], blocks: [TelegramBlock], open: Bool)
    case divider
}

struct TelegramBlock: Identifiable {
    let id: Int
    let content: TelegramBlockContent
}

/// 节点树 → 块与行内片段（剧透、引用分配稳定编号，用于点按揭开 / 展开）。
enum TelegramBlockBuilder {
    private final class Counter {
        var block = 0
        var spoiler = 0
        func nextBlock() -> Int { block += 1; return block }
        func nextSpoiler() -> Int { spoiler += 1; return spoiler }
    }

    static func build(_ nodes: [TelegramNode]) -> [TelegramBlock] {
        blocks(nodes, Counter())
    }

    private static func blocks(_ nodes: [TelegramNode], _ counter: Counter) -> [TelegramBlock] {
        var result: [TelegramBlock] = []
        var runs: [TelegramInlineRun] = []
        func flush() {
            let trimmed = trimBoundaryNewlines(runs)
            runs = []
            guard trimmed.contains(where: { !$0.text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty || $0.style.spoiler != nil }) else { return }
            result.append(TelegramBlock(id: counter.nextBlock(), content: .paragraph(trimmed)))
        }
        for node in nodes {
            switch node {
            case .text(let text):
                runs.append(TelegramInlineRun(text: text, style: TelegramInlineStyle()))
            case .element(let element):
                if element.kind.isBlock {
                    flush()
                    result.append(contentsOf: block(element, counter))
                } else {
                    runs.append(contentsOf: inline([node], TelegramInlineStyle(), counter))
                }
            }
        }
        flush()
        return result
    }

    private static func trimBoundaryNewlines(_ runs: [TelegramInlineRun]) -> [TelegramInlineRun] {
        var result = runs
        while let first = result.first, first.text.allSatisfy({ $0 == "\n" }) { result.removeFirst() }
        if var first = result.first {
            while first.text.hasPrefix("\n") { first.text.removeFirst() }
            result[0] = first
        }
        while let last = result.last, last.text.allSatisfy({ $0 == "\n" }) { result.removeLast() }
        if var last = result.last {
            while last.text.hasSuffix("\n") { last.text.removeLast() }
            result[result.count - 1] = last
        }
        return result
    }

    private static func inline(_ nodes: [TelegramNode], _ style: TelegramInlineStyle, _ counter: Counter) -> [TelegramInlineRun] {
        inlineRuns(nodes, style, counter)
    }

    private static func inlineRuns(_ nodes: [TelegramNode], _ base: TelegramInlineStyle, _ counter: Counter) -> [TelegramInlineRun] {
        var runs: [TelegramInlineRun] = []
        for node in nodes {
            switch node {
            case .text(let text):
                runs.append(TelegramInlineRun(text: text, style: base))
            case .element(let element):
                var style = base
                switch element.kind {
                case .bold: style.bold = true
                case .italic: style.italic = true
                case .underline: style.underline = true
                case .strikethrough: style.strike = true
                case .code: style.code = true
                case .mark: style.mark = true
                case .subscriptText: style.sub = true
                case .superscriptText: style.sup = true
                case .spoiler: style.spoiler = counter.nextSpoiler()
                case .link(let url): style.link = url ?? ""
                case .variable: style.variable = true
                case .missingVariable: style.missing = true
                case .math, .mathBlock: style.math = true
                case .thinking: style.thinking = true; style.italic = true
                case .reference, .time, .customEmoji: style.accent = true
                case .lineBreak:
                    runs.append(TelegramInlineRun(text: "\n", style: base)); continue
                case .divider:
                    runs.append(TelegramInlineRun(text: "\n———\n", style: base)); continue
                case .checkbox(let checked):
                    runs.append(TelegramInlineRun(text: checked ? "☑︎ " : "☐ ", style: base)); continue
                case .media(_, let label), .map(let label):
                    var chip = base
                    chip.accent = true
                    runs.append(TelegramInlineRun(text: "［\(label)］", style: chip)); continue
                case .inlineEmoji(let label):
                    runs.append(TelegramInlineRun(text: label, style: base)); continue
                default:
                    break
                }
                if element.kind.isBlock {
                    runs.append(TelegramInlineRun(text: "\n", style: base))
                    var blockStyle = style
                    if element.kind == .pre { blockStyle.code = true }
                    runs.append(contentsOf: inlineRuns(element.children, blockStyle, counter))
                    runs.append(TelegramInlineRun(text: "\n", style: base))
                } else {
                    runs.append(contentsOf: inlineRuns(element.children, style, counter))
                }
            }
        }
        return runs
    }

    private static func block(_ element: TelegramElement, _ counter: Counter) -> [TelegramBlock] {
        switch element.kind {
        case .pre, .mathBlock:
            return [TelegramBlock(id: counter.nextBlock(), content: .code(language: language(in: element.children), text: rawText(element.children)))]
        case .blockquote(let expandable):
            return [TelegramBlock(id: counter.nextBlock(), content: .quote(blocks(element.children, counter), expandable: expandable, pull: false))]
        case .pullquote:
            return [TelegramBlock(id: counter.nextBlock(), content: .quote(blocks(element.children, counter), expandable: false, pull: true))]
        case .heading(let level):
            return [TelegramBlock(id: counter.nextBlock(), content: .heading(level, inline(element.children, TelegramInlineStyle(), counter)))]
        case .unorderedList, .orderedList:
            var start = 1
            var ordered = false
            if case .orderedList(let first, _) = element.kind { ordered = true; start = first ?? 1 }
            let items = element.children.compactMap { child -> [TelegramBlock]? in
                switch child {
                case .element(let item) where item.kind == .listItem: return blocks(item.children, counter)
                case .text(let text) where text.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty: return nil
                default: return blocks([child], counter)
                }
            }
            return [TelegramBlock(id: counter.nextBlock(), content: .list(ordered: ordered, start: start, items: items))]
        case .table(let bordered, let striped):
            let rows = tableRows(element.children, counter)
            guard !rows.isEmpty else { return [] }
            return [TelegramBlock(id: counter.nextBlock(), content: .table(rows: rows, bordered: bordered, striped: striped))]
        case .details(let open):
            var summary: [TelegramInlineRun] = []
            var rest: [TelegramNode] = []
            for child in element.children {
                if case .element(let item) = child, item.kind == .summary, summary.isEmpty {
                    summary = inline(item.children, TelegramInlineStyle(), counter)
                } else {
                    rest.append(child)
                }
            }
            if summary.isEmpty { summary = [TelegramInlineRun(text: "详情", style: TelegramInlineStyle())] }
            return [TelegramBlock(id: counter.nextBlock(), content: .details(summary: summary, blocks: blocks(rest, counter), open: open))]
        case .divider:
            return [TelegramBlock(id: counter.nextBlock(), content: .divider)]
        default:
            // 段落、列表项、表格分组、媒体拼贴、页脚等：按普通内容展开。
            return blocks(element.children, counter)
        }
    }

    private static func tableRows(_ nodes: [TelegramNode], _ counter: Counter) -> [[TelegramTableCell]] {
        var rows: [[TelegramTableCell]] = []
        for node in nodes {
            guard case .element(let element) = node else { continue }
            switch element.kind {
            case .tableSection: rows.append(contentsOf: tableRows(element.children, counter))
            case .tableRow:
                let cells = element.children.compactMap { child -> TelegramTableCell? in
                    guard case .element(let cell) = child, case .tableCell(let header, _, let align) = cell.kind else { return nil }
                    return TelegramTableCell(header: header, runs: inline(cell.children, TelegramInlineStyle(bold: header), counter), align: align)
                }
                if !cells.isEmpty { rows.append(cells) }
            default: continue
            }
        }
        return rows
    }

    private static func language(in nodes: [TelegramNode]) -> String? {
        for node in nodes {
            if case .element(let element) = node {
                if case .code(let language) = element.kind, let language { return language }
                if let nested = language(in: element.children) { return nested }
            }
        }
        return nil
    }

    static func rawText(_ nodes: [TelegramNode]) -> String {
        var out = ""
        for node in nodes {
            switch node {
            case .text(let text): out += text
            case .element(let element):
                if element.kind == .lineBreak { out += "\n" } else { out += rawText(element.children) }
            }
        }
        while out.hasPrefix("\n") { out.removeFirst() }
        while out.hasSuffix("\n") { out.removeLast() }
        return out
    }
}

/// 节点树的原生渲染：剧透点按揭开、折叠引用点按展开、代码块可复制；链接在预览里不打开。
struct TelegramRichText: View {
    let nodes: [TelegramNode]
    var identifier = "tgPreview"
    @State private var revealed: Set<Int> = []
    @State private var expanded: Set<Int> = []
    @State private var tappedLink: String?

    var body: some View {
        VStack(alignment: .leading, spacing: 6) {
            ForEach(TelegramBlockBuilder.build(nodes)) { block in
                TelegramBlockView(block: block, revealed: $revealed, expanded: $expanded, identifier: identifier)
            }
            if let tappedLink {
                Text("\(Image(systemName: "link")) 预览中不打开链接：\(tappedLink.isEmpty ? "（地址不受支持）" : tappedLink)")
                    .font(.caption2).foregroundStyle(.secondary).lineLimit(2)
                    .accessibilityIdentifier("\(identifier).linkNotice")
            }
        }
        .environment(\.openURL, OpenURLAction { url in
            tappedLink = url.absoluteString
            return .handled
        })
        .onChange(of: nodes) { _, _ in tappedLink = nil }
    }
}

private struct TelegramBlockView: View {
    let block: TelegramBlock
    @Binding var revealed: Set<Int>
    @Binding var expanded: Set<Int>
    let identifier: String
    /// 折叠引用的最后一段：末尾留出「展开」按钮的位置。
    var trailingReserve = false
    @Environment(\.telegramAccent) private var accent
    @State private var copied = false

    var body: some View {
        switch block.content {
        case .paragraph(let runs):
            paragraph(runs, font: .subheadline)
        case .heading(let level, let runs):
            paragraph(runs, font: level <= 1 ? .title3.weight(.bold) : level == 2 ? .headline : .subheadline.weight(.semibold))
                .accessibilityAddTraits(.isHeader)
        case .code(let language, let text):
            codeBlock(language: language, text: text)
        case .quote(let blocks, let expandable, let pull):
            quote(blocks, expandable: expandable, pull: pull)
        case .list(let ordered, let start, let items):
            VStack(alignment: .leading, spacing: 4) {
                ForEach(Array(items.enumerated()), id: \.offset) { index, item in
                    HStack(alignment: .firstTextBaseline, spacing: 6) {
                        Text(ordered ? "\(start + index)." : "•").font(.subheadline.monospacedDigit()).foregroundStyle(.secondary)
                        VStack(alignment: .leading, spacing: 4) {
                            ForEach(item) { child in
                                TelegramBlockView(block: child, revealed: $revealed, expanded: $expanded, identifier: identifier)
                            }
                        }
                    }
                }
            }
        case .table(let rows, let bordered, let striped):
            table(rows, bordered: bordered, striped: striped)
        case .details(let summary, let blocks, let open):
            let isOpen = expanded.contains(block.id) != open
            VStack(alignment: .leading, spacing: 4) {
                Button { toggle(block.id) } label: {
                    HStack(spacing: 4) {
                        Image(systemName: isOpen ? "chevron.down" : "chevron.right").font(.caption.weight(.semibold))
                        Text(TelegramAttributed.string(summary, revealed: revealed, font: .subheadline.weight(.semibold), accent: accent))
                    }
                }
                .buttonStyle(.plain)
                if isOpen {
                    ForEach(blocks) { child in
                        TelegramBlockView(block: child, revealed: $revealed, expanded: $expanded, identifier: identifier)
                    }
                }
            }
        case .divider:
            Divider()
        }
    }

    @ViewBuilder private func paragraph(_ runs: [TelegramInlineRun], font: Font) -> some View {
        let hidden = runs.compactMap(\.style.spoiler).filter { !revealed.contains($0) }
        let attributed = TelegramAttributed.string(runs, revealed: revealed, font: font, accent: accent)
        let text = Group {
            if trailingReserve {
                let reserve = Self.quoteToggleLabel(expanded: false).foregroundStyle(.clear)
                Text("\(attributed) \(reserve)").accessibilityLabel(Text(attributed))
            } else {
                Text(attributed)
            }
        }
        .fixedSize(horizontal: false, vertical: true)
        .frame(maxWidth: .infinity, alignment: .leading)
        if hidden.isEmpty {
            text
        } else {
            text
                .contentShape(Rectangle())
                .onTapGesture { withAnimation(.easeOut(duration: 0.2)) { revealed.formUnion(hidden) } }
                .accessibilityAddTraits(.isButton)
                .accessibilityHint("轻点显示隐藏的文字")
                .accessibilityAction { revealed.formUnion(hidden) }
                .accessibilityIdentifier("\(identifier).spoiler")
        }
    }

    private func codeBlock(language: String?, text: String) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            HStack {
                Text(language ?? "代码").font(.caption2.weight(.semibold)).foregroundStyle(.tint)
                Spacer()
                Button {
                    UIPasteboard.general.string = text
                    copied = true
                    Task { try? await Task.sleep(for: .seconds(1.5)); copied = false }
                } label: {
                    // 图标与文字紧挨：不用 Label（列表里 Label 给图标留一整列宽）。
                    Self.inlineIconText(copied ? "checkmark" : "doc.on.doc", copied ? "已复制" : "复制").font(.caption2)
                }
                .buttonStyle(.borderless)
                .accessibilityLabel(copied ? "已复制" : "复制")
                .accessibilityIdentifier("\(identifier).code.copy")
            }
            // 与 Telegram 一致：代码块完整显示、按宽度自动换行，不截断、不横向滚动。
            Text(text).font(.footnote.monospaced()).textSelection(.enabled)
                .lineLimit(nil)
                .fixedSize(horizontal: false, vertical: true)
                .frame(maxWidth: .infinity, alignment: .leading)
                .accessibilityIdentifier("\(identifier).code.text")
        }
        .padding(8)
        .background(Color.primary.opacity(0.06), in: .rect(cornerRadius: 8, style: .continuous))
        .overlay(alignment: .leading) { Rectangle().fill(.tint).frame(width: 3).clipShape(.rect(cornerRadius: 1.5)) }
        .accessibilityElement(children: .contain)
        .accessibilityLabel(language == nil ? "代码块" : "代码块，语言 " + (language ?? ""))
        .accessibilityIdentifier("\(identifier).code")
    }

    @ViewBuilder private func quote(_ blocks: [TelegramBlock], expandable: Bool, pull: Bool) -> some View {
        let isExpanded = expanded.contains(block.id)
        // 与 Telegram 一致：「⌄ 展开 / ⌃ 收起」贴在引用框内右下角，不单独占一行。
        // 最后一块是段落时，在段落末尾留出与按钮等宽的透明占位：最后一行放得下就与文字同行右对齐，
        // 放不下占位自然折到下一行，按钮也就落在新的一行右侧（同 Telegram 气泡里时间的做法）。
        let lastReservable = expandable && (blocks.last.map { Self.canReserveTrailing($0) } ?? false)
        let content = VStack(alignment: .leading, spacing: 4) {
            ForEach(blocks) { child in
                TelegramBlockView(
                    block: child, revealed: $revealed, expanded: $expanded, identifier: identifier,
                    trailingReserve: lastReservable && child.id == blocks.last?.id
                )
            }
            if expandable && !lastReservable {
                // 最后一块是列表、代码块等无法续排的内容：另起一行右对齐。
                Self.quoteToggleLabel(expanded: false).hidden().frame(maxWidth: .infinity, alignment: .trailing)
                    .accessibilityHidden(true)
            }
        }
        HStack(alignment: .top, spacing: 8) {
            RoundedRectangle(cornerRadius: 1.5).fill(.tint).frame(width: 3)
            Group {
                if expandable && !isExpanded {
                    content.frame(maxHeight: 62, alignment: .top).clipped()
                        .mask(LinearGradient(colors: [.black, .black, .black.opacity(0.2)], startPoint: .top, endPoint: .bottom))
                } else {
                    content
                }
            }
            .frame(maxWidth: .infinity, alignment: .leading)
            .overlay(alignment: .bottomTrailing) {
                if expandable {
                    Button { toggle(block.id) } label: {
                        Self.quoteToggleLabel(expanded: isExpanded).foregroundStyle(.tint)
                    }
                    .buttonStyle(.borderless)
                    .accessibilityLabel(isExpanded ? "收起" : "展开")
                    .accessibilityIdentifier("\(identifier).quote.toggle")
                }
            }
        }
        .padding(.vertical, 4).padding(.trailing, 8)
        .background(.tint.opacity(pull ? 0.06 : 0.1), in: .rect(cornerRadius: 6, style: .continuous))
        .accessibilityElement(children: .contain)
        .accessibilityIdentifier(expandable ? "\(identifier).quote.expandable" : "\(identifier).quote")
        .accessibilityValue(expandable ? (isExpanded ? "已展开" : "已折叠") : "")
    }

    /// 折叠引用的控件文字：箭头与「展开 / 收起」紧挨（不走 Label，列表里 Label 会给图标留一整列宽）。
    /// 不换行空格与词连接符保证占位不会在中间折行。
    static func quoteToggleLabel(expanded: Bool) -> Text {
        inlineIconText(expanded ? "chevron.up" : "chevron.down", expanded ? "收起" : "展开").font(.caption2.weight(.semibold))
    }

    /// 图标 + 文字紧挨成一段 Text（图标后不换行空格，文字字间加词连接符，整段不会被拆到两行）。
    static func inlineIconText(_ systemImage: String, _ title: String) -> Text {
        let glued = title.map(String.init).joined(separator: "\u{2060}")
        return Text("\(Image(systemName: systemImage))\u{00A0}\(glued)")
    }

    nonisolated private static func canReserveTrailing(_ block: TelegramBlock) -> Bool {
        switch block.content {
        case .paragraph, .heading: true
        default: false
        }
    }

    private func table(_ rows: [[TelegramTableCell]], bordered: Bool, striped: Bool) -> some View {
        ScrollView(.horizontal, showsIndicators: false) {
            Grid(alignment: .leading, horizontalSpacing: 0, verticalSpacing: 0) {
                ForEach(Array(rows.enumerated()), id: \.offset) { rowIndex, row in
                    GridRow {
                        ForEach(Array(row.enumerated()), id: \.offset) { _, cell in
                            Text(TelegramAttributed.string(cell.runs, revealed: revealed, font: .footnote, accent: accent))
                                .padding(.horizontal, 8).padding(.vertical, 4)
                                .frame(minWidth: 44, alignment: cell.align == "right" ? .trailing : cell.align == "center" ? .center : .leading)
                                .background(striped && rowIndex % 2 == 1 ? Color.primary.opacity(0.05) : Color.clear)
                                .overlay { if bordered { Rectangle().stroke(Color.primary.opacity(0.2), lineWidth: 0.5) } }
                        }
                    }
                }
            }
        }
        .accessibilityIdentifier("\(identifier).table")
    }

    private func toggle(_ id: Int) {
        withAnimation(.easeInOut(duration: 0.2)) {
            if expanded.contains(id) { expanded.remove(id) } else { expanded.insert(id) }
        }
    }
}

private typealias SwiftUIAttr = AttributeScopes.SwiftUIAttributes

extension EnvironmentValues {
    /// 预览气泡内的强调色（引用竖条与底色、代码块语言名、链接、变量底色）。默认 App 强调色，气泡里设为 Telegram 名字蓝。
    @Entry var telegramAccent: Color = .accentColor
}

enum TelegramAttributed {
    /// `accent`：链接、引用标记与变量底色用的强调色；气泡里取 Telegram 的名字蓝，不跟 App 的橙色。
    static func string(_ runs: [TelegramInlineRun], revealed: Set<Int>, font base: Font, accent: Color = .accentColor) -> AttributedString {
        var result = AttributedString()
        for run in runs {
            let style = run.style
            let hidden = style.spoiler.map { !revealed.contains($0) } ?? false
            var text = run.text
            if hidden { text = String(text.map { $0 == "\n" ? "\n" : "░" }) }
            var piece = AttributedString(text)
            var font = base
            if style.code || style.math { font = .system(.subheadline, design: .monospaced) }
            if style.sub || style.sup { font = .caption2 }
            if style.bold { font = font.bold() }
            if style.italic { font = font.italic() }
            piece[SwiftUIAttr.FontAttribute.self] = font
            if style.underline { piece[SwiftUIAttr.UnderlineStyleAttribute.self] = .single }
            if style.strike { piece[SwiftUIAttr.StrikethroughStyleAttribute.self] = .single }
            if style.sub { piece[SwiftUIAttr.BaselineOffsetAttribute.self] = -3 }
            if style.sup { piece[SwiftUIAttr.BaselineOffsetAttribute.self] = 5 }
            if style.code { piece[SwiftUIAttr.BackgroundColorAttribute.self] = Color.primary.opacity(0.08) }
            if style.mark { piece[SwiftUIAttr.BackgroundColorAttribute.self] = Color.yellow.opacity(0.35) }
            if style.thinking { piece[SwiftUIAttr.ForegroundColorAttribute.self] = .secondary }
            if style.accent { piece[SwiftUIAttr.ForegroundColorAttribute.self] = accent }
            if let link = style.link {
                if let url = URL(string: link), !link.isEmpty {
                    piece[AttributeScopes.FoundationAttributes.LinkAttribute.self] = url
                } else {
                    piece[SwiftUIAttr.ForegroundColorAttribute.self] = accent
                    piece[SwiftUIAttr.UnderlineStyleAttribute.self] = .single
                }
            }
            if style.variable { piece[SwiftUIAttr.BackgroundColorAttribute.self] = accent.opacity(0.2) }
            if style.missing { piece[SwiftUIAttr.BackgroundColorAttribute.self] = Color.orange.opacity(0.28) }
            if hidden {
                piece[SwiftUIAttr.ForegroundColorAttribute.self] = .secondary
                piece[SwiftUIAttr.BackgroundColorAttribute.self] = Color.secondary.opacity(0.22)
                piece[AttributeScopes.FoundationAttributes.LinkAttribute.self] = nil
            }
            result += piece
        }
        return result
    }
}

// MARK: - 编辑器

/// 字数状态行：`已用 123 / 4096`，接近上限橙色、超出红色并说明。
struct TelegramLengthBadge: View {
    let model: TelegramMessagePreviewModel
    let identifier: String
    var body: some View {
        let color: Color = model.isOverLimit ? .red : model.isNearLimit ? .orange : .secondary
        let kind = model.limit == TelegramHTML.captionLimit ? "图片说明" : "消息"
        // 用 String 拼好再交给 Text：Text 的字符串插值会按地区给数字加千分位（4,096）。
        let summary: String = model.isOverLimit
            ? "\(model.length) / \(model.limit) · 超出\(kind)上限 \(model.length - model.limit) 字，Telegram 会拒绝发送"
            : "\(model.length) / \(model.limit) 字（\(kind)上限）"
        let spoken: String = (model.isOverLimit ? "字数超出上限：" : "已用字数：") + "\(model.length) / \(model.limit)"
        Label {
            Text(summary)
        } icon: {
            Image(systemName: model.isOverLimit ? "exclamationmark.octagon.fill" : model.isNearLimit ? "exclamationmark.triangle.fill" : "character.cursor.ibeam")
        }
        .font(.caption.monospacedDigit())
        .foregroundStyle(color)
        .accessibilityElement(children: .ignore)
        .accessibilityLabel(spoken)
        .accessibilityIdentifier("\(identifier).counter")
    }
}

/// 一条格式问题：图标 + 说明 + 位置，并把出问题的那一行标出来。
struct TelegramIssueRow: View {
    let issue: TelegramHTMLIssue
    let source: String
    var body: some View {
        let excerpt = issue.lineExcerpt(in: source)
        VStack(alignment: .leading, spacing: 4) {
            Label {
                Text(issue.message).font(.footnote)
            } icon: {
                Image(systemName: symbol).foregroundStyle(color)
            }
            if !excerpt.text.isEmpty {
                HStack(alignment: .firstTextBaseline, spacing: 6) {
                    Text(issue.locationText).font(.caption2).foregroundStyle(.secondary)
                    Text(highlighted(excerpt)).font(.caption.monospaced()).lineLimit(2)
                }
            }
        }
        .accessibilityElement(children: .combine)
    }
    private var symbol: String {
        switch issue.severity {
        case .error: "xmark.octagon.fill"
        case .warning: "exclamationmark.triangle.fill"
        case .info: "info.circle"
        }
    }
    private var color: Color {
        switch issue.severity {
        case .error: .red
        case .warning: .orange
        case .info: .secondary
        }
    }
    private func highlighted(_ excerpt: (text: String, range: Range<Int>)) -> AttributedString {
        let scalars = Array(excerpt.text.unicodeScalars)
        // 只显示问题附近 30 个字符，长行不撑满。
        let lower = max(0, excerpt.range.lowerBound - 15)
        let upper = min(scalars.count, excerpt.range.upperBound + 15)
        func slice(_ from: Int, _ to: Int) -> String {
            var view = String.UnicodeScalarView()
            if from < to { view.append(contentsOf: scalars[from..<to]) }
            return String(view)
        }
        var result = AttributedString((lower > 0 ? "…" : "") + slice(lower, excerpt.range.lowerBound))
        var mark = AttributedString(slice(excerpt.range.lowerBound, excerpt.range.upperBound))
        mark[SwiftUIAttr.BackgroundColorAttribute.self] = color.opacity(0.3)
        mark[SwiftUIAttr.UnderlineStyleAttribute.self] = .single
        result += mark
        result += AttributedString(slice(excerpt.range.upperBound, upper) + (upper < scalars.count ? "…" : ""))
        return result
    }
}

/// 表单行内的模板字段：输入框 + 字数 + 问题摘要 + 实时气泡预览；「展开编辑」进入完整编辑页（分屏 / 编辑 / 预览、示例值、外观）。
struct TelegramTemplateInlineField: View {
    let title: String
    /// 在输入框上方显示标题（第 21 轮：插件配置等没有同名分组标题的地方必须显示，否则填了模板就看不出是哪一项）。
    /// 分组标题已经写着同一个名字时（交互 › 查询指令与通知模板）保持 false，避免重复。
    var showsTitle = false
    @Binding var text: String
    let spec: TelegramTemplateSpec
    var identifier: String
    /// 同一条消息由几个字段组成时（玩法查询），只让第一个字段默认展开预览。
    var previewInitiallyShown = true
    @State private var showingEditor = false
    @State private var previewToggled = false
    private var showsPreview: Bool { previewInitiallyShown != previewToggled }

    var body: some View {
        let models = spec.models(for: text)
        VStack(alignment: .leading, spacing: 8) {
            if showsTitle {
                Text(title)
                    .font(.subheadline.weight(.medium))
                    .foregroundStyle(.primary)
                    .accessibilityIdentifier("\(identifier).title")
            }
            TextField(spec.defaultTemplate ?? title, text: $text, axis: .vertical)
                .font(.callout.monospaced())
                .lineLimit(2...10)
                .textInputAutocapitalization(.never).autocorrectionDisabled()
                .accessibilityLabel(title)
                .accessibilityIdentifier("\(identifier).editor")
            HStack(spacing: 8) {
                TelegramLengthBadge(model: models.preview, identifier: identifier)
                Spacer(minLength: 4)
                if models.diagnostics.warningCount > 0 {
                    Label("\(models.diagnostics.warningCount) 个格式问题", systemImage: "exclamationmark.triangle.fill")
                        .font(.caption).foregroundStyle(.orange)
                        .accessibilityElement(children: .combine)
                        .accessibilityIdentifier("\(identifier).issueCount")
                }
            }
            if let first = models.diagnostics.issues.first(where: { $0.severity >= .warning }) {
                TelegramIssueRow(issue: first, source: text)
                    .accessibilityIdentifier("\(identifier).firstIssue")
            }
            HStack {
                Button(showsPreview ? "收起预览" : "显示预览", systemImage: showsPreview ? "eye.slash" : "eye") {
                    withAnimation { previewToggled.toggle() }
                }
                .accessibilityIdentifier("\(identifier).togglePreview")
                Spacer()
                Button("展开编辑", systemImage: "arrow.up.left.and.arrow.down.right") { showingEditor = true }
                    .accessibilityIdentifier("\(identifier).expand")
            }
            .buttonStyle(.borderless)
            .font(.caption)
            if showsPreview {
                TelegramChatPreview(model: models.preview, botName: spec.botName, prompt: nil, buttons: spec.buttons,
                                    identifier: "\(identifier).preview")
            }
        }
        .sheet(isPresented: $showingEditor) {
            NavigationStack {
                TelegramTemplateEditorPage(title: title, text: $text, spec: spec, identifier: identifier)
                    .toolbar {
                        ToolbarItem(placement: .confirmationAction) {
                            Button("完成") { showingEditor = false }.accessibilityIdentifier("\(identifier).done")
                        }
                    }
            }
        }
    }
}

/// 完整的模板编辑页：分屏（默认）/ 只编辑 / 只预览，格式检查标出位置，变量示例值可改，预览外观可切换浅色／深色。
struct TelegramTemplateEditorPage<Extra: View>: View {
    enum Layout: String, CaseIterable, Identifiable {
        case split, edit, preview
        var id: String { rawValue }
        var title: String {
            switch self {
            case .split: "分屏"
            case .edit: "编辑"
            case .preview: "预览"
            }
        }
    }

    let title: String
    @Binding var text: String
    let spec: TelegramTemplateSpec
    var identifier: String
    var footer: String?
    /// 页面末尾附加的分组（模板实验室的服务器渲染与测试发送）；参数是预览模型与当前示例值。
    let extra: (TelegramMessagePreviewModel, [String: String]) -> Extra
    @State private var layout: Layout = .split
    @State private var overrides: [String: String] = [:]
    @State private var appearance: TelegramPreviewAppearance = .system

    init(title: String, text: Binding<String>, spec: TelegramTemplateSpec, identifier: String, footer: String? = nil,
         @ViewBuilder extra: @escaping (TelegramMessagePreviewModel, [String: String]) -> Extra) {
        self.title = title
        _text = text
        self.spec = spec
        self.identifier = identifier
        self.footer = footer
        self.extra = extra
    }

    var body: some View {
        let models = spec.models(for: text, overrides: overrides)
        Form {
            Section {
                Picker("显示方式", selection: $layout) {
                    ForEach(Layout.allCases) { Text($0.title).tag($0) }
                }
                .pickerStyle(.segmented)
                .labelsHidden()
                .accessibilityIdentifier("\(identifier).layout")
            }
            if layout != .preview {
                Section {
                    TextField(spec.defaultTemplate ?? "输入消息模板", text: $text, axis: .vertical)
                        .font(.callout.monospaced())
                        .lineLimit(4...16)
                        .textInputAutocapitalization(.never).autocorrectionDisabled()
                        .accessibilityLabel(title)
                        .accessibilityIdentifier("\(identifier).page.editor")
                    TelegramLengthBadge(model: models.preview, identifier: "\(identifier).page")
                } header: {
                    Text("\(title) · \(spec.mode.title)")
                } footer: {
                    VStack(alignment: .leading, spacing: 2) {
                        if let footer { Text(footer) }
                        if text.isEmpty, spec.defaultTemplate != nil { Text("当前留空，预览显示的是默认模板。") }
                        if spec.mode != .html { Text("\(spec.mode.title) 模式按原文显示，与 Web 一致。") }
                    }
                }
                Section {
                    if models.diagnostics.issues.isEmpty {
                        Label("没有发现格式问题", systemImage: "checkmark.circle.fill").foregroundStyle(.green)
                            .accessibilityIdentifier("\(identifier).noIssues")
                    } else {
                        ForEach(Array(models.diagnostics.issues.enumerated()), id: \.element.id) { index, issue in
                            TelegramIssueRow(issue: issue, source: text)
                                .accessibilityIdentifier("\(identifier).issue.\(index)")
                        }
                    }
                } header: { Text("格式检查") } footer: {
                    Text("按 Telegram HTML 规则检查：标签要成对且不能交叉，只能用 Telegram 支持的标签；“<”“&” 不作标签时要写成 &lt; &amp;。")
                }
            }
            if layout != .edit {
                Section {
                    TelegramChatPreview(model: models.preview, botName: spec.botName, prompt: spec.prompt, buttons: spec.buttons,
                                        appearance: appearance, identifier: "\(identifier).page.preview")
                        .listRowInsets(EdgeInsets(top: 8, leading: 8, bottom: 8, trailing: 8))
                    if layout == .preview { TelegramLengthBadge(model: models.preview, identifier: "\(identifier).page.previewCounter") }
                } header: {
                    HStack {
                        Text("预览")
                        Spacer()
                        Picker("外观", selection: $appearance) {
                            ForEach(TelegramPreviewAppearance.allCases) { Text($0.title).tag($0) }
                        }
                        .pickerStyle(.menu)
                        .textCase(nil)
                        .accessibilityIdentifier("\(identifier).appearance")
                    }
                } footer: {
                    Text("变量用示例值代入（浅色底）；橙色底是没有示例值的变量。点按灰色块揭开隐藏文字，链接在预览中不会打开。")
                }
            }
            if !models.diagnostics.variables.isEmpty || !models.preview.variables.isEmpty {
                Section {
                    ForEach(variableNames(models), id: \.self) { name in
                        LabeledContent {
                            TextField("示例值", text: overrideBinding(name), axis: .vertical)
                                .multilineTextAlignment(.trailing)
                                .lineLimit(1...4)
                                .accessibilityIdentifier("\(identifier).sample.\(name)")
                        } label: {
                            Text("{\(name)}").font(.callout.monospaced())
                        }
                    }
                    if !overrides.isEmpty {
                        Button("恢复默认示例值", systemImage: "arrow.uturn.backward") { overrides = [:] }
                    }
                } header: { Text("示例值") } footer: {
                    Text("只影响预览，不会保存。实际发送时由服务器填入真实内容。")
                }
            }
            extra(models.preview, spec.samples.merging(overrides) { _, new in new })
        }
        .navigationTitle(title).navigationBarTitleDisplayMode(.inline)
    }

    private func variableNames(_ models: (preview: TelegramMessagePreviewModel, diagnostics: TelegramMessagePreviewModel)) -> [String] {
        var seen: Set<String> = []
        return (models.diagnostics.variables + models.preview.variables).filter { seen.insert($0).inserted }
    }

    private func overrideBinding(_ name: String) -> Binding<String> {
        Binding(get: { overrides[name] ?? spec.samples[name] ?? "" }, set: { overrides[name] = $0 })
    }
}

extension TelegramTemplateEditorPage where Extra == EmptyView {
    init(title: String, text: Binding<String>, spec: TelegramTemplateSpec, identifier: String, footer: String? = nil) {
        self.init(title: title, text: text, spec: spec, identifier: identifier, footer: footer) { _, _ in EmptyView() }
    }
}

/// 只读的一组消息预览（插件配置的「预览结果」：按模板顺序显示一串 Bot 消息）。
struct TelegramPreviewThreadSection: View {
    struct Item: Identifiable {
        var id: String
        var title: String
        var model: TelegramMessagePreviewModel
    }
    let title: String
    let items: [Item]
    var prompt: String?
    var identifier = "tgThread"

    var body: some View {
        Section {
            TelegramChatPreview(messages: items.enumerated().map { index, item in
                TelegramChatPreview.Message(id: index, botName: item.title, nodes: item.model.nodes)
            }, prompt: prompt, identifier: identifier)
            .listRowInsets(EdgeInsets(top: 8, leading: 8, bottom: 8, trailing: 8))
            ForEach(items.filter { $0.model.isOverLimit }) { item in
                TelegramLengthBadge(model: item.model, identifier: "\(identifier).\(item.id)")
            }
        } header: { Text(title) } footer: {
            Text("按模板顺序展示一组 Telegram 消息，随上方的修改实时更新（尚未保存）。")
        }
    }
}
