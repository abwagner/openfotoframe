import SwiftUI

struct QuickLoginRequesterView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var scanning = false
    @State private var challenge: ReleasePairingChallenge?
    @State private var deadline: Date?
    @State private var message: String?
    @State private var requesting = false
    @State private var polling = false
    var body: some View {
        Section("Quick Login") {
            if let challenge {
                Text("Confirm this code on the signed-in device:")
                Text(challenge.confirmationCode).font(.largeTitle.monospaced().bold()).accessibilityIdentifier("pairingConfirmationCode")
                Text("Only approve if the device name and this code match. The request expires after two minutes.").font(.footnote).foregroundStyle(.secondary)
                if polling { ProgressView("Waiting for approval…") }
                if let message { Text(message).foregroundStyle(.secondary) }
            } else if connection.pendingLink?.purpose == .pair {
                Text("Request approval from the signed-in device. You will receive the same permissions as that account.")
                Button("Request Quick Login") { Task { await request() } }.disabled(requesting)
                if let message { Text(message).foregroundStyle(.secondary) }
            } else {
                Text("On a signed-in device, open Connection & Access → Sign In Another Device. Scan the code shown there.")
                Button("Scan Quick Login Code") { scanning = true }
            }
        }
        .sheet(isPresented: $scanning) { CodeScanner { value in scanning = false; connection.importLink(value) } }
        .task(id: challenge?.challengeId) {
            guard let challenge, let id = connection.pendingLink?.reference else { return }
            polling = true
            defer { polling = false }
            while !Task.isCancelled, let deadline, Date() < deadline {
                do {
                    if try await connection.exchangePairing(id: id, challenge: challenge) { return }
                    try await Task.sleep(for: .seconds(2))
                } catch is CancellationError { return }
                catch { message = error.localizedDescription; return }
            }
            if !Task.isCancelled { message = "This quick login request expired. Scan a new code to try again." }
        }
        .onDisappear { challenge = nil; deadline = nil }
    }
    private func request() async {
        guard let link = connection.pendingLink, let id = link.reference, let code = link.secret else { return }
        requesting = true; defer { requesting = false }
        do {
            challenge = try await connection.json("api/v1/pairings/\(id)/challenge", method: "POST", body: ["code": code, "device_name": UIDevice.current.name], authorized: false)
            deadline = Date().addingTimeInterval(120)
            message = nil
        } catch { message = error.localizedDescription }
    }
}

struct PairingApprovalView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @Environment(\.dismiss) private var dismiss
    let pairing: ReleasePairing
    @State private var challenges: [ReleasePairingApproval] = []
    @State private var expired = false
    @State private var approved = false
    @State private var failure: String?
    @State private var approving: String?
    var body: some View {
        NavigationStack {
            ScrollView {
                VStack(spacing: 20) {
                    if let server = connection.server, let mode = connection.profile?.mode {
                        let link = ConnectionLink.url(purpose: .pair, mode: mode, server: server, reference: pairing.id, secret: pairing.code, landingURL: connection.landingURL)
                        if !expired && !approved {
                            QRCodeImage(value: link.absoluteString).frame(maxWidth: 280)
                            ShareLink(item: link) { Label("Share Quick Login Code", systemImage: "square.and.arrow.up") }
                        }
                    }
                    Text(approved ? "Device Approved" : expired ? "Code Expired" : "Sign In Another Device").font(.title2.bold())
                    Text("Scan with the new device, then compare its confirmation code before approving.").foregroundStyle(.secondary)
                    ForEach(challenges) { challenge in
                        VStack(spacing: 12) {
                            Text(challenge.deviceName ?? "Requesting device").font(.headline)
                            Text(challenge.confirmationCode).font(.largeTitle.monospaced().bold())
                            Button("Approve This Device") { Task { await approve(challenge) } }
                                .buttonStyle(.borderedProminent).disabled(expired || approved || approving != nil)
                        }.padding().frame(maxWidth: .infinity).background(.quaternary, in: RoundedRectangle(cornerRadius: 16))
                    }
                    if let failure { Text(failure).font(.footnote).foregroundStyle(.secondary) }
                }.padding(24)
            }
            .navigationTitle("Quick Login").navigationBarTitleDisplayMode(.inline)
            .toolbar { Button("Done") { dismiss() } }
            .task {
                let deadline = Date().addingTimeInterval(min(120, pairing.expiresIn))
                while !Task.isCancelled, Date() < deadline, !approved {
                    do {
                        let result: ReleasePairingStatus = try await connection.json("api/v1/pairings/\(pairing.id)")
                        challenges = result.challenges
                        try await Task.sleep(for: .seconds(2))
                    } catch is CancellationError { return }
                    catch { failure = error.localizedDescription; return }
                }
                if !Task.isCancelled && !approved { expired = true }
            }
        }
    }
    private func approve(_ challenge: ReleasePairingApproval) async {
        approving = challenge.challengeId; defer { approving = nil }
        do {
            let _: ReleaseAcknowledgement = try await connection.json("api/v1/pairings/\(pairing.id)/approve", method: "POST", body: ["challenge_id": challenge.challengeId, "confirmation_code": challenge.confirmationCode])
            approved = true
        } catch { failure = error.localizedDescription }
    }
}

struct InvitationCreationView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @Environment(\.dismiss) private var dismiss
    @State private var email = ""
    @State private var transferable = false
    @State private var selectedCollections: Set<String> = []
    @State private var selectedFrames: Set<String> = []
    @State private var collectionRole = "viewer"
    @State private var frameRole = "viewer"
    @State private var link: URL?
    @State private var creating = false
    @State private var failure: String?
    var body: some View {
        NavigationStack {
            Form {
                if let link {
                    Section("Invitation Ready") {
                        Text("The invitation expires after 24 hours and can be accepted once. Sharing does not send your credentials.")
                        QRCodeImage(value: link.absoluteString).frame(maxWidth: 260).frame(maxWidth: .infinity)
                        ShareLink(item: link) { Label("Send with Mail or Messages", systemImage: "square.and.arrow.up") }
                        Button("Copy Invitation Link") { UIPasteboard.general.url = link }
                    }
                } else {
                    Section("Recipient") {
                        TextField("Email address", text: $email).textContentType(.emailAddress).keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled().disabled(transferable)
                        Toggle("Anyone with the invitation can accept", isOn: $transferable)
                        Text(transferable ? "This invitation is transferable. Share it only with the intended recipient." : "The recipient must verify this email address before accepting.").font(.footnote).foregroundStyle(.secondary)
                    }
                    Section("Collections") {
                        Picker("Access", selection: $collectionRole) {
                            Text("View").tag("viewer"); Text("Contribute").tag("contributor"); Text("Edit").tag("editor")
                        }
                        ForEach(connection.collections) { collection in
                            Toggle(collection.name, isOn: selection(collection.id, in: $selectedCollections))
                        }
                    }
                    Section("Frames") {
                        Picker("Access", selection: $frameRole) { Text("View Status").tag("viewer"); Text("Control Playback").tag("controller") }
                        ForEach(connection.frames) { frame in Toggle(frame.name, isOn: selection(frame.id, in: $selectedFrames)) }
                    }
                    Section {
                        Button("Create Invitation") { Task { await create() } }
                            .disabled(creating || (!transferable && email.trimmingCharacters(in: .whitespacesAndNewlines).isEmpty) || (selectedCollections.isEmpty && selectedFrames.isEmpty))
                    } footer: { Text("The invitation grants ordinary membership and only the selected collections and frames. It does not grant administrator access.") }
                }
                if let failure { Section { Text(failure).foregroundStyle(.secondary) } }
            }
            .navigationTitle("Invite Someone")
            .toolbar { Button("Done") { dismiss() } }
        }
    }
    private func selection(_ id: String, in values: Binding<Set<String>>) -> Binding<Bool> {
        Binding(get: { values.wrappedValue.contains(id) }, set: { if $0 { values.wrappedValue.insert(id) } else { values.wrappedValue.remove(id) } })
    }
    private func create() async {
        guard let scope = connection.scope, let server = connection.server, let mode = connection.profile?.mode else { return }
        creating = true; defer { creating = false }
        do {
            struct Invitation: Decodable { let token: String }
            let invitation: Invitation = try await connection.json("api/v1/households/\(scope.household.id)/invitations", method: "POST", body: [
                "recipient_email": transferable ? NSNull() : email.trimmingCharacters(in: .whitespacesAndNewlines) as Any,
                "transferable": transferable,
                "collection_grants": selectedCollections.sorted().map { ["collection_id": $0, "role": collectionRole] },
                "frame_grants": selectedFrames.sorted().map { ["frame_id": $0, "role": frameRole] }
            ])
            link = ConnectionLink.url(purpose: .invite, mode: mode, server: server, secret: invitation.token, landingURL: connection.landingURL)
        } catch { failure = error.localizedDescription }
    }
}

struct AccountRecoveryView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @Environment(\.dismiss) private var dismiss
    @State private var email = ""
    @State private var recoveryToken = ""
    @State private var password = ""
    @State private var sent = false
    @State private var complete = false
    @State private var code = ""
    @State private var busy = false
    @State private var failure: String?
    var body: some View {
        NavigationStack {
            Form {
                Section("Recover Your Account") {
                    TextField("Email address", text: $email).keyboardType(.emailAddress).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Request Recovery Instructions") { Task { await request() } }.disabled(email.isEmpty || busy)
                    if sent { Text("If recovery is available for this account, the server will send instructions. For a server without email, contact its operator.").font(.footnote).foregroundStyle(.secondary) }
                }
                Section("Use a Recovery Token") {
                    SecureField("Recovery token", text: $recoveryToken).textInputAutocapitalization(.never).autocorrectionDisabled()
                    SecureField("New password", text: $password).textContentType(.newPassword)
                    TextField("Authenticator or recovery code (if enabled)", text: $code).textInputAutocapitalization(.never).autocorrectionDisabled()
                    Button("Reset Password") { Task { await reset() } }.disabled(recoveryToken.isEmpty || password.isEmpty || busy)
                    if complete { Text("Password updated. Sign in again with your new password.") }
                }
                if let failure { Section { Text(failure).foregroundStyle(.secondary) } }
            }
            .navigationTitle("Account Recovery")
            .toolbar { Button("Done") { dismiss() } }
        }
    }
    private func request() async {
        busy = true; defer { busy = false }
        do {
            let _: ReleaseAcknowledgement = try await connection.json("api/v1/auth/recovery/request", method: "POST", body: ["email": email], authorized: false)
            sent = true; failure = nil
        } catch { failure = error.localizedDescription }
    }
    private func reset() async {
        busy = true; defer { busy = false }
        do {
            let _: ReleaseAcknowledgement = try await connection.json("api/v1/auth/recovery/complete", method: "POST", body: ["token": recoveryToken, "password": password, "code": code], authorized: false)
            complete = true; failure = nil; password = ""; recoveryToken = ""
        } catch { failure = error.localizedDescription }
    }
}

struct EmailVerificationSection: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var token = ""
    @State private var sent = false
    @State private var busy = false
    @State private var failure: String?
    var body: some View {
        Section("Verify Your Email") {
            Text("Verify your account email before accepting an invitation addressed to you.").font(.footnote).foregroundStyle(.secondary)
            Button("Send Verification Instructions") { Task { await request() } }.disabled(busy)
            SecureField("Verification token", text: $token).textInputAutocapitalization(.never).autocorrectionDisabled()
            Button("Verify Email") { Task { await complete() } }.disabled(token.isEmpty || busy)
            if sent { Text("Check your email, or contact the server operator if email delivery is not configured.").font(.footnote) }
            if let failure { Text(failure).font(.footnote).foregroundStyle(.secondary) }
        }
    }
    private func request() async {
        busy = true; defer { busy = false }
        do { let _: ReleaseAcknowledgement = try await connection.json("api/v1/auth/verification/request", method: "POST", body: [:]); sent = true; failure = nil }
        catch { failure = error.localizedDescription }
    }
    private func complete() async {
        busy = true; defer { busy = false }
        do { let _: ReleaseAcknowledgement = try await connection.json("api/v1/auth/verification/complete", method: "POST", body: ["token": token]); try await connection.refreshIdentity(); token = ""; failure = nil }
        catch { failure = error.localizedDescription }
    }
}
struct ReleaseAcknowledgement: Decodable { }

struct MFAEnrollmentSection: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var code = ""
    var body: some View {
        Section("Set Up an Authenticator") {
            if let setup = connection.mfaSetup {
                QRCodeImage(value: setup.provisioningUri).frame(maxWidth: 240).frame(maxWidth: .infinity)
                Text("Scan this code in your authenticator, or enter the setup key manually.").font(.footnote).foregroundStyle(.secondary)
                Text(setup.secret).font(.body.monospaced()).textSelection(.enabled).privacySensitive()
                TextField("Authenticator code", text: $code).keyboardType(.numberPad).textContentType(.oneTimeCode)
                Button("Verify and Save Recovery Codes") { Task { await connection.completeMFAEnrollment(code); code = "" } }.disabled(code.isEmpty || connection.busy)
            } else {
                Text("Your server requires an authenticator before you can sign in. You will receive recovery codes after verification.")
                Button("Set Up Authenticator") { Task { await connection.beginMFAEnrollment() } }.disabled(connection.busy)
            }
        }
    }
}

struct RecoveryCodesView: View {
    @EnvironmentObject private var connection: ReleaseSession
    var body: some View {
        NavigationStack {
            Form {
                Section("Save Your Recovery Codes") {
                    Text("Keep these codes in a password manager or another secure place. Each code can be used once if you lose access to your authenticator. They will not be shown again.")
                    ForEach(connection.recoveryCodes, id: \.self) { Text($0).font(.body.monospaced()).textSelection(.enabled).privacySensitive() }
                    Button("Copy Codes for One Minute") {
                        UIPasteboard.general.setItems([[UIPasteboard.typeAutomatic: connection.recoveryCodes.joined(separator: "\n")]], options: [.localOnly: true, .expirationDate: Date().addingTimeInterval(60)])
                    }
                }
                Section { Button("I Have Saved These Codes") { connection.recoveryCodes = [] } }
            }
            .navigationTitle("Recovery Codes")
        }
    }
}

struct NativeAccountSecurityView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var currentPassword = ""
    @State private var password = ""
    @State private var code = ""
    @State private var failure: String?
    @State private var busy = false
    var body: some View {
        Form {
            Section("Change Password") {
                SecureField("Current password", text: $currentPassword).textContentType(.password)
                SecureField("New password", text: $password).textContentType(.newPassword)
                TextField("Authenticator or recovery code (if enabled)", text: $code).textInputAutocapitalization(.never).autocorrectionDisabled()
                Button("Update Password and Sign Out Devices") { Task { await update() } }.disabled(currentPassword.isEmpty || password.isEmpty || busy)
            }
            if let failure { Section { Text(failure).foregroundStyle(.secondary) } }
        }.navigationTitle("Change Password")
    }
    private func update() async {
        busy = true; defer { busy = false }
        do {
            let _: ReleaseAcknowledgement = try await connection.json("api/v1/auth/password", method: "POST", body: ["current_password": currentPassword, "password": password, "code": code])
            currentPassword = ""; password = ""; code = ""
            await connection.signOut()
        } catch { failure = error.localizedDescription }
    }
}

struct FrameEnrollmentView: View {
    @EnvironmentObject private var connection: ReleaseSession
    @State private var code: String?
    @State private var expiry: Date?
    @State private var failure: String?
    @State private var confirmRevoke = false
    var body: some View {
        Form {
            if let scope = connection.scope, let frame = scope.frame {
                Section("Set Up \(frame.name)") {
                    Text("Open this address on the physical display, then enter the short-lived code. The display receives access only to this frame’s assigned collections.")
                    if let url = connection.server?.endpoint("display/enroll") { Text(url.absoluteString).textSelection(.enabled) }
                    if let code, let expiry, expiry > Date() {
                        Text(code).font(.title.monospaced()).textSelection(.enabled)
                        Text("Expires \(expiry, style: .relative)").font(.footnote)
                        Button("Copy Enrollment Code") { UIPasteboard.general.setItems([[UIPasteboard.typeAutomatic: code]], options: [.localOnly: true, .expirationDate: expiry]) }
                        if let url = connection.server?.endpoint("display/enroll") { ShareLink(item: "OpenFotoFrame display setup\n\(url.absoluteString)\nEnrollment code: \(code)\nExpires in two minutes.") { Label("Share Display Setup", systemImage: "square.and.arrow.up") } }
                    }
                    Button("Create New Enrollment Code") { Task { await enroll(scope: scope, frame: frame) } }
                    Button("Revoke Display Credentials", role: .destructive) { confirmRevoke = true }
                }
            }
            if let failure { Text(failure).foregroundStyle(.secondary) }
        }
        .navigationTitle("Set Up Display")
        .task(id: expiry) {
            guard let expiry else { return }
            do { try await Task.sleep(for: .seconds(max(0, expiry.timeIntervalSinceNow))) }
            catch { return }
            code = nil
        }
        .onDisappear { code = nil; expiry = nil }
        .confirmationDialog("Disconnect this physical display?", isPresented: $confirmRevoke, titleVisibility: .visible) {
            Button("Revoke Display Credentials", role: .destructive) { Task { await revoke() } }
        }
    }
    private func enroll(scope: ReleaseScope, frame: ReleaseFrame) async {
        do {
            struct Enrollment: Decodable { let enrollmentToken: String; let expiresIn: Double }
            let result: Enrollment = try await connection.json("api/v1/households/\(scope.household.id)/frames/\(frame.id)/enrollments", method: "POST", body: [:])
            code = result.enrollmentToken; expiry = Date().addingTimeInterval(result.expiresIn); failure = nil
        } catch { failure = error.localizedDescription }
    }
    private func revoke() async {
        guard let scope = connection.scope, let frame = scope.frame else { return }
        do {
            let _: ReleaseAcknowledgement = try await connection.json("api/v1/households/\(scope.household.id)/frames/\(frame.id)/credentials", method: "DELETE")
            code = nil; expiry = nil; failure = nil
        } catch { failure = error.localizedDescription }
    }
}
