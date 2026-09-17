//
//  KeyMapping.swift
//  cw-automa
//
//  xdotool-style key sequence -> CGKeyCode + CGEventFlags, plus a reverse
//  UCKeyTranslate lookup so printable keys resolve on the current layout.
//

import Carbon.HIToolbox
import CoreGraphics
import Foundation

enum KeyMapping {

    private static let table: [String: CGKeyCode] = {
        var t: [String: CGKeyCode] = [:]
        func put(_ code: Int, _ names: String...) {
            for n in names { t[n.lowercased()] = CGKeyCode(code) }
        }
        put(kVK_Return, "return", "enter", "\n", "\r")
        put(kVK_Tab, "tab", "\t")
        put(kVK_Space, "space", " ")
        put(kVK_Escape, "escape", "esc")
        put(kVK_Delete, "backspace", "delete", "back_space")
        put(kVK_ForwardDelete, "forwarddelete", "forward_delete", "del")
        put(kVK_UpArrow, "up", "uparrow", "up_arrow")
        put(kVK_DownArrow, "down", "downarrow", "down_arrow")
        put(kVK_LeftArrow, "left", "leftarrow", "left_arrow")
        put(kVK_RightArrow, "right", "rightarrow", "right_arrow")
        put(kVK_Home, "home")
        put(kVK_End, "end")
        put(kVK_PageUp, "prior", "page_up", "pageup")
        put(kVK_PageDown, "next", "page_down", "pagedown")
        put(kVK_CapsLock, "capslock", "caps_lock")
        put(kVK_Help, "help", "insert")
        for i in 0...9 { put(kVK_ANSI_Keypad0 + i, "kp_\(i)", "kp\(i)") }
        put(kVK_ANSI_KeypadDecimal, "kp_decimal")
        put(kVK_ANSI_KeypadPlus, "kp_add", "kp_plus")
        put(kVK_ANSI_KeypadMinus, "kp_subtract", "kp_minus")
        put(kVK_ANSI_KeypadMultiply, "kp_multiply")
        put(kVK_ANSI_KeypadDivide, "kp_divide")
        put(kVK_ANSI_KeypadEnter, "kp_enter")
        put(kVK_ANSI_KeypadClear, "clear", "num_lock")
        let fkeys = [
            kVK_F1, kVK_F2, kVK_F3, kVK_F4, kVK_F5, kVK_F6, kVK_F7, kVK_F8,
            kVK_F9, kVK_F10, kVK_F11, kVK_F12, kVK_F13, kVK_F14, kVK_F15,
            kVK_F16, kVK_F17, kVK_F18, kVK_F19, kVK_F20,
        ]
        for (i, code) in fkeys.enumerated() { put(code, "f\(i + 1)") }
        return t
    }()

    private static let modifierFlag: [String: CGEventFlags] = [
        "cmd": .maskCommand, "command": .maskCommand, "meta": .maskCommand,
        "super": .maskCommand, "win": .maskCommand,
        "ctrl": .maskControl, "control": .maskControl,
        "shift": .maskShift,
        "alt": .maskAlternate, "option": .maskAlternate, "opt": .maskAlternate,
        "fn": .maskSecondaryFn,
    ]

    /// Modifier names -> flags (used by click modifiers). Unknown names ignored.
    static func flags(for modifiers: [String]) -> CGEventFlags {
        var f: CGEventFlags = []
        for m in modifiers {
            if let bit = modifierFlag[m.trimmingCharacters(in: .whitespaces).lowercased()] {
                f.insert(bit)
            }
        }
        return f
    }

    /// True when the token names a modifier rather than a key.
    static func isModifier(_ token: String) -> Bool {
        modifierFlag[token.trimmingCharacters(in: .whitespaces).lowercased()] != nil
    }

    /// Resolve one non-modifier token to a keycode: named table first, then a
    /// reverse map on the current keyboard layout for single printable chars.
    static func keycode(forToken token: String) throws -> CGKeyCode {
        let trimmed = token.trimmingCharacters(in: .whitespaces)
        let lower = trimmed.lowercased()
        if let code = table[lower] { return code }
        if trimmed.count == 1, let ch = trimmed.first, let code = keycode(forCharacter: ch) {
            return code
        }
        if lower.count == 1, let ch = lower.first, let code = keycode(forCharacter: ch) {
            return code
        }
        throw HelperError("unknown_key", "Unsupported key: \(token)")
    }

    /// Parse `"cmd+shift+a"` / `"Return"` / `"super+c"` into (flags, keycode).
    static func parse(_ seq: String) throws -> (CGEventFlags, CGKeyCode) {
        let tokens = seq.split(separator: "+", omittingEmptySubsequences: false)
            .map { $0.trimmingCharacters(in: .whitespaces) }
            .filter { !$0.isEmpty }
        guard let keyToken = tokens.last else {
            throw HelperError("unknown_key", "Empty key sequence")
        }
        var flags: CGEventFlags = []
        for t in tokens.dropLast() {
            guard let bit = modifierFlag[t.lowercased()] else {
                throw HelperError("unknown_key", "Unexpected modifier token: \(t)")
            }
            flags.insert(bit)
        }
        return (flags, try keycode(forToken: keyToken))
    }

    /// Reverse `UCKeyTranslate` over the current layout.
    static func keycode(forCharacter ch: Character) -> CGKeyCode? {
        guard let src = TISCopyCurrentKeyboardLayoutInputSource()?.takeRetainedValue(),
              let ptr = TISGetInputSourceProperty(src, kTISPropertyUnicodeKeyLayoutData)
        else { return nil }
        let data = unsafeBitCast(ptr, to: CFData.self)
        guard let bytes = CFDataGetBytePtr(data) else { return nil }
        let kbType = UInt32(LMGetKbdType())
        let target = String(ch)
        let states: [UInt32] = [
            0,
            UInt32(shiftKey >> 8),
            UInt32(optionKey >> 8),
            UInt32((shiftKey | optionKey) >> 8),
        ]
        let layout = UnsafeRawPointer(bytes).assumingMemoryBound(to: UCKeyboardLayout.self)
        for code in 0..<128 {
            for state in states {
                var deadKeyState: UInt32 = 0
                var chars = [UniChar](repeating: 0, count: 8)
                var length = 0
                let status = UCKeyTranslate(
                    layout, UInt16(code), UInt16(kUCKeyActionDisplay), state,
                    kbType, OptionBits(kUCKeyTranslateNoDeadKeysBit),
                    &deadKeyState, chars.count, &length, &chars
                )
                if status == noErr, length > 0,
                   String(utf16CodeUnits: chars, count: length) == target {
                    return CGKeyCode(code)
                }
            }
        }
        return nil
    }
}
