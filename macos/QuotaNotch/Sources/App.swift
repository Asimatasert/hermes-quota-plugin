// Quota Notch — app entry: menu bar extra + notch island, both fed by one Store.

import AppKit
import SwiftUI

@main
struct QuotaNotchApp: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var store = Store.main
    @AppStorage(Pref.display) private var display = "both"  // observed so the icon follows the setting

    init() { Pref.register() }

    var showIcon: Binding<Bool> {
        Binding(get: { _ = display; return menuIconVisible() }, set: { _ in })
    }

    var body: some Scene {
        MenuBarExtra(isInserted: showIcon) {
            MenuPanel(store: store)
        } label: {
            BarLabel(store: store)
        }
        .menuBarExtraStyle(.window)
    }
}

final class AppDelegate: NSObject, NSApplicationDelegate {
    func applicationDidFinishLaunching(_ notification: Notification) {
        Pref.register()
        MainActor.assumeIsolated {
            Store.main.start()
            IslandController.shared.start()
            Hooks.afterLaunch()
        }
    }

    func applicationWillTerminate(_ notification: Notification) {
        MainActor.assumeIsolated { Store.main.writeStatus(stopping: true) }
    }
}
