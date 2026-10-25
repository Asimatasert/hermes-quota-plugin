// Quota Notch unit tests (no XCTest: compiled with swiftc together with the Foundation-only sources and run as a
// plain executable by macos/test.sh). Fixtures are synthetic; placeholders are expanded relative to a fixed NOW.

import Foundation

let NOW = 1_800_000_000.0
let FIXTURES = URL(fileURLWithPath: CommandLine.arguments.count > 1 ? CommandLine.arguments[1] : "Tests/Fixtures")
let FAKE = CommandLine.arguments.count > 2 ? CommandLine.arguments[2] : "Tests/fake-hermes"
var failures = 0
var passed = 0

func check(_ cond: @autoclosure () -> Bool, _ what: String, file: String = #file, line: Int = #line) {
    if cond() { passed += 1 } else { failures += 1; print("FAIL \(line): \(what)") }
}

func expand(_ text: String) -> String {
    var t = text
    func sub(_ pattern: String, _ f: (Double) -> String) {
        let re = try! NSRegularExpression(pattern: pattern)
        while let m = re.firstMatch(in: t, range: NSRange(t.startIndex..., in: t)) {
            let n = Double(t[Range(m.range(at: 1), in: t)!])!
            t.replaceSubrange(Range(m.range, in: t)!, with: f(n))
        }
    }
    let iso = ISO8601DateFormatter()
    sub(#"__ISO\(([-+]?\d+)\)__"#) { iso.string(from: Date(timeIntervalSince1970: NOW + $0)).replacingOccurrences(of: "Z", with: ".123456+00:00") }
    sub(#"__T\(([-+]?\d+)\)__"#) { String(NOW + $0) }
    return t.replacingOccurrences(of: "__DAY__", with: localDay(NOW))
}

func fixture(_ name: String) -> Data {
    Data(expand(try! String(contentsOf: FIXTURES.appendingPathComponent(name), encoding: .utf8)).utf8)
}

// ---------- time parsing ----------
check(parseTime("2026-10-24T22:10:00.324878+00:00").map { abs($0 - 1_792_879_800.324878) < 0.001 } ?? false, "ISO with 6 fraction digits")
check(parseTime("2026-10-24T22:10:00Z") == 1_792_879_800, "ISO Z")
check(parseTime("2026-10-24T22:10:00") == 1_792_879_800, "naive ISO is UTC")
check(parseTime(1_792_879_800 as NSNumber) == 1_792_879_800, "epoch number")
check(parseTime("garbage") == nil && parseTime(nil) == nil && parseTime(true as NSNumber) == nil, "bad time → nil")

// ---------- quota status ----------
let q = QuotaStatus.parse(fixture("status.json"))!
check(q.pluginVersion == "3.0.0", "plugin version")
check(q.ageSeconds == 42, "age_s")
let keys = q.visible.map(\.key)
check(keys == ["anthropic", "anthropic:home", "openai-codex:acct-a", "openai-codex:acct-b"], "visible accounts in provider order: \(keys)")
check(!keys.contains("copilot") && !keys.contains("grok") && !keys.contains("broken"), "unconfigured / malformed providers hidden")
let work = q.visible[0]
check(work.accountLabel == "work" && work.plan == "max", "account label + plan")
check(work.windows.count == 3 && work.windows[0].used == 58 && work.windows[0].label == "Current session", "windows decoded")
check(work.windows[0].reset.map { abs($0 - (NOW + 6780)) < 1 } ?? false, "reset parsed")
check(q.multiAccountBases == ["anthropic", "openai-codex"], "multi-account bases")
check(q.visible[2].accountLabel == "personal" && q.visible[3].stale, "nested codex accounts: label, stale")
check(q.visible.first { $0.key == "anthropic:home" }?.worstUsed == 87.5, "worst used")
check(QuotaStatus.parse(Data("not json".utf8)) == nil, "garbage status → nil")
check(QuotaStatus.parse(Data("{}".utf8))?.accounts.isEmpty == true, "empty status → no accounts")

// ---------- snapshot ----------
let s = Snapshot.parse(fixture("snapshot.json"), today: localDay(NOW))!
check(s.collection == .on, "collection on")
check(s.scope == "all", "scope")
check(s.counts.open == 5 && s.counts.needsApproval == 1 && s.counts.working == 2, "counts from backend")
check(s.sessions.count == 7 && s.mains.count == 5, "sessions/mains: \(s.sessions.count)/\(s.mains.count)")
check(s.costToday.map { abs($0 - 15.75) < 1e-9 } ?? false, "cost today = hermes 12.5 + CLIs today 3.25 (other days ignored): \(String(describing: s.costToday))")
check(s.burn.count == 2 && s.burn[0].fillsBeforeReset && s.burn[0].etaFull == NOW + 2160, "burn")
check(s.sectionErrors["kanban"] != nil, "section errors kept")
let cc = s.sessions.first { $0.kind == "claude-code" }!
check(cc.open?.command?.hasPrefix("cd /tmp/project && claude --resume") == true && cc.open?.running == true, "claude open action")

let off = Snapshot.parse(fixture("snapshot-off.json"), today: "x")!
check(off.collection == .off(code: "monitor-enrollment-off") && off.sessions.isEmpty, "enrollment off")
check(Snapshot.enableCommand == "hermes config set plugins.entries.quota.settings.monitorEnabled true", "enable command text")
if case .failed(let why) = Snapshot.parse(fixture("snapshot-error.json"), today: "x")!.collection { check(why.contains("synthetic"), "error text") }
else { check(false, "error payload → failed") }
let noisy = Snapshot.parse(fixture("snapshot-noise.txt"), today: "x")
check(noisy?.collection == .on && noisy?.burn.first?.perHour == nil && noisy?.burn.first?.etaFull == nil, "log noise + NaN/Infinity tolerated")
let legacy = Snapshot.parse(Data(#"{"monitor_version":1,"live":{"running":[{"id":"a","status":"running","state":"needs_approval"},{"id":"b","status":"running","state":"working"},{"status":"running"}]}}"#.utf8), today: "x")!
check(legacy.counts.open == 2 && legacy.counts.needsApproval == 1 && legacy.counts.working == 1 && legacy.scope == nil, "counts computed when the backend has none; id-less rows dropped")

// ---------- sessions: NOW pin + grouping ----------
let current = Sessions.current(s.mains)
check(current?.id == "sess_desk_now", "NOW = most recently active interactive Hermes session: \(current?.id ?? "nil")")
let groups = Sessions.groups(s.mains, excluding: current) { $0 == "default" ? "Hermes" : "Hermes · \($0)" }
check(groups.map(\.title) == ["Hermes", "Hermes · work", "Claude Code", "Copilot CLI"], "groups: \(groups.map(\.title))")
check(groups[0].sessions.map(\.id) == ["sess_desk_old"], "pinned session not repeated")
var focusedRows = s.mains
focusedRows[2] = LiveSession.parse(["id": "sess_work_1", "kind": "hermes", "profile": "work", "status": "running", "focused": true])!
check(Sessions.current(focusedRows)?.id == "sess_work_1", "backend-marked focused row wins")
check(Sessions.current([cc]) == nil, "no Hermes session → no NOW row")

// ---------- opener ----------
let now0 = s.mains[0]
check(Opener.plan(now0, desktopInstalled: false) == .url(URL(string: "hermes://open/sess_desk_now")!), "hermes url from payload")
let old = s.mains.first { $0.id == "sess_desk_old" }!
check(Opener.plan(old, desktopInstalled: true) == .url(URL(string: "hermes://open/sess_desk_old")!), "hermes url built when Desktop installed")
check(Opener.plan(old, desktopInstalled: false) == .copy("hermes --resume sess_desk_old"), "no Desktop → copy resume command")
check(Opener.plan(cc, desktopInstalled: true) == .copy(cc.open!.command!), "Claude Code → copy command")
check(Opener.validURL("https://example.com") == nil && Opener.validURL("file:///etc/passwd") == nil, "only hermes:// links open")
check(Opener.hermesURL(sessionID: "a b/c")?.absoluteString == "hermes://open/a%20b%2Fc", "session id escaped")

// ---------- events ----------
var eng = EventEngine()
let r1 = eng.quotas(q, burn: s.burn, now: NOW, step: 2)
let kinds = r1.events.map { "\($0.kind.rawValue):\($0.accountKey ?? ""):\($0.window ?? "")" }
check(kinds.contains("quota80:anthropic:home:Current week"), "80% event: \(kinds)")
check(kinds.contains("quota95:openai-codex:acct-a:Weekly"), "95% event")
check(kinds.contains("fillingSoon:anthropic:Current session"), "filling soon (eta 36 min, before reset)")
check(!kinds.contains("fillingSoon:anthropic:Current week"), "no filling soon when eta > 1h")
check(r1.moved.isEmpty, "first reading only seeds the peek baselines")
let r2 = eng.quotas(q, burn: s.burn, now: NOW + 60, step: 2)
check(r2.events.isEmpty, "thresholds announced once per period")
let bumped = QuotaStatus.parse(Data(expand(String(data: fixture("status.json"), encoding: .utf8)!.replacingOccurrences(of: "\"used_percent\": 58.0", with: "\"used_percent\": 61.0")).utf8))!
check(eng.quotas(bumped, burn: [], now: NOW + 120, step: 2).moved == ["anthropic"], "a 3-point move peeks that account")
var persisted = EventEngine(quotaSent: eng.quotaSent, soonSent: eng.soonSent)
check(persisted.quotas(q, burn: s.burn, now: NOW, step: 2).events.isEmpty, "persisted state survives a relaunch")
persisted.prune(now: NOW + 30 * 86400)
check(persisted.quotaSent.isEmpty, "old periods pruned")

var se = EventEngine()
check(se.sessions(s.mains).events.isEmpty, "first snapshot only seeds session states")
func row(_ id: String, _ state: String, interactive: Bool = true) -> LiveSession {
    LiveSession.parse(["id": id, "kind": "hermes", "status": "running", "state": state, "interactive": interactive, "title": "T \(id)"])!
}
var eng2 = EventEngine()
_ = eng2.sessions([row("a", "working"), row("b", "working"), row("c", "working", interactive: false)])
let tr = eng2.sessions([row("a", "needs_approval"), row("b", "waiting"), row("c", "waiting", interactive: false), row("d", "working")])
check(tr.events.map(\.kind) == [.approval, .yourTurn], "approval + your turn (not for unattended workers): \(tr.events.map(\.kind))")
check(tr.started, "new session → started")
check(eng2.sessions([row("a", "stalled"), row("b", "waiting"), row("c", "waiting"), row("d", "working")]).events.map(\.kind) == [.stalled], "stalled")

// ---------- history / plan ----------
let h = HistorySeries.parse(fixture("history.json"))!
check(h.count == 2 && h[0].points.count == 3 && h[1].points.count == 2, "history decoded, bad points dropped")
check(HistorySeries.parse(fixture("snapshot-off.json")) == [], "history while off → empty")
let p = PlanHint.parse(fixture("plan.json"))!
check(p.count == 1 && p[0].provider == "anthropic" && p[0].verdict == "over" && p[0].summary?.hasPrefix("On pace") == true, "plan hints")
check(PlanHint.parse(fixture("plan-unsupported.txt")) == nil, "older plugin without plan → nil")

// ---------- CLI ----------
check(HermesCLI.argv(profile: "work", ["status", "--json"]) == ["-p", "work", "quota", "status", "--json"], "argv with profile")
check(HermesCLI.argv(profile: "default", ["x"]) == ["quota", "x"] && HermesCLI.argv(profile: "bad name;rm", ["x"]) == ["quota", "x"], "default / invalid profile dropped")
check(HermesCLI.candidates(home: "/h", path: "/a:/b").prefix(2) == ["/h/.local/bin/hermes", "/h/.hermes/hermes-agent/.hermes/bin/hermes"], "candidates")
check(HermesCLI.locate(override: "/nonexistent/hermes") == nil, "bad override is not silently replaced")
if FileManager.default.isExecutableFile(atPath: FAKE) {
    let log = NSTemporaryDirectory() + "qn-fake-\(getpid()).log"
    setenv("FAKE_LOG", log, 1)
    let r = HermesCLI.run(FAKE, HermesCLI.argv(profile: "work", ["monitor", "snapshot", "--json"]), timeout: 20)
    check(r.status == 0 && !r.timedOut, "fake CLI ran")
    let live = Snapshot.parse(r.stdout, today: localDay())
    check(live?.mains.count == 5, "real process output decodes")
    check((try? String(contentsOfFile: log, encoding: .utf8))?.contains("-p work quota monitor snapshot --json") == true, "fixed argv passed through")
    unlink(log)
    setenv("FAKE_SLEEP", "5", 1)
    let slow = HermesCLI.run(FAKE, ["quota", "status", "--json"], timeout: 1)
    check(slow.timedOut && slow.seconds < 4.5, "timeout kills the child (\(slow.seconds)s)")
    unsetenv("FAKE_SLEEP")
} else {
    print("skip: fake hermes not executable at \(FAKE)")
}

// ---------- status file ----------
let st = CompanionStatus(version: "1.0.0", notch: true, running_since: NOW, display: "both", pid: 42)
let enc = try! JSONSerialization.jsonObject(with: JSONEncoder().encode(st)) as! [String: Any]
check(enc["notch"] as? Bool == true && enc["version"] as? String == "1.0.0" && enc["running_since"] as? Double == NOW, "status file shape")

print("\(passed) passed, \(failures) failed")
exit(failures == 0 ? 0 : 1)
