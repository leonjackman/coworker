//
//  Geometry.swift
//  cw-automa (CoWorker computer-use helper)
//
//  Display + window geometry, all in LOGICAL top-left global point space — the
//  same space the TS bridge, the JPEG screenshots and the injection layer use.
//  Cocoa's bottom-left screen frames are converted here, once, so nothing else
//  in the helper has to think about the flip.
//

import AppKit
import CoreGraphics
import Foundation

struct DisplayInfo {
    let index: Int
    let id: CGDirectDisplayID
    let bounds: CGRect        // logical, top-left global
    let scale: CGFloat
    let isInternal: Bool
}

enum Geometry {

    /// Primary screen height in points: the pivot for Quartz(top-left) <->
    /// Cocoa(bottom-left) flips.
    static var primaryHeight: CGFloat {
        NSScreen.screens.first?.frame.height ?? CGDisplayBounds(CGMainDisplayID()).height
    }

    /// Every active display, index-stable against `NSScreen.screens` order.
    static func displays() -> [DisplayInfo] {
        NSScreen.screens.enumerated().map { (i, screen) in
            let id = screen.displayID ?? CGMainDisplayID()
            return DisplayInfo(
                index: i,
                id: id,
                bounds: CGDisplayBounds(id),
                scale: screen.backingScaleFactor,
                isInternal: CGDisplayIsBuiltin(id) != 0
            )
        }
    }

    /// The display that contains a logical point, falling back to the primary.
    static func display(containing p: CGPoint) -> DisplayInfo? {
        let all = displays()
        for d in all where d.bounds.contains(p) { return d }
        return all.first
    }

    /// Clamp a logical point into a display's bounds (never the menu bar or an
    /// adjacent screen).
    static func clamp(_ p: CGPoint, into bounds: CGRect) -> CGPoint {
        CGPoint(
            x: min(max(p.x, bounds.minX), bounds.maxX - 1),
            y: min(max(p.y, bounds.minY), bounds.maxY - 1)
        )
    }
}

// MARK: - Window list

struct WindowInfo {
    let id: CGWindowID
    let ownerPID: pid_t
    let bounds: CGRect      // logical top-left global
}

enum WindowList {

    /// On-screen, normal-layer windows, front-to-back (CGWindowList order).
    /// Our own overlay windows are excluded by the caller when needed.
    static func onScreen() -> [WindowInfo] {
        guard let raw = CGWindowListCopyWindowInfo(
            [.optionOnScreenOnly, .excludeDesktopElements],
            kCGNullWindowID
        ) as? [[String: Any]] else { return [] }

        var out: [WindowInfo] = []
        out.reserveCapacity(raw.count)
        for entry in raw {
            guard let num = entry[kCGWindowNumber as String] as? CGWindowID,
                  let pid = entry[kCGWindowOwnerPID as String] as? pid_t,
                  let bdict = entry[kCGWindowBounds as String] as? [String: Any],
                  let rect = CGRect(dictionaryRepresentation: bdict as CFDictionary)
            else { continue }
            // Skip zero-size / off-screen.
            if rect.width < 1 || rect.height < 1 { continue }
            out.append(WindowInfo(id: num, ownerPID: pid, bounds: rect))
        }
        return out
    }

    /// The topmost window owned by `pid` whose bounds contain `point`.
    /// Used to bind a synthetic mouse event to the window it is meant for, so
    /// Chromium/CEF apps (which route by the event's declared window) accept it.
    static func window(at point: CGPoint, pid: pid_t) -> WindowInfo? {
        for win in onScreen() where win.ownerPID == pid && win.bounds.contains(point) {
            return win
        }
        return nil
    }

    /// The topmost window at a point regardless of owner.
    static func topmost(at point: CGPoint, excludingPID: pid_t? = nil) -> WindowInfo? {
        for win in onScreen() {
            if let ex = excludingPID, win.ownerPID == ex { continue }
            if win.bounds.contains(point) { return win }
        }
        return nil
    }
}

// MARK: - NSScreen helpers

extension NSScreen {
    var displayID: CGDirectDisplayID? {
        (deviceDescription[NSDeviceDescriptionKey("NSScreenNumber")] as? NSNumber)
            .map { CGDirectDisplayID($0.uint32Value) }
    }
}
