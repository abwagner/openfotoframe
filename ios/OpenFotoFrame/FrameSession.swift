import SwiftUI
import WebKit
import ImageIO

/// WebKit owns authentication, including forced password changes and WebAuthn.
/// Native requests use that same cookie jar; no passwords are stored by the app.
@MainActor
final class FrameSession: ObservableObject {
    @Published var server: FrameServer?
    @Published var authenticated = false
    @Published var isAdmin = false
    @Published var error: String?
    @Published var gallery: Gallery?
    @Published var loading = false
    @Published private(set) var signingOut = false
    let websiteData = WKWebsiteDataStore.default()
    private var generation = UUID()
    private var session: URLSession
    private let redirects = SameOriginRedirects()

    init() {
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.urlCache = nil
        configuration.timeoutIntervalForRequest = 60
        session = URLSession(configuration: configuration, delegate: redirects, delegateQueue: nil)
        if let saved = UserDefaults.standard.string(forKey: "frameServer") {
            server = try? FrameServer(saved)
        }
        redirects.server = server
    }

    func connect(_ address: String) throws {
        let value = try FrameServer(address)
        server = value
        redirects.server = value
        UserDefaults.standard.set(value.url.absoluteString, forKey: "frameServer")
        UserDefaults.standard.set(false, forKey: "frameAutoConnectDisabled")
        error = nil
    }

    func didAuthenticate(reportAuthenticationError: Bool = true) async {
        do {
            // A server can authorize access directly (for example over Tailscale),
            // or through a web session. Use the same CSRF-protected API either way.
            let page = try await send("upload")
            let html = String(decoding: page, as: UTF8.self)
            guard html.contains("name=\"csrf-token\"") else { return }
            let data = try await send("api/gallery")
            gallery = try FrameJSON.decode(Gallery.self, from: data)
            isAdmin = html.contains("href=\"/admin/users\"")
            authenticated = true
            error = nil
        } catch is CancellationError { }
        catch FrameError.signedOut where !reportAuthenticationError { }
        catch { self.error = error.localizedDescription }
    }

    func refresh() async {
        guard authenticated, !signingOut else { return }
        loading = true
        defer { loading = false }
        await perform {
            self.gallery = try await self.get("api/gallery", as: Gallery.self)
        }
    }

    func perform(_ action: () async throws -> Void) async {
        do { try await action() }
        catch is CancellationError { }
        catch { self.error = error.localizedDescription }
    }

    func get<T: Decodable>(_ path: String, as type: T.Type) async throws -> T {
        try FrameJSON.decode(type, from: await send(path))
    }

    @discardableResult
    func mutate(_ path: String, method: String = "POST", body: [String: Any] = [:]) async throws -> Data {
        let csrf = try await csrfToken()
        return try await send(path, method: method, body: JSONSerialization.data(withJSONObject: body),
                              contentType: "application/json", csrf: csrf)
    }

    func upload(_ file: UploadFile, checkOnly: Bool) async throws -> Data {
        // One file per request keeps below the server's 50 MB aggregate request cap.
        guard file.data.count < 49 * 1024 * 1024 else { throw FrameError.message("\(file.name) exceeds the 49 MB upload limit.") }
        let token = try await csrfToken()
        let boundary = "Frame-\(UUID().uuidString)"
        return try await send(checkOnly ? "api/check-duplicates" : "api/upload", method: "POST",
                              body: Multipart.encode([file], boundary: boundary),
                              contentType: "multipart/form-data; boundary=\(boundary)", csrf: token)
    }

    func image(_ path: String) async throws -> UIImage {
        let data = try await send(path)
        guard let source = CGImageSourceCreateWithData(data as CFData, nil),
              let cgImage = CGImageSourceCreateThumbnailAtIndex(source, 0, [
                kCGImageSourceCreateThumbnailFromImageAlways: true,
                kCGImageSourceCreateThumbnailWithTransform: true,
                kCGImageSourceThumbnailMaxPixelSize: 2048
              ] as CFDictionary) else { throw FrameError.message("This image could not be loaded.") }
        return UIImage(cgImage: cgImage)
    }

    private func csrfToken() async throws -> String {
        let page = try await send("upload")
        let html = String(decoding: page, as: UTF8.self)
        guard let match = html.range(of: #"<meta name="csrf-token" content="([^"]+)""#, options: .regularExpression) else {
            authenticated = false
            throw FrameError.signedOut
        }
        let tag = String(html[match])
        guard let start = tag.range(of: "content=\"") else { throw FrameError.signedOut }
        return String(tag[start.upperBound...].dropLast())
    }

    private func send(_ path: String, method: String = "GET", body: Data? = nil,
                      contentType: String? = nil, csrf: String? = nil) async throws -> Data {
        guard let server else { throw FrameError.message("Connect to your frame first.") }
        let requestGeneration = generation
        let url = server.endpoint(path)
        var request = URLRequest(url: url)
        request.httpMethod = method
        request.httpBody = body
        request.setValue(contentType, forHTTPHeaderField: "Content-Type")
        request.setValue(csrf, forHTTPHeaderField: "X-CSRFToken")
        request.setValue(server.url.absoluteString, forHTTPHeaderField: "Referer")
        let cookies = await websiteData.httpCookieStore.allCookies()
        let applicable = cookies.filter { Self.cookie($0, matches: url) }
        for (key, value) in HTTPCookie.requestHeaderFields(with: applicable) { request.setValue(value, forHTTPHeaderField: key) }
        let data: Data
        let response: URLResponse
        do { (data, response) = try await session.data(for: request) }
        catch {
            guard generation == requestGeneration else { throw CancellationError() }
            throw error
        }
        guard generation == requestGeneration else { throw CancellationError() }
        guard let http = response as? HTTPURLResponse, let responseURL = http.url, server.contains(responseURL) else {
            throw FrameError.message("The server returned an unexpected response.")
        }
        let headers = http.allHeaderFields.reduce(into: [String: String]()) { result, field in
            if let key = field.key as? String, let value = field.value as? String { result[key] = value }
        }
        for cookie in HTTPCookie.cookies(withResponseHeaderFields: headers, for: responseURL) {
            await websiteData.httpCookieStore.setCookie(cookie)
        }
        if http.statusCode == 401 || responseURL.path == "/login" || (path == "upload" && responseURL.path != "/upload") {
            authenticated = false
            gallery = nil
            throw FrameError.signedOut
        }
        guard (200..<300).contains(http.statusCode) || (path == "logout" && http.statusCode == 302) else {
            let object = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any]
            let errors = object?["errors"] as? [String]
            let text = object?["error"] as? String ?? errors?.joined(separator: "\n")
            throw FrameError.message(text ?? "The server returned HTTP \(http.statusCode). Please try again.")
        }
        return data
    }

    static func cookie(_ cookie: HTTPCookie, matches url: URL) -> Bool {
        guard let host = url.host?.lowercased(), cookie.expiresDate.map({ $0 > Date() }) ?? true,
              !cookie.isSecure || url.scheme == "https" else { return false }
        let domain = cookie.domain.lowercased()
        let domainMatch = domain.hasPrefix(".") ? (host == String(domain.dropFirst()) || host.hasSuffix(domain)) : host == domain
        let path = url.path.isEmpty ? "/" : url.path
        let cookiePath = cookie.path
        let pathMatch = path == cookiePath || (path.hasPrefix(cookiePath) && (cookiePath.hasSuffix("/") || path.dropFirst(cookiePath.count).hasPrefix("/")))
        return domainMatch && pathMatch
    }

    func signOut(forgetServer: Bool = false) async {
        guard !signingOut else { return }
        signingOut = true
        defer { signingOut = false }
        // Invalidate pending responses before clearing all local authentication state.
        generation = UUID()
        authenticated = false
        gallery = nil
        _ = try? await send("logout")
        let records = await websiteData.dataRecords(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes())
        await websiteData.removeData(ofTypes: WKWebsiteDataStore.allWebsiteDataTypes(), for: records)
        session.invalidateAndCancel()
        let configuration = URLSessionConfiguration.ephemeral
        configuration.httpShouldSetCookies = false
        configuration.urlCache = nil
        session = URLSession(configuration: configuration, delegate: redirects, delegateQueue: nil)
        generation = UUID()
        authenticated = false
        isAdmin = false
        gallery = nil
        error = nil
        if forgetServer {
            UserDefaults.standard.set(true, forKey: "frameAutoConnectDisabled")
            server = nil
            redirects.server = nil
            UserDefaults.standard.removeObject(forKey: "frameServer")
        }
    }
}

/// Avoid forwarding a session or CSRF header through redirects to another origin.
private final class SameOriginRedirects: NSObject, URLSessionTaskDelegate, @unchecked Sendable {
    private let lock = NSLock()
    private var value: FrameServer?
    var server: FrameServer? {
        get { lock.lock(); defer { lock.unlock() }; return value }
        set { lock.lock(); defer { lock.unlock() }; value = newValue }
    }
    func urlSession(_ session: URLSession, task: URLSessionTask,
                    willPerformHTTPRedirection response: HTTPURLResponse, newRequest request: URLRequest,
                    completionHandler: @escaping (URLRequest?) -> Void) {
        if task.originalRequest?.url?.path == "/logout" {
            // Process the expiring cookie on the logout response itself; do not
            // replay the old Cookie header along the redirect to the login page.
            completionHandler(nil)
        } else {
            completionHandler(request.url.map { server?.contains($0) == true } == true ? request : nil)
        }
    }
}

private extension WKHTTPCookieStore {
    func allCookies() async -> [HTTPCookie] {
        await withCheckedContinuation { continuation in getAllCookies { continuation.resume(returning: $0) } }
    }
}
