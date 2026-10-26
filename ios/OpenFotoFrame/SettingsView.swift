import SwiftUI

struct SettingsView: View {
    @EnvironmentObject private var session: FrameSession
    @State private var web: WebDestination?
    @State private var disconnecting = false
    var body: some View {
        List {
            Section("Connected Frame") {
                Label(session.server?.url.host ?? "OpenFotoFrame", systemImage: "network")
                LabeledContent("Access", value: session.isAdmin ? "Administrator" : "User")
                    .accessibilityElement(children: .ignore)
                    .accessibilityLabel("Account access")
                    .accessibilityValue(session.isAdmin ? "Administrator" : "User")
                    .accessibilityIdentifier("accountAccess")
            }
            Section("Presentation") {
                NavigationLink { AppearanceView() } label: { Label("Mat & Slideshow", systemImage: "photo.artframe") }
                Button { web = .init(title: "Full Frame Editor", path: "upload") } label: {
                    Label("Full Gallery & Group Editor", systemImage: "slider.horizontal.3")
                }
                if session.isAdmin {
                    Button { web = .init(title: "TV Power & Network", path: "upload", focus: .tv) } label: {
                        Label("TV Power Schedules & Network", systemImage: "power")
                    }
                }
            }
            Section("Account & Security") {
                Button { web = .init(title: "Change Password", path: "change-password") } label: {
                    Label("Change Password", systemImage: "key")
                }
                Button { web = .init(title: "Two-Factor Authentication", path: "mfa") } label: {
                    Label("Authenticator & Passkeys", systemImage: "lock.shield")
                }
            }
            if session.isAdmin {
                Section("Administration") {
                    Button { web = .init(title: "Dropbox Backup", path: "backup") } label: {
                        Label("Backup, Restore & History", systemImage: "externaldrive.badge.icloud")
                    }
                    Button { web = .init(title: "User Management", path: "admin/users") } label: {
                        Label("Manage Users", systemImage: "person.2")
                    }
                }
            }
            Section {
                Button("Sign Out") { Task { await session.signOut() } }
                Button("Disconnect This Frame", role: .destructive) { disconnecting = true }
            } footer: {
                Text("Advanced editors open your frame’s web interface and share your sign-in. Server permissions apply to every action.")
            }
            Section { LabeledContent("OpenFotoFrame for iOS", value: "0.1.0") }
        }
        .navigationTitle("Settings")
        .confirmationDialog("Sign out and forget this server?", isPresented: $disconnecting, titleVisibility: .visible) {
            Button("Disconnect", role: .destructive) { Task { await session.signOut(forgetServer: true) } }
        }
        .sheet(item: $web, onDismiss: { Task { await session.refresh() } }) { destination in
            NavigationStack { FrameWebScreen(destination: destination) }
        }
    }
}

struct AppearanceView: View {
    @EnvironmentObject private var session: FrameSession
    @State private var settings: FrameSettings?
    @State private var color = Color.white
    @State private var saving = false
    @State private var loadFailed = false
    @Environment(\.dismiss) private var dismiss
    var body: some View {
        Group {
            if settings != nil {
                Form {
                    Section("Default Mat") {
                        MatColorPicker(color: $color)
                        Picker("Finish", selection: value(\.matFinish, fallback: "eggshell")) {
                            ForEach(["eggshell", "flat", "linen", "suede", "silk"], id: \.self) { Text($0.capitalized) }
                        }
                        Picker("Border", selection: value(\.borderEffect, fallback: "bevel")) {
                            Text("Bevel").tag("bevel")
                            Text("Shadow").tag("shadow")
                        }
                        Stepper("Border width: \(Int(settings?.bevelWidth ?? 0)) px", value: value(\.bevelWidth, fallback: 4), in: 0...16)
                    }
                    Section("Slideshow") {
                        Stepper("Interval: \(Int(settings?.slideshowInterval ?? 60)) seconds", value: value(\.slideshowInterval, fallback: 60), in: 3...300)
                        VStack(alignment: .leading) {
                            Text("Transition: \(settings?.transitionDuration ?? 1, specifier: "%.1f") seconds")
                            Slider(value: value(\.transitionDuration, fallback: 1), in: 0...5, step: 0.1)
                        }
                        Toggle("Shuffle", isOn: value(\.shuffle, fallback: false))
                        Picker("Image fit", selection: value(\.fitMode, fallback: "contain")) {
                            Text("Show Whole Photo").tag("contain")
                            Text("Fill Screen").tag("cover")
                        }
                        Picker("Screen ratio", selection: value(\.targetAspectRatio, fallback: "16:9")) {
                            ForEach(["16:9", "21:9", "4:3", "1:1"], id: \.self) { Text($0) }
                        }
                    }
                    Section {
                        Text("Individual photos and groups can override mat settings. Saving appearance changes regenerates the frame’s display snapshots.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                }
            } else if loadFailed {
                ContentUnavailableView {
                    Label("Frame settings unavailable", systemImage: "wifi.exclamationmark")
                } actions: { Button("Try Again") { Task { await load() } } }
            } else {
                ProgressView("Loading frame settings…")
            }
        }
        .navigationTitle("Mat & Slideshow").navigationBarTitleDisplayMode(.inline)
        .toolbar {
            Button("Save") {
                Task {
                    saving = true
                    defer { saving = false }
                    await session.perform {
                        guard var settings else { return }
                        settings.matColor = color.frameHex
                        _ = try await session.mutate("api/settings", body: settings.payload)
                        dismiss()
                    }
                }
            }.disabled(settings == nil || saving)
        }
        .disabled(saving)
        .task { await load() }
    }
    private func load() async {
        loadFailed = false
        do {
            let loaded = try await session.get("api/settings", as: FrameSettings.self)
            settings = loaded
            color = Color(frameHex: loaded.matColor)
        } catch { loadFailed = true; session.error = error.localizedDescription }
    }
    private func value<T>(_ keyPath: WritableKeyPath<FrameSettings, T>, fallback: T) -> Binding<T> {
        Binding(get: { settings?[keyPath: keyPath] ?? fallback }, set: { settings?[keyPath: keyPath] = $0 })
    }
}
