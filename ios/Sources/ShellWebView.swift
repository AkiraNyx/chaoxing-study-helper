import SwiftUI
import WebKit

struct ShellWebView: UIViewRepresentable {
    let url: URL

    func makeCoordinator() -> Coordinator {
        Coordinator(url: url)
    }

    func makeUIView(context: Context) -> WKWebView {
        let controller = WKUserContentController()
        if let path = Bundle.main.path(forResource: "inject", ofType: "js"),
           let source = try? String(contentsOfFile: path, encoding: .utf8) {
            controller.addUserScript(
                WKUserScript(source: source,
                             injectionTime: .atDocumentEnd,
                             forMainFrameOnly: true)
            )
        }

        let config = WKWebViewConfiguration()
        config.userContentController = controller
        config.allowsInlineMediaPlayback = true
        config.mediaTypesRequiringUserActionForPlayback = []

        let webView = WKWebView(frame: .zero, configuration: config)
        webView.allowsBackForwardNavigationGestures = true
        webView.scrollView.keyboardDismissMode = .interactive
        webView.load(URLRequest(url: url))
        return webView
    }

    func updateUIView(_ webView: WKWebView, context: Context) {
        guard context.coordinator.loadedURL != url else { return }
        context.coordinator.loadedURL = url
        webView.load(URLRequest(url: url))
    }

    final class Coordinator {
        var loadedURL: URL?

        init(url: URL) {
            self.loadedURL = url
        }
    }
}
