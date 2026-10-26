import XCTest

final class OpenFotoFrameUITests: XCTestCase {
    func testServerValidation() {
        let app = XCUIApplication()
        app.launchArguments = ["--manual-frame-connection"]
        app.launch()
        resetServer(app)
        let address = app.textFields["serverAddress"]
        XCTAssertEqual(address.value as? String, "https://frame.tail5e8630.ts.net")
        XCTAssertTrue(app.buttons["connectButton"].isEnabled)
        address.tap()
        clear(address)
        address.typeText("http://example.com")
        app.buttons["connectButton"].tap()
        XCTAssertTrue(app.alerts["OpenFotoFrame"].waitForExistence(timeout: 5))
        XCTAssertTrue(app.alerts.staticTexts["Use HTTPS for servers outside your local network."].exists)
        app.alerts.buttons["OK"].tap()
        XCTAssertTrue(address.exists)
    }

    func testLiveServerLoginAndNativeWebSession() throws {
        guard let server = ProcessInfo.processInfo.environment["FRAME_TEST_SERVER"],
              let username = ProcessInfo.processInfo.environment["FRAME_TEST_USERNAME"],
              let password = ProcessInfo.processInfo.environment["FRAME_TEST_PASSWORD"] else {
            throw XCTSkip("Set FRAME_TEST_SERVER, FRAME_TEST_USERNAME, and FRAME_TEST_PASSWORD for the local integration server.")
        }
        let app = XCUIApplication()
        app.launchArguments = ["--manual-frame-connection"]
        app.launch()
        resetServer(app)
        if app.textFields["serverAddress"].exists {
            let address = app.textFields["serverAddress"]
            address.tap()
            clear(address)
            address.typeText(server)
            app.buttons["connectButton"].tap()
        }
        if app.webViews.textFields["Username"].waitForExistence(timeout: 10) {
            app.webViews.textFields["Username"].tap()
            app.webViews.textFields["Username"].typeText(username)
            app.webViews.secureTextFields["Password"].tap()
            app.webViews.secureTextFields["Password"].typeText(password)
            app.webViews.buttons["Login"].tap()
        }
        XCTAssertTrue(app.navigationBars["Your Photos"].waitForExistence(timeout: 15))
        XCTAssertTrue(app.staticTexts["2 photos"].exists)
        capture("Native gallery", app: app)
        let firstPhoto = app.buttons["galleryPhoto-abcdef00_Landscape.jpg"]
        XCTAssertTrue(firstPhoto.waitForExistence(timeout: 10))
        firstPhoto.tap()
        XCTAssertTrue(app.textFields["photoTitle"].waitForExistence(timeout: 5))
        app.swipeUp()
        app.swipeUp()
        app.buttons["Crop & Full Preview"].tap()
        XCTAssertTrue(app.webViews.buttons["Crop"].waitForExistence(timeout: 10))
        capture("Selected photo editor", app: app)
        app.buttons["Done"].tap()
        app.swipeDown()
        app.swipeDown()
        let title = app.textFields["photoTitle"]
        title.tap()
        let oldTitle = title.value as? String ?? ""
        title.typeText(String(repeating: XCUIKeyboardKey.delete.rawValue, count: oldTitle.count))
        title.typeText("Frame Test Landscape 1 Edited")
        app.buttons["Save"].tap()
        XCTAssertTrue(app.navigationBars["Your Photos"].waitForExistence(timeout: 10))
        app.tabBars.buttons["Frame"].tap()
        XCTAssertTrue(app.buttons["Watch Slideshow on This Device"].waitForExistence(timeout: 10))
        capture("Native frame remote", app: app)
        app.buttons["Watch Slideshow on This Device"].tap()
        XCTAssertTrue(app.webViews.firstMatch.waitForExistence(timeout: 10))
        XCTAssertFalse(app.webViews.buttons["Login"].exists)
        app.buttons["Done"].tap()
        app.tabBars.buttons["Settings"].tap()
        app.buttons["Change Password"].tap()
        XCTAssertTrue(app.webViews.secureTextFields.firstMatch.waitForExistence(timeout: 10))
        app.buttons["Done"].tap()
        app.swipeUp()
        app.buttons["Sign Out"].tap()
        XCTAssertTrue(app.webViews.buttons["Login"].waitForExistence(timeout: 10))
    }

    func testMFAGateAndForcedPasswordChange() throws {
        guard let server = ProcessInfo.processInfo.environment["FRAME_TEST_SERVER"] else {
            throw XCTSkip("Run the OpenFotoFrameIntegration scheme with a fresh fixture server.")
        }
        let app = XCUIApplication()
        app.launchArguments = ["--manual-frame-connection"]
        app.launch()
        connect(app, server: server, username: "firstlogin", password: "ios-firstlogin-password")
        XCTAssertTrue(app.webViews.buttons["Set Password"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.tabBars.buttons["Photos"].exists)
        app.webViews.secureTextFields["New Password"].tap()
        app.webViews.secureTextFields["New Password"].typeText("ios-firstlogin-password")
        app.webViews.secureTextFields["Confirm New Password"].tap()
        app.webViews.secureTextFields["Confirm New Password"].typeText("ios-firstlogin-password")
        app.webViews.buttons["Set Password"].tap()
        XCTAssertTrue(app.navigationBars["Your Photos"].waitForExistence(timeout: 10))
        app.tabBars.buttons["Settings"].tap()
        let access = app.descendants(matching: .any).matching(identifier: "accountAccess").firstMatch
        XCTAssertEqual(access.value as? String, "User")

        connect(app, server: server, username: "mfauser", password: "ios-mfauser-password")
        XCTAssertTrue(app.webViews.buttons["Verify code"].waitForExistence(timeout: 10))
        XCTAssertFalse(app.tabBars.buttons["Photos"].exists)
        app.webViews.textFields.firstMatch.tap()
        app.webViews.textFields.firstMatch.typeText("ios-recovery-code")
        app.webViews.buttons["Verify code"].tap()
        XCTAssertTrue(app.navigationBars["Your Photos"].waitForExistence(timeout: 10))
    }

    func testTailscaleConnectionWithoutSignIn() throws {
        guard ProcessInfo.processInfo.environment["FRAME_TEST_TAILSCALE"] == "1" else {
            throw XCTSkip("Run the OpenFotoFrameTailscale scheme while connected to the tailnet.")
        }
        let app = XCUIApplication()
        app.launchArguments = ["-frameServer", "https://frame.tail5e8630.ts.net", "-frameAutoConnectDisabled", "NO"]
        app.launch()
        XCTAssertTrue(app.navigationBars["Your Photos"].waitForExistence(timeout: 30))
        XCTAssertTrue(app.tabBars.buttons["Photos"].exists)
        XCTAssertFalse(app.navigationBars["Sign In"].exists)
        XCTAssertFalse(app.webViews.firstMatch.exists)
        // The live check only opens the gallery. No uploads or mutations.
    }

    func testMatPreviewInheritsDefaultsAndSavesExactPreset() throws {
        guard let server = ProcessInfo.processInfo.environment["FRAME_TEST_SERVER"] else {
            throw XCTSkip("Run with the isolated fixture server.")
        }
        let app = XCUIApplication()
        app.launchArguments = ["--manual-frame-connection"]
        app.launch()
        connect(app, server: server, username: "admin", password: "ios-integration-password")
        XCTAssertTrue(app.navigationBars["Your Photos"].waitForExistence(timeout: 10))
        app.buttons["galleryPhoto-abcdef00_Landscape.jpg"].tap()
        let preview = app.descendants(matching: .any).matching(identifier: "matPreview").firstMatch
        XCTAssertTrue(preview.waitForExistence(timeout: 10))
        XCTAssertTrue((preview.value as? String ?? "").contains("#f5f0e6"))
        XCTAssertTrue((preview.value as? String ?? "").contains("linen"))
        XCTAssertTrue((preview.value as? String ?? "").contains("bevel-lit, 10 pixels, light 25.0/0.0/50.0"), "Actual preview: \(preview.value ?? "missing")")
        capture("Native inherited cream mat", app: app)
        let toggle = app.switches["customMatToggle"]
        for _ in 0..<5 { if toggle.exists && toggle.isHittable { break }; app.swipeUp() }
        toggle.coordinate(withNormalizedOffset: CGVector(dx: 0.9, dy: 0.5)).tap()
        XCTAssertEqual(toggle.value as? String, "1")
        capture("Custom mat enabled", app: app)
        let tan = app.buttons["matPreset-Tan"]
        for _ in 0..<3 { if tan.exists && tan.isHittable { break }; app.swipeUp() }
        XCTAssertTrue(tan.exists)
        tan.tap()
        capture("Shared native mat colors", app: app)
        app.buttons["Save"].tap()
        XCTAssertTrue(app.navigationBars["Your Photos"].waitForExistence(timeout: 10))
        app.buttons["galleryPhoto-abcdef00_Landscape.jpg"].tap()
        XCTAssertTrue(preview.waitForExistence(timeout: 10))
        XCTAssertTrue((preview.value as? String ?? "").contains("#c4a882"))
        capture("Native saved tan mat", app: app)
    }

    func testAutomaticConnectionPreservesRequiredLogin() throws {
        guard let server = ProcessInfo.processInfo.environment["FRAME_TEST_SERVER"] else {
            throw XCTSkip("Run the OpenFotoFrameIntegration scheme with the fixture server.")
        }
        let app = XCUIApplication()
        app.launchArguments = ["--manual-frame-connection"]
        app.launch()
        resetServer(app)
        app.terminate()
        app.launchArguments = ["-frameServer", server, "-frameAutoConnectDisabled", "NO"]
        app.launch()
        XCTAssertTrue(app.webViews.buttons["Login"].waitForExistence(timeout: 15))
        XCTAssertFalse(app.tabBars.buttons["Photos"].exists)
        XCTAssertFalse(app.alerts["OpenFotoFrame"].exists)
    }

    private func connect(_ app: XCUIApplication, server: String, username: String, password: String) {
        resetServer(app)
        let address = app.textFields["serverAddress"]
        address.tap()
        clear(address)
        address.typeText(server)
        app.buttons["connectButton"].tap()
        XCTAssertTrue(app.webViews.textFields["Username"].waitForExistence(timeout: 10))
        app.webViews.textFields["Username"].tap()
        app.webViews.textFields["Username"].typeText(username)
        app.webViews.secureTextFields["Password"].tap()
        app.webViews.secureTextFields["Password"].typeText(password)
        app.webViews.buttons["Login"].tap()
    }

    private func resetServer(_ app: XCUIApplication) {
        let ready = NSPredicate { _, _ in
            app.textFields["serverAddress"].exists || app.tabBars.buttons["Settings"].exists || app.webViews.buttons["Login"].exists
        }
        expectation(for: ready, evaluatedWith: app)
        waitForExpectations(timeout: 15)
        if app.textFields["serverAddress"].exists { return }
        if app.tabBars.buttons["Settings"].exists {
            app.tabBars.buttons["Settings"].tap()
            app.swipeUp()
            app.buttons["Disconnect This Frame"].tap()
            app.buttons["Disconnect"].tap()
        } else if app.buttons["Server"].waitForExistence(timeout: 5) {
            app.buttons["Server"].tap()
        }
        XCTAssertTrue(app.textFields["serverAddress"].waitForExistence(timeout: 10))
    }

    private func capture(_ name: String, app: XCUIApplication) {
        let attachment = XCTAttachment(screenshot: app.screenshot())
        attachment.name = name
        attachment.lifetime = .keepAlways
        add(attachment)
    }

    private func clear(_ field: XCUIElement) {
        XCUIApplication().buttons["clearServerAddress"].tap()
        field.tap()
    }
}
