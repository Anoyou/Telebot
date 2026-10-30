import Foundation

/// 消息模板变量：`{name}` 占位符与 AI 输出模板的条件块 `{?name}…{/?}`（与 Web 各页面的 `renderTemplate*` 相同）。
public enum TelegramTemplate {
    /// 代入示例值时包住值的私用区标记，`highlighted(_:)` 把它们转成 `.variable` 节点，用于预览里高亮。
    static let valueStart: Unicode.Scalar = "\u{E000}"
    static let valueEnd: Unicode.Scalar = "\u{E001}"
    static let missingStart: Unicode.Scalar = "\u{E002}"
    static let missingEnd: Unicode.Scalar = "\u{E003}"

    /// 缺少示例值时：保留 `{name}` 原样（插件、交互、转账通知）或替换为空（AI 输出模板）。
    public enum Missing: Sendable { case keep, empty }

    public struct Occurrence: Equatable, Sendable {
        public var name: String
        /// 原文中的标量范围（含花括号）。
        public var range: Range<Int>
        public var conditional: Bool
    }

    /// 变量名：字母、数字、下划线与常用汉字（Web 交互查询模板支持 `{规则名称}`）。
    static func isNameScalar(_ s: Unicode.Scalar) -> Bool {
        TelegramHTML.isASCIILetter(s) || TelegramHTML.isASCIIDigit(s) || s == "_" || (0x4E00...0x9FA5).contains(s.value)
    }

    /// 模板里出现的全部变量（`{name}` 与 `{?name}`），按出现顺序。
    public static func occurrences(in template: String) -> [Occurrence] {
        let a = Array(template.unicodeScalars)
        var result: [Occurrence] = []
        var i = 0
        while i < a.count {
            guard a[i] == "{" else { i += 1; continue }
            var j = i + 1
            let conditional = j < a.count && a[j] == "?"
            if conditional { j += 1 }
            let nameStart = j
            while j < a.count, isNameScalar(a[j]) { j += 1 }
            if j > nameStart, j < a.count, a[j] == "}" {
                result.append(Occurrence(name: TelegramHTML.string(a[nameStart..<j]), range: i..<(j + 1), conditional: conditional))
                i = j + 1
            } else {
                i += 1
            }
        }
        return result
    }

    /// 去重后的变量名。
    public static func variableNames(in template: String) -> [String] {
        var seen: Set<String> = []
        return occurrences(in: template).compactMap { seen.insert($0.name).inserted ? $0.name : nil }
    }

    /// 没有 `{/?}` 结尾的条件块。
    public static func unclosedConditions(in template: String) -> [Occurrence] {
        let a = Array(template.unicodeScalars)
        return occurrences(in: template).filter { occurrence in
            guard occurrence.conditional else { return false }
            var k = occurrence.range.upperBound
            while k + 4 <= a.count {
                if a[k] == "{", a[k + 1] == "/", a[k + 2] == "?", a[k + 3] == "}" { return false }
                k += 1
            }
            return true
        }
    }

    /// 代入示例值。
    /// - conditionals: 先处理 `{?name}…{/?}`（值为空则整段去掉），只有 AI 输出模板使用。
    /// - highlight: 用私用区标记包住代入的值与缺失变量；位于标签内部（如 `href="{url}"`）的不包。
    public static func render(_ template: String, values: [String: String], conditionals: Bool = false,
                              missing: Missing = .keep, highlight: Bool = false) -> String {
        var source = template
        if conditionals { source = applyConditionals(source, values: values) }
        let a = Array(source.unicodeScalars)
        var out = String.UnicodeScalarView()
        var insideTag = false
        var i = 0
        while i < a.count {
            let scalar = a[i]
            if scalar == "<", i + 1 < a.count, TelegramHTML.isASCIILetter(a[i + 1]) || a[i + 1] == "/" { insideTag = true }
            else if scalar == ">" { insideTag = false }
            if scalar == "{" {
                var j = i + 1
                let nameStart = j
                while j < a.count, isNameScalar(a[j]) { j += 1 }
                if j > nameStart, j < a.count, a[j] == "}" {
                    let name = TelegramHTML.string(a[nameStart..<j])
                    let mark = highlight && !insideTag
                    if let value = values[name] {
                        if mark { out.append(valueStart) }
                        out.append(contentsOf: value.unicodeScalars)
                        if mark { out.append(valueEnd) }
                    } else if missing == .keep {
                        if mark { out.append(missingStart) }
                        out.append(contentsOf: a[i...j])
                        if mark { out.append(missingEnd) }
                    }
                    i = j + 1
                    continue
                }
            }
            out.append(scalar)
            i += 1
        }
        return String(out)
    }

    /// `{?key}inner{/?}`：key 有非空值保留 inner，否则去掉（与 Web `renderOutputTemplatePreview` 相同，非贪婪、不嵌套）。
    static func applyConditionals(_ template: String, values: [String: String]) -> String {
        let a = Array(template.unicodeScalars)
        var out = String.UnicodeScalarView()
        var i = 0
        outer: while i < a.count {
            if a[i] == "{", i + 1 < a.count, a[i + 1] == "?" {
                var j = i + 2
                let nameStart = j
                while j < a.count, TelegramHTML.isASCIILetter(a[j]) || TelegramHTML.isASCIIDigit(a[j]) || a[j] == "_" { j += 1 }
                if j > nameStart, j < a.count, a[j] == "}" {
                    let name = TelegramHTML.string(a[nameStart..<j])
                    var k = j + 1
                    while k + 3 < a.count {
                        if a[k] == "{", a[k + 1] == "/", a[k + 2] == "?", a[k + 3] == "}" {
                            if let value = values[name], !value.isEmpty { out.append(contentsOf: a[(j + 1)..<k]) }
                            i = k + 4
                            continue outer
                        }
                        k += 1
                    }
                }
            }
            out.append(a[i])
            i += 1
        }
        return String(out)
    }

    /// 把示例值标记转成 `.variable` / `.missingVariable` 节点（可跨标签：标记之间的每段文字各自包一层）。
    public static func highlighted(_ nodes: [TelegramNode]) -> [TelegramNode] {
        var state: TelegramElementKind?
        func split(_ text: String) -> [TelegramNode] {
            var result: [TelegramNode] = []
            var buffer = String.UnicodeScalarView()
            func flush() {
                guard !buffer.isEmpty else { return }
                let piece = String(buffer)
                buffer = String.UnicodeScalarView()
                if let state { result.append(.element(TelegramElement(kind: state, children: [.text(piece)]))) }
                else { result.append(.text(piece)) }
            }
            for scalar in text.unicodeScalars {
                switch scalar {
                case valueStart: flush(); state = .variable(name: nil)
                case missingStart: flush(); state = .missingVariable
                case valueEnd, missingEnd: flush(); state = nil
                default: buffer.append(scalar)
                }
            }
            flush()
            return result
        }
        func walk(_ nodes: [TelegramNode]) -> [TelegramNode] {
            nodes.flatMap { node -> [TelegramNode] in
                switch node {
                case .text(let text): return split(text)
                case .element(var element):
                    if case .inlineEmoji(let label) = element.kind { element.kind = .inlineEmoji(strip(label)) }
                    element.children = walk(element.children)
                    return [.element(element)]
                }
            }
        }
        return walk(nodes)
    }

    /// 去掉全部高亮标记（属性值、计数等用）。
    public static func strip(_ text: String) -> String {
        var view = String.UnicodeScalarView()
        view.append(contentsOf: text.unicodeScalars.filter { ![valueStart, valueEnd, missingStart, missingEnd].contains($0) })
        return String(view)
    }

    /// 字面 `\n`（插件 schema 默认值里常见）转成换行，与 Web `normalizePreviewEscapes` 相同。
    public static func normalizeEscapes(_ value: String) -> String {
        value.replacingOccurrences(of: "\\r\\n", with: "\n").replacingOccurrences(of: "\\n", with: "\n").replacingOccurrences(of: "\\r", with: "\n")
    }

    /// 与 Web `formatConfigValue` 相同的配置值显示。
    public static func format(_ value: JSONValue?) -> String {
        guard let value else { return "" }
        switch value {
        case .null: return ""
        case .string(let text): return text
        case .bool(let flag): return flag ? "true" : "false"
        case .integer(let number): return String(number)
        case .number(let number):
            if number.isFinite, number == number.rounded(), abs(number) < 1e15 { return String(Int64(number)) }
            return String(number)
        case .array(let items):
            if items.contains(where: { $0.objectValue != nil || $0.arrayValue != nil }) { return (try? value.prettyText()) ?? "" }
            return items.map { format($0) }.joined(separator: ", ")
        case .object: return (try? value.prettyText()) ?? ""
        }
    }
}

/// 各处预览用的示例值（逐字对应 Web 各页面的示例常量）。
public enum TelegramTemplateSamples {
    /// AI 输出模板留空时使用的默认格式（Web `PRESET_SIMPLE_TEMPLATE`）。
    public static let aiDefaultTemplate = "{answer}\n\n— {model} · in {in_tokens} / out {out_tokens}{?routing_note}  ·  {routing_note}{/?}"

    /// Web `OutputTemplateEditor` 的示例值。
    public static let aiOutput: [String: String] = [
        "answer": "这是 AI 回答示例，已按当前消息模板渲染。",
        "answer_first_2": "这是 AI 回答示例，已按当前消息模板渲染。",
        "answer_rest": "这里是从第三行开始的回答内容。",
        "display_input": "被回复消息或用户问题示例",
        "display_input_first_2": "被回复消息或用户问题示例",
        "display_input_rest": "这里是输入内容的剩余部分。",
        "question": "请总结这段内容",
        "quoted": "这是一段被回复的原文。",
        "model": "GPT-5.4",
        "model_id": "gpt-5.4",
        "provider": "OpenAI",
        "provider_kind": "openai",
        "command": "ai",
        "mode": "search",
        "api_format": "responses",
        "configured_api_format": "chat_completions",
        "web_search_api_format": "auto",
        "api_protocol": "responses",
        "endpoint": "/responses",
        "web_search": "true",
        "in_tokens": "128",
        "out_tokens": "64",
        "total_tokens": "192",
        "routing_note": "auto: chat",
        "sources": "1. OpenAI 文档\nhttps://platform.openai.com/docs\n2. 示例来源\nhttps://example.com",
        "time": "12:30",
    ]

    /// Web `TemplatesEditor` 的 reply_text 示例。
    public static let replyText: [String: String] = ["args": "帮我把这句话翻译成英文并保留语气", "cmd": "translate"]

    /// Web `BotTab` 转账／扣款通知示例。
    public static let transferNotice: [String: String] = [
        "payer_name": "Alice", "payer_user_id": "10001", "payer_user_id_line": "付款人ID：10001",
        "receiver_name": "Bob", "receiver_user_id": "10002", "receiver_user_id_line": "收款人ID：10002", "amount": "88.00",
    ]
    public static let defaultTransferNoticeTemplate = "<pre><code class=\"language-转账成功\">付款人：{payer_name}\n{payer_user_id_line}\n收款人：{receiver_name}\n金额：{amount}\n{receiver_user_id_line}</code></pre>"
    public static let defaultDebitNoticeTemplate = "<pre><code class=\"language-扣减成功\">{payer_name} 扣减 {amount} 蝌蚪\n{receiver_name} 接收 {amount} 蝌蚪</code></pre>"

    /// 交互规则命中回复的示例（转账触发）。
    public static let interactionReply: [String: String] = [
        "payer_name": "Alice", "receiver_name": "Bob", "amount": "88.00", "规则名称": "示例规则",
    ]

    public static let defaultQueryResponseTemplate = "<b>当前可用联动玩法</b>\n{items}"
    public static let defaultQueryItemTemplate = "{index}. <b>{name}</b>\n触发方式：{trigger}"
    public static let defaultQueryEmptyMessage = "当前群暂无开启中的联动玩法。"

    /// Web `renderInteractionQueryTemplatePreview`：先用两条示例玩法渲染条目模板，再代入回复模板。
    public static func interactionQuery(response: String, item: String, highlight: Bool = false) -> String {
        let source = response.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? defaultQueryResponseTemplate : response
        let itemSource = item.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty ? defaultQueryItemTemplate : item
        let samples: [[String: String]] = [
            ["index": "1", "name": "示例玩法", "trigger": "转账或关键词；关键词：<code>开局 num=数字</code>；转账通知",
             "kind": "玩法 <code>example_game</code>", "limit": "限时 <code>600</code> 秒", "module_key": "example_game",
             "module_action": "start_example_game", "chat_id": "-1001234567890"],
            ["index": "2", "name": "示例工具", "trigger": "关键词；关键词：<code>查询 id=12345</code>",
             "kind": "玩法 <code>example_tool</code>", "limit": "每用户 CD <code>12h</code>", "module_key": "example_tool",
             "module_action": "run_example_tool", "chat_id": "-1001234567890"],
        ]
        let items = samples.map { TelegramTemplate.render(itemSource, values: $0) }.joined(separator: "\n")
        let values = ["items": items, "count": "2", "closed_count": "0", "chat_id": "-1001234567890"]
        return TelegramTemplate.render(source, values: values, highlight: highlight)
    }

    /// Web `ConfigDialog.renderTemplateSample` 的插件模板示例值；`config` 为当前（草稿）配置。
    public static func plugin(config: [String: JSONValue], prefix: String) -> [String: String] {
        func value(_ key: String, _ fallback: String) -> String {
            let text = TelegramTemplate.format(config[key])
            return text.isEmpty ? fallback : text
        }
        func clock(_ key: String, _ fallback: String) -> String {
            let raw = value(key, fallback)
            guard let number = Double(raw), number.isFinite else { return fallback }
            let clamped = max(0, Int(number.rounded(.down)))
            return clamped < 10 ? "0\(clamped)" : String(clamped)
        }
        var sample: [String: String] = [
            "version": "1.0.0",
            "prefix": prefix.isEmpty ? "," : prefix,
            "command": value("command", "dicegrid"),
            "force_stop_command": value("force_stop_command", "stop"),
            "target_sum": "17",
            "answer_index": "6",
            "prize": "100",
            "timeout": value("timeout", "90"),
            "guess_cooldown": value("guess_cooldown", "2.0"),
            "winner": "小明",
            "elapsed": "8.2",
            "example": "100",
            "round": "12",
            "number": "3",
            "count": "5",
            "cost": "50003",
            "pool": "188888",
            "winners": "2",
            "payout": "53888",
            "history_limit": value("history_show_limit", "5"),
            "draw_numbers": value("draw_numbers", "1,2,3,4,5,6"),
            "draw_time": "\(clock("draw_hour", "21")):\(clock("draw_minute", "00"))",
            "close_minutes": value("close_minutes_before_draw", "1"),
            "interval": value("auto_draw_interval_sec", "86400"),
            "chat_id": "-1001234567890",
            "chat_display": "示例工作群",
            "summary": "1) 讨论了版本回滚原因\n2) 确认改为平台 AI 路由\n3) 约定今天内回归验证",
            "time": "2026-05-26 14:30",
            "message_count": value("default_count", "100"),
            "total_amount": "150000",
            "question_count": "40",
            "redpacket_id": "a1b2c3d4e5f6",
            "packets": "- a1b2c3d4e5f6 · 领取进度 12/40\n- f6e5d4c3b2a1 · 领取进度 8/40",
            "packet_count": "2",
            "date": "2026-07-14",
            "daily_limit": "1",
            "retry_count": "1",
            "question": "示例题目：网页正文中提到的核心结论是什么？",
            "options": "A. 示例正确答案\nB. 示例错误答案一\nC. 示例错误答案二",
            "reward": "3888",
            "answer": "A. 示例正确答案",
            "explanation": "正文明确给出了该结论。",
            "source": "https://example.com/source",
            "status": "已全部领完",
            "claimed_amount": "150000",
            "claim_count": "40",
            "luckiest_name": "好运用户",
            "luckiest_reward": "9888",
            "unluckiest_name": "保底用户",
            "unluckiest_reward": "1",
            "ranking": "<blockquote expandable><b>领取总名单（金额降序）</b>\n1. 好运用户 · 9888\n2. 保底用户 · 1</blockquote>",
            "weekly_title": "AI 红包周榜结算",
            "period_start": "2026-07-12 10:00",
            "period_end": "2026-07-19 10:00",
            "count_ranking": "1. 答题王 · 7 次\n2. 小明 · 5 次",
            "reward_ranking": "1. 奖金王 · 18888\n2. 小红 · 12888",
        ]
        sample["title"] = "九宫格竞猜"
        sample["target_line"] = "目标点数：<b>\(sample["target_sum"]!)</b>（9 格里唯一）"
        sample["guide_line"] = "回复 <code>1-9</code> 选择你认为答案所在的格子。"
        sample["reward_line"] = "首个答对者奖励：<b>+\(sample["prize"]!)</b> · 超时 \(sample["timeout"]!) 秒"
        return sample
    }

    /// 插件配置里的模板字段（Web `isTemplateFieldKey`）。
    public static func isTemplateKey(_ key: String) -> Bool {
        key == "message_template" || key.lowercased().hasSuffix("_template")
    }

    /// 预览字段（`*_preview` / `template_preview`）对应的模板字段（Web `findTemplateKeyForPreview`）。
    public static func templateKey(forPreview previewKey: String, keys: Set<String>) -> String? {
        if previewKey == "template_preview" {
            if keys.contains("round_message_template") { return "round_message_template" }
            if keys.contains("message_template") { return "message_template" }
        }
        var base = previewKey
        if base.lowercased().hasSuffix("_preview") { base = String(base.dropLast("_preview".count)) }
        return ["\(base)_message_template", "\(base)_template", base].first { keys.contains($0) }
    }
}

/// 消息的解析模式（Telegram `parse_mode`）。Markdown / 纯文本与 Web 一样按原文显示。
public enum TelegramParseMode: String, Sendable, CaseIterable {
    case html, markdown, plain

    public init(serverValue: String?) {
        switch (serverValue ?? "").trimmingCharacters(in: .whitespacesAndNewlines).lowercased() {
        case "markdown", "markdownv2", "md": self = .markdown
        case "plain", "text", "none": self = .plain
        default: self = .html
        }
    }
    public var title: String {
        switch self {
        case .html: "HTML"
        case .markdown: "Markdown"
        case .plain: "纯文本"
        }
    }
}

/// 一次预览的全部结果：代入示例值后的节点树、针对模板原文的警告（带位置）、长度与上限。
public struct TelegramMessagePreviewModel: Equatable, Sendable {
    public var source: String
    public var mode: TelegramParseMode
    /// 代入示例值后、已把示例值标成 `.variable` 的节点树。
    public var nodes: [TelegramNode]
    /// 模板原文的问题（行列位置对应 `source`），按位置排序。
    public var issues: [TelegramHTMLIssue]
    /// Telegram 口径长度（代入示例值、去掉标签后）。
    public var length: Int
    public var limit: Int
    public var variables: [String]
    public var missingVariables: [String]

    public var isOverLimit: Bool { length > limit }
    /// 超过上限的 90% 时提醒。
    public var isNearLimit: Bool { !isOverLimit && length * 10 >= limit * 9 }
    public var warningCount: Int { issues.filter { $0.severity >= .warning }.count }

    public init(template: String, values: [String: String], mode: TelegramParseMode = .html, caption: Bool = false,
                conditionals: Bool = false, missing: TelegramTemplate.Missing = .keep) {
        source = template
        self.mode = mode
        limit = caption ? TelegramHTML.captionLimit : TelegramHTML.messageLimit
        let names = TelegramTemplate.variableNames(in: template)
        variables = names
        let rendered = TelegramTemplate.render(template, values: values, conditionals: conditionals, missing: missing, highlight: true)
        var problems: [TelegramHTMLIssue] = []
        let built: [TelegramNode]
        if mode == .html {
            built = TelegramTemplate.highlighted(TelegramHTML.parse(rendered).nodes)
            problems = TelegramHTML.parse(template).issues
        } else {
            built = TelegramTemplate.highlighted([.text(rendered.trimmingCharacters(in: .whitespacesAndNewlines))])
        }
        nodes = built
        length = TelegramHTML.telegramLength(TelegramNode.plainText(built))
        let occurrences = TelegramTemplate.occurrences(in: template)
        var missingNames: [String] = []
        let scalars = Array(template.unicodeScalars)
        func position(_ offset: Int) -> (Int, Int) {
            var line = 1, column = 1
            for scalar in scalars.prefix(offset) {
                if scalar == "\n" { line += 1; column = 1 } else { column += 1 }
            }
            return (line, column)
        }
        for occurrence in occurrences where !occurrence.conditional && values[occurrence.name] == nil {
            guard !missingNames.contains(occurrence.name) else { continue }
            missingNames.append(occurrence.name)
            let (line, column) = position(occurrence.range.lowerBound)
            let effect = missing == .keep ? "预览中原样显示" : "预览中显示为空"
            problems.append(TelegramHTMLIssue(kind: .missingSample, severity: .info,
                                              message: "变量 {\(occurrence.name)} 没有示例值，\(effect)；实际发送时由服务器填入。",
                                              line: line, column: column, offset: occurrence.range.lowerBound, length: occurrence.range.count))
        }
        if conditionals {
            for occurrence in TelegramTemplate.unclosedConditions(in: template) {
                let (line, column) = position(occurrence.range.lowerBound)
                problems.append(TelegramHTMLIssue(kind: .unclosedCondition, severity: .warning,
                                                  message: "条件块 {?\(occurrence.name)} 缺少结尾 {/?}，这一段会原样显示。",
                                                  line: line, column: column, offset: occurrence.range.lowerBound, length: occurrence.range.count))
            }
        }
        missingVariables = missingNames
        issues = problems.sorted { $0.offset == $1.offset ? $0.severity > $1.severity : $0.offset < $1.offset }
    }
}
