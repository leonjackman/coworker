// Protocol.cs — request parsing + JSON-lines response plumbing.
//
// Mirrors macOS cw-automa/src/Protocol.swift so the TS layer sees one contract.

using System.Text;
using System.Text.Json;

namespace CwAutomaWin;

internal sealed class HelperError : Exception
{
    public string Code { get; }
    public HelperError(string code, string message) : base(message) { Code = code; }
}

internal static class Responder
{
    private static readonly object Lock = new();
    private static readonly StreamWriter Out =
        new(Console.OpenStandardOutput(), new UTF8Encoding(false)) { AutoFlush = true };

    private static void Emit(Dictionary<string, object> obj)
    {
        lock (Lock)
        {
            Out.WriteLine(JsonSerializer.Serialize(obj));
        }
    }

    public static void Ok(int id, Dictionary<string, object> result)
    {
        Emit(new Dictionary<string, object> { ["id"] = id, ["ok"] = true, ["result"] = result });
    }

    public static void Fail(int id, string error, string code = null)
    {
        var obj = new Dictionary<string, object> { ["id"] = id, ["ok"] = false, ["error"] = error };
        if (code != null) obj["error_code"] = code;
        Emit(obj);
    }
}

internal static class Params
{
    public static string Str(JsonElement p, string key, string fallback = "")
    {
        if (p.ValueKind == JsonValueKind.Object && p.TryGetProperty(key, out var v))
        {
            if (v.ValueKind == JsonValueKind.String) return v.GetString();
            if (v.ValueKind == JsonValueKind.Number) return v.ToString();
        }
        return fallback;
    }

    public static int Int(JsonElement p, string key, int fallback = 0)
    {
        if (p.ValueKind == JsonValueKind.Object && p.TryGetProperty(key, out var v))
        {
            if (v.ValueKind == JsonValueKind.Number && v.TryGetInt32(out var n)) return n;
            if (v.ValueKind == JsonValueKind.String && int.TryParse(v.GetString(), out var s)) return s;
        }
        return fallback;
    }

    public static double Dbl(JsonElement p, string key, double fallback = 0)
    {
        if (p.ValueKind == JsonValueKind.Object && p.TryGetProperty(key, out var v))
        {
            if (v.ValueKind == JsonValueKind.Number && v.TryGetDouble(out var n)) return n;
            if (v.ValueKind == JsonValueKind.String && double.TryParse(v.GetString(), out var s)) return s;
        }
        return fallback;
    }

    public static bool Bool(JsonElement p, string key, bool fallback = false)
    {
        if (p.ValueKind == JsonValueKind.Object && p.TryGetProperty(key, out var v))
        {
            if (v.ValueKind == JsonValueKind.True) return true;
            if (v.ValueKind == JsonValueKind.False) return false;
            if (v.ValueKind == JsonValueKind.Number) return v.GetDouble() != 0;
        }
        return fallback;
    }

    public static List<string> StrArray(JsonElement p, string key)
    {
        var list = new List<string>();
        if (p.ValueKind == JsonValueKind.Object && p.TryGetProperty(key, out var v) && v.ValueKind == JsonValueKind.Array)
        {
            foreach (var item in v.EnumerateArray())
            {
                if (item.ValueKind == JsonValueKind.String) list.Add(item.GetString());
            }
        }
        return list;
    }
}
