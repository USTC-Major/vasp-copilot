using System;
using System.IO;
using System.Threading;
using System.Web.Script.Serialization;
using VaspCopilot.Launcher;

class FullFeaturesHarness
{
    static int Main(string[] args)
    {
        var json = new JavaScriptSerializer();
        string evidence = args[3]; Directory.CreateDirectory(evidence);
        string before = Environment.GetEnvironmentVariable("VASP_REVIEWER_SHARED_SECRET");
        string mode = args[4];
        var options = new LauncherOptions { RootDirectory = args[0], PythonExecutable = args[1],
            EnableAi = mode != "full-no-ai" && mode != "normal", FullFeatures = mode.StartsWith("full"),
            IsolatedProfile = mode == "isolated" || mode == "full-conflict",
            VaspAiHome = Path.Combine(evidence, "home"), DataDirectory = Path.Combine(evidence, "data") };
        using (var controller = new LauncherController(args[2]))
        {
            try
            {
                controller.Start(options);
                File.WriteAllText(Path.Combine(evidence, "ready.json"), json.Serialize(controller.GetSnapshot()));
                DateTime deadline = DateTime.UtcNow.AddSeconds(60);
                while (!File.Exists(Path.Combine(evidence, "release")) && DateTime.UtcNow < deadline)
                {
                    if (File.Exists(Path.Combine(evidence, "restart")))
                    {
                        File.Delete(Path.Combine(evidence, "restart")); controller.Restart(options);
                        File.WriteAllText(Path.Combine(evidence, "ready2.json"), json.Serialize(controller.GetSnapshot()));
                    }
                    Thread.Sleep(40);
                }
                controller.Stop();
                File.WriteAllText(Path.Combine(evidence, "result.json"), json.Serialize(new { ok = true,
                    parentEnvironmentUnchanged = before == Environment.GetEnvironmentVariable("VASP_REVIEWER_SHARED_SECRET"),
                    snapshot = controller.GetSnapshot() }));
                return 0;
            }
            catch (LauncherException exc)
            {
                File.WriteAllText(Path.Combine(evidence, "result.json"), json.Serialize(new { ok = false,
                    parentEnvironmentUnchanged = before == Environment.GetEnvironmentVariable("VASP_REVIEWER_SHARED_SECRET"),
                    error = exc.Message, snapshot = controller.GetSnapshot() }));
                return 1;
            }
        }
    }
}
