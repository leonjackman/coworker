// cw-automa — macOS Accessibility automation helper for CoWorker's Computer Use.
//
// Runs as a child process of Electron main and speaks newline-delimited JSON
// over stdin/stdout:
//   request  {"id":1,"method":"snapshot","params":{...}}
//   response {"id":1,"ok":true,"result":{...}} | {"id":1,"ok":false,"error":"..."}
//
// Observation is STRUCTURE-FIRST (Accessibility element tree with stable `ref`
// paths) so the agent can act on real elements by ref instead of guessing pixel
// coordinates — the macOS analog of codex's CDP/DOM and openclaw's DOM+refs.
// Screen Recording is NOT required (only the Accessibility permission), so this
// works in the unsigned dev build too. Coordinates stay available as an explicit
// low-precision fallback for canvas/rendered content.

import Foundation
import ApplicationServices
import AppKit
import CoreGraphics

// ── AX helpers ──────────────────────────────────────────────────────────────

// The kAX* attribute constants bridge to Swift as `String`; convert on the way in.
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

func axInt(_ el: AXUIElement, _ name: String) -> Int {
    (axAttr(el, name) as? Int) ?? 0
}

func pointString(_ el: AXUIElement) -> String {
    guard let raw = axAttr(el, kAXPositionAttribute) else { return "" }
    let p = raw as! AXValue
    var point = CGPoint.zero
    if AXValueGetValue(p, .cgPoint, &point) { return "(\(Int(point.x)),\(Int(point.y)))" }
    return ""
}

func sizeString(_ el: AXUIElement) -> String {
    guard let raw = axAttr(el, kAXSizeAttribute) else { return "" }
    let s = raw as! AXValue
    var sz = CGSize.zero
    if AXValueGetValue(s, .cgSize, &sz) { return "\(Int(sz.width))x\(Int(sz.height))" }
    return ""
}

let INTERACTIVE: Set<String> = [
    kAXButtonRole, kAXCheckBoxRole, kAXRadioButtonRole, kAXTextFieldRole,
    kAXTextAreaRole, kAXPopUpButtonRole, kAXComboBoxRole, kAXMenuBarItemRole,
    kAXMenuItemRole, kAXStaticTextRole, kAXWindowRole,
]

func isInteractive(role: String) -> Bool { INTERACTIVE.contains(role) }

struct AXNode {
    var ref: String
    var role: String
    var label: String
    var value: String
    var enabled: Bool
    var position: String
    var size: String
    var children: [AXNode]
}

// Walk the AX tree from an app element, keeping interactive elements + windows.
func walk(_ el: AXUIElement, path: String, depth: Int, consumedRefs: inout Int) -> AXNode? {
    let role = axString(el, kAXRoleAttribute)
    guard !role.isEmpty else { return nil }
    consumedRefs += 1
    let ref = String(consumedRefs)
    let label = axString(el, kAXTitleAttribute).isEmpty ? axString(el, kAXDescriptionAttribute) : axString(el, kAXTitleAttribute)
    let value = axString(el, kAXValueAttribute)
    let enabled = aeEnabled(el)
    let pos = pointString(el)
    let size = sizeString(el)

    var children: [AXNode] = []
    if depth > 0, let kids = axAttr(el, kAXChildrenAttribute) as? [AXUIElement] {
        for (idx, kid) in kids.enumerated() {
            if let n = walk(kid, path: ref + "." + String(idx), depth: depth - 1, consumedRefs: &consumedRefs) {
                children.append(n)
            }
        }
    }

    let interactive = isInteractive(role: role)
    // Always keep windows and interactive elements; drop pure containers/labels
    // that have no children, to keep the tree compact.
    if role == kAXWindowRole || interactive || !children.isEmpty {
        return AXNode(ref: ref, role: role, label: label, value: value, enabled: enabled,
                      position: pos, size: size, children: children)
    }
    return nil
}

func aeEnabled(_ el: AXUIElement) -> Bool {
    // Presence of AXEnabled is not universal; treat missing as enabled.
    if axAttr(el, kAXEnabledAttribute) == nil { return true }
    return axBool(el, kAXEnabledAttribute)
}

func frontmostPid() -> pid_t? {
    let app = NSWorkspace.shared.frontmostApplication
    return app?.processIdentifier
}

// ── Act resolution (find element by ref path, re-walking from the app) ─────

func findElement(_ app: AXUIElement, ref: String) -> AXUIElement? {
    // The ref is a DFS counter value embedded in snapshot. Re-walk and match.
    var counter = 0
    func match(_ el: AXUIElement, depth: Int) -> AXUIElement? {
        let role = axString(el, kAXRoleAttribute)
        if role.isEmpty { return nil }
        counter += 1
        if counter == Int(ref) { return el }
        if depth <= 0 { return nil }
        if let kids = axAttr(el, kAXChildrenAttribute) as? [AXUIElement] {
            for kid in kids {
                if let found = match(kid, depth: depth - 1) { return found }
            }
        }
        return nil
    }
    return match(app, depth: 12)
}

// A reusable AX value for a point-based click is not needed; coords go via CGEvent.

// ── CoreGraphics input ──────────────────────────────────────────────────────

func postMouse(_ type: CGEventType, _ point: CGPoint) {
    let down = CGEvent(mouseEventSource: nil, mouseType: type, mouseCursorPosition: point, mouseButton: .left)
    down?.post(tap: .cghidEventTap)
}

func keyCode(_ key: String) -> CGKeyCode? {
    // Map common names -> virtual key codes (ANSI). Enough for hotkeys + typing.
    let map: [String: CGKeyCode] = [
        "space": 49, "tab": 48, "return": 36, "enter": 36, "escape": 53,
        "cmd": 55, "command": 55, "leftcmd": 55, "ctrl": 59, "control": 59,
        "option": 58, "alt": 58, "shift": 56, "left": 123, "right": 124,
        "up": 126, "down": 125, "home": 115, "end": 119, "pageup": 116,
        "pagedown": 121, "backspace": 51, "delete": 51, "capslock": 57,
    ]
    // letters a-z -> 0..25 keys at 0x00
    if key.count == 1, let c = key.lowercased().unicodeScalars.first, c.value >= 97, c.value <= 122 {
        return CGKeyCode(c.value - 97) // a=0 ... z=25
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
    // Down with modifiers, then up.
    let down = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: true)
    down?.flags = flags
    down?.post(tap: .cghidEventTap)
    let up = CGEvent(keyboardEventSource: nil, virtualKey: code, keyDown: false)
    up?.flags = flags
    up?.post(tap: .cghidEventTap)
}

func typeText(_ text: String) {
    // Unicode-safe typing via keyboardSetUnicodeString.
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

// ── JSON plumbing ───────────────────────────────────────────────────────────

func jsonStr(_ obj: Any) -> String {
    let data = try? JSONSerialization.data(withJSONObject: obj, options: [])
    return String(data: data ?? Data(), encoding: .utf8) ?? "{}"
}

struct Request { let id: Int; let method: String; let params: [String: Any] }

func emit(_ obj: [String: Any]) {
    let s = jsonStr(obj)
    FileHandle.standardOutput.write((s + "\n").data(using: .utf8)!)
    fflush(stdout)  // stdout is fully buffered when piped; flush per line or the
                    // parent sees nothing until the process exits.
}

func run() {
    // stdout is fully buffered when piped; unbuffer so the parent sees each
    // response immediately. read(0) does a short read (returns available bytes),
    // unlike readData(ofLength:) which can wait for the full count or EOF.
    setvbuf(stdout, nil, _IONBF, 0)
    var buffer = Data()
    while true {
        var chunk = [UInt8](repeating: 0, count: 65536)
        let n = read(0, &chunk, chunk.count)
        if n <= 0 { break } // EOF or error
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
    do {
        switch req.method {
        case "ping":
            respond(true, ["platform": "darwin"], nil)
        case "frontmost":
            let pid = frontmostPid()
            let name = NSWorkspace.shared.frontmostApplication?.localizedName ?? ""
            respond(true, ["pid": pid ?? -1, "app": name], nil)
        case "snapshot":
            let depth = (req.params["depth"] as? Int) ?? 6
            let pid = frontmostPid() ?? -1
            let app = AXUIElementCreateApplication(pid_t(pid))
            let frontName = NSWorkspace.shared.frontmostApplication?.localizedName ?? ""
            // If we cannot even read the app's role, the process has NO
            // Accessibility grant — report a precise, non-ambiguous error so the
            // caller does NOT treat "empty UI" as "permission missing".
            if axString(app, kAXRoleAttribute).isEmpty {
                respond(false, nil, "accessibility_not_trusted")
                return
            }
            var counter = 0
            if let node = walk(app, path: "0", depth: depth, consumedRefs: &counter) {
                let tree = nodeDict(node)
                respond(true, ["frontmost": frontName, "refs": counter, "root": tree], nil)
            } else {
                // Trusted but the frontmost app has no readable interactive
                // elements (e.g. it IS the agent/CoWorker itself, a menu-bar-only
                // app, or a canvas). Return a minimal honest root so the snapshot
                // path does NOT fail-closed on a UI that just isn't AX-rich.
                let minimal: [String: Any] = [
                    "ref": "1", "role": "AXApplication", "label": frontName,
                    "note": "No readable elements in the frontmost app.",
                ]
                respond(true, ["frontmost": frontName, "refs": 1, "root": minimal], nil)
            }
        case "act":
            // act: click_ref/double/right/type_into(press+type)/set_value/focus/scroll/show
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
                    // AX couldn't press; fall back to clicking the element center
                    // via CGEvent (works even for elements without an AXPress action).
                    clickCenter(el)
                    respond(true, ["performed": op, "via": "coords-fallback"], nil)
                }
            case "set_value", "focus":
                if let typed = req.params["value"] as? [String: Any],
                   let text = typed["text"] as? String {
                    AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFTypeRef)
                } else if let text = req.params["value"] as? String {
                    AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFTypeRef)
                }
                if op == "focus" { AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, true as CFTypeRef) }
                respond(true, ["performed": op], nil)
            case "type_into":
                if let text = req.params["text"] as? String {
                    AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFTypeRef)
                    AXUIElementPerformAction(el, kAXConfirmAction as CFString)
                    respond(true, ["performed": "type_into"], nil)
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
            let key = (req.params["key"] as? String) ?? ""
            let mods = (req.params["modifiers"] as? [String]) ?? []
            press(key, mods)
            respond(true, ["performed": "press_hotkey", "key": key, "modifiers": mods], nil)
        case "type_text":
            let text = (req.params["text"] as? String) ?? ""
            typeText(text)
            respond(true, ["performed": "type_text"], nil)
        case "click_coords":
            let x = (req.params["x"] as? Double) ?? 0
            let y = (req.params["y"] as? Double) ?? 0
            let pt = CGPoint(x: x, y: y)
            postMouse(.leftMouseDown, pt); postMouse(.leftMouseUp, pt)
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
    } catch {
        respond(false, nil, "\(error)")
    }
}

func clickCenter(_ el: AXUIElement) {
    guard let pra = axAttr(el, kAXPositionAttribute), let sra = axAttr(el, kAXSizeAttribute) else { return }
    let p = pra as! AXValue
    let s = sra as! AXValue
    var point = CGPoint.zero; var size = CGSize.zero
    if AXValueGetValue(p, .cgPoint, &point), AXValueGetValue(s, .cgSize, &size) {
        let center = CGPoint(x: point.x + size.width / 2, y: point.y + size.height / 2)
        postMouse(.leftMouseDown, center); postMouse(.leftMouseUp, center)
    }
}

func nodeDict(_ n: AXNode) -> [String: Any] {
    var d: [String: Any] = [
        "ref": n.ref, "role": n.role, "label": n.label, "value": n.value,
        "enabled": n.enabled, "position": n.position, "size": n.size,
    ]
    if !n.children.isEmpty {
        d["children"] = n.children.map(nodeDict)
    }
    return d
}

run()
