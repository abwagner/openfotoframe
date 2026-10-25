import SwiftUI

struct PhotoDetailView: View {
    @EnvironmentObject private var session: FrameSession
    @Environment(\.dismiss) private var dismiss
    let photo: FramePhoto
    @State private var title = ""
    @State private var enabled = true
    @State private var scale = 1.0
    @State private var overrideMat = false
    @State private var mat = Color.white
    @State private var finish = "default"
    @State private var border = "default"
    @State private var overrideBevel = false
    @State private var bevel = 4.0
    @State private var saving = false
    @State private var deleting = false
    @State private var web: WebDestination?

    var body: some View {
        Form {
            Section {
                ServerImage(path: "uploads/\(photo.filename)", fit: .fit)
                    .frame(height: 260).listRowInsets(EdgeInsets())
                    .background(overrideMat ? mat : Color(.secondarySystemBackground))
            }
            Section("Photo") {
                TextField("Title", text: $title).accessibilityIdentifier("photoTitle")
                Toggle("Visible on frame", isOn: $enabled).accessibilityIdentifier("photoVisibility")
                LabeledContent("Uploaded by", value: photo.uploadedBy ?? "Unknown")
                if let date = photo.uploadedAt { LabeledContent("Uploaded", value: String(date.prefix(10))) }
                if let width = photo.width, let height = photo.height { LabeledContent("Dimensions", value: "\(width) × \(height)") }
                LabeledContent("File size", value: ByteCountFormatter.string(fromByteCount: Int64(photo.size), countStyle: .file))
            }
            Section {
                VStack(alignment: .leading) {
                    Text("Scale: \(scale, specifier: "%.2f")×")
                    Slider(value: $scale, in: 0.1...3, step: 0.05)
                }
                Toggle("Custom mat color", isOn: $overrideMat)
                if overrideMat { ColorPicker("Mat color", selection: $mat, supportsOpacity: false) }
                Picker("Mat finish", selection: $finish) {
                    Text("Use Frame Default").tag("default")
                    ForEach(["eggshell", "flat", "linen", "suede", "silk"], id: \.self) { Text($0.capitalized).tag($0) }
                }
                Picker("Border effect", selection: $border) {
                    Text("Use Frame Default").tag("default")
                    Text("Bevel").tag("bevel")
                    Text("Shadow").tag("shadow")
                }
                Toggle("Custom border width", isOn: $overrideBevel)
                if overrideBevel { Stepper("Width: \(Int(bevel)) px", value: $bevel, in: 0...16) }
            } header: { Text("Display appearance") } footer: {
                Text("Save to update the frame. Use the full editor for an accurate mat preview, interactive crop, and matching heights across groups.")
            }
            Section {
                Button("Crop & Full Preview", systemImage: "crop.rotate") {
                    web = .init(title: "Photo Editor", path: "upload", focus: .photo(photo.filename))
                }
                Text("The full editor opens this photo. Changes there save directly to the frame.").font(.footnote).foregroundStyle(.secondary)
            }
            Section {
                Button("Delete Photo", role: .destructive) { deleting = true }
            }
        }
        .navigationTitle(photo.displayName).navigationBarTitleDisplayMode(.inline)
        .toolbar { Button("Save", action: save).disabled(saving) }
        .disabled(saving)
        .confirmationDialog("Permanently delete this photo?", isPresented: $deleting, titleVisibility: .visible) {
            Button("Delete Photo", role: .destructive) {
                Task {
                    await session.perform {
                        _ = try await session.mutate("api/gallery/\(photo.filename)", method: "DELETE")
                        await session.refresh()
                        dismiss()
                    }
                }
            }
        }
        .sheet(item: $web, onDismiss: { Task { await reload() } }) { destination in
            NavigationStack { FrameWebScreen(destination: destination) }
        }
        .onAppear { populate(photo) }
    }

    private func populate(_ photo: FramePhoto) {
        title = photo.title ?? ""
        enabled = photo.enabled
        scale = photo.scale ?? 1
        overrideMat = photo.matColor != nil
        mat = Color(frameHex: photo.matColor ?? "#ffffff")
        finish = photo.matFinish ?? "default"
        border = photo.borderEffect ?? "default"
        overrideBevel = photo.bevelWidth != nil
        bevel = photo.bevelWidth ?? 4
    }
    private func reload() async {
        await session.refresh()
        if let updated = session.gallery?.images.first(where: { $0.id == photo.id }) { populate(updated) }
    }
    private func save() {
        Task {
            saving = true
            defer { saving = false }
            await session.perform {
                let body: [String: Any] = [
                    "title": title, "enabled": enabled, "scale": scale,
                    "mat_color": overrideMat ? mat.frameHex as Any : NSNull(),
                    "mat_finish": finish == "default" ? NSNull() : finish as Any,
                    "border_effect": border == "default" ? NSNull() : border as Any,
                    "bevel_width": overrideBevel ? bevel as Any : NSNull()
                ]
                _ = try await session.mutate("api/gallery/\(photo.filename)", method: "PATCH", body: body)
                await session.refresh()
                dismiss()
            }
        }
    }
}
