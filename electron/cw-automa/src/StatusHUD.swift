//
//  StatusHUD.swift
//  cw-automa
//
//  A small, click-through status pill ("CoWorker is operating · <stop shortcut>
//  to pause") that replaces the old full-display veil's corner pill. It is
//  deliberately tiny and non-interactive: it never blocks the user, and pausing
//  stays on the global shortcut / tray, not on clicking the pill.
//

import AppKit
import Foundation
import QuartzCore

@MainActor
final class StatusHUD {

    static let shared = StatusHUD()

    private var window: NSWindow?
    private var titleField: NSTextField?
    private var bodyField: NSTextField?
    private var background: NSView?
    private var hideTimer: Timer?

    private var stopLabel = "⌘ + ⇧ Esc"
    private var paused = false
    private var isShown = false

    private let pillHeight: CGFloat = 34
    private let hPad: CGFloat = 16

    private init() {}

    func setLabel(_ label: String) {
        if !label.isEmpty { stopLabel = label }
        if isShown { renderAndShow() }
    }

    /// Show the "operating" pill and auto-hide after a short idle so it never
    /// lingers between agent turns.
    func showActive() {
        paused = false
        renderAndShow()
        scheduleHide(after: 4)
    }

    /// Pause switches to a persistent red pill (no auto-hide) so the user sees
    /// why the agent stopped.
    func setPaused(_ value: Bool) {
        paused = value
        hideTimer?.invalidate(); hideTimer = nil
        renderAndShow()
        if !value { scheduleHide(after: 4) }
    }

    func hide() {
        hideTimer?.invalidate(); hideTimer = nil
        isShown = false
        window?.orderOut(nil)
    }

    // MARK: - Internals

    private func scheduleHide(after seconds: TimeInterval) {
        hideTimer?.invalidate()
        hideTimer = Timer(timeInterval: seconds, repeats: false) { [weak self] _ in
            MainActor.assumeIsolated { self?.hide() }
        }
        RunLoop.main.add(hideTimer!, forMode: .common)
    }

    private func renderAndShow() {
        buildIfNeeded()
        guard let window, let background, let titleField, let bodyField else { return }

        titleField.stringValue = paused ? "已暫停" : "CoWorker 正在操作這台電腦"
        bodyField.stringValue = paused ? "\(stopLabel) 或選單列可恢復" : "\(stopLabel) 可暫停"

        let titleSize = titleField.intrinsicContentSize
        let bodySize = bodyField.intrinsicContentSize
        let width = max(220, hPad * 2 + titleSize.width + 8 + bodySize.width)
        let origin = pillOrigin(width: width)

        titleField.frame = NSRect(x: hPad, y: (pillHeight - titleSize.height) / 2,
                                  width: titleSize.width, height: titleSize.height)
        bodyField.frame = NSRect(x: hPad + titleSize.width + 8,
                                 y: (pillHeight - bodySize.height) / 2,
                                 width: bodySize.width, height: bodySize.height)
        background.frame = NSRect(x: 0, y: 0, width: width, height: pillHeight)
        background.layer?.cornerRadius = pillHeight / 2
        background.layer?.borderWidth = 1
        background.layer?.borderColor = (paused
            ? NSColor.systemRed.withAlphaComponent(0.65)
            : NSColor(srgbRed: 0.31, green: 0.55, blue: 1.0, alpha: 0.55)).cgColor
        background.layer?.backgroundColor = (paused
            ? NSColor(calibratedRed: 0.25, green: 0.09, blue: 0.09, alpha: 0.88)
            : NSColor(calibratedRed: 0.08, green: 0.10, blue: 0.14, alpha: 0.82)).cgColor
        titleField.textColor = paused ? NSColor(calibratedRed: 1, green: 0.85, blue: 0.85, alpha: 1) : .white

        window.setFrame(NSRect(x: origin.x, y: origin.y, width: width, height: pillHeight), display: false)
        window.orderFrontRegardless()
        isShown = true
    }

    /// Top-center of the main screen, in Cocoa (bottom-left) coordinates.
    private func pillOrigin(width: CGFloat) -> CGPoint {
        let screen = NSScreen.main ?? NSScreen.screens.first
        guard let frame = screen?.frame else { return CGPoint(x: 0, y: 0) }
        let x = frame.minX + (frame.width - width) / 2
        let y = frame.maxY - pillHeight - 10
        return CGPoint(x: x, y: y)
    }

    private func buildIfNeeded() {
        guard window == nil else { return }
        let w = NSWindow(
            contentRect: NSRect(x: 0, y: 0, width: 320, height: pillHeight),
            styleMask: .borderless, backing: .buffered, defer: false
        )
        w.isOpaque = false
        w.backgroundColor = .clear
        w.hasShadow = false
        w.ignoresMouseEvents = true
        w.level = NSWindow.Level(rawValue: Int(CGShieldingWindowLevel()))
        w.collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]
        w.isExcludedFromWindowsMenu = true
        w.isReleasedWhenClosed = false
        w.animationBehavior = .none
        w.hidesOnDeactivate = false

        let container = NSView(frame: NSRect(x: 0, y: 0, width: 320, height: pillHeight))
        container.wantsLayer = true

        let bg = NSView(frame: container.bounds)
        bg.wantsLayer = true
        bg.layer?.masksToBounds = true
        container.addSubview(bg)

        let title = makeField(bold: true)
        let body = makeField(bold: false)
        container.addSubview(title)
        container.addSubview(body)

        w.contentView = container
        window = w
        titleField = title
        bodyField = body
        background = bg
    }

    private func makeField(bold: Bool) -> NSTextField {
        let f = NSTextField(labelWithString: "")
        f.font = NSFont.systemFont(ofSize: 13, weight: bold ? .semibold : .regular)
        f.textColor = .white
        f.backgroundColor = .clear
        f.isBezeled = false
        f.isEditable = false
        f.isSelectable = false
        f.drawsBackground = false
        return f
    }
}
