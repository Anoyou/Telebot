import XCTest

/// 第 19 轮（iOS 0.20.0）：命中调试原生页、服务器参数原生表单、日志补「系统控制台」「Agent 运行」。
@MainActor
final class DiagnosticsUITests: XCTestCase {
    private func launch() -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        XCTAssertTrue(app.tabBars.buttons["更多"].waitForExistence(timeout: 45))
        app.tabBars.buttons["更多"].tap()
        return app
    }

    private func element(_ app: XCUIApplication, _ identifier: String) -> XCUIElement {
        app.descendants(matching: .any)[identifier].firstMatch
    }

    /// 小步滚动直到目标可点：List 懒加载，屏幕外的行不在层级里；一次 swipe 会滚过好几屏。
    /// 目标已在层级里时按它的位置决定方向，否则按 `up` 方向找。被键盘或底部标签栏挡住的也算不可点。
    /// 拖动只在屏幕上半部分（导航栏以下、键盘以上）进行，页面设了 scrollDismissesKeyboard，第一次拖动就会收起键盘。
    private func scrollTo(_ target: XCUIElement, in app: XCUIApplication, up: Bool = true, attempts: Int = 10) {
        let window = app.windows.firstMatch
        for _ in 0..<attempts {
            let keyboard = app.keyboards.firstMatch
            let tabBar = app.tabBars.firstMatch
            // 服务器参数页底部的保存状态条同样会挡住下面的行。
            let statusBar = app.descendants(matching: .any)["serverSettings.statusBar"].firstMatch
            let covered = target.exists && ((keyboard.exists && target.frame.midY > keyboard.frame.minY)
                || (tabBar.exists && target.frame.maxY > tabBar.frame.minY + 4)
                || (statusBar.exists && target.frame.midY > statusBar.frame.minY))
            if target.exists && target.isHittable && !covered { return }
            var towardBottom = up
            if target.exists, !target.frame.isEmpty {
                towardBottom = target.frame.midY > window.frame.midY
            }
            let start = window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: towardBottom ? 0.45 : 0.2))
            let end = window.coordinate(withNormalizedOffset: CGVector(dx: 0.5, dy: towardBottom ? 0.2 : 0.45))
            start.press(forDuration: 0.05, thenDragTo: end)
        }
    }

    /// 元素就在当前屏幕可见区域内（导航栏以下、键盘以上），不需要滚动。
    private func assertVisibleWithoutScrolling(_ target: XCUIElement, in app: XCUIApplication,
                                               file: StaticString = #filePath, line: UInt = #line) {
        let window = app.windows.firstMatch.frame
        let frame = target.frame
        XCTAssertFalse(frame.isEmpty, "元素没有位置", file: file, line: line)
        XCTAssertTrue(frame.minY >= window.minY && frame.maxY <= window.maxY, "元素不在屏幕内：\(frame)", file: file, line: line)
        let keyboard = app.keyboards.firstMatch
        if keyboard.exists {
            XCTAssertLessThanOrEqual(frame.maxY, keyboard.frame.minY + 1, "元素被键盘挡住：\(frame)", file: file, line: line)
        }
    }

    /// 点输入框的右端，让光标落在已有文字之后（右对齐的输入框点中间会把光标放到开头）。
    private func tapAtEnd(_ field: XCUIElement) {
        field.coordinate(withNormalizedOffset: CGVector(dx: 0.97, dy: 0.5)).tap()
    }

    /// 菜单（Menu / menu 样式 Picker）弹出的选项：不同系统版本是按钮或菜单项。
    private func menuOption(_ app: XCUIApplication, beginsWith prefix: String) -> XCUIElement {
        let predicate = NSPredicate(format: "(elementType == %d OR elementType == %d) AND label BEGINSWITH %@",
                                    XCUIElement.ElementType.button.rawValue, XCUIElement.ElementType.menuItem.rawValue, prefix)
        return app.descendants(matching: .any).matching(predicate).firstMatch
    }

    private func openLogs(_ app: XCUIApplication) {
        let logs = app.buttons["日志与诊断"]
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        scrollTo(logs, in: app, attempts: 6)
        XCTAssertTrue(logs.waitForExistence(timeout: 5))
        logs.tap()
        XCTAssertTrue(element(app, "logs.screen").waitForExistence(timeout: 10))
    }

    private func openDispatchDebug(_ app: XCUIApplication) {
        openLogs(app)
        let entry = app.buttons["logs.dispatchDebug"]
        scrollTo(entry, in: app)
        XCTAssertTrue(entry.exists, "日志与诊断里应有「命中调试」入口")
        XCTAssertTrue(entry.label.contains("命中调试"), entry.label)
        entry.tap()
        XCTAssertTrue(app.navigationBars["命中调试"].waitForExistence(timeout: 10))
    }

    private func runSimulation(_ app: XCUIApplication) {
        let run = app.buttons["dispatchDebug.run"]
        scrollTo(run, in: app)
        XCTAssertTrue(run.isEnabled, "填写文本后应能模拟")
        run.tap()
    }

    /// 选会话 → 输入关键词 → 模拟：四个阶段按顺序显示命中与否和中文原因。
    func testDispatchDebugShowsFourStagesWithReasons() {
        let app = launch()
        openDispatchDebug(app)
        let run = app.buttons["dispatchDebug.run"]
        scrollTo(run, in: app)
        XCTAssertTrue(run.exists, "应有「模拟命中」按钮")
        XCTAssertFalse(run.isEnabled, "没有文本时不能模拟")

        let peer = app.buttons["dispatchDebug.peer"]
        scrollTo(peer, in: app, up: false)
        XCTAssertTrue(peer.waitForExistence(timeout: 10))
        peer.tap()
        let groupA = menuOption(app, beginsWith: "测试群 A")
        XCTAssertTrue(groupA.waitForExistence(timeout: 10), "会话应来自会话白名单")
        groupA.tap()
        let chatID = app.textFields["dispatchDebug.chatID"]
        scrollTo(chatID, in: app, attempts: 4)
        XCTAssertEqual(chatID.value as? String, "-1001", "选中会话后自动填好 Chat ID")

        let text = element(app, "dispatchDebug.text")
        scrollTo(text, in: app, attempts: 6)
        text.tap()
        text.typeText("你好")
        runSimulation(app)

        let summary = element(app, "dispatchDebug.summary")
        scrollTo(summary, in: app, attempts: 6)
        XCTAssertTrue(summary.waitForExistence(timeout: 10))
        XCTAssertTrue(summary.label.contains("2/4"), summary.label)
        for (stage, expected) in [("direct_passthrough", "未命中"), ("prefix_command", "未命中"), ("keyword", "命中"), ("event_subscription", "命中")] {
            let row = element(app, "dispatchDebug.stage.\(stage)")
            scrollTo(row, in: app, attempts: 8)
            XCTAssertTrue(row.exists, "缺少阶段 \(stage)")
            XCTAssertEqual(row.value as? String, expected, "\(stage)：\(row.label)")
            if stage == "direct_passthrough" { XCTAssertTrue(row.label.contains("条件不满足"), row.label) }
            if stage == "keyword" { XCTAssertTrue(row.label.contains("关键词命中"), row.label) }
        }
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "命中调试四个阶段"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }

    /// 从最近消息一键填写并模拟；切到不在线的账号时如实提示 worker 未在线。
    func testDispatchDebugFillsFromRecentMessageAndReportsOfflineWorker() {
        let app = launch()
        openDispatchDebug(app)
        let recent = app.buttons["dispatchDebug.recent"]
        XCTAssertTrue(recent.waitForExistence(timeout: 10))
        // 整行都能打开菜单（点行中间，不只是标签文字）。
        recent.tap()
        let message = menuOption(app, beginsWith: "帮我总结一下今天的讨论")
        XCTAssertTrue(message.waitForExistence(timeout: 10), "最近消息应来自该账号的消息日志")
        message.tap()
        let text = element(app, "dispatchDebug.text")
        scrollTo(text, in: app, attempts: 6)
        XCTAssertTrue((text.value as? String ?? "").contains("帮我总结"), "选中消息后自动填好文本")
        runSimulation(app)
        let summary = element(app, "dispatchDebug.summary")
        scrollTo(summary, in: app, attempts: 6)
        XCTAssertTrue(summary.waitForExistence(timeout: 10), "模拟结果显示在按钮下方")
        XCTAssertTrue(summary.label.contains("1/4"), summary.label)

        let account = app.buttons["dispatchDebug.account"]
        scrollTo(account, in: app, up: false)
        account.tap()
        let backup = menuOption(app, beginsWith: "备用账号")
        XCTAssertTrue(backup.waitForExistence(timeout: 5))
        backup.tap()
        XCTAssertTrue(summary.waitForNonExistence(timeout: 5), "切换账号后清空旧结果")
        runSimulation(app)
        let error = element(app, "dispatchDebug.error")
        scrollTo(error, in: app, attempts: 6)
        XCTAssertTrue(error.waitForExistence(timeout: 10), "worker 未在线时显示错误")
        XCTAssertTrue(error.label.contains("worker 未在线"), error.label)
    }

    /// 服务器参数：中文名称与说明、只提交改动、校验与服务器一致；其它键仍可原始编辑。
    func testServerSettingsEditsCommonKeysAndKeepsRawEditor() {
        let app = launch()
        let system = app.buttons["服务器设置"]
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        // 「更多 › 工具与设置」里有命中调试原生页入口（与日志与诊断里的同一页）。
        let moreDebug = app.buttons["more.dispatchDebug"]
        scrollTo(moreDebug, in: app, attempts: 8)
        XCTAssertTrue(moreDebug.exists, "更多 › 工具与设置 应有「命中调试」")
        XCTAssertTrue(moreDebug.label.contains("命中调试"), moreDebug.label)
        scrollTo(system, in: app, attempts: 8)
        system.tap()
        let entry = app.buttons["system.serverSettings"]
        XCTAssertTrue(entry.waitForExistence(timeout: 10))
        entry.tap()
        XCTAssertTrue(app.navigationBars["服务器参数"].waitForExistence(timeout: 10))

        let prefix = app.textFields["serverSettings.field.command_prefix"]
        XCTAssertTrue(prefix.waitForExistence(timeout: 10))
        XCTAssertEqual(prefix.value as? String, ".")
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "指令前缀")).firstMatch.exists)
        let save = app.buttons["serverSettings.save"]
        XCTAssertFalse(save.isEnabled, "没有修改时不能保存")

        // 前缀超过 3 个字符：本地校验拦下，不提交。
        tapAtEnd(prefix)
        prefix.typeText("!!!")
        XCTAssertEqual(prefix.value as? String, ".!!!")
        XCTAssertTrue(save.isEnabled)
        XCTAssertTrue(element(app, "serverSettings.discard").waitForExistence(timeout: 5), "有修改时底部应提示并可放弃")
        save.tap()
        let saveError = element(app, "serverSettings.saveError")
        XCTAssertTrue(saveError.waitForExistence(timeout: 5))
        assertVisibleWithoutScrolling(saveError, in: app)
        XCTAssertTrue(saveError.label.contains("最多 3 个字符"), saveError.label)
        tapAtEnd(prefix)
        prefix.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: 3))
        XCTAssertEqual(prefix.value as? String, ".")

        let context = app.textFields["serverSettings.field.ai_context_messages"]
        scrollTo(context, in: app, attempts: 6)
        XCTAssertTrue(context.exists, "AI 指令上下文条数应为原生输入框")
        XCTAssertEqual(context.value as? String, "3")
        tapAtEnd(context)
        context.typeText(XCUIKeyboardKey.delete.rawValue + "5")
        XCTAssertEqual(context.value as? String, "5")
        save.tap()
        // 保存结果固定在页面底部（键盘上方），不用滚动就能看到。
        let saved = element(app, "serverSettings.saved")
        XCTAssertTrue(saved.waitForExistence(timeout: 10), "保存后应显示成功")
        XCTAssertFalse(saveError.exists, saveError.exists ? saveError.label : "")
        XCTAssertTrue(saved.label.contains("已保存"), saved.label)
        assertVisibleWithoutScrolling(saved, in: app)
        XCTAssertFalse(save.isEnabled, "保存后回到无修改状态")
        XCTAssertEqual(context.value as? String, "5")

        let trace = app.switches["serverSettings.toggle.trace_enabled"]
        scrollTo(trace, in: app)
        XCTAssertTrue(trace.exists, "Trace 设置应为原生开关")
        let raw = app.buttons["serverSettings.raw"]
        scrollTo(raw, in: app)
        XCTAssertTrue(raw.exists, "其它参数仍可原始编辑")
        XCTAssertTrue(app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "AI 调用限额")).firstMatch.exists, "未原生化的键列在「其它参数」里")
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "服务器参数"
        screenshot.lifetime = .keepAlways
        add(screenshot)
        raw.tap()
        XCTAssertTrue(app.navigationBars["全部服务器参数"].waitForExistence(timeout: 10))
    }

    /// 日志补两类：系统控制台（原始行、默认脱敏）与 Agent 运行（失败轮次的错误与事件轨迹）。
    func testLogsShowSystemConsoleAndAgentRuns() {
        let app = launch()
        openLogs(app)
        let console = app.buttons["logs.kind.console"]
        scrollTo(console, in: app)
        XCTAssertTrue(console.exists)
        console.tap()
        XCTAssertTrue(app.navigationBars["系统控制台"].waitForExistence(timeout: 10))
        let errorLine = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "模型请求超时")).firstMatch
        XCTAssertTrue(errorLine.waitForExistence(timeout: 10))
        XCTAssertFalse(errorLine.label.contains("sk-proj-THIS_IS_A_FAKE_SECRET_FOR_UI_TEST_123456"), "控制台输出默认脱敏")
        app.navigationBars.buttons.element(boundBy: 0).tap()

        let agent = app.buttons["logs.kind.agent"]
        scrollTo(agent, in: app)
        XCTAssertTrue(agent.exists)
        agent.tap()
        XCTAssertTrue(app.navigationBars["Agent 运行"].waitForExistence(timeout: 10))
        let failed = app.buttons["logs.agentRun.fixture-agent-run-failed"]
        XCTAssertTrue(failed.waitForExistence(timeout: 10))
        XCTAssertTrue(failed.label.contains("模型供应商 30 秒内没有响应"), failed.label)
        failed.tap()
        XCTAssertTrue(app.navigationBars["Agent 运行详情"].waitForExistence(timeout: 10))
        let retry = app.descendants(matching: .any).matching(NSPredicate(format: "label CONTAINS %@", "计划重试")).firstMatch
        scrollTo(retry, in: app)
        XCTAssertTrue(retry.waitForExistence(timeout: 10), "事件轨迹应显示中文步骤")
        let folded = app.staticTexts.matching(NSPredicate(format: "label CONTAINS %@", "已隐藏 2 条")).firstMatch
        scrollTo(folded, in: app)
        XCTAssertTrue(folded.exists, "心跳与流式增量折叠并注明条数")
        let screenshot = XCTAttachment(screenshot: app.screenshot())
        screenshot.name = "Agent 运行详情"
        screenshot.lifetime = .keepAlways
        add(screenshot)
    }
}
