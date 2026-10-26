import Foundation

struct FrameServer: Equatable {
    let url: URL

    init(_ address: String) throws {
        let text = address.trimmingCharacters(in: .whitespacesAndNewlines)
        guard let parts = URLComponents(string: text),
              let scheme = parts.scheme?.lowercased(), ["https", "http"].contains(scheme),
              let host = parts.host, !host.isEmpty,
              parts.user == nil, parts.password == nil,
              parts.query == nil, parts.fragment == nil,
              parts.path.isEmpty || parts.path == "/",
              let url = parts.url else { throw FrameError.message("Enter a server address such as https://frame.example.com. Use the server root without a path or credentials.") }
        guard scheme == "https" || Self.isLocal(host) else {
            throw FrameError.message("Use HTTPS for servers outside your local network.")
        }
        self.url = url
    }

    static func isLocal(_ host: String) -> Bool {
        let host = host.lowercased()
        if host == "localhost" || host == "[::1]" || host == "::1" || host.hasSuffix(".local") { return true }
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return false }
        let octets = parts.compactMap { Int($0) }
        guard octets.count == 4, octets.allSatisfy({ (0...255).contains($0) }) else { return false }
        return octets[0] == 10 || octets[0] == 127 ||
            (octets[0] == 192 && octets[1] == 168) ||
            (octets[0] == 172 && (16...31).contains(octets[1])) ||
            (octets[0] == 100 && (64...127).contains(octets[1]))
    }

    func contains(_ other: URL) -> Bool {
        other.scheme?.lowercased() == url.scheme?.lowercased() &&
        other.host?.lowercased() == url.host?.lowercased() &&
        (other.port ?? (other.scheme == "https" ? 443 : 80)) == (url.port ?? (url.scheme == "https" ? 443 : 80))
    }

    func endpoint(_ path: String) -> URL { url.appendingPathComponent(path) }
}

enum FrameError: LocalizedError {
    case message(String), signedOut
    var errorDescription: String? {
        switch self {
        case .message(let text): return text
        case .signedOut: return "Your session expired. Sign in again to continue."
        }
    }
}

struct FramePhoto: Decodable, Identifiable, Hashable {
    var id: String { filename }
    let filename: String
    let size: Int
    let enabled: Bool
    let title: String?
    let uploadedAt: String?
    let uploadedBy: String?
    let width: Int?
    let height: Int?
    let scale: Double?
    let matColor: String?
    let matFinish: String?
    let bevelWidth: Double?
    let borderEffect: String?
    let crop: FrameCrop?
    var displayName: String {
        if let title, !title.isEmpty { return title }
        return filename.replacingOccurrences(of: "^[a-f0-9]{8}_", with: "", options: .regularExpression)
    }
}

struct FrameCrop: Decodable, Hashable {
    let x: Double
    let y: Double
    let w: Double
    let h: Double
}

struct FrameGroup: Decodable {
    let images: [String]
    let matColor: String?
}

struct Gallery: Decodable {
    let images: [FramePhoto]
    let groups: [String: FrameGroup]
}

struct DisplayState: Decodable {
    let index: Int
    let paused: Bool
    let total: Int?
}

struct FrameSettings: Decodable {
    var matColor: String
    var matFinish: String
    var bevelWidth: Double
    var borderEffect: String
    var slideshowInterval: Double
    var transitionDuration: Double
    var fitMode: String
    var shuffle: Bool
    var targetAspectRatio: String
    let imageOrder: [String]

    var payload: [String: Any] {
        ["mat_color": matColor, "mat_finish": matFinish, "bevel_width": bevelWidth,
         "border_effect": borderEffect, "slideshow_interval": slideshowInterval,
         "transition_duration": transitionDuration, "fit_mode": fitMode,
         "shuffle": shuffle, "target_aspect_ratio": targetAspectRatio]
    }
}

struct UploadFile: Identifiable {
    let id = UUID()
    let name: String
    let data: Data
    let mimeType: String
}

struct DuplicateCheck: Decodable {
    struct Result: Decodable {
        struct Match: Decodable { let filename: String; let distance: Int }
        let width: Int
        let height: Int
        let matches: [Match]
        var lowResolution: Bool { width < 1280 || height < 720 }
    }
    let results: [String: Result]
    let errors: [String]?
}

struct UploadResult: Decodable {
    let uploaded: [String]
    let errors: [String]
}

enum FrameJSON {
    static func decode<T: Decodable>(_ type: T.Type, from data: Data) throws -> T {
        let decoder = JSONDecoder()
        decoder.keyDecodingStrategy = .convertFromSnakeCase
        return try decoder.decode(type, from: data)
    }
}

enum Multipart {
    static func encode(_ files: [UploadFile], boundary: String) -> Data {
        var body = Data()
        for file in files {
            // Never interpolate picker/file-provider names into multipart headers.
            let safeName = file.name.replacingOccurrences(of: "[^A-Za-z0-9._-]", with: "_", options: .regularExpression)
            body.append(Data("--\(boundary)\r\nContent-Disposition: form-data; name=\"files\"; filename=\"\(safeName)\"\r\nContent-Type: \(file.mimeType)\r\n\r\n".utf8))
            body.append(file.data)
            body.append(Data("\r\n".utf8))
        }
        body.append(Data("--\(boundary)--\r\n".utf8))
        return body
    }
}
