using System;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Threading;
using System.Web.Script.Serialization;
using VaspCopilot.Launcher;

class HealthHarness
{
    static LauncherController controller;
    static string root, evidence;
    static JavaScriptSerializer json = new JavaScriptSerializer();
    static ServiceSnapshot Toolbox() { return controller.GetSnapshot().Services.Single(s => s.Key == "toolbox"); }
    static void Require(bool condition, string detail) { if (!condition) throw new Exception(detail); }
    static void Mode(string mode) { File.WriteAllText(Path.Combine(root, "toolbox-mode"), mode); }
    static void Wait(Func<bool> condition, int milliseconds, string detail)
    {
        var watch = Stopwatch.StartNew();
        while (!condition() && watch.ElapsedMilliseconds < milliseconds) Thread.Sleep(50);
        Require(condition(), detail);
    }
    static void Record(string name, object detail)
    { File.AppendAllText(Path.Combine(evidence, "checks.jsonl"), json.Serialize(new { name, passed = true, detail, snapshot = controller.GetSnapshot() }) + Environment.NewLine); }
    static int Main(string[] args)
    {
        root = args[0]; evidence = args[3]; Directory.CreateDirectory(evidence);
        var options = new LauncherOptions { RootDirectory = root, PythonExecutable = args[1], EnableAi = true, IsolatedProfile = true,
            VaspAiHome = Path.Combine(evidence, "home"), DataDirectory = Path.Combine(evidence, "data") };
        controller = new LauncherController(args[2]);
        try
        {
            controller.Start(options); int pid = Toolbox().Pid;
            Mode("slow"); Wait(() => Toolbox().State == "degraded", 6000, "slow response was not observed");
            Thread.Sleep(2200); Require(Toolbox().State == "degraded", "brief slow response became error");
            Mode("normal"); Wait(() => Toolbox().State == "ready", 6000, "brief slow response did not recover");
            Require(Toolbox().Pid == pid && controller.StartupAttempts == 1, "recovery restarted process");
            Record("brief-timeout-grace-and-recovery-with-same-owned-pid", new { pid });

            Mode("slow"); var duration = Stopwatch.StartNew();
            Wait(() => Toolbox().State == "error", 17000, "persistent timeout never became error");
            Require(duration.ElapsedMilliseconds >= 8000 && Toolbox().Pid == pid, "persistent timeout grace or ownership wrong");
            Record("persistent-timeout-becomes-error-after-grace", new { elapsedMilliseconds = duration.ElapsedMilliseconds });
            Mode("normal"); Wait(() => Toolbox().State == "ready", 6000, "persistent timeout did not recover");
            Require(Toolbox().Pid == pid, "persistent recovery changed pid"); Record("persistent-timeout-recovers-without-restart", new { pid });

            foreach (string field in new [] { "token", "root", "fingerprint", "pid", "auth" })
            {
                Mode("forge-" + field); var watch = Stopwatch.StartNew();
                Wait(() => Toolbox().State == "error", 4000, "forged identity was not rejected immediately: " + field);
                Require(watch.ElapsedMilliseconds < 4000, "forged identity used timeout grace");
                Record("forged-" + field + "-rejected-without-grace", new { elapsedMilliseconds = watch.ElapsedMilliseconds });
                Mode("normal"); Wait(() => Toolbox().State == "ready", 6000, "identity recovery did not reverify");
            }
            using (var process = Process.GetProcessById(pid)) process.Kill();
            var exitWatch = Stopwatch.StartNew();
            Wait(() => Toolbox().State == "error" && Toolbox().Pid == 0, 5000, "owned runtime exit not promptly cleaned");
            Thread.Sleep(1800); Require(Toolbox().Pid == 0 && Toolbox().State == "error", "exited process was automatically restarted");
            Record("real-owned-runtime-exit-cleaned-without-auto-restart", new { elapsedMilliseconds = exitWatch.ElapsedMilliseconds });
            controller.Stop(); controller.Dispose();

            // A distinct test Job owns the forged server; it is never a user's process.
            Mode("normal"); controller = new LauncherController(args[2]); controller.Start(options);
            int port = Toolbox().Port; Mode("release");
            Wait(() => File.Exists(Path.Combine(root, "toolbox-released")), 4000, "owned listener did not release");
            using (var external = OwnedProcess.Start(args[1], "-X utf8 " + OwnedProcess.Quote(args[2]) + " --external --root " + OwnedProcess.Quote(root) + " --port " + port, root))
            {
                Wait(() => Native.ListenerPid(port) != 0, 4000, "external fixture listener did not bind");
                var watch = Stopwatch.StartNew(); Wait(() => Toolbox().State == "error", 4000, "external listener accepted");
                Require(!controller.GetSnapshot().Services.Where(s => s.Key != "toolbox").Any(s => s.State != "ready"), "unrelated service disrupted");
                Record("external-listener-with-copied-identity-rejected-by-job-ownership", new { elapsedMilliseconds = watch.ElapsedMilliseconds });
            }
            controller.Stop();
            File.WriteAllText(Path.Combine(evidence, "result.json"), json.Serialize(new { ok = true, snapshot = controller.GetSnapshot() })); return 0;
        }
        catch (Exception exc) { File.WriteAllText(Path.Combine(evidence, "result.json"), json.Serialize(new { ok = false, error = exc.ToString(), snapshot = controller.GetSnapshot() })); return 1; }
        finally { controller.Dispose(); }
    }
}
