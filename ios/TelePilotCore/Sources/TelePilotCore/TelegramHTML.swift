import Foundation

/// Telegram HTML 消息解析：与 Web `frontend/src/components/TelegramHtmlPreview.tsx`（`sanitizeTelegramHtml`）同一套规则。
///
/// - `sanitizeToWebHTML(_:)` 逐条移植 Web 的白名单清洗，输出与 Web 完全相同的 HTML 字符串，
///   由 `Tests/TelePilotCoreTests/telegram-html-cases.json`（Web 实际输出生成，前端测试同时校验）保证两端一致。
/// - `parse(_:)` 用同一次扫描得到原生渲染用的节点树，并给出带行列位置的中文警告（不闭合、交叉嵌套、不支持的标签等）。
///
/// 与 Web 的唯一有意差异：Web 先整体转义再“还原实体”，原文里写成实体的 `&lt;b&gt;` 会被 Web 误当成真标签；
/// 这里按原文扫描标签，实体只当文字（与 Telegram 实际显示一致）。代码块语言名也允许中文等非 ASCII 字符
/// （Telegram 客户端会显示任意语言名，Web 只认 ASCII）。
public enum TelegramHTML {
    // MARK: - 公共结果

    public struct Document: Equatable, Sendable {
        public var nodes: [TelegramNode]
        public var issues: [TelegramHTMLIssue]
        public init(nodes: [TelegramNode], issues: [TelegramHTMLIssue]) {
            self.nodes = nodes
            self.issues = issues
        }
        /// 去掉标签后的纯文本（Telegram 按它计算长度）。
        public var plainText: String { TelegramNode.plainText(nodes) }
        /// Telegram 口径的长度（UTF-16 码元数，与 Bot API 的实体偏移同一单位）。
        public var length: Int { TelegramHTML.telegramLength(plainText) }
        /// 是否用到了只有富文本消息（Rich Message）才支持的标签。
        public var usesRichTags: Bool { issues.contains { $0.kind == .richOnly } }
    }

    /// 普通文字消息上限。
    public static let messageLimit = 4096
    /// 带图片／文件的说明文字（caption）上限。
    public static let captionLimit = 1024

    public static func telegramLength(_ text: String) -> Int {
        text.trimmingCharacters(in: .whitespacesAndNewlines).utf16.count
    }

    // MARK: - 字符工具（与 JS 正则语义一致）

    typealias Scalars = [Unicode.Scalar]

    /// JS `\s`：`[\t\n\v\f\r \u00a0\u1680\u2000-\u200a\u2028\u2029\u202f\u205f\u3000\ufeff]`。
    static func isJSWhitespace(_ s: Unicode.Scalar) -> Bool {
        switch s.value {
        case 0x09...0x0D, 0x20, 0xA0, 0x1680, 0x2000...0x200A, 0x2028, 0x2029, 0x202F, 0x205F, 0x3000, 0xFEFF: return true
        default: return false
        }
    }
    static func isASCIILetter(_ s: Unicode.Scalar) -> Bool {
        (0x41...0x5A).contains(s.value) || (0x61...0x7A).contains(s.value)
    }
    static func isASCIIDigit(_ s: Unicode.Scalar) -> Bool { (0x30...0x39).contains(s.value) }
    static func isHexDigit(_ s: Unicode.Scalar) -> Bool {
        isASCIIDigit(s) || (0x41...0x46).contains(s.value) || (0x61...0x66).contains(s.value)
    }
    static func lowerASCII(_ s: Unicode.Scalar) -> Unicode.Scalar {
        (0x41...0x5A).contains(s.value) ? Unicode.Scalar(s.value + 32)! : s
    }
    static func string(_ scalars: ArraySlice<Unicode.Scalar>) -> String {
        var view = String.UnicodeScalarView()
        view.append(contentsOf: scalars)
        return String(view)
    }
    static func string(_ scalars: Scalars) -> String { string(scalars[...]) }
    /// ASCII 不区分大小写地比较 `a[at...]` 是否以 `word` 开头（`word` 用小写）。
    static func hasPrefixCI(_ a: Scalars, at index: Int, _ word: String) -> Bool {
        let w = Array(word.unicodeScalars)
        guard index >= 0, index + w.count <= a.count else { return false }
        for (offset, scalar) in w.enumerated() where lowerASCII(a[index + offset]) != scalar { return false }
        return true
    }
    /// JS `String.prototype.trim()`。
    static func jsTrim(_ value: String) -> String {
        let a = Array(value.unicodeScalars)
        var start = 0, end = a.count
        while start < end, isJSWhitespace(a[start]) || a[start] == "\u{2028}" { start += 1 }
        while end > start, isJSWhitespace(a[end - 1]) { end -= 1 }
        return string(a[start..<end])
    }

    // MARK: - 转义与实体

    static func escapeHTML(_ value: String) -> String {
        var out = ""
        out.reserveCapacity(value.utf8.count)
        for scalar in value.unicodeScalars {
            switch scalar {
            case "&": out += "&amp;"
            case "<": out += "&lt;"
            case ">": out += "&gt;"
            case "\"": out += "&quot;"
            case "'": out += "&#39;"
            default: out.unicodeScalars.append(scalar)
            }
        }
        return out
    }

    static let restorableNames = ["lt", "gt", "amp", "quot", "apos", "nbsp", "hellip", "mdash", "ndash", "lsquo", "rsquo", "ldquo", "rdquo"]

    /// 在 `a[index]`（`&` 之后）处匹配实体主体：`#x十六进制+` / `#数字+` / 给定名称之一，后接 `;`。返回主体结束（`;` 的下标）。
    static func matchEntityBody(_ a: Scalars, at index: Int, names: [String]) -> Int? {
        guard index < a.count else { return nil }
        if a[index] == "#" {
            var j = index + 1
            if j < a.count, lowerASCII(a[j]) == "x" {
                var k = j + 1
                while k < a.count, isHexDigit(a[k]) { k += 1 }
                if k > j + 1, k < a.count, a[k] == ";" { return k }
            }
            j = index + 1
            var k = j
            while k < a.count, isASCIIDigit(a[k]) { k += 1 }
            if k > j, k < a.count, a[k] == ";" { return k }
            return nil
        }
        for name in names where hasPrefixCI(a, at: index, name) {
            let end = index + name.unicodeScalars.count
            if end < a.count, a[end] == ";" { return end }
        }
        return nil
    }

    /// Web `restoreTelegramEntities`：把转义后的 `&amp;实体;` 还原为 `&实体;`。
    static func restoreEntities(_ escaped: String) -> String {
        let a = Array(escaped.unicodeScalars)
        var out = String.UnicodeScalarView()
        var i = 0
        while i < a.count {
            if a[i] == "&", hasPrefixCI(a, at: i, "&amp;"), let end = matchEntityBody(a, at: i + 5, names: restorableNames) {
                out.append("&")
                out.append(contentsOf: a[(i + 5)...end])
                i = end + 1
            } else {
                out.append(a[i])
                i += 1
            }
        }
        return String(out)
    }

    /// 解析数字实体；JS `Number.isSafeInteger && 0...0x10FFFF` 之外返回 nil。
    static func numericEntityValue(_ body: ArraySlice<Unicode.Scalar>) -> UInt32? {
        guard body.first == "#" else { return nil }
        var digits = body.dropFirst()
        var radix: UInt64 = 10
        if let first = digits.first, lowerASCII(first) == "x" { radix = 16; digits = digits.dropFirst() }
        var value: UInt64 = 0
        for scalar in digits {
            let digit: UInt64
            switch scalar.value {
            case 0x30...0x39: digit = UInt64(scalar.value - 0x30)
            case 0x41...0x46: digit = UInt64(scalar.value - 0x41 + 10)
            case 0x61...0x66: digit = UInt64(scalar.value - 0x61 + 10)
            default: return nil
            }
            value = value * radix + digit
            if value > 0x10FFFF { return nil }
        }
        return UInt32(value)
    }

    /// Web `decodeHtmlAttribute`（属性值里的 `&quot;` 等还原成字符）。
    static func decodeAttribute(_ value: String) -> String {
        let a = Array(value.unicodeScalars)
        var out = String.UnicodeScalarView()
        var i = 0
        while i < a.count {
            if a[i] == "&", let end = matchEntityBody(a, at: i + 1, names: ["quot", "apos", "amp", "lt", "gt"]) {
                let body = a[(i + 1)..<end]
                let name = string(body).lowercased()
                switch name {
                case "quot": out.append("\"")
                case "apos": out.append("'")
                case "amp": out.append("&")
                case "lt": out.append("<")
                case "gt": out.append(">")
                default:
                    if let code = numericEntityValue(body), let scalar = Unicode.Scalar(code) { out.append(scalar) }
                }
                i = end + 1
            } else {
                out.append(a[i])
                i += 1
            }
        }
        return String(out)
    }

    /// 文字里的实体（与浏览器显示 Web 输出时一致：只还原 Web 放行的实体，其余原样显示）。
    static func decodeText(_ value: String) -> String {
        let a = Array(value.unicodeScalars)
        var out = String.UnicodeScalarView()
        var i = 0
        while i < a.count {
            if a[i] == "&", let end = matchEntityBody(a, at: i + 1, names: restorableNames) {
                let body = a[(i + 1)..<end]
                switch string(body).lowercased() {
                case "lt": out.append("<")
                case "gt": out.append(">")
                case "amp": out.append("&")
                case "quot": out.append("\"")
                case "apos": out.append("'")
                case "nbsp": out.append("\u{00A0}")
                case "hellip": out.append("\u{2026}")
                case "mdash": out.append("\u{2014}")
                case "ndash": out.append("\u{2013}")
                case "lsquo": out.append("\u{2018}")
                case "rsquo": out.append("\u{2019}")
                case "ldquo": out.append("\u{201C}")
                case "rdquo": out.append("\u{201D}")
                default:
                    if let code = numericEntityValue(body), code != 0, let scalar = Unicode.Scalar(code) { out.append(scalar) }
                    else { out.append("\u{FFFD}") }
                }
                i = end + 1
            } else {
                out.append(a[i])
                i += 1
            }
        }
        return String(out)
    }

    // MARK: - 属性（作用于转义后的属性串，与 Web 一致）

    /// 依次尝试 `(?:^|\s)NAME` 的每个起点，返回 NAME 结束位置。
    private static func attributeNameEnds(_ a: Scalars, name: String) -> [Int] {
        let n = name.lowercased()
        let length = n.unicodeScalars.count
        var ends: [Int] = []
        var p = 0
        while p <= a.count {
            if p == 0, hasPrefixCI(a, at: 0, n) { ends.append(length) }
            if p < a.count, isJSWhitespace(a[p]), hasPrefixCI(a, at: p + 1, n) { ends.append(p + 1 + length) }
            p += 1
        }
        return ends
    }

    /// Web `readEscapedAttribute`。
    static func readAttribute(_ escapedAttrs: String, _ name: String) -> String? {
        let a = Array(escapedAttrs.unicodeScalars)
        for nameEnd in attributeNameEnds(a, name: name) {
            var r = nameEnd
            while r < a.count, isJSWhitespace(a[r]) { r += 1 }
            guard r < a.count, a[r] == "=" else { continue }
            r += 1
            while r < a.count, isJSWhitespace(a[r]) { r += 1 }
            for quote in ["&quot;", "&#39;"] where hasPrefixCI(a, at: r, quote) {
                let start = r + quote.unicodeScalars.count
                var k = start
                while k < a.count {
                    if hasPrefixCI(a, at: k, quote) { return decodeAttribute(string(a[start..<k])) }
                    k += 1
                }
            }
            var k = r
            while k < a.count, !isJSWhitespace(a[k]) { k += 1 }
            if k > r { return decodeAttribute(string(a[r..<k])) }
        }
        return nil
    }

    /// Web `hasEscapedAttribute`。
    static func hasAttribute(_ escapedAttrs: String, _ name: String) -> Bool {
        let a = Array(escapedAttrs.unicodeScalars)
        for nameEnd in attributeNameEnds(a, name: name) {
            if nameEnd == a.count { return true }
            let next = a[nameEnd]
            if isJSWhitespace(next) || next == "=" || next == "/" { return true }
        }
        return false
    }

    static func safeLanguage(_ attrs: String) -> String? {
        guard let value = readAttribute(attrs, "class") else { return nil }
        let a = Array(value.unicodeScalars)
        guard hasPrefixCI(a, at: 0, "language-") else { return nil }
        let rest = a.dropFirst(9)
        guard (1...40).contains(rest.count) else { return nil }
        for s in rest where !(isASCIILetter(s) || isASCIIDigit(s) || s == "_" || s == "+" || s == "." || s == "-") { return nil }
        return string(rest)
    }

    /// 原生显示用的语言名：在 Web 规则基础上也接受中文等非 ASCII 名称（不含空白与 HTML 特殊字符）。
    static func displayLanguage(_ attrs: String) -> String? {
        if let strict = safeLanguage(attrs) { return strict }
        guard let value = readAttribute(attrs, "class") else { return nil }
        let a = Array(value.unicodeScalars)
        guard hasPrefixCI(a, at: 0, "language-") else { return nil }
        let rest = a.dropFirst(9)
        guard (1...40).contains(rest.count) else { return nil }
        for s in rest where isJSWhitespace(s) || "<>\"'&".unicodeScalars.contains(s) { return nil }
        return string(rest)
    }

    static func safeHref(_ attrs: String) -> String? {
        guard let raw = readAttribute(attrs, "href") else { return nil }
        let href = jsTrim(raw)
        guard !href.isEmpty else { return nil }
        let a = Array(href.unicodeScalars)
        for prefix in ["https:", "http:", "mailto:", "tel:", "tg:", "#"] where hasPrefixCI(a, at: 0, prefix) { return href }
        return nil
    }

    static func safeNumber(_ attrs: String, _ name: String, maxDigits: Int = 4) -> String? {
        guard let raw = readAttribute(attrs, name) else { return nil }
        let value = jsTrim(raw)
        let a = Array(value.unicodeScalars)
        guard (1...maxDigits).contains(a.count), a.allSatisfy(isASCIIDigit) else { return nil }
        return value
    }

    static func safeListType(_ attrs: String) -> String? {
        guard let raw = readAttribute(attrs, "type") else { return nil }
        let value = jsTrim(raw)
        return ["a", "A", "i", "I", "1"].contains(value) ? value : nil
    }

    static func safeAlign(_ attrs: String, vertical: Bool) -> String? {
        guard let raw = readAttribute(attrs, vertical ? "valign" : "align") else { return nil }
        let value = jsTrim(raw).lowercased()
        let values = vertical ? ["top", "middle", "bottom"] : ["left", "center", "right"]
        return values.contains(value) ? value : nil
    }

    // MARK: - 扫描

    struct RawTag: Equatable {
        var closing: Bool
        /// 小写标签名。
        var name: String
        /// 原文里的标签名（报错时照原样显示）。
        var rawName: String
        /// 转义并还原实体后、去掉末尾 `/` 的属性串（Web 的 `attrs`）。
        var attrs: String
        /// 原文中的位置（Unicode 标量下标，左闭右开）。
        var start: Int
        var end: Int
    }

    enum Piece: Equatable {
        case text(String, start: Int, end: Int)
        case tag(RawTag)
        /// 不构成标签的 `<`（仍作为文字，仅用于提示）。
        case strayLessThan(Int)
    }

    /// Web 的标签正则 `&lt;(\/?)\s*([a-z][a-z0-9-]*)([\s\S]*?)&gt;`（作用于原文）。
    static func pieces(_ input: String) -> [Piece] {
        let a = Array(input.unicodeScalars)
        var result: [Piece] = []
        var textStart = 0
        var i = 0
        func flush(_ end: Int) {
            if end > textStart { result.append(.text(string(a[textStart..<end]), start: textStart, end: end)) }
        }
        while i < a.count {
            guard a[i] == "<" else { i += 1; continue }
            if let tag = matchTag(a, at: i) {
                flush(i)
                result.append(.tag(tag))
                i = tag.end
                textStart = i
            } else {
                result.append(.strayLessThan(i))
                i += 1
            }
        }
        flush(a.count)
        // strayLessThan 只是提示标记，文字本身仍在 text 里；按位置排序便于诊断。
        return result
    }

    private static func matchTag(_ a: Scalars, at index: Int) -> RawTag? {
        var j = index + 1
        var closing = false
        if j < a.count, a[j] == "/" { closing = true; j += 1 }
        var k = j
        while k < a.count, isJSWhitespace(a[k]) { k += 1 }
        if k >= a.count || !isASCIILetter(a[k]) {
            // `(\/?)` 回溯为空后再试一次：`</` 后不是字母时整体不是标签。
            return nil
        }
        let nameStart = k
        k += 1
        while k < a.count, isASCIILetter(a[k]) || isASCIIDigit(a[k]) || a[k] == "-" { k += 1 }
        let nameEnd = k
        var close = k
        while close < a.count, a[close] != ">" { close += 1 }
        guard close < a.count else { return nil }
        let rawName = string(a[nameStart..<nameEnd])
        var attrs = restoreEntities(escapeHTML(string(a[nameEnd..<close])))
        attrs = stripTrailingSlash(attrs)
        return RawTag(closing: closing, name: rawName.lowercased(), rawName: rawName, attrs: attrs, start: index, end: close + 1)
    }

    /// `attrs.replace(/\/\s*$/, "")`。
    static func stripTrailingSlash(_ value: String) -> String {
        var a = Array(value.unicodeScalars)
        var end = a.count
        while end > 0, isJSWhitespace(a[end - 1]) { end -= 1 }
        guard end > 0, a[end - 1] == "/" else { return value }
        a.removeSubrange((end - 1)...)
        return string(a)
    }

    // MARK: - Web HTML（逐条移植 richTag）

    static func richTag(_ tag: String, _ attrs: String, closing: Bool) -> String {
        if closing {
            switch tag {
            case "b", "strong": return "</strong>"
            case "i", "em": return "</em>"
            case "u", "ins": return "</u>"
            case "s", "strike", "del": return "</del>"
            case "a", "tg-emoji", "tg-time", "tg-reference", "tg-math", "tg-thinking": return "</span>"
            case "tg-spoiler": return "</button>"
            case "tg-math-block": return "</pre>"
            case "img", "video", "audio", "tg-map", "input": return ""
            case "tg-collage", "tg-slideshow": return "</div>"
            case "table": return "</table></div>"
            case "blockquote": return hasAttribute(attrs, "expandable") ? "</blockquote></details>" : "</blockquote>"
            default:
                return closingPassthrough.contains(tag) ? "</\(tag)>" : ""
            }
        }
        switch tag {
        case "b", "strong": return "<strong>"
        case "i", "em": return "<em>"
        case "u", "ins": return "<u>"
        case "s", "strike", "del": return "<del>"
        case "code":
            if let language = safeLanguage(attrs) {
                return "<span class=\"telegram-code-language\">\(escapeHTML(language))</span><code class=\"telegram-code-language-\(language)\">"
            }
            return "<code>"
        case "pre": return "<pre class=\"telegram-pre\">"
        case "mark": return "<mark>"
        case "sub": return "<sub>"
        case "sup": return "<sup>"
        case "tg-spoiler": return "<button type=\"button\" class=\"telegram-spoiler\" aria-label=\"显示隐藏文本\">"
        case "a":
            let href = safeHref(attrs)
            let name = nonEmpty(readAttribute(attrs, "name"))
            if let title = href ?? name.map({ "#\($0)" }) {
                return "<span class=\"telegram-link\" title=\"\(escapeHTML(title))\">"
            }
            return "<span class=\"telegram-link\">"
        case "tg-emoji":
            if let id = nonEmpty(readAttribute(attrs, "emoji-id")) {
                return "<span class=\"telegram-custom-emoji\" title=\"自定义 Emoji \(escapeHTML(id))\">"
            }
            return "<span class=\"telegram-custom-emoji\">"
        case "tg-time":
            let unix = safeNumber(attrs, "unix", maxDigits: 12)
            let format = readAttribute(attrs, "format")
            var title = "Telegram 时间"
            if let unix {
                title += " " + unix
                if let format, !format.isEmpty { title += " · " + format }
            }
            return "<span class=\"telegram-time\" title=\"\(escapeHTML(title))\">"
        case "tg-reference": return "<span class=\"telegram-reference\">"
        case "tg-math": return "<span class=\"telegram-math\">"
        case "tg-math-block": return "<pre class=\"telegram-math-block\">"
        case "tg-thinking": return "<span class=\"telegram-thinking\">"
        case "blockquote":
            return hasAttribute(attrs, "expandable")
                ? "<details class=\"telegram-expandable-quote\"><summary>展开引用</summary><blockquote class=\"telegram-quote\">"
                : "<blockquote class=\"telegram-quote\">"
        case "aside": return "<aside class=\"telegram-pullquote\">"
        case "details": return "<details class=\"telegram-details\"" + (hasAttribute(attrs, "open") ? " open" : "") + ">"
        case "summary": return "<summary class=\"telegram-details-summary\">"
        case "table":
            let variant = [hasAttribute(attrs, "bordered") ? "telegram-table--bordered" : "",
                           hasAttribute(attrs, "striped") ? "telegram-table--striped" : ""].filter { !$0.isEmpty }.joined(separator: " ")
            let classes = variant.isEmpty ? "telegram-table" : "telegram-table " + variant
            return "<div class=\"telegram-table-scroll\"><table class=\"" + classes + "\">"
        case "th", "td":
            var extra = ""
            if let colspan = safeNumber(attrs, "colspan") { extra += " colspan=\"\(colspan)\"" }
            if let rowspan = safeNumber(attrs, "rowspan") { extra += " rowspan=\"\(rowspan)\"" }
            if let align = safeAlign(attrs, vertical: false) { extra += " data-align=\"\(align)\"" }
            if let valign = safeAlign(attrs, vertical: true) { extra += " data-valign=\"\(valign)\"" }
            return "<\(tag)\(extra)>"
        case "ol":
            let start = safeNumber(attrs, "start")
            let type = safeListType(attrs)
            let reversed = hasAttribute(attrs, "reversed")
            var html = "<ol"
            if let start { html += " start=\"" + start + "\"" }
            if let type { html += " type=\"" + type + "\"" }
            if reversed { html += " reversed" }
            return html + ">"
        case "input":
            guard (readAttribute(attrs, "type") ?? "").lowercased() == "checkbox" else { return "" }
            let checked = hasAttribute(attrs, "checked")
            let state = checked ? " is-checked" : ""
            let mark = checked ? "✓" : ""
            return "<span class=\"telegram-checkbox" + state + "\" role=\"checkbox\" aria-checked=\"" + (checked ? "true" : "false") + "\">" + mark + "</span>"
        case "img", "video", "audio":
            let source = readAttribute(attrs, "src")
            let alt = readAttribute(attrs, "alt")
            if tag == "img", source?.hasPrefix("tg://emoji") == true || readAttribute(attrs, "class") == "emoji" {
                return "<span class=\"telegram-inline-emoji\">\(escapeHTML(nonEmpty(alt) ?? "🙂"))</span>"
            }
            let kind = mediaKindLabel(tag)
            let fallback = nonEmpty(alt) ?? (source?.hasPrefix("tg://emoji") == true ? "🙂" : kind)
            return "<span class=\"telegram-media-placeholder telegram-media-\(tag)\" title=\"富文本\(kind)占位\">\(escapeHTML(fallback))</span>"
        case "tg-map":
            return "<span class=\"telegram-map-placeholder\" title=\"富文本位置占位\">\(escapeHTML(mapLabel(attrs)))</span>"
        case "tg-collage": return "<div class=\"telegram-media-grid\">"
        case "tg-slideshow": return "<div class=\"telegram-media-grid telegram-media-slideshow\">"
        case "hr": return "<hr class=\"telegram-divider\" />"
        case "br": return "<br />"
        default:
            return openingPassthrough.contains(tag) ? "<\(tag)>" : ""
        }
    }

    static let closingPassthrough: Set<String> = [
        "code", "mark", "sub", "sup", "pre", "h1", "h2", "h3", "h4", "h5", "h6", "p", "footer", "ul",
        "ol", "li", "aside", "cite", "figure", "figcaption", "caption", "thead", "tbody", "tr", "th", "td",
        "details", "summary",
    ]
    static let openingPassthrough: Set<String> = [
        "h1", "h2", "h3", "h4", "h5", "h6", "p", "footer", "ul", "ol", "li", "figure", "figcaption", "caption",
        "cite", "thead", "tbody", "tr",
    ]

    static func nonEmpty(_ value: String?) -> String? {
        guard let value, !value.isEmpty else { return nil }
        return value
    }
    static func mediaKindLabel(_ tag: String) -> String { tag == "img" ? "图片" : tag == "video" ? "视频" : "音频" }
    static func mapLabel(_ attrs: String) -> String {
        if let lat = nonEmpty(readAttribute(attrs, "lat")), let long = nonEmpty(readAttribute(attrs, "long")) { return "\(lat), \(long)" }
        return "位置"
    }

    /// 每个标签片段在 Web 里的输出（处理 `span.tg-spoiler` 与 `blockquote expandable` 的配对栈）。
    static func webOutputs(_ pieces: [Piece]) -> [Int: String] {
        var outputs: [Int: String] = [:]
        var expandable: [Bool] = []
        var spoilerSpans: [Bool] = []
        for (index, piece) in pieces.enumerated() {
            guard case .tag(let tag) = piece else { continue }
            var attrs = tag.attrs
            if tag.name == "span" {
                if tag.closing {
                    outputs[index] = spoilerSpans.popLast() == true ? richTag("tg-spoiler", "", closing: true) : ""
                } else {
                    let isSpoiler = readAttribute(attrs, "class")?.lowercased() == "tg-spoiler"
                    spoilerSpans.append(isSpoiler)
                    outputs[index] = isSpoiler ? richTag("tg-spoiler", attrs, closing: false) : ""
                }
                continue
            }
            if tag.name == "blockquote" {
                if tag.closing { attrs = expandable.popLast() == true ? " expandable" : "" }
                else { expandable.append(hasAttribute(attrs, "expandable")) }
            }
            outputs[index] = richTag(tag.name, attrs, closing: tag.closing)
        }
        return outputs
    }

    static let structuralNames: Set<String> = [
        "h1", "h2", "h3", "h4", "h5", "h6", "p", "ul", "ol", "li", "pre", "blockquote", "aside", "details", "summary", "table",
        "thead", "tbody", "tr", "th", "td", "footer", "figure", "figcaption", "caption", "div", "hr",
    ]

    /// `html` 在 `index` 处是否是 `<` + 可选 `/` + 结构标签名 + 空白或 `>`。
    static func startsStructuralTag(_ a: Scalars, at index: Int) -> Bool {
        guard index < a.count, a[index] == "<" else { return false }
        var j = index + 1
        if j < a.count, a[j] == "/" { j += 1 }
        let nameStart = j
        while j < a.count, isASCIILetter(a[j]) || isASCIIDigit(a[j]) { j += 1 }
        guard j > nameStart, j < a.count, isJSWhitespace(a[j]) || a[j] == ">" else { return false }
        return structuralNames.contains(string(a[nameStart..<j]).lowercased())
    }

    /// `>\s+(?=<\/?结构标签(?:\s|>))` → `>`。
    static func stripStructuralWhitespace(_ html: String) -> String {
        let a = Array(html.unicodeScalars)
        var out = String.UnicodeScalarView()
        var i = 0
        while i < a.count {
            out.append(a[i])
            if a[i] == ">" {
                var j = i + 1
                while j < a.count, isJSWhitespace(a[j]) { j += 1 }
                if j > i + 1, startsStructuralTag(a, at: j) { i = j; continue }
            }
            i += 1
        }
        return String(out)
    }

    /// 与 Web `sanitizeTelegramHtml` 输出相同的 HTML 字符串（用于两端一致性测试）。
    public static func sanitizeToWebHTML(_ input: String) -> String {
        let pieces = pieces(input)
        let outputs = webOutputs(pieces)
        var html = ""
        for (index, piece) in pieces.enumerated() {
            switch piece {
            case .text(let text, _, _): html += restoreEntities(escapeHTML(text))
            case .tag: html += outputs[index] ?? ""
            case .strayLessThan: break
            }
        }
        return stripStructuralWhitespace(html)
    }

    // MARK: - 节点树与诊断

    /// 仅普通消息（sendMessage HTML）支持的标签；其余 Web 能渲染的都属于富文本消息。
    static let basicTags: Set<String> = ["b", "strong", "i", "em", "u", "ins", "s", "strike", "del", "span", "tg-spoiler", "a", "tg-emoji", "code", "pre", "blockquote"]
    static let voidTags: Set<String> = ["br", "hr", "img", "video", "audio", "tg-map", "input"]

    static func family(_ tag: String) -> String {
        switch tag {
        case "b", "strong": return "b"
        case "i", "em": return "i"
        case "u", "ins": return "u"
        case "s", "strike", "del": return "s"
        case "tg-spoiler": return "tg-spoiler"
        default: return tag
        }
    }

    private struct Frame {
        var kind: TelegramElementKind
        var family: String
        var tag: RawTag?
        var children: [TelegramNode] = []
    }

    public static func parse(_ input: String) -> Document {
        let scalars = Array(input.unicodeScalars)
        let pieces = pieces(input)
        let outputs = webOutputs(pieces)
        var issues: [TelegramHTMLIssue] = []
        var stack: [Frame] = [Frame(kind: .paragraph, family: "#root", tag: nil)]
        var spoilerSpans: [Bool] = []
        var expandable: [Bool] = []
        var reportedRich: Set<String> = []

        func position(_ offset: Int) -> (line: Int, column: Int) {
            var line = 1, column = 1
            var i = 0
            while i < offset, i < scalars.count {
                if scalars[i] == "\n" { line += 1; column = 1 } else { column += 1 }
                i += 1
            }
            return (line, column)
        }
        func issue(_ kind: TelegramHTMLIssue.Kind, _ severity: TelegramHTMLIssue.Severity, _ message: String, start: Int, end: Int) {
            guard issues.count < 50 else { return }
            let pos = position(start)
            issues.append(TelegramHTMLIssue(kind: kind, severity: severity, message: message, line: pos.line, column: pos.column, offset: start, length: max(1, end - start)))
        }
        func append(_ node: TelegramNode) { stack[stack.count - 1].children.append(node) }
        func closeTop() {
            let frame = stack.removeLast()
            append(.element(TelegramElement(kind: frame.kind, children: frame.children)))
        }
        func label(_ tag: RawTag) -> String { tag.closing ? "</\(tag.rawName)>" : "<\(tag.rawName)>" }
        func closeFamily(_ family: String, tag: RawTag) {
            guard let target = stack.lastIndex(where: { $0.family == family }), target > 0 else {
                issue(.stray, .warning, "</\(tag.rawName)> 前面没有对应的开始标签，Telegram 可能拒绝发送。", start: tag.start, end: tag.end)
                return
            }
            if target < stack.count - 1, let inner = stack.last?.tag {
                issue(.misnested, .warning, "</\(tag.rawName)> 与里面的 <\(inner.rawName)> 交叉嵌套，应先写 </\(inner.rawName)>。", start: tag.start, end: tag.end)
            }
            while stack.count - 1 > target { closeTop() }
            closeTop()
        }

        for (index, piece) in pieces.enumerated() {
            switch piece {
            case .strayLessThan(let offset):
                issue(.bareLessThan, .warning, "“<” 后面不是标签，Telegram 要求写成 &lt;，否则可能拒绝发送。", start: offset, end: offset + 1)
            case .text(let text, let start, let end):
                if text.unicodeScalars.allSatisfy(isJSWhitespace),
                   index > 0, case .tag = pieces[index - 1], (outputs[index - 1] ?? "").isEmpty == false,
                   let next = nextTagIndex(pieces, after: index), next == index + 1,
                   startsStructuralTag(Array((outputs[next] ?? "").unicodeScalars), at: 0) {
                    continue
                }
                checkAmpersands(text, base: start, end: end, issue: issue)
                append(.text(decodeText(text)))
            case .tag(let tag):
                let name = tag.name
                if !basicTags.contains(name), (outputs[index] ?? "").isEmpty == false, !tag.closing, !reportedRich.contains(name) {
                    reportedRich.insert(name)
                    issue(.richOnly, .info, "<\(tag.rawName)> 是富文本消息（Rich Message）标签，普通消息不支持，只在按富文本发送时生效。", start: tag.start, end: tag.end)
                }
                if name == "span" {
                    if tag.closing {
                        let hadOpen = !spoilerSpans.isEmpty
                        if spoilerSpans.popLast() == true {
                            closeFamily("tg-spoiler", tag: tag)
                        } else if !hadOpen {
                            // 不支持的 <span> 已在开始处提示；这里只提示完全多余的 </span>。
                            issue(.stray, .warning, "</\(tag.rawName)> 前面没有对应的开始标签，Telegram 可能拒绝发送。", start: tag.start, end: tag.end)
                        }
                    } else {
                        let isSpoiler = readAttribute(tag.attrs, "class")?.lowercased() == "tg-spoiler"
                        spoilerSpans.append(isSpoiler)
                        if isSpoiler { stack.append(Frame(kind: .spoiler, family: "tg-spoiler", tag: tag)) }
                        else { issue(.unsupported, .warning, "<span> 只支持 class=\"tg-spoiler\"（隐藏文字），其他写法 Telegram 可能拒绝；预览中已忽略这个标签。", start: tag.start, end: tag.end) }
                    }
                    continue
                }
                if name == "blockquote" {
                    if tag.closing { _ = expandable.popLast() } else { expandable.append(hasAttribute(tag.attrs, "expandable")) }
                }
                if tag.closing {
                    if voidTags.contains(name) { continue }
                    guard (outputs[index] ?? "").isEmpty == false else {
                        if !isKnownOpenOnly(name) {
                            issue(.unsupported, .warning, "Telegram 不支持 \(label(tag)) 标签，可能拒绝发送；预览中已忽略。", start: tag.start, end: tag.end)
                        }
                        continue
                    }
                    closeFamily(family(name), tag: tag)
                    continue
                }
                guard let event = openEvent(tag) else {
                    if name == "input" {
                        issue(.unsupported, .warning, "<input> 只支持 type=\"checkbox\"（富文本勾选框）；预览中已忽略。", start: tag.start, end: tag.end)
                    } else {
                        issue(.unsupported, .warning, "Telegram 不支持 <\(tag.rawName)> 标签，可能拒绝发送；预览中已忽略这个标签（文字保留）。", start: tag.start, end: tag.end)
                    }
                    continue
                }
                if name == "a", safeHref(tag.attrs) == nil, let href = readAttribute(tag.attrs, "href") {
                    issue(.unsafeLink, .info, "链接地址「\(href)」不受支持（只允许 http、https、mailto、tel、tg 或 #），预览中不可点。", start: tag.start, end: tag.end)
                }
                if voidTags.contains(name) || event.isVoid {
                    append(.element(TelegramElement(kind: event, children: [])))
                } else {
                    stack.append(Frame(kind: event, family: family(name), tag: tag))
                }
            }
        }
        while stack.count > 1 {
            if let tag = stack.last?.tag {
                issue(.unclosed, .warning, "<\(tag.rawName)> 没有闭合（缺少 </\(tag.rawName)>），Telegram 可能拒绝发送。", start: tag.start, end: tag.end)
            }
            closeTop()
        }
        let nodes = trimDocument(stack[0].children)
        issues.sort { $0.offset < $1.offset }
        return Document(nodes: nodes, issues: issues)
    }

    private static func nextTagIndex(_ pieces: [Piece], after index: Int) -> Int? {
        var i = index + 1
        while i < pieces.count {
            if case .tag = pieces[i] { return i }
            if case .strayLessThan = pieces[i] { i += 1; continue }
            return nil
        }
        return nil
    }

    /// 结束标签在 Web 里输出为空、但开始标签是支持的（如 `</hr>`），不提示“不支持”。
    private static func isKnownOpenOnly(_ name: String) -> Bool {
        ["hr", "br"].contains(name)
    }

    private static func checkAmpersands(_ text: String, base: Int, end: Int,
                                        issue: (TelegramHTMLIssue.Kind, TelegramHTMLIssue.Severity, String, Int, Int) -> Void) {
        let a = Array(text.unicodeScalars)
        var i = 0
        while i < a.count {
            guard a[i] == "&" else { i += 1; continue }
            if let close = matchEntityBody(a, at: i + 1, names: ["lt", "gt", "amp", "quot"]) {
                i = close + 1
                continue
            }
            if let close = matchEntityBody(a, at: i + 1, names: restorableNames) {
                let name = string(a[(i + 1)..<close])
                issue(.namedEntity, .info, "&\(name); 不在 Telegram 支持的命名实体里（只支持 &lt; &gt; &amp; &quot; 和数字实体），可能原样显示。", base + i, base + close + 1)
                i = close + 1
                continue
            }
            issue(.bareAmpersand, .info, "单独的“&”建议写成 &amp;。", base + i, base + i + 1)
            i += 1
        }
    }

    /// 开始标签 → 节点类型；nil 表示不支持（Web 输出为空）。
    static func openEvent(_ tag: RawTag) -> TelegramElementKind? {
        let attrs = tag.attrs
        switch tag.name {
        case "b", "strong": return .bold
        case "i", "em": return .italic
        case "u", "ins": return .underline
        case "s", "strike", "del": return .strikethrough
        case "code": return .code(language: displayLanguage(attrs))
        case "pre": return .pre
        case "mark": return .mark
        case "sub": return .subscriptText
        case "sup": return .superscriptText
        case "tg-spoiler": return .spoiler
        case "a": return .link(url: safeHref(attrs) ?? readAttribute(attrs, "name").map { "#\($0)" })
        case "tg-emoji": return .customEmoji(id: readAttribute(attrs, "emoji-id"))
        case "tg-time": return .time(unix: safeNumber(attrs, "unix", maxDigits: 12), format: readAttribute(attrs, "format"))
        case "tg-reference": return .reference
        case "tg-math": return .math
        case "tg-math-block": return .mathBlock
        case "tg-thinking": return .thinking
        case "blockquote": return .blockquote(expandable: hasAttribute(attrs, "expandable"))
        case "aside": return .pullquote
        case "details": return .details(open: hasAttribute(attrs, "open"))
        case "summary": return .summary
        case "table": return .table(bordered: hasAttribute(attrs, "bordered"), striped: hasAttribute(attrs, "striped"))
        case "thead", "tbody": return .tableSection
        case "tr": return .tableRow
        case "th", "td":
            return .tableCell(header: tag.name == "th", colspan: safeNumber(attrs, "colspan").flatMap { Int($0) },
                              align: safeAlign(attrs, vertical: false))
        case "ul": return .unorderedList
        case "ol": return .orderedList(start: safeNumber(attrs, "start").flatMap { Int($0) }, reversed: hasAttribute(attrs, "reversed"))
        case "li": return .listItem
        case "h1", "h2", "h3", "h4", "h5", "h6": return .heading(Int(String(tag.name.dropFirst())) ?? 1)
        case "p": return .paragraph
        case "footer", "figure", "figcaption", "caption", "cite": return .block(tag.name)
        case "tg-collage": return .mediaGrid(slideshow: false)
        case "tg-slideshow": return .mediaGrid(slideshow: true)
        case "hr": return .divider
        case "br": return .lineBreak
        case "input":
            guard (readAttribute(attrs, "type") ?? "").lowercased() == "checkbox" else { return nil }
            return .checkbox(checked: hasAttribute(attrs, "checked"))
        case "img", "video", "audio":
            let source = readAttribute(attrs, "src")
            let alt = readAttribute(attrs, "alt")
            if tag.name == "img", source?.hasPrefix("tg://emoji") == true || readAttribute(attrs, "class") == "emoji" {
                return .inlineEmoji(nonEmpty(alt) ?? "🙂")
            }
            let kind = mediaKindLabel(tag.name)
            return .media(kind: kind, label: nonEmpty(alt) ?? (source?.hasPrefix("tg://emoji") == true ? "🙂" : kind))
        case "tg-map": return .map(label: mapLabel(attrs))
        default: return nil
        }
    }

    /// 去掉文档首尾的纯空白（Telegram 发送时同样会去掉消息首尾空白）。
    static func trimDocument(_ nodes: [TelegramNode]) -> [TelegramNode] {
        var result = nodes
        while let first = result.first, case .text(let text) = first {
            let trimmed = string(Array(text.unicodeScalars.drop(while: { isJSWhitespace($0) })))
            if trimmed.isEmpty { result.removeFirst() } else { result[0] = .text(trimmed); break }
        }
        while let last = result.last, case .text(let text) = last {
            var scalars = Array(text.unicodeScalars)
            while let s = scalars.last, isJSWhitespace(s) { scalars.removeLast() }
            if scalars.isEmpty { result.removeLast() } else { result[result.count - 1] = .text(string(scalars)); break }
        }
        return result
    }
}

// MARK: - 节点

public indirect enum TelegramNode: Equatable, Sendable {
    case text(String)
    case element(TelegramElement)

    /// 纯文本：文字原样，换行标签为换行，块级元素之间补换行。
    public static func plainText(_ nodes: [TelegramNode]) -> String {
        var out = ""
        func ensureNewline() { if !out.isEmpty, !out.hasSuffix("\n") { out += "\n" } }
        func walk(_ nodes: [TelegramNode]) {
            for node in nodes {
                switch node {
                case .text(let text): out += text
                case .element(let element):
                    switch element.kind {
                    case .lineBreak: out += "\n"
                    case .divider: ensureNewline()
                    case .checkbox(let checked): out += checked ? "✓" : ""
                    case .media(_, let label), .map(let label), .inlineEmoji(let label): out += label
                    case .tableCell:
                        walk(element.children)
                        out += " "
                    default:
                        if element.kind.isBlock { ensureNewline() }
                        walk(element.children)
                        if element.kind.isBlock { ensureNewline() }
                    }
                }
            }
        }
        walk(nodes)
        return out.trimmingCharacters(in: .whitespacesAndNewlines)
    }
}

public struct TelegramElement: Equatable, Sendable {
    public var kind: TelegramElementKind
    public var children: [TelegramNode]
    public init(kind: TelegramElementKind, children: [TelegramNode]) {
        self.kind = kind
        self.children = children
    }
}

public enum TelegramElementKind: Equatable, Sendable {
    case bold, italic, underline, strikethrough, spoiler, mark, subscriptText, superscriptText
    case code(language: String?)
    case pre
    case link(url: String?)
    case customEmoji(id: String?)
    case time(unix: String?, format: String?)
    case reference, math, thinking
    case mathBlock
    case blockquote(expandable: Bool)
    case pullquote
    case details(open: Bool)
    case summary
    case heading(Int)
    case paragraph
    /// footer / figure / figcaption / caption / cite：按普通块显示。
    case block(String)
    case unorderedList
    case orderedList(start: Int?, reversed: Bool)
    case listItem
    case table(bordered: Bool, striped: Bool)
    case tableSection
    case tableRow
    case tableCell(header: Bool, colspan: Int?, align: String?)
    case mediaGrid(slideshow: Bool)
    case lineBreak, divider
    case checkbox(checked: Bool)
    case media(kind: String, label: String)
    case inlineEmoji(String)
    case map(label: String)
    /// 模板变量代入的示例值（`TelegramTemplate.render(highlight:)` 标出）。
    case variable(name: String?)
    /// 没有示例值、原样保留的模板变量。
    case missingVariable

    public var isBlock: Bool {
        switch self {
        case .pre, .mathBlock, .blockquote, .pullquote, .details, .summary, .heading, .paragraph, .block, .unorderedList,
             .orderedList, .listItem, .table, .tableSection, .tableRow, .mediaGrid, .divider: return true
        default: return false
        }
    }
    public var isVoid: Bool {
        switch self {
        case .lineBreak, .divider, .checkbox, .media, .inlineEmoji, .map: return true
        default: return false
        }
    }
}

// MARK: - 诊断

public struct TelegramHTMLIssue: Equatable, Sendable, Identifiable {
    public enum Kind: String, Sendable {
        case unclosed, stray, misnested, unsupported, richOnly, bareLessThan, bareAmpersand, namedEntity, unsafeLink
        case unclosedCondition, missingSample
    }
    public enum Severity: Int, Sendable, Comparable {
        case info = 0, warning = 1, error = 2
        public static func < (lhs: Severity, rhs: Severity) -> Bool { lhs.rawValue < rhs.rawValue }
    }
    public var kind: Kind
    public var severity: Severity
    public var message: String
    /// 1 起的行号与列号（按字符计）。
    public var line: Int
    public var column: Int
    /// 在原文中的位置（Unicode 标量下标）与长度。
    public var offset: Int
    public var length: Int
    public var id: String { "\(kind.rawValue)-\(offset)-\(length)" }

    public init(kind: Kind, severity: Severity, message: String, line: Int, column: Int, offset: Int, length: Int) {
        self.kind = kind
        self.severity = severity
        self.message = message
        self.line = line
        self.column = column
        self.offset = offset
        self.length = length
    }

    /// “第 3 行第 5 列”。
    public var locationText: String { "第 \(line) 行第 \(column) 列" }

    /// 出问题的那一行，以及问题在这一行里的标量范围（用于标出位置）。
    public func lineExcerpt(in source: String) -> (text: String, range: Range<Int>) {
        let lines = source.components(separatedBy: "\n")
        guard line >= 1, line <= lines.count else { return ("", 0..<0) }
        let text = lines[line - 1]
        let count = text.unicodeScalars.count
        let start = min(max(0, column - 1), count)
        let end = min(count, start + max(1, length))
        return (text, start..<end)
    }
}
