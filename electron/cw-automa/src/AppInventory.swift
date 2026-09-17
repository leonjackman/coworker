//
//  AppInventory.swift
//  cw-automa
//
//  App discovery for the persistent JS surface (`cua.getApp`, `cua.listApps`).
//  Running apps come from NSWorkspace (pid/bundle/name); installed apps come
//  from a one-shot scan of the standard application directories. Results are
//  cached for the process lifetime — the agent re-queries `list_running_apps`
//  when it needs live state.
//

import AppKit
import Foundation

enum AppInventory {

    struct Entry {
        let bundleId: String
        let displayName: String
        let pid: pid_t?
        let path: String?

        var dict: [String: Any] {
            var out: [String: Any] = [
                "bundleId": bundleId,
                "displayName": displayName,
            ]
            if let pid { out["pid"] = Int(pid) }
            if let path, !path.isEmpty { out["path"] = path }
            return out
        }
    }

    /// Running, UI-visible applications, frontmost first, then alphabetical.
    static func running() -> [Entry] {
        let apps = NSWorkspace.shared.runningApplications.filter {
            $0.activationPolicy != .prohibited
        }
        let entries = apps.map { app in
            Entry(
                bundleId: app.bundleIdentifier ?? "",
                displayName: app.localizedName ?? app.bundleIdentifier ?? "?",
                pid: app.processIdentifier,
                path: app.bundleURL?.path
            )
        }
        let front = NSWorkspace.shared.frontmostApplication?.processIdentifier
        return entries.sorted { a, b in
            if a.pid == front { return true }
            if b.pid == front { return false }
            return a.displayName.localizedCaseInsensitiveCompare(b.displayName) == .orderedAscending
        }
    }

    private static var cachedInstalled: [Entry]?

    /// Installed applications discovered by scanning the standard directories.
    /// Cached: a scan touches disk; callers that need new installs restart the
    /// helper (which happens on every app launch in this codebase).
    static func installed() -> [Entry] {
        if let cachedInstalled { return cachedInstalled }
        var roots: [URL] = [
            URL(fileURLWithPath: "/Applications", isDirectory: true),
            URL(fileURLWithPath: "/System/Applications", isDirectory: true),
        ]
        if let home = FileManager.default.homeDirectoryForCurrentUser as URL? {
            roots.append(home.appendingPathComponent("Applications", isDirectory: true))
        }
        var seen = Set<String>()
        var out: [Entry] = []
        let fm = FileManager.default
        for root in roots {
            guard let children = try? fm.contentsOfDirectory(
                at: root, includingPropertiesForKeys: [.isDirectoryKey], options: [.skipsHiddenFiles]
            ) else { continue }
            for child in children where child.pathExtension.lowercased() == "app" {
                let bundle = Bundle(url: child)
                let id = bundle?.bundleIdentifier ?? ""
                let name = (bundle?.object(forInfoDictionaryKey: "CFBundleDisplayName") as? String)
                    ?? (bundle?.object(forInfoDictionaryKey: "CFBundleName") as? String)
                    ?? child.deletingPathExtension().lastPathComponent
                let key = id.isEmpty ? child.path : id
                guard !seen.contains(key) else { continue }
                seen.insert(key)
                out.append(Entry(bundleId: id, displayName: name, pid: nil, path: child.path))
            }
        }
        out.sort { $0.displayName.localizedCaseInsensitiveCompare($1.displayName) == .orderedAscending }
        cachedInstalled = out
        return out
    }

    /// Resolve a name / bundle id / pid / path to a running pid (nil if not running).
    static func resolvePid(_ selector: String) -> pid_t? {
        let s = selector.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !s.isEmpty else { return nil }
        if let n = Int(s), n > 0 { return pid_t(n) }
        let running = NSWorkspace.shared.runningApplications
        // 1) exact bundle id
        if let a = running.first(where: { $0.bundleIdentifier == s }) { return a.processIdentifier }
        // 2) exact display name
        if let a = running.first(where: { $0.localizedName == s }) { return a.processIdentifier }
        // 3) case-insensitive display name
        if let a = running.first(where: {
            ($0.localizedName ?? "").caseInsensitiveCompare(s) == .orderedSame
        }) { return a.processIdentifier }
        // 4) bundle id / path suffix
        if let a = running.first(where: { ($0.bundleIdentifier ?? "").hasSuffix(s) }) {
            return a.processIdentifier
        }
        if let a = running.first(where: { ($0.bundleURL?.path ?? "") == s }) {
            return a.processIdentifier
        }
        // 5) bundle filename base ("Finder" for Finder.app) — lets English
        //    selectors resolve on a non-English system where localizedName is
        //    transliterated (e.g. 访达).
        if let a = running.first(where: {
            ($0.bundleURL?.deletingPathExtension().lastPathComponent ?? "")
                .caseInsensitiveCompare(s) == .orderedSame
        }) { return a.processIdentifier }
        if let a = running.first(where: {
            ($0.executableURL?.deletingPathExtension().lastPathComponent ?? "")
                .caseInsensitiveCompare(s) == .orderedSame
        }) { return a.processIdentifier }
        return nil
    }

    /// Resolve a selector to an installed app's bundle URL (for launching).
    static func resolveBundleURL(_ selector: String) -> URL? {
        if let url = NSWorkspace.shared.urlForApplication(withBundleIdentifier: selector) {
            return url
        }
        let s = selector.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasSuffix(".app"), FileManager.default.fileExists(atPath: s) {
            return URL(fileURLWithPath: s)
        }
        if let e = installed().first(where: {
            $0.bundleId == s || $0.displayName.caseInsensitiveCompare(s) == .orderedSame
        }), let p = e.path {
            return URL(fileURLWithPath: p)
        }
        // Bundle filename base ("Finder" -> Finder.app) on non-English systems.
        if let e = installed().first(where: {
            (URL(fileURLWithPath: $0.path ?? "").deletingPathExtension().lastPathComponent)
                .caseInsensitiveCompare(s) == .orderedSame
        }), let p = e.path {
            return URL(fileURLWithPath: p)
        }
        if let e = installed().first(where: {
            $0.displayName.lowercased().contains(s.lowercased())
        }), let p = e.path {
            return URL(fileURLWithPath: p)
        }
        return nil
    }
}
