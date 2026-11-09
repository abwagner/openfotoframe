import SwiftUI

struct RetainedPhoto: Decodable, Identifiable {
    struct Purge: Decodable { let id: String; let state: String }
    let id: String
    let filename: String
    let title: String?
    let byteSize: Int64
    let purge: Purge?
    var name: String { title.flatMap { $0.isEmpty ? nil : $0 } ?? filename }
    var canRestore: Bool { purge == nil }
    var canRequestRemoval: Bool { purge == nil || purge?.state == "failed" }
}

struct DeletedPhotosView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var photos: [RetainedPhoto] = []
    @State private var nextCursor: String?
    @State private var selected: RetainedPhoto?
    @State private var loading = false
    @State private var failure: String?
    @State private var message: String?
    var body: some View {
        List {
            if let scope = connection.scope, scope.administrator {
                Section(scope.collection.name) {
                    Text("Deleted photos still use storage. Permanent removal cannot be undone and is completed by the server’s background worker. Retained bytes count until removal succeeds.").font(.footnote)
                }
                if let message { Text(message).foregroundStyle(.secondary) }
                ForEach(photos) { photo in
                    Section(photo.name) {
                        Text(HouseholdStorage.bytes(photo.byteSize) + " retained")
                        if let purge = photo.purge {
                            Text("Permanent removal: " + purge.state.capitalized)
                            if purge.state == "failed" { Text("Removal failed. You can explicitly retry; storage has not been freed.").font(.footnote) }
                        }
                        if photo.canRestore { Button("Restore Photo") { Task { await restore(photo) } }.disabled(loading) }
                        if photo.canRequestRemoval { Button(photo.purge == nil ? "Permanently Remove Photo" : "Retry Permanent Removal", role: .destructive) { selected = photo }.disabled(loading) }
                    }
                }
                if photos.isEmpty && !loading { Text("No retained deleted photos in this collection.") }
                if nextCursor != nil { Button("Load More") { Task { await refresh(more: true) } }.disabled(loading) }
                if let failure { Text(failure).foregroundStyle(.secondary) }
                Button(loading ? "Refreshing…" : "Refresh Deleted Photos") { Task { await refresh() } }.disabled(loading)
            } else { Text("Only household owners and administrators can review retained deleted photos.") }
        }
        .navigationTitle("Deleted Photos")
        .task { await refresh() }
        .refreshable { await refresh() }
        .confirmationDialog("Permanently remove \(selected?.name ?? "this photo")?", isPresented: Binding(get: { selected != nil }, set: { if !$0 { selected = nil } }), titleVisibility: .visible) {
            if let selected { Button("Request Permanent Removal", role: .destructive) { Task { await purge(selected) } } }
        } message: { Text("This cannot be undone. Removal is queued on the server; retained bytes continue to count until its background worker successfully deletes the files.") }
    }
    private var prefix: String? {
        guard let scope = connection.scope, scope.administrator else { return nil }
        return "api/v1/households/\(scope.household.id)/collections/\(scope.collection.id)"
    }
    private func refresh(more: Bool = false) async {
        guard let prefix else { photos = []; return }
        loading = true; defer { loading = false }
        do {
            struct Page: Decodable { let assets: [RetainedPhoto]; let nextCursor: String? }
            var path = prefix + "/trash"
            if more, let nextCursor {
                var query = URLComponents(); query.queryItems = [URLQueryItem(name: "after", value: nextCursor)]
                path += "?" + (query.percentEncodedQuery ?? "")
            }
            let result: Page = try await connection.json(path)
            photos = more ? photos + result.assets : result.assets
            nextCursor = result.nextCursor; failure = nil
        } catch is CancellationError { }
        catch { photos = []; failure = error.localizedDescription }
    }
    private func restore(_ photo: RetainedPhoto) async {
        guard let prefix, photo.canRestore, !loading else { return }
        loading = true; defer { loading = false }
        do {
            let _: ReleaseAcknowledgement = try await connection.json(prefix + "/assets/\(photo.id)/restore", method: "POST", body: [:])
            message = "Photo restored without its former group."
            await refresh()
        } catch { failure = error.localizedDescription }
    }
    private func purge(_ photo: RetainedPhoto) async {
        guard let prefix, photo.canRequestRemoval, !loading else { return }
        loading = true; defer { loading = false }
        selected = nil
        do {
            let _: ReleaseAcknowledgement = try await connection.json(prefix + "/assets/\(photo.id)/purge", method: "POST", body: ["confirm_permanent_delete": true])
            message = "Permanent removal requested. Refresh after the server finishes to confirm storage was freed."
            await refresh()
        } catch { failure = error.localizedDescription }
    }
}
