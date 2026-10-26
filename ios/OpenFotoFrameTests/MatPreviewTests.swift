import XCTest
import SwiftUI
@testable import OpenFotoFrame

final class MatPreviewTests: XCTestCase {
    func testSharedPaletteAndExactSRGBRoundTrips() {
        let palette = MatPalette.shared
        XCTAssertEqual(palette.neutrals.count, 10)
        XCTAssertEqual(palette.accents.count, 25)
        for preset in palette.neutrals + palette.accents {
            XCTAssertEqual(Color(frameHex: preset.color).frameHex, preset.color, preset.title)
        }
        for component in 0...255 {
            let hex = String(format: "#%02x%02x%02x", component, 255 - component, (component * 37) % 256)
            XCTAssertEqual(Color(frameHex: hex).frameHex, hex)
        }
        XCTAssertEqual(Color(frameHex: "#fff").frameHex, "#ffffff")
        XCTAssertEqual(Color(frameHex: "#abc").frameHex, "#aabbcc")
    }

    func testPreviewLeavesMatVisibleAndRespondsToScale() {
        let frame = CGSize(width: 320, height: 180)
        let image = CGSize(width: 1600, height: 1000)
        let normal = MatPreviewGeometry.photoSize(in: frame, image: image, scale: 1, effect: 0, shadow: false)
        let smaller = MatPreviewGeometry.photoSize(in: frame, image: image, scale: 0.5, effect: 0, shadow: false)
        let zoomed = MatPreviewGeometry.photoSize(in: frame, image: image, scale: 3, effect: 2, shadow: true)
        XCTAssertEqual(normal.height, 162, accuracy: 0.01)
        XCTAssertEqual(normal.width / normal.height, 1.6, accuracy: 0.01)
        XCTAssertEqual(smaller.height, normal.height / 2, accuracy: 0.01)
        XCTAssertLessThanOrEqual(zoomed.width, frame.width * 0.95)
        XCTAssertLessThanOrEqual(zoomed.height, frame.height * 0.95)
        XCTAssertEqual(MatPreviewGeometry.aspectRatio("21:9"), 21.0 / 9, accuracy: 0.001)
        XCTAssertEqual(MatPreviewGeometry.aspectRatio("invalid"), 16.0 / 9, accuracy: 0.001)
    }

    func testGalleryRetainsNormalizedCropForNativePreview() throws {
        let data = Data(#"{"images":[{"filename":"a.jpg","size":20,"enabled":true,"crop":{"x":0.1,"y":0.2,"w":0.7,"h":0.6}}],"groups":{}}"#.utf8)
        let gallery = try FrameJSON.decode(Gallery.self, from: data)
        XCTAssertEqual(gallery.images.first?.crop?.x, 0.1)
        XCTAssertEqual(gallery.images.first?.crop?.w, 0.7)
    }
}
