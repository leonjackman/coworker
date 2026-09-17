//
//  TextInput.swift
//  cw-automa
//
//  AX-first text entry ladder (Codex-parity). Strategy order, most reliable
//  first, least invasive last:
//
//    1. AX value       — AXUIElementSetAttributeValue(kAXValue) when settable
//    2. AX focus + unicode keys — CGEvent.keyboardSetUnicodeString to the pid
//    3. Clipboard paste — general pasteboard + promised-data receipt, then wait
//                         for the target field to actually change
//
//  Every strategy reports what it did and whether a readback confirmed it, so
//  callers never have to infer success from a bare "ok". Synthetic pointer
//  events (clicking to focus) are the last resort, only when AX focus fails.
//

import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

// MARK: - Target-change observer

/// Watches a focused element for value/selection changes via AXObserver. Used to
/// confirm that a clipboard paste was actually consumed by the target field
/// (the pasteboard read alone only proves *someone* read the bytes).
final class AXTargetWatcher {
    private var observer: AXObserver?
    private var registered: [CFString] = []
    private let lock = NSLock()
    private var changed = false

    init?(pid: pid_t, element: AXUIElement) {
        var created: AXObserver?
        let context = Unmanaged.passUnretained(self).toOpaque()
        guard AXObserverCreate(pid, { _, _, _, ctx in
            guard let ctx else { return }
            Unmanaged<AXTargetWatcher>.fromOpaque(ctx).takeUnretainedValue().markChanged()
        }, &created) == .success, let created else { return nil }
        for name in [kAXSelectedTextChangedNotification, kAXValueChangedNotification] {
            if AXObserverAddNotification(created, element, name as CFString, context) == .success {
                registered.append(name as CFString)
            }
        }
        CFRunLoopAddSource(CFRunLoopGetMain(), AXObserverGetRunLoopSource(created), .commonModes)
        observer = created
    }

    fileprivate func markChanged() {
        lock.lock(); changed = true; lock.unlock()
    }

    var hasChanged: Bool {
        lock.lock(); defer { lock.unlock() }
        return changed
    }

    func stop() {
        guard let obs = observer else { return }
        let source = AXObserverGetRunLoopSource(obs)
        CFRunLoopRemoveSource(CFRunLoopGetMain(), source, .commonModes)
        observer = nil
        registered = []
    }

    deinit { stop() }
}

// MARK: - Pasteboard receipt

/// A promised-data provider: AppKit calls `provideDataForType` only when a
/// consumer actually requests the bytes. That read is evidence that a paste
/// happened, but it does not identify the reader — the target watcher covers
/// that second half.
final class PasteReceipt: NSObject, NSPasteboardItemDataProvider {
    private let payload: Data
    private let lock = NSLock()
    private var supplied = false
    private var requested = false

    init(text: String) {
        payload = Data(text.utf8)
        super.init()
    }

    func pasteboard(_ pasteboard: NSPasteboard?, item: NSPasteboardItem, provideDataForType type: NSPasteboard.PasteboardType) {
        guard type == .string else { return }
        lock.lock(); requested = true; lock.unlock()
        guard item.setData(payload, forType: .string) else { return }
        lock.lock(); supplied = true; lock.unlock()
    }

    var wasRequested: Bool { lock.lock(); defer { lock.unlock() }; return requested }
    var wasSupplied: Bool { lock.lock(); defer { lock.unlock() }; return supplied }
}

// MARK: - Text input ladder

enum TextInput {

    /// Result of an entry attempt. `strategy` tells the caller which rung ran,
    /// `verified` is a multi-signal judgement, `value` is the best readback.
    struct Outcome {
        var strategy: String
        var verified: Bool
        var value: String
        var notes: [String]

        var dict: [String: Any] {
            var out: [String: Any] = [
                "strategy": strategy,
                "verified": verified,
                "value": value,
            ]
            if !notes.isEmpty { out["notes"] = notes }
            return out
        }
    }

    /// Enter text into `ref` (or the app's focused element when ref is empty).
    /// Never throws on a failed strategy — it walks the ladder and reports the
    /// best outcome, so the caller can decide (rather than a false "ok").
    @discardableResult
    static func enter(pid: pid_t, ref: String, text: String, submit: Bool) -> Outcome {
        var notes: [String] = []
        let app = AXUIElementCreateApplication(pid)
        let element: AXUIElement? = ref.isEmpty ? nil : AX.find(app, ref: ref)
        if !ref.isEmpty && element == nil {
            notes.append("ref_not_found:\(ref)")
        }

        // ── 1. AX value (most deterministic) ────────────────────────────────
        if let el = element, isSettable(el, kAXValueAttribute) {
            let err = AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFString)
            if err == .success {
                let read = readValue(el)
                if confirms(read, text) {
                    return Outcome(strategy: "ax_value", verified: true, value: read ?? "", notes: notes)
                }
                notes.append("ax_value_unconfirmed")
            } else {
                notes.append("ax_value_error:\(err.rawValue)")
            }
        } else if element != nil {
            notes.append("value_not_settable")
        }

        // ── 2. AX focus + unicode key events ────────────────────────────────
        let focusedBefore = AX.focusedElement(app) ?? element
        if let el = focusedBefore ?? element {
            _ = setBool(el, kAXFocusedAttribute)
        }
        // AX focus is enough for native fields; clicking is the pointer fallback.
        if let el = element, !confirmFocused(app, el) {
            if let c = AX.center(el) {
                try? Injection.clickToPid(x: c.x, y: c.y, pid: pid)
                notes.append("pointer_focus_fallback")
            }
        }
        if Injection.typeUnicodeToPid(text, pid: pid) {
            if let el2 = AX.focusedElement(app) ?? element,
               let read = readValue(el2), confirms(read, text) {
                return Outcome(strategy: "unicode_keys", verified: true, value: read, notes: notes)
            }
            notes.append("unicode_unconfirmed")
        }

        // ── 3. Clipboard paste with receipt + target confirmation ───────────
        if paste(pid: pid, text: text, notes: &notes) {
            let read = readFocusedValue(pid) 
            var outcome = Outcome(strategy: "clipboard", verified: true, value: read ?? "", notes: notes)
            if read == nil { notes.append("clipboard_confirmed_by_target_change") }
            outcome.notes = notes
            if submit { submitReturn(pid) }
            return outcome
        }

        if submit { submitReturn(pid) }
        let read = readFocusedValue(pid) ?? ""
        return Outcome(strategy: "none", verified: confirms(read, text), value: read, notes: notes)
    }

    // MARK: Paste

    /// Write `text` to the general pasteboard, post Cmd+V, and wait until both
    /// the promised bytes were read AND the target field changed. The user's
    /// clipboard is restored only when we still own it.
    static func paste(pid: pid_t, text: String, notes: inout [String]) -> Bool {
        let pb = NSPasteboard.general
        let prior = pb.string(forType: .string)
        let focused = AX.focusedElement(AXUIElementCreateApplication(pid))
        let watcher = focused.flatMap { AXTargetWatcher(pid: pid, element: $0) }
        let baseline = focused.flatMap { readValue($0) } ?? ""

        let receipt = PasteReceipt(text: text)
        pb.clearContents()
        let item = NSPasteboardItem()
        item.setDataProvider(receipt, forTypes: [.string])
        guard pb.writeObjects([item]) else {
            notes.append("paste_write_failed")
            restore(pb, prior: prior, notes: &notes)
            return false
        }

        try? Injection.keyToPid("cmd+v", pid: pid)

        let deadline = Date().addingTimeInterval(2.0)
        var supplied = false
        var confirmed = false
        while Date() < deadline {
            if !supplied, receipt.wasSupplied { supplied = true }
            if watcher?.hasChanged == true { confirmed = true; break }
            if let f = focused, let now = readValue(f), now != baseline, confirms(now, text) {
                confirmed = true; break
            }
            if let f = focused, let now = readValue(f), now != baseline, watcher?.hasChanged == true {
                confirmed = true; break
            }
            usleep(25 * 1000)
        }
        watcher?.stop()
        notes.append("paste_read:\(supplied)")
        restore(pb, prior: prior, notes: &notes)
        return supplied && confirmed
    }

    private static func restore(_ pb: NSPasteboard, prior: String?, notes: inout [String]) {
        // Only restore when we still own the pasteboard (the user may have copied
        // something new in the meantime — their copy always wins).
        guard pb.string(forType: .string) == nil else { return }
        pb.clearContents()
        if let prior, !prior.isEmpty { pb.setString(prior, forType: .string) }
        notes.append("clipboard_restored")
    }

    // MARK: Submit

    static func submitReturn(_ pid: pid_t) {
        try? Injection.keyToPid("return", pid: pid)
    }

    // MARK: AX helpers

    private static func copyAttr(_ el: AXUIElement, _ name: String) -> CFTypeRef? {
        var value: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, name as CFString, &value) == .success else { return nil }
        return value
    }

    static func isSettable(_ el: AXUIElement, _ name: String) -> Bool {
        var settable: DarwinBoolean = false
        guard AXUIElementIsAttributeSettable(el, name as CFString, &settable) == .success else { return false }
        return settable.boolValue
    }

    static func readValue(_ el: AXUIElement) -> String? {
        if let v = copyAttr(el, kAXValueAttribute) as? String { return v }
        if let v = copyAttr(el, kAXValueAttribute) { return "\(v)" }
        return nil
    }

    private static func readFocusedValue(_ pid: pid_t) -> String? {
        guard let fe = AX.focusedElement(AXUIElementCreateApplication(pid)) else { return nil }
        return readValue(fe)
    }

    private static func setBool(_ el: AXUIElement, _ name: String) -> Bool {
        AXUIElementSetAttributeValue(el, name as CFString, kCFBooleanTrue) == .success
    }

    private static func confirmFocused(_ app: AXUIElement, _ el: AXUIElement) -> Bool {
        guard let focused = AX.focusedElement(app) else { return false }
        return CFEqual(focused, el)
    }

    /// Content comparison that ignores case and inter-token whitespace.
    private static func confirms(_ read: String?, _ wanted: String) -> Bool {
        guard let read, !read.isEmpty, !wanted.isEmpty else { return false }
        let a = normalize(read)
        let b = normalize(wanted)
        return a.contains(b) || b.contains(a)
    }

    private static func normalize(_ s: String) -> String {
        s.lowercased().filter { !$0.isWhitespace }
    }
}
