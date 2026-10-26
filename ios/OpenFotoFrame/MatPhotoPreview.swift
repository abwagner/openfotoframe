import SwiftUI

enum MatPreviewGeometry {
    static func photoSize(in frame: CGSize, image: CGSize, scale: Double, effect: CGFloat, shadow: Bool) -> CGSize {
        guard image.width > 0, image.height > 0 else { return .zero }
        let inset = max(0, effect) * (shadow ? 4 : 2)
        let maxWidth = min(frame.width * 0.95, frame.width - inset)
        let maxHeight = min(frame.height * 0.95, frame.height - inset)
        let width = max(1, min((frame.width * 0.9 - inset) * scale, maxWidth))
        let height = max(1, min((frame.height * 0.9 - inset) * scale, maxHeight))
        let ratio = image.width / image.height
        return ratio > width / height ? CGSize(width: width, height: width / ratio) : CGSize(width: height * ratio, height: height)
    }

    static func aspectRatio(_ value: String) -> CGFloat {
        let parts = value.split(separator: ":").compactMap { Double($0) }
        guard parts.count == 2, parts[0] > 0, parts[1] > 0 else { return 16 / 9 }
        return parts[0] / parts[1]
    }
}

struct MatPhotoPreview: View {
    @EnvironmentObject private var session: FrameSession
    let filename: String
    let crop: FrameCrop?
    let mat: Color
    let finish: String
    let border: String
    let bevel: Double
    let scale: Double
    let fitMode: String
    @State private var image: UIImage?
    @State private var failed = false
    @State private var retry = 0

    var body: some View {
        GeometryReader { geometry in
            ZStack {
                mat
                MatTexture(finish: finish)
                if let image {
                    if fitMode == "cover" {
                        Image(uiImage: image).resizable().scaledToFill()
                            .frame(width: geometry.size.width, height: geometry.size.height).clipped()
                    } else {
                        let cropped = croppedImage(image)
                        let effect = bevel > 0 ? max(0.5, bevel * geometry.size.width / 1920) : 0
                        let size = MatPreviewGeometry.photoSize(in: geometry.size, image: cropped.size, scale: scale, effect: effect, shadow: border == "shadow")
                        Image(uiImage: cropped).resizable().interpolation(.high)
                            .frame(width: size.width, height: size.height)
                            .padding(border == "bevel" ? effect : 0)
                            .background(mat)
                            .overlay {
                                if border == "bevel", effect > 0 { MatBevel(width: effect) }
                            }
                            .shadow(color: border == "shadow" ? .black.opacity(0.35) : .clear,
                                    radius: effect, y: effect * 0.5)
                    }
                } else if failed {
                    Button("Reload Photo", systemImage: "arrow.clockwise") { retry += 1 }
                        .buttonStyle(.borderedProminent)
                } else { ProgressView("Loading photo…").padding(8).background(.regularMaterial, in: Capsule()) }
            }
            .frame(width: geometry.size.width, height: geometry.size.height).clipped()
        }
        .accessibilityElement(children: image == nil ? .contain : .ignore)
        .accessibilityLabel("Photo with mat")
        .accessibilityValue("\(mat.frameHex), \(finish), \(border), \(Int(bevel)) pixels")
        .accessibilityIdentifier("matPreview")
        .task(id: "\(filename)-\(retry)") {
            failed = false
            do { image = try await session.image("uploads/\(filename)") }
            catch { if !Task.isCancelled { failed = true } }
        }
    }

    private func croppedImage(_ image: UIImage) -> UIImage {
        guard let crop, crop.w > 0, crop.h > 0, let source = image.cgImage else { return image }
        let bounds = CGRect(x: 0, y: 0, width: source.width, height: source.height)
        let rect = CGRect(x: crop.x * Double(source.width), y: crop.y * Double(source.height),
                          width: crop.w * Double(source.width), height: crop.h * Double(source.height)).intersection(bounds)
        guard !rect.isEmpty, let cropped = source.cropping(to: rect) else { return image }
        return UIImage(cgImage: cropped)
    }
}

private struct MatTexture: View {
    let finish: String
    var body: some View {
        Canvas { context, size in
            guard finish != "flat" else { return }
            let opacity = finish == "linen" ? 0.08 : finish == "eggshell" ? 0.05 : 0.06
            let ink = Color(white: 136 / 255.0).opacity(opacity)
            let step: CGFloat = finish == "linen" || finish == "eggshell" ? 4 : finish == "silk" ? 10 : 6
            var pattern = Path()
            for y in stride(from: 0, to: size.height, by: step) {
                for x in stride(from: 0, to: size.width, by: step) {
                    if finish == "linen" {
                        pattern.move(to: CGPoint(x: x, y: y)); pattern.addLine(to: CGPoint(x: x + step, y: y))
                        pattern.move(to: CGPoint(x: x, y: y)); pattern.addLine(to: CGPoint(x: x, y: y + step))
                    } else if finish == "silk" {
                        pattern.move(to: CGPoint(x: x, y: y + step)); pattern.addLine(to: CGPoint(x: x + step, y: y))
                    } else {
                        let dots: [(CGFloat, CGFloat, CGFloat)] = finish == "eggshell"
                            ? [(1, 1, 0.3), (3, 3, 0.25), (3, 0.5, 0.2), (0.5, 2.5, 0.15)]
                            : [(1, 1, 0.4), (4, 3, 0.35), (2, 5, 0.3), (5, 1, 0.25)]
                        for (dx, dy, radius) in dots {
                            pattern.addEllipse(in: CGRect(x: x + dx - radius, y: y + dy - radius, width: radius * 2, height: radius * 2))
                        }
                    }
                }
            }
            if finish == "linen" || finish == "silk" { context.stroke(pattern, with: .color(ink), lineWidth: 0.3) }
            else { context.fill(pattern, with: .color(ink)) }
        }.allowsHitTesting(false)
    }
}

private struct MatBevel: View {
    let width: CGFloat
    var body: some View {
        Rectangle().strokeBorder(.black.opacity(0.15), lineWidth: width)
            .overlay(Rectangle().strokeBorder(.black.opacity(0.08), lineWidth: 0.5).padding(width))
            .allowsHitTesting(false)
    }
}
