// AppInventory.cs — running/installed app discovery, resolve, launch, focus.
// Mirrors macOS AppInventory.swift with Windows process/window semantics.

using System.Diagnostics;
using Microsoft.Win32;

namespace CwAutomaWin;

internal static class AppInventory
{
    public sealed class Entry
    {
        public string AppId = "";
        public string DisplayName = "";
        public string Path = "";
        public int Pid;

        public Dictionary<string, object> Dict()
        {
            var d = new Dictionary<string, object>
            {
                ["appId"] = AppId,
                ["bundleId"] = AppId, // alias for cross-platform callers
                ["displayName"] = DisplayName,
                ["pid"] = Pid,
            };
            if (!string.IsNullOrEmpty(Path)) d["path"] = Path;
            return d;
        }
    }

    public static int FrontmostPid()
    {
        try
        {
            NativeMethods.GetWindowThreadProcessId(NativeMethods.GetForegroundWindow(), out uint pid);
            return pid > 0 ? (int)pid : -1;
        }
        catch { return -1; }
    }

    private static string TryPath(Process p)
    {
        try { return p.MainModule?.FileName ?? ""; } catch { return ""; }
    }

    public static List<Entry> Running()
    {
        var list = new List<Entry>();
        int front = FrontmostPid();
        foreach (var p in Process.GetProcesses())
        {
            try
            {
                if (p.MainWindowHandle == IntPtr.Zero) continue;
                string title = p.MainWindowTitle;
                list.Add(new Entry
                {
                    AppId = p.ProcessName,
                    DisplayName = string.IsNullOrEmpty(title) ? p.ProcessName : title,
                    Path = TryPath(p),
                    Pid = p.Id,
                });
            }
            catch { /* access denied / exited */ }
        }
        list.Sort((a, b) =>
        {
            if (a.Pid == front) return -1;
            if (b.Pid == front) return 1;
            return string.Compare(a.DisplayName, b.DisplayName, StringComparison.OrdinalIgnoreCase);
        });
        return list;
    }

    // A real "installed apps" inventory: Start Menu shortcuts (per-user + all
    // users) plus the registry `App Paths` entries. This is the set a user can
    // actually launch by name (including LOCALIZED Start Menu names), unlike the
    // old stub that just returned running windows.
    public static List<Entry> Installed()
    {
        var byName = new Dictionary<string, Entry>(StringComparer.OrdinalIgnoreCase);

        foreach (var dir in new[]
        {
            SafeFolder(Environment.SpecialFolder.CommonStartMenu),
            SafeFolder(Environment.SpecialFolder.StartMenu),
        })
        {
            if (string.IsNullOrEmpty(dir) || !Directory.Exists(dir)) continue;
            // IgnoreInaccessible: Start Menu trees routinely contain a protected
            // subfolder; without this the whole enumeration throws (and the old
            // lazy form threw mid-iteration, so `installed` came back empty).
            var opts = new EnumerationOptions
            {
                RecurseSubdirectories = true,
                IgnoreInaccessible = true,
                AttributesToSkip = 0,
                MatchCasing = MatchCasing.CaseInsensitive,
            };
            try
            {
                foreach (var lnk in Directory.EnumerateFiles(dir, "*.lnk", opts))
                {
                    try
                    {
                        string name = System.IO.Path.GetFileNameWithoutExtension(lnk);
                        if (string.IsNullOrWhiteSpace(name)) continue;
                        if (!byName.ContainsKey(name))
                            byName[name] = new Entry { AppId = name, DisplayName = name, Path = lnk, Pid = 0 };
                    }
                    catch { /* unreadable shortcut */ }
                }
            }
            catch { /* keep whatever was enumerated */ }
        }

        foreach (var hive in new[] { Registry.LocalMachine, Registry.CurrentUser })
        {
            try
            {
                using var root = hive.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths");
                if (root == null) continue;
                foreach (var sub in root.GetSubKeyNames())
                {
                    try
                    {
                        using var key = root.OpenSubKey(sub);
                        if (key?.GetValue(null) is not string full || string.IsNullOrEmpty(full)) continue;
                        string name = System.IO.Path.GetFileNameWithoutExtension(sub);
                        if (string.IsNullOrWhiteSpace(name)) continue;
                        if (!byName.ContainsKey(name))
                            byName[name] = new Entry { AppId = name, DisplayName = name, Path = full, Pid = 0 };
                    }
                    catch { /* inaccessible entry */ }
                }
            }
            catch { /* inaccessible hive */ }
        }

        var list = new List<Entry>(byName.Values);
        list.Sort((a, b) => string.Compare(a.DisplayName, b.DisplayName, StringComparison.OrdinalIgnoreCase));
        return list;
    }

    private static string SafeFolder(Environment.SpecialFolder folder)
    {
        try { return Environment.GetFolderPath(folder); } catch { return ""; }
    }

    /// <summary>Start-Menu shortcut matching a (possibly localized) display name.</summary>
    private static string FindInstalledShortcut(string name)
    {
        foreach (var e in Installed())
        {
            if (string.Equals(e.DisplayName, name, StringComparison.OrdinalIgnoreCase) &&
                e.Path.EndsWith(".lnk", StringComparison.OrdinalIgnoreCase) && File.Exists(e.Path))
                return e.Path;
        }
        return null;
    }

    private static string StripExe(string s) =>
        s.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) ? s.Substring(0, s.Length - 4) : s;

    private static string TryFileName(string path)
    {
        try { return System.IO.Path.GetFileName(path) ?? ""; } catch { return ""; }
    }

    public static int ResolvePid(string selector)
    {
        if (string.IsNullOrWhiteSpace(selector)) return -1;
        string s = selector.Trim();
        if (int.TryParse(s, out int numeric) && numeric > 0) return numeric;
        // Process names never include ".exe" (e.g. "notepad"), so compare against a
        // suffix-stripped form too — otherwise `notepad.exe` never resolves.
        string bare = StripExe(s);

        var procs = Process.GetProcesses();
        // 1) exact process name (tolerating an optional .exe suffix)
        foreach (var p in procs) { try { if (string.Equals(p.ProcessName, s, StringComparison.OrdinalIgnoreCase) || string.Equals(p.ProcessName, bare, StringComparison.OrdinalIgnoreCase)) return p.Id; } catch { } }
        // 2) window title contains
        foreach (var p in procs) { try { if (!string.IsNullOrEmpty(p.MainWindowTitle) && p.MainWindowTitle.IndexOf(bare, StringComparison.OrdinalIgnoreCase) >= 0) return p.Id; } catch { } }
        // 3) process-name substring
        foreach (var p in procs) { try { if (p.ProcessName.IndexOf(bare, StringComparison.OrdinalIgnoreCase) >= 0) return p.Id; } catch { } }
        // 4) executable path (full path or file name)
        foreach (var p in procs)
        {
            try
            {
                string path = TryPath(p);
                if (string.Equals(path, s, StringComparison.OrdinalIgnoreCase)) return p.Id;
                string file = TryFileName(path);
                if (string.Equals(file, s, StringComparison.OrdinalIgnoreCase) || string.Equals(file, bare, StringComparison.OrdinalIgnoreCase)) return p.Id;
            }
            catch { }
        }
        return -1;
    }

    public static Dictionary<string, object> ResolveApp(string selector)
    {
        int pid = ResolvePid(selector);
        var d = new Dictionary<string, object> { ["selector"] = selector };
        if (pid > 0)
        {
            d["pid"] = pid;
            try
            {
                var p = Process.GetProcessById(pid);
                d["appId"] = p.ProcessName;
                d["bundleId"] = p.ProcessName;
                string path = TryPath(p);
                if (!string.IsNullOrEmpty(path)) d["path"] = path;
            }
            catch { /* ignore */ }
        }
        return d;
    }

    public static bool FocusApp(string selector, bool settle)
    {
        int pid = ResolvePid(selector);
        if (pid <= 0) throw new HelperError("no_target", $"app not running: {selector}");
        try
        {
            var p = Process.GetProcessById(pid);
            p.Refresh();
            if (p.MainWindowHandle != IntPtr.Zero)
            {
                NativeMethods.ActivateWindow(p.MainWindowHandle);
                if (settle) Thread.Sleep(200);
                return true;
            }
        }
        catch { /* ignore */ }
        return false;
    }

    // A small set of Windows shell aliases that are not real files on PATH and are
    // normally started via ShellExecute (Store/UWP stubs, protocol handlers).
    private static readonly HashSet<string> ShellAliases = new(StringComparer.OrdinalIgnoreCase)
    {
        "calc", "calc.exe", "notepad", "explorer", "control", "taskmgr", "mspaint",
        "snippingtool", "ms-settings", "shell:appsFolder",
    };

    /// <summary>Resolve an app token to a launchable executable path, or null.</summary>
    private static string FindExecutable(string app)
    {
        try
        {
            if (File.Exists(app)) return app;
            // A path-like token that does not exist: never hand it to the shell
            // (ShellExecute would pop a "Windows cannot find" dialog).
            if (app.IndexOfAny(new[] { '\\', '/' }) >= 0) return null;

            string name = app.EndsWith(".exe", StringComparison.OrdinalIgnoreCase) ? app : app + ".exe";

            foreach (var dir in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
            {
                try
                {
                    if (string.IsNullOrWhiteSpace(dir)) continue;
                    var candidate = Path.Combine(dir.Trim(), name);
                    if (File.Exists(candidate)) return candidate;
                }
                catch { /* bad PATH entry */ }
            }

            foreach (var hive in new[] { Registry.LocalMachine, Registry.CurrentUser })
            {
                try
                {
                    using var key = hive.OpenSubKey(@"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\" + name);
                    if (key?.GetValue(null) is string full && !string.IsNullOrEmpty(full) && File.Exists(full))
                        return full;
                }
                catch { /* inaccessible hive */ }
            }
        }
        catch { /* ignore */ }
        return null;
    }

    private static bool LooksLikeShellTarget(string app) =>
        app.Contains(':') || ShellAliases.Contains(app);

    /// <summary>
    /// Launch an app and return the pid of the running instance, or -1 when it
    /// could not be confirmed. Callers must treat 0/-1 as failure: the old helper
    /// returned true whenever Process.Start did not throw, which reported success
    /// even when Windows showed a "cannot find" dialog instead of launching.
    /// </summary>
    public static int Launch(string app)
    {
        if (string.IsNullOrWhiteSpace(app)) throw new HelperError("param_error", "launch requires app");
        app = app.Trim();

        var before = new HashSet<int>();
        foreach (var p in Process.GetProcesses()) { try { before.Add(p.Id); } catch { /* exited */ } }

        string exe = FindExecutable(app);
        bool viaShortcut = false;
        if (exe != null)
        {
            Start(exe, app);
        }
        else if (LooksLikeShellTarget(app))
        {
            Start(app, app);
        }
        else
        {
            // Localized Start Menu display name → launch its shortcut.
            string lnk = FindInstalledShortcut(app);
            if (lnk == null)
            {
                throw new HelperError(
                    "launch_failed",
                    $"could not resolve \"{app}\" to an executable",
                    "On Windows, launch_app takes an executable or App Paths entry (e.g. notepad, mspaint, " +
                    "calc.exe), a full path, or an installed Start Menu app name. Refusing to hand an unknown " +
                    "name to the shell.");
            }
            viaShortcut = true;
            Start(lnk, app);
        }

        // Confirm the app actually started and, if so, bring it to the foreground.
        // A Start-Menu launch may register under a process name unrelated to the
        // display name, so also accept a newly appeared windowed process.
        int pid = -1;
        for (int i = 0; i < 20; i++)
        {
            Thread.Sleep(200);
            pid = ResolvePid(app);
            if (pid > 0) break;
            if (viaShortcut) { pid = FindNewWindowedPid(before); if (pid > 0) break; }
        }
        if (pid > 0)
        {
            try
            {
                var p = Process.GetProcessById(pid);
                p.Refresh();
                if (p.MainWindowHandle != IntPtr.Zero) NativeMethods.ActivateWindow(p.MainWindowHandle);
            }
            catch { /* exited already */ }
        }
        return pid;
    }

    private static void Start(string target, string app)
    {
        try { Process.Start(new ProcessStartInfo(target) { UseShellExecute = true }); }
        catch (Exception e) { throw new HelperError("launch_failed", $"could not launch \"{app}\": {e.Message}"); }
    }

    private static int FindNewWindowedPid(HashSet<int> before)
    {
        foreach (var p in Process.GetProcesses())
        {
            try { if (!before.Contains(p.Id) && p.MainWindowHandle != IntPtr.Zero) return p.Id; }
            catch { /* access denied / exited */ }
        }
        return -1;
    }
}
