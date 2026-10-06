using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Diagnostics;
using System.Web.Script.Serialization;
using VaspCopilot.Launcher;
class Harness
{
    static int Main(string[] args)
    {
        var json = new JavaScriptSerializer();
        string evidence = args[3]; Directory.CreateDirectory(evidence);
        var options = new LauncherOptions { RootDirectory = args[0], PythonExecutable = args[1], EnableAi = args[4] == "ai", IsolatedProfile = true, VaspAiHome = Path.Combine(evidence, "home"), DataDirectory = Path.Combine(evidence, "data") };
        if (args.Length > 5) options.VaspAiHome = args[5];
        var elapsed = Stopwatch.StartNew();
        using (var controller = new LauncherController(args[2]))
        {
            try
            {
                if (args[4] == "cancel") { var operation = Task.Run(delegate { controller.Start(options); }); Thread.Sleep(500); controller.CancelStartup(); operation.GetAwaiter().GetResult(); }
                else controller.Start(options);
                File.WriteAllText(Path.Combine(evidence, "ready.json"), json.Serialize(new { selectedPython = controller.SelectedPython, attempts = controller.StartupAttempts, snapshot = controller.GetSnapshot() }));
                if (args[4] == "restart") controller.Restart(options);
                if (args[4] == "hold")
                {
                    var deadline = DateTime.UtcNow.AddSeconds(60);
                    while (!File.Exists(Path.Combine(evidence, "release")) && DateTime.UtcNow < deadline)
                    {
                        if (File.Exists(Path.Combine(evidence, "restart-request")))
                        {
                            File.Delete(Path.Combine(evidence, "restart-request")); controller.Restart(options);
                            File.WriteAllText(Path.Combine(evidence, "ready2.json"), json.Serialize(new { selectedPython = controller.SelectedPython, attempts = controller.StartupAttempts, snapshot = controller.GetSnapshot() }));
                        }
                        Thread.Sleep(50);
                    }
                }
                controller.Stop();
                File.WriteAllText(Path.Combine(evidence, "result.json"), json.Serialize(new { ok = true, attempts = controller.StartupAttempts, running = controller.IsRunning, snapshot = controller.GetSnapshot() }));
                return 0;
            }
            catch (Exception e)
            {
                File.WriteAllText(Path.Combine(evidence, "result.json"), json.Serialize(new { ok = false, attempts = controller.StartupAttempts, running = controller.IsRunning, elapsedMilliseconds = elapsed.ElapsedMilliseconds, error = e.Message, snapshot = controller.GetSnapshot() }));
                return 1;
            }
        }
    }
}
