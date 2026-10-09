// Probe-only executable for controller discovery tests; never runs Python code.
using System;
using System.IO;
using System.Web.Script.Serialization;
class BasePythonStub
{
    static int Main(string[] args)
    {
        string directory = AppDomain.CurrentDomain.BaseDirectory;
        bool launcher = args.Length > 0 && args[0].StartsWith("-3.");
        if (Environment.GetEnvironmentVariable("PYTHON_MANAGER_AUTOMATIC_INSTALL") != "false") return 2;
        File.AppendAllText(Path.Combine(directory, "probe-calls.txt"), launcher ? args[0] + "\n" : "direct\n");
        File.WriteAllText(args[args.Length - 1], new JavaScriptSerializer().Serialize(new {
            python = File.ReadAllText(Path.Combine(directory, "real-python.txt")), major = 3,
            minor = launcher ? 12 : 10, bits = 64, machine = "amd64", implementation = "CPython", platform = "win32", releaselevel = "final", free_threaded = false }));
        return 0;
    }
}
