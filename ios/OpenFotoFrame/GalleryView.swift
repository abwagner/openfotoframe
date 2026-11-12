import SwiftUI

struct GalleryView: View {
    @EnvironmentObject private var session: FrameSession
    @State private var filter = "All"
    @State private var query = ""
    @State private var selecting = false
    @State private var selected: Set<String> = []
    @State private var deleting = false
    @State private var busy = false
    @State private var web: WebDestination?
    @State private var showDisplayed = true
    @State private var displayed: DisplayGallery?
    @State private var displayError: String?
    @State private var displayRequest = UUID()
    @State private var displayProfiles: [GalleryDisplayProfile] = []
    @State private var selectedDisplayId = ""

    private var displayedSlides: [GallerySlide] {
        (displayed?.slides ?? []).filter { slide in
            query.isEmpty || slide.filenames.contains { name in
                (session.gallery?.images.first { $0.filename == name }?.displayName ?? name)
                    .localizedCaseInsensitiveContains(query)
            }
        }
    }

    private var photos: [FramePhoto] {
        (session.gallery?.images ?? []).filter {
            (filter == "All" || (filter == "Visible" ? $0.enabled : !$0.enabled)) &&
            (query.isEmpty || $0.displayName.localizedCaseInsensitiveContains(query))
        }.sorted { ($0.uploadedAt ?? $0.filename) > ($1.uploadedAt ?? $1.filename) }
    }

    var body: some View {
        ScrollView {
            VStack(alignment: .leading, spacing: 20) {
                HStack {
                    let all = session.gallery?.images ?? []
                    Label("\(all.count) photos", systemImage: "photo.stack")
                    Spacer()
                    Text("\(all.filter(\.enabled).count) on display")
                }.font(.subheadline).foregroundStyle(.secondary)
                Picker("Gallery source", selection: $showDisplayed) {
                    Text("Displayed slides").tag(true)
                    Text("Original photos").tag(false)
                }.pickerStyle(.segmented).disabled(selecting)
                if showDisplayed {
                    if session.release == nil && displayProfiles.count > 1 {
                        Picker("Preview display", selection: $selectedDisplayId) {
                            ForEach(displayProfiles, id: \.id) { profile in Text(profile.name).tag(profile.id) }
                        }
                    }
                    if let displayed {
                        Text("\(displayed.slides.count) slides · \(displayed.profile.name)")
                            .font(.subheadline).foregroundStyle(.secondary)
                        Text("Includes automatic pairs, crops, and mats. Switch to originals to manage individual photos.")
                            .font(.caption).foregroundStyle(.secondary)
                    }
                    if let displayError {
                        Text(displayError).font(.subheadline).foregroundStyle(.secondary)
                        Button("Reload Display Previews") { Task { await loadDisplaySlides() } }
                    } else if displayed == nil {
                        ProgressView("Loading display previews…").frame(maxWidth: .infinity).padding(40)
                    } else if displayedSlides.isEmpty {
                        ContentUnavailableView("No displayed slides", systemImage: "photo.on.rectangle.angled",
                                               description: Text("Check the selected frame, photo visibility, or search."))
                    } else {
                        LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 14)], spacing: 18) {
                            ForEach(displayedSlides) { slide in
                                VStack(alignment: .leading, spacing: 8) {
                                    if let path = slide.localThumbnailPath {
                                        ServerImage(path: path, fit: .fit)
                                            .aspectRatio(displayed?.profile.aspectRatio ?? (16.0 / 9.0), contentMode: .fit)
                                            .background(.quaternary).clipShape(RoundedRectangle(cornerRadius: 12))
                                    }
                                    Text(slide.label).font(.subheadline.bold())
                                    ForEach(slide.filenames, id: \.self) { name in
                                        if let photo = session.gallery?.images.first(where: { $0.filename == name }) {
                                            NavigationLink { PhotoDetailView(photo: photo) } label: {
                                                Text(photo.displayName).font(.caption).lineLimit(1)
                                            }
                                        }
                                    }
                                    if let id = slide.groupId {
                                        Button("Edit Group") { web = .init(title: "Group Editor", path: "upload", focus: .group(id)) }
                                            .font(.caption)
                                    }
                                }.accessibilityIdentifier("gallerySlide-\(slide.id)")
                            }
                        }
                    }
                } else {
                Picker("Visibility", selection: $filter) {
                    ForEach(["All", "Visible", "Hidden"], id: \.self) { Text($0) }
                }.pickerStyle(.segmented)
                if session.loading && session.gallery == nil {
                    ProgressView().frame(maxWidth: .infinity).padding(60)
                } else if photos.isEmpty {
                    ContentUnavailableView(query.isEmpty ? "No photos here yet" : "No matching photos",
                                           systemImage: "photo.on.rectangle.angled",
                                           description: Text("Add photos from the Upload tab, or change the filter."))
                } else {
                    LazyVGrid(columns: [GridItem(.adaptive(minimum: 150), spacing: 14)], spacing: 18) {
                        ForEach(photos) { photo in
                            if selecting {
                                Button { toggle(photo.filename) } label: { card(photo) }.buttonStyle(.plain)
                                    .accessibilityIdentifier("galleryPhoto-\(photo.filename)")
                            } else {
                                NavigationLink { PhotoDetailView(photo: photo) } label: { card(photo) }.buttonStyle(.plain)
                                    .accessibilityIdentifier("galleryPhoto-\(photo.filename)")
                            }
                        }
                    }
                }
                if let groups = session.gallery?.groups, !groups.isEmpty {
                    Text("Photo Groups").font(.title2.bold()).padding(.top, 6)
                    ForEach(groups.keys.sorted(), id: \.self) { id in
                        if let group = groups[id] {
                            HStack {
                                Image(systemName: "rectangle.split.3x1").foregroundStyle(.teal)
                                VStack(alignment: .leading) {
                                    Text("\(group.images.count) photos together").font(.headline)
                                    Text(group.images.compactMap { name in session.gallery?.images.first { $0.filename == name }?.displayName }.joined(separator: ", "))
                                        .font(.caption).foregroundStyle(.secondary).lineLimit(1)
                                }
                                Spacer()
                                Menu {
                                    Button("Edit Group in Web Editor") { web = .init(title: "Group Editor", path: "upload", focus: .group(id)) }
                                    Button("Ungroup Photos") {
                                        Task { await run { _ = try await session.mutate("api/groups/\(id)", method: "DELETE") } }
                                    }.disabled(!session.canManageCollection)
                                } label: { Image(systemName: "ellipsis.circle").padding(8) }
                            }.padding().background(.quaternary, in: RoundedRectangle(cornerRadius: 14))
                        }
                    }
                }
                }
            }.padding()
        }
        .navigationTitle("Your Photos")
        .searchable(text: $query, prompt: "Find a photo")
        .refreshable { await session.refresh() }
        .toolbar {
            ToolbarItem(placement: .topBarLeading) {
                Menu {
                    NavigationLink("Reorder Slideshow") { ReorderView() }.disabled(!session.canManageCollection)
                    Button("Open Full Gallery Editor") { web = .init(title: "Gallery Editor", path: "upload") }
                } label: { Image(systemName: "ellipsis.circle") }
            }
            ToolbarItem(placement: .topBarTrailing) {
                Button(selecting ? "Done" : "Select") {
                    selecting.toggle()
                    if selecting { showDisplayed = false }
                    selected.removeAll()
                }.disabled(!session.canManageCollection)
            }
        }
        .safeAreaInset(edge: .bottom) {
            if selecting {
                VStack(spacing: 12) {
                    HStack {
                        Text("\(selected.count) selected").font(.subheadline)
                        Spacer()
                        Button("Select All") { selected = Set(photos.map(\.filename)) }
                    }
                    HStack(spacing: 24) {
                        Button("Show", systemImage: "eye") { bulk("enable") }
                        Button("Hide", systemImage: "eye.slash") { bulk("disable") }
                        Button("Group", systemImage: "rectangle.split.3x1") {
                            Task { await run { _ = try await session.mutate("api/groups", body: ["images": selected.sorted()]) } }
                        }.disabled(selected.count < 2)
                        Spacer(minLength: 0)
                        Button(role: .destructive) { deleting = true } label: { Image(systemName: "trash") }.accessibilityLabel("Delete selected photos")
                    }.disabled(selected.isEmpty || busy)
                }.padding().background(.regularMaterial)
            }
        }
        .confirmationDialog(session.release == nil ? "Permanently delete \(selected.count) photos?" : "Remove \(selected.count) photos from this collection?", isPresented: $deleting, titleVisibility: .visible) {
            Button("Delete Photos", role: .destructive) { bulk("delete") }
        }
        .sheet(item: $web, onDismiss: { Task { await session.refresh() } }) { destination in
            NavigationStack { FrameWebScreen(destination: destination) }
        }
        .task { await session.refresh() }
        .onReceive(session.$gallery) { gallery in
            displayed = nil
            displayRequest = UUID()
            if gallery != nil { Task { await loadDisplaySlides() } }
            else { displayProfiles = []; selectedDisplayId = "" }
        }
        .onChange(of: selectedDisplayId) { _, value in
            if !value.isEmpty { Task { await loadDisplaySlides() } }
        }
    }

    @MainActor
    private func loadDisplaySlides() async {
        let request = UUID()
        displayRequest = request
        displayError = nil
        displayed = nil
        do {
            if session.release == nil {
                let profiles = try await session.get("api/displays", as: GalleryDisplayProfiles.self)
                guard displayRequest == request else { return }
                displayProfiles = profiles.displays
                if !selectedDisplayId.isEmpty && !profiles.displays.contains(where: { $0.id == selectedDisplayId }) {
                    selectedDisplayId = ""
                }
            }
            let suffix = session.release == nil && !selectedDisplayId.isEmpty ? "/\(selectedDisplayId)" : ""
            let value = try await session.get("api/gallery/display-slides\(suffix)", as: DisplayGallery.self)
            guard displayRequest == request else { return }
            displayed = value
            if session.release == nil { selectedDisplayId = value.profile.id }
        } catch {
            guard displayRequest == request else { return }
            displayed = nil
            displayError = "Display previews could not be loaded. You can still manage original photos. \(error.localizedDescription)"
        }
    }

    private func card(_ photo: FramePhoto) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            ServerImage(path: "thumbnails/\(photo.filename)")
                .aspectRatio(1, contentMode: .fit)
                .background(.quaternary).clipShape(RoundedRectangle(cornerRadius: 16))
                .overlay(alignment: .topTrailing) {
                    Image(systemName: selecting ? (selected.contains(photo.filename) ? "checkmark.circle.fill" : "circle") : (photo.enabled ? "eye.fill" : "eye.slash.fill"))
                        .foregroundStyle(.white).padding(8).background(.black.opacity(0.55), in: Capsule()).padding(8)
                }
            Text(photo.displayName).font(.subheadline.weight(.medium)).lineLimit(1)
            Text(photo.uploadedBy.map { "By \($0)" } ?? "Photo").font(.caption).foregroundStyle(.secondary)
        }.accessibilityElement(children: .combine)
    }
    private func toggle(_ name: String) {
        if selected.contains(name) { selected.remove(name) } else { selected.insert(name) }
    }
    private func bulk(_ action: String) {
        Task { await run { _ = try await session.mutate("api/gallery/bulk", body: ["action": action, "filenames": selected.sorted()]) } }
    }
    private func run(_ action: () async throws -> Void) async {
        busy = true
        defer { busy = false }
        await session.perform {
            try await action()
            selected.removeAll()
            await session.refresh()
        }
    }
}

struct ReorderView: View {
    @EnvironmentObject private var session: FrameSession
    @State private var photos: [FramePhoto] = []
    @State private var saving = false
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        List {
            Section {
                ForEach(photos) { photo in
                    HStack {
                        ServerImage(path: "thumbnails/\(photo.filename)").frame(width: 48, height: 48).clipShape(RoundedRectangle(cornerRadius: 8))
                        Text(photo.displayName)
                    }
                }.onMove { photos.move(fromOffsets: $0, toOffset: $1) }
            } footer: { Text("Drag photos into slideshow order. Groups appear before single photos; turn off Shuffle to follow this order.") }
        }
        .environment(\.editMode, .constant(.active))
        .navigationTitle("Slideshow Order")
        .toolbar {
            Button("Save") {
                Task {
                    saving = true
                    defer { saving = false }
                    await session.perform {
                        _ = try await session.mutate("api/reorder", body: ["images": photos.map(\.filename)])
                        dismiss()
                    }
                }
            }.disabled(saving)
        }
        .task {
            await session.perform {
                let settings = try await session.get("api/settings", as: FrameSettings.self)
                let all = session.gallery?.images ?? []
                let ranks = Dictionary(settings.imageOrder.enumerated().map { ($1, $0) }, uniquingKeysWith: { first, _ in first })
                photos = all.sorted {
                    let left = ranks[$0.filename] ?? Int.max, right = ranks[$1.filename] ?? Int.max
                    return left == right ? $0.filename < $1.filename : left < right
                }
            }
        }
    }
}
