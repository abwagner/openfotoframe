import XCTest
import WebKit
@testable import OpenFotoFrame

final class LiveSessionTests: XCTestCase {
    @MainActor
    func testNativeSessionCSRFUploadsAndMutations() async throws {
        guard let address = ProcessInfo.processInfo.environment["FRAME_TEST_SERVER"] else {
            throw XCTSkip("Run the OpenFotoFrameIntegration scheme with the isolated fixture server.")
        }
        let frame = FrameSession()
        try frame.connect(address)
        let server = try FrameServer(address)
        let config = URLSessionConfiguration.ephemeral
        let login = URLSession(configuration: config)
        defer { login.invalidateAndCancel() }
        let (page, _) = try await login.data(from: server.endpoint("login"))
        let html = String(decoding: page, as: UTF8.self)
        let tokenStart = try XCTUnwrap(html.range(of: "name=\"csrf_token\" value=\""))
        let token = String(html[tokenStart.upperBound...].prefix { $0 != "\"" })
        var request = URLRequest(url: server.endpoint("login"))
        request.httpMethod = "POST"
        request.setValue("application/x-www-form-urlencoded", forHTTPHeaderField: "Content-Type")
        request.httpBody = Data("username=admin&password=ios-integration-password&csrf_token=\(token)".utf8)
        let (_, response) = try await login.data(for: request)
        XCTAssertEqual(response.url?.path, "/upload")
        for cookie in config.httpCookieStorage?.cookies ?? [] { await frame.websiteData.httpCookieStore.setCookie(cookie) }
        await frame.didAuthenticate()
        XCTAssertTrue(frame.authenticated, frame.error ?? "No authenticated session")
        XCTAssertTrue(frame.isAdmin)

        let renderer = UIGraphicsImageRenderer(size: CGSize(width: 160, height: 100))
        let image = renderer.image { context in
            UIColor.red.setFill()
            context.fill(CGRect(x: 0, y: 0, width: 160, height: 100))
        }
        let file = try PhotoImport.file(XCTUnwrap(image.pngData()), name: "Integration-Test")
        let check = try FrameJSON.decode(DuplicateCheck.self, from: await frame.upload(file, checkOnly: true))
        XCTAssertTrue(check.results[file.name]?.lowResolution == true)
        let uploaded = try FrameJSON.decode(UploadResult.self, from: await frame.upload(file, checkOnly: false))
        let filename = try XCTUnwrap(uploaded.uploaded.first)
        _ = try await frame.mutate("api/gallery/\(filename)", method: "PATCH", body: ["title": "Native Test", "enabled": false, "scale": 1.2])
        var gallery = try await frame.get("api/gallery", as: Gallery.self)
        let photo = try XCTUnwrap(gallery.images.first { $0.filename == filename })
        XCTAssertEqual(photo.title, "Native Test")
        XCTAssertFalse(photo.enabled)
        XCTAssertEqual(photo.scale, 1.2)
        _ = try await frame.mutate("api/gallery/bulk", body: ["action": "enable", "filenames": [filename]])
        let other = try XCTUnwrap(gallery.images.first { $0.filename != filename })
        let groupData = try await frame.mutate("api/groups", body: ["images": [filename, other.filename]])
        let group = try XCTUnwrap((try JSONSerialization.jsonObject(with: groupData) as? [String: Any])?["group_id"] as? String)
        _ = try await frame.mutate("api/groups/\(group)", method: "DELETE")
        _ = try await frame.mutate("api/reorder", body: ["images": gallery.images.map(\.filename)])
        let settings = try await frame.get("api/settings", as: FrameSettings.self)
        _ = try await frame.mutate("api/settings", body: settings.payload)
        _ = try await frame.mutate("api/display/control", body: ["action": "pause"])
        let state = try await frame.get("api/display/state", as: DisplayState.self)
        XCTAssertTrue(state.paused)
        _ = try await frame.mutate("api/display/control", body: ["action": "play"])
        _ = try await frame.mutate("api/gallery/\(filename)", method: "DELETE")
        gallery = try await frame.get("api/gallery", as: Gallery.self)
        XCTAssertFalse(gallery.images.contains { $0.filename == filename })
        await frame.signOut(forgetServer: true)
        XCTAssertFalse(frame.authenticated)
        XCTAssertNil(frame.gallery)
        XCTAssertNil(frame.server)
    }
}
