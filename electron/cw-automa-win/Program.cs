// Program.cs — cwautoma-win entry point.
//
// Resident Windows computer-use helper for CoWorker. Newline-delimited JSON over
// stdin/stdout. UI Automation is the primary perception/actuation surface;
// SendInput is the coordinate fallback. Mirrors macOS cw-automa.

using System.Drawing;
using System.Text;
using System.Text.Json;
using System.Windows.Forms;
using FlaUI.UIA3;

namespace CwAutomaWin;

internal static class Program
{
    private static UIA3Automation Automation;
    private static UiaTree Tree;

    private static readonly string[] Methods =
    {
        "ping", "frontmost", "displays", "snapshot", "get_app_state", "act",
        "list_apps", "resolve_app", "focus_app", "launch",
        "input_text", "type_text", "press_hotkey", "press_key",
        "click_coords", "click_point", "drag_point", "scroll", "scroll_to",
        "drag_to", "click_point_to",
        "ui_settle",
        "cursor_move", "cursor_show", "cursor_hide", "cursor_park", "cursor_debug",
        "cursor_demo", "cursor_position",
        "hud_show", "hud_pause", "hud_hide", "set_stop_label",
        "permissions", "permissions_request",
    };

    [STAThread]
    private static int Main(string[] args)
    {
        // Per-monitor DPI v2 (must precede any window creation) so coordinates and
        // UIA bounding boxes are consistent on mixed-DPI/multi-monitor setups.
        try { Application.SetHighDpiMode(HighDpiMode.PerMonitorV2); } catch { /* ignore */ }

        // Ensure the working directory is sane.
        try { Directory.SetCurrentDirectory(AppContext.BaseDirectory); } catch { /* ignore */ }

        Automation = new UIA3Automation();
        Tree = new UiaTree(Automation);
        Overlay.Start();

        var reader = new StreamReader(Console.OpenStandardInput(), new UTF8Encoding(false));
        string line;
        while ((line = reader.ReadLine()) != null)
        {
            if (line.Length == 0) continue;
            HandleLine(line);
        }
        try { Automation.Dispose(); } catch { /* ignore */ }
        return 0;
    }

    private static void HandleLine(string line)
    {
        int id = -1;
        try
        {
            using var doc = JsonDocument.Parse(line);
            var root = doc.RootElement;
            if (!root.TryGetProperty("id", out var idEl) || !idEl.TryGetInt32(out id)) return;
            string method = root.TryGetProperty("method", out var mEl) ? (mEl.GetString() ?? "") : "";
            JsonElement p = root.TryGetProperty("params", out var pp) && pp.ValueKind == JsonValueKind.Object
                ? pp.Clone() : default;
            Dispatch(id, method, p);
        }
        catch (HelperError he)
        {
            Responder.Fail(id, $"{he.Code}: {he.Message}", he.Code, he.Hint);
        }
        catch (JsonException)
        {
            // Malformed line: ignore (do not kill the resident helper).
        }
        catch (Exception e)
        {
            Responder.Fail(id, e.Message, "error");
        }
    }

    private static void Dispatch(int id, string method, JsonElement p)
    {
        switch (method)
        {
            case "ping":
                Responder.Ok(id, new Dictionary<string, object>
                {
                    ["platform"] = "win32",
                    ["version"] = 4,
                    ["methods"] = Methods,
                    // Bilateral capability manifest (Windows side). Keys are documented in
                    // docs/COMPUTER-USE-PARITY.md. Values reflect CURRENT behavior; P1 flips
                    // them as parity lands (clipboard_restore + unicode_graphemes now done;
                    // target_confirmation still false until a real paste receipt exists).
                    ["features"] = new Dictionary<string, object>
                    {
                        ["overlay"] = true, ["physical_displays"] = true, ["ax"] = false, ["uia"] = true,
                        ["input_model"] = "global",
                        ["background_input"] = false,
                        ["clipboard_restore"] = true,
                        ["target_confirmation"] = true,
                        ["unicode_graphemes"] = true,
                        ["middle_click"] = true,
                        ["keypad_keys"] = true,
                        ["permission_model"] = "uipi",
                    },
                });
                break;

            case "frontmost":
            {
                int pid = AppInventory.FrontmostPid();
                Responder.Ok(id, new Dictionary<string, object> { ["pid"] = pid, ["app"] = AppName(pid) });
                break;
            }

            case "displays":
                Responder.Ok(id, new Dictionary<string, object> { ["displays"] = Monitors.Enumerate() });
                break;

            case "snapshot":
            case "get_app_state":
                HandleSnapshot(id, p);
                break;

            case "act":
                HandleAct(id, p);
                break;

            case "list_apps":
            {
                string scope = Params.Str(p, "scope", "running");
                var apps = scope == "installed" ? AppInventory.Installed() : AppInventory.Running();
                Responder.Ok(id, new Dictionary<string, object>
                {
                    ["scope"] = scope,
                    ["apps"] = apps.Select(a => (object)a.Dict()).ToList(),
                });
                break;
            }

            case "resolve_app":
                Responder.Ok(id, AppInventory.ResolveApp(Params.Str(p, "app")));
                break;

            case "focus_app":
            {
                string app = Params.Str(p, "app");
                bool settle = Params.Bool(p, "settle", true);
                bool ok = AppInventory.FocusApp(app, settle);
                Responder.Ok(id, new Dictionary<string, object> { ["focused"] = ok, ["pid"] = AppInventory.ResolvePid(app) });
                break;
            }

            case "launch":
            {
                string app = Params.Str(p, "app");
                int pid = AppInventory.Launch(app);
                Overlay.Pulse();
                Responder.Ok(id, new Dictionary<string, object> { ["launched"] = app, ["ok"] = pid > 0, ["pid"] = pid });
                break;
            }

            case "input_text":
                HandleInputText(id, p);
                break;

            case "type_text":
            {
                string text = Params.Str(p, "text");
                int pid = ResolveActPid(p);
                if (pid > 0) Elevation.EnsureNotBlocked(pid);
                if (pid > 0) { AppInventory.FocusApp(pid.ToString(), false); }
                string rf = Params.Str(p, "ref");
                if (!string.IsNullOrEmpty(rf))
                {
                    var el = Tree.Find(pid > 0 ? pid : AppInventory.FrontmostPid(), rf);
                    TextInput.Enter(el, pid, text, false);
                }
                else
                {
                    TextInput.PasteText(text);
                }
                Overlay.Pulse();
                if (pid > 0) UiaActions.TryActivate(pid);
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "type_text" });
                break;
            }

            case "press_hotkey":
            {
                string key = Params.Str(p, "key");
                var mods = Params.StrArray(p, "modifiers");
                if (string.IsNullOrEmpty(key)) throw new HelperError("param_error", "press_hotkey requires key");
                // Windows injection is GLOBAL — bring the requested app forward
                // first so the chord lands in the target, not whatever is frontmost.
                int hp = ResolveActPid(p);
                if (hp > 0) { Elevation.EnsureNotBlocked(hp); UiaActions.TryActivate(hp); Thread.Sleep(60); }
                Input.PressCombo(key, mods, 1);
                Overlay.Pulse();
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "press_hotkey", ["pid"] = hp });
                break;
            }

            case "press_key":
            {
                string key = Params.Str(p, "key");
                var mods = Params.StrArray(p, "modifiers");
                int repeat = Math.Max(1, Params.Int(p, "repeat", 1));
                if (string.IsNullOrEmpty(key)) throw new HelperError("param_error", "press_key requires key");
                int pid = ResolveActPid(p);
                if (pid > 0) { Elevation.EnsureNotBlocked(pid); UiaActions.TryActivate(pid); Thread.Sleep(60); }
                Input.PressCombo(key, mods, repeat);
                Overlay.Pulse();
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "press_key", ["pid"] = pid });
                break;
            }

            case "click_coords":
            {
                int x = (int)Math.Round(Params.Dbl(p, "x"));
                int y = (int)Math.Round(Params.Dbl(p, "y"));
                Overlay.Move(x, y);
                Input.Click(x, y, "left");
                Overlay.ClickFx();
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "click_coords" });
                break;
            }

            case "click_point":
            {
                int x = (int)Math.Round(Params.Dbl(p, "x"));
                int y = (int)Math.Round(Params.Dbl(p, "y"));
                string kind = Params.Str(p, "kind", "left");
                Overlay.Move(x, y);
                Input.Click(x, y, kind);
                Overlay.ClickFx();
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "click_point" });
                break;
            }

            case "drag_point":
            {
                int x1 = (int)Math.Round(Params.Dbl(p, "x1")), y1 = (int)Math.Round(Params.Dbl(p, "y1"));
                int x2 = (int)Math.Round(Params.Dbl(p, "x2")), y2 = (int)Math.Round(Params.Dbl(p, "y2"));
                Overlay.Move(x1, y1);
                Input.Drag(x1, y1, x2, y2, 24, Params.Str(p, "button", "left"));
                Overlay.Move(x2, y2);
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "drag_point" });
                break;
            }

            case "scroll":
                Input.Scroll((int)Params.Dbl(p, "dx"), (int)Params.Dbl(p, "dy"));
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "scroll" });
                break;

            case "scroll_to":
            {
                int pid = ResolveActPid(p);
                int dx = (int)Params.Dbl(p, "dx");
                int dy = (int)Params.Dbl(p, "dy");
                bool hasX = p.ValueKind == JsonValueKind.Object && p.TryGetProperty("x", out _);
                bool hasY = p.ValueKind == JsonValueKind.Object && p.TryGetProperty("y", out _);
                if (hasX && hasY)
                {
                    Input.ScrollAt((int)Math.Round(Params.Dbl(p, "x")), (int)Math.Round(Params.Dbl(p, "y")), dx, dy);
                }
                else
                {
                    // No explicit point: bring the target forward and scroll at the
                    // CURRENT cursor — never jump to (0,0) (macOS falls back to the
                    // last pointer; Windows has a real cursor to reuse).
                    if (pid > 0) UiaActions.TryActivate(pid);
                    Input.Scroll(dx, dy);
                }
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "scroll_to", ["pid"] = pid });
                break;
            }

            case "drag_to":
            {
                int pid = ResolveActPid(p);
                int x1 = (int)Math.Round(Params.Dbl(p, "x1")), y1 = (int)Math.Round(Params.Dbl(p, "y1"));
                int x2 = (int)Math.Round(Params.Dbl(p, "x2")), y2 = (int)Math.Round(Params.Dbl(p, "y2"));
                int steps = Math.Max(1, Params.Int(p, "steps", 12));
                Input.Drag(x1, y1, x2, y2, steps);
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "drag_to", ["pid"] = pid });
                break;
            }

            case "click_point_to":
            {
                int pid = ResolveActPid(p);
                if (pid > 0) UiaActions.TryActivate(pid);
                int x = (int)Math.Round(Params.Dbl(p, "x"));
                int y = (int)Math.Round(Params.Dbl(p, "y"));
                Overlay.Move(x, y);
                Input.Click(x, y, "left");
                Overlay.ClickFx();
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "click_point_to", ["pid"] = pid });
                break;
            }

            case "ui_settle":
            {
                int pid = ResolveActPid(p);
                if (pid <= 0) throw new HelperError("no_target", "ui_settle requires a running app");
                int quiet = Params.Int(p, "quiet_ms", 250);
                int timeout = Params.Int(p, "timeout_ms", 3000);
                bool settled = Tree.WaitSettled(pid, quiet, timeout);
                Responder.Ok(id, new Dictionary<string, object> { ["settled"] = settled });
                break;
            }

            case "cursor_move":
                Overlay.Move((int)Params.Dbl(p, "x"), (int)Params.Dbl(p, "y"));
                Responder.Ok(id, new Dictionary<string, object> { ["performed"] = "cursor_move" });
                break;

            case "cursor_show":
                Overlay.ShowCursor();
                Responder.Ok(id, new Dictionary<string, object> { ["shown"] = true });
                break;

            case "cursor_hide":
            case "cursor_park":
                Overlay.Hide();
                Responder.Ok(id, new Dictionary<string, object> { ["shown"] = false });
                break;

            case "cursor_debug":
                Responder.Ok(id, new Dictionary<string, object> { ["virtual"] = Dbg(Overlay.Current()), ["real"] = Dbg(Input.GetCursor()) });
                break;

            case "cursor_demo":
            {
                double seconds = Math.Clamp(Params.Dbl(p, "seconds", 3), 0.5, 15);
                Overlay.ShowCursor();
                // Animate for the requested duration (bounds the call: ≤15s).
                var deadline = DateTime.UtcNow.AddSeconds(seconds);
                while (DateTime.UtcNow < deadline)
                {
                    Overlay.Pulse();
                    Thread.Sleep(250);
                }
                Responder.Ok(id, new Dictionary<string, object> { ["demo"] = true, ["seconds"] = seconds });
                break;
            }

            case "cursor_position":
            {
                var real = Input.GetCursor();
                var virt = Overlay.Current();
                Responder.Ok(id, new Dictionary<string, object>
                {
                    ["real"] = new Dictionary<string, object> { ["x"] = real.X, ["y"] = real.Y },
                    ["virtual"] = new Dictionary<string, object> { ["x"] = virt.X, ["y"] = virt.Y },
                });
                break;
            }

            case "hud_show":
                Overlay.HudShow();
                Responder.Ok(id, new Dictionary<string, object> { ["shown"] = true });
                break;

            case "hud_pause":
                Overlay.HudPause(Params.Bool(p, "paused", true));
                Responder.Ok(id, new Dictionary<string, object> { ["paused"] = Params.Bool(p, "paused", true) });
                break;

            case "hud_hide":
                Overlay.HudHide();
                Responder.Ok(id, new Dictionary<string, object> { ["shown"] = false });
                break;

            case "set_stop_label":
            {
                string label = Params.Str(p, "label");
                Overlay.SetLabel(label);
                Responder.Ok(id, new Dictionary<string, object> { ["label"] = label });
                break;
            }

            case "permissions":
            {
                // Windows has no TCC-style promptable permission; the real gate is
                // UIPI/elevation. Report that honestly instead of a bare `true`.
                int front = AppInventory.FrontmostPid();
                bool blocked = front > 0 && Elevation.IsBlocked(front);
                Responder.Ok(id, new Dictionary<string, object>
                {
                    ["platform"] = "win32",
                    ["model"] = "uipi",
                    ["accessibility"] = true,   // UIA needs no prompt on Windows
                    ["screen"] = true,          // screen capture needs no prompt
                    ["self_elevated"] = Elevation.SelfElevated,
                    ["frontmost_pid"] = front,
                    ["can_input_frontmost"] = !blocked,
                    ["blocked_reason"] = blocked ? "uipi_blocked" : "",
                    ["note"] = "Windows uses UIPI, not a prompt: input to an elevated window " +
                               "requires running CoWorker as administrator.",
                });
                break;
            }

            case "permissions_request":
            {
                string kind = Params.Str(p, "kind", "accessibility");
                Responder.Ok(id, new Dictionary<string, object>
                {
                    ["kind"] = kind,
                    ["granted"] = true,   // nothing to request; the gate is UIPI
                    ["status"] = true,
                    ["model"] = "uipi",
                    ["note"] = "Windows has no accessibility permission prompt; run CoWorker as " +
                               "administrator to drive elevated windows.",
                });
                break;
            }

            default:
                throw new HelperError("unknown_method", $"unknown method {method}");
        }
    }

    // ── Snapshot ─────────────────────────────────────────────────────────
    private static readonly object SnapLock = new();
    private static readonly Dictionary<string, (HashSet<string> refs, Dictionary<string, string> values)> LastSnaps = new();

    private static void HandleSnapshot(int id, JsonElement p)
    {
        int depth = Params.Int(p, "depth", UiaTree.TreeDepth);
        string requestedApp = Params.Str(p, "app");
        // STRICT: an explicitly requested app must resolve exactly. Never silently
        // fall back to the frontmost app — that returned a DIFFERENT app's tree
        // and let the model act on the wrong window (macOS throws no_target).
        int pid = ResolveRequestedPid(requestedApp);
        if (pid <= 0) throw new HelperError("no_target", $"No running application matches '{requestedApp}'");
        Elevation.EnsureNotBlocked(pid);

        string appName = AppName(pid);
        string frontName = AppName(AppInventory.FrontmostPid());

        var win = Tree.FindWindow(pid);
        if (win == null)
        {
            Responder.Ok(id, new Dictionary<string, object>
            {
                ["frontmost"] = frontName, ["app"] = appName, ["pid"] = pid, ["refs"] = 0, ["text"] = "",
                ["root"] = new Dictionary<string, object> { ["ref"] = "window", ["role"] = "Window", ["label"] = appName, ["note"] = "No readable window in this app." },
            });
            return;
        }

        var windowInfo = new Dictionary<string, object> { ["title"] = UiaTree.LabelOf(win) };
        var rect = UiaTree.RectOf(win);
        if (!rect.IsEmpty)
        {
            windowInfo["frame"] = new Dictionary<string, object>
            {
                ["x"] = rect.Left, ["y"] = rect.Top, ["width"] = rect.Width, ["height"] = rect.Height,
            };
        }

        var seq = new Dictionary<string, int>();
        int refCount = 0;
        var root = Tree.Build(win, true, depth, seq, ref refCount);
        if (root == null)
        {
            Responder.Ok(id, new Dictionary<string, object>
            {
                ["frontmost"] = frontName, ["app"] = appName, ["pid"] = pid, ["refs"] = 0, ["text"] = "",
                ["window"] = windowInfo,
                ["root"] = new Dictionary<string, object> { ["ref"] = "window", ["role"] = "Window", ["label"] = appName, ["note"] = "No readable elements in the focused window." },
            });
            return;
        }

        var lines = new List<string>();
        UiaTree.Render(root, lines, 0, Params.Int(p, "max_lines", 400));
        string text = string.Join("\n", lines);

        var map = new Dictionary<string, UiaNode>();
        UiaTree.Flatten(root, map);
        var values = new Dictionary<string, string>();
        foreach (var kv in map) values[kv.Key] = kv.Value.Label + "\u0001" + kv.Value.Value;

        bool disableDiff = Params.Bool(p, "disableDiff", false) || Params.Bool(p, "disable_diff", false);
        string key = $"{pid}:{windowInfo.GetValueOrDefault("title")}";
        bool changed = true;
        var removed = new List<string>();
        lock (SnapLock)
        {
            if (!disableDiff && LastSnaps.TryGetValue(key, out var prev))
            {
                var current = new HashSet<string>(map.Keys);
                removed = prev.refs.Except(current).OrderBy(x => x, StringComparer.Ordinal).ToList();
                changed = removed.Count > 0;
                if (!changed)
                {
                    foreach (var kv in values)
                    {
                        if (!prev.values.TryGetValue(kv.Key, out var old) || old != kv.Value) { changed = true; break; }
                    }
                }
            }
            LastSnaps[key] = (new HashSet<string>(map.Keys), values);
        }

        var result = new Dictionary<string, object>
        {
            ["frontmost"] = frontName, ["app"] = appName, ["pid"] = pid,
            ["refs"] = refCount, ["root"] = UiaTree.Dict(root), ["text"] = text,
            ["window"] = windowInfo, ["changed"] = disableDiff || changed, ["diff"] = !disableDiff,
        };
        if (removed.Count > 0) result["removed"] = removed;
        Responder.Ok(id, result);
    }

    private static void HandleAct(int id, JsonElement p)
    {
        int pid = ResolveActPid(p);
        if (pid <= 0) throw new HelperError("no_target", "no target app for act");
        string rf = Params.Str(p, "ref");
        string op = Params.Str(p, "op", "click");
        var result = UiaActions.Act(Automation, Tree, pid, rf, op, p);
        Overlay.Pulse();
        Responder.Ok(id, result);
    }

    private static void HandleInputText(int id, JsonElement p)
    {
        int pid = ResolveActPid(p);
        if (pid <= 0) throw new HelperError("no_target", "input_text requires a running app");
        Elevation.EnsureNotBlocked(pid);
        string rf = Params.Str(p, "ref");
        string text = Params.Str(p, "text");
        bool submit = Params.Bool(p, "submit", false);
        if (string.IsNullOrEmpty(text)) throw new HelperError("param_error", "input_text requires text");

        UiaActions.TryActivate(pid);
        Thread.Sleep(120);
        var el = string.IsNullOrEmpty(rf) ? null : Tree.Find(pid, rf);
        var outcome = TextInput.Enter(el, pid, text, submit);
        Overlay.Pulse();
        Responder.Ok(id, new Dictionary<string, object>
        {
            ["performed"] = "input_text", ["submit"] = submit,
            ["strategy"] = outcome.Strategy, ["verified"] = outcome.Verified, ["value"] = outcome.Value ?? "",
        });
    }

    // ── Helpers ──────────────────────────────────────────────────────────
    /// <summary>
    /// Resolve an explicitly-requested app selector to a pid. Returns -1 (never a
    /// wrong-app fallback) when an explicit selector does not match. An empty
    /// selector means "the frontmost app".
    /// </summary>
    private static int ResolveRequestedPid(string app)
    {
        if (string.IsNullOrEmpty(app)) return AppInventory.FrontmostPid();
        if (int.TryParse(app, out int n) && n > 0) return n;
        return AppInventory.ResolvePid(app);
    }

    private static int ResolveAppPid(string app)
    {
        if (string.IsNullOrEmpty(app)) return AppInventory.FrontmostPid();
        if (int.TryParse(app, out int n) && n > 0) return n;
        int pid = AppInventory.ResolvePid(app);
        return pid > 0 ? pid : AppInventory.FrontmostPid();
    }

    private static int ResolveActPid(JsonElement p)
    {
        if (p.ValueKind == JsonValueKind.Object && p.TryGetProperty("pid", out var pidEl) && pidEl.TryGetInt32(out int pid) && pid > 0)
            return pid;
        string app = Params.Str(p, "app");
        if (!string.IsNullOrEmpty(app))
        {
            int resolved = AppInventory.ResolvePid(app);
            if (resolved > 0) return resolved;
        }
        return AppInventory.FrontmostPid();
    }

    private static string AppName(int pid)
    {
        if (pid <= 0) return "";
        try { return System.Diagnostics.Process.GetProcessById(pid).ProcessName; }
        catch { return ""; }
    }

    private static Dictionary<string, object> Dbg(Input.POINT p) => new() { ["x"] = p.X, ["y"] = p.Y };
    private static Dictionary<string, object> Dbg(Point p) => new() { ["x"] = p.X, ["y"] = p.Y };
}
