import Foundation

/// 流式回答的“已闭合部分”边界（第十一轮性能优化）。
///
/// 助手回答边生成边显示时，原来每个增量都把整段文字重新脱敏、重新解析 Markdown，总工作量随长度平方增长。
/// 现在把已经闭合的块缓存起来，只重新处理最后一块：本函数找出 `text` 中从 `start`（UTF-8 偏移）往后、
/// 最后一个可以安全切开的位置，切开后前后两段分别解析与整体解析结果完全相同。
///
/// 可以切开的位置：代码块与私钥块之外的一个空行之后（块级 Markdown 解析器遇到空行会结束段落、引用与表格；
/// 列表、标题逐行生成）。下列情况不切开，留给下一次：
/// - 在 ``` 代码块里（空行属于代码）；
/// - 在 `-----BEGIN … PRIVATE KEY-----` 与 `-----END … PRIVATE KEY-----` 之间（脱敏规则需要整块匹配）；
/// - 空行前最后一个非空行以 `:`、`：`、`=` 或 `bearer`／`basic` 结尾（“密码：”后面隔一个空行写值时，脱敏规则要看到两行）。
/// - 最后一行还没收到换行符（不完整）。
///
/// `start` 必须是上一次返回的边界（或 0），那里一定不在代码块或私钥块内。返回值不小于 `start`。
public enum StreamingMarkdownBoundary {
    public static func stableEnd(in text: String, from start: Int) -> Int {
        let bytes = text.utf8
        guard start >= 0, start < bytes.count else { return max(0, min(start, bytes.count)) }
        var inFence = false
        var inPrivateKey = false
        var lastNonBlankEndsWithSeparator = false
        var result = start
        var lineStart = bytes.index(bytes.startIndex, offsetBy: start)
        var offset = start
        var index = lineStart
        while index < bytes.endIndex {
            if bytes[index] == UInt8(ascii: "\n") {
                let line = String(decoding: bytes[lineStart..<index], as: UTF8.self)
                let trimmed = line.trimmingCharacters(in: trimSet)
                let next = offset + 1
                if inFence {
                    if trimmed.hasPrefix("```") { inFence = false }
                } else if inPrivateKey {
                    if trimmed.contains("-----END"), trimmed.contains("PRIVATE KEY-----") { inPrivateKey = false }
                } else if trimmed.hasPrefix("```") {
                    inFence = true
                } else if trimmed.contains("-----BEGIN"), trimmed.contains("PRIVATE KEY-----") {
                    // 同一行里就结束的（极少见）不算进入私钥块。
                    inPrivateKey = !trimmed.contains("-----END")
                } else if trimmed.isEmpty {
                    if !lastNonBlankEndsWithSeparator { result = next }
                    lineStart = bytes.index(after: index)
                    index = lineStart
                    offset = next
                    continue
                }
                if !trimmed.isEmpty { lastNonBlankEndsWithSeparator = endsWithSeparator(trimmed) }
                lineStart = bytes.index(after: index)
                index = lineStart
                offset = next
                continue
            }
            index = bytes.index(after: index)
            offset += 1
        }
        return result
    }

    /// 取 `text` 的 UTF-8 区间 `[from, to)` 为字符串（边界都在换行符之后，不会切开字符）。
    public static func slice(_ text: String, from: Int, to: Int? = nil) -> String {
        let bytes = text.utf8
        let lower = bytes.index(bytes.startIndex, offsetBy: max(0, min(from, bytes.count)))
        let upper = bytes.index(bytes.startIndex, offsetBy: max(0, min(to ?? bytes.count, bytes.count)))
        guard lower < upper else { return "" }
        return String(decoding: bytes[lower..<upper], as: UTF8.self)
    }

    private static let trimSet = CharacterSet.whitespaces.union(CharacterSet(charactersIn: "\r"))

    private static func endsWithSeparator(_ line: String) -> Bool {
        if let last = line.last, ":：=".contains(last) { return true }
        let lower = line.lowercased()
        return lower.hasSuffix("bearer") || lower.hasSuffix("basic")
    }
}
