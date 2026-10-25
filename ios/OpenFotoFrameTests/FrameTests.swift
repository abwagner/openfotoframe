import XCTest
import WebKit
@testable import OpenFotoFrame

final class FrameTests: XCTestCase {
    func testServerAddressesAndOriginBoundaries() throws {
        for address in ["https://frame.example.com", "http://192.168.1.20:5000", "http://frame.local", "http://100.64.12.1:5000", "http://localhost:5000"] {
            XCTAssertNoThrow(try FrameServer(address), address)
        }
        for address in ["frame.local", "ftp://frame.local", "http://example.com", "http://10.bad.1.2.3", "https://user:secret@frame.local", "https://frame.local/upload", "https://frame.local?token=secret", "https://frame.local#test"] {
            XCTAssertThrowsError(try FrameServer(address), address)
        }
        let server = try FrameServer("https://frame.local")
        XCTAssertTrue(server.contains(URL(string: "https://frame.local:443/upload")!))
        XCTAssertFalse(server.contains(URL(string: "https://frame.local.attacker.com/upload")!))
        XCTAssertFalse(server.contains(URL(string: "http://frame.local/upload")!))
        XCTAssertFalse(server.contains(URL(string: "https://frame.local:444/upload")!))
        XCTAssertEqual(server.endpoint("api/gallery/photo name.jpg").path, "/api/gallery/photo name.jpg")
    }

    @MainActor
    func testCookieScopeDoesNotLeakCredentials() throws {
        let cookie = HTTPCookie(properties: [.domain: ".frame.local", .path: "/api", .name: "session", .value: "test", .secure: "TRUE"])!
        XCTAssertTrue(FrameSession.cookie(cookie, matches: URL(string: "https://frame.local/api/gallery")!))
        XCTAssertFalse(FrameSession.cookie(cookie, matches: URL(string: "https://frame.local/apiculture")!))
        XCTAssertFalse(FrameSession.cookie(cookie, matches: URL(string: "http://frame.local/api/gallery")!))
        XCTAssertFalse(FrameSession.cookie(cookie, matches: URL(string: "https://evilframe.local/api/gallery")!))
        let expired = HTTPCookie(properties: [.domain: "frame.local", .path: "/", .name: "session", .value: "old", .expires: Date(timeIntervalSince1970: 0)])!
        XCTAssertFalse(FrameSession.cookie(expired, matches: URL(string: "https://frame.local/api/gallery")!))
    }

    func testDecodesExistingGalleryWithNullOverrides() throws {
        let data = Data(#"{"images":[{"filename":"abcd1234_Family.jpg","size":1024,"enabled":true,"title":"","uploaded_by":"admin","uploaded_at":"2026-10-25T10:00:00","width":1920,"height":1080,"scale":1.25,"mat_color":null,"mat_finish":null,"bevel_width":null,"border_effect":null,"crop":{"x":0,"y":0,"w":1,"h":1}}],"groups":{"group_one":{"images":["a.jpg","b.jpg"],"mat_color":null}}}"#.utf8)
        let gallery = try FrameJSON.decode(Gallery.self, from: data)
        XCTAssertEqual(gallery.images.first?.displayName, "Family.jpg")
        XCTAssertEqual(gallery.images.first?.uploadedBy, "admin")
        XCTAssertEqual(gallery.images.first?.scale, 1.25)
        XCTAssertNil(gallery.images.first?.matColor)
        XCTAssertEqual(gallery.groups["group_one"]?.images.count, 2)
    }

    func testSettingsPayloadDoesNotOverwriteSecurityOrOrder() throws {
        let data = Data(##"{"mat_color":"#ffffff","mat_finish":"linen","bevel_width":4,"border_effect":"bevel","slideshow_interval":60,"transition_duration":1,"fit_mode":"contain","shuffle":false,"target_aspect_ratio":"16:9","image_order":["b.jpg","a.jpg"],"mfa_mode":"required_all","tv_schedules":[]}"##.utf8)
        let settings = try FrameJSON.decode(FrameSettings.self, from: data)
        XCTAssertEqual(settings.imageOrder, ["b.jpg", "a.jpg"])
        XCTAssertNil(settings.payload["mfa_mode"])
        XCTAssertNil(settings.payload["image_order"])
        XCTAssertNil(settings.payload["tv_schedules"])
        XCTAssertEqual(settings.payload["mat_finish"] as? String, "linen")
    }

    func testMultipartMatchesFlaskAndSanitizesNames() {
        let file = UploadFile(name: "a\"\r\nInjected: yes.jpg", data: Data([0, 1, 2]), mimeType: "image/jpeg")
        let body = Multipart.encode([file], boundary: "test-boundary")
        let text = String(decoding: body, as: UTF8.self)
        XCTAssertTrue(text.contains("name=\"files\""))
        XCTAssertTrue(text.contains("filename=\"a___Injected__yes.jpg\""))
        XCTAssertFalse(text.contains("\r\nInjected:"))
        XCTAssertTrue(body.range(of: Data([0, 1, 2])) != nil)
        XCTAssertTrue(text.hasSuffix("--test-boundary--\r\n"))
    }

    func testDuplicateWarningsAndSupportedPhotoImport() throws {
        let response = Data(#"{"results":{"a.jpg":{"width":640,"height":480,"matches":[{"filename":"old.jpg","distance":2}]}},"errors":[]}"#.utf8)
        let check = try FrameJSON.decode(DuplicateCheck.self, from: response)
        XCTAssertTrue(check.results["a.jpg"]!.lowResolution)
        XCTAssertEqual(check.results["a.jpg"]?.matches.first?.filename, "old.jpg")
        // A PNG is passed through byte for byte, preserving transparency.
        let png = Data([0x89, 0x50, 0x4e, 0x47, 0x0d, 0x0a, 0x1a, 0x0a])
        let file = try PhotoImport.file(png, name: "My Photo")
        XCTAssertEqual(file.name, "My_Photo.png")
        XCTAssertEqual(file.data, png)
        XCTAssertEqual(file.mimeType, "image/png")
        XCTAssertThrowsError(try PhotoImport.file(Data("invalid".utf8), name: "bad"))
    }
}
