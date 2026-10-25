import SwiftUI

@main
struct OpenFotoFrameApp: App {
    @StateObject private var session = FrameSession()
    var body: some Scene {
        WindowGroup {
            RootView().environmentObject(session).tint(.teal)
        }
    }
}

struct RootView: View {
    @EnvironmentObject private var session: FrameSession
    @State private var starting = true
    var body: some View {
        Group {
            if session.signingOut { ProgressView("Signing out…") }
            else if starting { ProgressView("Connecting to Frame…") }
            else if session.server == nil { ConnectView() }
            else if !session.authenticated {
                NavigationStack {
                    FrameWebScreen(destination: WebDestination(title: "Sign In", path: "upload"), login: true)
                }
            } else {
                TabView {
                    NavigationStack { GalleryView() }
                        .tabItem { Label("Photos", systemImage: "photo.on.rectangle.angled") }
                    NavigationStack { UploadView() }
                        .tabItem { Label("Upload", systemImage: "plus.circle") }
                    NavigationStack { DisplayView() }
                        .tabItem { Label("Frame", systemImage: "tv") }
                    NavigationStack { SettingsView() }
                        .tabItem { Label("Settings", systemImage: "gearshape") }
                }
            }
        }
        .task {
            guard starting else { return }
            defer { starting = false }
            #if DEBUG
            // Fixture UI tests explicitly choose a local test server.
            if ProcessInfo.processInfo.arguments.contains("--manual-frame-connection") { return }
            #endif
            guard !UserDefaults.standard.bool(forKey: "frameAutoConnectDisabled") else { return }
            if session.server == nil,
               let address = Bundle.main.object(forInfoDictionaryKey: "FrameDefaultServerURL") as? String {
                do { try session.connect(address) }
                catch { session.error = error.localizedDescription; return }
            }
            if session.server != nil {
                await session.didAuthenticate(reportAuthenticationError: false)
            }
        }
        .alert("OpenFotoFrame", isPresented: Binding(get: { session.error != nil }, set: { if !$0 { session.error = nil } })) {
            Button("OK") { session.error = nil }
        } message: { Text(session.error ?? "") }
    }
}

private struct ConnectView: View {
    @EnvironmentObject private var session: FrameSession
    @State private var address = Bundle.main.object(forInfoDictionaryKey: "FrameDefaultServerURL") as? String ?? ""
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(alignment: .leading, spacing: 28) {
                    Image(systemName: "photo.artframe")
                        .font(.system(size: 62, weight: .light)).foregroundStyle(.teal)
                        .frame(maxWidth: .infinity).padding(.top, 40)
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Your photos.\nYour frame.").font(.largeTitle.bold())
                        Text("Connect to OpenFotoFrame to curate your gallery and bring your favorite moments to the big screen.")
                            .foregroundStyle(.secondary)
                    }
                    VStack(alignment: .leading, spacing: 12) {
                        Text("Server address").font(.headline)
                        TextField("https://frame.example.com", text: $address)
                            .textContentType(.URL).keyboardType(.URL)
                            .textInputAutocapitalization(.never).autocorrectionDisabled()
                            .textFieldStyle(.roundedBorder)
                            .accessibilityIdentifier("serverAddress")
                            .onSubmit(connect)
                        Text("Use the same address you open in your browser. Local HTTP and Tailscale IP addresses are supported.")
                            .font(.footnote).foregroundStyle(.secondary)
                    }
                    Button(action: connect) {
                        Text("Connect to Frame").font(.headline).frame(maxWidth: .infinity).padding(8)
                    }
                    .buttonStyle(.borderedProminent).disabled(address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                    .accessibilityIdentifier("connectButton")
                    Label("Your photos stay on your server.", systemImage: "lock.shield")
                        .font(.footnote).foregroundStyle(.secondary)
                }.padding(28).frame(maxWidth: 560)
                    .frame(maxWidth: .infinity)
            }
            .navigationTitle("OpenFotoFrame").navigationBarTitleDisplayMode(.inline)
        }
    }
    private func connect() {
        do { try session.connect(address) } catch { session.error = error.localizedDescription }
    }
}

struct ServerImage: View {
    @EnvironmentObject private var session: FrameSession
    let path: String
    var fit: ContentMode = .fill
    @State private var image: UIImage?
    @State private var failed = false
    var body: some View {
        GeometryReader { geometry in
            Group {
                if let image {
                    Image(uiImage: image).resizable().aspectRatio(contentMode: fit)
                } else if failed {
                    Image(systemName: "photo.badge.exclamationmark").foregroundStyle(.secondary)
                } else { ProgressView() }
            }
            .frame(width: geometry.size.width, height: geometry.size.height)
            .clipped()
        }
        .task(id: path) {
            image = nil
            failed = false
            do { image = try await session.image(path) }
            catch { if !Task.isCancelled { failed = true } }
        }
    }
}

extension Color {
    init(frameHex: String) {
        let hex = frameHex.trimmingCharacters(in: CharacterSet(charactersIn: "#"))
        let number = UInt64(hex, radix: 16) ?? 0xffffff
        self.init(red: Double((number >> 16) & 255) / 255, green: Double((number >> 8) & 255) / 255, blue: Double(number & 255) / 255)
    }
    var frameHex: String {
        let color = UIColor(self).resolvedColor(with: UITraitCollection(userInterfaceStyle: .light))
        var r: CGFloat = 1, g: CGFloat = 1, b: CGFloat = 1, a: CGFloat = 1
        color.getRed(&r, green: &g, blue: &b, alpha: &a)
        return String(format: "#%02x%02x%02x", Int(r * 255), Int(g * 255), Int(b * 255))
    }
}
