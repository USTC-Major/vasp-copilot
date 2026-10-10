using System;
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
        Console.WriteLine(new JavaScriptSerializer().Serialize(new { passed = true, checks })); return 0;
    }
}
