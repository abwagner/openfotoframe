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
    var lighting = BevelLighting()
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
                            .padding(border == "bevel" || border == "bevel-lit" ? effect : 0)
                            .background(mat)
                            .overlay {
                                if border == "bevel" || border == "bevel-lit", effect > 0 {
                                    MatBevel(width: effect, mat: mat, lighting: border == "bevel-lit" ? lighting : nil)
                                }
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
        .accessibilityValue(accessibilityDescription)
        .accessibilityIdentifier("matPreview")
        .task(id: "\(filename)-\(retry)") {
            failed = false
            do { image = try await session.image("uploads/\(filename)") }
            catch { if !Task.isCancelled { failed = true } }
        }
    }

    private var accessibilityDescription: String {
        "\(mat.frameHex), \(finish), \(border), \(Int(bevel)) pixels, light \(lighting.intensity)/\(lighting.vertical)/\(lighting.horizontal)"
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

// Same corner lighting calculation as the web editor's getBevelColorsLit.
struct BevelLighting {
    var intensity = 50.0
    var vertical = 15.0
    var horizontal = 15.0

    struct Shade {
        let white: Bool
        let opacity: Double
        var color: Color { (white ? Color.white : Color.black).opacity(opacity) }
    }

    var corners: [Shade] {
        let v = (min(100, max(0, vertical)) - 50) / 50
        let h = (min(100, max(0, horizontal)) - 50) / 50
        let i = min(100, max(0, intensity)) / 100
        return [(v + h) / 2, (v - h) / 2, (h - v) / 2, -(v + h) / 2].map { factor in
            let alpha = factor > 0 ? factor * i * 0.55 : (1 - factor) / 2 * (0.04 + 0.20 * (1 - i))
            let rounded = (alpha * 1000).rounded() / 1000
            return Shade(white: factor > 0, opacity: rounded < 0.005 ? 0 : rounded)
        }
    }
}

struct MatBevel: View {
    let width: CGFloat
    let mat: Color
    let lighting: BevelLighting?
    var body: some View {
        Canvas { context, size in
            let w = min(width, min(size.width, size.height) / 2)
            let outer = [CGPoint.zero, CGPoint(x: size.width, y: 0),
                         CGPoint(x: size.width, y: size.height), CGPoint(x: 0, y: size.height)]
            let inner = [CGPoint(x: w, y: w), CGPoint(x: size.width - w, y: w),
                         CGPoint(x: size.width - w, y: size.height - w), CGPoint(x: w, y: size.height - w)]
            let corners = lighting?.corners
            let hex = mat.frameHex.dropFirst()
            let rgb = UInt32(hex, radix: 16) ?? 0xffffff
            let luminance = (0.299 * Double((rgb >> 16) & 255) + 0.587 * Double((rgb >> 8) & 255) + 0.114 * Double(rgb & 255)) / 255
            let innerAlpha = luminance < 0.5 ? 0.30 : 0.20
            // Each trapezoid fades across its width; lit faces also blend along
            // their length, meeting at the same four corner colors as the web.
            let indices = [0, 1, 3, 2] // clockwise geometry -> TL, TR, BR, BL
            for edge in 0..<4 {
                let next = (edge + 1) % 4
                var path = Path()
                path.addLines([outer[edge], outer[next], inner[next], inner[edge]])
                path.closeSubpath()
                let start = CGPoint(x: (outer[edge].x + outer[next].x) / 2, y: (outer[edge].y + outer[next].y) / 2)
                let end = CGPoint(x: (inner[edge].x + inner[next].x) / 2, y: (inner[edge].y + inner[next].y) / 2)
                if let corners {
                    context.fill(path, with: .color(.black.opacity(0.08)))
                    context.drawLayer { layer in
                        layer.clip(to: path)
                        layer.fill(path, with: .linearGradient(Gradient(colors: [corners[indices[edge]].color, corners[indices[next]].color]), startPoint: outer[edge], endPoint: outer[next]))
                        layer.blendMode = .destinationIn
                        layer.fill(path, with: .linearGradient(Gradient(colors: [.black, .clear]), startPoint: start, endPoint: end))
                    }
                } else {
                    context.fill(path, with: .linearGradient(Gradient(colors: [.clear, .black.opacity(innerAlpha)]), startPoint: start, endPoint: end))
                }
            }
            context.stroke(Path(CGRect(x: w, y: w, width: size.width - 2 * w, height: size.height - 2 * w)), with: .color(.black.opacity(lighting == nil ? 0.08 : 0.16)), lineWidth: 0.5)
            if lighting != nil {
                var seams = Path()
                for corner in 0..<4 { seams.move(to: outer[corner]); seams.addLine(to: inner[corner]) }
                context.stroke(seams, with: .color(.black.opacity(0.12)), lineWidth: 0.5)
            }
        }.allowsHitTesting(false)
    }
}
