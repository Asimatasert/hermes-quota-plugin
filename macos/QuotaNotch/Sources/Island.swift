// Quota Notch — the notch island (ported from Agent Bar): small "ears" beside the camera (quota rings left,
// running sessions right), events that pop down for a few seconds, a mini dashboard on hover. Mouse events are
// only taken while the pointer is over the island, so menus underneath stay clickable.

import AppKit
import SwiftUI

var islandSpring: Animation { motionOn() ? .spring(response: 0.38, dampingFraction: 0.72) : .easeInOut(duration: 0.15) }

struct IslandEvent: Identifiable {
    let id = UUID()
    let provider: String?   // provider logo inside a ring (quota events), else `symbol`
    let symbol: String
    let title: String
    let detail: String
    let tint: Color
    let gauge: Double?
    var session: LiveSession? = nil   // a click on the event opens this session
}

@MainActor
final class Island: ObservableObject {
    static let shared = Island()
    enum Mode { case compact, peek, event, hover }
    @Published private(set) var event: IslandEvent?
    @Published var hover = false
    @Published private(set) var peeking = false
    @Published private(set) var highlight: String?   // account key or "sessions": pops once when it changed
    private var queue: [IslandEvent] = []
    private var timer: Timer?
    private var peekTimer: Timer?

    var mode: Mode { hover ? .hover : event != nil ? .event : peeking ? .peek : .compact }

    /// Small heads-up: show the ears (rings + session count) for a moment and pop the part that changed.
    func peek(_ what: String) {
        guard islandEnabled(), Pref.on(Pref.islandEvents) else { return }
        withAnimation(islandSpring) {
            peeking = true
            highlight = what
        }
        DispatchQueue.main.asyncAfter(deadline: .now() + 0.6) {
            MainActor.assumeIsolated { withAnimation(islandSpring) { Island.shared.highlight = nil } }
        }
        peekTimer?.invalidate()
        peekTimer = Timer.scheduledTimer(withTimeInterval: max(3, Pref.num(Pref.peekSec)), repeats: false) { _ in
            Task { @MainActor in withAnimation(islandSpring) { Island.shared.peeking = false } }
        }
    }

    func push(_ e: IslandEvent) {
        guard islandEnabled(), Pref.on(Pref.islandEvents) else { return }
        if queue.count < 5 { queue.append(e) }
        if event == nil { advance() }
    }

    private func advance() {
        timer?.invalidate()
        guard !queue.isEmpty else {
            withAnimation(islandSpring) { event = nil }
            return
        }
        let e = queue.removeFirst()
        withAnimation(islandSpring) { event = e }
        timer = Timer.scheduledTimer(withTimeInterval: max(1.5, Pref.num(Pref.eventSec)), repeats: false) { _ in
            Task { @MainActor in Island.shared.advance() }
        }
    }

    /// Screenshot / test hook: keep an event up until `dismiss()`.
    func pin(_ e: IslandEvent) {
        timer?.invalidate()
        queue = []
        withAnimation(islandSpring) { event = e }
    }
    func dismiss() {
        timer?.invalidate()
        queue = []
        withAnimation(islandSpring) { event = nil }
    }
}

enum IslandSize {
    static var earsAlways: Bool { Pref.str(Pref.islandEars) == "always" }
    static let ear: CGFloat = 64
    static func of(_ mode: Island.Mode, notchW: CGFloat, notchH: CGFloat) -> CGSize {
        switch mode {
        // ears only while always-on; otherwise the island is exactly the notch (black on black: invisible)
        case .compact: return CGSize(width: earsAlways ? notchW + 2 * ear : notchW, height: notchH)
        case .peek: return CGSize(width: notchW + 2 * ear, height: notchH)
        case .event: return CGSize(width: max(notchW + 2 * ear, 400), height: notchH + 62)
        // the hover height only sizes the hover-detection rect; the visible panel grows with its content
        case .hover: return CGSize(width: 460, height: notchH + 520)
        }
    }
}

/// Test builds set QUOTA_NOTCH_LEVEL_BOOST so a test copy draws above an installed notch app.
let LEVEL_BOOST = Int(ProcessInfo.processInfo.environment["QUOTA_NOTCH_LEVEL_BOOST"] ?? "") ?? 0

@MainActor
final class IslandController {
    static let shared = IslandController()
    private(set) var panel: NSPanel?
    private var monitors: [Any] = []
    private var notch = (w: CGFloat(0), h: CGFloat(0), screen: NSRect.zero)
    private var pending: DispatchWorkItem?
    private var started = false

    func start() {
        guard !started else { return }
        started = true
        NotificationCenter.default.addObserver(forName: NSApplication.didChangeScreenParametersNotification, object: nil, queue: .main) { _ in
            Task { @MainActor in
                IslandController.shared.rebuild()
                Store.main.writeStatus()
            }
        }
        rebuild()
    }

    func rebuild() {
        panel?.orderOut(nil)
        panel = nil
        monitors.forEach { NSEvent.removeMonitor($0) }
        monitors = []
        guard islandEnabled(), let s = notchScreen() else { return }
        var w = s.frame.width - (s.auxiliaryTopLeftArea?.width ?? 0) - (s.auxiliaryTopRightArea?.width ?? 0)
        var h = s.safeAreaInsets.top
        if w <= 0 || w >= s.frame.width { w = 200 }   // QUOTA_NOTCH_FAKE_NOTCH on a screen without one
        if h <= 0 { h = 32 }
        notch = (w, h, s.frame)
        let size = CGSize(width: 500, height: h + 680)
        let p = NSPanel(contentRect: NSRect(x: s.frame.midX - size.width / 2, y: s.frame.maxY - size.height,
                                            width: size.width, height: size.height),
                        styleMask: [.borderless, .nonactivatingPanel], backing: .buffered, defer: false)
        p.isOpaque = false
        p.backgroundColor = .clear
        p.hasShadow = false
        p.level = NSWindow.Level(rawValue: NSWindow.Level.mainMenu.rawValue + 3 + LEVEL_BOOST)
        p.collectionBehavior = [.canJoinAllSpaces, .stationary, .fullScreenAuxiliary, .ignoresCycle]
        p.ignoresMouseEvents = true
        p.acceptsMouseMovedEvents = true
        p.contentView = NSHostingView(rootView: IslandView(store: .main, island: .shared, notchW: w, notchH: h))
        p.orderFrontRegardless()
        panel = p
        let handler: (NSEvent) -> Void = { _ in Task { @MainActor in IslandController.shared.track() } }
        if let m = NSEvent.addGlobalMonitorForEvents(matching: [.mouseMoved, .leftMouseDragged], handler: handler) { monitors.append(m) }
        if let m = NSEvent.addLocalMonitorForEvents(matching: [.mouseMoved], handler: { e in handler(e); return e }) { monitors.append(m) }
    }

    private func rect(_ mode: Island.Mode) -> NSRect {
        let sz = IslandSize.of(mode, notchW: notch.w, notchH: notch.h)
        return NSRect(x: notch.screen.midX - sz.width / 2, y: notch.screen.maxY - sz.height, width: sz.width, height: sz.height)
    }

    /// Hover in after a short dwell (passing over the ears on the way to a menu doesn't pop it), out after a grace.
    private func track() {
        guard panel != nil else { return }
        let isl = Island.shared
        let inside = rect(isl.mode).insetBy(dx: -2, dy: -2).contains(NSEvent.mouseLocation)
        guard inside != isl.hover else {
            pending?.cancel()
            pending = nil
            return
        }
        guard pending == nil else { return }
        let task = DispatchWorkItem {
            MainActor.assumeIsolated {
                IslandController.shared.pending = nil
                IslandController.shared.setHover(inside)
            }
        }
        pending = task
        DispatchQueue.main.asyncAfter(deadline: .now() + (inside ? max(0.05, Pref.num(Pref.hoverDelay)) : 0.4), execute: task)
    }

    func setHover(_ on: Bool) {
        withAnimation(islandSpring) { Island.shared.hover = on }
        panel?.ignoresMouseEvents = !on
        if on { Store.main.panelOpened() }
    }
}

/// Provider logo inside one or two usage rings (outer = first window), coloured by level.
struct RingGauge: View {
    let provider: String?
    let fractions: [Double]
    var size: CGFloat = 20
    var badge: String? = nil  // account initial, when the provider has several accounts
    var dark = true
    var body: some View {
        ZStack {
            ForEach(Array(fractions.prefix(2).enumerated()), id: \.offset) { i, f in
                let inset = CGFloat(i) * size * 0.2
                Circle().stroke((dark ? Color.white : Color.primary).opacity(0.16), lineWidth: 2).padding(inset)
                Circle().trim(from: 0, to: min(max(f, 0), 1))
                    .stroke(levelColor(f * 100), style: StrokeStyle(lineWidth: 2, lineCap: .round))
                    .rotationEffect(.degrees(-90))
                    .padding(inset)
                    .animation(motionOn() ? .easeOut(duration: 0.6) : nil, value: f)
            }
            if let p = provider, let ic = providerIcon(p) {
                Image(nsImage: ic).renderingMode(.template).resizable().scaledToFit()
                    .foregroundStyle(dark ? Color.white : Color.primary)
                    .frame(width: size * (fractions.count > 1 ? 0.34 : 0.46))
            }
        }
        .frame(width: size, height: size)
        .overlay(alignment: .bottomTrailing) {
            if let b = badge {
                Text(b).font(.system(size: size * 0.36, weight: .heavy, design: .rounded)).foregroundStyle(.black)
                    .frame(minWidth: size * 0.42, minHeight: size * 0.42)
                    .background(Circle().fill(Color.white))
                    .offset(x: size * 0.14, y: size * 0.1)
            }
        }
    }
}

struct IslandView: View {
    @ObservedObject var store: Store
    @ObservedObject var island: Island
    let notchW: CGFloat
    let notchH: CGFloat
    @AppStorage(Pref.lang) private var lang = "auto"
    @AppStorage(Pref.islandEars) private var earsSetting = "hover"
    @AppStorage(Pref.rightEar) private var rightEar = "sessions"

    /// Left ear: one ring per account (fullest first), two fit.
    var rings: [(account: QuotaAccount, fractions: [Double])] {
        let accts = (store.quota?.visible ?? []).sorted { ($0.worstUsed ?? 0) > ($1.worstUsed ?? 0) }
        return accts.prefix(2).map { a in (a, a.measured.prefix(2).map { ($0.used ?? 0) / 100 }) }
    }

    var body: some View {
        let sz = IslandSize.of(island.mode, notchW: notchW, notchH: notchH)
        let r: CGFloat = island.mode == .compact ? 10 : 24
        let shape = UnevenRoundedRectangle(bottomLeadingRadius: r, bottomTrailingRadius: r, style: .continuous).fill(.black)
        VStack(spacing: 0) {
            Group {
                if island.mode == .hover {
                    // the hover panel sizes to its content (the black shape follows it)
                    content.frame(width: sz.width).fixedSize(horizontal: false, vertical: true).background(shape)
                } else {
                    ZStack(alignment: .top) { shape; content }.frame(width: sz.width, height: sz.height)
                }
            }
            .contentShape(Rectangle())
            .onTapGesture {
                // the notch itself opens nothing; an alert about a session opens that session
                if let s = island.event?.session { store.open(s) }
            }
            Spacer(minLength: 0)
        }
        .frame(maxWidth: .infinity, maxHeight: .infinity, alignment: .top)
        .environment(\.locale, appLocale())
        .environment(\.colorScheme, .dark)
        .id(lang)
    }

    var content: some View {
        VStack(spacing: 0) {
            if island.mode != .compact || IslandSize.earsAlways {
                ears.frame(height: notchH).transition(.opacity)
            }
            switch island.mode {
            case .event: if let e = island.event { eventView(e).transition(.opacity.combined(with: .scale(scale: 0.9, anchor: .top))) }
            case .hover: hoverView.transition(.opacity.combined(with: .scale(scale: 0.95, anchor: .top)))
            case .compact, .peek: EmptyView()
            }
        }
    }

    var ears: some View {
        let multi = store.quota?.multiAccountBases ?? []
        return HStack(spacing: 0) {
            HStack(spacing: 6) {
                ForEach(rings, id: \.account.key) { g in
                    RingGauge(provider: g.account.base, fractions: g.fractions, size: 20,
                              badge: multi.contains(g.account.base) ? g.account.accountLabel.map { String($0.prefix(1)).uppercased() } : nil)
                        .opacity(g.account.stale ? 0.55 : 1)
                        .scaleEffect(island.highlight == g.account.key ? 1.3 : 1)
                        .shadow(color: island.highlight == g.account.key ? levelColor((g.fractions.first ?? 0) * 100) : .clear, radius: 6)
                }
            }
            .frame(width: IslandSize.ear, alignment: .center)
            Spacer(minLength: notchW)
            HStack(spacing: 5) {
                if store.hermesPath == nil && store.quota == nil {
                    Image(systemName: "bolt.horizontal.circle").foregroundStyle(.red)
                } else if rightEar == "cost" {
                    Text(store.snapshot?.costToday.map(money) ?? "—")
                        .font(.system(size: 12, weight: .semibold, design: .rounded)).monospacedDigit().foregroundStyle(.white)
                        .minimumScaleFactor(0.7).lineLimit(1)
                } else if rightEar == "sessions" {
                    let c = store.counts
                    if c.needsApproval > 0 {
                        Image(systemName: "hand.raised.fill").font(.system(size: 10, weight: .semibold)).foregroundStyle(.orange)
                        Text("\(c.needsApproval)").font(.system(size: 12, weight: .semibold, design: .rounded)).foregroundStyle(.orange)
                    }
                    let idle = max(0, c.open - c.working - c.needsApproval)
                    PulseDot(color: c.working > 0 ? .green : (idle > 0 ? Color.green.opacity(0.45) : .gray),
                             animated: c.working > 0 && island.mode != .compact).frame(width: 6, height: 6)
                    Text("\(c.working)").font(.system(size: 13, weight: .semibold, design: .rounded)).monospacedDigit()
                        .foregroundStyle(c.working > 0 ? .white : .white.opacity(0.5))
                    if idle > 0 && c.needsApproval == 0 {
                        Text("+\(idle)").font(.system(size: 10, weight: .medium, design: .rounded)).monospacedDigit()
                            .foregroundStyle(.white.opacity(0.45))
                    }
                }
            }
            .lineLimit(1)
            .fixedSize()
            .scaleEffect(island.highlight == "sessions" ? 1.3 : 1)
            .shadow(color: island.highlight == "sessions" ? .green : .clear, radius: 6)
            .frame(width: IslandSize.ear, alignment: .center)
        }
        .padding(.horizontal, island.mode == .compact || island.mode == .peek ? 0 : 14)
    }

    func eventView(_ e: IslandEvent) -> some View {
        HStack(spacing: 12) {
            if let g = e.gauge {
                RingGauge(provider: e.provider, fractions: [g], size: 34)
            } else {
                Image(systemName: e.symbol).font(.system(size: 20)).foregroundStyle(e.tint).frame(width: 34)
            }
            VStack(alignment: .leading, spacing: 2) {
                Text(e.title).font(.system(size: 13, weight: .semibold)).foregroundStyle(.white).lineLimit(1)
                Text(e.detail).font(.system(size: 11)).foregroundStyle(.white.opacity(0.65)).lineLimit(2)
            }
            Spacer(minLength: 0)
            if e.session != nil {
                Image(systemName: "arrow.up.forward.square").font(.system(size: 12)).foregroundStyle(.white.opacity(0.5))
            }
        }
        .padding(.horizontal, 20)
        .padding(.top, 6)
    }

    var hoverView: some View {
        DashboardView(store: store, dark: true, compact: true)
            .padding(.horizontal, 18)
            .padding(.top, 8)
            .padding(.bottom, 14)
            .frame(width: IslandSize.of(.hover, notchW: notchW, notchH: notchH).width)
    }
}

struct PulseDot: View {
    let color: Color
    var animated = true
    @State private var on = false
    var body: some View {
        Circle().fill(color)
            .overlay(Circle().stroke(color.opacity(on ? 0 : 0.6), lineWidth: on ? 6 : 0).scaleEffect(on ? 2 : 1))
            .onAppear {
                guard animated, motionOn() else { return }
                withAnimation(.easeOut(duration: 1.4).repeatForever(autoreverses: false)) { on = true }
            }
    }
}

func levelColor(_ used: Double) -> Color { used >= 85 ? .red : used >= 60 ? .orange : .green }

let PROVIDER_ICON = ["anthropic": "claude", "copilot": "githubcopilot", "openai-codex": "openai", "openai": "openai",
                     "gemini": "googlegemini", "nvidia": "nvidia", "openrouter": "openrouter"]
@MainActor var iconCache: [String: NSImage] = [:]

@MainActor func providerIcon(_ base: String) -> NSImage? {
    guard let name = PROVIDER_ICON[base] else { return nil }
    if let hit = iconCache[name] { return hit }
    guard let url = Bundle.main.url(forResource: name, withExtension: "svg", subdirectory: "icons"),
          let img = NSImage(contentsOf: url) else { return nil }
    iconCache[name] = img
    return img
}
