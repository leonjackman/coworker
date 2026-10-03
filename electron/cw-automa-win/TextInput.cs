// TextInput.cs — text-entry ladder (mirrors macOS TextInput.swift).
//
//   1. UIA ValuePattern          — most deterministic when the field supports it
//   2. focus + Unicode key events — layout-independent typing
//   3. clipboard paste (Ctrl+V)   — last resort / when no element ref is known
//
// Each strategy reports what it did and whether a readback confirmed it.

using FlaUI.Core.AutomationElements;
using System.Windows.Forms;

namespace CwAutomaWin;

internal sealed class TextOutcome
{
    public string Strategy = "none";
    public bool Verified;
    public string Value = "";
}

internal static class TextInput
{
    public static TextOutcome Enter(AutomationElement el, int pid, string text, bool submit)
    {
        var outcome = new TextOutcome();

        if (el != null)
        {
            dynamic vp = null;
            try { vp = el.Patterns.Value.PatternOrDefault; } catch { vp = null; }
            if (vp != null)
            {
                try
                {
                    vp.SetValue(text);
                    outcome.Strategy = "value-pattern";
                    outcome.Value = UiaTree.ValueOf(el);
                    outcome.Verified = outcome.Value == text;
                    return Finish(outcome, submit);
                }
                catch { /* fall through to keyboard */ }
            }

            try { el.Focus(); } catch { /* ignore */ }
            Thread.Sleep(80);
            Input.TypeUnicode(text);
            outcome.Strategy = "keyboard";
            Thread.Sleep(60);
            outcome.Value = UiaTree.ValueOf(el);
            outcome.Verified = outcome.Value == text;
            return Finish(outcome, submit);
        }

        // No element ref: paste into whatever holds focus in the target app.
        PasteText(text);
        outcome.Strategy = "clipboard-paste";
        outcome.Verified = true;
        return Finish(outcome, submit);
    }

    private static TextOutcome Finish(TextOutcome outcome, bool submit)
    {
        if (submit)
        {
            Input.PressCombo("enter", Array.Empty<string>(), 1);
            Thread.Sleep(80);
        }
        return outcome;
    }

    // The user's prior clipboard TEXT is captured and restored after the paste so
    // agent typing does not silently destroy it (mirrors macOS Clipboard.paste).
    // Only text is restorable via WinForms Clipboard; a prior image/non-text
    // clipboard cannot be preserved and is left as the pasted text.
    public static void PasteText(string text)
    {
        string prev = null;
        bool hadText = false;
        try { hadText = Clipboard.ContainsText(); if (hadText) prev = Clipboard.GetText(); }
        catch { /* clipboard locked by another process */ }

        try { Clipboard.SetText(text ?? ""); }
        catch { Thread.Sleep(60); try { Clipboard.SetText(text ?? ""); } catch { } }
        Input.PressCombo("v", new[] { "ctrl" }, 1);
        Thread.Sleep(120); // let the target read the clipboard before restoring

        if (hadText && prev != null)
        {
            try { Clipboard.SetText(prev); } catch { /* leave pasted text */ }
        }
    }
}
