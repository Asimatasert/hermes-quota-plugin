// Quota Notch — the only process this app ever starts is the user's own `hermes` CLI (plus `open` through
// NSWorkspace for hermes:// links). No shell, fixed argv, a hard timeout, stdout parsed as JSON, stderr discarded.
// Foundation only (unit-tested with a fake `hermes` script).

import Foundation

struct CLIResult {
    let status: Int32
    let stdout: Data
    let timedOut: Bool
    let seconds: Double
}

enum HermesCLI {
    /// Where `hermes` usually lives (the official installer, pipx/uv, Homebrew). The user can override it.
    static func candidates(home: String = NSHomeDirectory(), path: String? = ProcessInfo.processInfo.environment["PATH"]) -> [String] {
        var out = ["\(home)/.local/bin/hermes", "\(home)/.hermes/hermes-agent/.hermes/bin/hermes",
                   "/opt/homebrew/bin/hermes", "/usr/local/bin/hermes"]
        for dir in (path ?? "").split(separator: ":") where !dir.isEmpty {
            out.append("\(dir)/hermes")
        }
        var seen = Set<String>()
        return out.filter { seen.insert($0).inserted }
    }

    /// Override (setting or QUOTA_NOTCH_HERMES env, for tests) first, then the candidates.
    static func locate(override: String?, fm: FileManager = .default) -> String? {
        let env = ProcessInfo.processInfo.environment["QUOTA_NOTCH_HERMES"]
        for o in [override, env] {
            if let o = o?.trimmingCharacters(in: .whitespaces), !o.isEmpty {
                let p = (o as NSString).expandingTildeInPath
                return fm.isExecutableFile(atPath: p) ? p : nil
            }
        }
        return candidates().first { fm.isExecutableFile(atPath: $0) }
    }

    /// `hermes [-p <profile>] quota <args...>`. The profile name is validated (it becomes one argv element anyway).
    static func argv(profile: String?, _ args: [String]) -> [String] {
        var out: [String] = []
        if let p = profile?.trimmingCharacters(in: .whitespaces), !p.isEmpty, p != "default",
           p.range(of: #"^[A-Za-z0-9_.-]{1,64}$"#, options: .regularExpression) != nil {
            out += ["-p", p]
        }
        return out + ["quota"] + args
    }

    /// Runs synchronously (call it off the main thread). Output above `maxBytes` is cut (the JSON then fails to
    /// parse and the poll counts as failed rather than eating memory).
    static func run(_ exe: String, _ args: [String], timeout: TimeInterval = 20, maxBytes: Int = 8 << 20) -> CLIResult {
        let t0 = Date()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: exe)
        p.arguments = args
        var env = ProcessInfo.processInfo.environment
        let home = NSHomeDirectory()
        // A Finder-launched app gets a minimal PATH; the hermes launcher may need its own bin dirs.
        env["PATH"] = ["\(home)/.local/bin", "\(home)/.hermes/hermes-agent/.hermes/bin", "/opt/homebrew/bin", "/usr/local/bin",
                       env["PATH"] ?? "/usr/bin:/bin:/usr/sbin:/sbin"].joined(separator: ":")
        env["NO_COLOR"] = "1"
        p.environment = env
        let out = Pipe()
        p.standardOutput = out
        p.standardError = FileHandle.nullDevice
        p.standardInput = FileHandle.nullDevice
        do { try p.run() } catch {
            return CLIResult(status: -1, stdout: Data(), timedOut: false, seconds: 0)
        }
        // Drain stdout concurrently: a payload bigger than the pipe buffer would otherwise block the child.
        var data = Data()
        let lock = NSLock()
        let reader = DispatchGroup()
        reader.enter()
        DispatchQueue.global(qos: .utility).async {
            let h = out.fileHandleForReading
            while true {
                let chunk = h.availableData
                if chunk.isEmpty { break }
                lock.lock()
                if data.count < maxBytes { data.append(chunk.prefix(maxBytes - data.count)) }
                lock.unlock()
            }
            reader.leave()
        }
        let done = DispatchSemaphore(value: 0)
        p.terminationHandler = { _ in done.signal() }
        var timedOut = false
        if done.wait(timeout: .now() + timeout) == .timedOut {
            timedOut = true
            p.terminate()
            if done.wait(timeout: .now() + 2) == .timedOut { kill(p.processIdentifier, SIGKILL); _ = done.wait(timeout: .now() + 2) }
        }
        _ = reader.wait(timeout: .now() + 2)
        lock.lock()
        let result = data
        lock.unlock()
        return CLIResult(status: timedOut ? -2 : p.terminationStatus, stdout: result, timedOut: timedOut,
                         seconds: Date().timeIntervalSince(t0))
    }
}
