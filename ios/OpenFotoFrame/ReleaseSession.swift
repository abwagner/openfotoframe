import Foundation
import SwiftUI
import WebKit

struct ReleaseScope: Equatable {
    let household: ReleaseHousehold
    let collection: ReleaseCollection
    var frame: ReleaseFrame?
    var id: String { "\(household.id)|\(collection.id)|\(frame?.id ?? "")" }
    var administrator: Bool { ["owner", "admin"].contains(household.role) }
}

struct ReleaseHTTPFailure: LocalizedError {
    let status: Int
    let code: String
    let message: String
    var errorDescription: String? {
        switch code {
        case "storage_quota_exceeded": return "Household storage is full. An owner or administrator can review retained deleted photos and storage. Removing photos from a collection alone may not free space."
        case "asset_quota_exceeded": return "The household’s photo limit has been reached. Ask an owner or administrator to review its photos and plan."
        case "frame_quota_exceeded": return "The household’s frame limit has been reached. Ask an owner or administrator to review its frames and plan."
        case "subscription_required": return "Uploads are paused because the hosted subscription is inactive. Ask the household owner to review it, then retry."
        default: return message
        }
    }
}

/// Each transport owns one immutable origin; redirects cannot carry a credential elsewhere.
protocol ReleaseTransport: AnyObject {
    var server: FrameServer { get }
    func invalidate()
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse)
}

final class OriginLockedTransport: NSObject, URLSessionTaskDelegate, ReleaseTransport, @unchecked Sendable {
    let server: FrameServer
    private var session: URLSession!
    init(server: FrameServer) {
        self.server = server
        super.init()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.httpCookieStorage = nil
        configuration.urlCache = nil
        configuration.timeoutIntervalForRequest = 30
        session = URLSession(configuration: configuration, delegate: self, delegateQueue: nil)
    }
    func invalidate() { session.invalidateAndCancel() }
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        guard let url = request.url, server.contains(url) else { throw FrameError.message("The request does not belong to the selected server.") }
        let (data, response) = try await session.data(for: request)
        guard let response = response as? HTTPURLResponse, let responseURL = response.url, server.contains(responseURL) else { throw FrameError.message("The selected server returned an unexpected destination.") }
        return (data, response)
    }
    func urlSession(_ session: URLSession, task: URLSessionTask, willPerformHTTPRedirection response: HTTPURLResponse,
                    newRequest request: URLRequest, completionHandler: @escaping (URLRequest?) -> Void) {
        // Auth and bridge exchanges never follow redirects, even on the same origin.
        if task.originalRequest?.url?.path.hasPrefix("/api/v1/auth/") == true { completionHandler(nil); return }
        completionHandler(request.url.map(server.contains) == true ? request : nil)
    }
}

@MainActor
final class ReleaseSession: ObservableObject {
    @Published private(set) var profiles: [ConnectionProfile] = []
    @Published private(set) var profile: ConnectionProfile?
    @Published private(set) var serverInfo: ReleaseServerInfo?
    @Published private(set) var identity: ReleaseIdentity?
    @Published private(set) var scope: ReleaseScope?
    @Published private(set) var collections: [ReleaseCollection] = []
    @Published private(set) var frames: [ReleaseFrame] = []
    @Published private(set) var busy = false
    @Published var error: String?
    @Published var challengeToken: String?
    @Published var passwordChangeToken: String?
    @Published var enrollmentToken: String?
    @Published var mfaSetup: ReleaseMFASetup?
    @Published var recoveryCodes: [String] = []
    @Published var pendingLink: ConnectionLink?
    @Published var invitation: ReleaseInvitationPreview?
    @Published var legacyRequested = false
    private(set) var websiteData = WKWebsiteDataStore.nonPersistent()
    private let vault: CredentialVault
    private let profileStore: ConnectionProfileStore
    private var transport: ReleaseTransport?
    private let transportFactory: (FrameServer) -> ReleaseTransport
    private var credentials: DeviceCredentials?
    private let browserAuthorization = BrowserAuthorization()
    private var generation = UUID()
    private var refreshTask: Task<DeviceCredentials, Error>?
    private var resourceGeneration = UUID()
    var scopeIdentity: String { "\(generation.uuidString)|\(scope?.id ?? "")" }
    var authenticated: Bool { identity != nil }
    var server: FrameServer? { profile.flatMap { try? FrameServer($0.origin) } }
    var hostedAddress: String? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: "FrameHostedServerURL") as? String,
              !value.isEmpty, !value.contains("$("), let server = try? FrameServer(value), server.url.scheme == "https" else { return nil }
        return server.origin
    }
    var landingURL: URL? {
        guard let value = Bundle.main.object(forInfoDictionaryKey: "FrameInviteURL") as? String,
              let server = try? FrameServer(value), server.url.scheme == "https" else { return nil }
        return server.url
    }
    var invitationHosts: Set<String> { Set([landingURL?.host?.lowercased()].compactMap { $0 }) }

    init(vault: CredentialVault = KeychainCredentialVault(), defaults: UserDefaults = .standard, transportFactory: @escaping (FrameServer) -> ReleaseTransport = { OriginLockedTransport(server: $0) }) {
        self.vault = vault; self.transportFactory = transportFactory; profileStore = ConnectionProfileStore(defaults: defaults)
        do { profiles = try profileStore.migrateLegacyServer() }
        catch { self.error = error.localizedDescription }
    }

    func importLink(_ value: String) {
        do { pendingLink = try ConnectionLink(value, permittedHTTPSHosts: invitationHosts) }
        catch { self.error = error.localizedDescription }
    }

    func connect(mode: HostingMode, address: String, allowLocalHTTP: Bool = false, saved: ConnectionProfile? = nil) async {
        busy = true
        defer { busy = false }
        do {
            let server = try FrameServer(address)
            guard mode != .hosted || server.url.scheme == "https" else { throw FrameError.message("OpenFotoFrame Cloud requires HTTPS.") }
            guard server.url.scheme != "http" || allowLocalHTTP else { throw FrameError.message("Confirm local HTTP before connecting. Your credentials will not be encrypted on this network.") }
            resetConnection()
            var selected = saved ?? ConnectionProfile(mode: mode, server: server, allowsLocalHTTP: allowLocalHTTP)
            selected.legacy = false
            selected.allowsLocalHTTP = allowLocalHTTP
            profile = selected
            transport = transportFactory(server)
            let context = generation
            let info: ReleaseServerInfo
            do { info = try await json("api/v1/server", authorized: false) }
            catch let failure as ReleaseHTTPFailure where failure.status == 404 {
                throw FrameError.message("This server needs an update for secure app sign-in. You can explicitly use the existing web companion below while preparing the upgrade.")
            }
            guard context == generation else { throw CancellationError() }
            guard info.apiVersion == 1, info.deploymentMode == mode else { throw FrameError.message("This server does not match the selected service type or supported API version.") }
            if let previousID = selected.serverID, previousID != info.serverId {
                if let key = selected.credentialKey { try vault.remove(key: key) }
                selected.accountID = nil
            }
            selected.serverID = info.serverId
            profile = selected; serverInfo = info
            try saveProfile(selected)
            if let key = selected.credentialKey, let savedCredentials = try vault.read(key: key), savedCredentials.matches(selected) {
                credentials = savedCredentials
                _ = try await validCredentials()
                identity = try await json("api/v1/auth/me")
            }
            error = nil
        } catch is CancellationError { }
        catch { self.error = error.localizedDescription }
    }

    func confirmLink(allowLocalHTTP: Bool) async {
        guard let link = pendingLink else { return }
        await connect(mode: link.mode, address: link.server.origin, allowLocalHTTP: allowLocalHTTP)
        guard serverInfo != nil, server?.origin == link.server.origin else { return }
        if link.purpose == .invite, let token = link.secret {
            await perform { self.invitation = try await self.json("api/v1/invitations/preview", method: "POST", body: ["token": token], authorized: false) }
        }
    }

    func browserSignIn() async {
        guard let server, server.url.scheme == "https", serverInfo?.capabilities["auth_code_pkce"] == true else { return }
        busy = true; defer { busy = false }
        let context = generation
        do {
            let proof = try BrowserAuthorizationProof()
            struct Request: Decodable { let id: String; let authorizationUrl: String }
            let request: Request = try await json("api/v1/auth/authorization-requests", method: "POST", body: ["code_challenge": proof.challenge, "redirect_uri": AppConfiguration.current.callbackURI, "state": proof.state, "device_name": UIDevice.current.name], authorized: false)
            guard context == generation, let url = URL(string: request.authorizationUrl), server.contains(url), url.scheme == "https", url.path == "/authorize/" + request.id, url.query == nil, url.fragment == nil else { throw FrameError.message("The server returned an invalid sign-in destination.") }
            let callback = try await browserAuthorization.open(url)
            guard context == generation else { throw CancellationError() }
            let code = try proof.code(from: callback, issuer: server.origin)
            let result: ReleaseAuthentication = try await json("api/v1/auth/authorization-code", method: "POST", body: ["request_id": request.id, "code": code, "code_verifier": proof.verifier], authorized: false)
            try install(result, context: context)
            if pendingLink?.purpose == .invite { try await acceptInvitation() }
            error = nil
        } catch is CancellationError { }
        catch { if context == generation { self.error = error.localizedDescription } }
    }
    func login(email: String, password: String) async {
        await authenticate("api/v1/auth/login", body: ["email": email, "password": password, "device_name": UIDevice.current.name])
    }
    func register(email: String, password: String, householdName: String) async {
        var body: [String: Any] = ["email": email, "password": password, "device_name": UIDevice.current.name, "household_name": householdName]
        if pendingLink?.purpose == .invite, let token = pendingLink?.secret { body["invitation_token"] = token }
        await authenticate("api/v1/auth/register", body: body)
    }
    func bootstrap(secret: String, email: String, password: String, householdName: String) async {
        await authenticate("api/v1/auth/bootstrap", body: ["bootstrap_secret": secret, "email": email, "password": password, "household_name": householdName, "device_name": UIDevice.current.name])
    }
    func verifyMFA(_ code: String) async {
        guard let challengeToken else { return }
        await authenticate("api/v1/auth/mfa", body: ["challenge_token": challengeToken, "code": code])
    }
    func beginMFAEnrollment() async {
        await perform {
            let body: [String: Any] = enrollmentToken.map { ["challenge_token": $0] } ?? [:]
            mfaSetup = try await json("api/v1/auth/mfa/enroll/start", method: "POST", body: body, authorized: enrollmentToken == nil)
        }
    }
    func completeMFAEnrollment(_ code: String) async {
        guard let mfaSetup else { return }
        await authenticate("api/v1/auth/mfa/enroll/complete", body: ["setup_token": mfaSetup.setupToken, "code": code], authorized: identity != nil)
    }
    func changeRequiredPassword(_ password: String) async {
        guard let passwordChangeToken else { return }
        await authenticate("api/v1/auth/change-password", body: ["challenge_token": passwordChangeToken, "password": password])
    }
    private func authenticate(_ path: String, body: [String: Any], authorized: Bool = false) async {
        busy = true; defer { busy = false }
        do {
            let context = generation
            let (data, response) = try await send(path, method: "POST", body: JSONSerialization.data(withJSONObject: body), authorized: authorized)
            guard context == generation else { throw CancellationError() }
            struct Recovery: Decodable { let recoveryCodes: [String]? }
            if let codes = try? FrameJSON.decode(Recovery.self, from: data).recoveryCodes { recoveryCodes = codes }
            if let challenge = try? FrameJSON.decode(ReleaseChallenge.self, from: data),
               challenge.mfaRequired == true || challenge.passwordChangeRequired == true || challenge.mfaEnrollmentRequired == true {
                if challenge.mfaEnrollmentRequired == true { enrollmentToken = challenge.challengeToken; passwordChangeToken = nil; challengeToken = nil }
                else if challenge.passwordChangeRequired == true { passwordChangeToken = challenge.challengeToken; challengeToken = nil; enrollmentToken = nil }
                else { challengeToken = challenge.challengeToken; passwordChangeToken = nil; enrollmentToken = nil }
                return
            }
            guard response.statusCode != 202 else { throw FrameError.message("This server requires an unsupported sign-in step. Update the app or contact the server operator.") }
            try install(try FrameJSON.decode(ReleaseAuthentication.self, from: data), context: context)
            if pendingLink?.purpose == .invite { try await acceptInvitation() }
            error = nil
        } catch is CancellationError { }
        catch { self.error = error.localizedDescription }
    }
    private func install(_ result: ReleaseAuthentication, context: UUID) throws {
        guard context == generation, var profile, let info = serverInfo else { throw CancellationError() }
        profile.accountID = result.user.id; profile.serverID = info.serverId
        let credentials = DeviceCredentials(origin: profile.origin, serverID: info.serverId, accountID: result.user.id,
            accessToken: result.accessToken, refreshToken: result.refreshToken, expiresAt: Date().addingTimeInterval(result.expiresIn), sessionID: result.sessionId)
        guard let key = profile.credentialKey else { throw CancellationError() }
        try vault.write(credentials, key: key)
        self.profile = profile; self.credentials = credentials
        identity = ReleaseIdentity(user: result.user, households: result.households, sessionId: result.sessionId)
        challengeToken = nil; passwordChangeToken = nil; enrollmentToken = nil; mfaSetup = nil
        try saveProfile(profile)
    }
    func acceptInvitation() async throws {
        guard pendingLink?.purpose == .invite, let token = pendingLink?.secret else { return }
        let _: [String: String] = try await json("api/v1/invitations/accept", method: "POST", body: ["token": token])
        identity = try await json("api/v1/auth/me")
        pendingLink = nil; invitation = nil
    }
    func exchangePairing(id: String, challenge: ReleasePairingChallenge) async throws -> Bool {
        let context = generation
        do {
            let result: ReleaseAuthentication = try await json("api/v1/pairings/\(id)/exchange", method: "POST", body: ["challenge_id": challenge.challengeId, "challenge_secret": challenge.challengeSecret], authorized: false)
            try install(result, context: context)
            pendingLink = nil
            return true
        } catch let failure as ReleaseHTTPFailure where failure.status == 409 { return false }
    }
    func refreshIdentity() async throws { identity = try await json("api/v1/auth/me") }
    func loadResources(_ household: ReleaseHousehold) async {
        let operation = UUID()
        resourceGeneration = operation
        await perform {
            let context = self.generation
            struct Collections: Decodable { let collections: [ReleaseCollection] }
            struct Frames: Decodable { let frames: [ReleaseFrame] }
            let collectionResult: Collections = try await self.json("api/v1/households/\(household.id)/collections")
            let frameResult: Frames = try await self.json("api/v1/households/\(household.id)/frames")
            guard context == self.generation, operation == self.resourceGeneration else { throw CancellationError() }
            self.collections = collectionResult.collections; self.frames = frameResult.frames
        }
    }
    func selectScope(household: ReleaseHousehold, collection: ReleaseCollection, frame: ReleaseFrame?) {
        generation = UUID()
        websiteData = .nonPersistent()
        scope = ReleaseScope(household: household, collection: collection, frame: frame)
    }
    func changeScope() { generation = UUID(); scope = nil; collections = []; frames = []; websiteData = .nonPersistent() }
    func returnToConnections() { resetConnection(); profile = nil; pendingLink = nil; error = nil }
    func openLegacy() { legacyRequested = true }
    func closeLegacy() { legacyRequested = false }
    func removeProfile(_ value: ConnectionProfile) {
        do {
            if let key = value.credentialKey { try vault.remove(key: key) }
            profiles.removeAll { $0.id == value.id }
            try profileStore.save(profiles)
            if profile?.id == value.id { returnToConnections() }
        } catch { self.error = error.localizedDescription }
    }
    func signOut() async {
        let oldProfile = profile
        _ = try? await send("api/v1/auth/logout", method: "POST", body: Data("{}".utf8))
        do { if let key = oldProfile?.credentialKey { try vault.remove(key: key) } }
        catch { self.error = error.localizedDescription }
        resetConnection()
        if var oldProfile { oldProfile.accountID = nil; try? saveProfile(oldProfile) }
        profile = nil
    }
    func perform(_ action: () async throws -> Void) async {
        busy = true; defer { busy = false }
        do { try await action(); error = nil }
        catch is CancellationError { }
        catch { self.error = error.localizedDescription }
    }
    func json<T: Decodable>(_ path: String, method: String = "GET", body: [String: Any]? = nil, authorized: Bool = true, idempotencyKey: String? = nil) async throws -> T {
        let context = generation
        let data = try body.map { try JSONSerialization.data(withJSONObject: $0) }
        let response = try await send(path, method: method, body: data, authorized: authorized, idempotencyKey: idempotencyKey).0
        guard context == generation else { throw CancellationError() }
        return try FrameJSON.decode(T.self, from: response)
    }
    func sendLegacy(_ path: String, method: String = "GET", body: Data? = nil, contentType: String? = nil) async throws -> Data {
        guard scope != nil else { throw FrameError.message("Choose a collection first.") }
        return try await send(path, method: method, body: body, contentType: contentType).0
    }
    func householdStorage() async throws -> HouseholdStorage {
        guard let scope, scope.administrator else { throw FrameError.message("Only household owners and administrators can view aggregate storage.") }
        let result: HouseholdStorage = try await json("api/v1/households/\(scope.household.id)/storage")
        guard result.householdId == scope.household.id else { throw FrameError.message("The storage response does not match this household.") }
        return result
    }
    func bridgeRequest(path: String) async throws -> URLRequest {
        guard let scope, let server else { throw FrameError.signedOut }
        let context = scopeIdentity
        let store = websiteData
        _ = try server.scopedEndpoint(path)
        let bridge: ReleaseWebBridge = try await json("api/v1/auth/web-bridge", method: "POST", body: ["household_id": scope.household.id, "collection_id": scope.collection.id, "frame_id": scope.frame?.id as Any? ?? NSNull(), "path": "/" + path])
        let (_, response) = try await send("api/v1/auth/web-bridge/redeem", method: "POST", body: JSONSerialization.data(withJSONObject: ["bridge_token": bridge.bridgeToken]), authorized: false)
        guard context == scopeIdentity, response.statusCode == 303,
              response.value(forHTTPHeaderField: "Location") == "/" + path else { throw FrameError.message("The server returned an unexpected advanced editor destination.") }
        let fields = response.allHeaderFields.reduce(into: [String: String]()) { result, pair in if let key = pair.key as? String { result[key] = String(describing: pair.value) } }
        let cookies = HTTPCookie.cookies(withResponseHeaderFields: fields, for: server.url)
        let scoped = try BridgeCookieRules.validate(cookies, server: server, location: response.value(forHTTPHeaderField: "Location"), path: path)
        for cookie in scoped {
            guard context == scopeIdentity, !Task.isCancelled else { throw CancellationError() }
            await store.httpCookieStore.setCookie(cookie)
        }
        guard context == scopeIdentity, !Task.isCancelled else { throw CancellationError() }
        return URLRequest(url: try server.scopedEndpoint(path))
    }
    private func send(_ path: String, method: String = "GET", body: Data? = nil, contentType: String? = nil, authorized: Bool = true, retryExpired: Bool = true, idempotencyKey: String? = nil) async throws -> (Data, HTTPURLResponse) {
        guard let transport, let server else { throw FrameError.message("Choose a server first.") }
        let context = generation
        var request = URLRequest(url: try server.scopedEndpoint(path))
        request.httpMethod = method; request.httpBody = body
        request.setValue(contentType ?? "application/json", forHTTPHeaderField: "Content-Type")
        request.setValue("application/json", forHTTPHeaderField: "Accept")
        request.setValue(idempotencyKey, forHTTPHeaderField: "Idempotency-Key")
        if authorized {
            let value = try await validCredentials()
            guard context == generation else { throw CancellationError() }
            request.setValue("Bearer \(value.accessToken)", forHTTPHeaderField: "Authorization")
            if let scope {
                request.setValue(scope.household.id, forHTTPHeaderField: "X-OpenFotoFrame-Household")
                request.setValue(scope.collection.id, forHTTPHeaderField: "X-OpenFotoFrame-Collection")
                request.setValue(scope.frame?.id, forHTTPHeaderField: "X-OpenFotoFrame-Frame")
            }
        }
        let data: Data
        let response: HTTPURLResponse
        do { (data, response) = try await transport.data(for: request) }
        catch {
            guard context == generation else { throw CancellationError() }
            throw error
        }
        guard context == generation, !Task.isCancelled else { throw CancellationError() }
        if response.statusCode == 401 && authorized {
            if retryExpired, credentials != nil {
                credentials?.expiresAt = .distantPast
                _ = try await validCredentials()
                return try await send(path, method: method, body: body, contentType: contentType, authorized: true, retryExpired: false, idempotencyKey: idempotencyKey)
            }
            if let key = profile?.credentialKey { try? vault.remove(key: key) }
            credentials = nil; identity = nil; scope = nil
            throw FrameError.signedOut
        }
        guard (200..<300).contains(response.statusCode) || (path == "api/v1/auth/web-bridge/redeem" && response.statusCode == 303) else {
            struct Failure: Decodable { struct Detail: Decodable { let code: String; let message: String }; let error: Detail }
            let failure = try? JSONDecoder().decode(Failure.self, from: data)
            throw ReleaseHTTPFailure(status: response.statusCode, code: failure?.error.code ?? "http_error", message: failure?.error.message ?? "The server returned HTTP \(response.statusCode). Please try again.")
        }
        return (data, response)
    }
    private func validCredentials() async throws -> DeviceCredentials {
        guard let value = credentials, let profile, value.matches(profile) else { throw FrameError.signedOut }
        if value.expiresAt.timeIntervalSinceNow > 30 { return value }
        if let refreshTask { return try await refreshTask.value }
        let context = generation
        let task = Task { @MainActor in
            let result: ReleaseAuthentication = try await self.json("api/v1/auth/refresh", method: "POST", body: ["refresh_token": value.refreshToken], authorized: false)
            try self.install(result, context: context)
            guard let credentials = self.credentials else { throw FrameError.signedOut }
            return credentials
        }
        refreshTask = task
        defer { refreshTask = nil }
        do { return try await task.value }
        catch let failure as ReleaseHTTPFailure where failure.status == 401 {
            if context == generation {
                if let key = profile.credentialKey { try? vault.remove(key: key) }
                credentials = nil; identity = nil; scope = nil
            }
            throw FrameError.signedOut
        }
    }
    private func saveProfile(_ value: ConnectionProfile) throws {
        if let index = profiles.firstIndex(where: { $0.id == value.id }) { profiles[index] = value }
        else { profiles.append(value) }
        try profileStore.save(profiles)
    }
    private func resetConnection() {
        browserAuthorization.cancel(); generation = UUID(); resourceGeneration = UUID(); refreshTask?.cancel(); refreshTask = nil
        transport?.invalidate(); transport = nil; credentials = nil; identity = nil; serverInfo = nil
        scope = nil; collections = []; frames = []; challengeToken = nil; passwordChangeToken = nil; enrollmentToken = nil; mfaSetup = nil; recoveryCodes = []; invitation = nil
        websiteData = .nonPersistent()
    }
}
