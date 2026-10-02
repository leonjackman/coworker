// Monitors.cs — physical monitor enumeration (for DIP -> physical mapping).
//
// Under PerMonitorV2 the helper sees physical pixels; Electron reports display
// bounds in DIP. The WinDriver pairs Electron displays with these monitors by
// index and maps a DIP point to physical by preserving the position *fraction*
// within the monitor, so the absolute DIP origin never has to match.

using System.Runtime.InteropServices;

namespace CwAutomaWin;

internal static class Monitors
{
    [StructLayout(LayoutKind.Sequential)]
    private struct RECT { public int left, top, right, bottom; }

    [StructLayout(LayoutKind.Sequential, CharSet = CharSet.Unicode)]
    private struct MONITORINFOEX
    {
        public int cbSize;
        public RECT rcMonitor;
        public RECT rcWork;
        public uint dwFlags;
        [MarshalAs(UnmanagedType.ByValTStr, SizeConst = 32)] public string szDevice;
    }

    private delegate bool MonitorEnumProc(IntPtr hMonitor, IntPtr hdc, IntPtr lprcClip, IntPtr dwData);

    [DllImport("user32.dll")]
    private static extern bool EnumDisplayMonitors(IntPtr hdc, IntPtr lprcClip, MonitorEnumProc lpfnEnum, IntPtr dwData);

    [DllImport("user32.dll", CharSet = CharSet.Unicode)]
    private static extern bool GetMonitorInfo(IntPtr hMonitor, ref MONITORINFOEX lpmi);

    [DllImport("shcore.dll")]
    private static extern int GetDpiForMonitor(IntPtr hMonitor, int dpiType, out uint dpiX, out uint dpiY);

    private const int MDT_EFFECTIVE_DPI = 0;

    public static List<Dictionary<string, object>> Enumerate()
    {
        var list = new List<Dictionary<string, object>>();
        int index = 0;
        EnumDisplayMonitors(IntPtr.Zero, IntPtr.Zero, (hMonitor, hdc, clip, data) =>
        {
            var mi = new MONITORINFOEX { cbSize = Marshal.SizeOf<MONITORINFOEX>() };
            if (GetMonitorInfo(hMonitor, ref mi))
            {
                uint dpi = 96;
                try { if (GetDpiForMonitor(hMonitor, MDT_EFFECTIVE_DPI, out var dx, out _) == 0 && dx > 0) dpi = dx; }
                catch { /* shcore unavailable: assume 96 */ }
                double scale = dpi / 96.0;
                int w = mi.rcMonitor.right - mi.rcMonitor.left;
                int h = mi.rcMonitor.bottom - mi.rcMonitor.top;
                list.Add(new Dictionary<string, object>
                {
                    ["index"] = index,
                    ["id"] = mi.szDevice ?? "",
                    ["primary"] = (mi.dwFlags & 1) != 0,
                    ["bounds"] = new Dictionary<string, object>
                    {
                        ["x"] = mi.rcMonitor.left,
                        ["y"] = mi.rcMonitor.top,
                        ["width"] = w,
                        ["height"] = h,
                    },
                    ["scale_factor"] = scale,
                    ["internal"] = false,
                });
                index++;
            }
            return true;
        }, IntPtr.Zero);
        return list;
    }
}
