// Quota Notch — polling store: runs the plugin CLI on a schedule, keeps the decoded state, turns changes into
// notch peeks / pop-downs / banners, and reports the notch to the plugin through a tiny status file.

import AppKit
import SwiftUI
import UserNotifications

let APP_VERSION = (Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String) ?? "dev"
// History and the pacing plan only feed the open panels: fetched lazily when one opens, at most this often.
let HISTORY_EVERY: TimeInterval = 300
let PLAN_EVERY: TimeInterval = 600

/// `$HERMES_HOME` (or ~/.hermes) resolved to the Hermes root (`<root>/profiles/<name>` → `<root>`).
func hermesRoot() -> URL {
    let env = ProcessInfo.processInfo.environment["HERMES_HOME"].flatMap { $0.isEmpty ? nil : $0 }
    var u = URL(fileURLWithPath: env.map { ($0 as NSString).expandingTildeInPath } ?? NSHomeDirectory() + "/.hermes")
    if u.deletingLastPathComponent().lastPathComponent == "profiles" { u = u.deletingLastPathComponent().deletingLastPathComponent() }
    return u
}

func hermesDesktopInstalled() -> Bool {
    ["/Applications/Hermes.app", NSHomeDirectory() + "/Applications/Hermes.app"].contains { FileManager.default.fileExists(atPath: $0) }
}

func notchScreen() -> NSScreen? {
    if ProcessInfo.processInfo.environment["QUOTA_NOTCH_FAKE_NOTCH"] == "1" { return NSScreen.main }
    return NSScreen.screens.first { $0.safeAreaInsets.top > 0 && $0.auxiliaryTopLeftArea != nil }
}

func displayMode() -> String { Pref.str(Pref.display).isEmpty ? "both" : Pref.str(Pref.display) }
/// The island runs only on a Mac with a notch, unless "menu bar only" is chosen.
func islandEnabled() -> Bool { displayMode() != "menubar" && notchScreen() != nil }
/// The menu bar icon hides only for "notch only" on a Mac that really has a notch (never leave no UI at all).
func menuIconVisible() -> Bool { !(displayMode() == "island" && notchScreen() != nil) }
func motionOn() -> Bool { Pref.on(Pref.motion) && !NSWorkspace.shared.accessibilityDisplayShouldReduceMotion }

@MainActor
final class Store: ObservableObject {
    static let main = Store()

    @Published var quota: QuotaStatus?
    @Published var snapshot: Snapshot?
    @Published var history: [HistorySeries] = []
    @Published var plan: [PlanHint] = []
    @Published var hermesPath: String?
    @Published var lastError: String?
    @Published var updated: Date?
    @Published var toast: String?
    @Published var timings: [String: Double] = [:]   // last wall time per CLI call (s), shown in Settings

    private var engine: EventEngine
    private var busy: Set<String> = []
    private var last: [String: Date] = [:]
    private var planUnsupportedUntil = Date.distantPast
    private var asleep = false
    private var timer: Timer?
    private var toastToken = 0
    private let startedAt = Date().timeIntervalSince1970
    let shots = ProcessInfo.processInfo.environment["QUOTA_NOTCH_SHOTS"] != nil

    init() {
        let d = UserDefaults.standard
        engine = EventEngine(quotaSent: d.dictionary(forKey: "quotaSent") as? [String: Int] ?? [:],
                             soonSent: Set(d.stringArray(forKey: "soonSent") ?? []))
        let ws = NSWorkspace.shared.notificationCenter
        ws.addObserver(forName: NSWorkspace.screensDidSleepNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in Store.main.asleep = true }
        }
        ws.addObserver(forName: NSWorkspace.screensDidWakeNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in
                Store.main.asleep = false
                Store.main.refreshAll()
            }
        }
    }

    func start() {
        guard timer == nil else { return }
        writeStatus()
        refreshAll()
        // ticks every second so a changed interval applies right away; each feed keeps its own cadence
        timer = Timer.scheduledTimer(withTimeInterval: 1, repeats: true) { _ in
            Task { @MainActor in Store.main.tick() }
        }
    }

    private func due(_ name: String, _ every: TimeInterval) -> Bool {
        Date().timeIntervalSince(last[name] ?? .distantPast) >= every
    }

    private func tick() {
        guard !asleep else { return }
        if due("snapshot", max(5, Pref.num(Pref.liveEvery))) { refreshLive() }
        if due("status", max(15, Pref.num(Pref.quotaEvery))) { refreshQuota() }
    }

    /// The notch hover panel or the menu bar window opened: fetch the slow, display-only feeds if stale.
    func panelOpened() {
        guard !asleep else { return }
        if due("history", HISTORY_EVERY) { refreshHistory() }
        if due("plan", PLAN_EVERY), Date() > planUnsupportedUntil { refreshPlan() }
    }

    func refreshAll() {
        refreshQuota()
        refreshLive()
    }

    /// One CLI call off the main thread; `apply` runs on the main actor with stdout (nil = failed).
    private func call(_ name: String, _ args: [String], timeout: TimeInterval = 25, apply: @escaping @MainActor (Data?) -> Void) {
        guard !busy.contains(name) else { return }
        last[name] = Date()
        let exe = HermesCLI.locate(override: Pref.str(Pref.hermesPath))
        hermesPath = exe
        guard let exe else {
            lastError = L("The hermes command was not found. Set its path in Settings.",
                          "hermes komutu bulunamadı. Yolunu Ayarlar'dan gir.")
            apply(nil)
            return
        }
        busy.insert(name)
        let argv = HermesCLI.argv(profile: Pref.str(Pref.profile), args)
        DispatchQueue.global(qos: .utility).async {
            let r = HermesCLI.run(exe, argv, timeout: timeout)
            DispatchQueue.main.async {
                MainActor.assumeIsolated {
                    let s = Store.main
                    s.busy.remove(name)
                    s.timings[name] = r.seconds
                    if r.timedOut {
                        s.lastError = L("hermes \(args.prefix(2).joined(separator: " ")) timed out", "hermes \(args.prefix(2).joined(separator: " ")) zaman aşımına uğradı")
                    }
                    // the monitor answers exit 1 with a JSON body for refusals (e.g. enrollment off): keep the body
                    apply(r.stdout.isEmpty ? nil : r.stdout)
                }
            }
        }
    }

    func refreshQuota() {
        call("status", ["status", "--json", "--cached"]) { data in
            guard let data, let q = QuotaStatus.parse(data) else { return }
            self.quota = q
            self.lastError = nil
            self.updated = Date()
            self.quotaChanged()
        }
    }

    func refreshLive() {
        call("snapshot", ["monitor", "snapshot", "--json"]) { data in
            guard let data, let s = Snapshot.parse(data, today: localDay()) else {
                if data != nil { self.lastError = L("Unreadable monitor output (is the quota plugin installed?)", "Monitor çıktısı okunamadı (quota eklentisi kurulu mu?)") }
                return
            }
            self.snapshot = s
            self.updated = Date()
            if case .on = s.collection { self.lastError = nil }
            self.liveChanged(s)
        }
    }

    func refreshHistory() {
        call("history", ["monitor", "history", "--json", "--days", "1"]) { data in
            guard let data, let h = HistorySeries.parse(data) else { return }
            self.history = h
        }
    }

    func refreshPlan() {
        call("plan", ["monitor", "plan", "--json"]) { data in
            // An older plugin has no `plan`: argparse prints usage (not JSON). Ask again in an hour.
            guard let data, let p = PlanHint.parse(data) else {
                self.planUnsupportedUntil = Date().addingTimeInterval(3600)
                self.plan = []
                return
            }
            self.plan = p
        }
    }

    // MARK: change handling

    private func quotaChanged() {
        guard let q = quota else { return }
        let now = Date().timeIntervalSince1970
        let r = engine.quotas(q, burn: snapshot?.burn ?? [], now: now, step: max(1, Pref.num(Pref.stepPct)))
        engine.prune(now: now)
        UserDefaults.standard.set(engine.quotaSent, forKey: "quotaSent")
        UserDefaults.standard.set(Array(engine.soonSent), forKey: "soonSent")
        for key in r.moved { Island.shared.peek(key) }
        for e in r.events { announce(e) }
    }

    private func liveChanged(_ s: Snapshot) {
        guard case .on = s.collection else { return }
        let r = engine.sessions(s.mains)
        if r.started { Island.shared.peek("sessions") }
        for e in r.events { announce(e) }
        // the burn rate arrives with the snapshot: re-check "filling soon"
        if quota != nil { quotaChanged() }
    }

    func account(_ key: String?) -> QuotaAccount? { quota?.visible.first { $0.key == key } }
    func session(_ id: String?, kind: String?) -> LiveSession? { snapshot?.sessions.first { $0.id == id && $0.kind == kind } }

    /// Island pop-down + (optionally) a Notification Center banner.
    private func announce(_ e: NotchEvent) {
        let text = eventText(e)
        let isQuota = [.quota80, .quota95, .fillingSoon].contains(e.kind)
        let gauge = e.used.map { $0 / 100 }
        Island.shared.push(IslandEvent(provider: isQuota ? account(e.accountKey)?.base : nil, symbol: symbol(e.kind),
                                       title: text.title, detail: text.detail, tint: tint(e.kind),
                                       gauge: isQuota ? gauge : nil, session: isQuota ? nil : session(e.sessionID, kind: e.sessionKind)))
        if (isQuota && Pref.on(Pref.notifyQuota)) || (!isQuota && Pref.on(Pref.notifyAgents)) {
            Notifier.post(text.title, text.detail, sessionID: e.sessionID, sessionKind: e.sessionKind)
        }
    }

    func eventText(_ e: NotchEvent) -> (title: String, detail: String) {
        let who: String = {
            guard let s = session(e.sessionID, kind: e.sessionKind) else { return "Hermes" }
            return s.isHermes ? (s.profile.map { $0 == "default" ? "Hermes" : "Hermes · \($0)" } ?? "Hermes") : (s.sourceName ?? s.kind)
        }()
        let title = e.title ?? L("(untitled)", "(başlıksız)")
        let acct = account(e.accountKey).map { providerName($0, multi: quota?.multiAccountBases ?? []) } ?? (e.accountKey ?? "")
        let win = e.window.map(windowName) ?? ""
        let reset = e.resetSeconds.map { $0 > 0 ? L(" · resets in \(span($0))", " · sıfırlanma \(span($0)) sonra") : "" } ?? ""
        switch e.kind {
        case .approval: return (L("\(who) needs approval", "\(who) onay bekliyor"), title)
        case .yourTurn: return (L("\(who) finished — your turn", "\(who) bitirdi — sıra sende"), title)
        case .stalled: return (L("\(who) may be stuck", "\(who) takılmış olabilir"), title)
        case .quota80, .quota95:
            return (L("\(acct) · \(win) at \(pct(e.used ?? 0))", "\(acct) · \(win) \(pct(e.used ?? 0)) doldu"),
                    L("Quota \(e.kind == .quota95 ? "95" : "80")% threshold", "Kota %\(e.kind == .quota95 ? "95" : "80") eşiği") + reset)
        case .fillingSoon:
            return (L("\(acct) · \(win) filling up soon", "\(acct) · \(win) yakında dolacak"),
                    L("At this rate full in ~\(span(e.etaSeconds ?? 0))", "Bu hızla ~\(span(e.etaSeconds ?? 0)) içinde dolar") + reset)
        }
    }

    private func symbol(_ k: NotchEvent.Kind) -> String {
        switch k {
        case .approval: return "hand.raised.fill"
        case .yourTurn: return "checkmark.circle.fill"
        case .stalled: return "exclamationmark.triangle.fill"
        default: return "gauge.with.dots.needle.67percent"
        }
    }

    private func tint(_ k: NotchEvent.Kind) -> Color {
        switch k {
        case .approval: return .orange
        case .yourTurn: return .green
        case .stalled: return .yellow
        case .quota95: return .red
        default: return .orange
        }
    }

    // MARK: derived

    var mains: [LiveSession] { snapshot?.mains ?? [] }
    var counts: LiveCounts { snapshot?.counts ?? LiveCounts() }
    var current: LiveSession? { Sessions.current(mains) }
    var groups: [SessionGroup] {
        Sessions.groups(mains, excluding: current) { p in p == "default" ? "Hermes" : "Hermes · \(p)" }
    }
    /// Fullest measured window among accounts that are not unavailable (menu bar label, "auto").
    var tightest: (account: QuotaAccount, window: QuotaWindow)? {
        var best: (QuotaAccount, QuotaWindow)?
        for a in quota?.visible ?? [] where a.unavailable == nil {
            for w in a.measured where (w.used ?? 0) > (best?.1.used ?? -1) { best = (a, w) }
        }
        return best
    }
    var collectionOff: Bool { if case .off = snapshot?.collection { return true }; return false }
    func burn(_ a: QuotaAccount, _ w: QuotaWindow) -> Burn? { snapshot?.burn.first { $0.provider == a.key && $0.window == w.label } }
    func planHint(_ a: QuotaAccount) -> PlanHint? { plan.first { $0.provider == a.key } }
    func series(_ a: QuotaAccount, _ w: QuotaWindow) -> [Double] {
        (history.first { $0.provider == a.key && $0.window == w.label }?.points ?? []).suffix(48).map(\.used)
    }

    // MARK: actions

    func open(_ s: LiveSession) {
        switch Opener.plan(s, desktopInstalled: hermesDesktopInstalled()) {
        case .url(let u):
            NSWorkspace.shared.open(u)
            showToast(L("Opening in Hermes: \(s.title ?? s.id)", "Hermes'te açılıyor: \(s.title ?? s.id)"))
        case .copy(let c):
            NSPasteboard.general.clearContents()
            NSPasteboard.general.setString(c, forType: .string)
            showToast(s.isRunning && !s.isHermes
                      ? L("Running elsewhere — resume command copied: \(c)", "Başka yerde çalışıyor — devam komutu kopyalandı: \(c)")
                      : L("Command copied — run it in Terminal: \(c)", "Komut kopyalandı — Terminal'de çalıştır: \(c)"))
        case .nothing:
            showToast(L("Nothing to open for this row", "Bu satır için açılacak bir şey yok"))
        }
    }

    func copy(_ text: String, note: String) {
        NSPasteboard.general.clearContents()
        NSPasteboard.general.setString(text, forType: .string)
        showToast(note)
    }

    func showToast(_ text: String) {
        toast = text
        toastToken += 1
        let token = toastToken
        DispatchQueue.main.asyncAfter(deadline: .now() + 5) {
            MainActor.assumeIsolated {
                guard Store.main.toastToken == token else { return }
                withAnimation { Store.main.toast = nil }
            }
        }
    }

    // MARK: status file for the plugin card

    var statusURL: URL { hermesRoot().appendingPathComponent("quota_notch.json") }

    func writeStatus(stopping: Bool = false) {
        let st = CompanionStatus(version: APP_VERSION, notch: notchScreen() != nil,
                                 running_since: stopping ? nil : startedAt, display: displayMode(),
                                 pid: stopping ? nil : ProcessInfo.processInfo.processIdentifier)
        guard let data = try? JSONEncoder().encode(st), FileManager.default.fileExists(atPath: hermesRoot().path) else { return }
        try? data.write(to: statusURL, options: .atomic)
    }
}

// MARK: - notifications

enum Notifier {
    private static var asked = false

    /// A banner; clicking it opens the session it is about (same rule as a row click).
    @MainActor static func post(_ title: String, _ body: String, sessionID: String?, sessionKind: String?) {
        guard !Store.main.shots, Bundle.main.bundleIdentifier != nil else { return }
        let c = UNUserNotificationCenter.current()
        if !asked {
            asked = true
            c.delegate = NotifierDelegate.shared
            c.requestAuthorization(options: [.alert, .sound]) { _, _ in }
        }
        let n = UNMutableNotificationContent()
        n.title = title
        n.body = body
        n.sound = .default
        if let sessionID, let sessionKind { n.userInfo = ["session": sessionID, "kind": sessionKind] }
        c.add(UNNotificationRequest(identifier: UUID().uuidString, content: n, trigger: nil))
    }
}

final class NotifierDelegate: NSObject, UNUserNotificationCenterDelegate {
    static let shared = NotifierDelegate()
    func userNotificationCenter(_ center: UNUserNotificationCenter, willPresent notification: UNNotification,
                                withCompletionHandler done: @escaping (UNNotificationPresentationOptions) -> Void) {
        done([.banner, .sound])
    }
    func userNotificationCenter(_ center: UNUserNotificationCenter, didReceive response: UNNotificationResponse,
                                withCompletionHandler done: @escaping () -> Void) {
        let info = response.notification.request.content.userInfo
        let id = info["session"] as? String, kind = info["kind"] as? String
        Task { @MainActor in
            if let s = Store.main.session(id, kind: kind) { Store.main.open(s) }
            else if kind == "hermes", let id, let u = Opener.hermesURL(sessionID: id) { NSWorkspace.shared.open(u) }
        }
        done()
    }
}
