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
        // 第八轮：上一轮已结束时发出的消息直接执行——没有待处理气泡与处理方式按钮，输入栏也没有“发送方式”。
        XCTAssertFalse(app.descendants(matching: .any)["assistant.message.pending"].exists)
        XCTAssertFalse(app.buttons["assistant.queue.queue"].exists)
        XCTAssertFalse(app.buttons["assistant.sendMode"].exists)
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
        XCTAssertTrue(app.buttons["assistant.model.option.auto"].waitForExistence(timeout: 5))
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

    /// 第九轮：「+」与模型选择改为系统原生 popover（arrowEdge: .bottom，iPhone 上也以气泡呈现，箭头指向按钮）。
    /// 展开、选择、收起前后输入框、「+」、模型按钮、发送按钮的 frame 不变；模型列表每行前有家族 logo；选择模型生效并标出当前选中。
    func testComposerPanelsKeepComposerFramesAndSelectModel() {
        let app = openAssistant()
        let composer = app.descendants(matching: .any)["assistant.composer"].firstMatch
        let attach = app.buttons["assistant.attach"]
        let model = app.buttons["assistant.model"]
        let send = app.buttons["assistant.send"]
        XCTAssertTrue(model.waitForExistence(timeout: 5))
        let bar = app.descendants(matching: .any)["assistant.composerBar"].firstMatch
        XCTAssertTrue(bar.exists)
        let before = [composer.frame, attach.frame, model.frame, send.frame]
        /// 模型胶囊显示全名、可变宽——选择模型后只允许它的宽度变化（左缘紧挨「+」不动），其余 frame 完全不变。
        func assertUnchanged(_ step: String, modelWidthMayChange: Bool = false, file: StaticString = #filePath, line: UInt = #line) {
            let now = [composer.frame, attach.frame, model.frame, send.frame]
            for (index, (old, new)) in zip(before, now).enumerated() {
                XCTAssertEqual(new.minX, old.minX, accuracy: 0.5, "\(step)：\(old) → \(new)", file: file, line: line)
                XCTAssertEqual(new.minY, old.minY, accuracy: 0.5, "\(step)：\(old) → \(new)", file: file, line: line)
                if !(modelWidthMayChange && index == 2) {
                    XCTAssertEqual(new.width, old.width, accuracy: 0.5, "\(step)：\(old) → \(new)", file: file, line: line)
                }
                XCTAssertEqual(new.height, old.height, accuracy: 0.5, "\(step)：\(old) → \(new)", file: file, line: line)
            }
        }
        /// 第十轮：popover 锚定在输入栏卡片顶边、按钮正上方的隐形锚点（`assistant.popoverAnchor.<id>`）：
        /// 气泡整体在卡片之上（不压输入框），按钮中心落在 popover 的水平范围内；
        /// 箭头（锚点）离气泡左右两缘都至少 40pt——落在底边平直段，不落进 iOS 26 气泡的大圆角里（真机变形的根因）。
        func assertAnchored(_ card: XCUIElement, to button: XCUIElement, anchor id: String, _ step: String, file: StaticString = #filePath, line: UInt = #line) {
            let anchor = app.descendants(matching: .any)["assistant.popoverAnchor.\(id)"].firstMatch
            XCTAssertTrue(anchor.exists, "\(step)：缺少 popover 锚点", file: file, line: line)
            print("UI-RECORD R10-POPOVER \(id): content=\(card.frame) anchor=\(anchor.frame) button=\(button.frame) bar=\(bar.frame)")
            XCTAssertLessThanOrEqual(card.frame.maxY, bar.frame.minY + 0.5, "\(step)：popover 不应压住输入栏 \(card.frame) / \(bar.frame)", file: file, line: line)
            XCTAssertGreaterThan(card.frame.maxY, bar.frame.minY - 40, "\(step)：popover 离输入栏太远 \(card.frame) / \(bar.frame)", file: file, line: line)
            XCTAssertEqual(anchor.frame.midY, bar.frame.minY, accuracy: 2, "\(step)：锚点应在卡片顶边", file: file, line: line)
            XCTAssertTrue(card.frame.minX - 1 <= button.frame.midX && button.frame.midX <= card.frame.maxX + 1,
                          "\(step)：popover 应覆盖按钮上方 \(card.frame) / \(button.frame)", file: file, line: line)
            XCTAssertGreaterThanOrEqual(anchor.frame.midX - card.frame.minX, 40, "\(step)：箭头离气泡左缘太近（落进圆角） \(anchor.frame) / \(card.frame)", file: file, line: line)
            XCTAssertGreaterThanOrEqual(card.frame.maxX - anchor.frame.midX, 40, "\(step)：箭头离气泡右缘太近（落进圆角） \(anchor.frame) / \(card.frame)", file: file, line: line)
            XCTAssertLessThan(abs(anchor.frame.midX - button.frame.midX), 30, "\(step)：箭头应在按钮附近 \(anchor.frame) / \(button.frame)", file: file, line: line)
        }

        // 模型 popover：当前为“自动”，每行前有模型家族 logo。
        model.tap()
        let modelPanel = app.descendants(matching: .any)["assistant.panel.model"].firstMatch
        XCTAssertTrue(modelPanel.waitForExistence(timeout: 5))
        let auto = app.buttons["assistant.model.option.auto"]
        XCTAssertTrue(auto.waitForExistence(timeout: 3))
        XCTAssertEqual(auto.value as? String, "已选中")
        assertUnchanged("模型 popover 展开中")
        Thread.sleep(forTimeInterval: 0.6)
        assertUnchanged("模型 popover 展开后")
        assertAnchored(modelPanel, to: model, anchor: "model", "模型 popover")
        // 模型不多（7 行）时全部在 popover 内可见、无需滚动：最后一行（无法识别的家族）完整落在 popover 里。
        let lastRow = app.buttons["assistant.model.option.5"]
        XCTAssertTrue(lastRow.exists, "最后一个模型行未显示")
        XCTAssertLessThanOrEqual(lastRow.frame.maxY, modelPanel.frame.maxY + 1, "最后一行被裁切 \(lastRow.frame) / \(modelPanel.frame)")
        for family in ["auto", "openai", "anthropic", "deepseek", "google", "tencent", "unknown"] {
            let logo = app.descendants(matching: .any)["assistant.model.logo.\(family)"].firstMatch
            XCTAssertTrue(logo.exists, "缺少家族 logo：\(family)")
        }
        // logo 在行的最左侧（标题之前），与行垂直居中对齐。
        let deepseekRow = app.buttons["assistant.model.option.2"]
        let deepseekLogo = app.descendants(matching: .any)["assistant.model.logo.deepseek"].firstMatch
        XCTAssertTrue(deepseekRow.exists)
        XCTAssertLessThan(deepseekLogo.frame.midX, deepseekRow.frame.minX + 40, "\(deepseekLogo.frame) / \(deepseekRow.frame)")
        XCTAssertEqual(deepseekLogo.frame.midY, deepseekRow.frame.midY, accuracy: 4)
        XCTAssertTrue(deepseekRow.label.contains("deepseek-v4-flash"), deepseekRow.label)
        // 选择器：每行副标题都是「供应商 · 模型」全名；“自动”显示服务器当前路由到的模型。
        XCTAssertTrue(auto.label.contains("测试供应商 · openai/fixture-model"), auto.label)
        XCTAssertTrue(app.buttons["测试供应商 · openai/fixture-model"].firstMatch.exists)
        let option = app.buttons["测试供应商 · anthropic/fixture-model-with-a-much-longer-name"].firstMatch
        XCTAssertTrue(option.exists)
        XCTAssertGreaterThanOrEqual(option.frame.height, 44)
        option.tap()
        XCTAssertTrue(waitUntilGone(modelPanel), "选择后 popover 应收起")
        // 胶囊显示「供应商 · 模型」全名（与回答末尾模型名同一格式）。
        let selected = NSPredicate(format: "label CONTAINS %@", "测试供应商 · anthropic/fixture-model-with-a-much-longer-name")
        expectation(for: selected, evaluatedWith: model)
        waitForExpectations(timeout: 5)
        assertUnchanged("选择模型后立即", modelWidthMayChange: true)
        Thread.sleep(forTimeInterval: 1)
        assertUnchanged("选择模型 1 秒后", modelWidthMayChange: true)
        // 胶囊变宽也不越过发送按钮。
        XCTAssertLessThan(model.frame.maxX, send.frame.minX)

        // 再打开：标出刚选的模型；点 popover 外收起。
        model.tap()
        XCTAssertTrue(modelPanel.waitForExistence(timeout: 5))
        let chosen = app.buttons["测试供应商 · anthropic/fixture-model-with-a-much-longer-name"].firstMatch
        XCTAssertTrue(chosen.waitForExistence(timeout: 3))
        XCTAssertEqual(chosen.value as? String, "已选中")
        XCTAssertNotEqual(app.buttons["assistant.model.option.auto"].value as? String, "已选中")
        dismissPopover(app)
        XCTAssertTrue(waitUntilGone(modelPanel))
        Thread.sleep(forTimeInterval: 0.6)
        assertUnchanged("模型 popover 收起后", modelWidthMayChange: true)

        // 附件 popover：同样是原生 popover，从「+」弹出，只列图片来源。
        attach.tap()
        let attachPanel = app.descendants(matching: .any)["assistant.panel.attach"].firstMatch
        XCTAssertTrue(attachPanel.waitForExistence(timeout: 5))
        XCTAssertTrue(app.buttons["assistant.attach.photos"].exists)
        XCTAssertTrue(app.buttons["assistant.attach.file"].exists)
        XCTAssertEqual(app.buttons["assistant.attach.file"].label, "从文件选择图片")
        assertUnchanged("附件 popover 展开中", modelWidthMayChange: true)
        Thread.sleep(forTimeInterval: 0.6)
        assertUnchanged("附件 popover 展开后", modelWidthMayChange: true)
        assertAnchored(attachPanel, to: attach, anchor: "attach", "附件 popover")
        // 附件 popover 宽度贴合内容（第九轮固定 250pt，比文字宽出一截）。
        XCTAssertLessThanOrEqual(attachPanel.frame.width, 240, "附件 popover 过宽 \(attachPanel.frame)")
        XCTAssertGreaterThanOrEqual(app.buttons["assistant.attach.file"].frame.height, 44)
        XCTAssertEqual(attach.label, "添加图片")
        dismissPopover(app)
        XCTAssertTrue(waitUntilGone(attachPanel))
        Thread.sleep(forTimeInterval: 0.6)
        assertUnchanged("附件 popover 收起后", modelWidthMayChange: true)
        XCTAssertTrue(attach.isHittable)
        XCTAssertTrue(model.isHittable)
    }

    /// 第九轮：键盘弹起时打开原生 popover。只记录键盘是否被收起（写进测试日志 R9-KEYBOARD，供 PR 说明），
    /// 并确认 popover 仍出现在模型按钮上方、选择后输入栏可继续输入。
    func testComposerPopoverWithKeyboard() {
        let app = openAssistant()
        let composer = app.descendants(matching: .any)["assistant.composer"].firstMatch
        composer.tap()
        composer.typeText("草稿")
        XCTAssertTrue(app.keyboards.firstMatch.waitForExistence(timeout: 5))
        let model = app.buttons["assistant.model"]
        XCTAssertTrue(model.isHittable)
        model.tap()
        let modelPanel = app.descendants(matching: .any)["assistant.panel.model"].firstMatch
        XCTAssertTrue(modelPanel.waitForExistence(timeout: 5))
        Thread.sleep(forTimeInterval: 0.8)
        let keyboardVisible = app.keyboards.firstMatch.exists
        // 工作流「输出界面测试观察记录」步骤会把 UI-RECORD 行摘到 job 日志与摘要。
        print("UI-RECORD R9-KEYBOARD: 原生 popover 打开后键盘\(keyboardVisible ? "仍显示" : "已收起")")
        XCTContext.runActivity(named: "R9-KEYBOARD keyboardVisible=\(keyboardVisible)") { _ in }
        let bar = app.descendants(matching: .any)["assistant.composerBar"].firstMatch
        XCTAssertLessThanOrEqual(modelPanel.frame.maxY, bar.frame.minY + 0.5)
        app.buttons["assistant.model.option.auto"].tap()
        XCTAssertTrue(waitUntilGone(modelPanel))
        XCTAssertTrue((composer.value as? String ?? "").contains("草稿"))
    }

    /// 点 popover 外（对话区域上部）收起 popover。
    private func dismissPopover(_ app: XCUIApplication) {
        app.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: 0.2)).tap()
    }

    private func waitUntilGone(_ element: XCUIElement, timeout: TimeInterval = 3) -> Bool {
        let gone = NSPredicate(format: "exists == false")
        return XCTWaiter().wait(for: [XCTNSPredicateExpectation(predicate: gone, object: element)], timeout: timeout) == .completed
    }

    /// 第九轮：回答与我的消息都能打开「选择文本」面板（原生 sheet 里的只读 UITextView，可拖选任意片段）；
    /// 长按回复的菜单里也有「选择文本」；代码块右上角有「复制」。
    func testSelectTextOpensNativeTextViewSheet() {
        let app = openAssistant(["--ui-test-long-history"])
        XCTAssertTrue(app.staticTexts["第 120 条回答"].waitForExistence(timeout: 20), visibleTexts(app))
        let replySelect = app.buttons.matching(identifier: "assistant.reply.selectText").allElementsBoundByIndex.last { $0.isHittable }
        XCTAssertNotNil(replySelect, visibleTexts(app))
        guard let replySelect else { return }
        XCTAssertEqual(replySelect.label, "选择文本")
        XCTAssertGreaterThanOrEqual(replySelect.frame.height, 44)
        replySelect.tap()
        let textView = app.textViews["assistant.selectText.textView"]
        XCTAssertTrue(textView.waitForExistence(timeout: 8), "回答的选择文本面板没有出现")
        let replyText = textView.value as? String ?? ""
        XCTAssertTrue(replyText.contains("第 120 条回答"), replyText)
        XCTAssertTrue(replyText.contains("账号运行正常"), replyText)
        XCTAssertTrue(replyText.contains("status: ok"), replyText)
        XCTAssertFalse(replyText.contains("##"), "Markdown 标记应转成样式：\(replyText)")
        XCTAssertTrue(app.navigationBars["选择文本"].exists)
        app.buttons["assistant.selectText.done"].tap()
        XCTAssertTrue(waitUntilGone(textView, timeout: 10))

        // 我的消息：同样的面板，内容是原文。
        let messageSelect = app.buttons.matching(identifier: "assistant.message.selectText").allElementsBoundByIndex.last { $0.isHittable }
        XCTAssertNotNil(messageSelect)
        guard let messageSelect else { return }
        messageSelect.tap()
        XCTAssertTrue(textView.waitForExistence(timeout: 8), "我的消息的选择文本面板没有出现")
        XCTAssertTrue((textView.value as? String ?? "").contains("请检查账号与插件状态"), textView.value as? String ?? "")
        app.buttons["assistant.selectText.done"].tap()
        XCTAssertTrue(waitUntilGone(textView, timeout: 10))

        // 长按回复正文 → 菜单里的「选择文本」。
        // 菜单项与操作行按钮同名，用标识区分（操作行按钮的标识以 assistant. 开头）。
        let menuItem = app.buttons.matching(NSPredicate(format: "label == %@ AND NOT (identifier BEGINSWITH %@)", "选择文本", "assistant.")).firstMatch
        app.staticTexts["第 120 条回答"].press(forDuration: 1.2)
        if !menuItem.waitForExistence(timeout: 5) {
            // 模拟器偶发没识别出长按：再按一次。
            app.staticTexts["第 120 条回答"].press(forDuration: 1.5)
        }
        XCTAssertTrue(menuItem.waitForExistence(timeout: 5), "长按菜单里没有「选择文本」")
        menuItem.tap()
        XCTAssertTrue(textView.waitForExistence(timeout: 5))
        XCTAssertTrue((textView.value as? String ?? "").contains("第 120 条回答"))
        app.buttons["assistant.selectText.done"].tap()
        XCTAssertTrue(waitUntilGone(textView, timeout: 10))

        // 代码块右上角的「复制」：触控至少 44pt，点后显示“已复制”。
        let codeCopy = app.buttons.matching(identifier: "assistant.code.copy").allElementsBoundByIndex.last { $0.isHittable }
        XCTAssertNotNil(codeCopy)
        if let codeCopy {
            XCTAssertGreaterThanOrEqual(codeCopy.frame.height, 44)
            codeCopy.tap()
            // App 在界面测试里把「已复制」停留放长到 15 秒（点按后 XCTest 等空闲可能就要 5 秒以上）。
            XCTAssertTrue(app.buttons["已复制代码"].firstMatch.waitForExistence(timeout: 8), "代码块复制后没有显示“已复制”")
        }
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
        // 第八轮文案：我的消息「复制消息」「编辑消息」，回答「复制回复」「重新生成」（可见文字与无障碍标签一致）。
        XCTAssertTrue(app.staticTexts["编辑消息"].exists || edit.label == "编辑消息")
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
        // 正文左边缘：可选中文本（textSelection）的无障碍 frame 左侧多出约 16pt，不能直接比较；
        // 用同一条回答里列表项前不可选中的“•”（与正文同一左边缘）作基准。
        let bullet = app.staticTexts.matching(NSPredicate(format: "label == %@", "•")).allElementsBoundByIndex
            .filter { $0.frame.maxY < lastCopy.frame.minY && $0.frame.minY > answer.frame.minY }.last
        XCTAssertNotNil(bullet, visibleTexts(app))
        if let bullet {
            XCTAssertEqual(lastCopy.frame.minX, bullet.frame.minX, accuracy: 2.5, "复制 \(lastCopy.frame) 正文 \(bullet.frame)")
        }
        XCTAssertGreaterThan(regenerate.frame.minY, answer.frame.maxY)
        XCTAssertGreaterThanOrEqual(regenerate.frame.minX - lastCopy.frame.maxX, 16, "按钮间距过小")
        // 第八轮：回答的操作行在模型名／本轮用量那一行的上方（正文 → 操作行 → 模型名）。
        let details = app.descendants(matching: .any).matching(identifier: "assistant.turnDetails").allElementsBoundByIndex
            .filter { $0.frame.minY > regenerate.frame.minY - 1 }
            .min { $0.frame.minY < $1.frame.minY }
        XCTAssertNotNil(details, visibleTexts(app))
        if let details {
            XCTAssertLessThanOrEqual(regenerate.frame.maxY, details.frame.minY + 1, "操作行 \(regenerate.frame) 模型名行 \(details.frame)")
            XCTAssertLessThan(details.frame.minY - regenerate.frame.maxY, 40, "模型名行应紧跟操作行")
        }
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

    /// 第八轮：删除最后一个会话后离开 Agent 页再进入，仍是空会话（原来会从本机缓存把已删除会话的内容恢复出来）。
    func testDeletingLastSessionStaysEmptyAfterReentering() {
        let app = openAssistant()
        let answer = app.staticTexts["服务状态正常。这里是界面测试数据。"]
        XCTAssertTrue(answer.waitForExistence(timeout: 10))
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
        app.buttons["删除会话"].firstMatch.tap()
        XCTAssertTrue(waitUntilGone(row, timeout: 10), "删除后会话仍在列表里：\(visibleTexts(app))")
        let close = app.buttons["assistant.sessions.close"]
        XCTAssertTrue(close.waitForExistence(timeout: 5))
        close.tap()
        let empty = app.staticTexts["有什么需要处理？"]
        XCTAssertTrue(empty.waitForExistence(timeout: 5), visibleTexts(app))
        XCTAssertFalse(answer.exists)
        // 离开 Agent 页（最小化回到上一个标签页）再进入。
        let minimize = app.buttons["assistant.minimize"]
        XCTAssertTrue(minimize.waitForExistence(timeout: 5))
        minimize.tap()
        let dock = app.tabBars.buttons["Agent"]
        XCTAssertTrue(dock.waitForExistence(timeout: 5))
        dock.tap()
        XCTAssertTrue(app.descendants(matching: .any)["assistant.composer"].firstMatch.waitForExistence(timeout: 10))
        XCTAssertTrue(empty.waitForExistence(timeout: 5), visibleTexts(app))
        Thread.sleep(forTimeInterval: 1.5)
        XCTAssertFalse(answer.exists, "重新进入后显示了已删除会话的内容：\(visibleTexts(app))")
        XCTAssertTrue(empty.exists)
    }

    /// 第八轮：执行阶段显示在回答位置（不再有输入栏上方的“排队中”状态条），文字随服务器事件变化；
    /// 执行中发出的消息是虚线待处理气泡，下方可选排队／插入调整／停止并替换。
    func testRunPhasesShowInReplyAndQueuedMessagesArePending() {
        let app = openAssistant(["--ui-test-run-phases"])
        XCTAssertTrue(app.staticTexts["服务状态正常。这里是界面测试数据。"].waitForExistence(timeout: 10))
        let composer = app.descendants(matching: .any)["assistant.composer"].firstMatch
        composer.tap()
        composer.typeText("检查账号")
        app.buttons["assistant.send"].tap()

        // 回答位置的状态行随阶段变化，直到“调用工具：读取账号列表…”；全程不出现“排队中”。
        let status = app.descendants(matching: .any)["assistant.live.status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 5), visibleTexts(app))
        var seen: [String] = []
        let deadline = Date().addingTimeInterval(12)
        while Date() < deadline {
            if status.exists {
                let label = status.label
                if seen.last != label { seen.append(label) }
                if label.contains("调用工具：读取账号列表") { break }
            }
            Thread.sleep(forTimeInterval: 0.3)
        }
        XCTAssertTrue(seen.contains { $0.contains("调用工具：读取账号列表") }, "阶段：\(seen)")
        XCTAssertGreaterThanOrEqual(seen.count, 2, "状态没有随阶段变化：\(seen)")
        XCTAssertFalse(seen.contains { $0.contains("排队中") }, "执行中显示了排队中：\(seen)")
        // 没有独立的状态条与“发送方式”按钮。
        XCTAssertFalse(app.staticTexts["排队中"].exists)
        XCTAssertFalse(app.staticTexts["执行中"].exists)
        XCTAssertFalse(app.buttons["assistant.sendMode"].exists)

        // 执行中再发一条：虚线待处理气泡＋三个处理方式，默认“排队”。
        composer.tap()
        composer.typeText("改查最近两小时")
        app.buttons["assistant.send"].tap()
        let pending = app.descendants(matching: .any).matching(identifier: "assistant.message.pending")
        XCTAssertTrue(pending.firstMatch.waitForExistence(timeout: 5), visibleTexts(app))
        XCTAssertEqual(pending.firstMatch.value as? String, "待处理")
        let queueChip = app.buttons["assistant.queue.queue"].firstMatch
        let adjustChip = app.buttons["assistant.queue.adjust"].firstMatch
        XCTAssertTrue(queueChip.exists)
        XCTAssertTrue(adjustChip.exists)
        XCTAssertTrue(app.buttons["assistant.queue.replace"].firstMatch.exists)
        XCTAssertEqual(queueChip.value as? String, "已选中")
        let queueStatus = app.descendants(matching: .any).matching(identifier: "assistant.queue.status")
        XCTAssertTrue(queueStatus.firstMatch.label.contains("排队中 · 前面 1 条"), queueStatus.firstMatch.label)
        // 出现待处理消息时列表滚到底部，回答位置的状态行仍可见，且不是“排队中”。
        XCTAssertTrue(status.exists && !status.label.contains("排队中"), status.label)

        // 改为插入调整：服务器把它并入当前任务。
        adjustChip.tap()
        let adjusted = NSPredicate(format: "value == %@", "已选中")
        expectation(for: adjusted, evaluatedWith: app.buttons["assistant.queue.adjust"].firstMatch)
        // CI 模拟器负载高时界面已切到「插入调整」，无障碍值要 5～11 秒才跟上（录像与快照日志实测），放宽到 15 秒。
        waitForExpectations(timeout: 15)
        XCTAssertTrue(queueStatus.firstMatch.label.contains("将插入当前任务"), queueStatus.firstMatch.label)

        // 再发一条并改为停止并替换（需要确认）。
        composer.tap()
        composer.typeText("停止并改查插件")
        app.buttons["assistant.send"].tap()
        let replaceChips = app.buttons.matching(identifier: "assistant.queue.replace")
        let queuedAgain = app.descendants(matching: .any).matching(NSPredicate(format: "identifier == %@ AND label CONTAINS %@", "assistant.queue.status", "排队中 · 前面"))
        XCTAssertTrue(queuedAgain.firstMatch.waitForExistence(timeout: 5), visibleTexts(app))
        // 最后一个“停止并替换”属于刚发出的这条（插入调整的气泡在它上方）。
        let replace = replaceChips.element(boundBy: replaceChips.count - 1)
        XCTAssertTrue(replace.isEnabled)
        replace.tap()
        let confirm = app.buttons["确认停止并替换"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        let replaced = NSPredicate(format: "label CONTAINS %@", "等待停止当前任务")
        expectation(for: replaced, evaluatedWith: queueStatus.element(boundBy: queueStatus.count - 1))
        waitForExpectations(timeout: 15)
        XCTAssertEqual(app.buttons.matching(identifier: "assistant.queue.replace").element(boundBy: replaceChips.count - 1).value as? String, "已选中")
    }

    /// 失败时附上当前可见的文字，便于判断停在了哪里。
    private func visibleTexts(_ app: XCUIApplication) -> String {
        "可见文字：" + app.staticTexts.allElementsBoundByIndex.prefix(40).map { "\($0.label)@\(Int($0.frame.minY))" }.joined(separator: " | ")
    }
}
