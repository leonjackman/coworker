//
//  VirtualCursor.swift
//  cw-automa
//
//  The blue virtual cursor the agent drives. This is the ONLY cursor that moves
//  when the AI operates the machine — the real OS cursor is never touched (see
//  Injection.swift). It is pure visual feedback: one borderless, transparent,
//  click-through, non-key window per screen at the shielding level, hosting a
//  CALayer the main run loop glides, plus a click ripple.
//
//  This replaces the previous whole-display "veil" overlay that made the user's
//  screen feel hijacked. The user keeps their own mouse and keeps seeing it.
//

import AppKit
import Foundation
import QuartzCore

@MainActor
final class VirtualCursor {

    static let shared = VirtualCursor()

    private var windows: [CGDirectDisplayID: CursorWindow] = [:]
    private var visible = false
    private var position: CGPoint = CGPoint(x: 0, y: 0)
    private var targetPid: pid_t?

    private var glideTimer: Timer?
    private var glideFrom: CGPoint = .zero
    private var glideTo: CGPoint = .zero
    private var glideStart: CFTimeInterval = 0
    private var glideDuration: CFTimeInterval = 0.35

    private var ripples: [CALayer] = []

    private var didObserve = false
    private var didPreload = false

    private init() {}

    // MARK: Lifecycle

    func preload() {
        guard !didPreload else { rebuildScreens(); return }
        didPreload = true
        if position == .zero, let main = Geometry.displays().first {
            position = CGPoint(x: main.bounds.midX, y: main.bounds.midY)
        }
        rebuildScreens()
        observeAppSwitches()
    }

    func show(targetPid: pid_t? = nil) {
        preload()
        if let targetPid { self.targetPid = targetPid }
        visible = true
        applyVisibility()
    }

    func hide() {
        visible = false
        stopGlide()
        for w in windows.values { w.orderOut(nil) }
        clearRipples()
    }

    func park() { hide() }

    /// The virtual cursor's current logical point (for the invariance probe).
    var current: CGPoint { position }

    // MARK: Motion

    /// Glide the virtual cursor to a logical point. Non-blocking: the caller is
    /// never made to wait, and the real OS cursor never moves.
    func move(to p: CGPoint, targetPid: pid_t? = nil, animated: Bool = true) {
        preload()
        if let targetPid { self.targetPid = targetPid }
        let dst = sanitized(p)
        if !visible { position = dst; place(animated: false); return }
        // A click comes right after — don't make the user watch a slow glide.
        if !animated || distance(position, dst) < 1 {
            position = dst; stopGlide(); place(animated: false); return
        }
        stopGlide()
        glideFrom = position
        glideTo = dst
        glideStart = CACurrentMediaTime()
        glideDuration = min(0.42, max(0.14, distance(position, dst) / 2600))
        let t = Timer(timeInterval: 1.0 / 60.0, repeats: true) { [weak self] _ in
            MainActor.assumeIsolated { self?.stepGlide() }
        }
        RunLoop.main.add(t, forMode: .common)
        glideTimer = t
    }

    /// Show click feedback at a logical point. Posts no input events.
    func click(at p: CGPoint?, kind: ClickKind = .single) {
        guard visible else { return }
        let logical = sanitized(p ?? position)
        guard let (win, view) = windowAndView(containing: logical) else { return }
        let local = view.convertFromLogical(logical, in: win)
        pulse(view.cursorLayer)
        let ring = makeRipple(at: local, kind: kind)
        view.layer?.addSublayer(ring.layer)
        ripples.append(ring.layer)
        let ns = UInt64((ring.duration + 0.05) * 1_000_000_000)
        DispatchQueue.main.asyncAfter(deadline: .now() + Double(ns) / 1_000_000_000) { [weak self] in
            ring.layer.removeFromSuperlayer()
            self?.ripples.removeAll { $0 === ring.layer }
        }
    }

    enum ClickKind { case single, doubleClick, rightClick }

    // MARK: Glide internals

    private func stepGlide() {
        let elapsed = CACurrentMediaTime() - glideStart
        let t = min(1.0, max(0.0, elapsed / glideDuration))
        // ease-out cubic
        let e = 1 - pow(1 - t, 3)
        position = CGPoint(
            x: glideFrom.x + (glideTo.x - glideFrom.x) * e,
            y: glideFrom.y + (glideTo.y - glideFrom.y) * e
        )
        place(animated: false)
        if t >= 1.0 { stopGlide() }
    }

    private func stopGlide() {
        glideTimer?.invalidate()
        glideTimer = nil
    }

    private func sanitized(_ p: CGPoint) -> CGPoint {
        CGPoint(
            x: p.x.isFinite ? p.x : position.x,
            y: p.y.isFinite ? p.y : position.y
        )
    }

    private func distance(_ a: CGPoint, _ b: CGPoint) -> CGFloat {
        hypot(b.x - a.x, b.y - a.y)
    }

    // MARK: Placement

    private func place(animated: Bool) {
        let activeID = Geometry.display(containing: position)?.id
        for (id, win) in windows {
            guard let view = win.flipped, let cursor = view.cursorLayer else { continue }
            if id == activeID {
                let local = view.convertFromLogical(position, in: win)
                CATransaction.begin()
                CATransaction.setDisableActions(true)
                cursor.position = local
                cursor.isHidden = !visible
                CATransaction.commit()
            } else {
                CATransaction.begin()
                CATransaction.setDisableActions(true)
                cursor.isHidden = true
                CATransaction.commit()
            }
        }
    }

    private func applyVisibility() {
        // NOTE: do NOT call preload() here — rebuildScreens() calls this when
        // visible, and preload() calls rebuildScreens(), which would recurse
        // until the stack overflows. Callers ensure the screens are built.
        let activeID = Geometry.display(containing: position)?.id
        for (id, win) in windows {
            if visible && id == activeID {
                win.orderFrontRegardless()
            } else {
                win.orderOut(nil)
            }
        }
        place(animated: false)
    }

    private func windowAndView(containing p: CGPoint) -> (CursorWindow, CursorView)? {
        guard let d = Geometry.display(containing: p), let win = windows[d.id],
              let view = win.flipped else { return nil }
        return (win, view)
    }

    // MARK: Screens

    private func rebuildScreens() {
        var live = Set<CGDirectDisplayID>()
        for d in Geometry.displays() {
            live.insert(d.id)
            if let existing = windows[d.id] {
                existing.setFrame(cocoaFrame(d), display: false)
                existing.flipped?.logicalOrigin = d.bounds.origin
                existing.flipped?.syncScale(d.scale)
            } else {
                let win = CursorWindow(contentRect: cocoaFrame(d), display: d)
                windows[d.id] = win
            }
        }
        for (id, win) in windows where !live.contains(id) {
            win.orderOut(nil); win.close(); windows.removeValue(forKey: id)
        }
        if visible { applyVisibility() }
    }

    /// Cocoa window frame for a display (bottom-left origin).
    private func cocoaFrame(_ d: DisplayInfo) -> CGRect {
        guard let screen = NSScreen.screens.first(where: { $0.displayID == d.id }) else {
            return CGRect(x: d.bounds.minX, y: d.bounds.minY, width: d.bounds.width, height: d.bounds.height)
        }
        return screen.frame
    }

    private func observeAppSwitches() {
        guard !didObserve else { return }
        didObserve = true
        NotificationCenter.default.addObserver(
            forName: NSApplication.didChangeScreenParametersNotification,
            object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated { self?.rebuildScreens() }
        }
        // Hide the cursor the moment the user switches to a DIFFERENT app than
        // the one the agent is driving, so it never strands over their work.
        NSWorkspace.shared.notificationCenter.addObserver(
            forName: NSWorkspace.didActivateApplicationNotification,
            object: nil, queue: .main
        ) { [weak self] _ in
            MainActor.assumeIsolated {
                guard let self, self.visible else { return }
                if let t = self.targetPid,
                   NSWorkspace.shared.frontmostApplication?.processIdentifier != t {
                    for w in self.windows.values { w.orderOut(nil) }
                } else {
                    self.applyVisibility()
                }
            }
        }
    }

    // MARK: Visuals

    private func pulse(_ layer: CALayer?) {
        guard let layer else { return }
        let a = CABasicAnimation(keyPath: "transform.scale")
        a.fromValue = 1.0
        a.toValue = 0.8
        a.autoreverses = true
        a.duration = 0.08
        a.timingFunction = CAMediaTimingFunction(name: .easeOut)
        layer.add(a, forKey: "pulse")
    }

    private func clearRipples() {
        for r in ripples { r.removeFromSuperlayer() }
        ripples.removeAll()
    }

    private struct Ripple { let layer: CALayer; let duration: CFTimeInterval }

    private func makeRipple(at local: CGPoint, kind: ClickKind) -> Ripple {
        let endRadius: CGFloat
        let duration: CFTimeInterval
        switch kind {
        case .single: endRadius = 26; duration = 0.38
        case .doubleClick: endRadius = 32; duration = 0.42
        case .rightClick: endRadius = 28; duration = 0.40
        }
        let color: NSColor
        switch kind {
        case .rightClick: color = .systemOrange
        case .doubleClick: color = .systemTeal
        case .single: color = NSColor(srgbRed: 0.30, green: 0.62, blue: 1.0, alpha: 1)
        }
        let ring = CAShapeLayer()
        ring.bounds = CGRect(x: 0, y: 0, width: endRadius * 2, height: endRadius * 2)
        ring.position = local
        ring.fillColor = NSColor.clear.cgColor
        ring.strokeColor = color.cgColor
        ring.lineWidth = 2.5
        ring.path = CGPath(ellipseIn: CGRect(x: endRadius - 7, y: endRadius - 7, width: 14, height: 14), transform: nil)
        ring.opacity = 0

        let scale = CABasicAnimation(keyPath: "transform.scale")
        scale.fromValue = 0.35; scale.toValue = 1.0
        scale.timingFunction = CAMediaTimingFunction(name: .easeOut)
        let fade = CABasicAnimation(keyPath: "opacity")
        fade.fromValue = 0.9; fade.toValue = 0.0
        fade.timingFunction = CAMediaTimingFunction(name: .easeOut)
        let group = CAAnimationGroup()
        group.animations = [scale, fade]
        group.duration = duration
        ring.add(group, forKey: "ripple")
        return Ripple(layer: ring, duration: duration)
    }
}

// MARK: - Overlay window

@MainActor
private final class CursorWindow: NSWindow {

    var flipped: CursorView? { contentView as? CursorView }

    init(contentRect: CGRect, display: DisplayInfo) {
        super.init(
            contentRect: contentRect,
            styleMask: .borderless,
            backing: .buffered,
            defer: false
        )
        isOpaque = false
        backgroundColor = .clear
        hasShadow = false
        ignoresMouseEvents = true                 // click-through
        level = NSWindow.Level(rawValue: Int(CGShieldingWindowLevel()))
        collectionBehavior = [.canJoinAllSpaces, .stationary, .ignoresCycle, .fullScreenAuxiliary]
        isExcludedFromWindowsMenu = true
        isReleasedWhenClosed = false
        animationBehavior = .none
        hidesOnDeactivate = false
        let v = CursorView(frame: CGRect(origin: .zero, size: contentRect.size))
        v.logicalOrigin = display.bounds.origin
        v.syncScale(display.scale)
        v.installCursorLayer()
        contentView = v
    }

    override var canBecomeKey: Bool { false }
    override var canBecomeMain: Bool { false }
    override var acceptsFirstResponder: Bool { false }
}

// MARK: - Flipped, click-through view

@MainActor
private final class CursorView: NSView {

    var logicalOrigin: CGPoint = .zero
    private(set) var cursorLayer: CALayer?

    override var isFlipped: Bool { true }

    override func hitTest(_ point: NSPoint) -> NSView? { nil }

    func syncScale(_ scale: CGFloat) {
        layer?.contentsScale = scale
        cursorLayer?.contentsScale = scale
    }

    func convertFromLogical(_ p: CGPoint, in window: NSWindow) -> CGPoint {
        CGPoint(x: p.x - logicalOrigin.x, y: p.y - logicalOrigin.y)
    }

    func installCursorLayer() {
        guard cursorLayer == nil, let host = layer else { return }
        let cursor = CursorView.makeArrow()
        cursor.contentsScale = host.contentsScale
        cursor.isHidden = true
        host.addSublayer(cursor)
        cursorLayer = cursor
    }

    /// A macOS-shaped arrow whose tip is the exact point being acted on.
    /// Blue fill + white keyline + halo, so it reads as "not your pointer".
    private static func makeArrow() -> CALayer {
        let path = arrowPath()
        let box = path.boundingBox

        let container = CALayer()
        container.bounds = CGRect(x: 0, y: 0, width: box.maxX, height: box.maxY)
        container.anchorPoint = .zero      // (0,0) = the tip

        // Halo behind the silhouette.
        let halo = CAGradientLayer()
        halo.type = .radial
        let accent = NSColor(srgbRed: 0.15, green: 0.55, blue: 1.0, alpha: 1)
        halo.colors = [
            accent.withAlphaComponent(0.42).cgColor,
            accent.withAlphaComponent(0.0).cgColor,
        ]
        halo.locations = [0.0, 1.0]
        halo.startPoint = CGPoint(x: 0.5, y: 0.5)
        halo.endPoint = CGPoint(x: 1.0, y: 1.0)
        let dia: CGFloat = max(28, box.maxY * 1.6)
        halo.bounds = CGRect(x: 0, y: 0, width: dia, height: dia)
        halo.anchorPoint = CGPoint(x: 0.5, y: 0.5)
        halo.position = CGPoint(x: box.midX, y: box.midY)
        container.addSublayer(halo)

        let shape = CAShapeLayer()
        shape.path = path
        shape.frame = container.bounds
        shape.fillColor = NSColor(srgbRed: 0.0, green: 0.46, blue: 0.95, alpha: 1).cgColor
        shape.strokeColor = NSColor.white.cgColor
        shape.lineWidth = 1.5
        shape.lineJoin = .round
        shape.shadowColor = NSColor.black.cgColor
        shape.shadowOpacity = 0.35
        shape.shadowRadius = 3
        shape.shadowOffset = CGSize(width: 0, height: 1)
        container.addSublayer(shape)

        container.actions = [
            "position": NSNull(), "bounds": NSNull(),
            "hidden": NSNull(), "transform": NSNull(),
        ]
        return container
    }

    /// Arrow silhouette in a top-left origin, tip at (0,0).
    private static func arrowPath() -> CGPath {
        let p = CGMutablePath()
        p.move(to: CGPoint(x: 0, y: 0))
        p.addLine(to: CGPoint(x: 0, y: 17))
        p.addLine(to: CGPoint(x: 4.2, y: 13))
        p.addLine(to: CGPoint(x: 7, y: 19.5))
        p.addLine(to: CGPoint(x: 9.8, y: 18.2))
        p.addLine(to: CGPoint(x: 7, y: 11.8))
        p.addLine(to: CGPoint(x: 12.5, y: 11.5))
        p.closeSubpath()
        return p
    }
}
