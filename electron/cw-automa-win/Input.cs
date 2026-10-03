// Input.cs — Win32 SendInput wrapper + key mapping.
//
// All synthetic input on Windows goes through SendInput (physical pixels,
// virtual-desktop absolute coords). Unlike macOS CGEvent.postToPid there is no
// per-process posting, so coordinate fallback MOVES the real cursor. The
// structure-first UIA path (UiaActions) avoids the cursor entirely; coordinate
// actions are the last resort.

using System.Runtime.InteropServices;

namespace CwAutomaWin;

internal static class Input
{
    // ── Win32 ────────────────────────────────────────────────────────────
    [StructLayout(LayoutKind.Sequential)]
    private struct MOUSEINPUT { public int dx, dy; public uint mouseData, dwFlags, time; public IntPtr dwExtraInfo; }

    [StructLayout(LayoutKind.Sequential)]
    private struct KEYBDINPUT { public ushort wVk, wScan; public uint dwFlags, time; public IntPtr dwExtraInfo; }

    [StructLayout(LayoutKind.Sequential)]
    private struct HARDWAREINPUT { public uint uMsg; public ushort wParamL, wParamH; }

    [StructLayout(LayoutKind.Explicit)]
    private struct InputUnion
    {
        [FieldOffset(0)] public MOUSEINPUT mi;
        [FieldOffset(0)] public KEYBDINPUT ki;
        [FieldOffset(0)] public HARDWAREINPUT hi;
    }

    [StructLayout(LayoutKind.Sequential)]
    private struct INPUT { public uint type; public InputUnion U; }

    [StructLayout(LayoutKind.Sequential)]
    public struct POINT { public int X; public int Y; }

    [DllImport("user32.dll", SetLastError = true)]
    private static extern uint SendInput(uint nInputs, INPUT[] pInputs, int cbSize);

    [DllImport("user32.dll")]
    private static extern bool GetCursorPos(out POINT p);

    [DllImport("user32.dll")]
    private static extern int GetSystemMetrics(int nIndex);

    [DllImport("user32.dll")]
    private static extern short VkKeyScan(char ch);

    private const uint INPUT_MOUSE = 0, INPUT_KEYBOARD = 1;
    private const uint MOUSEEVENTF_MOVE = 0x0001, MOUSEEVENTF_LEFTDOWN = 0x0002, MOUSEEVENTF_LEFTUP = 0x0004;
    private const uint MOUSEEVENTF_RIGHTDOWN = 0x0008, MOUSEEVENTF_RIGHTUP = 0x0010;
    private const uint MOUSEEVENTF_MIDDLEDOWN = 0x0020, MOUSEEVENTF_MIDDLEUP = 0x0040;
    private const uint MOUSEEVENTF_WHEEL = 0x0800, MOUSEEVENTF_HWHEEL = 0x1000;
    private const uint MOUSEEVENTF_ABSOLUTE = 0x8000, MOUSEEVENTF_VIRTUALDESK = 0x4000;
    private const uint KEYEVENTF_KEYUP = 0x0002, KEYEVENTF_UNICODE = 0x0004;

    private const int SM_XVIRTUALSCREEN = 76, SM_YVIRTUALSCREEN = 77;
    private const int SM_CXVIRTUALSCREEN = 78, SM_CYVIRTUALSCREEN = 79;

    public static POINT GetCursor() { GetCursorPos(out var p); return p; }

    private static INPUT Mouse(int dx, int dy, uint flags, uint data = 0) => new()
    {
        type = INPUT_MOUSE,
        U = new InputUnion { mi = new MOUSEINPUT { dx = dx, dy = dy, mouseData = data, dwFlags = flags, time = 0, dwExtraInfo = IntPtr.Zero } }
    };

    private static INPUT Key(ushort vk, ushort scan, uint flags) => new()
    {
        type = INPUT_KEYBOARD,
        U = new InputUnion { ki = new KEYBDINPUT { wVk = vk, wScan = scan, dwFlags = flags, time = 0, dwExtraInfo = IntPtr.Zero } }
    };

    private static void Send(params INPUT[] inputs)
    {
        uint inserted = SendInput((uint)inputs.Length, inputs, Marshal.SizeOf<INPUT>());
        if (inserted == 0 && Marshal.GetLastWin32Error() == 5)
        {
            throw new HelperError(
                "uipi_blocked",
                "input injection was blocked by UIPI; the foreground window is elevated",
                "Run CoWorker as administrator to control elevated windows.");
        }
    }

    private static (int, int) Normalize(int x, int y)
    {
        int vx = GetSystemMetrics(SM_XVIRTUALSCREEN), vy = GetSystemMetrics(SM_YVIRTUALSCREEN);
        int vw = Math.Max(1, GetSystemMetrics(SM_CXVIRTUALSCREEN));
        int vh = Math.Max(1, GetSystemMetrics(SM_CYVIRTUALSCREEN));
        int nx = (int)Math.Round((x - vx) * 65535.0 / (vw - 1));
        int ny = (int)Math.Round((y - vy) * 65535.0 / (vh - 1));
        return (nx, ny);
    }

    public static void MoveTo(int x, int y)
    {
        var (nx, ny) = Normalize(x, y);
        Send(Mouse(nx, ny, MOUSEEVENTF_MOVE | MOUSEEVENTF_ABSOLUTE | MOUSEEVENTF_VIRTUALDESK));
    }

    /// kind: left | right | double | middle
    public static void Click(int x, int y, string kind)
    {
        MoveTo(x, y);
        uint down = MOUSEEVENTF_LEFTDOWN, up = MOUSEEVENTF_LEFTUP;
        if (kind == "right") { down = MOUSEEVENTF_RIGHTDOWN; up = MOUSEEVENTF_RIGHTUP; }
        else if (kind == "middle") { down = MOUSEEVENTF_MIDDLEDOWN; up = MOUSEEVENTF_MIDDLEUP; }
        int times = kind == "double" ? 2 : 1;
        for (int i = 0; i < times; i++)
        {
            Send(Mouse(0, 0, down), Mouse(0, 0, up));
            if (times > 1) Thread.Sleep(40);
        }
    }

    public static void Drag(int x1, int y1, int x2, int y2, int steps, string button = "left")
    {
        steps = Math.Max(1, steps);
        MoveTo(x1, y1);
        uint down = button == "right" ? MOUSEEVENTF_RIGHTDOWN : MOUSEEVENTF_LEFTDOWN;
        uint up = button == "right" ? MOUSEEVENTF_RIGHTUP : MOUSEEVENTF_LEFTUP;
        Send(Mouse(0, 0, down));
        for (int i = 1; i <= steps; i++)
        {
            int x = x1 + (x2 - x1) * i / steps;
            int y = y1 + (y2 - y1) * i / steps;
            MoveTo(x, y);
            Thread.Sleep(6);
        }
        Send(Mouse(0, 0, up));
    }

    /// Scroll at the current cursor position. Positive dy scrolls down.
    public static void Scroll(int dx, int dy)
    {
        const int WHEEL_DELTA = 120;
        if (dy != 0) Send(Mouse(0, 0, MOUSEEVENTF_WHEEL, unchecked((uint)(-dy))));
        if (dx != 0) Send(Mouse(0, 0, MOUSEEVENTF_HWHEEL, unchecked((uint)(dx))));
        _ = WHEEL_DELTA;
    }

    public static void ScrollAt(int x, int y, int dx, int dy)
    {
        MoveTo(x, y);
        Scroll(dx, dy);
    }

    // ── Keyboard ─────────────────────────────────────────────────────────
    public static bool TryGetVk(string token, out ushort vk, out bool shift)
    {
        vk = 0; shift = false;
        if (string.IsNullOrEmpty(token)) return false;
        string t = token.Trim().ToLowerInvariant();
        switch (t)
        {
            case "cmd": case "command": case "win": case "super": case "meta": case "windows": vk = 0x5B; return true; // VK_LWIN
            case "ctrl": case "control": vk = 0x11; return true;
            case "alt": case "option": vk = 0x12; return true;
            case "shift": vk = 0x10; return true;
            case "space": vk = 0x20; return true;
            case "enter": case "return": vk = 0x0D; return true;
            case "tab": vk = 0x09; return true;
            case "escape": case "esc": vk = 0x1B; return true;
            case "backspace": vk = 0x08; return true;
            case "delete": case "del": vk = 0x2E; return true;
            case "home": vk = 0x24; return true;
            case "end": vk = 0x23; return true;
            case "pageup": case "pgup": vk = 0x21; return true;
            case "pagedown": case "pgdn": vk = 0x22; return true;
            case "up": vk = 0x26; return true;
            case "down": vk = 0x28; return true;
            case "left": vk = 0x25; return true;
            case "right": vk = 0x27; return true;
            case "capslock": vk = 0x14; return true;
            case "insert": case "ins": vk = 0x2D; return true;
            case "printscreen": case "prtsc": vk = 0x2C; return true;
        }
        if (t.Length >= 2 && t[0] == 'f' && int.TryParse(t.Substring(1), out var fn) && fn >= 1 && fn <= 24)
        {
            vk = (ushort)(0x70 + fn - 1); return true;
        }
        if (t.Length == 1)
        {
            char c = t[0];
            if (c >= 'a' && c <= 'z') { vk = (ushort)(0x41 + (c - 'a')); return true; }
            if (c >= '0' && c <= '9') { vk = (ushort)(0x30 + (c - '0')); return true; }
            short vs = VkKeyScan(c);
            if (vs != -1) { vk = (ushort)(vs & 0xFF); shift = (vs & 0x100) != 0; return true; }
        }
        return false;
    }

    public static void PressCombo(string key, IReadOnlyList<string> modifiers, int repeat = 1)
    {
        if (!TryGetVk(key, out var vk, out var keyShift)) throw new HelperError("param_error", $"unknown key \"{key}\"");
        var modVks = new List<ushort>();
        foreach (var m in modifiers)
        {
            if (TryGetVk(m, out var mv, out _)) modVks.Add(mv);
        }
        repeat = Math.Max(1, repeat);
        for (int i = 0; i < repeat; i++)
        {
            foreach (var mv in modVks) Send(Key(mv, 0, 0));
            if (keyShift) Send(Key(0x10, 0, 0));
            Send(Key(vk, 0, 0));
            Send(Key(vk, 0, KEYEVENTF_KEYUP));
            if (keyShift) Send(Key(0x10, 0, KEYEVENTF_KEYUP));
            for (int j = modVks.Count - 1; j >= 0; j--) Send(Key(modVks[j], 0, KEYEVENTF_KEYUP));
            Thread.Sleep(10);
        }
    }

    /// Type arbitrary text via Unicode key events (layout-independent).
    ///
    /// Iterates by Unicode SCALAR, not by UTF-16 code unit: a surrogate pair
    /// (emoji, rare CJK) is emitted as high+low surrogate in ONE SendInput call
    /// so the target composes it. Sending the two halves in separate calls (the
    /// old behavior) left many apps with two replacement glyphs.
    public static void TypeUnicode(string text)
    {
        if (string.IsNullOrEmpty(text)) return;
        int i = 0;
        while (i < text.Length)
        {
            if (char.IsHighSurrogate(text[i]) && i + 1 < text.Length && char.IsLowSurrogate(text[i + 1]))
            {
                ushort hi = text[i], lo = text[i + 1];
                Send(
                    Key(0, hi, KEYEVENTF_UNICODE), Key(0, hi, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP),
                    Key(0, lo, KEYEVENTF_UNICODE), Key(0, lo, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));
                i += 2;
            }
            else
            {
                ushort u = text[i];
                Send(Key(0, u, KEYEVENTF_UNICODE), Key(0, u, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP));
                i += 1;
            }
        }
    }
}
