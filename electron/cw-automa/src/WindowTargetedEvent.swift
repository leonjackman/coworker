//
//  WindowTargetedEvent.swift
//  cw-automa
//
//  Mouse events that declare WHICH window they belong to.
//
//  Why this exists: Chromium/CEF (VS Code, Slack, Discord, Notion, Electron in
//  general) route synthetic input by the window the event claims. A bare CGEvent
//  from CGEventPostToPid claims window 0, so it is silently dropped — the action
//  "succeeds" and nothing happens. Binding the window identity onto the event
//  makes those apps accept it, while native AppKit apps simply re-hit-test and
//  are unaffected.
//
//  Note: this is about the *event's declared window*, never about moving the
//  real cursor. The real system cursor is never warped anywhere in this helper.
//

import AppKit
import CoreGraphics
import Darwin
import Foundation

// MARK: - Private SPI

private typealias CGEventSetWindowLocationFn = @convention(c) (CGEvent, CGPoint) -> Void
private typealias SLPSSetFrontProcessFn = @convention(c) (UnsafeMutableRawPointer, CGWindowID, UInt32) -> Int32
private typealias GetProcessForPIDFn = @convention(c) (pid_t, UnsafeMutableRawPointer) -> Int32

private enum PrivateSPI {
    static let coreGraphics: UnsafeMutableRawPointer? =
        dlopen("/System/Library/Frameworks/CoreGraphics.framework/CoreGraphics", RTLD_NOW)

    static let setWindowLocation: CGEventSetWindowLocationFn? = {
        guard let h = coreGraphics, let s = dlsym(h, "CGEventSetWindowLocation") else { return nil }
        return unsafeBitCast(s, to: CGEventSetWindowLocationFn.self)
    }()

    /// SkyLight front-process switch. RTLD_DEFAULT so we pick it up from the
    /// already-loaded ApplicationServices/HIToolbox image.
    static let setFrontProcess: SLPSSetFrontProcessFn? = {
        let rtldDefault = UnsafeMutableRawPointer(bitPattern: -2)
        guard let s = dlsym(rtldDefault, "SLPSSetFrontProcessWithOptions") else { return nil }
        return unsafeBitCast(s, to: SLPSSetFrontProcessFn.self)
    }()

    static let getProcessForPID: GetProcessForPIDFn? = {
        let rtldDefault = UnsafeMutableRawPointer(bitPattern: -2)
        guard let s = dlsym(rtldDefault, "GetProcessForPID") else { return nil }
        return unsafeBitCast(s, to: GetProcessForPIDFn.self)
    }()
}

enum WindowTargetedEvent {

    private static var eventCounter: UInt32 = 1

    /// Map a CGEventType to the NSEvent type AppKit needs to vend a
    /// window-numbered event. Only mouse types have a window-bound form.
    static func nsType(for type: CGEventType) -> NSEvent.EventType? {
        switch type {
        case .leftMouseDown: return .leftMouseDown
        case .leftMouseUp: return .leftMouseUp
        case .rightMouseDown: return .rightMouseDown
        case .rightMouseUp: return .rightMouseUp
        case .otherMouseDown: return .otherMouseDown
        case .otherMouseUp: return .otherMouseUp
        case .mouseMoved: return .mouseMoved
        case .leftMouseDragged: return .leftMouseDragged
        case .rightMouseDragged: return .rightMouseDragged
        case .otherMouseDragged: return .otherMouseDragged
        default: return nil
        }
    }

    /// Build a window-bound mouse event. Returns nil when AppKit will not vend
    /// one, so the caller can fall back to a plain (pre-window-identity) event.
    static func makeMouse(
        type: CGEventType,
        point: CGPoint,
        button: MouseButton,
        clickCount: Int,
        window: WindowInfo
    ) -> CGEvent? {
        guard let nsType = nsType(for: type) else { return nil }
        let flags: NSEvent.ModifierFlags = []
        guard let ns = NSEvent.mouseEvent(
            with: nsType,
            location: point,             // global logical point
            modifierFlags: flags,
            timestamp: 0,                 // real timestamp stamped right before post
            windowNumber: Int(window.id), // <- the routing key
            context: nil,
            eventNumber: Int(WindowTargetedEvent.nextEventNumber()),
            clickCount: max(1, clickCount),
            pressure: 1.0
        ) else { return nil }
        guard let cg = ns.cgEvent else { return nil }

        // Window hit fields (private CGEventFields 91/92): "window under the
        // pointer" and "window that can handle this event".
        if let f1 = CGEventField(rawValue: 91), let f2 = CGEventField(rawValue: 92) {
            cg.setIntegerValueField(f1, value: Int64(window.id))
            cg.setIntegerValueField(f2, value: Int64(window.id))
        }
        // Window-local location, top-left origin, no flip (private SPI).
        if let setLoc = PrivateSPI.setWindowLocation {
            setLoc(cg, CGPoint(x: point.x - window.bounds.minX, y: point.y - window.bounds.minY))
        }
        return cg
    }

    /// Post an event to a specific process, stamping a fresh timestamp.
    static func post(_ event: CGEvent, to pid: pid_t) {
        event.timestamp = DispatchTime.now().uptimeNanoseconds
        event.postToPid(pid)
    }

    /// Bring a specific window of another process to the front so a
    /// background first click is not swallowed as the activation click.
    /// Returns false when the private API is unavailable (caller still tries).
    @discardableResult
    static func focusWindow(pid: pid_t, windowID: CGWindowID) -> Bool {
        guard let setFront = PrivateSPI.setFrontProcess,
              let getPSN = PrivateSPI.getProcessForPID
        else { return false }
        var psn = [UInt8](repeating: 0, count: 16)   // ProcessSerialNumber
        var ok = false
        psn.withUnsafeMutableBytes { raw in
            guard let ptr = raw.baseAddress, getPSN(pid, ptr) == 0 else { return }
            ok = setFront(ptr, windowID, 0x2) == 0
        }
        return ok
    }

    private static func nextEventNumber() -> UInt32 {
        eventCounter &+= 1
        if eventCounter == 0 { eventCounter = 1 }
        return eventCounter
    }
}
