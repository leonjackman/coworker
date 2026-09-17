//
//  UISettle.swift
//  cw-automa
//
//  UI-settle policy: after an action that changes an app's UI, wait until the
//  accessibility tree stops changing before the agent observes. This replaces
//  the guesswork of fixed sleeps — a spinner/transition keeps the signature
//  moving, so we wait; a static screen returns immediately.
//

import ApplicationServices
import Foundation

enum UISettle {

    /// Current tree signature for the app's focused window. `nil` when the app
    /// has no readable window (the caller treats that as "nothing to settle").
    static func signature(pid: pid_t, depth: Int = 6) -> String? {
        let app = AXUIElementCreateApplication(pid)
        guard let win = AX.focusedWindow(app) else { return nil }
        var seq: [String: Int] = [:]
        var refCount = 0
        guard let root = AX.build(win, isRoot: true, depth: depth, seq: &seq, refCount: &refCount) else {
            return nil
        }
        var map: [String: AX.Node] = [:]
        AX.flatten(root, into: &map)
        // Order-independent digest: ref -> label|value.
        let parts = map.map { "\($0.key)=\($0.value.label)\u{1}\($0.value.value)" }.sorted()
        return "\(map.count):" + parts.joined(separator: "|")
    }

    /// Wait until the signature is unchanged for `quietMs`, or `timeoutMs`
    /// elapses. Returns true when settled, false on timeout.
    static func wait(pid: pid_t, quietMs: Int = 250, timeoutMs: Int = 3000) -> Bool {
        let deadline = Date().addingTimeInterval(Double(max(0, timeoutMs)) / 1000.0)
        var last = signature(pid: pid)
        var lastChange = Date()
        while Date() < deadline {
            usleep(50 * 1000)
            let now = signature(pid: pid)
            if now != last {
                last = now
                lastChange = Date()
                continue
            }
            if Date().timeIntervalSince(lastChange) * 1000 >= Double(quietMs) {
                return true
            }
        }
        return false
    }
}
