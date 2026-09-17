//
//  Protocol.swift
//  cw-automa
//
//  Shared types + JSON-lines plumbing. One JSON object per line on stdout.
//    in : {"id":N,"method":"...","params":{...}}
//    out: {"id":N,"ok":true,"result":{...}} | {"id":N,"ok":false,"error":"..."}
//

import Foundation

/// An error carrying a stable machine code the TS layer can branch on.
struct HelperError: Error, CustomStringConvertible {
    let code: String
    let message: String
    init(_ code: String, _ message: String) {
        self.code = code
        self.message = message
    }
    var description: String { message }
}

struct Request {
    let id: Int
    let method: String
    let params: [String: Any]
}

enum Responder {

    /// Serialize + write one line to stdout. Thread-safe (stdout lock).
    static func emit(_ obj: [String: Any]) {
        guard let data = try? JSONSerialization.data(withJSONObject: obj, options: []) else { return }
        let lock = stdoutLock
        objc_sync_enter(lock)
        defer { objc_sync_exit(lock) }
        FileHandle.standardOutput.write(data)
        FileHandle.standardOutput.write(Data([0x0A]))
    }

    static func ok(_ id: Int, _ result: [String: Any]) {
        emit(["id": id, "ok": true, "result": result])
    }

    static func fail(_ id: Int, _ error: String, code: String? = nil) {
        var obj: [String: Any] = ["id": id, "ok": false, "error": error]
        if let code { obj["error_code"] = code }
        emit(obj)
    }

    private static let stdoutLock = NSObject()
}

/// Parse one raw stdin line into a Request, or nil when malformed.
func parseRequest(_ line: String) -> Request? {
    guard let data = line.data(using: .utf8),
          let raw = try? JSONSerialization.jsonObject(with: data) as? [String: Any],
          let id = raw["id"] as? Int,
          let method = raw["method"] as? String
    else { return nil }
    let params = raw["params"] as? [String: Any] ?? [:]
    return Request(id: id, method: method, params: params)
}

/// Small typed accessors so call sites stay readable.
extension Dictionary where Key == String, Value == Any {
    func str(_ key: String, _ fallback: String = "") -> String {
        (self[key] as? String) ?? fallback
    }
    func int(_ key: String, _ fallback: Int = 0) -> Int {
        if let n = self[key] as? NSNumber { return n.intValue }
        if let s = self[key] as? String, let n = Int(s) { return n }
        return fallback
    }
    func dbl(_ key: String, _ fallback: Double = 0) -> Double {
        if let n = self[key] as? NSNumber { return n.doubleValue }
        if let s = self[key] as? String, let n = Double(s) { return n }
        return fallback
    }
    func bool(_ key: String, _ fallback: Bool = false) -> Bool {
        if let b = self[key] as? Bool { return b }
        if let n = self[key] as? NSNumber { return n.boolValue }
        return fallback
    }
    func strArray(_ key: String) -> [String] {
        (self[key] as? [String]) ?? []
    }
}
