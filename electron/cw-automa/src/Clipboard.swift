//
//  Clipboard.swift
//  cw-automa
//
//  Clipboard-paste text entry. Semantically app-agnostic and robust for
//  CJK/IME, where per-character synthetic key events are unreliable. The user's
//  prior clipboard is restored afterwards.
//

import AppKit
import Foundation

enum Clipboard {

    static func paste(_ text: String, into target: ProcessTarget?) throws {
        let pb = NSPasteboard.general
        let prior = pb.string(forType: .string)

        pb.clearContents()
        pb.setString(text, forType: .string)

        try Injection.key("cmd+v", repeat: 1)
        usleep(150 * 1000)

        // Restore the user's clipboard so we never leave it dirty.
        pb.clearContents()
        if let prior, !prior.isEmpty {
            pb.setString(prior, forType: .string)
        }
    }

    static func setCurrentText(_ text: String) {
        let pb = NSPasteboard.general
        pb.clearContents()
        if !text.isEmpty { pb.setString(text, forType: .string) }
    }
}
