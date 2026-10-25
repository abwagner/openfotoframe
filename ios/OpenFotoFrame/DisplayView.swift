import SwiftUI

struct DisplayView: View {
    @EnvironmentObject private var session: FrameSession
    @Environment(\.scenePhase) private var scenePhase
    @State private var state: DisplayState?
    @State private var busy = false
    @State private var web: WebDestination?
    @State private var connectionIssue: String?

    var body: some View {
        ScrollView {
            VStack(spacing: 28) {
                ZStack {
                    RoundedRectangle(cornerRadius: 24).fill(.teal.gradient)
                    VStack(spacing: 16) {
                        Image(systemName: "tv").font(.system(size: 70, weight: .ultraLight))
                        Text(state?.paused == true ? "Slideshow Paused" : "Your Frame").font(.title2.bold())
                        if let state {
                            Text((state.total ?? 0) == 0 ? "No slides to display" : "Slide \(state.index + 1) of \(state.total ?? 0)")
                        } else { Text("Connecting to your slideshow…") }
                    }.foregroundStyle(.white).padding(40)
                }.aspectRatio(1.4, contentMode: .fit)
                HStack(spacing: 34) {
                    control("Previous", symbol: "backward.end.fill", action: "prev")
                    control(state?.paused == true ? "Play" : "Pause", symbol: state?.paused == true ? "play.fill" : "pause.fill", action: state?.paused == true ? "play" : "pause")
                    control("Next", symbol: "forward.end.fill", action: "next")
                }.disabled(busy || connectionIssue != nil || (state?.total ?? 0) == 0)
                if let connectionIssue {
                    VStack(spacing: 8) {
                        Label("Connection interrupted", systemImage: "wifi.exclamationmark").font(.headline)
                        Text(connectionIssue).font(.footnote).foregroundStyle(.secondary)
                        Button("Reconnect") { Task { await refresh() } }
                    }
                }
                Text("Controls update the slideshow on your connected displays.")
                    .font(.subheadline).foregroundStyle(.secondary).multilineTextAlignment(.center)
                Button {
                    web = .init(title: "Live Display", path: "display")
                } label: {
                    Label("Watch Slideshow on This Device", systemImage: "play.rectangle")
                        .frame(maxWidth: .infinity).padding(8)
                }.buttonStyle(.borderedProminent)
                Text("The live display uses the same rendering as your TV, including crops, groups, mat textures, and transitions.")
                    .font(.footnote).foregroundStyle(.secondary)
                Button("Open Full Frame Controls") { web = .init(title: "Frame Controls", path: "upload") }
            }.padding(24).frame(maxWidth: 680).frame(maxWidth: .infinity)
        }
        .navigationTitle("Your Frame")
        .refreshable { await refresh() }
        .task(id: scenePhase) {
            guard scenePhase == .active else { return }
            await refresh()
            while !Task.isCancelled {
                do { try await Task.sleep(for: .seconds(5)) } catch { break }
                guard session.authenticated else { break }
                await refresh()
            }
        }
        .fullScreenCover(item: $web, onDismiss: { Task { await refresh() } }) { destination in
            NavigationStack { FrameWebScreen(destination: destination) }
        }
    }

    private func control(_ title: String, symbol: String, action: String) -> some View {
        Button {
            Task {
                busy = true
                defer { busy = false }
                await session.perform {
                    _ = try await session.mutate("api/display/control", body: ["action": action])
                    await refresh()
                }
            }
        } label: {
            VStack(spacing: 10) {
                Image(systemName: symbol).font(.title2).frame(width: 64, height: 64).background(.teal.opacity(0.1), in: Circle())
                Text(title).font(.caption)
            }
        }.accessibilityLabel(title)
    }
    private func refresh() async {
        do {
            state = try await session.get("api/display/state", as: DisplayState.self)
            connectionIssue = nil
        } catch is CancellationError { }
        catch { connectionIssue = error.localizedDescription }
    }
}
