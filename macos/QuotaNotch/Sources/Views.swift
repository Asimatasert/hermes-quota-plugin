// Quota Notch — shared views: the dashboard (notch hover panel and menu bar window), session rows, settings.

import AppKit
import ServiceManagement
import SwiftUI

// MARK: - dashboard

struct DashboardView: View {
    @ObservedObject var store: Store
    let dark: Bool        // the notch is always black; the menu bar window follows the system
    let compact: Bool     // notch hover: honours the "hover panel shows" settings
    @AppStorage(Pref.hoverQuotas) private var hoverQuotas = true
    @AppStorage(Pref.hoverSessions) private var hoverSessions = true
    @AppStorage(Pref.hoverCost) private var hoverCost = true

    var fg: Color { dark ? .white : .primary }
    var dim: Color { dark ? .white.opacity(0.55) : .secondary }
    var hair: Color { dark ? .white.opacity(0.12) : Color.primary.opacity(0.1) }
    var showQuotas: Bool { !compact || hoverQuotas }
    var showSessions: Bool { !compact || hoverSessions }
    var showCost: Bool { !compact || hoverCost }

    var body: some View {
        VStack(alignment: .leading, spacing: 8) {
            hints
            if showQuotas { quotas }
            if showSessions || showCost {
                Rectangle().fill(hair).frame(height: 1)
                summary
            }
            if showSessions { sessions }
            if let t = store.toast {
                Text(t).font(.system(size: 10.5)).foregroundStyle(fg).lineLimit(3)
                    .padding(.vertical, 4).padding(.horizontal, 8)
                    .frame(maxWidth: .infinity, alignment: .leading)
                    .background(fg.opacity(0.12), in: RoundedRectangle(cornerRadius: 8))
            }
        }
        .foregroundStyle(fg)
    }

    @ViewBuilder var hints: some View {
        if store.hermesPath == nil, store.quota == nil {
            hint(icon: "exclamationmark.triangle.fill", tint: .orange,
                 text: L("hermes not found — set its path in Settings", "hermes bulunamadı — yolunu Ayarlar'dan gir"), command: nil)
        } else if store.collectionOff {
            hint(icon: "pause.circle.fill", tint: .yellow,
                 text: L("Session monitor is off. Enable it with:", "Oturum izleme kapalı. Açmak için:"), command: Snapshot.enableCommand)
        } else if case .failed(let why) = store.snapshot?.collection {
            hint(icon: "exclamationmark.triangle.fill", tint: .orange, text: L("Monitor error: ", "Monitor hatası: ") + why.prefix(120), command: nil)
        } else if let e = store.lastError {
            hint(icon: "exclamationmark.triangle.fill", tint: .orange, text: e, command: nil)
        }
    }

    func hint(icon: String, tint: Color, text: String, command: String?) -> some View {
        VStack(alignment: .leading, spacing: 4) {
            Label { Text(text).font(.system(size: 11)) } icon: { Image(systemName: icon).foregroundStyle(tint) }
            if let command {
                HStack(spacing: 6) {
                    Text(command).font(.system(size: 10, design: .monospaced)).lineLimit(1).minimumScaleFactor(0.6).textSelection(.enabled)
                    Spacer(minLength: 4)
                    Button { store.copy(command, note: L("Copied: \(command)", "Kopyalandı: \(command)")) } label: {
                        Image(systemName: "doc.on.doc")
                    }
                    .buttonStyle(.borderless).help(L("Copy", "Kopyala"))
                }
                .padding(6)
                .background(fg.opacity(0.08), in: RoundedRectangle(cornerRadius: 6))
            }
        }
    }

    @ViewBuilder var quotas: some View {
        let accts = store.quota?.visible ?? []
        if accts.isEmpty {
            Text(store.quota == nil ? L("Loading quotas…", "Kotalar yükleniyor…") : L("No quota readings yet", "Henüz kota okuması yok"))
                .font(.system(size: 11)).foregroundStyle(dim)
        } else {
            let multi = store.quota?.multiAccountBases ?? []
            ForEach(accts.prefix(compact ? 4 : 12)) { a in
                QuotaAccountRow(store: store, account: a, multi: multi, dark: dark, compact: compact)
            }
        }
    }

    var summary: some View {
        HStack(spacing: 10) {
            if showSessions {
                let c = store.counts
                Label(L("\(c.working) working", "\(c.working) çalışıyor"), systemImage: "bolt.horizontal.circle.fill")
                    .foregroundStyle(c.working == 0 ? Color.gray : .green)
                if c.needsApproval > 0 { Text(L("\(c.needsApproval) needs approval", "\(c.needsApproval) onay bekliyor")).foregroundStyle(.orange).fontWeight(.semibold) }
                if c.yourTurn > 0 { Text(L("\(c.yourTurn) your turn", "\(c.yourTurn) sıra sende")).foregroundStyle(dim) }
                if c.stalled > 0 { Text(L("\(c.stalled) stalled?", "\(c.stalled) takıldı mı?")).foregroundStyle(.yellow.opacity(0.85)) }
            }
            Spacer(minLength: 4)
            if showCost, let cost = store.snapshot?.costToday {
                Text(L("today ", "bugün ") + money(cost)).monospacedDigit().foregroundStyle(dim)
                    .help(L("API-price equivalent (estimate), not a bill", "API fiyatı karşılığı (tahmin), fatura değil"))
            }
        }
        .font(.system(size: 11))
    }

    @ViewBuilder var sessions: some View {
        let now = store.snapshot?.now ?? Date().timeIntervalSince1970
        let current = store.current
        let groups = store.groups
        if current == nil && groups.isEmpty {
            Text(store.snapshot == nil || store.collectionOff ? "" : L("No open sessions", "Açık oturum yok"))
                .font(.system(size: 11)).foregroundStyle(dim)
        } else {
            // short lists hug their content; long ones scroll (the notch takes mouse events while hovered)
            if store.mains.count > 7 {
                ScrollView(.vertical, showsIndicators: true) { sessionList(current: current, groups: groups, now: now) }
                    .frame(height: compact ? 300 : 340)
            } else {
                sessionList(current: current, groups: groups, now: now)
            }
        }
    }
    func sessionList(current: LiveSession?, groups: [SessionGroup], now: Double) -> some View {
        VStack(alignment: .leading, spacing: 8) {
            if let s = current {
                VStack(alignment: .leading, spacing: 3) {
                    Text(L("NOW", "ŞİMDİ")).font(.system(size: 9.5, weight: .bold)).foregroundStyle(Color.accentColor)
                    SessionRowView(store: store, s: s, now: now, dark: dark)
                }
                .padding(.leading, 8)
                .overlay(alignment: .leading) { Rectangle().fill(Color.accentColor).frame(width: 2) }
            }
            ForEach(groups, id: \.key) { g in
                VStack(alignment: .leading, spacing: 4) {
                    HStack {
                        Text(g.title.uppercased()).font(.system(size: 9.5, weight: .semibold)).foregroundStyle(dim)
                        Spacer()
                        Text("\(g.sessions.count)").font(.system(size: 9.5)).foregroundStyle(dim)
                    }
                    ForEach(g.sessions) { s in SessionRowView(store: store, s: s, now: now, dark: dark) }
                }
            }
            if store.snapshot?.scope == "active" {
                Text(L("Only this profile's sessions. Other profiles and Claude Code / Copilot: set monitorScope to all.",
                       "Yalnızca bu profilin oturumları. Diğer profiller ve Claude Code / Copilot için monitorScope'u all yap."))
                    .font(.system(size: 9.5)).foregroundStyle(dim)
            }
        }
        .padding(.trailing, 4)
    }

}

/// used % over the last day (0...100 mapped to the frame height).
struct Sparkline: Shape {
    let values: [Double]
    func path(in r: CGRect) -> Path {
        var p = Path()
        guard values.count >= 2 else { return p }
        for (i, v) in values.enumerated() {
            let pt = CGPoint(x: r.minX + r.width * CGFloat(i) / CGFloat(values.count - 1),
                             y: r.maxY - r.height * CGFloat(min(max(v, 0), 100) / 100))
            if i == 0 { p.move(to: pt) } else { p.addLine(to: pt) }
        }
        return p
    }
}

struct QuotaAccountRow: View {
    @ObservedObject var store: Store
    let account: QuotaAccount
    let multi: Set<String>
    let dark: Bool
    var compact = false
    var body: some View {
        let now = Date().timeIntervalSince1970
        HStack(alignment: .top, spacing: 9) {
            RingGauge(provider: account.base, fractions: account.measured.prefix(2).map { ($0.used ?? 0) / 100 }, size: 26, dark: dark)
                .opacity(account.stale ? 0.55 : 1)
            VStack(alignment: .leading, spacing: 2) {
                HStack(spacing: 6) {
                    Text(providerName(account, multi: multi)).font(.system(size: 11.5, weight: .semibold)).lineLimit(1)
                    if account.stale {
                        Text(L("last known", "son değer")).font(.system(size: 9)).foregroundStyle(.secondary)
                    }
                }
                ForEach(Array(account.measured.prefix(3).enumerated()), id: \.offset) { _, w in
                    let used = w.used ?? 0
                    HStack(spacing: 6) {
                        Text(windowName(w.label)).font(.system(size: 10.5)).lineLimit(1).minimumScaleFactor(0.8).frame(width: 74, alignment: .leading)
                        Capsule().fill((dark ? Color.white : Color.primary).opacity(0.14)).frame(width: 64, height: 4)
                            .overlay(alignment: .leading) { Capsule().fill(levelColor(used)).frame(width: 64 * min(max(used, 0), 100) / 100, height: 4) }
                        Text(pct(used)).font(.system(size: 10.5, weight: .semibold)).monospacedDigit().frame(width: 34, alignment: .trailing)
                        if !compact, case let pts = store.series(account, w), pts.count >= 2 {
                            Sparkline(values: pts).stroke(levelColor(used).opacity(0.8), lineWidth: 1).frame(width: 36, height: 11)
                                .help(L("Last 24 h", "Son 24 saat"))
                        }
                        if let r = w.reset, r > now {
                            Text(L("resets \(span(r - now))", "sıfırlanma \(span(r - now))")).font(.system(size: 10)).foregroundStyle(.secondary).lineLimit(1)
                        }
                        if let b = store.burn(account, w), let ph = b.perHour, ph > 0.5 {
                            Text(String(format: L("+%.0f%%/h", "+%%%.0f/sa"), ph)).font(.system(size: 9.5)).monospacedDigit()
                                .foregroundStyle(b.fillsBeforeReset ? Color.orange : .secondary)
                                .help(b.etaFull.map { L("full in ~\(span($0 - now)) at this rate", "bu hızla ~\(span($0 - now)) içinde dolar") } ?? "")
                        }
                    }
                }
                if let h = store.planHint(account), let sum = h.summary {
                    Text(sum).font(.system(size: 9.5)).foregroundStyle(.secondary).lineLimit(2)
                }
            }
            Spacer(minLength: 0)
        }
    }
}

struct SessionRowView: View {
    @ObservedObject var store: Store
    let s: LiveSession
    let now: Double
    let dark: Bool
    @State private var hovered = false

    var stateText: (String, Color)? {
        switch s.state {
        case "needs_approval": return (L("needs approval", "onay bekliyor"), .orange)
        case "waiting": return (L("your turn", "sıra sende"), .green)
        case "stalled": return (L("stalled?", "takıldı mı?"), .yellow)
        case "working": return (L("working", "çalışıyor"), .secondary)
        default: return nil
        }
    }

    var body: some View {
        HStack(alignment: .top, spacing: 8) {
            Group {
                if s.state == "working" { PulseDot(color: .green) }
                else if s.state == "needs_approval" { Circle().fill(Color.orange) }
                else { Circle().fill(Color.green.opacity(0.45)) }
            }
            .frame(width: 7, height: 7).padding(.top, 5)
            VStack(alignment: .leading, spacing: 1) {
                Text(s.title ?? L("(untitled)", "(başlıksız)")).font(.system(size: 11.5)).lineLimit(1).truncationMode(.tail)
                HStack(spacing: 4) {
                    if let st = stateText {
                        Text(st.0).foregroundStyle(st.1).fontWeight(s.state == "needs_approval" ? .bold : .regular)
                    }
                    Text([modelShort(s.model), idle].compactMap { $0 }.joined(separator: " · ")).foregroundStyle(.secondary)
                }
                .font(.system(size: 10)).lineLimit(1)
            }
            Spacer(minLength: 6)
            VStack(alignment: .trailing, spacing: 1) {
                Text(compactNumber(s.tokens)).font(.system(size: 10.5)).monospacedDigit()
                if let c = s.cost, c > 0 { Text(money(c)).font(.system(size: 10)).monospacedDigit().foregroundStyle(.secondary) }
            }
            Image(systemName: s.isHermes ? "arrow.up.forward.square" : "doc.on.doc")
                .font(.system(size: 10)).foregroundStyle(.secondary).opacity(hovered ? 1 : 0.4).padding(.top, 2)
        }
        .padding(.vertical, 2).padding(.horizontal, 4)
        .background((s.state == "needs_approval" ? Color.orange.opacity(0.16) : (hovered ? (dark ? Color.white : .primary).opacity(0.08) : .clear)),
                    in: RoundedRectangle(cornerRadius: 6))
        .contentShape(Rectangle())
        .onHover { hovered = $0 }
        .onTapGesture { store.open(s) }
        .help(s.isHermes ? L("Click to open in Hermes", "Tıkla: Hermes'te aç") : L("Click to copy the resume command", "Tıkla: devam komutunu kopyala"))
    }

    var idle: String? {
        guard let l = s.last, now - l > 60 else { return nil }
        return L("idle \(span(now - l))", "\(span(now - l)) sessiz")
    }
}

func modelShort(_ m: String?) -> String? {
    guard var m, !m.isEmpty else { return nil }
    if let slash = m.lastIndex(of: "/") { m = String(m[m.index(after: slash)...]) }
    return m.count > 28 ? String(m.prefix(27)) + "…" : m
}

// MARK: - menu bar

struct MenuPanel: View {
    @ObservedObject var store: Store
    @AppStorage(Pref.lang) private var lang = "auto"
    var body: some View {
        VStack(alignment: .leading, spacing: 10) {
            HStack(spacing: 8) {
                Circle().fill(store.lastError == nil && store.quota != nil ? Color.green : .orange).frame(width: 8, height: 8)
                Text("Quota Notch").font(.headline)
                Spacer()
                if let u = store.updated { Text(u, style: .time).font(.caption).foregroundStyle(.secondary) }
                Button { store.refreshAll() } label: { Image(systemName: "arrow.clockwise") }.buttonStyle(.borderless)
                    .help(L("Refresh now", "Şimdi yenile"))
                Button { SettingsWindow.show() } label: { Image(systemName: "gearshape") }.buttonStyle(.borderless)
                    .help(L("Settings", "Ayarlar"))
            }
            Divider()
            DashboardView(store: store, dark: false, compact: false)
            Divider()
            HStack {
                Text(L("live every \(Int(Pref.num(Pref.liveEvery)))s · quotas \(Int(Pref.num(Pref.quotaEvery)))s",
                       "canlı \(Int(Pref.num(Pref.liveEvery))) sn · kota \(Int(Pref.num(Pref.quotaEvery))) sn"))
                    .font(.caption2).foregroundStyle(.secondary)
                Spacer()
                Button(L("Quit", "Çık")) { NSApp.terminate(nil) }.controlSize(.small)
            }
        }
        .padding(14)
        .frame(width: 420)
        .environment(\.locale, appLocale())
        .id(lang)
        .onAppear {
            store.refreshAll()
            store.panelOpened()
        }
    }
}

struct BarLabel: View {
    @ObservedObject var store: Store
    @AppStorage(Pref.barQuota) private var showQuota = true
    @AppStorage(Pref.barCount) private var showCount = true
    var body: some View {
        let t = store.tightest
        let c = store.counts
        HStack(spacing: 4) {
            Image(systemName: c.needsApproval > 0 ? "hand.raised.fill" : "gauge.with.dots.needle.33percent")
            if showQuota, let t, let u = t.window.used { Text(pct(u)) }
            if showCount, store.snapshot != nil, !store.collectionOff { Text("· \(c.working)") }
        }
    }
}

// MARK: - settings

@MainActor
enum SettingsWindow {
    static var window: NSWindow?
    static func show() {
        if window == nil {
            let w = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 520, height: 680), styleMask: [.titled, .closable, .resizable],
                             backing: .buffered, defer: false)
            w.title = L("Quota Notch Settings", "Quota Notch Ayarları")
            w.isReleasedWhenClosed = false
            w.contentView = NSHostingView(rootView: ScrollView { SettingsView(store: .main).padding(16) }.frame(minWidth: 480, minHeight: 400))
            w.center()
            window = w
        }
        NSApp.activate(ignoringOtherApps: true)
        window?.makeKeyAndOrderFront(nil)
    }
}

struct SettingsSection<Content: View>: View {
    let title: String
    let icon: String
    @ViewBuilder var content: Content
    var body: some View {
        VStack(alignment: .leading, spacing: 9) {
            Label(title, systemImage: icon).font(.callout.weight(.semibold))
            content
        }
        .frame(maxWidth: .infinity, alignment: .leading)
        .padding(12)
        .background(.quaternary.opacity(0.4), in: RoundedRectangle(cornerRadius: 10))
    }
}

/// Settings rows: label on the left, switch on the right.
struct TrailingSwitch: ToggleStyle {
    func makeBody(configuration: Configuration) -> some View {
        HStack(spacing: 8) {
            configuration.label.font(.callout)
            Spacer(minLength: 8)
            Toggle("", isOn: configuration.$isOn).labelsHidden().toggleStyle(.switch)
        }
    }
}

struct SliderRow: View {
    let title: String
    @Binding var value: Double
    let range: ClosedRange<Double>
    let step: Double
    let format: (Double) -> String
    var body: some View {
        VStack(alignment: .leading, spacing: 2) {
            HStack { Text(title); Spacer(); Text(format(value)).monospacedDigit().foregroundStyle(.secondary) }.font(.callout)
            Slider(value: $value, in: range, step: step).controlSize(.small)
        }
    }
}

struct SegmentRow<Content: View>: View {
    let title: String
    @ViewBuilder var picker: Content
    var body: some View {
        HStack(spacing: 10) {
            Text(title).font(.callout).fixedSize()
            Spacer(minLength: 6)
            picker.pickerStyle(.segmented).labelsHidden().fixedSize()
        }
    }
}

func seconds(_ v: Double) -> String { v < 1 ? String(format: L("%.2f s", "%.2f sn"), v) : L("\(Int(v)) s", "\(Int(v)) sn") }

struct SettingsView: View {
    @ObservedObject var store: Store
    @AppStorage(Pref.display) private var display = "both"
    @AppStorage(Pref.lang) private var lang = "auto"
    @AppStorage(Pref.motion) private var motion = true
    @AppStorage(Pref.islandEars) private var ears = "hover"
    @AppStorage(Pref.rightEar) private var rightEar = "sessions"
    @AppStorage(Pref.islandEvents) private var events = true
    @AppStorage(Pref.peekSec) private var peekSec = 12.0
    @AppStorage(Pref.eventSec) private var eventSec = 5.0
    @AppStorage(Pref.hoverDelay) private var hoverDelay = 0.22
    @AppStorage(Pref.stepPct) private var stepPct = 2.0
    @AppStorage(Pref.hoverQuotas) private var hoverQuotas = true
    @AppStorage(Pref.hoverSessions) private var hoverSessions = true
    @AppStorage(Pref.hoverCost) private var hoverCost = true
    @AppStorage(Pref.barQuota) private var barQuota = true
    @AppStorage(Pref.barCount) private var barCount = true
    @AppStorage(Pref.notifyQuota) private var nQuota = true
    @AppStorage(Pref.notifyAgents) private var nAgents = true
    @AppStorage(Pref.liveEvery) private var liveEvery = 30.0
    @AppStorage(Pref.quotaEvery) private var quotaEvery = 60.0
    @AppStorage(Pref.hermesPath) private var hermesPath = ""
    @AppStorage(Pref.profile) private var profile = ""
    @State private var armed = false
    @State private var login = SMAppService.mainApp.status == .enabled
    @State private var loginError: String?

    var hasNotch: Bool { notchScreen() != nil }

    var body: some View {
        VStack(alignment: .leading, spacing: 12) {
            appearance
            if hasNotch && display != "menubar" { island }
            if display != "island" || !hasNotch { menuBar }
            notifications
            dataSource
            general
        }
        .toggleStyle(TrailingSwitch())
        .controlSize(.small)
        .environment(\.locale, appLocale())
        .id(lang)
    }

    var appearance: some View {
        SettingsSection(title: L("Appearance", "Görünüm"), icon: "paintbrush") {
            if hasNotch {
                SegmentRow(title: L("Show in", "Nerede")) {
                    Picker("", selection: Binding(get: { display }, set: { v in
                        display = v
                        IslandController.shared.rebuild()
                        Store.main.writeStatus()
                    })) {
                        Text(L("Notch + menu bar", "Çentik + menü")).tag("both")
                        Text(L("Notch", "Çentik")).tag("island")
                        Text(L("Menu bar", "Menü")).tag("menubar")
                    }
                }
            } else {
                Text(L("This Mac has no notch: Quota Notch lives in the menu bar.", "Bu Mac'te çentik yok: Quota Notch menü çubuğunda çalışır."))
                    .font(.caption).foregroundStyle(.secondary)
            }
            SegmentRow(title: L("Language", "Dil")) {
                Picker("", selection: $lang) {
                    Text(L("System", "Sistem")).tag("auto")
                    Text("English").tag("en")
                    Text("Türkçe").tag("tr")
                }
            }
            Toggle(L("Animations", "Animasyonlar"), isOn: $motion)
        }
    }

    var island: some View {
        SettingsSection(title: L("Notch island", "Çentik adası"), icon: "rectangle.topthird.inset.filled") {
            SegmentRow(title: L("Rings & count", "Halkalar")) {
                Picker("", selection: $ears) {
                    Text(L("On hover", "Üzerine gelince")).tag("hover")
                    Text(L("Always", "Her zaman")).tag("always")
                }
            }
            SegmentRow(title: L("Right side", "Sağ taraf")) {
                Picker("", selection: $rightEar) {
                    Text(L("Sessions", "Oturum")).tag("sessions")
                    Text(L("Cost today", "Bugünkü maliyet")).tag("cost")
                    Text(L("None", "Yok")).tag("none")
                }
            }
            Divider()
            Toggle(L("Pop alerts out of the notch", "Uyarıları çentikten göster"), isOn: $events)
            SliderRow(title: L("Peek on change for", "Değişince görünme süresi"), value: $peekSec, range: 3...30, step: 1, format: seconds)
                .disabled(!events)
            SliderRow(title: L("Alerts stay for", "Uyarı görünme süresi"), value: $eventSec, range: 2...10, step: 0.5, format: seconds)
                .disabled(!events)
            SegmentRow(title: L("Peek when a quota moves by", "Kota şu kadar değişince göster")) {
                Picker("", selection: $stepPct) {
                    Text("1%").tag(1.0)
                    Text("2%").tag(2.0)
                    Text("5%").tag(5.0)
                    Text("10%").tag(10.0)
                }
            }
            .disabled(!events)
            SliderRow(title: L("Hover delay", "Üzerine gelme gecikmesi"), value: $hoverDelay, range: 0...1, step: 0.05, format: seconds)
            Divider()
            Text(L("Hover panel shows", "Üzerine gelince görünenler")).font(.caption).foregroundStyle(.secondary)
            Toggle(L("Quotas", "Kotalar"), isOn: $hoverQuotas)
            Toggle(L("Running sessions", "Çalışan oturumlar"), isOn: $hoverSessions)
            Toggle(L("Today's API-equivalent cost (estimate)", "Bugünkü API karşılığı (tahmini)"), isOn: $hoverCost)
        }
    }

    var menuBar: some View {
        SettingsSection(title: L("Menu bar", "Menü çubuğu"), icon: "menubar.rectangle") {
            Toggle(L("Fullest quota", "En dolu kota"), isOn: $barQuota)
            Toggle(L("Working session count", "Çalışan oturum sayısı"), isOn: $barCount)
        }
    }

    var notifications: some View {
        SettingsSection(title: L("Notifications", "Bildirimler"), icon: "bell.badge") {
            Toggle(L("Quota 80% / 95% and filling soon", "Kota %80 / %95 ve yakında dolacak"), isOn: $nQuota)
            Toggle(L("Agents: approval, stalled, your turn", "Ajanlar: onay, takıldı, sıra sende"), isOn: $nAgents)
            Text(L("Banners use macOS Notification Center; the notch pop-downs follow “Pop alerts out of the notch”.",
                   "Bildirimler macOS Bildirim Merkezi'ni kullanır; çentik uyarıları “Uyarıları çentikten göster” ayarına bağlıdır."))
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    var dataSource: some View {
        SettingsSection(title: L("Data source", "Veri kaynağı"), icon: "terminal") {
            HStack {
                Text(L("hermes command", "hermes komutu")).font(.callout)
                Spacer()
                TextField(L("auto", "otomatik"), text: $hermesPath).textFieldStyle(.roundedBorder).frame(width: 260)
            }
            Text(store.hermesPath.map { L("Using: \($0)", "Kullanılan: \($0)") } ?? L("Not found — enter the full path.", "Bulunamadı — tam yolu gir."))
                .font(.caption2).foregroundStyle(store.hermesPath == nil ? Color.orange : .secondary).lineLimit(2).textSelection(.enabled)
            HStack {
                Text(L("Hermes profile", "Hermes profili")).font(.callout)
                Spacer()
                TextField("default", text: $profile).textFieldStyle(.roundedBorder).frame(width: 160)
            }
            SliderRow(title: L("Sessions refresh every", "Oturum yenileme sıklığı"), value: $liveEvery, range: 10...120, step: 5, format: seconds)
            SliderRow(title: L("Quotas refresh every", "Kota yenileme sıklığı"), value: $quotaEvery, range: 30...300, step: 15, format: seconds)
            Text(timingLine).font(.caption2).foregroundStyle(.secondary)
            Text(L("Reads only the quota plugin's local JSON (`hermes quota … --json`); quota numbers come from its cache, so polling never calls a provider. No network, no credentials.",
                   "Yalnızca quota eklentisinin yerel JSON çıktısını okur (`hermes quota … --json`); kota değerleri önbellekten gelir, yoklama hiçbir sağlayıcıyı çağırmaz. Ağ yok, kimlik bilgisi yok."))
                .font(.caption2).foregroundStyle(.secondary)
        }
    }

    var timingLine: String {
        let parts = ["snapshot", "status", "history", "plan"].compactMap { k in store.timings[k].map { "\(k) \(String(format: "%.1f", $0)) s" } }
        return parts.isEmpty ? "" : L("Last poll: ", "Son yoklama: ") + parts.joined(separator: " · ")
    }

    var general: some View {
        SettingsSection(title: L("General", "Genel"), icon: "gearshape") {
            Toggle(L("Launch at login", "Girişte başlat"), isOn: Binding(get: { login }, set: { v in
                do {
                    if v { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
                    loginError = nil
                } catch { loginError = error.localizedDescription }
                login = SMAppService.mainApp.status == .enabled
            }))
            if let loginError { Text(loginError).font(.caption2).foregroundStyle(.orange) }
            HStack {
                Button(L("Refresh now", "Şimdi yenile")) { store.refreshAll() }
                Button(armed ? L("Sure? Click again", "Emin misin? Tekrar tıkla") : L("Restore defaults", "Varsayılanlara dön")) {
                    if armed {
                        Pref.restoreDefaults()
                        IslandController.shared.rebuild()
                        armed = false
                    } else {
                        armed = true
                        DispatchQueue.main.asyncAfter(deadline: .now() + 4) { armed = false }
                    }
                }
                .tint(armed ? .red : nil)
                Spacer()
                Button(L("Quit", "Çık"), role: .destructive) { NSApp.terminate(nil) }
            }
            .controlSize(.regular)
            Text("Quota Notch \(APP_VERSION)").font(.caption2).foregroundStyle(.secondary)
        }
    }
}
