// UiaTree.cs — UI Automation perception, mirroring macOS AXTree.swift.
//
// Refs are `sig#n` where sig = normalized "controlType:name", n is that sig's
// occurrence ordinal — an IDENTITY, not a positional counter. Traversal is
// scoped to the app's top-level window and skips noise so refs do not churn.

using System.Drawing;
using FlaUI.Core.AutomationElements;
using FlaUI.Core.Definitions;
using FlaUI.UIA3;

namespace CwAutomaWin;

internal sealed class UiaNode
{
    public string Ref;
    public string Sig;
    public string Role;
    public string Label;
    public string Value;
    public bool Enabled;
    public string Position;
    public string Size;
    public List<UiaNode> Children = new();
}

internal sealed class UiaTree
{
    private readonly UIA3Automation _automation;
    public const int TreeDepth = 12;

    public UiaTree(UIA3Automation automation) { _automation = automation; }

    private static readonly HashSet<ControlType> Interactive = new()
    {
        ControlType.Button, ControlType.CheckBox, ControlType.RadioButton, ControlType.Edit,
        ControlType.ComboBox, ControlType.ListItem, ControlType.MenuItem, ControlType.TabItem,
        ControlType.Hyperlink, ControlType.Spinner, ControlType.Slider, ControlType.SplitButton,
        ControlType.TreeItem, ControlType.DataItem, ControlType.Text, ControlType.Window,
        ControlType.Document, ControlType.HeaderItem, ControlType.Custom,
    };

    private static readonly HashSet<ControlType> Noise = new()
    {
        ControlType.ScrollBar, ControlType.TitleBar, ControlType.MenuBar, ControlType.ToolBar, ControlType.StatusBar,
    };

    private static string Normalize(string s)
    {
        if (string.IsNullOrEmpty(s)) return "";
        var parts = s.Split((char[])null, StringSplitOptions.RemoveEmptyEntries);
        return string.Join(" ", parts).ToLowerInvariant();
    }

    public static string LabelOf(AutomationElement el)
    {
        try { return el.Properties.Name.ValueOrDefault ?? ""; } catch { return ""; }
    }

    public static string ValueOf(AutomationElement el)
    {
        try { return el.Patterns.Value.PatternOrDefault?.Value ?? ""; } catch { return ""; }
    }

    public static bool EnabledOf(AutomationElement el)
    {
        try { return el.Properties.IsEnabled.ValueOrDefault; } catch { return true; }
    }

    public static Rectangle RectOf(AutomationElement el)
    {
        try { return el.Properties.BoundingRectangle.ValueOrDefault; } catch { return Rectangle.Empty; }
    }

    private static string Sig(AutomationElement el)
    {
        string role = "";
        try { role = el.ControlType.ToString(); } catch { /* ignore */ }
        string label = LabelOf(el);
        if (string.IsNullOrEmpty(label)) { try { label = el.Properties.AutomationId.ValueOrDefault ?? ""; } catch { label = ""; } }
        return Normalize(role + ":" + label);
    }

    private static bool IsZeroSize(AutomationElement el)
    {
        var r = RectOf(el);
        return r.Width <= 1 && r.Height <= 1;
    }

    /// Top-level window for a process: foreground window if it matches, else the
    /// first Window-typed top-level element owned by the pid.
    public AutomationElement FindWindow(int pid)
    {
        try
        {
            IntPtr fg = NativeMethods.GetForegroundWindow();
            NativeMethods.GetWindowThreadProcessId(fg, out uint fgPid);
            if ((int)fgPid == pid && fg != IntPtr.Zero)
            {
                var el = _automation.FromHandle(fg);
                if (el != null) return el;
            }
        }
        catch { /* fall through */ }

        var desktop = _automation.GetDesktop();
        AutomationElement[] children;
        try { children = desktop.FindAllChildren(cf => cf.ByProcessId(pid)); }
        catch { return null; }

        AutomationElement best = null;
        int bestArea = -1;
        foreach (var c in children)
        {
            ControlType ct;
            try { ct = c.ControlType; } catch { continue; }
            if (ct != ControlType.Window) continue;
            var r = RectOf(c);
            int area = r.Width * r.Height;
            if (area > bestArea) { best = c; bestArea = area; }
        }
        return best ?? (children.Length > 0 ? children[0] : null);
    }

    public UiaNode Build(AutomationElement el, bool isRoot, int depth, Dictionary<string, int> seq, ref int refCount)
    {
        ControlType? ct = null;
        try { ct = el.ControlType; } catch { return null; }
        if (ct == null) return null;
        if (!isRoot && (Noise.Contains(ct.Value) || IsZeroSize(el))) return null;

        string signature = Sig(el);
        int ord = 0;
        if (!isRoot)
        {
            seq.TryGetValue(signature, out ord);
            ord += 1;
            seq[signature] = ord;
        }

        var children = new List<UiaNode>();
        if (depth > 0)
        {
            try
            {
                var walker = _automation.TreeWalkerFactory.GetControlViewWalker();
                var child = walker.GetFirstChild(el);
                while (child != null)
                {
                    var node = Build(child, false, depth - 1, seq, ref refCount);
                    if (node != null) children.Add(node);
                    child = walker.GetNextSibling(child);
                }
            }
            catch { /* ignore a broken subtree */ }
        }

        bool keep = isRoot || Interactive.Contains(ct.Value) || children.Count > 0;
        if (!keep) return null;
        refCount++;

        var rect = RectOf(el);
        return new UiaNode
        {
            Ref = isRoot ? "window" : $"{signature}#{ord}",
            Sig = signature,
            Role = ct.Value.ToString(),
            Label = LabelOf(el),
            Value = ValueOf(el),
            Enabled = EnabledOf(el),
            Position = rect.IsEmpty ? "" : $"({rect.Left},{rect.Top})",
            Size = rect.IsEmpty ? "" : $"{rect.Width}x{rect.Height}",
            Children = children,
        };
    }

    public static Dictionary<string, object> Dict(UiaNode n)
    {
        var d = new Dictionary<string, object>
        {
            ["ref"] = n.Ref, ["sig"] = n.Sig, ["role"] = n.Role, ["label"] = n.Label,
            ["value"] = n.Value, ["enabled"] = n.Enabled,
            ["position"] = n.Position, ["size"] = n.Size,
        };
        if (n.Children.Count > 0) d["children"] = n.Children.Select(Dict).ToList();
        return d;
    }

    public static void Flatten(UiaNode n, Dictionary<string, UiaNode> map)
    {
        map[n.Ref] = n;
        foreach (var c in n.Children) Flatten(c, map);
    }

    public static void Render(UiaNode n, List<string> lines, int indent, int maxLines)
    {
        if (lines.Count >= maxLines) return;
        var line = new string(' ', indent * 2) + $"[{n.Ref}] {n.Role}";
        if (!string.IsNullOrEmpty(n.Label)) line += $" \"{n.Label}\"";
        if (!string.IsNullOrEmpty(n.Value)) line += $" value=\"{n.Value}\"";
        if (!string.IsNullOrEmpty(n.Position)) line += $" at {n.Position}";
        lines.Add(line);
        foreach (var c in n.Children) Render(c, lines, indent + 1, maxLines);
    }

    /// Re-locate the element named by `ref` against the CURRENT window tree.
    public AutomationElement Find(int pid, string reference)
    {
        var win = FindWindow(pid);
        if (win == null) return null;
        var parts = reference.Split('#');
        if (parts.Length != 2 || !int.TryParse(parts[1], out int want)) return null;
        string sigRef = parts[0];
        int seq = 0;
        AutomationElement result = null;

        var walker = _automation.TreeWalkerFactory.GetControlViewWalker();
        bool Scan(AutomationElement el, int depth, bool isRoot)
        {
            if (el == null) return false;
            ControlType ct;
            try { ct = el.ControlType; } catch { return false; }
            bool noisy = !isRoot && (Noise.Contains(ct) || IsZeroSize(el));
            if (!isRoot && !noisy && sigRef == Sig(el))
            {
                seq++;
                if (seq == want) { result = el; return true; }
            }
            if (depth > 0)
            {
                try
                {
                    var child = walker.GetFirstChild(el);
                    while (child != null)
                    {
                        if (Scan(child, depth - 1, false)) return true;
                        child = walker.GetNextSibling(child);
                    }
                }
                catch { /* ignore */ }
            }
            return false;
        }
        Scan(win, TreeDepth, true);
        return result;
    }

    public string Signature(int pid, int depth = 6)
    {
        var win = FindWindow(pid);
        if (win == null) return null;
        var seq = new Dictionary<string, int>();
        int refCount = 0;
        var root = Build(win, true, depth, seq, ref refCount);
        if (root == null) return null;
        var map = new Dictionary<string, UiaNode>();
        Flatten(root, map);
        var parts = map.Select(kv => $"{kv.Key}={kv.Value.Label}\u0001{kv.Value.Value}").OrderBy(s => s, StringComparer.Ordinal);
        return $"{map.Count}:" + string.Join("|", parts);
    }

    public bool WaitSettled(int pid, int quietMs, int timeoutMs)
    {
        var deadline = DateTime.UtcNow.AddMilliseconds(Math.Max(0, timeoutMs));
        string last = Signature(pid);
        var lastChange = DateTime.UtcNow;
        while (DateTime.UtcNow < deadline)
        {
            Thread.Sleep(50);
            var now = Signature(pid);
            if (now != last) { last = now; lastChange = DateTime.UtcNow; continue; }
            if ((DateTime.UtcNow - lastChange).TotalMilliseconds >= quietMs) return true;
        }
        return false;
    }
}
