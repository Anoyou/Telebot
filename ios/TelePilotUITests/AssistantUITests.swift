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
        XCTAssertTrue(app.descendants(matching: .any)["assistant.panel.model"].firstMatch.exists)
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

    /// 第七轮：「+」与模型选择改为输入栏上方的浮层卡片（参考 Grok 录屏：从按钮位置模糊＋缩放展开），展开、选择、收起前后
    /// 输入框、「+」、模型按钮、发送按钮的 frame 完全不变；选择模型生效并在浮层里标出当前选中。
    func testComposerPanelsKeepComposerFramesAndSelectModel() {
        let app = openAssistant()
        let composer = app.descendants(matching: .any)["assistant.composer"].firstMatch
        let attach = app.buttons["assistant.attach"]
        let model = app.buttons["assistant.model"]
        let send = app.buttons["assistant.send"]
        XCTAssertTrue(model.waitForExistence(timeout: 5))
        let before = [composer.frame, attach.frame, model.frame, send.frame]
        func assertUnchanged(_ step: String, file: StaticString = #filePath, line: UInt = #line) {
            let now = [composer.frame, attach.frame, model.frame, send.frame]
            for (old, new) in zip(before, now) {
                XCTAssertEqual(new.minX, old.minX, accuracy: 0.5, "\(step)：\(old) → \(new)", file: file, line: line)
                XCTAssertEqual(new.minY, old.minY, accuracy: 0.5, "\(step)：\(old) → \(new)", file: file, line: line)
                XCTAssertEqual(new.width, old.width, accuracy: 0.5, "\(step)：\(old) → \(new)", file: file, line: line)
                XCTAssertEqual(new.height, old.height, accuracy: 0.5, "\(step)：\(old) → \(new)", file: file, line: line)
            }
        }

        // 模型浮层：在输入栏上方、靠右展开，当前为“自动”。
        model.tap()
        let modelPanel = app.descendants(matching: .any)["assistant.panel.model"].firstMatch
        XCTAssertTrue(modelPanel.waitForExistence(timeout: 5))
        let auto = app.buttons["assistant.model.option.auto"]
        XCTAssertTrue(auto.waitForExistence(timeout: 3))
        XCTAssertEqual(auto.value as? String, "已选中")
        assertUnchanged("模型浮层展开中")
        Thread.sleep(forTimeInterval: 0.6)
        assertUnchanged("模型浮层展开后")
        XCTAssertLessThanOrEqual(modelPanel.frame.maxY, composer.frame.minY)
        // 卡片右缘与模型按钮右缘对齐（从按钮位置展开）。
        XCTAssertEqual(modelPanel.frame.maxX, model.frame.maxX, accuracy: 2, "模型卡片 \(modelPanel.frame) 按钮 \(model.frame)")
        let option = app.buttons["测试供应商 · anthropic/fixture-model-with-a-much-longer-name"].firstMatch
        XCTAssertTrue(option.exists)
        XCTAssertGreaterThanOrEqual(option.frame.height, 44)
        option.tap()
        XCTAssertTrue(waitUntilGone(modelPanel), "选择后浮层应收起")
        let selected = NSPredicate(format: "label CONTAINS %@", "fixture-model-with-a-much-longer-name")
        expectation(for: selected, evaluatedWith: model)
        waitForExpectations(timeout: 5)
        assertUnchanged("选择模型后立即")
        Thread.sleep(forTimeInterval: 1)
        assertUnchanged("选择模型 1 秒后")

        // 再打开：浮层标出刚选的模型；点浮层外收起。
        model.tap()
        XCTAssertTrue(modelPanel.waitForExistence(timeout: 5))
        let chosen = app.buttons["测试供应商 · anthropic/fixture-model-with-a-much-longer-name"].firstMatch
        XCTAssertTrue(chosen.waitForExistence(timeout: 3))
        XCTAssertEqual(chosen.value as? String, "已选中")
        XCTAssertNotEqual(app.buttons["assistant.model.option.auto"].value as? String, "已选中")
        let modelPanelFrame = modelPanel.frame
        dismissPanel(app)
        XCTAssertTrue(waitUntilGone(modelPanel))
        assertUnchanged("模型浮层收起后")

        // 附件浮层：同一套卡片，从左下「+」一侧展开，只列图片来源。
        attach.tap()
        let attachPanel = app.descendants(matching: .any)["assistant.panel.attach"].firstMatch
        XCTAssertTrue(attachPanel.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["assistant.attach.photos"].exists)
        XCTAssertTrue(app.buttons["assistant.attach.file"].exists)
        XCTAssertEqual(app.buttons["assistant.attach.file"].label, "从文件选择图片")
        XCTAssertLessThan(attachPanel.frame.midX, modelPanelFrame.midX)
        XCTAssertLessThanOrEqual(attachPanel.frame.maxY, composer.frame.minY)
        XCTAssertEqual(attachPanel.frame.minX, attach.frame.minX, accuracy: 2, "附件卡片 \(attachPanel.frame) 按钮 \(attach.frame)")
        assertUnchanged("附件浮层展开中")
        Thread.sleep(forTimeInterval: 0.6)
        assertUnchanged("附件浮层展开后")
        // 打开时「+」模糊淡出（Grok 同款），点卡片外收起。
        dismissPanel(app)
        XCTAssertTrue(waitUntilGone(attachPanel))
        Thread.sleep(forTimeInterval: 0.6)
        assertUnchanged("附件浮层收起后")
        XCTAssertTrue(attach.isHittable)
        XCTAssertTrue(model.isHittable)
    }

    /// 点浮层外（对话区域上部）收起浮层。
    private func dismissPanel(_ app: XCUIApplication) {
        let scrim = app.descendants(matching: .any)["assistant.panel.dismiss"].firstMatch
        XCTAssertTrue(scrim.waitForExistence(timeout: 3))
        scrim.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.08)).tap()
    }

    private func waitUntilGone(_ element: XCUIElement, timeout: TimeInterval = 3) -> Bool {
        let gone = NSPredicate(format: "exists == false")
        return XCTWaiter().wait(for: [XCTNSPredicateExpectation(predicate: gone, object: element)], timeout: timeout) == .completed
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

    /// 第七轮：消息操作（参考 Codex）——图标＋文字、间距拉开、触控高度 ≥ 44pt；
    /// 我的消息下「复制／编辑」在气泡外下方、与气泡右边缘对齐；回答下「复制／重新生成」在正文下方、与正文左边缘对齐。
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
        // 带文字：比单个图标宽；触控高度至少 44pt。
        XCTAssertGreaterThan(edit.frame.width, 34)
        XCTAssertGreaterThan(regenerate.frame.width, 50)
        XCTAssertGreaterThanOrEqual(edit.frame.height, 44)
        XCTAssertGreaterThanOrEqual(regenerate.frame.height, 44)

        let question = app.staticTexts.matching(NSPredicate(format: "label BEGINSWITH %@", "第 119 条提问")).firstMatch
        let answer = app.staticTexts["第 120 条回答"]
        // 我的消息：操作行在气泡下方，右边缘与气泡右边缘对齐（按钮的无障碍 frame 按整点取整，实测相差约 2pt，容差 2.5pt）。
        let bubbles = app.descendants(matching: .any).matching(identifier: "assistant.message.bubble")
        let bubble = bubbles.allElementsBoundByIndex.last { $0.frame.contains(CGPoint(x: question.frame.midX, y: question.frame.midY)) }
        XCTAssertNotNil(bubble, visibleTexts(app))
        if let bubble {
            XCTAssertEqual(edit.frame.maxX, bubble.frame.maxX, accuracy: 2.5, "编辑 \(edit.frame) 气泡 \(bubble.frame)")
            XCTAssertGreaterThanOrEqual(edit.frame.minY, bubble.frame.maxY - 0.5)
        }
        XCTAssertLessThan(edit.frame.maxY, answer.frame.minY)
        let messageCopy = app.buttons.matching(identifier: "assistant.message.copy").allElementsBoundByIndex
            .last { abs($0.frame.midY - edit.frame.midY) < 2 }
        XCTAssertNotNil(messageCopy)
        if let messageCopy {
            XCTAssertLessThan(messageCopy.frame.maxX, edit.frame.minX)
            XCTAssertGreaterThanOrEqual(edit.frame.minX - messageCopy.frame.maxX, 16, "按钮间距过小")
            XCTAssertEqual(messageCopy.label, "复制消息")
        }

        // 回答：操作行在正文下方，左边缘与正文左边缘对齐。
        let replyCopies = app.buttons.matching(identifier: "assistant.reply.copy")
        XCTAssertTrue(replyCopies.count > 0)
        let lastCopy = replyCopies.allElementsBoundByIndex.last { abs($0.frame.midY - regenerate.frame.midY) < 2 }
        XCTAssertNotNil(lastCopy)
        guard let lastCopy else { return }
        XCTAssertEqual(lastCopy.label, "复制回复")
        XCTAssertEqual(lastCopy.frame.minX, answer.frame.minX, accuracy: 2.5, "复制 \(lastCopy.frame) 正文 \(answer.frame)")
        XCTAssertGreaterThan(regenerate.frame.minY, answer.frame.maxY)
        XCTAssertGreaterThanOrEqual(regenerate.frame.minX - lastCopy.frame.maxX, 16, "按钮间距过小")
        // 复制回复：轻量反馈（图标短暂变为对勾，VoiceOver 读“已复制”）。
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
