import SwiftUI
import WebKit

struct WebDestination: Identifiable {
    enum Focus { case photo(String), group(String), tv }
    let id = UUID()
    let title: String
    let path: String
    var focus: Focus? = nil
}

struct FrameWebScreen: View {
    @EnvironmentObject private var session: FrameSession
    @Environment(\.dismiss) private var dismiss
    let destination: WebDestination
    var login = false
    @State private var failure: String?
    @State private var reloadID = UUID()
    @State private var loading = true
    @State private var pagePath = ""

    var body: some View {
        VStack(spacing: 0) {
            if loading { ProgressView().frame(maxWidth: .infinity).padding(6) }
            if pagePath.hasPrefix("/mfa"),
               (Bundle.main.object(forInfoDictionaryKey: "FramePasskeyDomain") as? String)?.lowercased() != session.server?.url.host?.lowercased() {
                Text("Passkeys aren’t enabled for this server in this app build. Use an authenticator or recovery code to sign in.")
                    .font(.footnote).foregroundStyle(.secondary).padding()
            }
            if let failure {
                ContentUnavailableView {
                    Label("Couldn’t load your frame", systemImage: "wifi.exclamationmark")
                } description: {
                    Text(failure + "\nFor HTTPS, your iPhone must trust the server certificate.")
                } actions: {
                    Button("Try Again") { self.failure = nil; loading = true; reloadID = UUID() }
                    if login { Button("Change Server") { Task { await session.signOut(forgetServer: true) } } }
                }
            } else if let server = session.server {
                FrameWebView(url: server.endpoint(destination.path), session: session,
                             loading: $loading, failure: $failure, pagePath: $pagePath, login: login, focus: destination.focus)
                    .id(reloadID)
            }
        }
        .navigationTitle(destination.title)
        .navigationBarTitleDisplayMode(.inline)
        .onAppear { if destination.path == "display" { UIApplication.shared.isIdleTimerDisabled = true } }
        .onDisappear { if destination.path == "display" { UIApplication.shared.isIdleTimerDisabled = false } }
        .toolbar {
            if !login { ToolbarItem(placement: .confirmationAction) { Button("Done") { dismiss() } } }
            if login {
                ToolbarItem(placement: .topBarTrailing) {
                    Button("Server") { Task { await session.signOut(forgetServer: true) } }
                }
            }
        }
    }
}

private struct FrameWebView: UIViewRepresentable {
    let url: URL
    let session: FrameSession
    @Binding var loading: Bool
    @Binding var failure: String?
    @Binding var pagePath: String
    let login: Bool
    let focus: WebDestination.Focus?

    func makeCoordinator() -> Coordinator { Coordinator(self) }
    func makeUIView(context: Context) -> WKWebView {
        let configuration = WKWebViewConfiguration()
        configuration.websiteDataStore = session.websiteData
        configuration.allowsInlineMediaPlayback = true
        let webView = WKWebView(frame: .zero, configuration: configuration)
        webView.navigationDelegate = context.coordinator
        webView.uiDelegate = context.coordinator
        webView.allowsBackForwardNavigationGestures = true
        webView.isInspectable = _isDebugAssertConfiguration()
        webView.load(URLRequest(url: url))
        return webView
    }
    func updateUIView(_ uiView: WKWebView, context: Context) { context.coordinator.parent = self }
    static func dismantleUIView(_ uiView: WKWebView, coordinator: Coordinator) {
        uiView.stopLoading()
        uiView.navigationDelegate = nil
        uiView.uiDelegate = nil
    }

    final class Coordinator: NSObject, WKNavigationDelegate, WKUIDelegate {
        var parent: FrameWebView
        init(_ parent: FrameWebView) { self.parent = parent }

        func webView(_ webView: WKWebView, decidePolicyFor action: WKNavigationAction,
                     decisionHandler: @escaping (WKNavigationActionPolicy) -> Void) {
            guard let url = action.request.url else { decisionHandler(.cancel); return }
            if parent.session.server?.contains(url) == true {
                decisionHandler(.allow)
            } else {
                if action.navigationType == .linkActivated, ["https", "http"].contains(url.scheme) {
                    UIApplication.shared.open(url)
                }
                decisionHandler(.cancel)
            }
        }

        func webView(_ webView: WKWebView, didStartProvisionalNavigation navigation: WKNavigation!) {
            parent.loading = true
        }
        func webView(_ webView: WKWebView, didFinish navigation: WKNavigation!) {
            parent.loading = false
            guard let url = webView.url else { return }
            parent.pagePath = url.path
            if url.path == "/logout" || url.path == "/login" {
                parent.session.authenticated = false
                parent.session.gallery = nil
            }
            if parent.login && url.path == "/upload" {
                Task { await parent.session.didAuthenticate() }
            }
            if url.path == "/upload", let focus = parent.focus {
                Task { await focusEditor(focus, webView: webView) }
            }
        }

        private func focusEditor(_ focus: WebDestination.Focus, webView: WKWebView) async {
            let kind: String
            let identifier: String
            switch focus {
            case .photo(let name): kind = "photo"; identifier = name
            case .group(let id): kind = "group"; identifier = id
            case .tv: kind = "tv"; identifier = ""
            }
            // Arguments are bridged, never interpolated into executable JavaScript.
            // The editor loads gallery metadata asynchronously after navigation.
            _ = try? await webView.callAsyncJavaScript("""
                for (let attempt = 0; attempt < 50; attempt++) {
                    if (kind === 'tv' && typeof switchSettingsTab === 'function') {
                        switchSettingsTab('settings');
                        document.getElementById('tv-schedule-panel')?.scrollIntoView();
                        return true;
                    }
                    if (kind === 'photo' && typeof images !== 'undefined' &&
                        images.some(image => image.filename === identifier)) {
                        openPreview(identifier);
                        document.getElementById('preview-section')?.scrollIntoView();
                        return true;
                    }
                    if (kind === 'group' && typeof groups !== 'undefined' && groups[identifier]) {
                        openGroupPreview(identifier);
                        document.getElementById('preview-section')?.scrollIntoView();
                        return true;
                    }
                    await new Promise(resolve => setTimeout(resolve, 100));
                }
                return false;
                """, arguments: ["kind": kind, "identifier": identifier], in: nil, contentWorld: .page)
        }
        func webView(_ webView: WKWebView, didFailProvisionalNavigation navigation: WKNavigation!, withError error: Error) {
            if (error as NSError).code != NSURLErrorCancelled { parent.failure = error.localizedDescription; parent.loading = false }
        }
        func webView(_ webView: WKWebView, didFail navigation: WKNavigation!, withError error: Error) {
            parent.failure = error.localizedDescription
            parent.loading = false
        }
        func webViewWebContentProcessDidTerminate(_ webView: WKWebView) { webView.reload() }

        // Existing pages use target=_blank for the TV display. Keep it in our session.
        func webView(_ webView: WKWebView, createWebViewWith configuration: WKWebViewConfiguration,
                     for navigationAction: WKNavigationAction, windowFeatures: WKWindowFeatures) -> WKWebView? {
            if let url = navigationAction.request.url, parent.session.server?.contains(url) == true {
                webView.load(navigationAction.request)
            }
            return nil
        }

        // Gallery deletion, backup restore, disconnect, and user administration use confirm().
        func webView(_ webView: WKWebView, runJavaScriptConfirmPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping (Bool) -> Void) {
            let alert = UIAlertController(title: "OpenFotoFrame", message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(false) })
            alert.addAction(UIAlertAction(title: "Continue", style: .default) { _ in completionHandler(true) })
            present(alert, from: webView, fallback: { completionHandler(false) })
        }
        func webView(_ webView: WKWebView, runJavaScriptAlertPanelWithMessage message: String,
                     initiatedByFrame frame: WKFrameInfo, completionHandler: @escaping () -> Void) {
            let alert = UIAlertController(title: "OpenFotoFrame", message: message, preferredStyle: .alert)
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler() })
            present(alert, from: webView, fallback: completionHandler)
        }
        func webView(_ webView: WKWebView, runJavaScriptTextInputPanelWithPrompt prompt: String,
                     defaultText: String?, initiatedByFrame frame: WKFrameInfo,
                     completionHandler: @escaping (String?) -> Void) {
            let alert = UIAlertController(title: "OpenFotoFrame", message: prompt, preferredStyle: .alert)
            alert.addTextField { $0.text = defaultText }
            alert.addAction(UIAlertAction(title: "Cancel", style: .cancel) { _ in completionHandler(nil) })
            alert.addAction(UIAlertAction(title: "OK", style: .default) { _ in completionHandler(alert.textFields?.first?.text) })
            present(alert, from: webView, fallback: { completionHandler(nil) })
        }
        private func present(_ alert: UIAlertController, from webView: WKWebView, fallback: () -> Void) {
            guard var controller = webView.window?.rootViewController else { fallback(); return }
            while let presented = controller.presentedViewController { controller = presented }
            controller.present(alert, animated: true)
        }
    }
}
