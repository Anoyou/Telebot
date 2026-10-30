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
        toggle.tap()
        XCTAssertTrue(app.buttons["templateLab.page.preview.quote.toggle"].label.contains("收起"))
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
        XCTAssertTrue(text(app, containing: "示例玩法").exists)
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
