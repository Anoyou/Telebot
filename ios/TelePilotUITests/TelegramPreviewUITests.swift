import XCTest

/// Telegram 消息格式预览（0.22.0）：模板实验室、交互 Bot 文案、插件模板字段的实时气泡预览、格式检查与字数。
@MainActor
final class TelegramPreviewUITests: XCTestCase {
    private func launch(dock: String? = nil) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        var arguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        if let dock { arguments.insert("--ui-test-dock=\(dock)", at: 1) }
        app.launchArguments = arguments
        app.launch()
        XCTAssertTrue(app.tabBars.buttons["更多"].waitForExistence(timeout: 45))
        return app
    }

    private func element(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        app.descendants(matching: .any)[identifier].firstMatch
    }

    /// 懒加载表单：向上滑到元素出现、可点且不被键盘挡住。
    private func reveal(_ target: XCUIElement, in app: XCUIApplication, attempts: Int = 10) {
        for _ in 0..<attempts {
            if target.exists, target.isHittable {
                let keyboard = app.keyboards.firstMatch
                if !keyboard.exists || target.frame.maxY < keyboard.frame.minY - 8 { return }
            }
            app.swipeUp()
        }
    }

    /// 控件在容器右半边、贴着右下角（允许内边距）。
    private func assertAtBottomTrailing(_ control: XCUIElement, of container: XCUIElement, strictBottom: Bool, file: StaticString = #filePath, line: UInt = #line) {
        let inner = control.frame, outer = container.frame
        XCTAssertGreaterThan(inner.minX, outer.midX, "应在引用框右半边：\(inner) / \(outer)", file: file, line: line)
        XCTAssertLessThanOrEqual(outer.maxX - inner.maxX, 24, "应贴着引用框右边：\(inner) / \(outer)", file: file, line: line)
        XCTAssertLessThanOrEqual(inner.maxY, outer.maxY + 1, "应在引用框内：\(inner) / \(outer)", file: file, line: line)
        if strictBottom {
            XCTAssertLessThanOrEqual(outer.maxY - inner.maxY, 16, "应贴着引用框底边：\(inner) / \(outer)", file: file, line: line)
        } else {
            XCTAssertGreaterThan(inner.minY, outer.midY, "应在引用框下半部分：\(inner) / \(outer)", file: file, line: line)
        }
        XCTAssertGreaterThanOrEqual(inner.minY, outer.minY, file: file, line: line)
        // 箭头与文字紧挨：整个控件约 40pt；Label 给图标留整列宽时会宽到 50pt 以上。
        XCTAssertLessThan(inner.width, 48, "箭头与文字应紧挨：\(inner)", file: file, line: line)
    }

    private func text(_ app: XCUIApplication, containing value: String) -> XCUIElement {
        app.staticTexts.containing(NSPredicate(format: "label CONTAINS %@", value)).firstMatch
    }

    func testMessageTemplateLabLivePreviewRenderAndSend() {
        let app = launch(dock: "overview,plugins,operations")
        app.tabBars.buttons["指令"].tap()
        let lab = app.buttons["operations.templateLab"]
        reveal(lab, in: app)
        XCTAssertTrue(lab.waitForExistence(timeout: 10))
        lab.tap()
        let item = app.buttons["templateLab.item.plugin:redpacket:result_template"]
        XCTAssertTrue(item.waitForExistence(timeout: 10))
        item.tap()

        // 分屏：上方编辑、格式检查，下方实时预览；原模板没有格式问题。
        XCTAssertTrue(element(app, "templateLab.page.editor").waitForExistence(timeout: 10))
        XCTAssertTrue(element(app, "templateLab.noIssues").exists)
        let counter = element(app, "templateLab.page.counter")
        XCTAssertTrue(counter.exists)
        XCTAssertTrue(counter.label.contains("/ 4096"), counter.label)

        // 预览：示例值代入、剧透点按揭开、折叠引用展开、内联按钮。
        let spoiler = element(app, "templateLab.page.preview.spoiler")
        reveal(spoiler, in: app)
        XCTAssertTrue(spoiler.waitForExistence(timeout: 10))
        XCTAssertTrue(spoiler.label.contains("AI 红包"), spoiler.label)
        XCTAssertFalse(spoiler.label.contains("手气最佳"), "剧透默认隐藏：\(spoiler.label)")
        spoiler.tap()
        XCTAssertTrue(text(app, containing: "手气最佳：好运用户").waitForExistence(timeout: 5))
        let toggle = app.buttons["templateLab.page.preview.quote.toggle"]
        reveal(toggle, in: app)
        XCTAssertTrue(toggle.waitForExistence(timeout: 5))
        XCTAssertTrue(toggle.label.contains("展开"), toggle.label)
        // 与 Telegram 一致：「展开 / 收起」贴在引用框内右下角。
        let quote = element(app, "templateLab.page.preview.quote.expandable")
        XCTAssertTrue(quote.exists)
        // 折叠时内容被裁切，但无障碍容器的 frame 仍按未裁切的全文计算，底边只要求落在下半部分。
        assertAtBottomTrailing(toggle, of: quote, strictBottom: false)
        toggle.tap()
        let collapse = app.buttons["templateLab.page.preview.quote.toggle"]
        XCTAssertTrue(collapse.label.contains("收起"), collapse.label)
        // 等展开动画结束、引用框长高后再量位置（动画中读到的仍是折叠时的位置）。
        for _ in 0..<15 where quote.frame.maxY - collapse.frame.maxY > 16 { Thread.sleep(forTimeInterval: 0.2) }
        assertAtBottomTrailing(collapse, of: quote, strictBottom: true)
        let ruleButton = element(app, "templateLab.page.preview.button.0.1")
        reveal(ruleButton, in: app)
        XCTAssertTrue(ruleButton.exists)
        XCTAssertTrue(ruleButton.label.contains("规则"), ruleButton.label)

        // 服务器渲染校验，再测试发送（确认框挂在按钮上）。
        let render = app.buttons["templateLab.render"]
        reveal(render, in: app)
        XCTAssertTrue(render.waitForExistence(timeout: 5))
        render.tap()
        XCTAssertTrue(element(app, "templateLab.render.ok").waitForExistence(timeout: 10))
        let target = app.textFields["templateLab.target"]
        reveal(target, in: app)
        XCTAssertTrue(target.waitForExistence(timeout: 5))
        target.tap()
        target.typeText("10001")
        let send = app.buttons["templateLab.send"]
        reveal(send, in: app)
        XCTAssertTrue(send.isEnabled)
        send.tap()
        let confirm = app.buttons["确认发送"]
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        XCTAssertTrue(element(app, "templateLab.sent").waitForExistence(timeout: 10))

        // 改坏模板：格式检查立即给出带位置的中文提示；恢复原模板后消失。
        for _ in 0..<8 where !element(app, "templateLab.page.editor").isHittable { app.swipeDown() }
        let editor = element(app, "templateLab.page.editor")
        XCTAssertTrue(editor.isHittable)
        editor.coordinate(withNormalizedOffset: CGVector(dx: 0.97, dy: 0.97)).tap()
        editor.typeText("<i>")
        XCTAssertTrue(element(app, "templateLab.issue.0").waitForExistence(timeout: 5))
        XCTAssertFalse(element(app, "templateLab.noIssues").exists)
        app.buttons["templateLab.reset"].tap()
        XCTAssertTrue(element(app, "templateLab.noIssues").waitForExistence(timeout: 5))
    }

    func testInteractionTemplatesShowLivePreviewAndFullEditor() {
        let app = launch()
        app.tabBars.buttons["交互"].tap()
        let advanced = app.buttons["interaction.advanced"]
        reveal(advanced, in: app, attempts: 12)
        XCTAssertTrue(advanced.waitForExistence(timeout: 10))
        advanced.tap()

        // 玩法查询：回复模板与条目模板组合成整条消息预览。
        let queryPreview = element(app, "interaction.query.response.preview.bubble")
        reveal(queryPreview, in: app)
        XCTAssertTrue(queryPreview.waitForExistence(timeout: 10))
        // 第 21 轮：用本账号真实的玩法规则代入条目模板（夹具条目模板「• {name}」，启用的插件玩法「AI 答疑」）。
        XCTAssertTrue(text(app, containing: "AI 答疑").exists)
        XCTAssertFalse(text(app, containing: "示例玩法").exists, "有真实规则时不再用示例玩法")
        let counter = element(app, "interaction.query.response.counter")
        XCTAssertTrue(counter.label.contains("/ 4096"), counter.label)

        // 收款通知：示例金额代入；写错标签立即提示。
        let notice = element(app, "interaction.notice.transfer.editor")
        reveal(notice, in: app)
        XCTAssertTrue(notice.waitForExistence(timeout: 10))
        XCTAssertTrue(text(app, containing: "收到 88.00").exists)
        notice.coordinate(withNormalizedOffset: CGVector(dx: 0.97, dy: 0.8)).tap()
        notice.typeText(" <b>")
        XCTAssertTrue(element(app, "interaction.notice.transfer.issueCount").waitForExistence(timeout: 5))

        // 展开编辑：只看预览、切换外观，完成后回到原页面。
        let expand = app.buttons["interaction.notice.transfer.expand"]
        reveal(expand, in: app)
        XCTAssertTrue(expand.waitForExistence(timeout: 5))
        expand.tap()
        let layout = element(app, "interaction.notice.transfer.layout")
        XCTAssertTrue(layout.waitForExistence(timeout: 10))
        app.buttons["预览"].firstMatch.tap()
        XCTAssertTrue(element(app, "interaction.notice.transfer.page.preview.bubble").waitForExistence(timeout: 5))
        XCTAssertFalse(element(app, "interaction.notice.transfer.page.editor").exists)
        app.buttons["interaction.notice.transfer.done"].tap()
        XCTAssertTrue(element(app, "interaction.notice.transfer.editor").waitForExistence(timeout: 5))

        // 扣款通知（<pre><code> 代码块，第二行很长）：与 Telegram 一致完整显示、自动换行，不截成一行「…」。
        let code = element(app, "interaction.notice.debit.preview.code.text")
        reveal(code, in: app)
        XCTAssertTrue(code.waitForExistence(timeout: 10))
        XCTAssertTrue(code.label.contains("Alice 扣减 88.00 蝌蚪"), code.label)
        XCTAssertTrue(code.label.contains("Bob 接收 88.00 蝌蚪"), code.label)
        XCTAssertTrue(code.label.contains("在气泡里完整显示"), code.label)
        // XCUI 的 label 总是全文，截断与否要看高度：两行原文 + 长行折行，至少三行。
        let copy = app.buttons["interaction.notice.debit.preview.code.copy"]
        XCTAssertTrue(copy.exists)
        XCTAssertGreaterThanOrEqual(code.frame.height, 40, "代码块应完整显示多行：\(code.frame)")
        let box = element(app, "interaction.notice.debit.preview.code")
        XCTAssertLessThanOrEqual(code.frame.maxX, box.frame.maxX + 1, "代码块不应超出气泡：\(code.frame) / \(box.frame)")
        XCTAssertLessThanOrEqual(code.frame.maxY, box.frame.maxY + 1, "代码块不应被裁掉：\(code.frame) / \(box.frame)")
        // 「复制」图标与文字紧挨：整个按钮不应比文字宽太多（Label 给图标留整列宽时 50pt 以上）。
        XCTAssertLessThan(copy.frame.width, 48, "复制按钮图标与文字应紧挨：\(copy.frame)")
    }

    /// 第 21 轮：「每个玩法的条目模板」有示例效果——模板编辑处默认显示预览（按真实规则逐条代入），
    /// 交互页规则行显示「查询里显示：…」，规则区底部可展开整条查询回复，规则编辑页显示这条玩法在查询里的样子；改模板即时跟着变。
    func testInteractionQueryItemTemplatePreviewsEverywhere() {
        let app = launch()
        app.tabBars.buttons["交互"].tap()

        // 交互页规则行：会出现在查询回复里的规则带一行「查询里显示」。
        let ask = app.buttons["interaction.rule.rule-ask"]
        reveal(ask, in: app, attempts: 12)
        XCTAssertTrue(ask.waitForExistence(timeout: 10))
        XCTAssertTrue(ask.label.contains("查询里显示：• AI 答疑"), ask.label)
        let notice = app.buttons["interaction.rule.rule-notice"]
        reveal(notice, in: app)
        XCTAssertFalse(notice.label.contains("查询里显示"), "只发通知的规则不出现在查询回复里：\(notice.label)")

        // 规则区底部：整条查询回复（默认收起）。
        let toggle = app.buttons["interaction.queryPreview.toggle"]
        reveal(toggle, in: app)
        XCTAssertTrue(toggle.label.contains("列出 1 个玩法"), toggle.label)
        XCTAssertFalse(element(app, "interaction.queryPreview.bubble").exists)
        toggle.tap()
        let bubble = element(app, "interaction.queryPreview.bubble")
        reveal(bubble, in: app)
        XCTAssertTrue(bubble.waitForExistence(timeout: 5))
        XCTAssertTrue(text(app, containing: "当前可用联动玩法").exists)
        XCTAssertTrue(element(app, "interaction.queryPreview.prompt").exists, "上方是群成员发的查询指令")

        // 模板编辑处：条目模板默认显示预览；改模板后规则行跟着变。
        let advanced = app.buttons["interaction.advanced"]
        reveal(advanced, in: app)
        advanced.tap()
        let itemPreview = element(app, "interaction.query.item.preview.bubble")
        reveal(itemPreview, in: app)
        XCTAssertTrue(itemPreview.waitForExistence(timeout: 10), "条目模板默认显示示例效果")
        let itemEditor = element(app, "interaction.query.item.editor")
        reveal(itemEditor, in: app)
        itemEditor.coordinate(withNormalizedOffset: CGVector(dx: 0.97, dy: 0.8)).tap()
        itemEditor.typeText("（{kind}）")
        XCTAssertTrue(text(app, containing: "AI 答疑（玩法 content_assistant）").waitForExistence(timeout: 5))
        app.navigationBars.buttons.element(boundBy: 0).tap()
        XCTAssertTrue(advanced.waitForExistence(timeout: 10))
        // 回到交互页时停在下方的「查询指令与通知模板」，规则行在上面。
        for _ in 0..<8 where !(ask.exists && ask.isHittable) { app.swipeDown() }
        XCTAssertTrue(ask.isHittable)
        XCTAssertTrue(ask.label.contains("AI 答疑（玩法 content_assistant）"), ask.label)

        // 规则编辑页：这条玩法在查询里的样子。
        ask.tap()
        let rulePreview = element(app, "interaction.rule.queryPreview.bubble")
        reveal(rulePreview, in: app, attempts: 14)
        XCTAssertTrue(rulePreview.waitForExistence(timeout: 10))
        XCTAssertTrue(text(app, containing: "AI 答疑（玩法 content_assistant）").exists)
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "规则编辑页玩法查询预览"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    func testPluginTemplateFieldsPreviewWhileEditing() {
        let app = launch()
        app.tabBars.buttons["插件"].tap()
        let card = app.buttons["plugin.card.content_assistant"]
        XCTAssertTrue(card.waitForExistence(timeout: 10))
        card.tap()
        XCTAssertTrue(app.buttons["账号配置"].waitForExistence(timeout: 5))
        app.buttons["账号配置"].tap()
        XCTAssertTrue(app.buttons["plugin.config.edit"].waitForExistence(timeout: 5))
        app.buttons["plugin.config.edit"].tap()

        // `*_preview` 字段：主页「预览结果」按模板顺序显示消息气泡。
        let thread = element(app, "plugin.config.previews.bubble")
        reveal(thread, in: app)
        XCTAssertTrue(thread.waitForExistence(timeout: 10))
        XCTAssertTrue(text(app, containing: "@我 + 问题即可提问。").exists)

        // 模板字段：输入框下方实时预览、字数与格式检查。
        let templatePage = app.buttons["plugin.config.page.消息模板"]
        reveal(templatePage, in: app)
        XCTAssertTrue(templatePage.waitForExistence(timeout: 5))
        templatePage.tap()
        let editor = element(app, "tgField.help_template.editor")
        XCTAssertTrue(editor.waitForExistence(timeout: 10))
        XCTAssertTrue(element(app, "tgField.help_template.preview.bubble").exists)
        XCTAssertTrue(element(app, "tgField.help_template.counter").exists)
        editor.tap()
        editor.typeText("<b>")
        XCTAssertTrue(element(app, "tgField.help_template.issueCount").waitForExistence(timeout: 5))
    }
}
