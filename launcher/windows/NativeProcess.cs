using System;
using System.ComponentModel;
using System.Collections;
using System.Collections.Generic;
using System.Diagnostics;
using System.Net;
using System.Runtime.InteropServices;
using System.Text;

namespace VaspCopilot.Launcher
{
    // A per-service Job owns descendants, including subprocesses created during shutdown.
    // Create suspended, assign before resume: there is no unowned spawning window.
    internal sealed class OwnedProcess : IDisposable
    {
        private IntPtr job, process, runtime;
        private readonly System.Collections.Generic.List<IntPtr> pending = new System.Collections.Generic.List<IntPtr>();
        public int Pid { get; private set; }
        public bool HasExited { get { return job == IntPtr.Zero ? pending.TrueForAll(h => Native.WaitForSingleObject(h, 0) == 0) : Native.JobPids(job).Length == 0; } }
        public bool RuntimeExited { get { return runtime != IntPtr.Zero && Native.WaitForSingleObject(runtime, 0) == 0; } }
        public void TrackRuntime(int pid)
        {
            if (!ContainsPid(pid)) throw new InvalidOperationException("Runtime identity is not owned.");
            IntPtr handle = Native.OpenProcess(0x100000, false, (uint)pid);
            if (handle == IntPtr.Zero) throw new Win32Exception();
            if (runtime != IntPtr.Zero) Native.CloseHandle(runtime);
            runtime = handle;
        }
        public bool ContainsPid(int pid)
        {
            if (pid == 0 || job == IntPtr.Zero) return false;
            IntPtr candidate = Native.OpenProcess(0x1000, false, (uint)pid);
            if (candidate == IntPtr.Zero) return false;
            try { bool member; return Native.IsProcessInJob(candidate, job, out member) && member; }
            finally { Native.CloseHandle(candidate); }
        }
        public static OwnedProcess Start(string executable, string arguments, string workingDirectory, IDictionary<string, string> environmentOverrides = null)
        {
            var owner = new OwnedProcess();
            IntPtr thread = IntPtr.Zero;
            IntPtr environment = IntPtr.Zero;
            try
            {
                owner.job = Native.CreateJobObject(IntPtr.Zero, null);
                if (owner.job == IntPtr.Zero) throw new Win32Exception();
                var limits = new Native.JOBOBJECT_EXTENDED_LIMIT_INFORMATION();
                limits.BasicLimitInformation.LimitFlags = 0x2000; // KILL_ON_JOB_CLOSE
                int size = Marshal.SizeOf(limits);
                IntPtr memory = Marshal.AllocHGlobal(size);
                try
                {
                    Marshal.StructureToPtr(limits, memory, false);
                    if (!Native.SetInformationJobObject(owner.job, 9, memory, (uint)size)) throw new Win32Exception();
                }
                finally { Marshal.FreeHGlobal(memory); }
                var startup = new Native.STARTUPINFO();
                startup.cb = Marshal.SizeOf(startup);
                Native.PROCESS_INFORMATION info;
                if (environmentOverrides != null)
                    environment = Marshal.StringToHGlobalUni(BuildEnvironmentBlock(environmentOverrides));
                if (!Native.CreateProcess(executable, new StringBuilder(Quote(executable) + " " + arguments), IntPtr.Zero,
                    IntPtr.Zero, false, 0x08000004 | (environment != IntPtr.Zero ? 0x00000400u : 0u), environment, workingDirectory, ref startup, out info))
                    throw new Win32Exception(); // CREATE_NO_WINDOW | CREATE_SUSPENDED | CREATE_UNICODE_ENVIRONMENT
                owner.process = info.hProcess;
                thread = info.hThread;
                owner.Pid = (int)info.dwProcessId;
                if (!Native.AssignProcessToJobObject(owner.job, owner.process)) throw new Win32Exception();
                if (Native.ResumeThread(thread) == UInt32.MaxValue) throw new Win32Exception();
                return owner;
            }
            catch
            {
                if (owner.process != IntPtr.Zero) Native.TerminateProcess(owner.process, 1);
                owner.Dispose();
                throw;
            }
            finally
            {
                if (thread != IntPtr.Zero) Native.CloseHandle(thread);
                if (environment != IntPtr.Zero) Marshal.FreeHGlobal(environment);
            }
        }
        internal static string BuildEnvironmentBlock(IDictionary<string, string> overrides)
        {
            // Windows requires a sorted, double-NUL-terminated UTF-16 block.
            // Copy rather than mutate the parent environment, including Unicode values.
            var values = new SortedDictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (DictionaryEntry entry in Environment.GetEnvironmentVariables())
                values[(string)entry.Key] = (string)entry.Value;
            foreach (var entry in overrides)
            {
                if (String.IsNullOrEmpty(entry.Key) || entry.Key.IndexOfAny(new [] { '=', '\0' }) >= 0 || (entry.Value != null && entry.Value.IndexOf('\0') >= 0))
                    throw new ArgumentException("Invalid child environment entry.");
                if (entry.Value == null) values.Remove(entry.Key); else values[entry.Key] = entry.Value;
            }
            var block = new StringBuilder();
            foreach (var entry in values) block.Append(entry.Key).Append('=').Append(entry.Value).Append('\0');
            if (block.Length == 0) block.Append('\0');
            return block.Append('\0').ToString();
        }
        public bool Wait(int milliseconds)
        {
            DateTime deadline = DateTime.UtcNow.AddMilliseconds(milliseconds);
            while (!HasExited && DateTime.UtcNow < deadline) System.Threading.Thread.Sleep(50);
            return HasExited;
        }
        public void Dispose()
        {
            if (job != IntPtr.Zero)
            {
                int[] pids = Native.JobPids(job);
                foreach (int pid in pids)
                {
                    IntPtr handle = Native.OpenProcess(0x100000, false, (uint)pid);
                    if (handle != IntPtr.Zero) pending.Add(handle);
                }
                bool closed = Native.CloseHandle(job);
                if (!closed) throw new InvalidOperationException("Owned Job close failed.");
                job = IntPtr.Zero;
            }
            bool stopped = true;
            foreach (IntPtr handle in pending) if (Native.WaitForSingleObject(handle, 2000) != 0) stopped = false;
            if (!stopped) throw new InvalidOperationException("Owned process cleanup could not be verified.");
            foreach (IntPtr handle in pending) Native.CloseHandle(handle);
            pending.Clear();
            if (process != IntPtr.Zero) { Native.WaitForSingleObject(process, 2000); Native.CloseHandle(process); process = IntPtr.Zero; }
            if (runtime != IntPtr.Zero) { Native.CloseHandle(runtime); runtime = IntPtr.Zero; }
        }
        internal static string Quote(string value)
        {
            var result = new StringBuilder("\"");
            int backslashes = 0;
            foreach (char c in value)
            {
                if (c == '\\') { backslashes++; continue; }
                if (c == '"') { result.Append('\\', backslashes * 2 + 1).Append(c); backslashes = 0; continue; }
                result.Append('\\', backslashes).Append(c); backslashes = 0;
            }
            return result.Append('\\', backslashes * 2).Append('"').ToString();
        }
    }

    internal static class Native
    {
        [StructLayout(LayoutKind.Sequential)] internal struct STARTUPINFO
        {
            public int cb; public string lpReserved, lpDesktop, lpTitle;
            public uint dwX, dwY, dwXSize, dwYSize, dwXCountChars, dwYCountChars, dwFillAttribute, dwFlags;
            public short wShowWindow, cbReserved2; public IntPtr lpReserved2, hStdInput, hStdOutput, hStdError;
        }
        [StructLayout(LayoutKind.Sequential)] internal struct PROCESS_INFORMATION { public IntPtr hProcess, hThread; public uint dwProcessId, dwThreadId; }
        [StructLayout(LayoutKind.Sequential)] internal struct JOBOBJECT_BASIC_LIMIT_INFORMATION
        {
            public long PerProcessUserTimeLimit, PerJobUserTimeLimit;
            public uint LimitFlags; public UIntPtr MinimumWorkingSetSize, MaximumWorkingSetSize;
            public uint ActiveProcessLimit; public UIntPtr Affinity; public uint PriorityClass, SchedulingClass;
        }
        [StructLayout(LayoutKind.Sequential)] internal struct IO_COUNTERS { public ulong a, b, c, d, e, f; }
        [StructLayout(LayoutKind.Sequential)] internal struct JOBOBJECT_EXTENDED_LIMIT_INFORMATION
        {
            public JOBOBJECT_BASIC_LIMIT_INFORMATION BasicLimitInformation;
            public IO_COUNTERS IoInfo; public UIntPtr ProcessMemoryLimit, JobMemoryLimit, PeakProcessMemoryUsed, PeakJobMemoryUsed;
        }
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] internal static extern IntPtr CreateJobObject(IntPtr attributes, string name);
        [DllImport("kernel32.dll", SetLastError = true)] internal static extern bool SetInformationJobObject(IntPtr job, int info, IntPtr value, uint length);
        [DllImport("kernel32.dll", SetLastError = true)] internal static extern bool AssignProcessToJobObject(IntPtr job, IntPtr process);
        [DllImport("kernel32.dll", CharSet = CharSet.Unicode, SetLastError = true)] internal static extern bool CreateProcess(string app, StringBuilder command, IntPtr pa, IntPtr ta, bool inherit, uint flags, IntPtr env, string cwd, ref STARTUPINFO startup, out PROCESS_INFORMATION info);
        [DllImport("kernel32.dll", SetLastError = true)] internal static extern uint ResumeThread(IntPtr thread);
        [DllImport("kernel32.dll")] internal static extern bool TerminateProcess(IntPtr process, uint code);
        [DllImport("kernel32.dll")] internal static extern bool CloseHandle(IntPtr handle);
        [DllImport("kernel32.dll")] internal static extern uint WaitForSingleObject(IntPtr handle, uint milliseconds);
        [DllImport("kernel32.dll", SetLastError = true)] internal static extern IntPtr OpenProcess(uint access, bool inherit, uint pid);
        [DllImport("kernel32.dll", SetLastError = true)] internal static extern bool IsProcessInJob(IntPtr process, IntPtr job, out bool result);
        [DllImport("kernel32.dll", SetLastError = true)] private static extern bool QueryInformationJobObject(IntPtr job, int type, IntPtr buffer, uint size, out uint length);
        internal static int[] JobPids(IntPtr job)
        {
            const int bytes = 65536;
            IntPtr buffer = Marshal.AllocHGlobal(bytes);
            try
            {
                uint length;
                if (!QueryInformationJobObject(job, 3, buffer, bytes, out length)) throw new Win32Exception();
                int count = Marshal.ReadInt32(buffer, 4);
                var result = new int[count];
                for (int i = 0; i < count; i++) result[i] = (int)Marshal.ReadIntPtr(buffer, 8 + i * IntPtr.Size);
                return result;
            }
            finally { Marshal.FreeHGlobal(buffer); }
        }
        [DllImport("iphlpapi.dll")] private static extern uint GetExtendedTcpTable(IntPtr table, ref int size, bool order, int family, int kind, uint reserved);
        public static int ListenerPid(int port)
        {
            int ipv4 = ListenerPid(port, 2, 24, 8, 20);
            return ipv4 != 0 ? ipv4 : ListenerPid(port, 23, 56, 20, 52);
        }
        private static int ListenerPid(int port, int family, int rowSize, int portOffset, int pidOffset)
        {
            int size = 0;
            GetExtendedTcpTable(IntPtr.Zero, ref size, false, family, 3, 0); // TCP_TABLE_OWNER_PID_LISTENER
            IntPtr table = Marshal.AllocHGlobal(size);
            try
            {
                uint result = GetExtendedTcpTable(table, ref size, false, family, 3, 0);
                if (result != 0) throw new Win32Exception((int)result);
                int count = Marshal.ReadInt32(table);
                for (int i = 0; i < count; i++)
                {
                    IntPtr row = IntPtr.Add(table, 4 + i * rowSize);
                    int networkPort = Marshal.ReadInt32(row, portOffset);
                    int localPort = ((networkPort & 255) << 8) | ((networkPort >> 8) & 255);
                    if (localPort == port) return Marshal.ReadInt32(row, pidOffset);
                }
                return 0;
            }
            finally { Marshal.FreeHGlobal(table); }
        }
    }
}
