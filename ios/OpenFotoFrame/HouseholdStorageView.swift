import SwiftUI

struct HouseholdStorage: Decodable {
    struct Usage: Decodable {
        let activeBytes: Int64
        let retainedDeletedBytes: Int64
        let totalBytes: Int64
        let activeAssets: Int
        let frames: Int
    }
    struct Entitlement: Decodable { let active: Bool; let status: String; let planId: String?; let validUntil: Double? }
    struct Limits: Decodable { let storageBytes: Int64; let assetLimit: Int; let frameLimit: Int }
    struct Remaining: Decodable { let storageBytes: Int64; let assets: Int; let frames: Int }
    let householdId: String
    let deploymentMode: String
    let accounting: String
    let usage: Usage
    let entitlement: Entitlement
    let limits: Limits?
    let remaining: Remaining?
    var full: Bool { limits.map { usage.totalBytes >= $0.storageBytes } ?? false }
    var uploadStatus: String {
        if deploymentMode == "hosted" && !entitlement.active { return "Uploads paused: the hosted subscription is inactive." }
        if full { return "Household storage is full." }
        if let limits, usage.activeAssets >= limits.assetLimit { return "The household’s active photo limit has been reached." }
        return deploymentMode == "self_hosted" ? "Storage is managed by your server." : "Hosted subscription active."
    }
    static func bytes(_ value: Int64) -> String { ByteCountFormatter.string(fromByteCount: max(0, value), countStyle: .file) }
}

struct HouseholdStorageView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var storage: HouseholdStorage?
    @State private var failure: String?
    @State private var loading = false
    var body: some View {
        List {
            if let scope = connection.scope, scope.administrator {
                Section(scope.household.name) {
                    Text("Household totals include every collection. They are available only to owners and administrators.").font(.footnote).foregroundStyle(.secondary)
                    if let storage {
                        Text(storage.uploadStatus).font(.headline)
                        if storage.full { Text("Review retained deleted photos with your household administrator. Removing a photo from a collection alone does not free its retained storage.") }
                        if storage.deploymentMode == "hosted" && !storage.entitlement.active {
                            Text(scope.household.role == "owner" ? "Review your existing subscription before trying another upload. No purchase or upgrade happens automatically." : "Ask the household owner to review the hosted subscription.")
                        }
                    }
                }
                if let storage {
                    Section("Included storage") {
                        if let limit = storage.limits {
                            LabeledContent("Used", value: HouseholdStorage.bytes(storage.usage.totalBytes) + " of " + HouseholdStorage.bytes(limit.storageBytes))
                            ProgressView(value: min(1, max(0, Double(storage.usage.totalBytes) / Double(max(1, limit.storageBytes)))))
                            if let remaining = storage.remaining { LabeledContent("Available", value: HouseholdStorage.bytes(remaining.storageBytes)) }
                            LabeledContent("Active photos", value: "\(storage.usage.activeAssets) of \(limit.assetLimit)")
                            LabeledContent("Frames", value: "\(storage.usage.frames) of \(limit.frameLimit)")
                        } else {
                            LabeledContent("Used", value: HouseholdStorage.bytes(storage.usage.totalBytes))
                            LabeledContent("Active photos", value: "\(storage.usage.activeAssets)")
                            LabeledContent("Frames", value: "\(storage.usage.frames)")
                            Text(storage.deploymentMode == "self_hosted" ? "No application storage limit is configured. Available disk capacity is not reported here." : "This server has not supplied plan limits. Ask the household owner or service operator to review its configuration.").font(.footnote)
                        }
                    }
                    Section("What counts") {
                        LabeledContent("Current photo files", value: HouseholdStorage.bytes(storage.usage.activeBytes))
                        LabeledContent("Retained deleted files", value: HouseholdStorage.bytes(storage.usage.retainedDeletedBytes)).accessibilityIdentifier("storageRetainedBytes")
                        Text("Deleted files continue to count until permanently removed. Storage counts original photo files; generated previews and thumbnails are excluded.").font(.footnote).foregroundStyle(.secondary)
                    }
                    NavigationLink("Review Deleted Photos in \(scope.collection.name)") { DeletedPhotosView() }.accessibilityIdentifier("reviewDeletedPhotos")
                    Text("Change your selected collection in Connection & Access to review deleted photos in another collection.").font(.footnote).foregroundStyle(.secondary)
                    if storage.deploymentMode == "hosted" && scope.household.role == "owner" {
                        NavigationLink("Review Hosted Subscription") { Form { PaidServiceSection(household: scope.household) }.navigationTitle("Hosted Service Plan") }
                    }
                }
                if let failure { Text(failure).foregroundStyle(.secondary) }
                Button(loading ? "Refreshing…" : "Refresh Storage") { Task { await refresh() } }.disabled(loading)
            } else {
                Text("Household storage totals are available only to owners and administrators. Ask your household administrator about storage or upload limits.")
            }
        }
        .navigationTitle("Household Storage")
        .task { await refresh() }
        .refreshable { await refresh() }
    }
    private func refresh() async {
        guard let scope = connection.scope, scope.administrator else { storage = nil; return }
        loading = true; defer { loading = false }
        do {
            let result = try await connection.householdStorage()
            storage = result; failure = nil
        } catch is CancellationError { }
        catch { storage = nil; failure = error.localizedDescription }
    }
}
