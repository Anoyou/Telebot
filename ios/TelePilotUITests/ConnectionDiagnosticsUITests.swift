import XCTest

/// 第 22 轮：连接失败诊断提示（一句原因 + 怎么办、可展开可复制的诊断详情）与 Agent 进度流断线重连提示。
@MainActor
final class ConnectionDiagnosticsUITests: XCTestCase {
    private func launch(_ extra: [String]) -> XCUIApplication {
        continueAfterFailure = false
        let app = XCUIApplication()
        app.launchArguments = ["--ui-testing", "-AppleLanguages", "(zh-Hans)", "-AppleLocale", "zh_CN"] + extra
        app.launch()
        return app
    }

    /// cloudflared 没在运行（Cloudflare 530 / 错误 1033）：概览状态行直接说原因和怎么办，
    /// 「诊断详情」展开后有目标地址、请求、尝试次数、耗时、底层错误码与 Cloudflare 编号，可复制。
    func testTunnelOfflineShowsReasonAndCopyableDetails() {
        let app = launch(["--ui-test-tunnel-offline"])
        let status = app.descendants(matching: .any)["overview.status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 45))
        let explained = NSPredicate(format: "label CONTAINS %@ AND label CONTAINS %@", "无法读取服务器状态", "cloudflared")
        expectation(for: explained, evaluatedWith: status)
        waitForExpectations(timeout: 30)
        XCTAssertFalse(status.label.contains("HTTP 530），请在 Web 管理台检查详情"), status.label)

        let toggle = app.buttons.matching(NSPredicate(format: "label == %@ OR identifier == %@", "诊断详情", "connection.details.toggle")).firstMatch
        XCTAssertTrue(toggle.waitForExistence(timeout: 10), app.debugDescription)
        toggle.tap()
        let target = app.descendants(matching: .any)["connection.details.target"].firstMatch
        XCTAssertTrue(target.waitForExistence(timeout: 5))
        XCTAssertTrue(target.label.contains("https://ui-test.invalid"), target.label)
        let request = app.descendants(matching: .any)["connection.details.request"].firstMatch
        XCTAssertTrue(request.label.contains("GET /api/system/version"), request.label)
        let low = app.descendants(matching: .any)["connection.details.error"].firstMatch
        XCTAssertTrue(low.label.contains("HTTP 530") && low.label.contains("CLOUDFLARE_1033"), low.label)
        XCTAssertTrue(app.descendants(matching: .any)["connection.details.ray"].firstMatch.exists)
        XCTAssertTrue(app.descendants(matching: .any)["connection.details.attempts"].firstMatch.exists)
        XCTAssertTrue(app.descendants(matching: .any)["connection.details.elapsed"].firstMatch.exists)

        let copy = app.buttons["connection.details.copy"].firstMatch
        for _ in 0..<3 where !(copy.exists && copy.isHittable) { app.swipeUp() }
        XCTAssertTrue(copy.exists)
        copy.tap()
        XCTAssertTrue(app.buttons["已复制"].firstMatch.waitForExistence(timeout: 5))

        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = "Tunnel 不在线的诊断详情"
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    /// Agent 进度流中途断开：显示「已断开 x 秒，正在第 n 次重连」，重新连上（收到进度）后提示消失。
    func testAgentStreamDropShowsReconnectingThenRecovers() {
        let app = launch(["--ui-test-run-phases", "--ui-test-stream-drop"])
        let dock = app.tabBars.buttons["Agent"]
        XCTAssertTrue(dock.waitForExistence(timeout: 45))
        dock.tap()
        let composer = app.descendants(matching: .any)["assistant.composer"].firstMatch
        XCTAssertTrue(composer.waitForExistence(timeout: 10))
        XCTAssertTrue(app.staticTexts["服务状态正常。这里是界面测试数据。"].waitForExistence(timeout: 10))
        composer.tap()
        composer.typeText("检查账号")
        app.buttons["assistant.send"].tap()

        let banner = app.descendants(matching: .any)["assistant.reconnect"].firstMatch
        XCTAssertTrue(banner.waitForExistence(timeout: 15), "没有出现断线重连提示")
        XCTAssertTrue(banner.label.contains("已断开") && banner.label.contains("次重连"), banner.label)
        XCTAssertTrue(banner.label.contains("连接中途断开"), banner.label)

        // 第二次重连仍失败时次数递增；随后重新连上，提示消失、进度继续显示。
        let gone = NSPredicate(format: "exists == false")
        expectation(for: gone, evaluatedWith: banner)
        waitForExpectations(timeout: 20)
        let status = app.descendants(matching: .any)["assistant.live.status"].firstMatch
        XCTAssertTrue(status.waitForExistence(timeout: 10))
        XCTAssertFalse(app.staticTexts["进度连接已中断，任务仍在服务器执行。点击“恢复进度”重新订阅。"].exists)
    }
}
