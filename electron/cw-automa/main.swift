// cw-automa — macOS Accessibility automation helper for CoWorker's Computer Use.
//
// Newline-delimited JSON over stdin/stdout:
//   request  {"id":1,"method":"snapshot","params":{...}}
//   response {"id":1,"ok":true,"result":{...}} | {"id":1,"ok":false,"error":"..."}
//
// STRUCTURE-FIRST, SEMANTIC-IDENTITY refs (openclaw-style role/name refs):
//   * refs are `sig#n` where sig = normalized "role:label/placeholder" and n is
//     that sig's occurrence ordinal — an IDENTITY, not a positional counter.
//   * traversal is scoped to the app's FOCUSED WINDOW and skips noise (menu bar,
//     scroll bars, zero-size nodes) so refs do not churn when the UI loads or
//     animates. An action re-walks the CURRENT tree and matches sig#n, so it
//     survives reordering (the "no AX element for ref" dead-end is gone).
// Only the Accessibility permission is needed (no Screen Recording).

import Foundation
import ApplicationServices
import AppKit
import CoreGraphics

// Fixed tree depth for BOTH snapshot and act-resolution so ordinal counts match.
let TREE_DEPTH = 12

// ── AX helpers ──────────────────────────────────────────────────────────────

func axAttr(_ el: AXUIElement, _ name: String) -> Any? {
    var v: CFTypeRef?
    let r = AXUIElementCopyAttributeValue(el, name as CFString, &v)
    guard r == .success, let value = v else { return nil }
    return value
}

func axString(_ el: AXUIElement, _ name: String) -> String {
    (axAttr(el, name) as? String) ?? ""
}

func axBool(_ el: AXUIElement, _ name: String) -> Bool {
    (axAttr(el, name) as? Bool) ?? true
}

func frameValue(_ el: AXUIElement, _ name: String) -> CGSize? {
    guard let raw = axAttr(el, name) else { return nil }
    let v = raw as! AXValue
    var sz = CGSize.zero
    if AXValueGetValue(v, .cgSize, &sz) { return sz }
    return nil
}

func framePoint(_ el: AXUIElement) -> CGPoint? {
    guard let raw = axAttr(el, kAXPositionAttribute) else { return nil }
    let v = raw as! AXValue
    var p = CGPoint.zero
    if AXValueGetValue(v, .cgPoint, &p) { return p }
    return nil
}

func pointString(_ el: AXUIElement) -> String {
    guard let p = framePoint(el) else { return "" }
    return "(\(Int(p.x)),\(Int(p.y)))"
}

func sizeString(_ el: AXUIElement) -> String {
    guard let s = frameValue(el, kAXSizeAttribute) else { return "" }
    return "\(Int(s.width))x\(Int(s.height))"
}

// ── Roles / noise / semantic signature ──────────────────────────────────────

let INTERACTIVE: Set<String> = [
    kAXButtonRole, kAXCheckBoxRole, kAXRadioButtonRole, kAXTextFieldRole,
    kAXTextAreaRole, kAXPopUpButtonRole, kAXComboBoxRole, kAXMenuBarItemRole,
    kAXMenuItemRole, kAXStaticTextRole, kAXWindowRole,
]

// Roles that only churn (global menu bar, scroll bars) — never surfaced.
let NOISE_ROLES: Set<String> = [kAXMenuBarRole, kAXScrollBarRole, kAXMenuRole]

func isInteractive(_ role: String) -> Bool { INTERACTIVE.contains(role) }

// Zero-size elements are hidden/offscreen (e.g. parked menu items) — ignore.
func isZeroSize(_ el: AXUIElement) -> Bool {
    guard let s = frameValue(el, kAXSizeAttribute) else { return false }
    return s.width <= 1 && s.height <= 1
}

func normalizedSigText(_ s: String) -> String {
    let parts = s.split(whereSeparator: { $0.isWhitespace })
    return parts.joined(separator: " ").lowercased()
}

func displayLabel(_ el: AXUIElement) -> String {
    let title = axString(el, kAXTitleAttribute)
    let desc = axString(el, kAXDescriptionAttribute)
    let ph = axString(el, kAXPlaceholderValueAttribute)
    return title.isEmpty ? (desc.isEmpty ? ph : desc) : title
}

func elementSig(_ el: AXUIElement) -> String {
    let role = axString(el, kAXRoleAttribute)
    let label = displayLabel(el)
    return normalizedSigText(role.isEmpty ? ":" : role + ":" + label)
}

// ── Tree model ──────────────────────────────────────────────────────────────

struct AXNode {
    var ref: String
    var sig: String
    var role: String
    var label: String
    var value: String
    var enabled: Bool
    var position: String
    var size: String
    var children: [AXNode]
}

// The frontmost window to scope interaction to (stable numbering).
func frontmostWindow(_ app: AXUIElement) -> AXUIElement? {
    if let raw = axAttr(app, kAXFocusedWindowAttribute) {
        return (raw as! AXUIElement)
    }
    if let raw = axAttr(app, kAXWindowsAttribute) {
        if let first = (raw as! [AXUIElement]).first { return first }
    }
    return nil
}

// Recursively build the tree scoped to `root` (a window). Counting rule (shared
// with findElement so refs always match): every visited NON-noise, non-root node
// advances its sig's ordinal, whether or not it is later kept. Noise roles and
// zero-size nodes are dropped WITHOUT counting or recursing. A node is kept if
// it is the root, interactive, or has kept children; kept nodes carry `sig#n`.
func buildTree(_ el: AXUIElement, isRoot: Bool, depth: Int, seq: inout [String: Int], refCount: inout Int) -> AXNode? {
    let role = axString(el, kAXRoleAttribute)
    if role.isEmpty { return nil }
    if !isRoot {
        if NOISE_ROLES.contains(role) || isZeroSize(el) {
            return nil // noise: not counted, not recursed, not surfaced
        }
    }
    let sig = elementSig(el)
    var ord = 0
    if !isRoot {
        seq[sig, default: 0] += 1
        ord = seq[sig]!
    }

    var children: [AXNode] = []
    if depth > 0, let rawKids = axAttr(el, kAXChildrenAttribute) {
        let kids = (rawKids as! [AXUIElement])
        for kid in kids {
            if let c = buildTree(kid, isRoot: false, depth: depth - 1, seq: &seq, refCount: &refCount) {
                children.append(c)
            }
        }
    }

    let keep = isRoot || isInteractive(role) || !children.isEmpty
    if !keep { return nil }
    refCount += 1
    let ref = isRoot ? "window" : "\(sig)#\(ord)"
    let node = AXNode(ref: ref, sig: sig, role: role, label: displayLabel(el),
                      value: axString(el, kAXValueAttribute), enabled: isEnabled(el),
                      position: pointString(el), size: sizeString(el), children: children)
    return node
}

func isEnabled(_ el: AXUIElement) -> Bool {
    if axAttr(el, kAXEnabledAttribute) == nil { return true }
    return axBool(el, kAXEnabledAttribute)
}

// ── Act resolution: match ref `sig#n` against the CURRENT window tree ──────

func findElement(_ app: AXUIElement, ref: String) -> AXUIElement? {
    guard let win = frontmostWindow(app) else { return nil }
    let parts = ref.split(separator: "#", maxSplits: 1, omittingEmptySubsequences: false)
    guard parts.count == 2, let want = Int(parts[1]) else { return nil }
    let sigRef = String(parts[0])
    var seq = 0
    var result: AXUIElement? = nil

    func scan(_ el: AXUIElement, depth: Int, isRoot: Bool) -> Bool {
        let role = axString(el, kAXRoleAttribute)
        if role.isEmpty { return false }
        let noisy = !isRoot && (NOISE_ROLES.contains(role) || isZeroSize(el))
        if !isRoot && !noisy && sigRef == elementSig(el) {
            seq += 1
            if seq == want {
                result = el
                return true
            }
        }
        if depth > 0, let rawKids = axAttr(el, kAXChildrenAttribute) {
            let kids = (rawKids as! [AXUIElement])
            for kid in kids {
                if scan(kid, depth: depth - 1, isRoot: false) { return true }
            }
        }
        return false
    }
    _ = scan(win, depth: TREE_DEPTH, isRoot: true)
    return result
}

// ── CoreGraphics input ──────────────────────────────────────────────────────

func postMouse(_ type: CGEventType, _ point: CGPoint) {
    let ev = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: .left)
    ev?.post(tap: .cghidEventTap)
}

func keyCode(_ key: String) -> CGKeyCode? {
    let map: [String: CGKeyCode] = [
        "space": 49, "tab": 48, "return": 36, "enter": 36, "escape": 53,
        "cmd": 55, "command": 55, "leftcmd": 55, "ctrl": 59, "control": 59,
        "option": 58, "alt": 58, "shift": 56, "left": 123, "right": 124,
        "up": 126, "down": 125, "home": 115, "end": 119, "pageup": 116,
        "pagedown": 121, "backspace": 51, "delete": 51, "capslock": 57,
    ]
    if key.count == 1, let c = key.lowercased().unicodeScalars.first, c.value >= 97, c.value <= 122 {
        return CGKeyCode(c.value - 97)
    }
    return map[key.lowercased()]
}

func modifiersFor(_ names: [String]) -> CGEventFlags {
    var f: CGEventFlags = []
    for n in names {
        switch n.lowercased() {
        case "cmd", "command", "meta", "super": f.insert(.maskCommand)
        case "ctrl", "control": f.insert(.maskControl)
        case "alt", "option": f.insert(.maskAlternate)
        case "shift": f.insert(.maskShift)
        default: break
        }
    }
    return f
}

func press(_ key: String, _ mods: [String]) {
    guard let code = keyCode(key) else { return }
    let flags = modifiersFor(mods)
    let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)
    down?.flags = flags
    down?.post(tap: .cghidEventTap)
    let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
    up?.flags = flags
    up?.post(tap: .cghidEventTap)
}

func typeText(_ text: String) {
    for scalar in text.unicodeScalars {
        var chars = [UniChar(scalar.value)]
        let down = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: true)
        down?.keyboardSetUnicodeString(stringLength: 1, unicodeString: &chars)
        down?.post(tap: .cghidEventTap)
        let up = CGEvent(keyboardEventSource: nil, virtualKey: 0, keyDown: false)
        up?.keyboardSetUnicodeString(stringLength: 1, unicodeString: &chars)
        up?.post(tap: .cghidEventTap)
    }
}

func sleepMs(_ ms: Int) {
    usleep(useconds_t(ms * 1000))
}

// A REAL editing session: many apps (Apple Music search, forms, IM fields) only
// accept text typed with keyboard focus — AX setValue alone fills the value but
// leaves the field "unfocused", so an Enter afterwards is discarded. Sequence:
// activate app -> AX focus -> click the field (real caret) -> Cmd+A select all
// -> type the text as keyboard events -> optionally press Enter to submit.
func realTypeInto(app: AXUIElement, pid: pid_t, el: AXUIElement, text: String, submit: Bool) {
    // 1) Bring the owning app to the front (clicks/keys must land on it).
    if let running = NSRunningApplication(processIdentifier: pid) {
        running.activate(options: [.activateIgnoringOtherApps])
    } else {
        AXUIElementPerformAction(app, kAXRaiseAction as CFString)
    }
    sleepMs(160)
    // 2) AX-level focus.
    AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, true as CFTypeRef)
    sleepMs(80)
    // 3) Real click in the field -> caret + actual editing session.
    clickCenter(el)
    sleepMs(140)
    // 4) Select existing content so typing replaces it.
    press("a", ["cmd"])
    sleepMs(60)
    // 5) Type as keyboard events (unicode-safe; like typing/pasting, no IME).
    typeText(text)
    sleepMs(80)
    // 6) Optional submit (Enter) — search fields need it while still focused.
    if submit {
        press("return", [])
        sleepMs(80)
    }
}

func clickCenter(_ el: AXUIElement) {
    guard let p = framePoint(el), let s = frameValue(el, kAXSizeAttribute) else { return }
    let c = CGPoint(x: p.x + s.width / 2, y: p.y + s.height / 2)
    postMouse(.leftMouseDown, c)
    postMouse(.leftMouseUp, c)
}

// ── JSON plumbing ───────────────────────────────────────────────────────────

func jsonStr(_ obj: Any) -> String {
    let data = try? JSONSerialization.data(withJSONObject: obj, options: [])
    return String(data: data ?? Data(), encoding: .utf8) ?? "{}"
}

struct Request { let id: Int; let method: String; let params: [String: Any] }

func emit(_ obj: [String: Any]) {
    let s = jsonStr(obj)
    FileHandle.standardOutput.write((s + "\n").data(using: .utf8)!)
    fflush(stdout)
}

func run() {
    setvbuf(stdout, nil, _IONBF, 0)
    var buffer = Data()
    while true {
        var chunk = [UInt8](repeating: 0, count: 65536)
        let n = read(0, &chunk, chunk.count)
        if n <= 0 { break }
        buffer.append(contentsOf: chunk[0..<n])
        while let nl = buffer.firstIndex(of: 0x0A) {
            let line = buffer.subdata(in: buffer.startIndex..<nl)
            buffer.removeSubrange(buffer.startIndex...nl)
            if line.isEmpty { continue }
            if let req = parse(String(data: line, encoding: .utf8) ?? "") {
                handle(req)
            }
        }
    }
}

func parse(_ line: String) -> Request? {
    guard let data = line.data(using: .utf8),
          let obj = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let id = obj["id"] as? Int,
          let method = obj["method"] as? String else { return nil }
    let params = (obj["params"] as? [String: Any]) ?? [:]
    return Request(id: id, method: method, params: params)
}

func handle(_ req: Request) {
    let respond = { (ok: Bool, result: Any?, error: String?) in
        var out: [String: Any] = ["id": req.id, "ok": ok]
        if let r = result { out["result"] = r }
        if let e = error { out["error"] = e }
        emit(out)
    }
    switch req.method {
    case "ping":
        respond(true, ["platform": "darwin"], nil)
    case "frontmost":
        let pid = frontmostPid()
        respond(true, ["pid": pid ?? -1, "app": NSWorkspace.shared.frontmostApplication?.localizedName ?? ""], nil)
    case "snapshot":
        let pid = frontmostPid() ?? -1
        let app = AXUIElementCreateApplication(pid_t(pid))
        let frontName = NSWorkspace.shared.frontmostApplication?.localizedName ?? ""
        if axString(app, kAXRoleAttribute).isEmpty {
            respond(false, nil, "accessibility_not_trusted")
            return
        }
        guard let win = frontmostWindow(app) else {
            // App has no readable window (menu-bar-only / canvas) — honest minimal.
            respond(true, ["frontmost": frontName, "refs": 0, "root": [
                "ref": "window", "role": "AXApplication", "label": frontName,
                "note": "No readable window in the frontmost app.",
            ]], nil)
            return
        }
        var seq: [String: Int] = [:]
        var refCount = 0
        guard let root = buildTree(win, isRoot: true, depth: TREE_DEPTH, seq: &seq, refCount: &refCount) else {
            respond(true, ["frontmost": frontName, "refs": 0, "root": [
                "ref": "window", "role": "AXWindow", "label": frontName,
                "note": "No readable elements in the frontmost window.",
            ]], nil)
            return
        }
        respond(true, ["frontmost": frontName, "refs": refCount, "root": nodeDict(root)], nil)
    case "act":
        let pid = (req.params["pid"] as? Int) ?? Int(frontmostPid() ?? -1)
        let app = AXUIElementCreateApplication(pid_t(pid))
        let ref = (req.params["ref"] as? String) ?? ""
        let op = (req.params["op"] as? String) ?? "click"
        guard let el = findElement(app, ref: ref) else {
            respond(false, nil, "no AX element for ref \(ref)")
            return
        }
        switch op {
        case "click", "double", "right":
            let r = AXUIElementPerformAction(el, kAXPressAction as CFString)
            if r == .success {
                respond(true, ["performed": op], nil)
            } else {
                clickCenter(el)
                respond(true, ["performed": op, "via": "coords-fallback"], nil)
            }
        case "set_value", "focus":
            if let text = req.params["value"] as? String {
                AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFTypeRef)
            }
            if op == "focus" {
                AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, true as CFTypeRef)
            }
            respond(true, ["performed": op], nil)
        case "type_into":
            if let text = req.params["text"] as? String {
                let submit = (req.params["submit"] as? Bool) ?? false
                let pid = (req.params["pid"] as? Int) ?? Int(frontmostPid() ?? -1)
                realTypeInto(app: app, pid: pid_t(pid), el: el, text: text, submit: submit)
                respond(true, ["performed": "type_into", "submit": submit], nil)
            } else {
                respond(false, nil, "type_into requires text")
            }
        case "show":
            AXUIElementPerformAction(el, kAXRaiseAction as CFString)
            respond(true, ["performed": "show"], nil)
        default:
            respond(false, nil, "unsupported op \(op)")
        }
    case "press_hotkey":
        press((req.params["key"] as? String) ?? "", (req.params["modifiers"] as? [String]) ?? [])
        respond(true, ["performed": "press_hotkey"], nil)
    case "type_text":
        typeText((req.params["text"] as? String) ?? "")
        respond(true, ["performed": "type_text"], nil)
    case "click_coords":
        let pt = CGPoint(x: (req.params["x"] as? Double) ?? 0, y: (req.params["y"] as? Double) ?? 0)
        postMouse(.leftMouseDown, pt)
        postMouse(.leftMouseUp, pt)
        respond(true, ["performed": "click_coords"], nil)
    case "launch":
        let app = (req.params["app"] as? String) ?? ""
        if app.isEmpty { respond(false, nil, "launch requires app"); return }
        let ok = NSWorkspace.shared.launchApplication(app)
        respond(ok, ["launched": app], ok ? nil : "open -a failed for \(app)")
    case "scroll":
        let dy = (req.params["dy"] as? Double) ?? 0
        let dx = (req.params["dx"] as? Double) ?? 0
        let ev = CGEvent(scrollWheelEvent2Source: nil, units: .pixel, wheelCount: 1,
                         wheel1: Int32(dy), wheel2: Int32(dx), wheel3: 0)
        ev?.post(tap: .cghidEventTap)
        respond(true, ["performed": "scroll"], nil)
    default:
        respond(false, nil, "unknown method \(req.method)")
    }
}

func frontmostPid() -> pid_t? {
    NSWorkspace.shared.frontmostApplication?.processIdentifier
}

func nodeDict(_ n: AXNode) -> [String: Any] {
    var d: [String: Any] = [
        "ref": n.ref, "sig": n.sig, "role": n.role, "label": n.label, "value": n.value,
        "enabled": n.enabled, "position": n.position, "size": n.size,
    ]
    if !n.children.isEmpty { d["children"] = n.children.map(nodeDict) }
    return d
}

run()
