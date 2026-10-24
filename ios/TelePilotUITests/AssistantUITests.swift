import XCTest

@MainActor
final class AssistantUITests: XCTestCase {
    private func openAssistant() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        let dock = app.tabBars.buttons["Agent"]
        XCTAssertTrue(dock.waitForExistence(timeout: 15))
        dock.tap()
        XCTAssertTrue(app.descendants(matching: .any)["assistant.composer"].firstMatch.waitForExistence(timeout: 10))
        return app
    }

    func testNativeConversationSendsAndKeepsHistory() {
        let app = openAssistant()
        XCTAssertTrue(app.staticTexts["服务状态正常。这里是界面测试数据。"].waitForExistence(timeout: 10))
        let composer = app.descendants(matching: .any)["assistant.composer"].firstMatch
        composer.tap()
        composer.typeText("请核对当前状态")
        let send = app.buttons["assistant.send"]
        XCTAssertTrue(send.isEnabled)
        send.tap()
        let cleared = NSPredicate { _, _ in !(composer.value as? String ?? "").contains("请核对当前状态") }
        expectation(for: cleared, evaluatedWith: composer)
        waitForExpectations(timeout: 10)
        XCTAssertTrue(app.staticTexts["服务状态正常。这里是界面测试数据。"].exists)
        XCTAssertFalse(send.isEnabled)
    }

    func testActionRequiresExplicitConfirmation() {
        let app = openAssistant()
        app.buttons["助手菜单"].tap()
        app.buttons["待确认操作"].firstMatch.tap()
        let row = app.buttons.matching(NSPredicate(format: "label CONTAINS %@", "暂停备用账号")).firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.tap()
        let execute = app.buttons["确认执行"].firstMatch
        XCTAssertTrue(execute.waitForExistence(timeout: 10))
        execute.tap()
        XCTAssertTrue(app.staticTexts["确认执行此操作？"].waitForExistence(timeout: 5))
        let cancel = app.buttons["取消"].firstMatch
        if cancel.exists && cancel.isHittable { cancel.tap() }
        else { app.otherElements["PopoverDismissRegion"].firstMatch.tap() }
        XCTAssertTrue(execute.exists)
        XCTAssertFalse(app.staticTexts["服务器已确认执行结果。"].exists)
    }

    func testLongTermMemoryRejectsOversizedEntry() {
        let app = openAssistant()
        app.buttons["助手菜单"].tap()
        app.buttons["长期记忆"].firstMatch.tap()
        let add = app.buttons["添加"].firstMatch
        XCTAssertTrue(add.waitForExistence(timeout: 10))
        add.tap()
        let save = app.buttons["保存"].firstMatch
        XCTAssertTrue(save.waitForExistence(timeout: 5))
        XCTAssertFalse(save.isEnabled)
        let editor = app.textViews.firstMatch
        editor.tap()
        editor.typeText(String(repeating: "a", count: 201))
        XCTAssertFalse(save.isEnabled)
    }
}
