using System;
using System.IO;
using System.Threading;
class DiscoveryProbeStub
{
    static int Main(string[] args)
    {
        if (Environment.GetEnvironmentVariable("PYTHON_MANAGER_AUTOMATIC_INSTALL") != "false") return 2;
        if (Environment.GetEnvironmentVariable("PYLAUNCHER_ALLOW_INSTALL") != null) return 3;
        string source = Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "probe-data.json");
        if (File.Exists(source + ".timeout")) { Thread.Sleep(30000); return 4; }
        File.Copy(source, args[args.Length - 1], true);
        return 0;
    }
}
