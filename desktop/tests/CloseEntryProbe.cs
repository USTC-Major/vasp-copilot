using System;
using System.Diagnostics;
using System.Runtime.InteropServices;
using System.Threading;
class CloseEntryProbe
{
    [DllImport("user32.dll")] static extern bool EnumWindows(Callback callback, IntPtr parameter);
    [DllImport("user32.dll")] static extern uint GetWindowThreadProcessId(IntPtr window, out uint pid);
    [DllImport("user32.dll")] static extern bool PostMessage(IntPtr window, uint message, IntPtr wparam, IntPtr lparam);
    delegate bool Callback(IntPtr window, IntPtr parameter);
    static int Main(string[] args)
    {
        try
        {
        int pid = Int32.Parse(args[0]); var process = Process.GetProcessById(pid); bool sent = false;
        EnumWindows(delegate(IntPtr window, IntPtr parameter) { uint owner; GetWindowThreadProcessId(window, out owner); if (owner == pid) { PostMessage(window, 0x0010, IntPtr.Zero, IntPtr.Zero); sent = true; } return true; }, IntPtr.Zero);
        if (!sent || !process.WaitForExit(30000)) return 1;
        // The Python parent owns the process handle and verifies its exit code.
        // A GetProcessById wrapper may lose access to ExitCode after termination.
        return 0;
        }
        catch (Exception error) { Console.Error.WriteLine(error.GetType().Name + ": " + error.Message); return 2; }
    }
}
