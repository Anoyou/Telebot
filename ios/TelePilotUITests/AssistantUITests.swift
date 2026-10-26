import XCTest

@MainActor
final class AssistantUITests: XCTestCase {
    private func openAssistant() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        let dock = app.tabBars.buttons["Agent"]
        XCTAssertTrue(dock.waitForExistence(timeout: 45))
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

    func testAgentHidesDockAndMinimizeReturnsToPreviousTab() {
        let app = openAssistant()
        let overview = app.tabBars.buttons["概览"]
        XCTAssertFalse(overview.exists && overview.isHittable)
        let minimize = app.buttons["assistant.minimize"]
        XCTAssertTrue(minimize.waitForExistence(timeout: 5))
        minimize.tap()
        XCTAssertTrue(app.tabBars.buttons["Agent"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.tabBars.buttons["概览"].isHittable)
    }

    func testSessionListClosesAndDeleteNeedsConfirmation() {
        let app = openAssistant()
        let open = app.buttons["assistant.sessions"]
        guard open.waitForExistence(timeout: 5) else { return } // iPad 使用常驻侧栏
        open.tap()
        let row = app.buttons["assistant.session.fixture-session"].firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.swipeLeft()
        let delete = app.buttons["删除"].firstMatch
        XCTAssertTrue(delete.waitForExistence(timeout: 5))
        delete.tap()
        XCTAssertTrue(app.staticTexts["删除这个会话？"].waitForExistence(timeout: 5))
        let cancel = app.buttons["取消"].firstMatch
        if cancel.exists && cancel.isHittable { cancel.tap() }
        else { app.otherElements["PopoverDismissRegion"].firstMatch.tap() }
        XCTAssertTrue(row.exists)
        let close = app.buttons["assistant.sessions.close"]
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        close.tap()
        XCTAssertTrue(app.descendants(matching: .any)["assistant.composer"].firstMatch.waitForExistence(timeout: 5))
        XCTAssertFalse(close.exists)
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

    func testSkillsToolbarEntryOpensListAndDiffWraps() {
        let app = openAssistant()
        // Skills 直接在助手工具栏，不再藏在“助手菜单”
        let entry = app.buttons["assistant.skills"]
        XCTAssertTrue(entry.waitForExistence(timeout: 5))
        entry.tap()
        let row = app.descendants(matching: .any)["skills.row.group-helper"].firstMatch
        XCTAssertTrue(row.waitForExistence(timeout: 10))
        row.tap()
        let diffLink = app.buttons["查看与默认版本的差异"].firstMatch
        for _ in 0..<4 where !(diffLink.exists && diffLink.isHittable) { app.swipeUp() }
        XCTAssertTrue(diffLink.waitForExistence(timeout: 5))
        diffLink.tap()
        let stats = app.descendants(matching: .any)["skills.diff.stats"].firstMatch
        XCTAssertTrue(stats.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["+3"].exists)
        XCTAssertTrue(app.staticTexts["−2"].exists)
        // 未改动的大段折叠为「展开 N 行」
        let gap = app.buttons.matching(NSPredicate(format: "label BEGINSWITH %@", "展开")).firstMatch
        XCTAssertTrue(gap.waitForExistence(timeout: 5))
        let longLine = app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", "新增：- 结果整理成表格")).firstMatch
        for _ in 0..<4 where !longLine.exists { app.swipeUp() }
        XCTAssertTrue(longLine.waitForExistence(timeout: 5))
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label BEGINSWITH %@", "删除：## PT 站点搜索")).firstMatch.exists)
        // 长行换行：不超出窗口宽度
        let window = app.windows.firstMatch.frame
        XCTAssertLessThanOrEqual(longLine.frame.maxX, window.maxX + 1)
        XCTAssertGreaterThan(longLine.frame.height, 30)
    }
}
