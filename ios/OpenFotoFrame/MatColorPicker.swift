import SwiftUI

struct MatPalette: Decodable {
    struct Preset: Decodable, Identifiable {
        var id: String { color }
        let color: String
        let title: String
    }
    let neutrals: [Preset]
    let accents: [Preset]
    static let shared: MatPalette = {
        guard let url = Bundle.main.url(forResource: "mat-colors", withExtension: "json"),
              let data = try? Data(contentsOf: url),
              let palette = try? JSONDecoder().decode(MatPalette.self, from: data) else {
            preconditionFailure("The shared mat color palette must be bundled with the app.")
        }
        return palette
    }()
}

struct MatColorPicker: View {
    @Binding var color: Color
    var body: some View {
        VStack(alignment: .leading, spacing: 16) {
            swatches(MatPalette.shared.neutrals)
            DisclosureGroup("More Colors") { swatches(MatPalette.shared.accents).padding(.top, 12) }
            ColorPicker("Custom Color", selection: $color, supportsOpacity: false)
            Text(color.frameHex.uppercased()).font(.caption.monospaced()).foregroundStyle(.secondary)
                .accessibilityIdentifier("matColorHex")
        }.padding(.vertical, 4)
    }
    private func swatches(_ presets: [MatPalette.Preset]) -> some View {
        LazyVGrid(columns: Array(repeating: GridItem(.flexible(), spacing: 8), count: 5), spacing: 12) {
            ForEach(presets) { preset in
                Button {
                    color = Color(frameHex: preset.color)
                } label: {
                    VStack(spacing: 6) {
                        Circle().fill(Color(frameHex: preset.color))
                            .frame(width: 32, height: 32)
                            .overlay(Circle().strokeBorder(.gray.opacity(0.4), lineWidth: 1))
                            .padding(4)
                            .overlay(Circle().strokeBorder(color.frameHex == preset.color ? Color.accentColor : .clear, lineWidth: 2))
                        Text(preset.title).font(.caption2).lineLimit(2)
                            .multilineTextAlignment(.center).frame(minHeight: 24, alignment: .top)
                    }.frame(maxWidth: .infinity)
                }.buttonStyle(.plain)
                    .accessibilityLabel(preset.title)
                    .accessibilityValue(preset.color)
                    .accessibilityAddTraits(color.frameHex == preset.color ? .isSelected : [])
                    .accessibilityIdentifier("matPreset-\(preset.title)")
            }
        }
    }
}
