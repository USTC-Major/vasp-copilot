using System;
using System.IO;
using System.Linq;
using System.Collections.Generic;
using System.Reflection;
using System.Web.Script.Serialization;
using Microsoft.Win32;
using VaspCopilot.Launcher;

class DiscoveryTests
{
    static readonly JavaScriptSerializer Json = new JavaScriptSerializer();
    static object Call(string method, params object[] args)
    { return typeof(LauncherController).GetMethod(method, BindingFlags.Static | BindingFlags.NonPublic).Invoke(null, args); }
    static void Check(bool ok, string name)
    { if (!ok) throw new Exception(name); Console.WriteLine("PASS " + name); }
    static int Main(string[] args)
    {
        string root = args[0], work = args[1], stub = args[2];
        var policy = PythonRuntimePolicy.Load(root);
        string data = Path.Combine(Path.GetDirectoryName(stub), "probe-data.json");
        using (var controller = new LauncherController())
        {
            var flags = BindingFlags.Instance | BindingFlags.NonPublic;
            typeof(LauncherController).GetField("pythonPolicy", flags).SetValue(controller, policy);
            var probe = typeof(LauncherController).GetMethod("ProbeBasePython", flags);
            foreach (int minor in new [] { 10, 11, 12, 13, 14, 15, 16 })
            {
                File.WriteAllText(data, Json.Serialize(new { python = stub, major = 3, minor, bits = 64, machine = "amd64", implementation = "CPython", platform = "win32", free_threaded = false, releaselevel = "final" }));
                string found = (string)probe.Invoke(controller, new object[] { stub, "", work });
                Check((found != null) == (minor >= 11 && minor <= 14), "version-3." + minor);
            }
            foreach (var item in new [] { new { bits = 32, machine = "amd64", free = false, release = "final" }, new { bits = 64, machine = "arm64", free = false, release = "final" }, new { bits = 64, machine = "amd64", free = true, release = "final" }, new { bits = 64, machine = "amd64", free = false, release = "candidate" } })
            {
                File.WriteAllText(data, Json.Serialize(new { python = stub, major = 3, minor = 13, bits = item.bits, machine = item.machine, implementation = "CPython", platform = "win32", free_threaded = item.free, releaselevel = item.release }));
                Check(probe.Invoke(controller, new object[] { stub, "", work }) == null, "reject-unverified-variant-" + item);
            }
            File.WriteAllText(data, "malformed");
            Check(probe.Invoke(controller, new object[] { stub, "", work }) == null, "malformed-probe");
            Check(probe.Invoke(controller, new object[] { Path.Combine(work, "missing.exe"), "", work }) == null, "non-launchable-probe");
            File.WriteAllText(data + ".timeout", "");
            Check(probe.Invoke(controller, new object[] { stub, "", work }) == null, "timeout-probe");
            File.Delete(data + ".timeout");
            var failures = (List<string>)typeof(LauncherController).GetField("pythonProbeFailures", flags).GetValue(controller);
            Check(failures.Any(x => x.Contains("3.15") && x.Contains("pymatgen-core")), "315-dependency-reason");
            Check(failures.Any(x => x.Contains("超时")), "timeout-reason");
        }
        var options = new LauncherOptions { RootDirectory = work, FullFeatures = true, AutoPrepareEnvironment = true };
        string oldPath = Environment.GetEnvironmentVariable("PATH");
        try
        {
            Environment.SetEnvironmentVariable("PATH", Path.GetDirectoryName(stub) + ";\"" + Path.GetDirectoryName(stub) + "\"");
            var paths = ((IEnumerable<string>)Call("PythonCandidates", options)).ToArray();
            Check(paths.Count(x => x == stub) == 1, "unicode-space-path-deduplicated");
            options.PythonExecutable = stub;
            Check(((IEnumerable<string>)Call("PythonCandidates", options)).SequenceEqual(new [] { stub }), "manual-selection-exclusive");
            options.PythonExecutable = Path.Combine(work, "missing.exe");
            try { ((IEnumerable<string>)Call("PythonCandidates", options)).ToArray(); throw new Exception("missing manual path silently fell back"); }
            catch (LauncherException exc) { Check(exc.Message.Contains("自动检测"), "stale-path-recovery-guidance"); }
        }
        finally { Environment.SetEnvironmentVariable("PATH", oldPath); }
        string registryPath = @"Software\VaspCopilotDiscoveryTests\" + Guid.NewGuid().ToString("N");
        try
        {
            using (var fixture = Registry.CurrentUser.CreateSubKey(registryPath))
            {
                using (var install = fixture.CreateSubKey(@"ExampleProvider\3.13\InstallPath")) install.SetValue("ExecutablePath", stub);
                using (var install = fixture.CreateSubKey(@"OtherProvider\3.14\InstallPath")) install.SetValue("", Path.GetDirectoryName(stub));
                var paths = (IEnumerable<string>)Call("RegistryPythons", fixture);
                Check(paths.Count(x => x == stub) == 2, "pep514-company-tag-explicit-and-default-path");
            }
        }
        finally { Registry.CurrentUser.DeleteSubKeyTree(registryPath, false); }
        string conda = Path.Combine(work, "environments.txt");
        File.WriteAllText(conda, Path.GetDirectoryName(stub) + "\n\n");
        Check(((IEnumerable<string>)Call("CondaPythons", conda)).Contains(stub), "inactive-conda-unicode-path");
        Console.WriteLine("All discovery checks passed");
        return 0;
    }
}
