//
//  main.swift
//  cw-automa
//
//  Resident macOS computer-use helper for CoWorker. Newline-delimited JSON over
//  stdin/stdout; the process runs an AppKit run loop (accessory) so it can host
//  the VirtualCursor overlay while serving commands.
//
//    in : {"id":N,"method":"...","params":{...}}
//    out: {"id":N,"ok":true,"result":{...}} | {"id":N,"ok":false,"error":"..."}
//
//  All input is delivered with CGEvent.postToPid (see Injection.swift); the real
//  OS cursor is never moved. The blue virtual cursor is the agent's pointer.
//

import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

// Commands are serialized on this queue; AppKit/window work is hopped to main.
let controlQueue = DispatchQueue(label: "cw-automa.control")

/// Last logical point any pointer action targeted — the anchor for a scroll
/// that carries no explicit point. Serialized on `controlQueue`.
var lastPointerPoint: CGPoint = {
    if let d = Geometry.displays().first {
        return CGPoint(x: d.bounds.midX, y: d.bounds.midY)
    }
    return .zero
}()

func frontmostPid() -> pid_t? {
    NSWorkspace.shared.frontmostApplication?.processIdentifier
}

/// Launch an app by name. Wraps the deprecated `launchApplication` so the
/// deprecation warning is localized to one call site.
private func launchAppByName(_ name: String) -> Bool {
    // swift:disable:next DeprecatedDeclaration
    return NSWorkspace.shared.launchApplication(name)
}

/// Re-activate the target app if something (e.g. Dock) stole frontmost.
/// Used as a post-action recovery so the next observe/action sees the right app.
private func ensureAppFrontmost(_ pid: pid_t) {
    guard NSWorkspace.shared.frontmostApplication?.processIdentifier != pid else { return }
    NSRunningApplication(processIdentifier: pid)?.activate()
}

// MARK: - Virtual cursor bridging (main-actor owned)

func cursorShow(targetPid: pid_t?) {
    DispatchQueue.main.async { VirtualCursor.shared.show(targetPid: targetPid) }
}

func cursorMove(_ p: CGPoint, targetPid: pid_t?) {
    lastPointerPoint = p
    DispatchQueue.main.async {
        VirtualCursor.shared.show(targetPid: targetPid)
        VirtualCursor.shared.move(to: p, targetPid: targetPid, animated: false)
        StatusHUD.shared.showActive()
    }
}

func hudPulse() {
    DispatchQueue.main.async { StatusHUD.shared.showActive() }
}

func cursorClick(_ p: CGPoint, kind: VirtualCursor.ClickKind) {
    DispatchQueue.main.async { VirtualCursor.shared.click(at: p, kind: kind) }
}

func cursorHide() {
    DispatchQueue.main.async { VirtualCursor.shared.hide() }
}

// MARK: - Command handling

func handleRequest(_ req: Request) {
    let p = req.params
    do {
        switch req.method {

        case "ping":
            Responder.ok(req.id, ["platform": "darwin", "version": 2])

        case "frontmost":
            Responder.ok(req.id, [
                "pid": frontmostPid() ?? -1,
                "app": NSWorkspace.shared.frontmostApplication?.localizedName ?? "",
            ])

        case "displays":
            let list = Geometry.displays().map { d -> [String: Any] in
                ["index": d.index, "id": Int(d.id),
                 "bounds": ["x": d.bounds.minX, "y": d.bounds.minY,
                            "width": d.bounds.width, "height": d.bounds.height],
                 "scale_factor": d.scale, "internal": d.isInternal]
            }
            Responder.ok(req.id, ["displays": list])

        case "snapshot", "get_app_state":
            try handleSnapshot(req.id, p)

        case "act":
            try handleAct(req.id, p)

        case "click_coords":
            let pt = CGPoint(x: p.dbl("x"), y: p.dbl("y"))
            let target = try Injection.resolveCoordinateTarget(at: pt)
            cursorMove(pt, targetPid: target.pid)
            try Injection.click(x: pt.x, y: pt.y, button: .left, count: 1, modifiers: [])
            cursorClick(pt, kind: .single)
            ensureAppFrontmost(target.pid)
            Responder.ok(req.id, ["performed": "click_coords"])

        case "press_hotkey":
            let key = p.str("key")
            let mods = p.strArray("modifiers")
            let modifierOnly: Set<String> = ["cmd", "command", "leftcmd", "ctrl", "control",
                                             "alt", "option", "shift", "super", "meta"]
            if key.isEmpty || modifierOnly.contains(key.lowercased()) {
                throw HelperError("param_error",
                    "press_hotkey needs a real key, not a bare modifier (\(key.isEmpty ? "empty key" : key)). Example: key:\"space\", modifiers:[\"cmd\"]")
            }
            if (try? KeyMapping.keycode(forToken: key)) == nil {
                throw HelperError("param_error", "unknown key \"\(key)\"")
            }
            try Injection.key(mods.isEmpty ? key : mods.joined(separator: "+") + "+" + key, repeat: 1)
            cursorShow(targetPid: Injection.lastTargetPid)
            hudPulse()
            if let pid = Injection.lastTargetPid { ensureAppFrontmost(pid) }
            Responder.ok(req.id, ["performed": "press_hotkey"])

        case "type_text":
            // Paste into the CURRENTLY focused field; caller ensures focus.
            try Clipboard.paste(p.str("text"), into: Injection.lastTargetPid.flatMap(ProcessTarget.resolve))
            cursorShow(targetPid: Injection.lastTargetPid)
            hudPulse()
            if let pid = Injection.lastTargetPid { ensureAppFrontmost(pid) }
            Responder.ok(req.id, ["performed": "type_text"])

        case "scroll":
            let dx = Int(p.dbl("dx"))
            let dy = Int(p.dbl("dy"))
            try Injection.scroll(x: lastPointerPoint.x, y: lastPointerPoint.y, dx: dx, dy: dy)
            Responder.ok(req.id, ["performed": "scroll"])

        case "launch":
            let app = p.str("app")
            if app.isEmpty { throw HelperError("param_error", "launch requires app") }
            var ok = false
            if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: app) {
                NSWorkspace.shared.open(url, configuration: NSWorkspace.OpenConfiguration())
                ok = true
            } else {
                // Fallback: try as app name via open -a (deprecated but still works).
                ok = launchAppByName(app)
            }
            if ok {
                cursorShow(targetPid: Injection.lastTargetPid)
                hudPulse()
                // Brief settle then re-activate to beat the Dock out.
                DispatchQueue.global().asyncAfter(deadline: .now() + 0.5) {
                    let apps = NSWorkspace.shared.runningApplications
                    for a in apps {
                        if let name = a.localizedName, name.lowercased().contains(app.lowercased()) {
                            a.activate()
                            break
                        }
                    }
                }
                Responder.ok(req.id, ["launched": app])
            } else {
                throw HelperError("launch_failed", "open -a failed for \(app)")
            }

        case "click_point":
            let pt = CGPoint(x: p.dbl("x"), y: p.dbl("y"))
            let kind = p.str("kind", "left")
            let button: MouseButton = kind == "right" ? .right : .left
            let count = kind == "double" ? 2 : 1
            let target = try Injection.resolveCoordinateTarget(at: pt)
            cursorMove(pt, targetPid: target.pid)
            try Injection.click(x: pt.x, y: pt.y, button: button, count: count, modifiers: [])
            cursorClick(pt, kind: kind == "double" ? .doubleClick : (kind == "right" ? .rightClick : .single))
            Responder.ok(req.id, ["performed": "click_point"])

        case "drag_point":
            let from = CGPoint(x: p.dbl("x1"), y: p.dbl("y1"))
            let to = CGPoint(x: p.dbl("x2"), y: p.dbl("y2"))
            cursorMove(from, targetPid: nil)
            try Injection.drag(from: from, to: to, button: MouseButton.parse(p.str("button", "left")), steps: 24)
            cursorMove(to, targetPid: nil)
            Responder.ok(req.id, ["performed": "drag_point"])

        case "cursor_move":
            cursorMove(CGPoint(x: p.dbl("x"), y: p.dbl("y")), targetPid: nil)
            Responder.ok(req.id, ["performed": "cursor_move"])

        case "cursor_show":
            cursorShow(targetPid: frontmostPid())
            Responder.ok(req.id, ["shown": true])

        case "cursor_hide", "cursor_park":
            cursorHide()
            Responder.ok(req.id, ["shown": false])

        case "cursor_debug":
            DispatchQueue.main.async {
                Responder.ok(req.id, VirtualCursor.shared.debugInfo())
            }

        case "cursor_demo":
            let seconds = p.dbl("seconds", 3)
            DispatchQueue.main.async {
                VirtualCursor.shared.beginDemo(seconds: seconds)
                if let d = Geometry.displays().first {
                    let c = CGPoint(x: d.bounds.midX, y: d.bounds.midY)
                    VirtualCursor.shared.move(to: c, targetPid: nil, animated: true)
                    VirtualCursor.shared.click(at: c, kind: .single)
                }
                StatusHUD.shared.showActive()
            }
            Responder.ok(req.id, ["demo": true])

        case "hud_show":
            hudPulse()
            Responder.ok(req.id, ["shown": true])

        case "hud_pause":
            let value = p.bool("paused", true)
            DispatchQueue.main.async { StatusHUD.shared.setPaused(value) }
            Responder.ok(req.id, ["paused": value])

        case "hud_hide":
            DispatchQueue.main.async { StatusHUD.shared.hide() }
            Responder.ok(req.id, ["shown": false])

        case "set_stop_label":
            let label = p.str("label")
            DispatchQueue.main.async { StatusHUD.shared.setLabel(label) }
            Responder.ok(req.id, ["label": label])

        case "permissions":
            Responder.ok(req.id, Permissions.status())

        case "cursor_position":
            // Real OS cursor (CGEvent location) vs the virtual cursor. Used by
            // the invariance test: the real cursor must never move.
            let real = CGEvent(source: nil)?.location ?? .zero
            let virt = DispatchQueue.main.sync { VirtualCursor.shared.current }
            Responder.ok(req.id, [
                "real": ["x": real.x, "y": real.y],
                "virtual": ["x": virt.x, "y": virt.y],
            ])

        case "permissions_request":
            let kind = p.str("kind", "accessibility")
            var result: [String: Any] = ["kind": kind]
            if kind == "screen" {
                result["granted"] = Permissions.requestScreen()
                result["status"] = Permissions.screenGranted
            } else {
                result["granted"] = Permissions.requestAccessibility()
                result["status"] = Permissions.accessibilityTrusted
            }
            Responder.ok(req.id, result)

        default:
            throw HelperError("unknown_method", "unknown method \(req.method)")
        }
    } catch let e as HelperError {
        Responder.fail(req.id, "\(e.code): \(e.message)", code: e.code)
    } catch {
        Responder.fail(req.id, "\(error)")
    }
}

// MARK: - Snapshot / get_app_state

/// Previous AX snapshot per (pid, window title), for diffing.
private struct SnapshotEntry {
    var refs: Set<String>
    var value: [String: String]
}
private var lastSnapshots: [String: SnapshotEntry] = [:]
private let snapshotLock = NSLock()

/// Resolve a target pid from an app name / bundle id / numeric pid.
/// Empty -> the frontmost app.
private func resolveAppPid(_ appName: String) -> pid_t? {
    if appName.isEmpty { return frontmostPid() }
    if let n = Int(appName), n > 0 { return pid_t(n) }
    let apps = NSWorkspace.shared.runningApplications
    if let a = apps.first(where: { $0.bundleIdentifier == appName || $0.localizedName == appName }) {
        return a.processIdentifier
    }
    if let a = apps.first(where: {
        ($0.localizedName ?? "").caseInsensitiveCompare(appName) == .orderedSame
    }) {
        return a.processIdentifier
    }
    return nil
}

/// The single perception primitive: key-window AX tree (semantic refs) + the
/// window's title/frame + an incremental diff against the previous read. The
/// nested `root` node shape is unchanged so existing callers keep working;
/// `text` is the ready-to-read indented form.
private func handleSnapshot(_ id: Int, _ p: [String: Any]) throws {
    guard AX.trusted else {
        throw HelperError("accessibility_not_trusted", "accessibility_not_trusted")
    }
    let depth = p.int("depth", TREE_DEPTH)
    let requestedApp = p.str("app")
    guard let pid = resolveAppPid(requestedApp) else {
        throw HelperError("no_target", "No running application matches '\(requestedApp)'")
    }
    let app = AXUIElementCreateApplication(pid_t(pid))
    let appName = NSRunningApplication(processIdentifier: pid_t(pid))?.localizedName ?? requestedApp
    let frontName = NSWorkspace.shared.frontmostApplication?.localizedName ?? appName

    if AX.string(app, kAXRoleAttribute).isEmpty {
        throw HelperError("accessibility_not_trusted", "accessibility_not_trusted")
    }
    guard let win = AX.focusedWindow(app) else {
        Responder.ok(id, [
            "frontmost": frontName, "app": appName, "pid": pid, "refs": 0, "text": "",
            "root": ["ref": "window", "role": "AXApplication", "label": appName,
                     "note": "No readable window in this app."],
        ])
        return
    }
    let title = AX.string(win, kAXTitleAttribute)
    var windowInfo: [String: Any] = ["title": title]
    if let pt = AX.point(win), let sz = AX.size(win, kAXSizeAttribute) {
        windowInfo["frame"] = ["x": pt.x, "y": pt.y, "width": sz.width, "height": sz.height]
    }

    var seq: [String: Int] = [:]
    var refCount = 0
    guard let root = AX.build(win, isRoot: true, depth: depth, seq: &seq, refCount: &refCount) else {
        Responder.ok(id, [
            "frontmost": frontName, "app": appName, "pid": pid, "refs": 0, "text": "",
            "window": windowInfo,
            "root": ["ref": "window", "role": "AXWindow", "label": appName,
                     "note": "No readable elements in the focused window."],
        ])
        return
    }

    var lines: [String] = []
    AX.render(root, into: &lines, indent: 0, maxLines: p.int("max_lines", 400))
    let text = lines.joined(separator: "\n")

    var map: [String: AX.Node] = [:]
    AX.flatten(root, into: &map)
    var values: [String: String] = [:]
    for (ref, node) in map { values[ref] = node.label + "\u{1}" + node.value }

    let disableDiff = p.bool("disableDiff", false) || p.bool("disable_diff", false)
    let key = "\(pid):\(title)"
    var changed = true
    var removed: [String] = []
    snapshotLock.lock()
    if !disableDiff, let prev = lastSnapshots[key] {
        let current = Set(map.keys)
        removed = prev.refs.subtracting(current).sorted()
        changed = !removed.isEmpty
        if !changed {
            for (ref, val) in values where prev.value[ref] != val { changed = true; break }
        }
    }
    lastSnapshots[key] = SnapshotEntry(refs: Set(map.keys), value: values)
    snapshotLock.unlock()

    var result: [String: Any] = [
        "frontmost": frontName, "app": appName, "pid": pid,
        "refs": refCount, "root": AX.dict(root), "text": text,
        "window": windowInfo, "changed": disableDiff ? true : changed,
        "diff": !disableDiff,
    ]
    if !removed.isEmpty { result["removed"] = removed }
    Responder.ok(id, result)
}

// MARK: - Act (semantic, by ref)

private func handleAct(_ id: Int, _ p: [String: Any]) throws {
    let pid = pid_t(p.int("pid", Int(frontmostPid() ?? -1)))
    let app = AXUIElementCreateApplication(pid)
    let ref = p.str("ref")
    let op = p.str("op", "click")
    guard let el = AX.find(app, ref: ref) else {
        throw HelperError("computer_error", "no AX element for ref \(ref)")
    }
    let center = AX.center(el)

    switch op {
    case "click", "double", "right":
        let kind: VirtualCursor.ClickKind = op == "double" ? .doubleClick : (op == "right" ? .rightClick : .single)
        if let c = center { cursorMove(c, targetPid: pid) } else { cursorShow(targetPid: pid) }
        let axResult = AXUIElementPerformAction(el, kAXPressAction as CFString)
        if axResult == .success {
            if let c = center { cursorClick(c, kind: kind) }
            ensureAppFrontmost(pid)
            Responder.ok(id, ["performed": op])
        } else if let c = center {
            // AX press unavailable (canvas/rendered control) — non-blocking click.
            let button: MouseButton = op == "right" ? .right : .left
            try Injection.click(x: c.x, y: c.y, button: button, count: op == "double" ? 2 : 1, modifiers: [])
            cursorClick(c, kind: kind)
            ensureAppFrontmost(pid)
            Responder.ok(id, ["performed": op, "via": "coords-fallback"])
        } else {
            throw HelperError("computer_error", "element has no frame for coordinate fallback")
        }

    case "set_value":
        if let text = p["value"] as? String {
            AXUIElementSetAttributeValue(el, kAXValueAttribute as CFString, text as CFTypeRef)
        }
        ensureAppFrontmost(pid)
        Responder.ok(id, ["performed": op])

    case "focus":
        AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, true as CFTypeRef)
        ensureAppFrontmost(pid)
        Responder.ok(id, ["performed": op])

    case "type_into":
        guard let text = p["text"] as? String else {
            throw HelperError("param_error", "type_into requires text")
        }
        let submit = p.bool("submit", false)
        if let c = center { cursorMove(c, targetPid: pid) } else { cursorShow(targetPid: pid) }
        try realTypeInto(pid: pid, app: app, el: el, text: text, submit: submit)
        var result: [String: Any] = ["performed": "type_into", "submit": submit]
        if let focus = AX.focusedInfo(app) { result["focused"] = focus }
        ensureAppFrontmost(pid)
        Responder.ok(id, result)

    case "show":
        AXUIElementPerformAction(el, kAXRaiseAction as CFString)
        ensureAppFrontmost(pid)
        Responder.ok(id, ["performed": "show"])

    default:
        throw HelperError("computer_error", "unsupported op \(op)")
    }
}

/// Real editing session: activate → focus → click for a caret → select all →
/// paste → optional Return. All input uses postToPid (no cursor hijack).
private func realTypeInto(pid: pid_t, app: AXUIElement, el: AXUIElement, text: String, submit: Bool) throws {
    NSRunningApplication(processIdentifier: pid)?.activate()
    usleep(160 * 1000)
    AXUIElementSetAttributeValue(el, kAXFocusedAttribute as CFString, true as CFTypeRef)
    usleep(80 * 1000)
    if let c = AX.center(el) {
        try Injection.click(x: c.x, y: c.y, button: .left, count: 1, modifiers: [])
        cursorClick(c, kind: .single)
    }
    usleep(140 * 1000)
    try Injection.key("cmd+a", repeat: 1)
    usleep(60 * 1000)
    try Clipboard.paste(text, into: ProcessTarget.resolve(pid: pid))
    if submit {
        try Injection.key("return", repeat: 1)
        usleep(80 * 1000)
    }
}

// MARK: - Resident entry

@MainActor
final class AppDelegate: NSObject, NSApplicationDelegate {

    func applicationDidFinishLaunching(_ notification: Notification) {
        VirtualCursor.shared.preload()
        startStdinReader()
    }

    func applicationWillTerminate(_ notification: Notification) {
        Injection.releaseAllHeld()
    }

    private func startStdinReader() {
        let thread = Thread { readLoop() }
        thread.stackSize = 1 << 20
        thread.start()
    }
}

/// Blocking read loop on a background thread; each line is dispatched to the
/// serial control queue so command handling never blocks the main run loop.
func readLoop() {
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
            let text = String(data: line, encoding: .utf8) ?? ""
            guard let req = parseRequest(text) else { continue }
            controlQueue.async { handleRequest(req) }
        }
    }
    // stdin closed (host quit) — drain any queued commands first, then tear
    // down held input and exit. Enqueuing on the control queue (not main)
    // guarantees this runs AFTER every already-dispatched command.
    controlQueue.async {
        DispatchQueue.main.async {
            Injection.releaseAllHeld()
            NSApplication.shared.terminate(nil)
        }
    }
}

let appDelegate = MainActor.assumeIsolated { AppDelegate() }
NSApplication.shared.delegate = appDelegate
NSApplication.shared.setActivationPolicy(.accessory)
NSApplication.shared.run()
