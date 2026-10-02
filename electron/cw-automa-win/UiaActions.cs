// UiaActions.cs — semantic actuation by element ref (mirrors macOS handleAct).
//
// Pattern-first (Invoke/Value/Toggle/SelectionItem), coordinate SendInput only
// as a fallback for canvas/custom-drawn controls.

using System.Drawing;
using System.Text.Json;
using FlaUI.Core.AutomationElements;
using FlaUI.UIA3;

namespace CwAutomaWin;

internal static class UiaActions
{
    public static Dictionary<string, object> Act(UIA3Automation automation, UiaTree tree, int pid, string reference, string op, JsonElement paramsEl)
    {
        if (string.IsNullOrEmpty(reference)) throw new HelperError("param_error", "act requires a ref");
        Elevation.EnsureNotBlocked(pid);
        var el = tree.Find(pid, reference)
                 ?? throw new HelperError("computer_error", $"no UIA element for ref {reference} in pid {pid}");

        var rect = UiaTree.RectOf(el);
        Point center = rect.IsEmpty
            ? Point.Empty
            : new Point(rect.Left + rect.Width / 2, rect.Top + rect.Height / 2);

        var result = new Dictionary<string, object> { ["performed"] = op };

        switch (op)
        {
            case "click":
            {
                var inv = SafeInvoke(el);
                if (inv != null) { try { inv.Invoke(); result["via"] = "invoke"; } catch { inv = null; } }
                if (inv == null)
                {
                    if (rect.IsEmpty) throw new HelperError("computer_error", "element has no bounds for coordinate fallback");
                    Input.Click(center.X, center.Y, "left");
                    result["via"] = "coords-fallback";
                }
                break;
            }
            case "double":
            {
                if (rect.IsEmpty) throw new HelperError("computer_error", "element has no bounds");
                Input.Click(center.X, center.Y, "double");
                result["via"] = "coords";
                break;
            }
            case "right":
            {
                if (rect.IsEmpty) throw new HelperError("computer_error", "element has no bounds");
                Input.Click(center.X, center.Y, "right");
                result["via"] = "coords";
                break;
            }
            case "set_value":
            {
                string value = Params.Str(paramsEl, "value");
                var vp = SafeValue(el);
                if (vp != null) { vp.SetValue(value); result["strategy"] = "value-pattern"; }
                else { FocusElement(el); Input.TypeUnicode(value); result["strategy"] = "focus+type"; }
                break;
            }
            case "focus":
            {
                bool ok = FocusElement(el);
                result["focused"] = ok;
                break;
            }
            case "show":
            {
                try
                {
                    dynamic si = el.Patterns.ScrollItem.PatternOrDefault;
                    if (si != null) si.ScrollIntoView();
                }
                catch { /* ignore */ }
                try { el.Focus(); } catch { /* ignore */ }
                result["performed"] = "show";
                break;
            }
            case "type_into":
            {
                string text = Params.Str(paramsEl, "text");
                bool submit = Params.Bool(paramsEl, "submit");
                var outcome = TextInput.Enter(el, pid, text, submit);
                result["performed"] = "type_into";
                result["submit"] = submit;
                result["strategy"] = outcome.Strategy;
                result["verified"] = outcome.Verified;
                result["value"] = outcome.Value ?? "";
                break;
            }
            default:
                throw new HelperError("computer_error", $"unsupported op {op}");
        }

        TryActivate(pid);
        return result;
    }

    public static bool FocusElement(AutomationElement el)
    {
        try { el.Focus(); return true; } catch { return false; }
    }

    private static dynamic SafeInvoke(AutomationElement el)
    {
        try { return el.Patterns.Invoke.PatternOrDefault; } catch { return null; }
    }

    private static dynamic SafeValue(AutomationElement el)
    {
        try { return el.Patterns.Value.PatternOrDefault; } catch { return null; }
    }

    public static void TryActivate(int pid)
    {
        try
        {
            var proc = System.Diagnostics.Process.GetProcessById(pid);
            proc.Refresh();
            if (proc.MainWindowHandle != IntPtr.Zero) NativeMethods.ActivateWindow(proc.MainWindowHandle);
        }
        catch { /* ignore */ }
    }
}
