import SwiftUI
import PhotosUI
import UniformTypeIdentifiers

struct UploadView: View {
    @EnvironmentObject private var session: FrameSession
    @State private var pickerItems: [PhotosPickerItem] = []
    @State private var files: [UploadFile] = []
    @State private var checks: [String: DuplicateCheck.Result] = [:]
    @State private var busy = false
    @State private var importing = false
    @State private var status = ""
    @State private var reviewed = false
    @State private var web: WebDestination?

    var body: some View {
        List {
            Section {
                VStack(alignment: .leading, spacing: 12) {
                    Image(systemName: "photo.badge.plus").font(.system(size: 42)).foregroundStyle(.teal)
                    Text("Make room for new memories.").font(.title2.bold())
                    Text("Choose photos, review duplicate and resolution warnings, then send them to your frame.")
                        .foregroundStyle(.secondary)
                }.padding(.vertical, 16)
                PhotosPicker(selection: $pickerItems, maxSelectionCount: 10, matching: .images) {
                    Label("Choose from Photo Library", systemImage: "photo.on.rectangle")
                }
                Button("Choose from Files", systemImage: "folder") { importing = true }
            }
            if busy { Section { HStack { ProgressView(); Text(status.isEmpty ? "Preparing photos…" : status) } } }
            else if !status.isEmpty { Section { Text(status).foregroundStyle(.secondary) } }
            if !files.isEmpty {
                Section {
                    ForEach(files) { file in
                        VStack(alignment: .leading, spacing: 8) {
                            HStack {
                                if let image = UIImage(data: file.data) {
                                    Image(uiImage: image).resizable().scaledToFill().frame(width: 54, height: 54).clipped().clipShape(RoundedRectangle(cornerRadius: 8))
                                }
                                VStack(alignment: .leading) {
                                    Text(file.name).lineLimit(1)
                                    Text(ByteCountFormatter.string(fromByteCount: Int64(file.data.count), countStyle: .file)).font(.caption).foregroundStyle(.secondary)
                                }
                                Spacer()
                                Button(role: .destructive) {
                                    files.removeAll { $0.id == file.id }
                                    checks.removeValue(forKey: file.name)
                                } label: { Image(systemName: "xmark.circle") }.buttonStyle(.borderless).accessibilityLabel("Remove \(file.name)")
                            }
                            if let check = checks[file.name] {
                                if check.lowResolution {
                                    Label("Low resolution: \(check.width) × \(check.height)", systemImage: "exclamationmark.triangle")
                                        .font(.caption).foregroundStyle(.orange)
                                }
                                ForEach(check.matches.prefix(3), id: \.filename) { match in
                                    HStack {
                                        ServerImage(path: "thumbnails/\(match.filename)").frame(width: 42, height: 42).clipShape(RoundedRectangle(cornerRadius: 6))
                                        Text("Similar to \(session.gallery?.images.first { $0.filename == match.filename }?.displayName ?? match.filename)")
                                            .font(.caption).foregroundStyle(.orange)
                                    }
                                }
                                if check.matches.isEmpty && !check.lowResolution {
                                    Label("Ready to upload", systemImage: "checkmark.circle").font(.caption).foregroundStyle(.teal)
                                }
                            }
                        }.padding(.vertical, 4)
                    }
                } header: { Text("\(files.count) photos to upload") } footer: {
                    Text("Each photo must be under 49 MB. HEIC photos are converted to JPEG for the frame. Existing supported formats are preserved.")
                }
                Section {
                    Button(reviewed ? "Upload \(files.count) Photos" : "Check for Duplicates") {
                        Task { if reviewed { await upload() } else { await check() } }
                    }.font(.headline)
                    if reviewed { Text("You can upload similar or low-resolution photos if you want to keep them.").font(.footnote).foregroundStyle(.secondary) }
                }
            }
            Section {
                Button("Open Web Upload", systemImage: "globe") { web = .init(title: "Web Upload", path: "upload") }
            }
        }
        .navigationTitle("Upload Photos")
        .disabled(busy)
        .onChange(of: pickerItems) { _, items in Task { await prepare(items) } }
        .fileImporter(isPresented: $importing, allowedContentTypes: [.image], allowsMultipleSelection: true) { result in
            Task { await importFiles(result) }
        }
        .sheet(item: $web, onDismiss: { Task { await session.refresh() } }) { destination in
            NavigationStack { FrameWebScreen(destination: destination) }
        }
    }

    private func reset() { files = []; checks = [:]; reviewed = false; status = "" }

    private func prepare(_ items: [PhotosPickerItem]) async {
        guard !items.isEmpty else { return }
        reset()
        busy = true
        defer { busy = false; pickerItems = [] }
        await session.perform {
            for (index, item) in items.enumerated() {
                status = "Preparing photo \(index + 1) of \(items.count)…"
                guard let data = try await item.loadTransferable(type: Data.self) else { throw FrameError.message("A selected photo could not be read. Download it from iCloud and try again.") }
                try append(data, name: "Photo-\(index + 1)-\(UUID().uuidString.prefix(6))")
            }
            status = "Review \(files.count) photos before uploading."
        }
    }

    private func importFiles(_ result: Result<[URL], Error>) async {
        busy = true
        defer { busy = false }
        await session.perform {
            let urls = try result.get()
            guard urls.count <= 10 else { throw FrameError.message("Choose up to 10 photos at a time.") }
            reset()
            for (index, url) in urls.enumerated() {
                status = "Reading photo \(index + 1) of \(urls.count)…"
                let access = url.startAccessingSecurityScopedResource()
                defer { if access { url.stopAccessingSecurityScopedResource() } }
                let size = try url.resourceValues(forKeys: [.fileSizeKey]).fileSize ?? 0
                guard size < 49 * 1024 * 1024 else { throw FrameError.message("\(url.lastPathComponent) exceeds the 49 MB upload limit.") }
                let data = try Data(contentsOf: url)
                try append(data, name: "\(index + 1)-\(url.deletingPathExtension().lastPathComponent)")
            }
            status = "Review \(files.count) photos before uploading."
        }
    }

    private func append(_ data: Data, name: String) throws {
        let file = try PhotoImport.file(data, name: name)
        guard files.reduce(0, { $0 + $1.data.count }) + file.data.count < 100 * 1024 * 1024 else {
            throw FrameError.message("Choose a smaller batch; staged photos must total less than 100 MB.")
        }
        files.append(file)
    }

    private func check() async {
        busy = true
        defer { busy = false }
        await session.perform {
            for (index, file) in files.enumerated() {
                status = "Checking photo \(index + 1) of \(files.count)…"
                let result = try FrameJSON.decode(DuplicateCheck.self, from: await session.upload(file, checkOnly: true))
                // Multipart uses sanitized names; generated names are sanitized below too.
                checks[file.name] = result.results[file.name]
            }
            reviewed = true
            status = "Checks complete. Review any warnings below."
        }
    }

    private func upload() async {
        busy = true
        defer { busy = false }
        let pending = files
        var completed = 0
        await session.perform {
            for (index, file) in pending.enumerated() {
                status = "Uploading photo \(index + 1) of \(pending.count)…"
                let result = try FrameJSON.decode(UploadResult.self, from: await session.upload(file, checkOnly: false))
                guard !result.uploaded.isEmpty else { throw FrameError.message(result.errors.joined(separator: "\n")) }
                files.removeAll { $0.id == file.id }
                completed += result.uploaded.count
            }
        }
        status = files.isEmpty ? "\(completed) photos uploaded to your frame." : "\(completed) uploaded; \(files.count) remain. Retry when you’re ready."
        await session.refresh()
    }
}

enum PhotoImport {
    static func file(_ data: Data, name: String) throws -> UploadFile {
        guard !data.isEmpty, data.count < 49 * 1024 * 1024 else { throw FrameError.message("Photos must be under 49 MB.") }
        let prefix = Array(data.prefix(12))
        let ext: String
        let mime: String
        var bytes = data
        if prefix.starts(with: [0xff, 0xd8, 0xff]) { ext = "jpg"; mime = "image/jpeg" }
        else if prefix.starts(with: [0x89, 0x50, 0x4e, 0x47]) { ext = "png"; mime = "image/png" }
        else if prefix.starts(with: Array("GIF8".utf8)) { ext = "gif"; mime = "image/gif" }
        else if prefix.starts(with: Array("RIFF".utf8)) && Array(prefix.suffix(4)) == Array("WEBP".utf8) { ext = "webp"; mime = "image/webp" }
        else {
            guard let image = UIImage(data: data), let jpeg = image.jpegData(compressionQuality: 0.92) else {
                throw FrameError.message("This file is not a supported image.")
            }
            ext = "jpg"; mime = "image/jpeg"; bytes = jpeg
        }
        guard bytes.count < 49 * 1024 * 1024 else { throw FrameError.message("The converted photo exceeds 49 MB.") }
        let safe = name.replacingOccurrences(of: "[^A-Za-z0-9._-]", with: "_", options: .regularExpression)
        return UploadFile(name: "\(safe).\(ext)", data: bytes, mimeType: mime)
    }
}
