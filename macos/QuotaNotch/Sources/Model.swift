// Quota Notch — data model (Foundation only, unit-tested by Tests/main.swift).
//
// Every payload comes from the quota plugin's own CLI (`hermes quota ... --json`). Decoding is defensive on
// purpose: the plugin evolves independently of this app, so unknown fields are ignored, missing ones become nil,
// wrong types never throw, and one broken section never hides the others.

import Foundation

// MARK: - tolerant JSON access

typealias JObj = [String: Any]

enum JSON {
    /// Parses one JSON object from CLI stdout. Tolerates log noise around the object and Python's
    /// non-standard `NaN` / `Infinity` tokens (json.dumps emits them by default).
    static func object(_ data: Data) -> JObj? {
        if let o = (try? JSONSerialization.jsonObject(with: data, options: [])) as? JObj { return o }
        guard var text = String(data: data, encoding: .utf8) else { return nil }
        if let a = text.firstIndex(of: "{"), let b = text.lastIndex(of: "}"), a < b {
            text = String(text[a...b])
        }
        text = text.replacingOccurrences(of: #"(?<=[:\[,\s])-?(NaN|Infinity)(?=\s*[,\]}])"#, with: "null",
                                         options: .regularExpression)
        return (try? JSONSerialization.jsonObject(with: Data(text.utf8), options: [])) as? JObj
    }
}

extension Dictionary where Key == String, Value == Any {
    func str(_ k: String) -> String? {
        guard let s = self[k] as? String else { return nil }
        let t = s.trimmingCharacters(in: .whitespacesAndNewlines)
        return t.isEmpty ? nil : t
    }
    func num(_ k: String) -> Double? {
        guard let n = self[k] as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() else { return nil }
        let d = n.doubleValue
        return d.isFinite ? d : nil
    }
    func int(_ k: String) -> Int { num(k).map { Int($0) } ?? 0 }
    func bool(_ k: String) -> Bool? {
        guard let n = self[k] as? NSNumber, CFGetTypeID(n) == CFBooleanGetTypeID() else { return nil }
        return n.boolValue
    }
    func obj(_ k: String) -> JObj? { self[k] as? JObj }
    func objs(_ k: String) -> [JObj] { (self[k] as? [Any] ?? []).compactMap { $0 as? JObj } }
    func strs(_ k: String) -> [String] { (self[k] as? [Any] ?? []).compactMap { $0 as? String } }
}

/// ISO-8601 with any number of fractional digits (Python emits 6) or an epoch number → seconds since 1970.
func parseTime(_ v: Any?) -> Double? {
    if let n = v as? NSNumber, CFGetTypeID(n) != CFBooleanGetTypeID() {
        let d = n.doubleValue
        return d.isFinite && d > 0 ? d : nil
    }
    guard var s = v as? String, !s.isEmpty else { return nil }
    var fraction = 0.0
    if let r = s.range(of: #"\.\d+"#, options: .regularExpression) {
        fraction = Double("0" + s[r]) ?? 0
        s.removeSubrange(r)
    }
    if s.hasSuffix("Z") == false, s.range(of: #"[+-]\d\d:\d\d$"#, options: .regularExpression) == nil {
        s += "Z"  // naive stamps are UTC in the plugin
    }
    let f = ISO8601DateFormatter()
    f.formatOptions = [.withInternetDateTime]
    guard let d = f.date(from: s) else { return nil }
    return d.timeIntervalSince1970 + fraction
}

// MARK: - quota (hermes quota status --json --cached)

struct QuotaWindow: Equatable {
    let label: String
    let used: Double?      // percent used, 0...100 (may exceed 100 on some providers)
    let reset: Double?     // epoch seconds
}

struct QuotaAccount: Equatable, Identifiable {
    let key: String            // cache key: "anthropic", "anthropic:<id>", "openai-codex:<id>"
    let base: String           // provider id without the account suffix
    let name: String           // provider label from the cache
    let accountLabel: String?  // pool label (runtime data: displayed only, never logged)
    let plan: String?
    let windows: [QuotaWindow]
    let unavailable: String?
    let stale: Bool
    var id: String { key }

    /// The windows that carry a reading.
    var measured: [QuotaWindow] { windows.filter { $0.used != nil } }
    var worstUsed: Double? { measured.compactMap(\.used).max() }
}

struct QuotaStatus {
    var fetchedAt: Double?
    var ageSeconds: Double?
    var pluginVersion: String?
    var accounts: [QuotaAccount] = []

    static let providerOrder = ["anthropic", "openai-codex", "copilot", "gemini", "cursor", "openrouter"]

    static func parse(_ data: Data) -> QuotaStatus? {
        guard let o = JSON.object(data) else { return nil }
        return parse(o)
    }

    static func parse(_ o: JObj) -> QuotaStatus {
        var out = QuotaStatus(fetchedAt: parseTime(o["fetched_at"]), ageSeconds: o.num("age_s"),
                              pluginVersion: o.str("plugin_version"))
        let providers = o.obj("providers") ?? [:]
        for key in providers.keys.sorted() {
            guard let rec = providers[key] as? JObj else { continue }
            let base = rec.str("provider") ?? String(key.split(separator: ":", maxSplits: 1).first ?? Substring(key))
            let top = account(key: key, base: base, rec: rec, nested: false)
            var nested: [QuotaAccount] = []
            for (i, acc) in rec.objs("accounts").enumerated() {
                let aid = acc.str("id") ?? "account\(i + 1)"
                nested.append(account(key: "\(key):\(aid)", base: base, rec: acc, nested: true))
            }
            // Codex nests its saved accounts in one record whose top-level windows repeat the active one:
            // show each account once.
            if nested.isEmpty {
                out.accounts.append(top)
            } else {
                let dup = nested.contains { n in top.measured.allSatisfy { tw in n.windows.contains { $0.label == tw.label && $0.used == tw.used } } }
                if !top.measured.isEmpty, !dup { out.accounts.append(top) }
                out.accounts += nested
            }
        }
        out.accounts.sort { a, b in
            let ia = providerOrder.firstIndex(of: a.base) ?? providerOrder.count
            let ib = providerOrder.firstIndex(of: b.base) ?? providerOrder.count
            return ia != ib ? ia < ib : a.key < b.key
        }
        return out
    }

    private static func account(key: String, base: String, rec: JObj, nested: Bool) -> QuotaAccount {
        let windows = rec.objs("windows").map {
            QuotaWindow(label: $0.str("label") ?? "window", used: $0.num("used_percent") ?? $0.num("used"),
                        reset: parseTime($0["reset_at"] ?? $0["reset"]))
        }
        let details = (rec["details"] as? [Any] ?? []).map { "\($0)" }
        return QuotaAccount(key: key, base: base, name: rec.str("label") ?? key,
                            accountLabel: rec.str("account_label") ?? (nested ? rec.str("label") : nil),
                            plan: rec.str("plan"), windows: windows, unavailable: rec.str("unavailable_reason"),
                            stale: details.contains { $0.lowercased().contains("stale") })
    }

    /// Accounts worth showing: at least one measured window, or an explicit reason why not.
    var visible: [QuotaAccount] { accounts.filter { !$0.measured.isEmpty } }

    /// Providers with more than one visible account (their rows show the account label).
    var multiAccountBases: Set<String> {
        var n: [String: Int] = [:]
        for a in visible { n[a.base, default: 0] += 1 }
        return Set(n.filter { $0.value > 1 }.keys)
    }
}

// MARK: - live (hermes quota monitor snapshot --json)

struct OpenAction: Equatable {
    let kind: String          // hermes-session | claude-code | copilot-cli | kanban-card | none
    let url: String?
    let command: String?
    let label: String?
    let running: Bool

    static func parse(_ o: JObj?) -> OpenAction? {
        guard let o, let kind = o.str("kind"), kind != "none" else { return nil }
        return OpenAction(kind: kind, url: o.str("url"), command: o.str("command"), label: o.str("label"),
                          running: o.bool("running") ?? false)
    }
}

struct LiveSession: Equatable, Identifiable {
    let id: String
    let kind: String            // hermes | claude-code | copilot-cli | ...
    let profile: String?
    let sourceName: String?
    let title: String?
    let model: String?
    let status: String          // running | ended
    let state: String?          // working | waiting | needs_approval | stalled | ended
    let stateSince: Double?
    let started: Double?
    let last: Double?
    let subagent: Bool
    let parent: String?
    let origin: String?
    let interactive: Bool
    let tokens: Double?
    let cost: Double?
    let contextTokens: Double?
    let focused: Bool           // forward-compatible: a backend that knows the focused chat may mark it
    let open: OpenAction?

    var isRunning: Bool { status == "running" }
    var isMain: Bool { isRunning && !subagent }
    var isHermes: Bool { kind == "hermes" }

    static func parse(_ o: JObj) -> LiveSession? {
        guard let id = o.str("id") else { return nil }
        return LiveSession(id: id, kind: o.str("kind") ?? "hermes", profile: o.str("profile"),
                           sourceName: o.str("source_name"), title: o.str("title"), model: o.str("model"),
                           status: o.str("status") ?? "running", state: o.str("state"), stateSince: o.num("state_since"),
                           started: o.num("started"), last: o.num("last"), subagent: o.bool("subagent") ?? false,
                           parent: o.str("parent"), origin: o.str("origin"), interactive: o.bool("interactive") ?? false,
                           tokens: o.num("tokens"), cost: o.num("cost"), contextTokens: o.num("context_tokens"),
                           focused: (o.bool("focused") ?? false) || (o.bool("in_use") ?? false),
                           open: OpenAction.parse(o.obj("open")))
    }
}

struct LiveCounts: Equatable {
    var open = 0, working = 0, needsApproval = 0, yourTurn = 0, stalled = 0, subagents = 0
}

struct Burn: Equatable {
    let provider: String
    let window: String
    let used: Double?
    let perHour: Double?
    let etaFull: Double?
    let reset: Double?
    let fillsBeforeReset: Bool
}

struct Snapshot {
    enum Collection: Equatable { case on, off(code: String), failed(String) }
    var collection: Collection = .on
    var generatedAt: Double?
    var now: Double?
    var scope: String?                 // "active" | "all" (newer backends); nil = unknown
    var sessions: [LiveSession] = []
    var counts = LiveCounts()
    var burn: [Burn] = []
    var costToday: Double?             // Hermes + CLI estimated API-price equivalent, nil when unknown
    var cliAvailable: Bool?
    var sectionErrors: [String: String] = [:]

    static let enableCommand = "hermes config set plugins.entries.quota.settings.monitorEnabled true"

    static func parse(_ data: Data, today: String) -> Snapshot? {
        guard let o = JSON.object(data) else { return nil }
        return parse(o, today: today)
    }

    /// `today` is the local calendar day (yyyy-MM-dd) the cost total is summed for.
    static func parse(_ o: JObj, today: String) -> Snapshot {
        var s = Snapshot()
        s.generatedAt = o.num("generated_at")
        if o.bool("enrolled") == false || o.str("collection") == "disabled" || o.str("error_code") == "monitor-enrollment-off" {
            s.collection = .off(code: o.str("error_code") ?? "monitor-enrollment-off")
            return s
        }
        if let err = o.str("error"), o.obj("live") == nil {
            s.collection = .failed(err)
            return s
        }
        if let errs = o.obj("errors") { for (k, v) in errs { s.sectionErrors[k] = "\(v)" } }
        let live = o.obj("live") ?? [:]
        s.now = live.num("now")
        s.scope = live.str("scope")
        s.sessions = live.objs("running").compactMap(LiveSession.parse)
        if let c = live.obj("counts") {
            s.counts = LiveCounts(open: c.int("open"), working: c.int("working"), needsApproval: c.int("needs_approval"),
                                  yourTurn: c.int("your_turn"), stalled: c.int("stalled"), subagents: c.int("subagents"))
        } else {
            let m = s.sessions.filter(\.isMain)
            s.counts = LiveCounts(open: m.count, working: m.filter { $0.state == "working" }.count,
                                  needsApproval: m.filter { $0.state == "needs_approval" }.count,
                                  yourTurn: m.filter { $0.state == "waiting" }.count,
                                  stalled: m.filter { $0.state == "stalled" }.count,
                                  subagents: s.sessions.filter { $0.subagent && $0.isRunning }.count)
        }
        s.burn = live.objs("burn").compactMap { b in
            guard let p = b.str("provider"), let w = b.str("window") else { return nil }
            return Burn(provider: p, window: w, used: b.num("used"), perHour: b.num("per_hour"), etaFull: b.num("eta_full"),
                        reset: b.num("reset"), fillsBeforeReset: b.bool("fills_before_reset") ?? false)
        }
        // Today's cost: Hermes usage + external CLIs (both API-price estimates).
        var cost: Double?
        if let today = o.obj("usage")?.obj("today")?.obj("totals")?.num("cost") { cost = today }
        if let clis = o.obj("clis") {
            s.cliAvailable = clis.bool("available")
            if s.cliAvailable != false {
                for d in clis.objs("daily") where d.str("day") == today {
                    let c = (d.obj("cost") ?? [:]).values.compactMap { ($0 as? NSNumber)?.doubleValue }.reduce(0, +)
                    cost = (cost ?? 0) + c
                }
            }
        }
        s.costToday = cost
        return s
    }

    var mains: [LiveSession] { sessions.filter(\.isMain) }
}

// MARK: - history / plan

struct HistorySeries: Equatable {
    let provider: String
    let window: String
    let points: [(t: Double, used: Double)]
    static func == (a: HistorySeries, b: HistorySeries) -> Bool {
        a.provider == b.provider && a.window == b.window && a.points.map(\.t) == b.points.map(\.t)
            && a.points.map(\.used) == b.points.map(\.used)
    }

    static func parse(_ data: Data) -> [HistorySeries]? {
        guard let o = JSON.object(data) else { return nil }
        if o.bool("enrolled") == false { return [] }
        return o.objs("history").compactMap { h in
            guard let p = h.str("provider"), let w = h.str("window") else { return nil }
            let pts: [(t: Double, used: Double)] = (h["points"] as? [Any] ?? []).compactMap { raw in
                guard let a = raw as? [Any], a.count >= 2, let t = (a[0] as? NSNumber)?.doubleValue,
                      let u = (a[1] as? NSNumber)?.doubleValue, t.isFinite, u.isFinite else { return nil }
                return (t, u)
            }
            return HistorySeries(provider: p, window: w, points: pts.sorted { $0.t < $1.t })
        }
    }
}

/// `hermes quota monitor plan --json` (newer plugin builds only): one pacing sentence per account.
struct PlanHint: Equatable {
    let provider: String
    let verdict: String?   // under | on_track | over | ...
    let summary: String?

    static func parse(_ data: Data) -> [PlanHint]? {
        guard let o = JSON.object(data), o["providers"] != nil else { return nil }
        return o.objs("providers").flatMap { p in
            p.objs("accounts").compactMap { a in
                guard let prov = a.str("provider"), a.bool("plannable") != false else { return nil }
                return PlanHint(provider: prov, verdict: a.obj("pace")?.str("verdict"), summary: a.str("summary"))
            }
        }
    }
}

// MARK: - pure presentation logic

let SOURCE_ORDER = ["hermes", "claude-code", "copilot-cli"]

struct SessionGroup: Equatable {
    let key: String
    let title: String   // "Hermes · work", "Claude Code", ...
    let sessions: [LiveSession]
}

enum Sessions {
    static let stateRank: [String: Int] = ["needs_approval": 0, "waiting": 1, "stalled": 2, "working": 3]

    /// The session in use NOW: a row the backend marked focused, else the most recently active interactive Hermes
    /// session (the Desktop chat you are typing in), else the most recently active Hermes session at all.
    static func current(_ mains: [LiveSession]) -> LiveSession? {
        if let f = mains.first(where: \.focused) { return f }
        let hermes = mains.filter(\.isHermes)
        let pick = hermes.filter(\.interactive).max { ($0.last ?? 0) < ($1.last ?? 0) }
        return pick ?? hermes.max { ($0.last ?? 0) < ($1.last ?? 0) }
    }

    /// The other open sessions grouped by Hermes profile, then Claude Code, Copilot CLI, other sources;
    /// inside a group the ones needing you first, then the most recently active.
    static func groups(_ mains: [LiveSession], excluding pinned: LiveSession?, profileLabel: (String) -> String) -> [SessionGroup] {
        var order: [String] = []
        var byKey: [String: [LiveSession]] = [:]
        var titles: [String: String] = [:]
        for s in mains where s.id != pinned?.id || s.kind != pinned?.kind {
            let key = s.isHermes ? "hermes|\(s.profile ?? "default")" : s.kind
            if byKey[key] == nil {
                order.append(key)
                titles[key] = s.isHermes ? profileLabel(s.profile ?? "default") : (s.sourceName ?? s.kind)
            }
            byKey[key, default: []].append(s)
        }
        func rank(_ key: String) -> (Int, String) {
            let kind = key.hasPrefix("hermes|") ? "hermes" : key
            let profile = key.hasPrefix("hermes|") ? String(key.dropFirst(7)) : ""
            // default profile first among Hermes groups
            return (SOURCE_ORDER.firstIndex(of: kind) ?? SOURCE_ORDER.count, profile == "default" ? "" : profile + key)
        }
        return order.sorted { rank($0) < rank($1) }.map { key in
            let rows = byKey[key]!.sorted { a, b in
                let ra = stateRank[a.state ?? ""] ?? 4, rb = stateRank[b.state ?? ""] ?? 4
                return ra != rb ? ra < rb : (a.last ?? 0) > (b.last ?? 0)
            }
            return SessionGroup(key: key, title: titles[key]!, sessions: rows)
        }
    }
}

/// What a click on a session does. Only two outcomes: open a hermes:// link (Hermes Desktop), or copy the
/// resume command. The app never runs commands or scripts itself.
enum OpenStep: Equatable {
    case url(URL)
    case copy(String)
    case nothing
}

enum Opener {
    /// `hermes://open/<session id>` is routed by Hermes Desktop to the session view; nothing else is opened.
    static func validURL(_ s: String?) -> URL? {
        guard let s, let u = URL(string: s), u.scheme?.lowercased() == "hermes" else { return nil }
        return u
    }

    static func hermesURL(sessionID: String) -> URL? {
        guard !sessionID.isEmpty,
              let enc = sessionID.addingPercentEncoding(withAllowedCharacters: .alphanumerics.union(CharacterSet(charactersIn: "-_.")))
        else { return nil }
        return URL(string: "hermes://open/\(enc)")
    }

    static func plan(_ s: LiveSession, desktopInstalled: Bool) -> OpenStep {
        if s.isHermes {
            if let u = validURL(s.open?.url) { return .url(u) }
            if desktopInstalled, let u = hermesURL(sessionID: s.id) { return .url(u) }
        }
        if let c = s.open?.command, !c.contains("\n") { return .copy(c) }
        return .nothing
    }
}

// MARK: - events (pure state machine; the app turns its output into notch pop-downs, peeks and notifications)

struct NotchEvent: Equatable {
    enum Kind: String { case approval, yourTurn, stalled, quota80, quota95, fillingSoon }
    let kind: Kind
    let key: String           // dedupe key
    let accountKey: String?   // quota events: the account whose ring is shown
    let window: String?
    let used: Double?
    let sessionID: String?
    let sessionKind: String?
    let title: String?        // session title (quota events: nil)
    let etaSeconds: Double?
    let resetSeconds: Double?
}

struct EventEngine {
    /// Remembered across launches by the app (UserDefaults): quota thresholds already announced per window period.
    var quotaSent: [String: Int] = [:]
    var soonSent: Set<String> = []
    private(set) var sessionStates: [String: String]? = nil   // nil until the first snapshot was seen
    private var baselines: [String: Double] = [:]             // quota value at the last peek, per account|window

    init(quotaSent: [String: Int] = [:], soonSent: Set<String> = []) {
        self.quotaSent = quotaSent
        self.soonSent = soonSent
    }

    static func periodKey(_ a: QuotaAccount, _ w: QuotaWindow) -> String {
        "\(a.key)|\(w.label)|\(Int((w.reset ?? 0) / 3600))"
    }

    /// Quota thresholds (80 %, 95 %) once per window period; "filling soon" once per period when the burn rate
    /// fills the window within the hour and before it resets. Returns events + the account keys whose value moved
    /// by at least `step` points since the last peek (the notch peeks them).
    mutating func quotas(_ q: QuotaStatus, burn: [Burn], now: Double, step: Double) -> (events: [NotchEvent], moved: [String]) {
        var out: [NotchEvent] = []
        var moved: [String] = []
        for a in q.visible {
            for w in a.measured {
                guard let used = w.used else { continue }
                let pk = Self.periodKey(a, w)
                let level = used >= 95 ? 95 : used >= 80 ? 80 : 0
                if level > (quotaSent[pk] ?? 0) {
                    quotaSent[pk] = level
                    out.append(NotchEvent(kind: level == 95 ? .quota95 : .quota80, key: "\(pk)|\(level)", accountKey: a.key,
                                          window: w.label, used: used, sessionID: nil, sessionKind: nil, title: nil,
                                          etaSeconds: nil, resetSeconds: w.reset.map { $0 - now }))
                }
                if let b = burn.first(where: { $0.provider == a.key && $0.window == w.label }), let eta = b.etaFull,
                   eta < (w.reset ?? .infinity), eta - now < 3600, eta > now, used < 100, !soonSent.contains(pk) {
                    soonSent.insert(pk)
                    out.append(NotchEvent(kind: .fillingSoon, key: "soon|\(pk)", accountKey: a.key, window: w.label, used: used,
                                          sessionID: nil, sessionKind: nil, title: nil, etaSeconds: eta - now,
                                          resetSeconds: w.reset.map { $0 - now }))
                }
                let bk = "\(a.key)|\(w.label)"
                if let base = baselines[bk] {
                    if abs(used - base) >= step {
                        baselines[bk] = used
                        if !moved.contains(a.key) { moved.append(a.key) }
                    }
                } else {
                    baselines[bk] = used
                }
            }
        }
        return (out, moved)
    }

    /// Session transitions. The first snapshot only seeds the states (no burst of alerts at launch).
    /// Returns the events plus whether a session started (the notch peeks its count).
    mutating func sessions(_ mains: [LiveSession]) -> (events: [NotchEvent], started: Bool) {
        let now = Dictionary(mains.map { ("\($0.kind)|\($0.id)", $0.state ?? "working") }, uniquingKeysWith: { a, _ in a })
        defer { sessionStates = now }
        guard let before = sessionStates else { return ([], false) }
        var out: [NotchEvent] = []
        var started = false
        for s in mains {
            let k = "\(s.kind)|\(s.id)"
            let prev = before[k]
            if prev == nil { started = true }
            let state = s.state ?? "working"
            guard prev != state else { continue }
            let kind: NotchEvent.Kind?
            switch state {
            case "needs_approval": kind = .approval
            case "stalled": kind = .stalled
            case "waiting" where prev == "working" && s.interactive: kind = .yourTurn
            default: kind = nil
            }
            if let kind {
                out.append(NotchEvent(kind: kind, key: "\(kind.rawValue)|\(k)", accountKey: nil, window: nil, used: nil,
                                      sessionID: s.id, sessionKind: s.kind, title: s.title, etaSeconds: nil, resetSeconds: nil))
            }
        }
        return (out, started)
    }

    /// Drop remembered periods whose reset is long past (keeps the persisted dictionaries small).
    mutating func prune(now: Double) {
        func alive(_ key: String) -> Bool {
            guard let h = key.split(separator: "|").last.flatMap({ Double($0) }) else { return true }
            return h == 0 || h * 3600 > now - 8 * 86400
        }
        quotaSent = quotaSent.filter { alive($0.key) }
        soonSent = soonSent.filter(alive)
    }
}

// MARK: - formatting helpers (language-independent parts)

func localDay(_ t: Double = Date().timeIntervalSince1970, tz: TimeZone = .current) -> String {
    let f = DateFormatter()
    f.calendar = Calendar(identifier: .gregorian)
    f.locale = Locale(identifier: "en_US_POSIX")
    f.timeZone = tz
    f.dateFormat = "yyyy-MM-dd"
    return f.string(from: Date(timeIntervalSince1970: t))
}

func compactNumber(_ v: Double?) -> String {
    guard let v else { return "—" }
    switch abs(v) {
    case 1_000_000_000...: return String(format: "%.1fB", v / 1_000_000_000)
    case 1_000_000...: return String(format: "%.1fM", v / 1_000_000)
    case 1_000...: return String(format: "%.0fk", v / 1_000)
    default: return String(format: "%.0f", v)
    }
}

func money(_ v: Double?) -> String { String(format: "$%.2f", v ?? 0) }

/// Status file the CLI reads (`hermes quota monitor companion --json`): the renderer cannot know whether this
/// Mac has a notch, so the app reports it.
struct CompanionStatus: Codable, Equatable {
    var version: String
    var notch: Bool
    var running_since: Double?
    var display: String
    var pid: Int32?
}
