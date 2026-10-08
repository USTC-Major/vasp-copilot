using System;
using System.IO;
using System.Threading;
using System.Threading.Tasks;
using System.Collections.Generic;
using System.Reflection;
using System.Web.Script.Serialization;
using VaspCopilot.Launcher;

class EnvironmentHarness
{
    static int Main(string[] args)
    {
        var json = new JavaScriptSerializer();
        string evidence = args[3], mode = args[4];
        Directory.CreateDirectory(evidence);
        var options = new LauncherOptions { RootDirectory = args[0], PythonExecutable = args[1],
            EnableAi = true, FullFeatures = mode != "normal" && mode != "isolated",
            IsolatedProfile = mode == "isolated", AutoPrepareEnvironment = mode != "full-default",
            VaspAiHome = Path.Combine(evidence, "home"), DataDirectory = Path.Combine(evidence, "data") };
        var stages = new List<string>();
        var outcomes = new List<object>();
        using (var controller = new LauncherController(args[2]))
        {
            if (mode == "metadata-sharing")
            {
                var flags = BindingFlags.Instance | BindingFlags.NonPublic;
                string metadata = Path.Combine(evidence, "shared-progress.json");
                string replacement = metadata + ".tmp";
                File.WriteAllText(metadata, "before");
                bool legacyWriteBlocked = false, legacyReplaceBlocked = false;
                // Reproduce the old File.ReadAllText sharing mode without timing races.
                using (var reader = new StreamReader(new FileStream(metadata, FileMode.Open, FileAccess.Read, FileShare.Read)))
                {
                    try { File.WriteAllText(metadata, "write"); } catch (IOException) { legacyWriteBlocked = true; }
                    File.WriteAllText(replacement, "replacement");
                    try { File.Replace(replacement, metadata, null); } catch (IOException) { legacyReplaceBlocked = true; }
                }
                bool writerAllowed = false, replacementAllowed = false, oldSnapshotReadable = false;
                var openReader = typeof(LauncherController).GetMethod("OpenPreparationReader", BindingFlags.Static | BindingFlags.NonPublic);
                using (var reader = (StreamReader)openReader.Invoke(null, new object[] { metadata }))
                {
                    File.WriteAllText(metadata, "snapshot"); writerAllowed = true;
                    File.WriteAllText(replacement, "replacement");
                    File.Replace(replacement, metadata, null); replacementAllowed = true;
                    oldSnapshotReadable = reader.ReadToEnd() == "snapshot";
                }
                File.WriteAllText(metadata, json.Serialize(new { stage = "install", message = "synthetic shared progress" }));
                // Exercise the actual poll consumer with a writer still owning its handle.
                using (var writer = new FileStream(metadata, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite | FileShare.Delete))
                    typeof(LauncherController).GetMethod("ReadPreparationProgress", flags).Invoke(controller, new object[] { metadata, "" });
                bool progressRead = controller.GetSnapshot().StartupStage == "environment_install";
                string state = Path.Combine(evidence, "metadata-state"), log = Path.Combine(state, "logs", "owned.local.log");
                Directory.CreateDirectory(Path.GetDirectoryName(log)); File.WriteAllText(log, "synthetic");
                File.WriteAllText(metadata, json.Serialize(new { log_path = log }));
                object[] logArgs = { metadata, state, Path.Combine(state, "logs") };
                using (var writer = new FileStream(metadata, FileMode.Open, FileAccess.ReadWrite, FileShare.ReadWrite | FileShare.Delete))
                    typeof(LauncherController).GetMethod("ReadPreparationLog", flags).Invoke(controller, logArgs);
                bool logRead = String.Equals((string)logArgs[2], log, StringComparison.Ordinal);
                bool passed = legacyWriteBlocked && legacyReplaceBlocked && writerAllowed && replacementAllowed && oldSnapshotReadable && progressRead && logRead;
                File.WriteAllText(Path.Combine(evidence, "result.json"), json.Serialize(new {
                    outcomes = new [] { new { ok = passed, error = passed ? "" : "metadata sharing regression", selectedPython = (string)null,
                        environmentFailureLogPath = (string)null, running = false, snapshot = controller.GetSnapshot() } },
                    metadataChecks = new { legacyWriteBlocked, legacyReplaceBlocked, writerAllowed, replacementAllowed, oldSnapshotReadable, progressRead, logRead },
                    stages, running = false, defaultsOff = !new LauncherOptions().AutoPrepareEnvironment,
                    parentSecretUnchanged = Environment.GetEnvironmentVariable("OPENAI_API_KEY") == "synthetic-controller-secret" }));
                return passed ? 0 : 1;
            }
            if (mode == "discovery-probe")
            {
                // Other supported Pythons may legitimately precede py.exe on this
                // machine. Exercise the private discovery/probe contract directly.
                string request = Path.Combine(evidence, "probe-request"); Directory.CreateDirectory(request);
                var flags = BindingFlags.Instance | BindingFlags.NonPublic;
                var launchers = (IEnumerable<string>)typeof(LauncherController).GetMethod("PythonLaunchers", flags).Invoke(controller, null);
                bool found = false;
                foreach (string launcher in launchers) if (String.Equals(launcher, Path.GetFullPath(args[1]), StringComparison.OrdinalIgnoreCase)) found = true;
                string python = (string)typeof(LauncherController).GetMethod("ProbeBasePython", flags).Invoke(controller, new object[] { args[1], "-3.12 ", request });
                var outcome = new { ok = found && !String.IsNullOrEmpty(python), error = "", selectedPython = python,
                    environmentFailureLogPath = (string)null, running = false, snapshot = controller.GetSnapshot() };
                File.WriteAllText(Path.Combine(evidence, "result.json"), json.Serialize(new { outcomes = new [] { outcome }, stages,
                    running = false, defaultsOff = !new LauncherOptions().AutoPrepareEnvironment,
                    parentSecretUnchanged = Environment.GetEnvironmentVariable("OPENAI_API_KEY") == "synthetic-controller-secret" }));
                return 0;
            }
            int rounds = mode == "retry" || mode == "cancel-retry" || mode == "pre-cancel" ? 2 : 1;
            if (mode == "pre-cancel") controller.CancelStartup();
            for (int round = 0; round < rounds; round++)
            {
                Exception failure = null;
                Task disposal = null;
                bool cancelled = false;
                var operation = Task.Run(delegate { try { controller.Start(options); } catch (Exception exc) { failure = exc; } });
                while (!operation.IsCompleted)
                {
                    var snapshot = controller.GetSnapshot();
                    if (!stages.Contains(snapshot.StartupStage)) stages.Add(snapshot.StartupStage);
                    if (round == 0 && (mode == "cancel" || mode == "cancel-retry" || mode == "dispose") && File.Exists(Path.Combine(args[0], "child-pid.json")) && !cancelled)
                    { if (mode == "dispose") disposal = Task.Run(delegate { controller.Dispose(); }); else controller.CancelStartup(); cancelled = true; }
                    Thread.Sleep(40);
                }
                operation.GetAwaiter().GetResult();
                if (disposal != null) disposal.GetAwaiter().GetResult();
                var ready = controller.GetSnapshot();
                if (failure == null && mode == "actual")
                {
                    File.WriteAllText(Path.Combine(evidence, "ready.json"), json.Serialize(new { selectedPython = controller.SelectedPython, snapshot = ready }));
                    DateTime deadline = DateTime.UtcNow.AddMinutes(3);
                    while (!File.Exists(Path.Combine(evidence, "release")) && DateTime.UtcNow < deadline)
                    {
                        if (File.Exists(Path.Combine(evidence, "restart")))
                        {
                            File.Delete(Path.Combine(evidence, "restart"));
                            // A ready environment must reuse offline even with a proxy.
                            Environment.SetEnvironmentVariable("HTTPS_PROXY", "http://synthetic:password@invalid.test:8080");
                            controller.Restart(options);
                            ready = controller.GetSnapshot();
                            File.WriteAllText(Path.Combine(evidence, "ready2.json"), json.Serialize(new { selectedPython = controller.SelectedPython, snapshot = ready }));
                        }
                        Thread.Sleep(50);
                    }
                }
                outcomes.Add(new { ok = failure == null, error = failure == null ? "" : failure.Message,
                    environmentFailureLogPath = controller.EnvironmentFailureLogPath,
                    selectedPython = controller.SelectedPython, running = controller.IsRunning, snapshot = ready });
                if (failure == null) controller.Stop();
                if (round + 1 < rounds) File.WriteAllText(Path.Combine(args[0], "helper-mode"), "success");
            }
            File.WriteAllText(Path.Combine(evidence, "result.json"), json.Serialize(new { outcomes, stages, running = controller.IsRunning,
                defaultsOff = !new LauncherOptions().AutoPrepareEnvironment,
                parentSecretUnchanged = Environment.GetEnvironmentVariable("OPENAI_API_KEY") == "synthetic-controller-secret" }));
        }
        return 0;
    }
}
