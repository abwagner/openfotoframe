import Foundation
import Security

enum HostingMode: String, Codable, CaseIterable {
    case hosted, selfHosted = "self_hosted"
    var title: String { self == .hosted ? "OpenFotoFrame Cloud" : "Your own server" }
}

struct ConnectionProfile: Codable, Identifiable, Equatable {
    let id: UUID
    let mode: HostingMode
    let origin: String
    var label: String
    var accountID: String?
    var serverID: String?
    var allowsLocalHTTP: Bool
    var legacy: Bool

    init(mode: HostingMode, server: FrameServer, label: String? = nil, allowsLocalHTTP: Bool = false, legacy: Bool = false) {
        id = UUID()
        self.mode = mode
        origin = server.origin
        self.label = label ?? server.url.host ?? mode.title
        self.allowsLocalHTTP = allowsLocalHTTP
        self.legacy = legacy
    }
    var credentialKey: String? { accountID.map { "\(origin)|\($0)" } }
}

struct DeviceCredentials: Codable, Equatable {
    let origin: String
    let serverID: String
    let accountID: String
    var accessToken: String
    var refreshToken: String
    var expiresAt: Date
    let sessionID: String

    func matches(_ profile: ConnectionProfile) -> Bool {
        origin == profile.origin && serverID == profile.serverID && accountID == profile.accountID
    }
}

protocol CredentialVault {
    func read(key: String) throws -> DeviceCredentials?
    func write(_ value: DeviceCredentials, key: String) throws
    func remove(key: String) throws
}

struct KeychainCredentialVault: CredentialVault {
    private let service: String
    init(configuration: AppConfiguration = .current) { service = configuration.keychainService }
    private func query(_ key: String) -> [String: Any] {
        [kSecClass as String: kSecClassGenericPassword, kSecAttrService as String: service, kSecAttrAccount as String: key]
    }
    func read(key: String) throws -> DeviceCredentials? {
        var request = query(key)
        request[kSecReturnData as String] = true
        request[kSecMatchLimit as String] = kSecMatchLimitOne
        var item: CFTypeRef?
        let status = SecItemCopyMatching(request as CFDictionary, &item)
        if status == errSecItemNotFound { return nil }
        guard status == errSecSuccess, let data = item as? Data else { throw FrameError.message("Your saved sign-in is unavailable. Unlock your device and try again.") }
        return try JSONDecoder().decode(DeviceCredentials.self, from: data)
    }
    func write(_ value: DeviceCredentials, key: String) throws {
        let data = try JSONEncoder().encode(value)
        let attributes: [String: Any] = [kSecValueData as String: data, kSecAttrAccessible as String: kSecAttrAccessibleWhenUnlockedThisDeviceOnly]
        let status = SecItemUpdate(query(key) as CFDictionary, attributes as CFDictionary)
        if status == errSecItemNotFound {
            var item = query(key)
            attributes.forEach { item[$0.key] = $0.value }
            guard SecItemAdd(item as CFDictionary, nil) == errSecSuccess else { throw FrameError.message("Could not save your device sign-in securely.") }
        } else if status != errSecSuccess { throw FrameError.message("Could not update your saved device sign-in.") }
    }
    func remove(key: String) throws {
        let status = SecItemDelete(query(key) as CFDictionary)
        guard status == errSecSuccess || status == errSecItemNotFound else { throw FrameError.message("Could not remove your device sign-in from Keychain.") }
    }
}

struct ConnectionProfileStore {
    let defaults: UserDefaults
    init(defaults: UserDefaults = .standard) { self.defaults = defaults }
    func load() -> [ConnectionProfile] {
        guard let data = defaults.data(forKey: "connectionProfiles") else { return [] }
        return (try? JSONDecoder().decode([ConnectionProfile].self, from: data)) ?? []
    }
    func save(_ profiles: [ConnectionProfile]) throws {
        defaults.set(try JSONEncoder().encode(profiles), forKey: "connectionProfiles")
    }
    func migrateLegacyServer() throws -> [ConnectionProfile] {
        var profiles = load()
        guard !defaults.bool(forKey: "legacyProfileMigrated") else { return profiles }
        if let address = defaults.string(forKey: "frameServer"), let server = try? FrameServer(address),
           !profiles.contains(where: { $0.origin == server.origin }) {
            profiles.append(ConnectionProfile(mode: .selfHosted, server: server, label: "Existing frame", allowsLocalHTTP: server.url.scheme == "http", legacy: true))
        }
        try save(profiles)
        defaults.set(true, forKey: "legacyProfileMigrated")
        return profiles
    }
}

struct ReleaseServerInfo: Decodable {
    let apiVersion: Int
    let deploymentMode: HostingMode
    let serverId: String
    let capabilities: [String: Bool]
    let registrationEnabled: Bool
    let bootstrapRequired: Bool?
}
struct ReleaseUser: Decodable { let id: String; let email: String; let emailVerified: Bool }
struct ReleaseHousehold: Decodable, Identifiable, Hashable { let id: String; let name: String; let role: String }
struct ReleaseCollection: Decodable, Identifiable, Hashable { let id: String; let name: String; let permission: String }
struct ReleaseFrame: Decodable, Identifiable, Hashable { let id: String; let name: String; let permission: String? }
struct ReleaseIdentity: Decodable { let user: ReleaseUser; let households: [ReleaseHousehold]; let sessionId: String }
struct ReleaseAuthentication: Decodable {
    let accessToken: String
    let refreshToken: String
    let expiresIn: Double
    let sessionId: String
    let user: ReleaseUser
    let households: [ReleaseHousehold]
}
struct ReleaseChallenge: Decodable { let mfaRequired: Bool?; let passwordChangeRequired: Bool?; let mfaEnrollmentRequired: Bool?; let challengeToken: String }
struct ReleaseInvitationPreview: Decodable { let householdName: String; let expiresAt: Double?; let recipientEmail: String?; let recipientRequired: Bool? }
struct ReleasePairing: Decodable { let id: String; let code: String; let expiresIn: Double }
struct ReleasePairingChallenge: Decodable { let challengeId: String; let challengeSecret: String; let confirmationCode: String }
struct ReleasePairingApproval: Decodable, Identifiable {
    var id: String { challengeId }
    let challengeId: String
    let confirmationCode: String
    let deviceName: String?
}
struct ReleasePairingStatus: Decodable { let challenges: [ReleasePairingApproval] }
struct ReleaseWebBridge: Decodable { let bridgeToken: String; let expiresIn: Double }

extension FrameServer {
    var origin: String {
        var parts = URLComponents(url: url, resolvingAgainstBaseURL: false)!
        parts.scheme = parts.scheme?.lowercased()
        parts.host = parts.host?.lowercased()
        if (parts.scheme == "https" && parts.port == 443) || (parts.scheme == "http" && parts.port == 80) { parts.port = nil }
        parts.path = ""
        return parts.string!
    }
    func scopedEndpoint(_ path: String) throws -> URL {
        guard !path.hasPrefix("/"), !path.contains("\\"), let parts = URLComponents(string: path),
              parts.scheme == nil, parts.host == nil, parts.fragment == nil,
              !parts.path.split(separator: "/", omittingEmptySubsequences: false).contains(where: { $0 == "." || $0 == ".." }),
              let result = URL(string: path, relativeTo: URL(string: origin + "/")!)?.absoluteURL,
              contains(result) else { throw FrameError.message("The server returned an unsafe destination.") }
        return result
    }
}

/// Importing a link only parses it. The caller must confirm its destination before networking.
struct ConnectionLink: Equatable, Identifiable {
    enum Purpose: String { case setup, invite, pair }
    var id: String { "\(purpose.rawValue)|\(server.origin)|\(reference ?? "")" }
    let purpose: Purpose
    let mode: HostingMode
    let server: FrameServer
    let secret: String?
    let reference: String?

    init(_ value: String, permittedHTTPSHosts: Set<String>) throws {
        guard value.utf8.count <= 8192, let url = URL(string: value), let components = URLComponents(url: url, resolvingAgainstBaseURL: false),
              components.user == nil, components.password == nil else { throw FrameError.message("This setup code is not valid.") }
        guard components.query == nil else { throw FrameError.message("Setup details must be in the link fragment.") }
        if ["openfotoframe", AppConfiguration.current.urlScheme].contains(components.scheme?.lowercased() ?? "") {
            guard components.host == "connect", components.path.isEmpty || components.path == "/" else { throw FrameError.message("This setup code is not valid.") }
        } else {
            guard components.scheme?.lowercased() == "https", let host = components.host?.lowercased(), permittedHTTPSHosts.contains(host),
                  components.port == nil || components.port == 443, components.path == "/connect" else { throw FrameError.message("This is not a supported OpenFotoFrame invitation link. Paste a server setup code instead.") }
        }
        let items = URLComponents(string: "https://unused.invalid/?" + (components.percentEncodedFragment ?? ""))?.queryItems ?? []
        var fields: [String: String] = [:]
        for item in items {
            guard ["v", "purpose", "mode", "server", "id", "token"].contains(item.name), fields[item.name] == nil, let value = item.value else { throw FrameError.message("This setup code contains unsupported or repeated fields.") }
            fields[item.name] = value
        }
        guard let purpose = Purpose(rawValue: fields["purpose"] ?? "") else { throw FrameError.message("This setup code is not supported.") }
        guard fields["v"] == "1", let mode = HostingMode(rawValue: fields["mode"] ?? ""), let address = fields["server"] else { throw FrameError.message("This setup code is missing its destination.") }
        let server = try FrameServer(address)
        guard mode != .hosted || server.url.scheme == "https" else { throw FrameError.message("OpenFotoFrame Cloud requires HTTPS.") }
        let secret = fields["token"]
        if let secret, secret.isEmpty || secret.utf8.count > 1024 { throw FrameError.message("This invitation has an invalid secret.") }
        guard purpose != .setup || (secret == nil && fields["id"] == nil),
              purpose != .invite || secret != nil,
              purpose != .pair || (secret != nil && !(fields["id"] ?? "").isEmpty) else { throw FrameError.message("This setup code is incomplete.") }
        self.purpose = purpose; self.mode = mode; self.server = server; self.secret = secret; reference = fields["id"]
    }

    static func url(purpose: Purpose, mode: HostingMode, server: FrameServer, reference: String? = nil, secret: String? = nil, landingURL: URL? = nil) -> URL {
        var parts = URLComponents()
        if let landingURL, landingURL.scheme == "https", landingURL.user == nil, landingURL.password == nil {
            parts = URLComponents(url: landingURL, resolvingAgainstBaseURL: false)!
            parts.path = "/connect"
        } else { parts.scheme = AppConfiguration.current.urlScheme; parts.host = "connect" }
        parts.query = nil
        var fragment = URLComponents()
        fragment.queryItems = [URLQueryItem(name: "v", value: "1"), URLQueryItem(name: "purpose", value: purpose.rawValue), URLQueryItem(name: "mode", value: mode.rawValue), URLQueryItem(name: "server", value: server.origin)]
        if let reference { fragment.queryItems?.append(URLQueryItem(name: "id", value: reference)) }
        if let secret { fragment.queryItems?.append(URLQueryItem(name: "token", value: secret)) }
        parts.percentEncodedFragment = fragment.percentEncodedQuery
        return parts.url!
    }
}

struct ReleasePlan: Decodable, Identifiable { let id: String; let name: String; let description: String?; let priceLabel: String?; let appleProductId: String?; let purchaseChannels: [String]? }
struct ReleaseEntitlement: Decodable { let active: Bool; let planId: String?; let status: String; let provider: String? }
struct ReleaseDeviceSession: Decodable, Identifiable { let id: String; let deviceName: String; let createdAt: Double; let revoked: Bool }

struct ReleaseMFASetup: Decodable { let setupToken: String; let secret: String; let provisioningUri: String }

/// Only the backend's scoped browser session crosses into the ephemeral web store.
enum BridgeCookieRules {
    static func validate(_ cookies: [HTTPCookie], server: FrameServer, location: String?, path: String) throws -> [HTTPCookie] {
        let names = Set(["release_session", "release_csrf", "release_context"])
        guard location == "/" + path, cookies.count == names.count, Set(cookies.map(\.name)) == names,
              cookies.allSatisfy({ cookie in cookie.domain.lowercased() == server.url.host?.lowercased() && cookie.path == "/" && (server.url.scheme != "https" || cookie.isSecure) && (cookie.name == "release_csrf" || cookie.isHTTPOnly) }) else { throw FrameError.message("The server returned an invalid advanced editor session.") }
        return cookies
    }
}
