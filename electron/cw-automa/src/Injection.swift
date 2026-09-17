//
//  Injection.swift
//  cw-automa
//
//  ABSOLUTE RULE — the reason this helper was rewritten:
//
//    Every mouse/keyboard event is delivered with `CGEvent.postToPid(_:)`.
//    We NEVER call `CGEvent.post(tap: .cghidEventTap)`, and we NEVER call
//    `CGWarpMouseCursorPosition` / `CGDisplayMoveCursorToPoint` / any NSEvent
//    mouse warp. The user must keep their physical mouse and keyboard free at
//    all times. The "cursor" the agent drives is the VirtualCursor overlay
//    (see VirtualCursor.swift); the real OS cursor is never touched.
//
//    A mouse event still carries a location — that is the *event location*
//    stamped so the receiving app hit-tests the right control. Posting to a pid
//    with a location does NOT move the hardware cursor; only a warp would.
//

import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

// MARK: - MouseButton

enum MouseButton: String {
    case left, right, middle, back, forward

    var down: CGEventType {
        switch self {
        case .left: return .leftMouseDown
        case .right: return .rightMouseDown
        case .middle, .back, .forward: return .otherMouseDown
        }
    }
    var up: CGEventType {
        switch self {
        case .left: return .leftMouseUp
        case .right: return .rightMouseUp
        case .middle, .back, .forward: return .otherMouseUp
        }
    }
    var dragged: CGEventType {
        switch self {
        case .left: return .leftMouseDragged
        case .right: return .rightMouseDragged
        case .middle, .back, .forward: return .otherMouseDragged
        }
    }
    var cg: CGMouseButton {
        switch self {
        case .left: return .left
        case .right: return .right
        case .middle: return .center
        case .back: return CGMouseButton(rawValue: 3)!
        case .forward: return CGMouseButton(rawValue: 4)!
        }
    }
    static func parse(_ raw: String?) -> MouseButton {
        switch (raw ?? "left").lowercased() {
        case "right": return .right
        case "middle", "center": return .middle
        case "back": return .back
        case "forward": return .forward
        default: return .left
        }
    }
}

// MARK: - Process target (pid-reuse safe)

struct ProcessTarget: Equatable {
    let pid: pid_t
    let bundleID: String
    let launchDate: Date?

    static func resolve(pid: pid_t) -> ProcessTarget? {
        guard pid > 0, let app = NSRunningApplication(processIdentifier: pid) else { return nil }
        return ProcessTarget(pid: pid, bundleID: app.bundleIdentifier ?? "", launchDate: app.launchDate)
    }

    /// Re-validate against the live process so a recycled pid can never be
    /// mistaken for the target we resolved earlier.
    func validated() -> ProcessTarget? {
        guard let app = NSRunningApplication(processIdentifier: pid) else { return nil }
        if let d = launchDate, let cur = app.launchDate, abs(cur.timeIntervalSince(d)) > 0.5 {
            return nil
        }
        if !bundleID.isEmpty, let cur = app.bundleIdentifier, cur != bundleID { return nil }
        return self
    }
}

// MARK: - Held input ledger (teardown safety)

@MainActor
private enum HeldState {
    struct Key { let code: CGKeyCode; let target: ProcessTarget }
    struct Btn { let button: MouseButton; let point: CGPoint; let target: ProcessTarget }

    static var keys: [Key] = []
    static var buttons: [Btn] = []
}

// MARK: - Injection

enum Injection {

    /// Inter-event spacing. Small, on the control queue via usleep (never on the
    /// main thread, so the cursor overlay keeps animating).
    static let pressHoldMs: UInt32 = 24
    static let interClickGapMs: UInt32 = 80
    static let interKeyGapMs: UInt32 = 6
    static let interGraphemeGapMs: UInt32 = 4

    /// First click into a background window is swallowed as the activation
    /// click; wait this long after bringing the window forward.
    static let focusSettleMs: UInt32 = 800

    private static var lastTarget: ProcessTarget?

    static func recordTarget(_ t: ProcessTarget) { lastTarget = t }
    static func clearTarget() { lastTarget = nil }
    static var lastTargetPid: pid_t? { lastTarget?.pid }

    /// Shared event source in the HID system state so synthetic events inherit
    /// the real keyboard/modifier baseline.
    static let source: CGEventSource? = CGEventSource(stateID: .hidSystemState)

    private static func eventSource() throws -> CGEventSource {
        guard let s = source else {
            throw HelperError("event_alloc", "Failed to create CGEventSource")
        }
        return s
    }

    // MARK: Target resolution

    /// Coordinate op: the topmost real window under the point owns the event.
    /// Fails closed (no frontmost fallback) so a desktop gap never drives
    /// whatever app happens to be in front.
    static func resolveCoordinateTarget(at point: CGPoint) throws -> ProcessTarget {
        guard let win = WindowList.topmost(at: point, excludingPID: getpid()),
              win.ownerPID > 0,
              let t = ProcessTarget.resolve(pid: win.ownerPID)
        else {
            throw HelperError("no_target", "No application window under the point")
        }
        try ensurePostable(t.pid)
        recordTarget(t)
        return t
    }

    /// Keyboard op: prefer the app the last coordinate action drove (so
    /// "click a field, then type" lands in that app even when it is not
    /// frontmost); fall back to the frontmost app only for a pure-key macro.
    static func resolveKeyboardTarget() throws -> ProcessTarget {
        if let t = lastTarget, t.pid != getpid(), let v = t.validated() {
            try ensurePostable(v.pid)
            return v
        }
        clearTarget()
        guard let front = NSWorkspace.shared.frontmostApplication,
              front.processIdentifier != getpid(),
              let t = ProcessTarget.resolve(pid: front.processIdentifier)
        else {
            throw HelperError("no_target", "No frontmost application")
        }
        try ensurePostable(t.pid)
        recordTarget(t)
        return t
    }

    /// Accessibility permission is required to post to any other process.
    static func ensurePostable(_ pid: pid_t) throws {
        if pid == getpid() { return }
        guard NSRunningApplication(processIdentifier: pid) != nil else {
            throw HelperError("no_target", "Target process \(pid) is not running")
        }
        guard AXIsProcessTrusted() else {
            throw HelperError(
                "not_trusted",
                "Accessibility permission is required. Grant it in System Settings ▸ Privacy & Security ▸ Accessibility."
            )
        }
    }

    // MARK: Event construction

    private static func makeMouse(
        _ type: CGEventType,
        at point: CGPoint,
        button: MouseButton,
        clickState: Int?,
        target: ProcessTarget,
        window: WindowInfo?
    ) throws -> CGEvent {
        if let window,
           let ev = WindowTargetedEvent.makeMouse(
               type: type, point: point, button: button,
               clickCount: max(1, clickState ?? 1), window: window
           ) {
            if let cs = clickState {
                ev.setIntegerValueField(.mouseEventClickState, value: Int64(cs))
            }
            return ev
        }
        // Fallback: window-less event (old behavior; never worse).
        let src = try eventSource()
        guard let ev = CGEvent(
            mouseEventSource: src, mouseType: type,
            mouseCursorPosition: point, mouseButton: button.cg
        ) else { throw HelperError("event_alloc", "Failed to allocate mouse event") }
        if let cs = clickState {
            ev.setIntegerValueField(.mouseEventClickState, value: Int64(cs))
        }
        return ev
    }

    /// Bring the target window forward when it is not frontmost and wait out the
    /// activation click. No-op when it is already frontmost.
    private static func focusForClick(_ target: ProcessTarget, window: WindowInfo?) {
        if NSWorkspace.shared.frontmostApplication?.processIdentifier == target.pid { return }
        guard let window else { return }
        if WindowTargetedEvent.focusWindow(pid: target.pid, windowID: window.id) {
            usleep(focusSettleMs * 1000)
        }
    }

    // MARK: Click

    static func click(
        x: Double, y: Double, button: MouseButton, count: Int, modifiers: [String]
    ) throws {
        let point = CGPoint(x: x, y: y)
        let target = try resolveCoordinateTarget(at: point)
        let window = WindowList.window(at: point, pid: target.pid)
        focusForClick(target, window: window)

        let clicks = max(1, count)
        let flags = KeyMapping.flags(for: modifiers)
        for state in 1...clicks {
            let down = try makeMouse(button.down, at: point, button: button, clickState: state, target: target, window: window)
            let up = try makeMouse(button.up, at: point, button: button, clickState: state, target: target, window: window)
            if !flags.isEmpty { down.flags = flags; up.flags = flags }
            WindowTargetedEvent.post(down, to: target.pid)
            usleep(pressHoldMs * 1000)
            WindowTargetedEvent.post(up, to: target.pid)
            if state < clicks { usleep(interClickGapMs * 1000) }
        }
    }

    // MARK: Decomposed press / release (drag across commands)

    static func mouseDown(_ button: MouseButton, at p: CGPoint) throws {
        let target = try resolveCoordinateTarget(at: p)
        let window = WindowList.window(at: p, pid: target.pid)
        focusForClick(target, window: window)
        let ev = try makeMouse(button.down, at: p, button: button, clickState: 1, target: target, window: window)
        WindowTargetedEvent.post(ev, to: target.pid)
        DispatchQueue.main.async { HeldState.buttons.append(HeldState.Btn(button: button, point: p, target: target)) }
    }

    static func mouseUp(_ button: MouseButton, at p: CGPoint) throws {
        let target = try resolveCoordinateTarget(at: p)
        let window = WindowList.window(at: p, pid: target.pid)
        let ev = try makeMouse(button.up, at: p, button: button, clickState: 1, target: target, window: window)
        WindowTargetedEvent.post(ev, to: target.pid)
        DispatchQueue.main.async { HeldState.buttons.removeAll { $0.button == button } }
    }

    // MARK: Drag

    static func drag(from: CGPoint, to: CGPoint, button: MouseButton, steps: Int) throws {
        let target = try resolveCoordinateTarget(at: from)
        let window = WindowList.window(at: from, pid: target.pid)
        focusForClick(target, window: window)

        let n = max(1, steps)
        let down = try makeMouse(button.down, at: from, button: button, clickState: 1, target: target, window: window)
        WindowTargetedEvent.post(down, to: target.pid)
        for step in 1...n {
            let t = Double(step) / Double(n)
            let pt = CGPoint(x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t)
            let ev = try makeMouse(button.dragged, at: pt, button: button, clickState: 1, target: target, window: window)
            WindowTargetedEvent.post(ev, to: target.pid)
            usleep(6 * 1000)
        }
        let up = try makeMouse(button.up, at: to, button: button, clickState: 1, target: target, window: window)
        WindowTargetedEvent.post(up, to: target.pid)
    }

    // MARK: Scroll

    static func scroll(x: Double, y: Double, dx: Int, dy: Int) throws {
        let point = CGPoint(x: x, y: y)
        let target = try resolveCoordinateTarget(at: point)
        let src = try eventSource()
        guard let ev = CGEvent(
            scrollWheelEvent2Source: src, units: .pixel, wheelCount: 2,
            wheel1: Int32(clamping: dy), wheel2: Int32(clamping: dx), wheel3: 0
        ) else { throw HelperError("event_alloc", "Failed to allocate scroll event") }
        ev.location = point
        WindowTargetedEvent.post(ev, to: target.pid)
    }

    // MARK: Keys

    static func key(_ sequence: String, repeat count: Int) throws {
        let target = try resolveKeyboardTarget()
        let (flags, code) = try KeyMapping.parse(sequence)
        let src = try eventSource()
        let reps = max(1, count)
        for i in 0..<reps {
            guard let down = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true) else {
                throw HelperError("event_alloc", "Failed to allocate key-down")
            }
            down.flags = flags
            WindowTargetedEvent.post(down, to: target.pid)
            usleep(interKeyGapMs * 1000)
            guard let up = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false) else {
                throw HelperError("event_alloc", "Failed to allocate key-up")
            }
            up.flags = []
            WindowTargetedEvent.post(up, to: target.pid)
            if i < reps - 1 { usleep(interKeyGapMs * 1000) }
        }
    }

    static func holdKey(_ names: [String], durationMs: Int) throws {
        let target = try resolveKeyboardTarget()
        let src = try eventSource()
        let codes = try names.map { try KeyMapping.keycode(forToken: $0) }
        for code in codes {
            guard let down = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true) else {
                throw HelperError("event_alloc", "Failed to allocate key-down")
            }
            WindowTargetedEvent.post(down, to: target.pid)
            DispatchQueue.main.async { HeldState.keys.append(HeldState.Key(code: code, target: target)) }
            usleep(interKeyGapMs * 1000)
        }
        usleep(UInt32(max(0, durationMs)) * 1000)
        for code in codes.reversed() {
            guard let up = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false) else { continue }
            up.flags = []
            WindowTargetedEvent.post(up, to: target.pid)
            DispatchQueue.main.async { HeldState.keys.removeAll { $0.code == code } }
            usleep(interKeyGapMs * 1000)
        }
    }

    /// Layout-independent per-grapheme unicode typing into the target app.
    static func typeUnicode(_ text: String) throws {
        guard !text.isEmpty else { return }
        let target = try resolveKeyboardTarget()
        let src = try eventSource()
        for grapheme in text {
            var utf16 = Array(String(grapheme).utf16)
            guard let down = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true) else {
                throw HelperError("event_alloc", "Failed to allocate type key-down")
            }
            down.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: &utf16)
            WindowTargetedEvent.post(down, to: target.pid)
            guard let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false) else {
                throw HelperError("event_alloc", "Failed to allocate type key-up")
            }
            up.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: &utf16)
            WindowTargetedEvent.post(up, to: target.pid)
            usleep(interGraphemeGapMs * 1000)
        }
    }

    // MARK: Explicit-target variants (app-bound actions)

    /// Press a key sequence into an explicit pid (no frontmost/lastTarget
    /// inference). Used by the AX-first text ladder and the persistent surface.
    static func keyToPid(_ sequence: String, pid: pid_t, repeat count: Int = 1) throws {
        guard pid > 0 else { throw HelperError("no_target", "keyToPid requires a pid") }
        let (flags, code) = try KeyMapping.parse(sequence)
        let src = try eventSource()
        let reps = max(1, count)
        for i in 0..<reps {
            guard let down = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: true) else {
                throw HelperError("event_alloc", "Failed to allocate key-down")
            }
            down.flags = flags
            WindowTargetedEvent.post(down, to: pid)
            usleep(interKeyGapMs * 1000)
            guard let up = CGEvent(keyboardEventSource: src, virtualKey: code, keyDown: false) else {
                throw HelperError("event_alloc", "Failed to allocate key-up")
            }
            up.flags = []
            WindowTargetedEvent.post(up, to: pid)
            if i < reps - 1 { usleep(interKeyGapMs * 1000) }
        }
        if let t = ProcessTarget.resolve(pid: pid) { recordTarget(t) }
    }

    /// Layout-independent per-grapheme unicode typing into an explicit pid.
    /// Returns false (instead of throwing) so the caller can fall through the
    /// ladder to the next strategy.
    @discardableResult
    static func typeUnicodeToPid(_ text: String, pid: pid_t) -> Bool {
        guard !text.isEmpty, pid > 0 else { return false }
        guard let src = source else { return false }
        for grapheme in text {
            var utf16 = Array(String(grapheme).utf16)
            guard let down = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: true) else { return false }
            down.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: &utf16)
            WindowTargetedEvent.post(down, to: pid)
            guard let up = CGEvent(keyboardEventSource: src, virtualKey: 0, keyDown: false) else { return false }
            up.keyboardSetUnicodeString(stringLength: utf16.count, unicodeString: &utf16)
            WindowTargetedEvent.post(up, to: pid)
            usleep(interGraphemeGapMs * 1000)
        }
        if let t = ProcessTarget.resolve(pid: pid) { recordTarget(t) }
        return true
    }

    /// A single click into an explicit pid (pointer focus fallback). Binds the
    /// event to that pid's window under the point when one exists.
    static func clickToPid(x: Double, y: Double, pid: pid_t) throws {
        let point = CGPoint(x: x, y: y)
        guard let target = ProcessTarget.resolve(pid: pid) else {
            throw HelperError("no_target", "clickToPid: process \(pid) is not running")
        }
        try ensurePostable(pid)
        let window = WindowList.window(at: point, pid: pid)
        let down = try makeMouse(.leftMouseDown, at: point, button: .left, clickState: 1, target: target, window: window)
        let up = try makeMouse(.leftMouseUp, at: point, button: .left, clickState: 1, target: target, window: window)
        WindowTargetedEvent.post(down, to: pid)
        usleep(pressHoldMs * 1000)
        WindowTargetedEvent.post(up, to: pid)
        recordTarget(target)
    }

    /// Scroll wheel events into an explicit pid, at a point inside its window.
    static func scrollToPid(x: Double, y: Double, dx: Int, dy: Int, pid: pid_t) throws {
        guard let target = ProcessTarget.resolve(pid: pid) else {
            throw HelperError("no_target", "scrollToPid: process \(pid) is not running")
        }
        try ensurePostable(pid)
        let point = CGPoint(x: x, y: y)
        guard let src = source,
              let ev = CGEvent(
                scrollWheelEvent2Source: src, units: .pixel, wheelCount: 2,
                wheel1: Int32(clamping: dy), wheel2: Int32(clamping: dx), wheel3: 0
              )
        else { throw HelperError("event_alloc", "Failed to allocate scroll event") }
        ev.location = point
        WindowTargetedEvent.post(ev, to: pid)
        recordTarget(target)
    }

    /// Drag gesture into an explicit pid, bound to its window under `from`.
    static func dragToPid(from: CGPoint, to: CGPoint, button: MouseButton, steps: Int, pid: pid_t) throws {
        guard let target = ProcessTarget.resolve(pid: pid) else {
            throw HelperError("no_target", "dragToPid: process \(pid) is not running")
        }
        try ensurePostable(pid)
        let window = WindowList.window(at: from, pid: pid)
        let n = max(1, steps)
        let down = try makeMouse(button.down, at: from, button: button, clickState: 1, target: target, window: window)
        WindowTargetedEvent.post(down, to: pid)
        for step in 1...n {
            let t = Double(step) / Double(n)
            let pt = CGPoint(x: from.x + (to.x - from.x) * t, y: from.y + (to.y - from.y) * t)
            let ev = try makeMouse(button.dragged, at: pt, button: button, clickState: 1, target: target, window: window)
            WindowTargetedEvent.post(ev, to: pid)
            usleep(6 * 1000)
        }
        let up = try makeMouse(button.up, at: to, button: button, clickState: 1, target: target, window: window)
        WindowTargetedEvent.post(up, to: pid)
        recordTarget(target)
    }

    // MARK: Teardown

    /// Release anything we pressed but never released, so an aborted agent loop
    /// never strands a held modifier or mouse button in the target app.
    @MainActor
    static func releaseAllHeld() {
        let src = source
        for held in HeldState.buttons.reversed() {
            guard let v = held.target.validated(), let src else { continue }
            if let ev = CGEvent(mouseEventSource: src, mouseType: held.button.up,
                                mouseCursorPosition: held.point, mouseButton: held.button.cg) {
                ev.setIntegerValueField(.mouseEventClickState, value: 1)
                ev.postToPid(v.pid)
            }
        }
        HeldState.buttons.removeAll()
        for held in HeldState.keys.reversed() {
            guard let v = held.target.validated(), let src else { continue }
            if let ev = CGEvent(keyboardEventSource: src, virtualKey: held.code, keyDown: false) {
                ev.flags = []
                ev.postToPid(v.pid)
            }
        }
        HeldState.keys.removeAll()
    }
}

private extension Int32 {
    init(clamping value: Int) {
        if value > Int(Int32.max) { self = Int32.max }
        else if value < Int(Int32.min) { self = Int32.min }
        else { self = Int32(value) }
    }
}
