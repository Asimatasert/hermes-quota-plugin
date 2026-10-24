// Release builds: no hooks. `macos/shots.sh` compiles Tests/ShotsHooks.swift in place of this file.

@MainActor
enum Hooks {
    static func afterLaunch() {}
}
