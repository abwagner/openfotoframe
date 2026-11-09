import AuthenticationServices
import CryptoKit
import Security
import UIKit

struct BrowserAuthorizationProof {
    let verifier: String
    let challenge: String
    let state: String
    init() throws {
        func random() throws -> String {
            var bytes = [UInt8](repeating: 0, count: 32)
            guard SecRandomCopyBytes(kSecRandomDefault, bytes.count, &bytes) == errSecSuccess else { throw FrameError.message("Could not create a secure sign-in request.") }
            return Data(bytes).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
        }
        verifier = try random(); state = try random()
        challenge = Data(SHA256.hash(data: Data(verifier.utf8))).base64EncodedString().replacingOccurrences(of: "+", with: "-").replacingOccurrences(of: "/", with: "_").replacingOccurrences(of: "=", with: "")
    }
    func code(from url: URL, issuer: String) throws -> String {
        guard url.scheme == "openfotoframe", url.host == "auth", url.path == "/callback", url.fragment == nil,
              let items = URLComponents(url: url, resolvingAgainstBaseURL: false)?.queryItems,
              Set(items.map(\.name)) == ["code", "state", "iss"], items.count == 3 else { throw FrameError.message("The sign-in callback is invalid.") }
        let values = Dictionary(uniqueKeysWithValues: items.compactMap { item in item.value.map { (item.name, $0) } })
        guard values["state"] == state, values["iss"] == issuer, let code = values["code"], !code.isEmpty else { throw FrameError.message("The sign-in callback does not match this server and request.") }
        return code
    }
}

@MainActor
final class BrowserAuthorization: NSObject, ASWebAuthenticationPresentationContextProviding {
    private var session: ASWebAuthenticationSession?
    func open(_ url: URL) async throws -> URL {
        try await withTaskCancellationHandler(operation: {
            try await withCheckedThrowingContinuation { continuation in
                let session = ASWebAuthenticationSession(url: url, callbackURLScheme: "openfotoframe") { [weak self] url, error in
                    Task { @MainActor in self?.session = nil }
                    if let url { continuation.resume(returning: url) }
                    else { continuation.resume(throwing: error ?? CancellationError()) }
                }
                self.session = session; session.presentationContextProvider = self
                session.prefersEphemeralWebBrowserSession = true
                if !session.start() { self.session = nil; continuation.resume(throwing: FrameError.message("Could not open secure browser sign-in.")) }
            }
        }, onCancel: { Task { @MainActor [weak self] in self?.session?.cancel() } })
    }
    func cancel() { session?.cancel(); session = nil }
    func presentationAnchor(for session: ASWebAuthenticationSession) -> ASPresentationAnchor {
        UIApplication.shared.connectedScenes.compactMap { $0 as? UIWindowScene }.flatMap(\.windows).first { $0.isKeyWindow } ?? ASPresentationAnchor()
    }
}
