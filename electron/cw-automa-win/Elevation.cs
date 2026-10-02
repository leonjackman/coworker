// Elevation.cs — User Interface Privilege Isolation (UIPI) detection.
//
// Windows blocks a medium-integrity process from reading or driving windows that
// belong to a higher-integrity (elevated) process. UIA element access and UIA
// control patterns fail with access-denied, and SendInput is silently dropped.
// Rather than let those surface as generic failures, this reports a clear
// `uipi_blocked` error with a hint to run CoWorker elevated.

using System.Runtime.InteropServices;

namespace CwAutomaWin;

internal static class Elevation
{
    [DllImport("kernel32.dll")]
    private static extern IntPtr GetCurrentProcess();

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern IntPtr OpenProcess(uint dwDesiredAccess, bool bInheritHandle, int dwProcessId);

    [DllImport("kernel32.dll", SetLastError = true)]
    private static extern bool CloseHandle(IntPtr hObject);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool OpenProcessToken(IntPtr processHandle, uint desiredAccess, out IntPtr tokenHandle);

    [DllImport("advapi32.dll", SetLastError = true)]
    private static extern bool GetTokenInformation(IntPtr tokenHandle, int tokenInformationClass, out int tokenInformation, uint tokenInformationLength, out uint returnLength);

    private const uint PROCESS_QUERY_LIMITED_INFORMATION = 0x1000;
    private const uint TOKEN_QUERY = 0x0008;
    private const int TokenElevation = 20;
    private const int ERROR_ACCESS_DENIED = 5;

    private static readonly Lazy<bool> SelfElevatedLazy = new(() => TokenElevated(GetCurrentProcess()));

    public static bool SelfElevated => SelfElevatedLazy.Value;

    private static bool TokenElevated(IntPtr process)
    {
        if (!OpenProcessToken(process, TOKEN_QUERY, out var token)) return false;
        try
        {
            if (!GetTokenInformation(token, TokenElevation, out int elevated, sizeof(int), out _)) return false;
            return elevated != 0;
        }
        finally { CloseHandle(token); }
    }

    /// True when this (non-elevated) helper cannot drive `pid` because the target
    /// runs at a higher integrity level. Unknown/denied cases are treated as
    /// blocked only when access is denied, to avoid false positives.
    public static bool IsBlocked(int pid)
    {
        if (pid <= 0 || SelfElevated) return false;

        IntPtr process = OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, false, pid);
        if (process == IntPtr.Zero)
        {
            // Can't even open a query handle: elevated or protected process.
            return Marshal.GetLastWin32Error() == ERROR_ACCESS_DENIED;
        }
        try
        {
            if (!OpenProcessToken(process, TOKEN_QUERY, out var token))
            {
                // Token of a higher-integrity process is not readable.
                return Marshal.GetLastWin32Error() == ERROR_ACCESS_DENIED;
            }
            try
            {
                if (!GetTokenInformation(token, TokenElevation, out int elevated, sizeof(int), out _)) return false;
                return elevated != 0;
            }
            finally { CloseHandle(token); }
        }
        finally { CloseHandle(process); }
    }

    /// Throw `uipi_blocked` when the target cannot be driven from this integrity level.
    public static void EnsureNotBlocked(int pid)
    {
        if (IsBlocked(pid))
        {
            throw new HelperError(
                "uipi_blocked",
                $"target process {pid} runs elevated; a non-elevated helper cannot drive it",
                "Run CoWorker as administrator to control elevated windows.");
        }
    }
}
