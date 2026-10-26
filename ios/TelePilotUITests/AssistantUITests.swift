import XCTest

@MainActor
final class AssistantUITests: XCTestCase {
    private func openAssistant(_ extra: [String] = []) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"] + extra
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
        // 输入为空时发送不可用；若任务仍在执行，右下角按钮会变为“停止”
        XCTAssertFalse(send.exists && send.isEnabled)
    }

    func testTurnUsageShowsModelThenMetricsOnTwoLines() {
        let app = openAssistant()
        XCTAssertTrue(app.staticTexts["服务状态正常。这里是界面测试数据。"].waitForExistence(timeout: 10))
        // 第一行“供应商 · 模型”，第二行词元、工具调用次数与耗时
        let details = app.descendants(matching: .any)
            .matching(NSPredicate(format: "label CONTAINS %@ AND label CONTAINS %@", "测试供应商 · fixture-model", "工具 2 次")).firstMatch
        XCTAssertTrue(details.waitForExistence(timeout: 5))
        XCTAssertTrue(details.label.contains("输出 340 词元"))
        XCTAssertTrue(details.label.contains("耗时"))
    }

    func testComposerUsesGlassToolbarWithAttachModelAndSend() {
        let app = openAssistant()
        let attach = app.buttons["assistant.attach"]
        let model = app.buttons["assistant.model"]
        let send = app.buttons["assistant.send"]
        XCTAssertTrue(attach.waitForExistence(timeout: 5))
        XCTAssertTrue(model.exists)
        XCTAssertTrue(send.exists)
        let composer = app.descendants(matching: .any)["assistant.composer"].firstMatch
        // 布局：输入框在上，工具栏在下；“+” 在左下，模型选择在发送按钮左侧
        XCTAssertLessThan(composer.frame.minY, attach.frame.minY)
        XCTAssertLessThan(attach.frame.midX, model.frame.midX)
        XCTAssertLessThan(model.frame.midX, send.frame.midX)
        XCTAssertLessThan(abs(model.frame.midY - send.frame.midY), 12)
        model.tap()
        XCTAssertTrue(app.buttons["自动，使用服务器配置"].firstMatch.waitForExistence(timeout: 5))
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

    /// 第六轮：模型菜单与「+」菜单收回后标签尺寸、位置不变（固定尺寸标签，选择变化不改变布局）。
    func testComposerMenusKeepFixedLabelFrames() {
        let app = openAssistant()
        let model = app.buttons["assistant.model"]
        let attach = app.buttons["assistant.attach"]
        XCTAssertTrue(model.waitForExistence(timeout: 5))
        let modelFrame = model.frame
        let attachFrame = attach.frame
        model.tap()
        let option = app.buttons["测试供应商 · anthropic/fixture-model-with-a-much-longer-name"].firstMatch
        XCTAssertTrue(option.waitForExistence(timeout: 5))
        option.tap()
        let selected = NSPredicate(format: "label CONTAINS %@", "fixture-model-with-a-much-longer-name")
        expectation(for: selected, evaluatedWith: model)
        waitForExpectations(timeout: 5)
        Thread.sleep(forTimeInterval: 1)
        XCTAssertEqual(model.frame.width, modelFrame.width, accuracy: 0.5)
        XCTAssertEqual(model.frame.minX, modelFrame.minX, accuracy: 0.5)
        XCTAssertEqual(model.frame.minY, modelFrame.minY, accuracy: 0.5)
        // 再选回“自动”，宽度仍不变。
        model.tap()
        let auto = app.buttons["自动，使用服务器配置"].firstMatch
        XCTAssertTrue(auto.waitForExistence(timeout: 5))
        auto.tap()
        Thread.sleep(forTimeInterval: 1)
        XCTAssertEqual(model.frame.width, modelFrame.width, accuracy: 0.5)
        // 「+」菜单打开后收回，标签位置与尺寸不变、仍可点按。
        attach.tap()
        XCTAssertTrue(app.buttons["从文件选择图片"].firstMatch.waitForExistence(timeout: 5))
        app.navigationBars.firstMatch.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.5)).tap()
        XCTAssertFalse(app.buttons["从文件选择图片"].firstMatch.waitForExistence(timeout: 2))
        Thread.sleep(forTimeInterval: 1)
        XCTAssertEqual(attach.frame, attachFrame)
        XCTAssertTrue(attach.isHittable)
    }

    /// 第六轮：长会话（120 条、每页 40 条）打开即可操作——导航栏按钮与输入栏立刻可点，先显示最近一屏，
    /// 加载期间即可上翻，且不会被内容高度变化拉回底部。
    func testLongHistoryOpensResponsivelyAndScrollsImmediately() {
        let app = openAssistant(["--ui-test-long-history"])
        let menu = app.buttons["助手菜单"]
        XCTAssertTrue(menu.waitForExistence(timeout: 3))
        XCTAssertTrue(menu.isHittable)
        XCTAssertTrue(app.buttons["assistant.model"].isHittable)
        let latest = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "第 119 条提问")).firstMatch
        XCTAssertTrue(latest.waitForExistence(timeout: 8), visibleTexts(app))
        XCTAssertTrue(app.staticTexts["第 120 条回答"].waitForExistence(timeout: 5), visibleTexts(app))
        let start = latest.frame.minY
        app.scrollViews.firstMatch.swipeDown()
        // 内容随手指下移，且停留 1 秒后没有被拉回底部。
        Thread.sleep(forTimeInterval: 1)
        XCTAssertTrue(!latest.exists || latest.frame.minY > start + 100, "上翻后被拉回：\(start) → \(latest.frame.minY)")
        app.scrollViews.firstMatch.swipeDown()
        Thread.sleep(forTimeInterval: 0.5)
        let newest = app.staticTexts["第 120 条回答"]
        XCTAssertTrue(!newest.exists || !newest.isHittable, "两次上翻后仍停在最新回答")
        // 更早的回答已经显示出来（增量追加完成，上翻可达）。
        let earlier = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@ AND label ENDSWITH %@", "第 1", "条回答"))
        XCTAssertTrue(earlier.firstMatch.waitForExistence(timeout: 3))
    }

    /// 第六轮：消息操作——我的消息下「复制消息／编辑消息」，回答下「复制回复／重新生成」（需确认）。
    func testMessageActionsSitUnderTheRightMessages() {
        let app = openAssistant(["--ui-test-long-history"])
        XCTAssertTrue(app.staticTexts["第 120 条回答"].waitForExistence(timeout: 10), visibleTexts(app))
        let edit = app.buttons["assistant.message.edit"]
        let regenerate = app.buttons["assistant.reply.regenerate"]
        XCTAssertTrue(edit.waitForExistence(timeout: 5))
        XCTAssertTrue(regenerate.exists)
        XCTAssertEqual(app.buttons.matching(identifier: "assistant.message.edit").count, 1)
        XCTAssertEqual(app.buttons.matching(identifier: "assistant.reply.regenerate").count, 1)
        XCTAssertEqual(edit.label, "编辑消息")
        XCTAssertEqual(regenerate.label, "重新生成")
        // 编辑在最新提问下方，重新生成在最新回答下方。
        let question = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "第 119 条提问")).firstMatch
        let answer = app.staticTexts["第 120 条回答"]
        XCTAssertGreaterThan(edit.frame.minY, question.frame.maxY)
        XCTAssertLessThan(edit.frame.maxY, answer.frame.minY)
        XCTAssertGreaterThan(regenerate.frame.minY, answer.frame.maxY)
        XCTAssertTrue(app.buttons.matching(identifier: "assistant.message.copy").count > 0)
        let replyCopies = app.buttons.matching(identifier: "assistant.reply.copy")
        XCTAssertTrue(replyCopies.count > 0)
        // 复制回复：轻量反馈，图标短暂变为对勾。
        let lastCopy = replyCopies.element(boundBy: replyCopies.count - 1)
        XCTAssertEqual(lastCopy.label, "复制回复")
        lastCopy.tap()
        XCTAssertTrue(app.buttons["已复制"].firstMatch.waitForExistence(timeout: 2))
        // 重新生成需要确认。
        regenerate.tap()
        XCTAssertTrue(app.staticTexts["重新生成这一轮回答？"].waitForExistence(timeout: 5))
        let cancel = app.buttons["取消"].firstMatch
        if cancel.exists && cancel.isHittable { cancel.tap() }
        else { app.otherElements["PopoverDismissRegion"].firstMatch.tap() }
        // 编辑消息打开编辑后重发。
        edit.tap()
        XCTAssertTrue(app.navigationBars["编辑后重新生成"].waitForExistence(timeout: 5))
        app.buttons["取消"].firstMatch.tap()
        XCTAssertTrue(app.descendants(matching: .any)["assistant.composer"].firstMatch.waitForExistence(timeout: 5))
    }

    /// 失败时附上当前可见的文字，便于判断停在了哪里。
    private func visibleTexts(_ app: XCUIApplication) -> String {
        "可见文字：" + app.staticTexts.allElementsBoundByIndex.prefix(40).map { "\($0.label)@\(Int($0.frame.minY))" }.joined(separator: " | ")
    }
}
