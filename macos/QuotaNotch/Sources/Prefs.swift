// Quota Notch — settings keys and UI language (English + Turkish, as Agent Bar).

import Foundation

enum Pref {
    static let display = "display"            // both | island | menubar
    static let lang = "lang"                  // auto | en | tr
    static let islandEars = "islandEars"      // hover (notch stays plain until hovered) | always (rings + count)
    static let rightEar = "rightEar"          // sessions | cost | none
    static let islandEvents = "islandEvents"  // pop alerts / peeks out of the notch
    static let peekSec = "peekSec"
    static let eventSec = "eventSec"
    static let hoverDelay = "hoverDelay"
    static let stepPct = "stepPct"            // a quota "moves" (peek) every N points
    static let hoverQuotas = "hoverQuotas"
    static let hoverSessions = "hoverSessions"
    static let hoverCost = "hoverCost"
    static let barQuota = "barQuota"          // menu bar label shows the fullest quota
    static let barCount = "barCount"          // menu bar label shows the working count
    static let notifyQuota = "notifyQuota"    // banners: quota 80/95 %, filling soon
    static let notifyAgents = "notifyAgents"  // banners: approval, stalled, your turn
    static let liveEvery = "liveEvery"        // s between `monitor snapshot` polls
    static let quotaEvery = "quotaEvery"      // s between `status --json --cached` polls
    static let hermesPath = "hermesPath"      // override for the hermes executable ("" = auto)
    static let profile = "profile"            // Hermes profile to read ("" = default)
    static let motion = "motion"

    static let defaults: [String: Any] = [
        display: "both", lang: "auto", islandEars: "hover", rightEar: "sessions", islandEvents: true,
        peekSec: 12.0, eventSec: 5.0, hoverDelay: 0.22, stepPct: 2.0, hoverQuotas: true, hoverSessions: true,
        hoverCost: true, barQuota: true, barCount: true, notifyQuota: true, notifyAgents: true,
        liveEvery: 30.0, quotaEvery: 60.0, hermesPath: "", profile: "", motion: true,
    ]

    /// Every user-facing setting except the language (resetting it would flip the UI).
    static var resettable: [String] { defaults.keys.filter { $0 != lang } }

    static func register() { UserDefaults.standard.register(defaults: defaults) }
    static func restoreDefaults() { resettable.forEach { UserDefaults.standard.removeObject(forKey: $0) } }
    static func on(_ k: String) -> Bool { UserDefaults.standard.bool(forKey: k) }
    static func num(_ k: String) -> Double { UserDefaults.standard.double(forKey: k) }
    static func str(_ k: String) -> String { UserDefaults.standard.string(forKey: k) ?? "" }
}

func isTR() -> Bool {
    switch Pref.str(Pref.lang) {
    case "tr": return true
    case "en": return false
    default: return (Locale.preferredLanguages.first ?? "en").hasPrefix("tr")
    }
}

func L(_ en: String, _ tr: String) -> String { isTR() ? tr : en }
func appLocale() -> Locale { Locale(identifier: isTR() ? "tr_TR" : "en_US") }

/// "1h 53m", "4d 2h", "35s".
func span(_ seconds: Double) -> String {
    let s = max(0, Int(seconds.rounded()))
    let d = s / 86400, h = (s % 86400) / 3600, m = (s % 3600) / 60
    if d > 0 { return L("\(d)d \(h)h", "\(d)g \(h)s") }
    if h > 0 { return L("\(h)h \(m)m", "\(h)s \(m)dk") }
    if m > 0 { return L("\(m)m", "\(m)dk") }
    return L("\(s)s", "\(s)sn")
}

func pct(_ v: Double) -> String { L("\(Int(v.rounded()))%", "%\(Int(v.rounded()))") }

let PROVIDER_NAME = ["anthropic": "Claude", "openai-codex": "Codex", "copilot": "Copilot", "gemini": "Gemini",
                     "openrouter": "OpenRouter", "nvidia": "NVIDIA", "cursor": "Cursor", "deepseek": "DeepSeek",
                     "grok": "Grok", "kimi": "Kimi", "minimax": "MiniMax", "zai": "Z.ai", "ollama": "Ollama"]

func providerName(_ a: QuotaAccount, multi: Set<String>) -> String {
    let base = PROVIDER_NAME[a.base] ?? a.name
    if multi.contains(a.base) || a.accountLabel != nil, let acct = a.accountLabel { return "\(base) · \(acct)" }
    return base
}

func windowName(_ w: String) -> String {
    switch w.lowercased() {
    case "current session", "session", "5h", "5-hour": return L("Session", "Oturum")
    case "current week", "weekly", "week", "7d": return L("Week", "Hafta")
    case "current day", "daily", "day": return L("Day", "Gün")
    case "monthly", "month": return L("Month", "Ay")
    default: return w
    }
}
