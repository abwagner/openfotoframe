import Foundation

/// Distribution identities never share URL registrations or credential services.
struct AppConfiguration {
    let bundleIdentifier: String
    let urlScheme: String
    let development: Bool
    let suggestedServer: String

    init(bundleIdentifier: String, info: [String: Any]) {
        self.bundleIdentifier = bundleIdentifier
        development = info["FrameBuildEnvironment"] as? String == "development"
        // Bind the scheme to the app identity, rather than accepting a link's scheme.
        urlScheme = development ? "openfotoframe-dev" : "openfotoframe"
        if development, let address = info["FrameSuggestedServerURL"] as? String,
           let server = try? FrameServer(address), server.url.scheme == "https" {
            suggestedServer = server.origin
        } else { suggestedServer = "" }
    }
    static var current: Self {
        .init(bundleIdentifier: Bundle.main.bundleIdentifier ?? "tech.swagner.openfotoframe",
              info: Bundle.main.infoDictionary ?? [:])
    }
    var callbackURI: String { "\(urlScheme)://auth/callback" }
    var keychainService: String { bundleIdentifier + ".device-sessions" }
}
