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
        XCTAssertTrue(element.isHittable)
    }

    func testOverviewAndAccountActions() {
        let app = launch()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 15))
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

    func testOfflineKeepsSessionAndAllowsRetry() {
        let app = launch(["--ui-test-offline"])
        let refresh = app.buttons["overview.refresh"]
        XCTAssertTrue(refresh.waitForExistence(timeout: 15))
        XCTAssertFalse(app.secureTextFields["login.password"].exists)
        capture("离线状态", app: app)
        refresh.tap()
        XCTAssertTrue(refresh.exists)
        XCTAssertFalse(app.secureTextFields["login.password"].exists)
    }

    func testSettingsPresentation() {
        let app = launch()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 15))
        app.tabBars.buttons["更多"].tap()
        let settings = app.buttons["more.settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 10))
        settings.tap()
        XCTAssertTrue(app.buttons["settings.openWeb"].waitForExistence(timeout: 10))
        capture("设置", app: app)
        reveal(app.buttons["settings.logout"], app: app)
    }

    func testUnknownStatusDisablesMutations() {
        let app = launch()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 15))
        app.tabBars.buttons["更多"].tap()
        let accounts = app.buttons["more.accounts"]
        XCTAssertTrue(accounts.waitForExistence(timeout: 10))
        accounts.tap()
        let account = app.buttons["account.row.4"]
        XCTAssertTrue(account.waitForExistence(timeout: 10))
        reveal(account, app: app)
        account.tap()
        XCTAssertTrue(app.navigationBars["账号详情"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.descendants(matching: .any)["account.status"].firstMatch.waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["account.pause"].exists)
        XCTAssertFalse(app.buttons["account.resume"].exists)
    }

    func testUncertainMutationRequiresRefreshBeforeRetry() {
        let app = launch(["--ui-test-mutation-uncertain"])
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 15))
        app.tabBars.buttons["更多"].tap()
        let accounts = app.buttons["more.accounts"]
        XCTAssertTrue(accounts.waitForExistence(timeout: 10))
        accounts.tap()
        let account = app.buttons["account.row.1"]
        XCTAssertTrue(account.waitForExistence(timeout: 10))
        account.tap()
        XCTAssertTrue(app.navigationBars["账号详情"].waitForExistence(timeout: 10))
        let pause = app.buttons["account.pause"]
        XCTAssertTrue(app.descendants(matching: .any)["account.status"].firstMatch.waitForExistence(timeout: 10))
        reveal(pause, app: app)
        pause.tap()
        app.buttons["account.confirmAction"].firstMatch.tap()
        let feedback = app.descendants(matching: .any)["account.actionResult"].firstMatch
        XCTAssertTrue(feedback.waitForExistence(timeout: 10))
        XCTAssertFalse(pause.isEnabled)
        let refresh = app.buttons["account.refresh"]
        reveal(refresh, app: app)
        refresh.tap()
        XCTAssertTrue(app.buttons["account.resume"].waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["account.resume"].isEnabled)
    }

    func testStartupOfflineCanRetrySavedSession() {
        let app = launch(["--ui-test-session-retry"])
        let retry = app.buttons["login.retrySession"]
        XCTAssertTrue(retry.waitForExistence(timeout: 10))
        reveal(retry, app: app)
        retry.tap()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 10))
    }

    func testExpiredSessionClearsAccountViews() {
        let app = launch(["--ui-test-session-expired"])
        XCTAssertTrue(app.secureTextFields["login.password"].waitForExistence(timeout: 15))
        XCTAssertFalse(app.buttons["account.row.1"].exists)
        XCTAssertFalse(app.buttons["more.accounts"].exists)
    }

    func testCleanupFailureBlocksLoginUntilRetried() {
        let app = launch(["--ui-test-cleanup-failure"])
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 15))
        app.tabBars.buttons["更多"].tap()
        let settings = app.buttons["more.settings"]
        XCTAssertTrue(settings.waitForExistence(timeout: 10))
        settings.tap()
        let logout = app.buttons["settings.logout"]
        reveal(logout, app: app)
        logout.tap()
        app.buttons["settings.confirmLogout"].firstMatch.tap()
        let retry = app.buttons["login.retryCleanup"]
        XCTAssertTrue(retry.waitForExistence(timeout: 10))
        XCTAssertFalse(app.buttons["login.submit"].isEnabled)
        reveal(retry, app: app)
        retry.tap()
        XCTAssertTrue(app.secureTextFields["login.password"].exists)
        XCTAssertFalse(retry.exists)
    }

    func testRejectedResumeStillReadsChangedServerState() {
        let app = launch(["--ui-test-resume-rejected"])
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 15))
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
        XCTAssertTrue(cover.waitForExistence(timeout: 15))
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
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 15))
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
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 15))

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

    func testOverviewSurfacesItemsThatNeedAttention() {
        let app = launch()
        XCTAssertTrue(app.buttons["overview.refresh"].waitForExistence(timeout: 15))
        let relogin = app.buttons["overview.attention.account-3"]
        XCTAssertTrue(relogin.waitForExistence(timeout: 10))
        XCTAssertTrue(app.buttons["overview.attention.plugin-content_assistant"].exists)
        capture("概览需要处理", app: app)
        relogin.tap()
        XCTAssertTrue(app.descendants(matching: .any)["account.status"].firstMatch.waitForExistence(timeout: 10))
    }
}
