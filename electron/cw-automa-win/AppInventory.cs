// AppInventory.cs — running/installed app discovery, resolve, launch, focus.
// Mirrors macOS AppInventory.swift with Windows process/window semantics.

using System.Diagnostics;

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

    // Windows has no cheap "installed apps" inventory; return running windows.
    public static List<Entry> Installed() => Running();

    public static int ResolvePid(string selector)
    {
        if (string.IsNullOrWhiteSpace(selector)) return -1;
        string s = selector.Trim();
        if (int.TryParse(s, out int numeric) && numeric > 0) return numeric;

        var procs = Process.GetProcesses();
        // 1) exact process name
        foreach (var p in procs) { try { if (string.Equals(p.ProcessName, s, StringComparison.OrdinalIgnoreCase)) return p.Id; } catch { } }
        // 2) window title contains
        foreach (var p in procs) { try { if (!string.IsNullOrEmpty(p.MainWindowTitle) && p.MainWindowTitle.IndexOf(s, StringComparison.OrdinalIgnoreCase) >= 0) return p.Id; } catch { } }
        // 3) process-name suffix / substring
        foreach (var p in procs) { try { if (p.ProcessName.IndexOf(s, StringComparison.OrdinalIgnoreCase) >= 0) return p.Id; } catch { } }
        // 4) executable path
        foreach (var p in procs) { try { if (string.Equals(TryPath(p), s, StringComparison.OrdinalIgnoreCase)) return p.Id; } catch { } }
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

    public static bool Launch(string app)
    {
        if (string.IsNullOrWhiteSpace(app)) throw new HelperError("param_error", "launch requires app");
        try
        {
            Process.Start(new ProcessStartInfo(app) { UseShellExecute = true });
            Thread.Sleep(300);
            return true;
        }
        catch
        {
            try
            {
                Process.Start(new ProcessStartInfo("cmd.exe", $"/c start \"\" \"{app}\"") { UseShellExecute = false, CreateNoWindow = true });
                Thread.Sleep(300);
                return true;
            }
            catch (Exception e)
            {
                throw new HelperError("launch_failed", $"could not launch \"{app}\": {e.Message}");
            }
        }
    }
}
