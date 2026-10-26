// Package the shared web icon for Apple's opaque 1024-point app icon catalog.
// Run from any directory with: swift ios/scripts/generate_icon.swift
import AppKit
import ImageIO
import UniformTypeIdentifiers

let root = URL(fileURLWithPath: #filePath)
    .deletingLastPathComponent().deletingLastPathComponent().deletingLastPathComponent()
let source = root.appendingPathComponent("static/icons/openfotoframe-512.png")
let output = root.appendingPathComponent("ios/OpenFotoFrame/Assets.xcassets/AppIcon.appiconset/AppIcon.png")

guard let imageSource = CGImageSourceCreateWithURL(source as CFURL, nil),
      let image = CGImageSourceCreateImageAtIndex(imageSource, 0, nil),
      let context = CGContext(
        data: nil, width: 1024, height: 1024, bitsPerComponent: 8, bytesPerRow: 0,
        space: CGColorSpace(name: CGColorSpace.sRGB)!,
        bitmapInfo: CGImageAlphaInfo.noneSkipLast.rawValue
      ) else { fatalError("Cannot load the shared web icon") }

// iOS supplies its own corner mask; fill the web icon's transparent corners
// with the same background as the web manifest.
context.setFillColor(CGColor(srgbRed: 23 / 255, green: 21 / 255, blue: 19 / 255, alpha: 1))
context.fill(CGRect(x: 0, y: 0, width: 1024, height: 1024))
context.interpolationQuality = .high
context.draw(image, in: CGRect(x: 0, y: 0, width: 1024, height: 1024))
guard let rendered = context.makeImage(),
      let destination = CGImageDestinationCreateWithURL(output as CFURL, UTType.png.identifier as CFString, 1, nil)
else { fatalError("Cannot create the iOS icon") }
CGImageDestinationAddImage(destination, rendered, nil)
guard CGImageDestinationFinalize(destination) else { fatalError("Cannot save the iOS icon") }
print("Updated \(output.path)")
