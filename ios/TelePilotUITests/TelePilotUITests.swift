import XCTest

@MainActor
final class TelePilotUITests: XCTestCase {
    private func launch(_ arguments: [String] = []) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"] + arguments
        app.launch()
        return app
    }

    private func capture(_ name: String, app: XCUIApplication) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func reveal(_ element: XCUIElement, app: XCUIApplication) {
        for _ in 0..<6 {
            if element.exists && element.isHittable { return }
            app.swipeUp()
        }
        // 目标可能已经滚到屏幕上方（例如先滚到底部的提交按钮），再往回找。
        for _ in 0..<6 {
            if element.exists && element.isHittable { return }
            app.swipeDown()
        }
        XCTAssertTrue(element.isHittable)
    }

    func testOverviewAndAccountActions() {
        let app = launch()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 45))
        capture("概览", app: app)

        app.tabBars.buttons["更多"].tap()
        let accounts = app.buttons["more.accounts"]
        XCTAssertTrue(accounts.waitForExistence(timeout: 10))
        accounts.tap()
        let account = app.buttons["account.row.1"]
        XCTAssertTrue(account.waitForExistence(timeout: 10))
        capture("账号列表", app: app)
        account.tap()
        XCTAssertTrue(app.navigationBars["账号详情"].waitForExistence(timeout: 10))

        let pause = app.buttons["account.pause"]
        XCTAssertTrue(app.descendants(matching: .any)["account.status"].firstMatch.waitForExistence(timeout: 10))
        capture("账号详情", app: app)
        reveal(pause, app: app)
        pause.tap()
        let confirm = app.buttons["account.confirmAction"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        let cancel = app.buttons["account.cancelAction"].firstMatch
        if cancel.exists {
            cancel.tap()
        } else {
            // Newer system popovers dismiss through their backdrop instead of a cancel row.
            app.otherElements["PopoverDismissRegion"].firstMatch.tap()
        }
        XCTAssertFalse(confirm.exists)
        XCTAssertTrue(pause.exists)

        pause.tap()
        app.buttons["account.confirmAction"].firstMatch.tap()
        let resume = app.buttons["account.resume"]
        XCTAssertTrue(resume.waitForExistence(timeout: 10))
        resume.tap()
        app.buttons["account.confirmAction"].firstMatch.tap()
        XCTAssertTrue(pause.waitForExistence(timeout: 10))
    }

    func testNativeLoginChallenges() {
        let app = launch(["--ui-test-login"])
        let server = app.textFields["login.server"]
        XCTAssertTrue(server.waitForExistence(timeout: 10))
        // Fixtures can prefill the server, but never real credentials.
        if !(server.value as? String ?? "").hasPrefix("https://") {
            server.tap()
            server.typeText("https://demo.telepilot.example")
        }
        let username = app.textFields["login.username"]
        username.tap()
        username.typeText("owner")
        app.buttons["login.keyboardDone"].firstMatch.tap()
        let password = app.secureTextFields["login.password"]
        reveal(password, app: app)
        password.tap()
        password.typeText("test-password")
        app.buttons["login.keyboardDone"].firstMatch.tap()
        reveal(app.buttons["login.submit"], app: app)
        app.buttons["login.submit"].tap()

        let code = app.textFields["login.code"]
        XCTAssertTrue(code.waitForExistence(timeout: 10))
        capture("二次验证", app: app)
        code.tap()
        code.typeText("123456")
        app.buttons["login.keyboardDone"].firstMatch.tap()
        reveal(app.buttons["login.submit"], app: app)
        app.buttons["login.submit"].tap()
        XCTAssertTrue(app.descendants(matching: .any)["login.challenge"].firstMatch.waitForExistence(timeout: 10))
        code.tap()
        let oldValue = code.value as? String ?? ""
        if oldValue.allSatisfy(\.isNumber) && !oldValue.isEmpty {
            code.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: oldValue.count))
        }
        code.typeText("654321")
        app.buttons["login.keyboardDone"].firstMatch.tap()
        reveal(app.buttons["login.submit"], app: app)
        app.buttons["login.submit"].tap()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 10))
    }

    /// 服务器启用 Cloudflare Access：登录被拦截时单独提示，用 Google 验证（fixture 不弹系统登录窗）后登录继续。
    func testCloudflareAccessBlockedLoginGuidesToGoogle() {
        let app = launch(["--ui-test-login", "--ui-test-cf-access"])
        let server = app.textFields["login.server"]
        XCTAssertTrue(server.waitForExistence(timeout: 10))
        if !(server.value as? String ?? "").hasPrefix("https://") {
            server.tap()
            server.typeText("https://demo.telepilot.example")
        }
        let username = app.textFields["login.username"]
        username.tap()
        username.typeText("owner")
        app.buttons["login.keyboardDone"].firstMatch.tap()
        let password = app.secureTextFields["login.password"]
        reveal(password, app: app)
        password.tap()
        password.typeText("test-password")
        app.buttons["login.keyboardDone"].firstMatch.tap()
        reveal(app.buttons["login.submit"], app: app)
        app.buttons["login.submit"].tap()

        // 懒加载表单：先滚动到 Cloudflare Access 区再断言。
        let google = app.buttons["login.cfAccess.google"]
        reveal(google, app: app)
        XCTAssertTrue(app.descendants(matching: .any)["login.cfAccess.notice"].firstMatch.exists)
        XCTAssertFalse(app.staticTexts["服务器返回了重定向。为保护登录信息，请直接填写最终 HTTPS 地址。"].exists)
        // 主方案是 Google 验证：Service Token 输入框默认收起。
        XCTAssertFalse(app.textFields["login.cfAccess.clientID"].exists)
        capture("Cloudflare Access 引导", app: app)
        google.tap()

        let result = app.descendants(matching: .any)["login.cfAccess.result"].firstMatch
        XCTAssertTrue(result.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["login.cfAccess.expiry"].firstMatch.exists)
        capture("Cloudflare Access 已验证", app: app)
        reveal(app.buttons["login.submit"], app: app)
        XCTAssertTrue(app.buttons["login.submit"].isEnabled)
        app.buttons["login.submit"].tap()
        // Access 放行后进入原有的二次验证步骤。
        XCTAssertTrue(app.textFields["login.code"].waitForExistence(timeout: 10))
    }

    /// 已登录后可在设置里用 Google 验证 Access，并在“高级”中填写、验证、清除 Service Token。
    func testCloudflareAccessSettingsGoogleAndServiceToken() {
        let app = launch()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 45))
        app.tabBars.buttons["更多"].tap()
        let settings = app.buttons["more.settings"]
        // 「App 设置」在「更多」最后一组，小屏首屏可能放不下（List 懒加载）：先滑到底部再找。
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<4 where !(settings.exists && settings.isHittable) { app.swipeUp() }
        XCTAssertTrue(settings.waitForExistence(timeout: 10))
        settings.tap()
        let entry = app.buttons["settings.cfAccess"]
        reveal(entry, app: app)
        entry.tap()

        let google = app.buttons["cfAccess.google"]
        XCTAssertTrue(google.waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["cfAccess.clearSession"].exists)
        google.tap()
        let result = app.descendants(matching: .any)["cfAccess.result"].firstMatch
        XCTAssertTrue(result.waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["cfAccess.expiry"].firstMatch.exists)
        capture("Cloudflare Access 设置", app: app)
        let clearSession = app.buttons["cfAccess.clearSession"]
        reveal(clearSession, app: app)
        clearSession.tap()
        let confirmSession = app.buttons["cfAccess.confirmClearSession"].firstMatch
        XCTAssertTrue(confirmSession.waitForExistence(timeout: 5))
        confirmSession.tap()
        XCTAssertTrue(clearSession.waitForNonExistence(timeout: 10))

        // 高级：Service Token。
        let clientID = app.textFields["cfAccess.clientID"]
        reveal(clientID, app: app)
        XCTAssertFalse(app.buttons["cfAccess.clear"].exists)
        clientID.tap()
        clientID.typeText("fixture.access")
        // 先收起键盘，否则下方的 Client Secret 可能被键盘挡住。
        app.buttons["cfAccess.keyboardDone"].firstMatch.tap()
        let secret = app.secureTextFields["cfAccess.clientSecret"]
        reveal(secret, app: app)
        secret.tap()
        secret.typeText("fixture-secret")
        app.buttons["cfAccess.keyboardDone"].firstMatch.tap()
        let save = app.buttons["cfAccess.save"]
        reveal(save, app: app)
        XCTAssertTrue(save.isEnabled)
        save.tap()
        let clear = app.buttons["cfAccess.clear"]
        _ = clear.waitForExistence(timeout: 10)
        reveal(clear, app: app)
        clear.tap()
        let confirm = app.buttons["cfAccess.confirmClear"].firstMatch
        XCTAssertTrue(confirm.waitForExistence(timeout: 5))
        confirm.tap()
        XCTAssertTrue(clear.waitForNonExistence(timeout: 10))
    }

    func testCleanupFailureBlocksLoginUntilRetried() {
        let app = launch(["--ui-test-cleanup-failure"])
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 45))
        app.tabBars.buttons["更多"].tap()
        let settings = app.buttons["more.settings"]
        // 「App 设置」在「更多」最后一组，小屏首屏可能放不下（List 懒加载）：先滑到底部再找。
        XCTAssertTrue(app.buttons["more.accounts"].waitForExistence(timeout: 10))
        for _ in 0..<4 where !(settings.exists && settings.isHittable) { app.swipeUp() }
        XCTAssertTrue(settings.waitForExistence(timeout: 10))
        settings.tap()
        let logout = app.buttons["settings.logout"]
        reveal(logout, app: app)
        logout.tap()
        app.buttons["settings.confirmLogout"].firstMatch.tap()
        // 懒加载表单：重试清理按钮在 Cloudflare Access 区下方，先等登录页出现再滚动查找。
        XCTAssertTrue(app.textFields["login.server"].waitForExistence(timeout: 10))
        let retry = app.buttons["login.retryCleanup"]
        reveal(retry, app: app)
        // 提交按钮在更下方，同样先滚动到它再断言。
        let submit = app.buttons["login.submit"]
        for _ in 0..<4 where !submit.exists { app.swipeUp() }
        XCTAssertFalse(submit.isEnabled)
        reveal(retry, app: app)
        retry.tap()
        XCTAssertTrue(retry.waitForNonExistence(timeout: 10))
        // 清理成功后仍停在登录页（密码框在 Access 区上方，滚回去确认）。
        reveal(app.secureTextFields["login.password"], app: app)
    }

    func testRejectedResumeStillReadsChangedServerState() {
        let app = launch(["--ui-test-resume-rejected"])
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 45))
        app.tabBars.buttons["更多"].tap()
        let accounts = app.buttons["more.accounts"]
        XCTAssertTrue(accounts.waitForExistence(timeout: 10))
        accounts.tap()
        let account = app.buttons["account.row.2"]
        XCTAssertTrue(account.waitForExistence(timeout: 10))
        account.tap()
        XCTAssertTrue(app.navigationBars["账号详情"].waitForExistence(timeout: 10))
        let resume = app.buttons["account.resume"]
        XCTAssertTrue(app.descendants(matching: .any)["account.status"].firstMatch.waitForExistence(timeout: 10))
        reveal(resume, app: app)
        resume.tap()
        app.buttons["account.confirmAction"].firstMatch.tap()
        let feedback = app.descendants(matching: .any)["account.actionResult"].firstMatch
        XCTAssertTrue(feedback.waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["account.resume"].exists)
        XCTAssertFalse(app.buttons["account.pause"].exists)
        let status = app.staticTexts.matching(NSPredicate(format: "label == %@", "需要重新登录")).firstMatch
        for _ in 0..<5 {
            if status.exists && status.isHittable { break }
            app.swipeDown()
        }
        capture("恢复失败后的真实状态", app: app)
        XCTAssertTrue(status.exists, app.debugDescription)
    }

    func testInterfaceLockCancelAndSuccess() {
        let app = launch(["--ui-test-lock", "--ui-test-lock-cancel"])
        let cover = app.descendants(matching: .any)["lock.cover"].firstMatch
        XCTAssertTrue(cover.waitForExistence(timeout: 45))
        let unlock = app.buttons["lock.unlock"]
        XCTAssertTrue(unlock.waitForExistence(timeout: 10), app.debugDescription)
        unlock.tap()
        XCTAssertTrue(cover.exists)
        XCTAssertTrue(app.staticTexts["验证已取消，内容仍保持锁定。"].waitForExistence(timeout: 5))
        capture("界面锁取消", app: app)

        app.terminate()
        app.launchArguments = ["--ui-testing", "--ui-test-lock", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"]
        app.launch()
        if unlock.waitForExistence(timeout: 2), unlock.isHittable { unlock.tap() }
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 10))
        XCTAssertFalse(cover.exists)
        app.buttons["overview.refresh"].tap()
        XCTAssertTrue(app.buttons["overview.refresh"].exists)
    }

    func testGlobalSearchAndCustomizableOverview() {
        let app = launch()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 45))
        app.buttons["overview.search"].tap()
        let search = app.searchFields.firstMatch
        XCTAssertTrue(search.waitForExistence(timeout: 10))
        search.tap()
        search.typeText("日常")
        let account = app.buttons["search.account.1"]
        XCTAssertTrue(account.waitForExistence(timeout: 5))
        account.tap()
        XCTAssertTrue(app.descendants(matching: .any)["account.status"].firstMatch.waitForExistence(timeout: 10))
        app.terminate()
        app.launch()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 45))

        let customize = app.buttons["overview.customizeButton"]
        reveal(customize, app: app)
        customize.tap()
        let moveDown = app.buttons["overview.moveDown.health"]
        XCTAssertTrue(moveDown.waitForExistence(timeout: 5))
        moveDown.tap()
        moveDown.tap()
        XCTAssertFalse(moveDown.isEnabled)
        let reset = app.buttons["overview.resetLayout"]
        reveal(reset, app: app)
        reset.tap()
        XCTAssertTrue(moveDown.isEnabled)
        capture("概览自定义", app: app)
    }

    /// 第 21 轮：概览改紧凑版。关键状态（运行模式、账号、插件、AI、待确认、24 小时错误）与三个服务模块
    /// 合成一张 3 列小指标格，不滚动就能在 Dock 上方看全；每格保留原来的入口。
    func testOverviewCompactTilesFitFirstScreen() {
        let app = launch()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 45))
        let runtime = app.buttons["overview.tile.runtime"]
        XCTAssertTrue(runtime.waitForExistence(timeout: 15))
        expectation(for: NSPredicate(format: "value CONTAINS %@", "生产"), evaluatedWith: runtime)
        waitForExpectations(timeout: 20)

        let tabBar = app.tabBars.firstMatch
        XCTAssertTrue(tabBar.waitForExistence(timeout: 5))
        let ids = ["runtime", "accounts", "plugins", "ai", "pending", "errors", "health", "workers", "resources"]
        for id in ids {
            let tile = app.buttons["overview.tile.\(id)"]
            XCTAssertTrue(tile.waitForExistence(timeout: 5), "缺少格子 \(id)")
            XCTAssertTrue(tile.isHittable, "格子 \(id) 应在首屏可点")
            XCTAssertLessThanOrEqual(tile.frame.maxY, tabBar.frame.minY + 1, "格子 \(id) 不应被 Dock 挡住")
            // 紧凑：一格不超过 80pt 高。
            XCTAssertLessThan(tile.frame.height, 80, "格子 \(id) 太高：\(tile.frame.height)")
        }
        let accounts = app.buttons["overview.tile.accounts"]
        expectation(for: NSPredicate(format: "value CONTAINS %@", "/"), evaluatedWith: accounts)
        waitForExpectations(timeout: 15)
        // 旧的大块标题不再出现；格子已能进入的入口不在快捷行里重复。
        XCTAssertFalse(app.staticTexts["关键数字"].exists)
        XCTAssertFalse(app.staticTexts["快捷操作"].exists)
        XCTAssertFalse(app.buttons["overview.quick.accounts"].exists)
        XCTAssertTrue(app.buttons["overview.quick.logs"].exists)
        capture("概览紧凑版", app: app)

        // 服务模块格点开是原来整组的内容。
        app.buttons["overview.tile.workers"].tap()
        XCTAssertTrue(app.navigationBars["账号运行进程"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["应运行进程"].waitForExistence(timeout: 5))
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // 待确认格进 Agent 待确认。
        let pending = app.buttons["overview.tile.pending"]
        XCTAssertTrue(pending.waitForExistence(timeout: 10))
        pending.tap()
        XCTAssertTrue(app.navigationBars["待确认操作"].waitForExistence(timeout: 10))
        app.navigationBars.buttons.element(boundBy: 0).tap()

        // 运行模式格进插件 ›「运行」。
        XCTAssertTrue(runtime.waitForExistence(timeout: 10))
        runtime.tap()
        XCTAssertTrue(app.descendants(matching: .any)["plugins.mode"].firstMatch.waitForExistence(timeout: 10))
    }

    /// 第 21 轮：值守中「运行模式」格显示「值守」并标需关注（与 Web 运行模式卡同名）。
    func testOverviewRuntimeTileShowsSafeWatch() {
        let app = launch(["--ui-test-safe-watch"])
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 45))
        let runtime = app.buttons["overview.tile.runtime"]
        XCTAssertTrue(runtime.waitForExistence(timeout: 15))
        expectation(for: NSPredicate(format: "value CONTAINS %@ AND value CONTAINS %@", "值守", "需关注"), evaluatedWith: runtime)
        waitForExpectations(timeout: 20)
        XCTAssertTrue(runtime.isHittable)
    }

    func testOverviewCheckupToolbarShowsVerdictSectionsAndFixEntry() {
        let app = launch()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 45))
        // “需要处理”区块已移除，由导航栏右上角的「一键体检」替代
        XCTAssertFalse(app.staticTexts["需要处理"].exists)
        let checkup = app.buttons["overview.checkup"]
        XCTAssertTrue(checkup.waitForExistence(timeout: 5))
        XCTAssertEqual(checkup.label, "一键体检")
        checkup.tap()
        XCTAssertTrue(app.descendants(matching: .any)["checkup.verdict"].firstMatch.waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["建议下一步"].exists)
        let section = app.buttons["checkup.section.accounts"]
        reveal(section, app: app)
        capture("一键体检", app: app)
        section.tap()
        XCTAssertTrue(app.staticTexts["原因与修复建议"].waitForExistence(timeout: 5))
        let fix = app.buttons["打开账号"].firstMatch
        XCTAssertTrue(fix.waitForExistence(timeout: 5))
        fix.tap()
        XCTAssertTrue(app.navigationBars["账号"].waitForExistence(timeout: 10))
    }
}
