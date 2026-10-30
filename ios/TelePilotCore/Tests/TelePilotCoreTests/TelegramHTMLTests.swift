import Foundation
import Testing
@testable import TelePilotCore

/// Telegram HTML 预览：与 Web `TelegramHtmlPreview.tsx` 一致（用例由 Web 实际输出生成），并覆盖原生节点树与中文诊断。
@Suite struct TelegramHTMLTests {
    private struct Cases: Decodable {
        struct Item: Decodable { let name: String; let input: String; let expected: String }
        let cases: [Item]
    }

    // MARK: - 与 Web 逐字一致

    @Test func matchesWebSanitizerOutputForEveryFixture() throws {
        let url = try #require(Bundle.module.url(forResource: "telegram-html-cases", withExtension: "json"))
        let cases = try JSONDecoder().decode(Cases.self, from: Data(contentsOf: url)).cases
        #expect(cases.count >= 80)
        for item in cases {
            #expect(TelegramHTML.sanitizeToWebHTML(item.input) == item.expected, "\(item.name)")
        }
    }

    // MARK: - 移植自 TelegramHtmlPreview.test.cjs

    @Test func rendersRichBlocksAndInlineFormatting() {
        let input = """

        <h1>结算报告</h1>
        <p>Tom &amp; Jerry <mark>重点</mark> H<sub>2</sub>O</p>
        <ul><li><input type="checkbox" checked>已完成</li></ul>
        <table bordered striped><tr><th>项目</th><td align="right">1</td></tr></table>
        <details open><summary>详情</summary>正文</details>
        <tg-time unix="1647531900" format="r">明天</tg-time>
        <tg-math>x^2</tg-math>

        """
        let html = TelegramHTML.sanitizeToWebHTML(input)
        #expect(html.contains("<h1>结算报告</h1>"))
        #expect(html.contains("Tom &amp; Jerry"))
        #expect(html.contains("telegram-checkbox is-checked"))
        #expect(html.contains("telegram-table--bordered telegram-table--striped"))
        #expect(html.contains("<details class=\"telegram-details\" open>"))
        #expect(html.contains("Telegram 时间 1647531900"))
        #expect(html.contains("class=\"telegram-math\""))
        #expect(!html.contains("</p>\n<ul>"))
        #expect(!html.contains("</ul>\n<div class=\"telegram-table-scroll\">"))

        let document = TelegramHTML.parse(input)
        let kinds = Self.kinds(document.nodes)
        #expect(kinds.contains(.heading(1)))
        #expect(kinds.contains(.checkbox(checked: true)))
        #expect(kinds.contains(.table(bordered: true, striped: true)))
        #expect(kinds.contains(.details(open: true)))
        #expect(kinds.contains(.time(unix: "1647531900", format: "r")))
        #expect(document.plainText.contains("Tom & Jerry"))
        #expect(document.usesRichTags)
    }

    @Test func keepsExpandableQuotesAndBothSpoilerSyntaxesInteractive() {
        let input = """
        <blockquote expandable><b>完整名单</b></blockquote>
        <tg-spoiler>新语法</tg-spoiler>
        <span class="tg-spoiler">兼容语法</span>
        """
        let html = TelegramHTML.sanitizeToWebHTML(input)
        #expect(html.contains("<details class=\"telegram-expandable-quote\">"))
        #expect(html.contains("</blockquote></details>"))
        #expect(html.components(separatedBy: "class=\"telegram-spoiler\"").count - 1 == 2)

        let kinds = Self.kinds(TelegramHTML.parse(input).nodes)
        #expect(kinds.contains(.blockquote(expandable: true)))
        #expect(kinds.filter { $0 == .spoiler }.count == 2)
    }

    @Test func dropsExecutableTagsHandlersAndUnsafeLinkProtocols() {
        let input = """
        <script>alert(1)</script>
        <img src="x" onerror="alert(2)">
        <a href="javascript:alert(3)" onclick="alert(4)">危险链接</a>
        """
        let html = TelegramHTML.sanitizeToWebHTML(input)
        for forbidden in ["<script", "onerror", "onclick", "javascript:"] { #expect(!html.lowercased().contains(forbidden)) }
        #expect(html.contains("telegram-media-placeholder"))
        #expect(html.contains("<span class=\"telegram-link\">危险链接</span>"))

        let document = TelegramHTML.parse(input)
        #expect(Self.kinds(document.nodes).contains(.link(url: nil)))
        #expect(document.issues.contains { $0.kind == .unsupported && $0.message.contains("<script>") })
        #expect(document.issues.contains { $0.kind == .unsafeLink })
    }

    @Test func ignoresInvalidNumericEntitiesInAllowedAttributes() {
        let html = TelegramHTML.sanitizeToWebHTML("<a href=\"https://example.com/?x=&#9999999999;\">链接</a>")
        #expect(html == "<span class=\"telegram-link\" title=\"https://example.com/?x=\">链接</span>")
    }

    // MARK: - 原生节点树

    @Test func parsesTelegramBasicsIntoNodes() {
        let document = TelegramHTML.parse("<b>粗<i>斜</i></b> <code>x</code>\n<pre><code class=\"language-swift\">let a = 1</code></pre><a href=\"https://t.me\">链接</a><tg-emoji emoji-id=\"1\">👍</tg-emoji>")
        let kinds = Self.kinds(document.nodes)
        #expect(kinds.contains(.bold))
        #expect(kinds.contains(.italic))
        #expect(kinds.contains(.code(language: nil)))
        #expect(kinds.contains(.pre))
        #expect(kinds.contains(.code(language: "swift")))
        #expect(kinds.contains(.link(url: "https://t.me")))
        #expect(kinds.contains(.customEmoji(id: "1")))
        #expect(document.plainText == "粗斜 x\nlet a = 1\n链接👍")
        #expect(document.issues.isEmpty)
        #expect(!document.usesRichTags)
    }

    @Test func showsNonASCIICodeLanguageLikeTelegram() {
        let document = TelegramHTML.parse(TelegramTemplateSamples.defaultTransferNoticeTemplate)
        #expect(Self.kinds(document.nodes).contains(.code(language: "转账成功")))
        // Web 只认 ASCII 语言名：清洗输出里没有语言标签（两端只在显示上不同）。
        #expect(!TelegramHTML.sanitizeToWebHTML(TelegramTemplateSamples.defaultTransferNoticeTemplate).contains("telegram-code-language"))
    }

    @Test func treatsEntityEncodedTagsAsTextLikeTelegram() {
        let document = TelegramHTML.parse("&lt;b&gt;不是粗体&lt;/b&gt; &amp;lt;")
        #expect(document.nodes == [.text("<b>不是粗体</b> &lt;")])
    }

    @Test func dropsWhitespaceBetweenStructuralTagsAndTrimsEdges() {
        let document = TelegramHTML.parse("\n  <ul>\n  <li>一</li>\n  <li>二</li>\n</ul>\n")
        #expect(document.nodes.count == 1)
        guard case .element(let list) = document.nodes.first else { Issue.record("应为列表"); return }
        #expect(list.kind == .unorderedList)
        #expect(list.children.count == 2)
    }

    // MARK: - 诊断

    @Test func reportsUnclosedStrayAndMisnestedTagsWithPositions() {
        let source = "第一行\n<b>粗体 <i>斜体</b>\n多余</u>"
        let issues = TelegramHTML.parse(source).issues
        let misnested = issues.first { $0.kind == .misnested }
        #expect(misnested?.line == 2)
        #expect(misnested?.message.contains("</b>") == true)
        let stray = issues.filter { $0.kind == .stray }
        #expect(stray.contains { $0.line == 3 && $0.column == 3 && $0.message.contains("</u>") })
        let unclosed = TelegramHTML.parse("<b>没闭合").issues
        #expect(unclosed.count == 1)
        #expect(unclosed[0].kind == .unclosed)
        #expect(unclosed[0].locationText == "第 1 行第 1 列")
        let excerpt = unclosed[0].lineExcerpt(in: "<b>没闭合")
        #expect(excerpt.text == "<b>没闭合")
        #expect(excerpt.range == 0..<3)
    }

    @Test func reportsUnsupportedTagsBareCharactersAndRichOnlyTags() {
        let issues = TelegramHTML.parse("<div>块</div> a <3 b & c &nbsp; <h2>标题</h2> <span>普通</span>").issues
        #expect(issues.contains { $0.kind == .unsupported && $0.message.contains("<div>") })
        #expect(issues.contains { $0.kind == .bareLessThan && $0.column == 16 })
        #expect(issues.contains { $0.kind == .bareAmpersand })
        #expect(issues.contains { $0.kind == .namedEntity })
        #expect(issues.contains { $0.kind == .richOnly && $0.message.contains("<h2>") })
        #expect(issues.contains { $0.kind == .unsupported && $0.message.contains("tg-spoiler") })
        #expect(TelegramHTML.parse("<b>好</b> &lt;ok&gt; &amp; &quot; &#128512;").issues.isEmpty)
    }

    @Test func countsLengthAfterRemovingTags() {
        #expect(TelegramHTML.parse("<b>你好</b>，世界").length == 5)
        #expect(TelegramHTML.parse("😀").length == 2)
        let long = String(repeating: "长", count: 1025)
        let model = TelegramMessagePreviewModel(template: long, values: [:], caption: true)
        #expect(model.limit == 1024)
        #expect(model.isOverLimit)
        let near = TelegramMessagePreviewModel(template: String(repeating: "字", count: 3700), values: [:])
        #expect(near.limit == 4096)
        #expect(near.isNearLimit)
        #expect(!near.isOverLimit)
    }

    // MARK: - 模板变量

    @Test func rendersVariablesWithSamplesAndConditionals() {
        #expect(TelegramTemplate.variableNames(in: "{a} {b} {a} {?c}x{/?} {规则名称}") == ["a", "b", "c", "规则名称"])
        #expect(TelegramTemplate.render("你好 {name}，{missing}", values: ["name": "小明"]) == "你好 小明，{missing}")
        #expect(TelegramTemplate.render("你好 {name}，{missing}", values: ["name": "小明"], missing: .empty) == "你好 小明，")
        let ai = TelegramTemplate.render(TelegramTemplateSamples.aiDefaultTemplate, values: TelegramTemplateSamples.aiOutput, conditionals: true, missing: .empty)
        #expect(ai == "这是 AI 回答示例，已按当前消息模板渲染。\n\n— GPT-5.4 · in 128 / out 64  ·  auto: chat")
        #expect(TelegramTemplate.render("{?quoted}引用：{quoted}{/?}尾", values: [:], conditionals: true) == "尾")
        #expect(TelegramTemplate.unclosedConditions(in: "{?a}没有结尾").map(\.name) == ["a"])
    }

    @Test func highlightsSampleValuesButNotInsideTags() {
        let model = TelegramMessagePreviewModel(template: "<a href=\"{url}\">{title}</a> {nope}", values: ["url": "https://e.com", "title": "标题"])
        let kinds = Self.kinds(model.nodes)
        #expect(kinds.contains(.link(url: "https://e.com")))
        #expect(kinds.contains(.variable(name: nil)))
        #expect(kinds.contains(.missingVariable))
        #expect(model.missingVariables == ["nope"])
        #expect(model.issues.contains { $0.kind == .missingSample && $0.column == 29 })
        #expect(TelegramNode.plainText(model.nodes) == "标题 {nope}")
    }

    @Test func highlightsValuesThatContainTags() {
        let values = TelegramTemplateSamples.plugin(config: [:], prefix: ",")
        let model = TelegramMessagePreviewModel(template: "{ranking}", values: values)
        #expect(Self.kinds(model.nodes).contains(.blockquote(expandable: true)))
        #expect(Self.kinds(model.nodes).contains(.variable(name: nil)))
        #expect(model.issues.isEmpty)
    }

    @Test func buildsInteractionQueryAndPluginSamplesLikeWeb() {
        let query = TelegramTemplateSamples.interactionQuery(response: "", item: "")
        #expect(query.hasPrefix("<b>当前可用联动玩法</b>\n1. <b>示例玩法</b>\n触发方式：转账或关键词"))
        #expect(query.contains("2. <b>示例工具</b>"))
        let plugin = TelegramTemplateSamples.plugin(config: ["timeout": .integer(30), "draw_hour": .number(9), "command": .string("")], prefix: "")
        #expect(plugin["timeout"] == "30")
        #expect(plugin["draw_time"] == "09:00")
        #expect(plugin["command"] == "dicegrid")
        #expect(plugin["prefix"] == ",")
        #expect(plugin["reward_line"] == "首个答对者奖励：<b>+100</b> · 超时 30 秒")
        let keys: Set<String> = ["message_template", "round_message_template", "win_template", "help"]
        #expect(TelegramTemplateSamples.templateKey(forPreview: "template_preview", keys: keys) == "round_message_template")
        #expect(TelegramTemplateSamples.templateKey(forPreview: "win_preview", keys: keys) == "win_template")
        #expect(TelegramTemplateSamples.templateKey(forPreview: "help_preview", keys: keys) == "help")
        #expect(TelegramTemplateSamples.templateKey(forPreview: "none_preview", keys: keys) == nil)
        #expect(TelegramTemplate.normalizeEscapes("a\\nb\\r\\nc") == "a\nb\nc")
        #expect(TelegramTemplate.format(.array([.string("a"), .integer(1)])) == "a, 1")
        #expect(TelegramTemplate.format(.number(2.5)) == "2.5")
        #expect(TelegramParseMode(serverValue: "MarkdownV2") == .markdown)
        #expect(TelegramParseMode(serverValue: nil) == .html)
    }

    // MARK: - 工具

    static func kinds(_ nodes: [TelegramNode]) -> [TelegramElementKind] {
        nodes.flatMap { node -> [TelegramElementKind] in
            guard case .element(let element) = node else { return [] }
            return [element.kind] + kinds(element.children)
        }
    }
}
