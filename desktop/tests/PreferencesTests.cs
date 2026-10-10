using System;
using System.Collections.Generic;
using System.IO;
using System.Linq;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using VaspCopilot.Launcher;

class PreferencesTests
{
    static void Require(bool ok, string why) { if (!ok) throw new Exception(why); }
    static LauncherOptions Options(string root) { return new LauncherOptions { RootDirectory = root, PythonExecutable = "C:\\中文 Python\\python.exe", VaspAiHome = "C:\\synthetic-home", DataDirectory = "C:\\synthetic-data", EnableAi = false }; }
    static void Write(string dir, LauncherOptions o) { new LauncherPreferencesStore(dir).Save(o); }
    static string Case(string root, string name) { string p = Path.Combine(root, name); Directory.CreateDirectory(p); return p; }
    static void MustFail(Action action, string stage)
    { try { action(); throw new Exception("Expected failure: " + stage); } catch (PreferencesException error) { Require(error.Stage == stage, "Wrong failure stage: " + error.Stage); } }
    static LauncherPreferencesStore LegacyCase(string root, string name, string legacy, LauncherOptions options, string dotenv, out string backend)
    {
        string app = Case(root, name), package = Case(app, "旧 中文包 with spaces");
        backend = Case(package, "backend"); options.RootDirectory = package;
        Write(Path.Combine(app, legacy), options);
        if (dotenv != null) File.WriteAllText(Path.Combine(backend, ".env"), dotenv);
        return new LauncherPreferencesStore(Path.Combine(app, "desktop"));
    }
    static LauncherOptions Migrate(LauncherPreferencesStore store, Func<string, string> environment)
    { return store.LoadFormal(Path.GetDirectoryName(store.DirectoryPath), "C:\\新 包", sources => null, environment); }
    static int LegacyDirectoryChecks(string root)
    {
        int checks = 0; string backend;
        string user = Case(root, "合成 用户 home");
        var inherited = new Dictionary<string, string> { { "USERPROFILE", user } };
        Func<string, string> environment = key => inherited.ContainsKey(key) ? inherited[key] : null;
        foreach (string legacy in new[] { "desktop-v3", "launcher" })
        {
            var store = LegacyCase(root, "default-" + legacy, legacy, new LauncherOptions(), null, out backend);
            string oldFile = Path.Combine(Path.GetDirectoryName(store.DirectoryPath), legacy, "preferences.json"), before = File.ReadAllText(oldFile);
            // A repository/parent .env is not loaded by the desktop runtime.
            File.WriteAllText(Path.Combine(Path.GetDirectoryName(backend), ".env"), "DATA_DIR=${UNSUPPORTED_PARENT}/wrong\nVASP_AI_HOME=wrong\n");
            string pp = Case(Path.Combine(user, ".vasp-ai"), "postprocessing"), marker = Path.Combine(pp, "synthetic-project.json");
            File.WriteAllText(marker, "retain-pp-project");
            var options = Migrate(store, environment);
            Require(options.DataDirectory == Path.Combine(backend, "data") && options.VaspAiHome == Path.Combine(user, ".vasp-ai"), "Normal defaults changed data/PP home");
            options.RootDirectory = "C:\\新 包"; store.Save(options);
            var restarted = new LauncherPreferencesStore(store.DirectoryPath);
            var restored = Migrate(restarted, key => { throw new Exception("Canonical preferences re-read the environment"); });
            Require(restored.DataDirectory == Path.Combine(backend, "data") && restored.VaspAiHome == Path.Combine(user, ".vasp-ai"), "Changing package changed preserved roots");
            Require(File.ReadAllText(oldFile) == before && File.ReadAllText(marker) == "retain-pp-project", "Migration changed old config or PP data"); checks++;
        }
        var envStore = LegacyCase(root, "relative-dotenv", "desktop-v3", new LauncherOptions(),
            "export DATA_DIR = '历史 data' # comment\nVASP_AI_HOME=旧 home # comment\nMODEL_KEY='synthetic-secret-not-for-migration'\n", out backend);
        var fromEnv = Migrate(envStore, environment);
        Require(fromEnv.DataDirectory == Path.Combine(backend, "历史 data") && fromEnv.VaspAiHome == Path.Combine(backend, "旧 home"), "Old .env relative paths moved to the new backend");
        Require(!File.ReadAllText(envStore.Target).Contains("synthetic-secret") && !File.ReadAllText(Path.Combine(envStore.DirectoryPath, "preferences-events.jsonl")).Contains("synthetic-secret"), "Migration retained unrelated .env content"); checks++;
        inherited["DATA_DIR"] = "继承 data"; inherited["VASP_AI_HOME"] = "继承 home";
        var priorityStore = LegacyCase(root, "environment-priority", "launcher", new LauncherOptions(), "DATA_DIR=${UNKNOWN}/wrong\nVASP_AI_HOME=${UNKNOWN}/wrong\n", out backend);
        var priority = Migrate(priorityStore, environment);
        Require(priority.DataDirectory == Path.Combine(backend, "继承 data") && priority.VaspAiHome == Path.Combine(backend, "继承 home"), "Inherited environment lost setdefault priority"); checks++;
        inherited.Remove("DATA_DIR"); inherited.Remove("VASP_AI_HOME");
        var explicitStore = LegacyCase(root, "explicit-priority", "desktop-v3", new LauncherOptions { DataDirectory = "明确 data", VaspAiHome = "明确 home" },
            "DATA_DIR=${UNKNOWN}/wrong\nVASP_AI_HOME=${UNKNOWN}/wrong\n", out backend);
        var explicitPaths = Migrate(explicitStore, key => { throw new Exception("Explicit paths consulted the environment"); });
        Require(explicitPaths.DataDirectory == Path.Combine(backend, "明确 data") && explicitPaths.VaspAiHome == Path.Combine(backend, "明确 home"), "Explicit preference path priority changed"); checks++;
        var emptyStore = LegacyCase(root, "empty-dotenv", "desktop-v3", new LauncherOptions(), "DATA_DIR=\nVASP_AI_HOME=\n", out backend);
        var empty = Migrate(emptyStore, environment);
        Require(empty.DataDirectory == backend && empty.VaspAiHome == Path.Combine(user, ".vasp-ai"), "Empty dotenv values changed Python path semantics"); checks++;
        var tildeStore = LegacyCase(root, "tilde-home", "launcher", new LauncherOptions(), "VASP_AI_HOME=~/旧 PP home\nDATA_DIR=old-data\n", out backend);
        Require(Migrate(tildeStore, environment).VaspAiHome == Path.Combine(user, "旧 PP home"), "Home expansion was lost"); checks++;
        var ambiguous = LegacyCase(root, "dynamic-dotenv", "desktop-v3", new LauncherOptions(), "DATA_DIR=${SHARED}/data\nVASP_AI_HOME=old-home\n", out backend);
        string original = Path.Combine(Path.GetDirectoryName(ambiguous.DirectoryPath), "desktop-v3", "preferences.json"), originalBytes = File.ReadAllText(original);
        bool rejected = false;
        try { Migrate(ambiguous, environment); } catch (LauncherException error) { rejected = error.Message.Contains("动态插值") && error.Message.Contains("迁移已停止"); }
        Require(rejected && !File.Exists(ambiguous.Target) && File.ReadAllText(original) == originalBytes, "Ambiguous dynamic path was guessed or original changed"); checks++;
        var multiline = LegacyCase(root, "multiline-dotenv", "desktop-v3", new LauncherOptions(), "UNRELATED='first\nDATA_DIR=hidden\nlast'\n", out backend);
        rejected = false;
        try { Migrate(multiline, environment); } catch (LauncherException error) { rejected = error.Message.Contains("多行"); }
        Require(rejected && !File.Exists(multiline.Target), "Path assignment inside multiline dotenv was interpreted"); checks++;
        var lockedEnv = LegacyCase(root, "locked-dotenv", "desktop-v3", new LauncherOptions(), "DATA_DIR=old-data\n", out backend);
        using (var handle = new FileStream(Path.Combine(backend, ".env"), FileMode.Open, FileAccess.ReadWrite, FileShare.None)) MustFail(() => Migrate(lockedEnv, environment), "read");
        Require(!File.Exists(lockedEnv.Target), "Unreadable old .env was ignored"); checks++;
        var lowerCase = LegacyCase(root, "windows-env-case", "desktop-v3", new LauncherOptions(), "data_dir=case-data\nvasp_ai_home=case-home\n", out backend);
        var lowerPaths = Migrate(lowerCase, environment);
        Require(lowerPaths.DataDirectory == Path.Combine(backend, "case-data") && lowerPaths.VaspAiHome == Path.Combine(backend, "case-home"), "Windows environment key case changed path semantics"); checks++;
        var mixedCase = LegacyCase(root, "mixed-env-case", "desktop-v3", new LauncherOptions(), "DATA_DIR=first\ndata_dir=second\n", out backend);
        rejected = false;
        try { Migrate(mixedCase, environment); } catch (LauncherException error) { rejected = error.Message.Contains("大小写不同"); }
        Require(rejected && !File.Exists(mixedCase.Target), "Ambiguous Windows dotenv casing was guessed"); checks++;
        return checks;
    }
    static int Main(string[] args)
    {
        string root = args[0]; Directory.CreateDirectory(root); int checks = 0;
        string fresh = Case(root, "fresh"); var store = new LauncherPreferencesStore(Path.Combine(fresh, "desktop"));
        var first = store.LoadFormal(fresh, "C:\\当前 包", s => { throw new Exception("No migration expected"); });
        Require(first.EnableAi && first.RootDirectory == "C:\\当前 包" && first.VaspAiHome == Path.Combine(fresh, "desktop-v040-full", "home"), "New defaults changed business location"); checks++;
        store.Save(Options("C:\\中文 安装")); var restored = new LauncherPreferencesStore(store.DirectoryPath).Load();
        Require(restored.RootDirectory == "C:\\中文 安装" && !restored.EnableAi, "Roundtrip lost AI choice/path"); checks++;
        restored.PythonExecutable = ""; store.Save(restored); Require(store.Load().PythonExecutable == "", "Automatic mode was not saved"); checks++;
        string single = Case(root, "single"); Write(Path.Combine(single, "desktop-v040-full"), new LauncherOptions { RootDirectory = "C:\\old", EnableAi = false });
        var migrated = new LauncherPreferencesStore(Path.Combine(single, "desktop")).LoadFormal(single, "C:\\new", s => null);
        Require(migrated.VaspAiHome == Path.Combine(single, "desktop-v040-full", "home") && migrated.DataDirectory == Path.Combine(single, "desktop-v040-full", "data") && !migrated.EnableAi, "Legacy full defaults lost");
        Require(File.Exists(Path.Combine(single, "desktop-v040-full", "preferences.json")), "Old source removed"); checks++;
        var migratedStore = new LauncherPreferencesStore(Path.Combine(single, "desktop"));
        migratedStore.LoadFormal(single, "ignored", s => null);
        Require(migratedStore.BrowserStateDirectory == Path.Combine(single, "desktop-v040-full"), "Original browser state not retained after restart"); checks++;
        string equal = Case(root, "equal"); Write(Path.Combine(equal, "desktop-v040-full"), Options("C:\\same")); Write(Path.Combine(equal, "desktop-v3"), Options("C:\\same"));
        Require(new LauncherPreferencesStore(Path.Combine(equal, "desktop")).LoadFormal(equal, "ignored", s => { throw new Exception("Identical sources prompted"); }).RootDirectory == "C:\\same", "Equal sources failed"); checks++;
        string conflict = Case(root, "conflict"); Write(Path.Combine(conflict, "desktop-v040-full"), Options("C:\\full")); Write(Path.Combine(conflict, "desktop-v3"), Options("C:\\normal")); bool selected = false;
        var cs = new LauncherPreferencesStore(Path.Combine(conflict, "desktop"));
        Require(cs.LoadFormal(conflict, "ignored", s => { selected = true; return s[1]; }).RootDirectory == "C:\\normal" && selected, "Conflict was guessed"); checks++;
        File.WriteAllText(Path.Combine(conflict, "desktop-v040-full", "preferences.json"), "broken");
        Require(cs.LoadFormal(conflict, "ignored", s => null).RootDirectory == "C:\\normal", "Canonical overwritten by old source"); checks++;
        string cancel = Case(root, "cancel"); Write(Path.Combine(cancel, "desktop-v040-full"), Options("full")); Write(Path.Combine(cancel, "desktop-v3"), Options("normal"));
        try { new LauncherPreferencesStore(Path.Combine(cancel, "desktop")).LoadFormal(cancel, "ignored", s => null); throw new Exception("Cancelled migration accepted"); } catch (LauncherException) { }
        Require(!File.Exists(Path.Combine(cancel, "desktop", "preferences.json")), "Cancel wrote preferences"); checks++;
        string invalidOld = Case(root, "invalid-old"); Directory.CreateDirectory(Path.Combine(invalidOld, "desktop-v040-full")); File.WriteAllText(Path.Combine(invalidOld, "desktop-v040-full", "preferences.json"), "broken");
        MustFail(() => new LauncherPreferencesStore(Path.Combine(invalidOld, "desktop")).LoadFormal(invalidOld, "ignored", s => null), "read");
        Require(!File.Exists(Path.Combine(invalidOld, "desktop", "preferences.json")), "Unreadable legacy treated as first use"); checks++;
        string broken = Case(root, "broken"); var bs = new LauncherPreferencesStore(broken); File.WriteAllText(bs.Target, "{bad");
        MustFail(() => bs.Load(), "read"); MustFail(() => bs.Save(Options("new")), "read"); Require(File.ReadAllText(bs.Target) == "{bad", "Corrupt original overwritten"); checks++;
        var locked = new LauncherPreferencesStore(Case(root, "locked")); locked.Save(Options("old")); string oldBytes = File.ReadAllText(locked.Target);
        using (var handle = new FileStream(locked.Target, FileMode.Open, FileAccess.ReadWrite, FileShare.None)) MustFail(() => locked.Save(Options("new")), "read");
        Require(File.ReadAllText(locked.Target) == oldBytes, "Read-lock failure changed file"); checks++;
        using (var handle = new FileStream(locked.Target, FileMode.Open, FileAccess.Read, FileShare.Read)) MustFail(() => locked.Save(Options("new")), "replace");
        Require(File.ReadAllText(locked.Target) == oldBytes, "Replace failure changed old file"); checks++;
        string blocked = Path.Combine(root, "blocked-directory"); File.WriteAllText(blocked, "preserve-me");
        MustFail(() => new LauncherPreferencesStore(blocked).Save(Options("new")), "write"); Require(File.ReadAllText(blocked) == "preserve-me", "Directory failure overwrote blocker"); checks++;
        File.WriteAllText(locked.Target + ".tmp", "foreign-temp");
        Task.WaitAll(Enumerable.Range(0, 12).Select(i => Task.Run(() => new LauncherPreferencesStore(locked.DirectoryPath).Save(Options("concurrent-" + i)))).ToArray());
        Require(locked.Load().RootDirectory.StartsWith("concurrent-") && File.ReadAllText(locked.Target + ".tmp") == "foreign-temp", "Concurrent save/temp ownership failed");
        Require(Directory.GetFiles(locked.DirectoryPath, "*.previous").Length == 0 && Directory.GetFiles(locked.DirectoryPath, "*.tmp").Length == 1, "Owned temporary files leaked"); checks++;
        var diagnostic = new JavaScriptSerializer().DeserializeObject(File.ReadAllText(Path.Combine(locked.DirectoryPath, "last-preferences-operation.json")));
        Require(diagnostic != null && !File.ReadAllText(Path.Combine(locked.DirectoryPath, "preferences-events.jsonl")).Contains("C:\\\\中文 Python"), "Diagnostics exposed settings content"); checks++;
        checks += LegacyDirectoryChecks(root);
        Console.WriteLine(new JavaScriptSerializer().Serialize(new { passed = true, checks })); return 0;
    }
}
