import SwiftUI
import CoreImage.CIFilterBuiltins
import AVFoundation

struct RootView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @EnvironmentObject private var session: FrameSession
    @State private var confirmingLink = false
    private var fixtureLegacyFlow: Bool {
        #if DEBUG
        return ProcessInfo.processInfo.arguments.contains("--manual-frame-connection") || ProcessInfo.processInfo.environment["FRAME_TEST_TAILSCALE"] == "1"
        #else
        return false
        #endif
    }
    var body: some View {
        Group {
            if fixtureLegacyFlow { LegacyRootView() }
            else if connection.legacyRequested {
                LegacyRootView().safeAreaInset(edge: .top) {
                    HStack {
                        Text("Existing web companion").font(.caption)
                        Spacer()
                        Button("Connections") { Task { await session.signOut(forgetServer: true); connection.closeLegacy(); connection.returnToConnections() } }
                    }.padding(8).background(.regularMaterial)
                }
            } else if !connection.recoveryCodes.isEmpty {
                RecoveryCodesView()
            } else if connection.scope != nil {
                ReleaseWorkspaceView().id(connection.scopeIdentity)
            } else if connection.authenticated {
                ScopeSelectionView()
            } else if connection.serverInfo != nil {
                NativeSignInView()
            } else { ConnectionChoiceView() }
        }
        .onOpenURL { connection.importLink($0.absoluteString) }
        .onChange(of: connection.pendingLink?.id) { _, value in if value != nil { confirmingLink = true } }
        .sheet(isPresented: $confirmingLink) {
            DestinationConfirmationView { confirmingLink = false }
        }
        .alert("OpenFotoFrame", isPresented: Binding(get: { connection.error != nil }, set: { if !$0 { connection.error = nil } })) {
            Button("OK") { connection.error = nil }
        } message: { Text(connection.error ?? "") }
    }
}

private struct ReleaseWorkspaceView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @EnvironmentObject private var session: FrameSession
    var body: some View {
        Group {
            if session.authenticated {
                TabView {
                    NavigationStack { GalleryView() }.tabItem { Label("Photos", systemImage: "photo.on.rectangle.angled") }
                    if session.canUpload { NavigationStack { UploadView() }.tabItem { Label("Upload", systemImage: "plus.circle") } }
                    NavigationStack { DisplayView() }.tabItem { Label("Frame", systemImage: "tv") }
                    NavigationStack { SettingsView() }.tabItem { Label("Settings", systemImage: "gearshape") }
                }
            } else {
                VStack(spacing: 18) {
                    if let error = session.error { Text(error).foregroundStyle(.secondary); Button("Choose Collection") { connection.changeScope() } }
                    else { ProgressView("Opening your collection…") }
                }.padding()
            }
        }
        .task { await session.adoptRelease(connection) }
        .alert("OpenFotoFrame", isPresented: Binding(get: { session.error != nil && session.authenticated }, set: { if !$0 { session.error = nil } })) {
            Button("OK") { session.error = nil }
        } message: { Text(session.error ?? "") }
    }
}

private struct ConnectionChoiceView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @EnvironmentObject private var session: FrameSession
    @State private var mode: HostingMode?
    @State private var address = ""
    @State private var scanning = false
    @State private var importing = false
    @State private var importText = ""
    @State private var confirmHTTP = false
    @State private var pendingSaved: ConnectionProfile?
    @State private var pendingLegacy = false
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Image(systemName: "photo.artframe").font(.system(size: 54, weight: .light)).foregroundStyle(.teal).frame(maxWidth: .infinity).padding()
                    Text("Your photos. Your choice.").font(.title.bold())
                    Text("Use OpenFotoFrame Cloud, or connect to a server you manage.").foregroundStyle(.secondary)
                }
                Section("Get Started") {
                    Button { mode = .hosted } label: { Label("OpenFotoFrame Cloud", systemImage: "cloud") }
                        .accessibilityIdentifier("chooseHostedService")
                    Button { mode = .selfHosted } label: { Label("Your own server", systemImage: "externaldrive.connected.to.line.below") }
                        .accessibilityIdentifier("chooseSelfHosted")
                    Button { scanning = true } label: { Label("Scan a setup or invitation code", systemImage: "qrcode.viewfinder") }
                    Button("Paste an invitation or setup link") { importing = true }
                }
                if let mode {
                    Section(mode.title) {
                        if mode == .hosted {
                            if let hosted = connection.hostedAddress {
                                LabeledContent("Service", value: hosted)
                                Button("Continue") { Task { await connection.connect(mode: .hosted, address: hosted) } }
                            } else {
                                Text("The hosted service is not available in this build yet. You can connect to your own server or use an invitation.").foregroundStyle(.secondary)
                            }
                        } else {
                            TextField("https://frame.example.com", text: $address)
                                .textContentType(.URL).keyboardType(.URL).textInputAutocapitalization(.never).autocorrectionDisabled()
                                .accessibilityIdentifier("releaseServerAddress")
                            Button("Continue") { connect(address) }.disabled(address.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty)
                            Text("Enter the server root address. If it cannot be reached, you can retry or edit the address.").font(.footnote).foregroundStyle(.secondary)
                        }
                    }
                }
                if !connection.profiles.isEmpty {
                    Section("Saved Connections") {
                        ForEach(connection.profiles) { profile in
                            Button {
                                pendingSaved = profile
                                if profile.legacy { address = profile.origin; mode = .selfHosted }
                                else { connect(profile.origin, saved: profile) }
                            } label: {
                                VStack(alignment: .leading) {
                                    Text(profile.label)
                                    Text(profile.origin).font(.caption).foregroundStyle(.secondary)
                                    if profile.legacy { Text("Existing server — confirm updated sign-in or use the web companion").font(.caption).foregroundStyle(.secondary) }
                                }
                            }
                            .swipeActions { Button("Forget", role: .destructive) { connection.removeProfile(profile) } }
                        }
                    }
                }
                Section {
                    Button("Use Existing Web Companion") {
                        do {
                            let destination = address.isEmpty ? pendingSaved?.origin ?? "" : address
                            let server = try FrameServer(destination)
                            if server.url.scheme == "http" { address = server.origin; pendingLegacy = true; confirmHTTP = true; return }
                            try session.connect(server.origin); connection.openLegacy()
                        } catch { connection.error = error.localizedDescription }
                    }.disabled(mode != .selfHosted || (address.isEmpty && pendingSaved == nil))
                } footer: {
                    Text("The existing web companion retains the server’s current authentication and shared-library behavior. The new collection permissions require an updated server.")
                }
            }
            .navigationTitle("OpenFotoFrame")
            .disabled(connection.busy)
            .overlay { if connection.busy { ProgressView("Connecting…").padding().background(.regularMaterial, in: RoundedRectangle(cornerRadius: 12)) } }
            .sheet(isPresented: $scanning) { CodeScanner { value in scanning = false; connection.importLink(value) } }
            .sheet(isPresented: $importing) {
                NavigationStack {
                    Form { TextField("Paste the full link", text: $importText, axis: .vertical).textInputAutocapitalization(.never).autocorrectionDisabled() }
                        .navigationTitle("Import a Link")
                        .toolbar {
                            ToolbarItem(placement: .cancellationAction) { Button("Cancel") { importing = false; importText = "" } }
                            ToolbarItem(placement: .confirmationAction) { Button("Continue") { connection.importLink(importText); importing = false; importText = "" }.disabled(importText.isEmpty) }
                        }
                }
            }
            .confirmationDialog("Use an unencrypted local connection?", isPresented: $confirmHTTP, titleVisibility: .visible) {
                Button("Continue with Local HTTP") {
                    if pendingLegacy {
                        do { try session.connect(address); connection.openLegacy() } catch { connection.error = error.localizedDescription }
                        pendingLegacy = false
                    } else { Task { await connection.connect(mode: .selfHosted, address: address, allowLocalHTTP: true, saved: pendingSaved) } }
                }
                Button("Cancel", role: .cancel) { pendingLegacy = false }
            } message: { Text("Your credentials and photos will travel without HTTPS encryption. Continue only on a network you trust.") }
        }
    }
    private func connect(_ value: String, saved: ConnectionProfile? = nil) {
        do {
            let server = try FrameServer(value)
            address = server.origin; pendingSaved = saved; pendingLegacy = false
            let selectedMode = saved?.mode ?? mode ?? .selfHosted
            mode = selectedMode
            if server.url.scheme == "http" && !(saved?.allowsLocalHTTP == true && saved?.legacy == false) { confirmHTTP = true }
            else { Task { await connection.connect(mode: selectedMode, address: server.origin, allowLocalHTTP: saved?.allowsLocalHTTP == true, saved: saved) } }
        } catch { connection.error = error.localizedDescription }
    }
}

private struct DestinationConfirmationView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var allowHTTP = false
    let dismiss: () -> Void
    var body: some View {
        NavigationStack {
            Form {
                if let link = connection.pendingLink {
                    Section("Confirm Destination") {
                        LabeledContent("Service", value: link.mode.title)
                        Text(link.server.origin).font(.headline).textSelection(.enabled)
                        Text(link.purpose == .setup ? "This code fills in a server address. It does not sign you in." : link.purpose == .invite ? "Sign in or create an account on this server to accept the invitation." : "Quick login requires approval from the signed-in device. Scanning alone does not sign you in.")
                    }
                    if link.server.url.scheme == "http" {
                        Section { Toggle("Allow unencrypted local HTTP", isOn: $allowHTTP) } footer: { Text("Credentials and photos will not be encrypted on this connection.") }
                    }
                    Button("Connect to This Server") {
                        Task {
                            await connection.confirmLink(allowLocalHTTP: allowHTTP)
                            if connection.serverInfo != nil { dismiss() }
                        }
                    }.disabled(connection.busy || (link.server.url.scheme == "http" && !allowHTTP))
                }
            }
            .navigationTitle("OpenFotoFrame Link")
            .toolbar { Button("Cancel") { connection.returnToConnections(); dismiss() } }
        }
    }
}

private struct NativeSignInView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var email = ""
    @State private var password = ""
    @State private var householdName = ""
    @State private var bootstrapSecret = ""
    @State private var code = ""
    @State private var createAccount = false
    @State private var bootstrap = false
    @State private var quickLogin = false
    @State private var recovery = false
    var body: some View {
        NavigationStack {
            Form {
                Section {
                    Text(connection.profile?.mode.title ?? "OpenFotoFrame").font(.title2.bold())
                    Text(connection.profile?.origin ?? "").font(.footnote).foregroundStyle(.secondary)
                }
                if connection.enrollmentToken != nil { MFAEnrollmentSection() }
                else if connection.passwordChangeToken != nil {
                    Section("Set a New Password") {
                        Text("Your server requires a new password before you can access photos.")
                        SecureField("New password", text: $password).textContentType(.newPassword)
                        Button("Set Password") { let entered = password; password = ""; Task { await connection.changeRequiredPassword(entered) } }.disabled(password.isEmpty)
                    }
                } else if connection.challengeToken != nil {
                    Section("Verify Your Identity") {
                        TextField("Authenticator or recovery code", text: $code).textContentType(.oneTimeCode).textInputAutocapitalization(.never).autocorrectionDisabled()
                        Button("Verify") { Task { await connection.verifyMFA(code); code = "" } }.disabled(code.isEmpty)
                    }
                } else if connection.pendingLink?.purpose == .pair || quickLogin {
                    QuickLoginRequesterView()
                } else {
                    if let invitation = connection.invitation {
                        Section("Invitation") {
                            Text("Join \(invitation.householdName)")
                            if let recipient = invitation.recipientEmail { Text("For \(recipient)").font(.footnote).foregroundStyle(.secondary) }
                            else if invitation.recipientRequired == true { Text("Accept using the verified email address selected by the sender.").font(.footnote).foregroundStyle(.secondary) }
                            Text("You will receive only the collections and frames selected by the sender.").font(.footnote)
                        }
                    }
                    Section(createAccount || bootstrap ? "Create Your Account" : "Sign In") {
                        TextField(connection.profile?.mode == .selfHosted && !createAccount && !bootstrap ? "Email or username" : "Email", text: $email).textContentType(.username).keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled().accessibilityIdentifier("releaseEmail")
                        SecureField("Password", text: $password).textContentType(createAccount || bootstrap ? .newPassword : .password).accessibilityIdentifier("releasePassword")
                        if createAccount && connection.invitation == nil || bootstrap { TextField("Household name", text: $householdName) }
                        if bootstrap { SecureField("Operator setup secret", text: $bootstrapSecret).textInputAutocapitalization(.never).autocorrectionDisabled() }
                        Button(bootstrap ? "Set Up Server" : createAccount ? "Create Account" : "Sign In") {
                            let enteredPassword = password
                            password = ""
                            Task {
                                if bootstrap { await connection.bootstrap(secret: bootstrapSecret, email: email, password: enteredPassword, householdName: householdName); bootstrapSecret = "" }
                                else if createAccount { await connection.register(email: email, password: enteredPassword, householdName: householdName) }
                                else { await connection.login(email: email, password: enteredPassword) }
                            }
                        }.disabled(email.isEmpty || password.isEmpty || (bootstrap && bootstrapSecret.isEmpty))
                    }
                    Section {
                        Button(createAccount ? "I already have an account" : "Create an account") { createAccount.toggle(); bootstrap = false }
                            .disabled(connection.serverInfo?.registrationEnabled != true && connection.invitation == nil)
                        if connection.serverInfo?.capabilities["auth_code_pkce"] == true && connection.server?.url.scheme == "https" {
                            Button("Sign In Securely in Browser or with Passkey") { Task { await connection.browserSignIn() } }
                        }
                        Button("Quick login with a QR code") { quickLogin = true }
                        Button("Forgot password?") { recovery = true }
                        if connection.profile?.mode == .selfHosted && connection.serverInfo?.bootstrapRequired == true {
                            Button("Set up a new server") { bootstrap.toggle(); createAccount = false }
                        }
                    }
                }
            }
            .disabled(connection.busy)
            .navigationTitle(connection.challengeToken == nil ? "Welcome" : "Verification")
            .toolbar { ToolbarItem(placement: .topBarLeading) { Button("Connections") { connection.returnToConnections() } } }
            .sheet(isPresented: $recovery) { AccountRecoveryView() }
        }
    }
}

private struct ScopeSelectionView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var householdID = ""
    @State private var collectionID = ""
    @State private var frameID = ""
    @State private var accepting = false
    @State private var collectionName = ""
    @State private var frameName = ""
    @State private var assignCollection = false
    var body: some View {
        NavigationStack {
            Form {
                Section("Household") {
                    Picker("Household", selection: $householdID) {
                        Text("Choose a household").tag("")
                        ForEach(connection.identity?.households ?? []) { Text($0.name).tag($0.id) }
                    }.accessibilityIdentifier("releaseHouseholdPicker")
                }
                if let household = selectedHousehold {
                    Section("Collection") {
                        Picker("Collection", selection: $collectionID) {
                            Text("Choose a collection").tag("")
                            ForEach(connection.collections) { Text($0.name).tag($0.id) }
                        }.accessibilityIdentifier("releaseCollectionPicker")
                        if connection.collections.isEmpty { Text("You do not have access to a collection yet. Ask a household administrator for an invitation.").foregroundStyle(.secondary) }
                        if ["owner", "admin"].contains(household.role) {
                            TextField("New collection name", text: $collectionName)
                            Button("Create Collection") { Task { await createCollection(household) } }.disabled(collectionName.isEmpty || connection.busy)
                        }
                    }
                    Section("Frame") {
                        Picker("Frame", selection: $frameID) {
                            Text("No frame selected").tag("")
                            ForEach(connection.frames) { Text($0.name).tag($0.id) }
                        }.accessibilityIdentifier("releaseFramePicker")
                        Text("Frame controls use the selected frame. Frame access does not grant access to its photos.").font(.footnote).foregroundStyle(.secondary)
                        if ["owner", "admin"].contains(household.role) {
                            TextField("New frame name", text: $frameName)
                            if !collectionID.isEmpty { Toggle("Assign the selected collection to this new frame", isOn: $assignCollection) }
                            Button("Create Frame") { Task { await createFrame(household) } }.disabled(frameName.isEmpty || connection.busy)
                        }
                    }
                    if connection.profile?.mode == .hosted && household.role == "owner" { PaidServiceSection(household: household) }
                    if let collection = connection.collections.first(where: { $0.id == collectionID }) {
                        Button("Open Collection") { connection.selectScope(household: household, collection: collection, frame: connection.frames.first { $0.id == frameID }) }.accessibilityIdentifier("openScopedCollection")
                    }
                }
                if connection.identity?.user.emailVerified == false { EmailVerificationSection() }
                if connection.pendingLink?.purpose == .invite {
                    Section("Pending Invitation") {
                        Text(connection.invitation?.householdName ?? "Your invitation")
                        Button("Accept Invitation") { Task { await connection.perform { try await connection.acceptInvitation() } } }
                    }
                }
                if connection.identity?.households.isEmpty == true {
                    if connection.profile?.mode == .hosted && connection.pendingLink?.purpose != .invite { PaidServiceSection(household: nil) }
                    Section {
                        Text("Your account is ready. Accept an invitation to join a household, or finish hosted service registration when a paid plan is available.")
                        if connection.pendingLink?.purpose == .invite { Button("Accept Invitation") { Task { await connection.perform { try await connection.acceptInvitation() } } } }
                    }
                }
                Section {
                    Button("Use Another Connection") { connection.returnToConnections() }
                    Button("Sign Out") { Task { await connection.signOut() } }
                }
            }
            .navigationTitle("Choose Your Photos")
            .onChange(of: householdID) { _, _ in
                collectionID = ""; frameID = ""
                if let household = selectedHousehold { Task { await connection.loadResources(household) } }
            }
            .task {
                if let first = connection.identity?.households.first { householdID = first.id; await connection.loadResources(first) }
            }
            .onChange(of: connection.identity?.households.map(\.id)) { _, _ in
                if selectedHousehold == nil, let first = connection.identity?.households.first { householdID = first.id }
            }
        }
    }
    private func createCollection(_ household: ReleaseHousehold) async {
        await connection.perform {
            struct Created: Decodable { let id: String }
            let created: Created = try await connection.json("api/v1/households/\(household.id)/collections", method: "POST", body: ["name": collectionName])
            await connection.loadResources(household); collectionID = created.id; collectionName = ""
        }
    }
    private func createFrame(_ household: ReleaseHousehold) async {
        await connection.perform {
            struct Created: Decodable { let id: String }
            let created: Created = try await connection.json("api/v1/households/\(household.id)/frames", method: "POST", body: ["name": frameName])
            if assignCollection && !collectionID.isEmpty {
                let _: ReleaseAcknowledgement = try await connection.json("api/v1/households/\(household.id)/frames/\(created.id)", method: "PATCH", body: ["collection_ids": [collectionID]])
            }
            await connection.loadResources(household); frameID = created.id; frameName = ""
        }
    }
    private var selectedHousehold: ReleaseHousehold? { connection.identity?.households.first { $0.id == householdID } }
}

struct ConnectionAccountView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var pairing: ReleasePairing?
    @State private var invitations = false
    var body: some View {
        List {
            Section("This Connection") {
                LabeledContent("Service", value: connection.profile?.mode.title ?? "")
                Text(connection.profile?.origin ?? "").font(.footnote)
                LabeledContent("Account", value: connection.identity?.user.email ?? "").accessibilityIdentifier("connectionAccount")
                if let scope = connection.scope {
                    LabeledContent("Household", value: scope.household.name)
                    LabeledContent("Collection", value: scope.collection.name).accessibilityIdentifier("connectionCollection")
                    LabeledContent("Frame", value: scope.frame?.name ?? "None")
                    Button("Change Household, Collection or Frame") { connection.changeScope() }
                }
            }
            if connection.identity?.user.emailVerified == false { EmailVerificationSection() }
            Section {
                NavigationLink("Signed-In Devices") { DeviceSessionsView() }
                if connection.scope?.administrator == true && connection.scope?.frame != nil { NavigationLink("Set Up Display") { FrameEnrollmentView() } }
                NavigationLink("Set Up Authenticator") { Form { MFAEnrollmentSection() }.navigationTitle("Authenticator Setup") }
            }
            Section("Share Access") {
                Button("Sign In Another Device") { Task { await connection.perform { pairing = try await connection.json("api/v1/pairings", method: "POST", body: [:]) } } }
                if connection.scope?.administrator == true { Button("Invite to Selected Collections and Frames") { invitations = true } }
            }
            Section {
                Button("Connections") { connection.returnToConnections() }
                Button("Sign Out This Device") { Task { await connection.signOut() } }
            }
        }
        .navigationTitle("Connection & Access")
        .sheet(item: Binding(get: { pairing.map { PairingItem(pairing: $0) } }, set: { if $0 == nil { pairing = nil } })) { item in PairingApprovalView(pairing: item.pairing) }
        .sheet(isPresented: $invitations) { InvitationCreationView() }
    }
}
private struct PairingItem: Identifiable { var id: String { pairing.id }; let pairing: ReleasePairing }

struct CodeScanner: View {
    @Environment(\.dismiss) private var dismiss
    let found: (String) -> Void
    @State private var permitted: Bool?
    var body: some View {
        NavigationStack {
            Group {
                if permitted == true { CameraCodeReader(found: found).ignoresSafeArea(edges: .bottom) }
                else if permitted == false { ContentUnavailableView("Camera Access Needed", systemImage: "camera", description: Text("Allow camera access in iOS Settings, or paste the setup link instead.")) }
                else { ProgressView("Opening camera…") }
            }.navigationTitle("Scan OpenFotoFrame Code").navigationBarTitleDisplayMode(.inline)
                .toolbar { Button("Cancel") { dismiss() } }
                .task { permitted = await AVCaptureDevice.requestAccess(for: .video) }
        }
    }
}
private struct CameraCodeReader: UIViewControllerRepresentable {
    let found: (String) -> Void
    func makeUIViewController(context: Context) -> ScannerController { ScannerController(found: found) }
    func updateUIViewController(_ uiViewController: ScannerController, context: Context) { }
    static func dismantleUIViewController(_ controller: ScannerController, coordinator: ()) { controller.stop() }
}
private final class ScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    let capture = AVCaptureSession()
    let queue = DispatchQueue(label: "OpenFotoFrame.QR")
    let found: (String) -> Void
    private var handled = false
    init(found: @escaping (String) -> Void) { self.found = found; super.init(nibName: nil, bundle: nil) }
    required init?(coder: NSCoder) { fatalError("init(coder:) has not been implemented") }
    override func viewDidLoad() {
        super.viewDidLoad()
        guard let camera = AVCaptureDevice.default(for: .video), let input = try? AVCaptureDeviceInput(device: camera), capture.canAddInput(input) else { return }
        capture.addInput(input)
        let output = AVCaptureMetadataOutput()
        guard capture.canAddOutput(output) else { return }
        capture.addOutput(output); output.setMetadataObjectsDelegate(self, queue: .main); output.metadataObjectTypes = [.qr]
        let preview = AVCaptureVideoPreviewLayer(session: capture)
        preview.videoGravity = .resizeAspectFill; preview.frame = view.bounds; view.layer.addSublayer(preview)
        queue.async { [capture] in capture.startRunning() }
    }
    override func viewDidLayoutSubviews() { super.viewDidLayoutSubviews(); view.layer.sublayers?.compactMap { $0 as? AVCaptureVideoPreviewLayer }.forEach { $0.frame = view.bounds } }
    func stop() { queue.async { [capture] in if capture.isRunning { capture.stopRunning() } } }
    func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject], from connection: AVCaptureConnection) {
        guard !handled, let code = metadataObjects.compactMap({ ($0 as? AVMetadataMachineReadableCodeObject)?.stringValue }).first else { return }
        handled = true; stop(); found(code)
    }
}

struct QRCodeImage: View {
    let value: String
    var body: some View {
        if let image {
            Image(uiImage: image).interpolation(.none).resizable().scaledToFit().padding(16).background(.white).accessibilityLabel("OpenFotoFrame QR code")
        } else { Image(systemName: "qrcode").font(.largeTitle) }
    }
    private var image: UIImage? {
        let filter = CIFilter.qrCodeGenerator(); filter.message = Data(value.utf8)
        guard let output = filter.outputImage, let cg = CIContext().createCGImage(output.transformed(by: CGAffineTransform(scaleX: 8, y: 8)), from: output.extent.applying(CGAffineTransform(scaleX: 8, y: 8))) else { return nil }
        return UIImage(cgImage: cg)
    }
}
