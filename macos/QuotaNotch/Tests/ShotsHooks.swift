// Screenshot harness (compiled by macos/shots.sh in place of Sources/Hooks.swift; never in a release build).
// With QUOTA_NOTCH_SHOTS=<dir> the app captures the notch hover panel, pop-down events, a peek, the menu bar
// window and the settings window, in English then Turkish, and quits. Each capture is written twice:
// <name>.png (the window's own view hierarchy, needs no permission) and <name>.screen.png (screencapture -l,
// only when the launching process has Screen Recording permission).

import AppKit
import SwiftUI

@MainActor
enum Hooks {
    static var out = ""
    static var windows: [NSWindow] = []

    static func afterLaunch() {
        guard let dir = ProcessInfo.processInfo.environment["QUOTA_NOTCH_SHOTS"] else { return }
        out = dir
        try? FileManager.default.createDirectory(atPath: dir, withIntermediateDirectories: true)
        UserDefaults.standard.set("en", forKey: Pref.lang)
        if ProcessInfo.processInfo.environment["QUOTA_NOTCH_SHOTS_ONLY"] == "menubar-off" {
            let off: [(Double, () -> Void)] = [
                (6.0, { show(MenuPanel(store: .main), "menubar-off", width: 420, height: 420) }),
                (2.2, { capture(windows.last, "menubar-off") }),
                (0.3, { NSApp.terminate(nil) }),
            ]
            run(off)
            return
        }
        let steps: [(Double, () -> Void)] = [
            (6.0, { Island.shared.dismiss(); IslandController.shared.setHover(true) }),
            (2.4, { capture(IslandController.shared.panel, "island-hover") }),
            (0.2, { IslandController.shared.setHover(false); Island.shared.pin(event(kind: .approval, session: "sess_work_1")) }),
            (2.2, { capture(IslandController.shared.panel, "island-event-approval") }),
            (0.2, { Island.shared.pin(event(kind: .fillingSoon, account: "anthropic", window: "Current session", used: 58, eta: 2160, reset: 6780)) }),
            (2.2, { capture(IslandController.shared.panel, "island-event-quota") }),
            (0.2, { Island.shared.dismiss(); Island.shared.peek("sessions") }),
            (2.2, { capture(IslandController.shared.panel, "island-peek") }),
            (0.2, { show(MenuPanel(store: .main), "menubar-panel", width: 420, height: 760) }),
            (2.2, { capture(windows.last, "menubar-panel") }),
            (0.2, { show(ScrollView { SettingsView(store: .main).padding(16) }, "settings", width: 520, height: 1560) }),
            (2.2, { capture(windows.last, "settings") }),
            (0.2, { closeWindows(); UserDefaults.standard.set("tr", forKey: Pref.lang); IslandController.shared.setHover(true) }),
            (2.4, { capture(IslandController.shared.panel, "island-hover-tr") }),
            (0.2, { IslandController.shared.setHover(false); show(MenuPanel(store: .main), "menubar-panel-tr", width: 420, height: 760) }),
            (2.2, { capture(windows.last, "menubar-panel-tr") }),
            (0.5, { NSApp.terminate(nil) }),
        ]
        run(steps)
    }

    static func run(_ steps: [(Double, () -> Void)]) {
        var t = 0.0
        for (delay, f) in steps {
            t += delay
            DispatchQueue.main.asyncAfter(deadline: .now() + t) { MainActor.assumeIsolated { f() } }
        }
    }

    static func event(kind: NotchEvent.Kind, session: String? = nil, account: String? = nil, window: String? = nil,
                      used: Double? = nil, eta: Double? = nil, reset: Double? = nil) -> IslandEvent {
        let store = Store.main
        let s = store.snapshot?.sessions.first { $0.id == session }
        let e = NotchEvent(kind: kind, key: "shot", accountKey: account, window: window, used: used, sessionID: session,
                           sessionKind: s?.kind, title: s?.title, etaSeconds: eta, resetSeconds: reset)
        let text = store.eventText(e)
        let quota = account != nil
        return IslandEvent(provider: quota ? store.account(account)?.base : nil,
                           symbol: kind == .approval ? "hand.raised.fill" : "gauge.with.dots.needle.67percent",
                           title: text.title, detail: text.detail, tint: kind == .approval ? .orange : .orange,
                           gauge: used.map { $0 / 100 }, session: s)
    }

    static func show<V: View>(_ view: V, _ title: String, width: CGFloat, height: CGFloat) {
        let w = NSWindow(contentRect: NSRect(x: 60, y: 40, width: width, height: height), styleMask: [.titled],
                         backing: .buffered, defer: false)
        w.title = title
        w.isReleasedWhenClosed = false
        w.contentView = NSHostingView(rootView: view.frame(width: width, height: height, alignment: .top)
            .background(Color(nsColor: .windowBackgroundColor)))
        w.orderFrontRegardless()
        windows.append(w)
    }

    static func closeWindows() {
        windows.forEach { $0.orderOut(nil) }
        windows = []
    }

    static func capture(_ w: NSWindow?, _ name: String) {
        guard let w, let v = w.contentView else { print("no window for \(name)"); return }
        if let rep = v.bitmapImageRepForCachingDisplay(in: v.bounds) {
            v.cacheDisplay(in: v.bounds, to: rep)
            // flatten transparent areas onto a desktop-like grey so the island's edge is visible
            let flat = NSBitmapImageRep(bitmapDataPlanes: nil, pixelsWide: rep.pixelsWide, pixelsHigh: rep.pixelsHigh,
                                        bitsPerSample: 8, samplesPerPixel: 4, hasAlpha: true, isPlanar: false,
                                        colorSpaceName: .deviceRGB, bytesPerRow: 0, bitsPerPixel: 0)!
            NSGraphicsContext.saveGraphicsState()
            NSGraphicsContext.current = NSGraphicsContext(bitmapImageRep: flat)
            NSColor(white: 0.42, alpha: 1).setFill()
            NSRect(x: 0, y: 0, width: rep.pixelsWide, height: rep.pixelsHigh).fill()
            rep.draw(in: NSRect(x: 0, y: 0, width: rep.pixelsWide, height: rep.pixelsHigh), from: .zero, operation: .sourceOver,
                     fraction: 1, respectFlipped: true, hints: nil)
            NSGraphicsContext.restoreGraphicsState()
            try? flat.representation(using: .png, properties: [:])?.write(to: URL(fileURLWithPath: "\(out)/\(name).png"))
        }
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/sbin/screencapture")
        p.arguments = ["-x", "-o", "-l", String(w.windowNumber), "\(out)/\(name).screen.png"]
        try? p.run()
        p.waitUntilExit()
        print("captured \(name)")
    }
}
