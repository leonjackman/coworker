//
//  AXTree.swift
//  cw-automa
//
//  Accessibility perception, ported from the original single-file helper and
//  kept behaviour-identical so the TS/Python ref contract does not change:
//    refs are `sig#n` where sig = normalized "role:label/placeholder" and n is
//    that sig's occurrence ordinal — an IDENTITY, not a positional counter.
//  Traversal is scoped to the app's focused window and skips noise (menu bar,
//  scroll bars, zero-size nodes) so refs do not churn as the UI animates.
//
//  Only the Accessibility permission is needed (no Screen Recording).
//

import AppKit
import ApplicationServices
import CoreGraphics
import Foundation

let TREE_DEPTH = 12

enum AX {

    static func attr(_ el: AXUIElement, _ name: String) -> Any? {
        var v: CFTypeRef?
        guard AXUIElementCopyAttributeValue(el, name as CFString, &v) == .success, let value = v else {
            return nil
        }
        return value
    }

    static func string(_ el: AXUIElement, _ name: String) -> String {
        (attr(el, name) as? String) ?? ""
    }

    static func bool(_ el: AXUIElement, _ name: String) -> Bool {
        (attr(el, name) as? Bool) ?? true
    }

    static func size(_ el: AXUIElement, _ name: String) -> CGSize? {
        guard let raw = attr(el, name) else { return nil }
        let v = raw as! AXValue
        var sz = CGSize.zero
        return AXValueGetValue(v, .cgSize, &sz) ? sz : nil
    }

    static func point(_ el: AXUIElement) -> CGPoint? {
        guard let raw = attr(el, kAXPositionAttribute) else { return nil }
        let v = raw as! AXValue
        var p = CGPoint.zero
        return AXValueGetValue(v, .cgPoint, &p) ? p : nil
    }

    static func pointString(_ el: AXUIElement) -> String {
        guard let p = point(el) else { return "" }
        return "(\(Int(p.x)),\(Int(p.y)))"
    }

    static func sizeString(_ el: AXUIElement) -> String {
        guard let s = size(el, kAXSizeAttribute) else { return "" }
        return "\(Int(s.width))x\(Int(s.height))"
    }

    /// A logical top-left point at the center of an element's frame.
    static func center(_ el: AXUIElement) -> CGPoint? {
        guard let p = point(el), let s = size(el, kAXSizeAttribute) else { return nil }
        return CGPoint(x: p.x + s.width / 2, y: p.y + s.height / 2)
    }

    static func displayLabel(_ el: AXUIElement) -> String {
        let title = string(el, kAXTitleAttribute)
        let desc = string(el, kAXDescriptionAttribute)
        let ph = string(el, kAXPlaceholderValueAttribute)
        return title.isEmpty ? (desc.isEmpty ? ph : desc) : title
    }

    static func enabled(_ el: AXUIElement) -> Bool {
        if attr(el, kAXEnabledAttribute) == nil { return true }
        return bool(el, kAXEnabledAttribute)
    }

    private static let interactive: Set<String> = [
        kAXButtonRole, kAXCheckBoxRole, kAXRadioButtonRole, kAXTextFieldRole,
        kAXTextAreaRole, kAXPopUpButtonRole, kAXComboBoxRole, kAXMenuBarItemRole,
        kAXMenuItemRole, kAXStaticTextRole, kAXWindowRole,
    ]
    private static let noise: Set<String> = [kAXMenuBarRole, kAXScrollBarRole, kAXMenuRole]

    private static func normalized(_ s: String) -> String {
        s.split(whereSeparator: { $0.isWhitespace }).joined(separator: " ").lowercased()
    }

    static func sig(_ el: AXUIElement) -> String {
        let role = string(el, kAXRoleAttribute)
        let label = displayLabel(el)
        return normalized(role.isEmpty ? ":" : role + ":" + label)
    }

    private static func isZeroSize(_ el: AXUIElement) -> Bool {
        guard let s = size(el, kAXSizeAttribute) else { return false }
        return s.width <= 1 && s.height <= 1
    }

    static func focusedWindow(_ app: AXUIElement) -> AXUIElement? {
        if let raw = attr(app, kAXFocusedWindowAttribute) { return (raw as! AXUIElement) }
        if let raw = attr(app, kAXWindowsAttribute) {
            return (raw as! [AXUIElement]).first
        }
        return nil
    }

    // MARK: Tree model

    struct Node {
        var ref: String
        var sig: String
        var role: String
        var label: String
        var value: String
        var enabled: Bool
        var position: String
        var size: String
        var children: [Node]
    }

    /// Counting rule shared with `find`: every visited non-noise, non-root node
    /// advances its sig's ordinal, whether or not it is later kept.
    static func build(_ el: AXUIElement, isRoot: Bool, depth: Int,
                      seq: inout [String: Int], refCount: inout Int) -> Node? {
        let role = string(el, kAXRoleAttribute)
        if role.isEmpty { return nil }
        if !isRoot, noise.contains(role) || isZeroSize(el) { return nil }

        let signature = sig(el)
        var ord = 0
        if !isRoot {
            seq[signature, default: 0] += 1
            ord = seq[signature]!
        }

        var children: [Node] = []
        if depth > 0, let rawKids = attr(el, kAXChildrenAttribute) {
            for kid in (rawKids as! [AXUIElement]) {
                if let c = build(kid, isRoot: false, depth: depth - 1, seq: &seq, refCount: &refCount) {
                    children.append(c)
                }
            }
        }

        let keep = isRoot || interactive.contains(role) || !children.isEmpty
        if !keep { return nil }
        refCount += 1
        return Node(
            ref: isRoot ? "window" : "\(signature)#\(ord)",
            sig: signature, role: role, label: displayLabel(el),
            value: string(el, kAXValueAttribute), enabled: enabled(el),
            position: pointString(el), size: sizeString(el), children: children
        )
    }

    static func dict(_ n: Node) -> [String: Any] {
        var d: [String: Any] = [
            "ref": n.ref, "sig": n.sig, "role": n.role, "label": n.label,
            "value": n.value, "enabled": n.enabled,
            "position": n.position, "size": n.size,
        ]
        if !n.children.isEmpty { d["children"] = n.children.map(dict) }
        return d
    }

    // MARK: Act resolution — match `sig#n` against the CURRENT window tree

    static func find(_ app: AXUIElement, ref: String) -> AXUIElement? {
        guard let win = focusedWindow(app) else { return nil }
        let parts = ref.split(separator: "#", maxSplits: 1, omittingEmptySubsequences: false)
        guard parts.count == 2, let want = Int(parts[1]) else { return nil }
        let sigRef = String(parts[0])
        var seq = 0
        var result: AXUIElement?

        func scan(_ el: AXUIElement, depth: Int, isRoot: Bool) -> Bool {
            let role = string(el, kAXRoleAttribute)
            if role.isEmpty { return false }
            let noisy = !isRoot && (noise.contains(role) || isZeroSize(el))
            if !isRoot, !noisy, sigRef == sig(el) {
                seq += 1
                if seq == want { result = el; return true }
            }
            if depth > 0, let rawKids = attr(el, kAXChildrenAttribute) {
                for kid in (rawKids as! [AXUIElement]) {
                    if scan(kid, depth: depth - 1, isRoot: false) { return true }
                }
            }
            return false
        }
        _ = scan(win, depth: TREE_DEPTH, isRoot: true)
        return result
    }

    /// The app's current focused (keyboard) element, if any.
    static func focusedElement(_ app: AXUIElement) -> AXUIElement? {
        var raw: CFTypeRef?
        guard AXUIElementCopyAttributeValue(app, kAXFocusedUIElementAttribute as CFString, &raw) == .success,
              let raw, CFGetTypeID(raw) == AXUIElementGetTypeID()
        else { return nil }
        return unsafeBitCast(raw, to: AXUIElement.self)
    }

    /// Focused element readback (role/label/value) for post-type verification.
    static func focusedInfo(_ app: AXUIElement) -> [String: String]? {
        guard let raw = attr(app, kAXFocusedUIElementAttribute) else { return nil }
        let fe = raw as! AXUIElement
        var info: [String: String] = [:]
        info["role"] = string(fe, kAXRoleAttribute)
        let label = displayLabel(fe)
        if !label.isEmpty { info["label"] = label }
        info["value"] = string(fe, kAXValueAttribute)
        return info
    }

    /// True when this process is a trusted Accessibility client.
    static var trusted: Bool { AXIsProcessTrusted() }

    // MARK: Model-facing text + diff support

    /// Indented, model-facing text: `[ref] role "label" value="..." at (x,y)`.
    static func render(_ n: Node, into lines: inout [String], indent: Int, maxLines: Int) {
        if lines.count >= maxLines { return }
        var line = String(repeating: "  ", count: indent) + "[\(n.ref)] \(n.role)"
        if !n.label.isEmpty { line += " \"\(n.label)\"" }
        if !n.value.isEmpty { line += " value=\"\(n.value)\"" }
        if !n.position.isEmpty { line += " at \(n.position)" }
        lines.append(line)
        for c in n.children { render(c, into: &lines, indent: indent + 1, maxLines: maxLines) }
    }

    /// ref -> node, for diffing a snapshot against the previous one.
    static func flatten(_ n: Node, into map: inout [String: Node]) {
        map[n.ref] = n
        for c in n.children { flatten(c, into: &map) }
    }
}
