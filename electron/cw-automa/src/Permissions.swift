//
//  Permissions.swift
//  cw-automa
//
//  Native TCC status + prompt for the helper process. This matters because the
//  helper posts the synthetic input, so macOS attributes Accessibility to THIS
//  binary — the prompt must be raised by the helper, not by the Electron host.
//

import ApplicationServices
import CoreGraphics
import Foundation

enum Permissions {

    /// Accessibility (input) trusted for this helper process.
    static var accessibilityTrusted: Bool { AXIsProcessTrusted() }

    /// Screen Recording preflight (no capture is started).
    static var screenGranted: Bool { CGPreflightScreenCaptureAccess() }

    static func status() -> [String: Any] {
        ["accessibility": accessibilityTrusted, "screen": screenGranted]
    }

    /// Raise the macOS Accessibility consent prompt / open the pane (once).
    @discardableResult
    static func requestAccessibility() -> Bool {
        let opts = [kAXTrustedCheckOptionPrompt.takeUnretainedValue() as String: true] as CFDictionary
        return AXIsProcessTrustedWithOptions(opts)
    }

    /// Raise the Screen Recording consent prompt (once).
    @discardableResult
    static func requestScreen() -> Bool {
        CGRequestScreenCaptureAccess()
    }
}
