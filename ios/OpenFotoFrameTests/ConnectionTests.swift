import XCTest
import WebKit
#if DEV_APP
@testable import OpenFotoFrameDev
#else
@testable import OpenFotoFrame
#endif

final class ConnectionTests: XCTestCase {
    func testDevelopmentIdentityAndCallbackIsolation() throws {
        let production = AppConfiguration(bundleIdentifier: "tech.swagner.openfotoframe", info: ["FrameBuildEnvironment": "production", "FrameSuggestedServerURL": "https://private.example"])
        let dev = AppConfiguration(bundleIdentifier: "tech.swagner.openfotoframe.dev", info: ["FrameBuildEnvironment": "development", "FrameSuggestedServerURL": "https://DEV.example:443"])
        XCTAssertEqual(production.keychainService, "tech.swagner.openfotoframe.device-sessions")
        XCTAssertNotEqual(production.keychainService, dev.keychainService)
        XCTAssertEqual(production.urlScheme, "openfotoframe")
        XCTAssertEqual(dev.urlScheme, "openfotoframe-dev")
        XCTAssertEqual(production.suggestedServer, "")
        XCTAssertEqual(dev.suggestedServer, "https://dev.example")
        let proof = try BrowserAuthorizationProof()
        let callback = URL(string: dev.callbackURI + "?code=once&state=\(proof.state)&iss=https%3A%2F%2Fdev.example")!
        XCTAssertEqual(try proof.code(from: callback, issuer: "https://dev.example", scheme: dev.urlScheme), "once")
        XCTAssertThrowsError(try proof.code(from: callback, issuer: "https://dev.example", scheme: production.urlScheme))
        let types = try XCTUnwrap(Bundle.main.object(forInfoDictionaryKey: "CFBundleURLTypes") as? [[String: Any]])
        XCTAssertEqual(types.flatMap { $0["CFBundleURLSchemes"] as? [String] ?? [] }, [AppConfiguration.current.urlScheme])
    }

    func testDevelopmentCredentialRemovalCannotRemoveProductionNamespace() throws {
        let prefix = "fixture.identity." + UUID().uuidString
        let prod = KeychainCredentialVault(configuration: .init(bundleIdentifier: prefix, info: [:]))
        let dev = KeychainCredentialVault(configuration: .init(bundleIdentifier: prefix + ".dev", info: ["FrameBuildEnvironment": "development"]))
        let key = "same-origin-and-account"
        defer { try? prod.remove(key: key); try? dev.remove(key: key) }
        let value = DeviceCredentials(origin: "https://frame.example.test", serverID: "s", accountID: "a", accessToken: "fixture-access", refreshToken: "fixture-refresh", expiresAt: .distantFuture, sessionID: "session")
        try prod.write(value, key: key)
        XCTAssertNil(try dev.read(key: key))
        try dev.write(value, key: key)
        try dev.remove(key: key)
        XCTAssertEqual(try prod.read(key: key), value)
    }

    func testRetainedPhotoCannotRestoreAfterAnyPurgeRequest() {
        let untouched = RetainedPhoto(id: "a", filename: "file.jpg", title: "", byteSize: 10, purge: nil)
        XCTAssertTrue(untouched.canRestore); XCTAssertTrue(untouched.canRequestRemoval)
        XCTAssertEqual(untouched.name, "file.jpg")
        let failed = RetainedPhoto(id: "a", filename: "file.jpg", title: "Photo", byteSize: 10, purge: .init(id: "p", state: "failed"))
        XCTAssertFalse(failed.canRestore); XCTAssertTrue(failed.canRequestRemoval)
        let queued = RetainedPhoto(id: "a", filename: "file.jpg", title: nil, byteSize: 10, purge: .init(id: "p", state: "queued"))
        XCTAssertFalse(queued.canRestore); XCTAssertFalse(queued.canRequestRemoval)
    }

    func testHouseholdStorageStatusAndUnknownSelfHostedLimits() throws {
        let usage = HouseholdStorage.Usage(activeBytes: 60, retainedDeletedBytes: 40, totalBytes: 100, activeAssets: 2, frames: 1)
        let full = HouseholdStorage(householdId: "h", deploymentMode: "hosted", accounting: "original_bytes", usage: usage, entitlement: .init(active: true, status: "active", planId: "configured", validUntil: nil), limits: .init(storageBytes: 100, assetLimit: 5, frameLimit: 2), remaining: .init(storageBytes: 0, assets: 3, frames: 1))
        XCTAssertTrue(full.full)
        XCTAssertEqual(full.uploadStatus, "Household storage is full.")
        let selfHosted = try FrameJSON.decode(HouseholdStorage.self, from: Data(#"{"household_id":"h","deployment_mode":"self_hosted","accounting":"original_bytes","usage":{"active_bytes":60,"retained_deleted_bytes":40,"total_bytes":100,"active_assets":2,"frames":1},"entitlement":{"active":false,"status":"unconfigured","plan_id":null,"valid_until":null},"limits":null,"remaining":null}"#.utf8))
        XCTAssertFalse(selfHosted.full)
        XCTAssertEqual(selfHosted.uploadStatus, "Storage is managed by your server.")
        XCTAssertNil(selfHosted.limits)
        XCTAssertFalse(ReleaseHTTPFailure(status: 409, code: "storage_quota_exceeded", message: "hidden original message").localizedDescription.contains("hidden original message"))
        XCTAssertTrue(ReleaseHTTPFailure(status: 402, code: "subscription_required", message: "inactive").localizedDescription.contains("household owner"))
    }

    @MainActor
    func testPartialCollectionMemberNeverRequestsHouseholdStorageTotals() async throws {
        let name = "StorageTests-" + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let transport = FakeTransport(server: try FrameServer("https://frame.example.test"))
        let connection = ReleaseSession(vault: MemoryVault(), defaults: defaults, transportFactory: { _ in transport })
        await connection.connect(mode: .selfHosted, address: transport.server.origin)
        await connection.login(email: "person@example.com", password: "fixture")
        connection.selectScope(household: .init(id: "h", name: "Shared", role: "member"), collection: .init(id: "c", name: "Selected", permission: "contributor"), frame: nil)
        do { _ = try await connection.householdStorage(); XCTFail("A member must not request aggregate statistics") }
        catch { XCTAssertTrue(error.localizedDescription.contains("owners and administrators")) }
        XCTAssertFalse(transport.requests.contains { $0.url?.path.hasSuffix("/storage") == true })
    }

    func testBridgeRejectsForeignRedirectCookiesAndInsecureSession() throws {
        let server = try FrameServer("https://frame.example.com")
        func cookies(_ domain: String = "frame.example.com", secure: Bool = true) -> [HTTPCookie] {
            ["release_session", "release_csrf", "release_context"].map { name in
                var properties: [HTTPCookiePropertyKey: Any] = [.name: name, .value: "fixture", .domain: domain, .path: "/"]
                if secure { properties[.secure] = "TRUE" }
                if name != "release_csrf" { properties[HTTPCookiePropertyKey("HttpOnly")] = "TRUE" }
                return HTTPCookie(properties: properties)!
            }
        }
        XCTAssertEqual(try BridgeCookieRules.validate(cookies(), server: server, location: "/upload", path: "upload").count, 3)
        XCTAssertThrowsError(try BridgeCookieRules.validate(cookies(), server: server, location: "https://evil.example/upload", path: "upload"))
        XCTAssertThrowsError(try BridgeCookieRules.validate(cookies("example.com"), server: server, location: "/upload", path: "upload"))
        XCTAssertThrowsError(try BridgeCookieRules.validate(cookies(".frame.example.com"), server: server, location: "/upload", path: "upload"))
        XCTAssertThrowsError(try BridgeCookieRules.validate(cookies(secure: false), server: server, location: "/upload", path: "upload"))
    }
    func testAppleProofDestinationAndAccountBinding() throws {
        XCTAssertFalse(StoreKitPurchaseRules.permits(origin: "https://local.example", configuredHostedOrigin: "https://service.example"))
        XCTAssertFalse(StoreKitPurchaseRules.permits(origin: "http://service.example", configuredHostedOrigin: "https://service.example"))
        XCTAssertFalse(StoreKitPurchaseRules.permits(origin: "https://service.example", configuredHostedOrigin: nil))
        XCTAssertTrue(StoreKitPurchaseRules.permits(origin: "https://SERVICE.example:443", configuredHostedOrigin: "https://service.example"))
        let token = UUID()
        let intent = ApplePurchaseIntent(appAccountToken: token.uuidString, productId: "configured.sku", planId: "plan", checkoutReference: "ref")
        XCTAssertEqual(try StoreKitPurchaseRules.accountToken(intent, expectedProduct: "configured.sku"), token)
        XCTAssertThrowsError(try StoreKitPurchaseRules.accountToken(intent, expectedProduct: "foreign.sku"))
        XCTAssertThrowsError(try StoreKitPurchaseRules.accountToken(ApplePurchaseIntent(appAccountToken: "bad", productId: "configured.sku", planId: "plan", checkoutReference: "ref"), expectedProduct: "configured.sku"))
    }
    func testBrowserAuthorizationBindsStateIssuerAndSingleCode() throws {
        let proof = try BrowserAuthorizationProof()
        XCTAssertEqual(proof.verifier.count, 43); XCTAssertEqual(proof.challenge.count, 43)
        let callback = "\(AppConfiguration.current.urlScheme)://auth/callback?code=once&state=\(proof.state)&iss=https%3A%2F%2Fserver.example"
        XCTAssertEqual(try proof.code(from: URL(string: callback)!, issuer: "https://server.example"), "once")
        XCTAssertThrowsError(try proof.code(from: URL(string: callback)!, issuer: "https://foreign.example"))
        XCTAssertThrowsError(try proof.code(from: URL(string: callback + "&code=duplicate")!, issuer: "https://server.example"))
        XCTAssertThrowsError(try proof.code(from: URL(string: callback.replacingOccurrences(of: proof.state, with: "wrong"))!, issuer: "https://server.example"))
    }
    func testFragmentOnlyLinksRoundTripWithoutSecretsInRequestURL() throws {
        let server = try FrameServer("https://FRAME.example.com:443")
        let landing = URL(string: "https://invite.example.com")!
        let link = ConnectionLink.url(purpose: .invite, mode: .selfHosted, server: server, secret: "sensitive+token&value", landingURL: landing)
        XCTAssertNil(URLComponents(url: link, resolvingAgainstBaseURL: false)?.query)
        XCTAssertEqual(link.path, "/connect")
        let parsed = try ConnectionLink(link.absoluteString, permittedHTTPSHosts: ["invite.example.com"])
        XCTAssertEqual(parsed.server.origin, "https://frame.example.com")
        XCTAssertEqual(parsed.secret, "sensitive+token&value")
        XCTAssertEqual(parsed.purpose, .invite)
        let custom = ConnectionLink.url(purpose: .pair, mode: .hosted, server: server, reference: "pair-id", secret: "short-code")
        XCTAssertEqual(try ConnectionLink(custom.absoluteString, permittedHTTPSHosts: []).reference, "pair-id")
    }

    func testLinksRejectForeignLandingHostDuplicateFieldsAndCredentialedOrigins() throws {
        let valid = "https://invite.example.com/connect#v=1&purpose=invite&mode=self_hosted&server=https%3A%2F%2Fframe.example.com&token=secret"
        XCTAssertThrowsError(try ConnectionLink(valid, permittedHTTPSHosts: ["other.example.com"]))
        XCTAssertThrowsError(try ConnectionLink(valid + "&server=https%3A%2F%2Fevil.example.com", permittedHTTPSHosts: ["invite.example.com"]))
        XCTAssertThrowsError(try ConnectionLink(valid.replacingOccurrences(of: "frame.example.com", with: "user%3Apassword%40frame.example.com"), permittedHTTPSHosts: ["invite.example.com"]))
        XCTAssertThrowsError(try ConnectionLink(valid.replacingOccurrences(of: "/connect#", with: "/connect?"), permittedHTTPSHosts: ["invite.example.com"]))
        XCTAssertThrowsError(try ConnectionLink(valid.replacingOccurrences(of: "purpose=invite", with: "purpose=setup"), permittedHTTPSHosts: ["invite.example.com"]))
        XCTAssertThrowsError(try ConnectionLink(valid + "&role=owner", permittedHTTPSHosts: ["invite.example.com"]))
    }

    func testScopedGalleryAcceptsEpochAndLegacyStringDates() throws {
        let epoch = Data(#"{"images":[{"filename":"opaque-asset","size":1024,"enabled":true,"uploaded_at":1700000000}],"groups":{}}"#.utf8)
        XCTAssertEqual(try FrameJSON.decode(Gallery.self, from: epoch).images.first?.uploadedAt, "2023-11-14T22:13:20Z")
        let legacy = Data(#"{"images":[{"filename":"legacy.jpg","size":1024,"enabled":true,"uploaded_at":"2023-11-14T22:13:20"}],"groups":{}}"#.utf8)
        XCTAssertEqual(try FrameJSON.decode(Gallery.self, from: legacy).images.first?.uploadedAt, "2023-11-14T22:13:20")
    }

    func testSafePathsAndCanonicalCredentialKeys() throws {
        let server = try FrameServer("https://FRAME.example.com:443/")
        XCTAssertEqual(server.origin, "https://frame.example.com")
        XCTAssertEqual(try server.scopedEndpoint("api/v1/server").absoluteString, "https://frame.example.com/api/v1/server")
        for path in ["https://evil.example.com", "//evil.example.com", "../secret", "%2e%2e/secret", "api/../../secret", "api\\evil"] {
            XCTAssertThrowsError(try server.scopedEndpoint(path), path)
        }
        var first = ConnectionProfile(mode: .selfHosted, server: server)
        first.accountID = "same-user"; first.serverID = "first-server"
        var second = ConnectionProfile(mode: .selfHosted, server: try FrameServer("https://other.example.com"))
        second.accountID = "same-user"; second.serverID = "second-server"
        XCTAssertNotEqual(first.credentialKey, second.credentialKey)
        let credentials = DeviceCredentials(origin: first.origin, serverID: "first-server", accountID: "same-user", accessToken: "access", refreshToken: "refresh", expiresAt: .distantFuture, sessionID: "device")
        XCTAssertTrue(credentials.matches(first))
        XCTAssertFalse(credentials.matches(second))
        first.serverID = "replaced-server"
        XCTAssertFalse(credentials.matches(first))
    }

    func testLegacyMigrationRetainsOnlyServerMetadataAndIsIdempotent() throws {
        let name = "ConnectionTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        defaults.set("https://frame.example.com", forKey: "frameServer")
        let store = ConnectionProfileStore(defaults: defaults)
        let first = try store.migrateLegacyServer()
        XCTAssertEqual(first.count, 1)
        XCTAssertTrue(first[0].legacy)
        XCTAssertNil(first[0].accountID)
        XCTAssertEqual(try store.migrateLegacyServer(), first)
        let serialized = String(decoding: try JSONEncoder().encode(first), as: UTF8.self)
        XCTAssertFalse(serialized.contains("password"))
        XCTAssertFalse(serialized.contains("accessToken"))
        XCTAssertFalse(serialized.contains("refreshToken"))
    }

    func testKeychainRoundTripAndRemoval() throws {
        let vault = KeychainCredentialVault()
        let key = "native-test-\(UUID().uuidString)"
        defer { try? vault.remove(key: key) }
        let credentials = DeviceCredentials(origin: "https://frame.example.test", serverID: "server", accountID: "account", accessToken: "fixture-access", refreshToken: "fixture-refresh", expiresAt: Date(timeIntervalSince1970: 2_000_000_000), sessionID: "device")
        try vault.write(credentials, key: key)
        XCTAssertEqual(try vault.read(key: key), credentials)
        var updated = credentials
        updated.accessToken = "rotated-access"
        try vault.write(updated, key: key)
        XCTAssertEqual(try vault.read(key: key)?.accessToken, "rotated-access")
        try vault.remove(key: key)
        XCTAssertNil(try vault.read(key: key))
    }

    @MainActor
    func testNewOriginNeverReceivesPreviousCredentialsAndMetadataStaysSecretFree() async throws {
        let name = "ConnectionTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let vault = MemoryVault()
        var transports: [FakeTransport] = []
        let connection = ReleaseSession(vault: vault, defaults: defaults) { server in
            let transport = FakeTransport(server: server)
            transports.append(transport)
            return transport
        }
        await connection.connect(mode: .selfHosted, address: "https://first.example.com")
        await connection.login(email: "person@example.com", password: "temporary-password")
        XCTAssertTrue(connection.authenticated)
        XCTAssertEqual(vault.values.count, 1)
        connection.returnToConnections()
        await connection.connect(mode: .selfHosted, address: "https://second.example.com")
        XCTAssertFalse(connection.authenticated)
        XCTAssertTrue(transports[0].invalidated)
        XCTAssertTrue(transports[1].requests.allSatisfy { $0.value(forHTTPHeaderField: "Authorization") == nil })
        await connection.login(email: "person@example.com", password: "different-password")
        XCTAssertEqual(vault.values.count, 2)
        let metadata = String(decoding: defaults.data(forKey: "connectionProfiles")!, as: UTF8.self)
        for secret in ["access-secret", "refresh-secret", "temporary-password", "different-password"] { XCTAssertFalse(metadata.contains(secret)) }
    }

    @MainActor
    func testConnectionSwitchDiscardsDelayedPreviousServerResponse() async throws {
        let name = "ConnectionTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let connection = ReleaseSession(vault: MemoryVault(), defaults: defaults) { server in
            let transport = FakeTransport(server: server)
            transport.serverDelay = server.url.host == "slow.example.com" ? 150_000_000 : 0
            return transport
        }
        let old = Task { await connection.connect(mode: .selfHosted, address: "https://slow.example.com") }
        try await Task.sleep(nanoseconds: 20_000_000)
        await connection.connect(mode: .selfHosted, address: "https://current.example.com")
        await old.value
        XCTAssertEqual(connection.profile?.origin, "https://current.example.com")
        XCTAssertEqual(connection.serverInfo?.serverId, "current.example.com")
        XCTAssertNil(connection.error)
    }

    @MainActor
    func testMFAEnrollmentAndPasswordGateDoNotInstallPartialCredentials() async throws {
        let name = "ConnectionTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let vault = MemoryVault()
        let transport = FakeTransport(server: try FrameServer("https://frame.example.test"))
        transport.authResponses = [
            ("/api/v1/auth/login", 202, ["mfa_enrollment_required": true, "challenge_token": "enrollment-challenge"]),
            ("/api/v1/auth/mfa/enroll/complete", 200, ["password_change_required": true, "challenge_token": "password-challenge", "recovery_codes": ["save-once"]])
        ]
        let connection = ReleaseSession(vault: vault, defaults: defaults, transportFactory: { _ in transport })
        await connection.connect(mode: .selfHosted, address: transport.server.origin)
        await connection.login(email: "person@example.com", password: "fixture-password")
        XCTAssertEqual(connection.enrollmentToken, "enrollment-challenge")
        XCTAssertFalse(connection.authenticated)
        XCTAssertTrue(vault.values.isEmpty)
        connection.mfaSetup = ReleaseMFASetup(setupToken: "setup", secret: "fixture-only", provisioningUri: "otpauth://totp/fixture")
        await connection.completeMFAEnrollment("123456")
        XCTAssertEqual(connection.passwordChangeToken, "password-challenge")
        XCTAssertEqual(connection.recoveryCodes, ["save-once"])
        XCTAssertFalse(connection.authenticated)
        XCTAssertTrue(vault.values.isEmpty)
        await connection.changeRequiredPassword("new-fixture-password")
        XCTAssertTrue(connection.authenticated)
        XCTAssertEqual(vault.values.count, 1)
        XCTAssertNil(connection.passwordChangeToken)
        connection.returnToConnections()
        XCTAssertTrue(connection.recoveryCodes.isEmpty)
    }

    @MainActor
    func testSwitchCancelsBridgeBeforeRedeemingOrInstallingCookies() async throws {
        let name = "ConnectionTests-" + UUID().uuidString
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let transport = FakeTransport(server: try FrameServer("https://frame.example.test"))
        let connection = ReleaseSession(vault: MemoryVault(), defaults: defaults, transportFactory: { _ in transport })
        await connection.connect(mode: .selfHosted, address: transport.server.origin)
        await connection.login(email: "person@example.com", password: "fixture-password")
        connection.selectScope(household: ReleaseHousehold(id: "h", name: "Fixture", role: "owner"), collection: ReleaseCollection(id: "c", name: "Fixture", permission: "owner"), frame: nil)
        transport.delayedPath = "/api/v1/auth/web-bridge"
        transport.authResponses = [("/api/v1/auth/web-bridge", 200, ["bridge_token": "one-use-fixture", "expires_in": 30])]
        let oldStore = connection.websiteData
        let task = Task { try await connection.bridgeRequest(path: "upload") }
        try await Task.sleep(nanoseconds: 20_000_000)
        connection.returnToConnections()
        do { _ = try await task.value; XCTFail("A stale bridge must not be redeemed") }
        catch is CancellationError { }
        XCTAssertFalse(transport.requests.contains { $0.url?.path == "/api/v1/auth/web-bridge/redeem" })
        let oldCookies: [HTTPCookie] = await withCheckedContinuation { continuation in oldStore.httpCookieStore.getAllCookies { continuation.resume(returning: $0) } }
        let newCookies: [HTTPCookie] = await withCheckedContinuation { continuation in connection.websiteData.httpCookieStore.getAllCookies { continuation.resume(returning: $0) } }
        XCTAssertTrue(oldCookies.isEmpty); XCTAssertTrue(newCookies.isEmpty)
    }

    @MainActor
    func testCanceledConnectionCannotInstallItsDelayedResponse() async throws {
        let name = "ConnectionTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        let connection = ReleaseSession(vault: MemoryVault(), defaults: defaults) { server in
            let transport = FakeTransport(server: server); transport.serverDelay = 100_000_000; return transport
        }
        let request = Task { await connection.connect(mode: .selfHosted, address: "https://slow.example.com") }
        try await Task.sleep(nanoseconds: 20_000_000)
        connection.returnToConnections()
        await request.value
        XCTAssertNil(connection.profile)
        XCTAssertNil(connection.serverInfo)
        XCTAssertFalse(connection.authenticated)
        XCTAssertNil(connection.error)
    }

    @MainActor
    func testHTTPRequiresExplicitConfirmationBeforeAnyNetworkRequest() async throws {
        let name = "ConnectionTests-\(UUID().uuidString)"
        let defaults = UserDefaults(suiteName: name)!
        defer { defaults.removePersistentDomain(forName: name) }
        var requests = 0
        let connection = ReleaseSession(vault: MemoryVault(), defaults: defaults) { server in requests += 1; return FakeTransport(server: server) }
        await connection.connect(mode: .selfHosted, address: "http://192.168.1.2")
        XCTAssertEqual(requests, 0)
        XCTAssertNil(connection.serverInfo)
        await connection.connect(mode: .selfHosted, address: "http://192.168.1.2", allowLocalHTTP: true)
        XCTAssertEqual(requests, 1)
        XCTAssertNotNil(connection.serverInfo)
        await connection.connect(mode: .hosted, address: "http://192.168.1.2", allowLocalHTTP: true)
        XCTAssertEqual(requests, 1)
    }
}

private final class MemoryVault: CredentialVault {
    var values: [String: DeviceCredentials] = [:]
    func read(key: String) throws -> DeviceCredentials? { values[key] }
    func write(_ value: DeviceCredentials, key: String) throws { values[key] = value }
    func remove(key: String) throws { values.removeValue(forKey: key) }
}

private final class FakeTransport: ReleaseTransport {
    let server: FrameServer
    var requests: [URLRequest] = []
    var invalidated = false
    var serverDelay: UInt64 = 0
    var delayedPath: String?
    var authResponses: [(String, Int, [String: Any])] = []
    init(server: FrameServer) { self.server = server }
    func invalidate() { invalidated = true }
    func data(for request: URLRequest) async throws -> (Data, HTTPURLResponse) {
        requests.append(request)
        if request.url?.path == delayedPath { try await Task.sleep(nanoseconds: 150_000_000) }
        if let index = authResponses.firstIndex(where: { $0.0 == request.url?.path }) {
            let response = authResponses.remove(at: index)
            return (try JSONSerialization.data(withJSONObject: response.2), HTTPURLResponse(url: request.url!, statusCode: response.1, httpVersion: nil, headerFields: nil)!)
        }
        let body: [String: Any]
        if request.url?.path == "/api/v1/server" {
            if serverDelay > 0 { try await Task.sleep(nanoseconds: serverDelay) }
            body = ["api_version": 1, "deployment_mode": "self_hosted", "server_id": server.url.host!, "capabilities": ["password_login": true], "registration_enabled": true]
        } else {
            body = ["access_token": "access-secret", "refresh_token": "refresh-secret", "expires_in": 900, "session_id": "device", "user": ["id": "same-user", "email": "person@example.com", "email_verified": true], "households": []]
        }
        return (try JSONSerialization.data(withJSONObject: body), HTTPURLResponse(url: request.url!, statusCode: 200, httpVersion: nil, headerFields: nil)!)
    }
}
