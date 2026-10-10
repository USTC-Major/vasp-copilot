using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Text.RegularExpressions;
using System.Threading;
using System.Web.Script.Serialization;
using Microsoft.Win32;

namespace VaspCopilot.Launcher
{
    public class LauncherException : Exception { public LauncherException(string message) : base(message) {} }
    public sealed class PythonRuntimePolicy
    {
        public sealed class Entry { public string version { get; set; } public string status { get; set; } public string reason { get; set; } }
        public int schema { get; set; }
        public string platform { get; set; }
        public string architecture { get; set; }
        public string implementation { get; set; }
        public Entry[] versions { get; set; }
        public string Supported { get { return String.Join(" / ", versions.Where(x => x.status == "supported").Select(x => x.version).OrderBy(x => new Version(x))); } }
        public static PythonRuntimePolicy Load(string root)
        {
            try
            {
                var policy = new JavaScriptSerializer().Deserialize<PythonRuntimePolicy>(File.ReadAllText(Path.Combine(root, "launcher", "python-support.json"), Encoding.UTF8));
                if (policy.schema != 1 || policy.platform != "win32" || policy.architecture != "x64" || policy.implementation != "CPython"
                    || policy.versions == null || policy.versions.Length == 0 || policy.versions.Length > 16
                    || policy.versions.Select(x => x.version).Distinct().Count() != policy.versions.Length
                    || !policy.versions.Any(x => x.status == "supported")) throw new FormatException();
                foreach (var entry in policy.versions)
                {
                    var parsed = new Version(entry.version);
                    if (parsed.Major != 3 || parsed.Build != -1 || entry.version != "3." + parsed.Minor
                        || (entry.status != "supported" && entry.status != "blocked")
                        || (entry.status == "blocked" && String.IsNullOrWhiteSpace(entry.reason))) throw new FormatException();
                }
                return policy;
            }
            catch { throw new LauncherException("安装目录缺少或损坏 launcher/python-support.json，请恢复完整应用目录。"); }
        }
    }
    public sealed class LauncherOptions
    {
        public string RootDirectory { get; set; }
        public string PythonExecutable { get; set; }
        public string VaspAiHome { get; set; }
        public string DataDirectory { get; set; }
        public int ToolboxPort { get; set; }
        public int AiPort { get; set; }
        public int WebPort { get; set; }
        public bool EnableAi { get; set; }
        public bool FullFeatures { get; set; }
        public bool AutoPrepareEnvironment { get; set; }
        public bool IsolatedProfile { get; set; }
        public LauncherOptions() { ToolboxPort = 8000; AiPort = 8500; WebPort = 5173; RootDirectory = ""; PythonExecutable = ""; VaspAiHome = ""; DataDirectory = ""; }
    }
    public sealed class ServiceSnapshot
    {
        public string Key { get; set; } public string Label { get; set; }
        public string State { get; set; } public string Detail { get; set; }
        public int Port { get; set; } public int Pid { get; set; }
    }
    public sealed class LauncherSnapshot
    {
        public bool Busy { get; set; } public string Message { get; set; } public string StartupStage { get; set; }
        public string Version { get; set; } public string WebUrl { get; set; }
        public List<ServiceSnapshot> Services { get; set; }
    }
    public sealed class PreferencesException : LauncherException
    {
        public string Stage { get; private set; }
        public string Category { get; private set; }
        private static string Kind(Exception error) { var known = error as PreferencesException; return known == null ? error.GetType().Name : known.Category; }
        public PreferencesException(string stage, string path, Exception error)
            : base("启动设置" + (stage == "read" ? "读取" : "保存") + "失败（" + Kind(error) + "，阶段 " + stage + "）。\n文件：" + path
                + "\n未确认保存成功，请检查文件权限、占用或 JSON 内容后重试；不要删除原有配置和业务数据。") { Stage = stage; Category = Kind(error); }
    }
    public sealed class PreferencesSource
    {
        public string Path { get; set; }
        public LauncherOptions Options { get; set; }
    }
    // One explicitly initialized context is shared by the form and controller.
    // The facade below also preserves isolated controller harness compatibility.
    public sealed class LauncherPreferencesStore
    {
        public string DirectoryPath { get; private set; }
        public string Target { get { return Path.Combine(DirectoryPath, "preferences.json"); } }
        public string RuntimeStateDirectory { get; private set; }
        public string BrowserStateDirectory { get; private set; }
        public string Source { get; private set; }
        public LauncherPreferencesStore(string directory, string runtimeStateDirectory = null)
        {
            DirectoryPath = Path.GetFullPath(directory);
            RuntimeStateDirectory = Path.GetFullPath(runtimeStateDirectory ?? directory);
            BrowserStateDirectory = RuntimeStateDirectory;
            Source = Target;
        }
        public static LauncherOptions Copy(LauncherOptions o)
        {
            return new LauncherOptions { RootDirectory = o.RootDirectory, PythonExecutable = o.PythonExecutable,
                VaspAiHome = o.VaspAiHome, DataDirectory = o.DataDirectory, EnableAi = o.EnableAi,
                FullFeatures = o.FullFeatures, AutoPrepareEnvironment = o.AutoPrepareEnvironment, IsolatedProfile = o.IsolatedProfile };
        }
        public static void Assign(LauncherOptions destination, LauncherOptions o)
        {
            destination.RootDirectory = o.RootDirectory; destination.PythonExecutable = o.PythonExecutable;
            destination.VaspAiHome = o.VaspAiHome; destination.DataDirectory = o.DataDirectory; destination.EnableAi = o.EnableAi;
        }
        private static string Content(LauncherOptions o)
        {
            return new JavaScriptSerializer().Serialize(new { o.RootDirectory, o.PythonExecutable, o.VaspAiHome, o.DataDirectory, o.EnableAi });
        }
        private static LauncherOptions Read(string path)
        {
            try
            {
                string text;
                using (var stream = new FileStream(path, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    if (stream.Length > 1024 * 1024) throw new FormatException("Oversized preferences");
                    using (var reader = new StreamReader(stream, Encoding.UTF8)) text = reader.ReadToEnd();
                }
                var fields = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(text);
                if (fields == null || !fields.ContainsKey("RootDirectory")) throw new FormatException("Missing preferences fields");
                foreach (string key in new[] { "RootDirectory", "PythonExecutable", "VaspAiHome", "DataDirectory" })
                    if (fields.ContainsKey(key) && !(fields[key] is string)) throw new FormatException("Invalid preferences field");
                if (fields.ContainsKey("EnableAi") && !(fields["EnableAi"] is bool)) throw new FormatException("Invalid AI choice");
                var o = new JavaScriptSerializer().Deserialize<LauncherOptions>(text);
                o.RootDirectory = o.RootDirectory ?? ""; o.PythonExecutable = o.PythonExecutable ?? "";
                o.VaspAiHome = o.VaspAiHome ?? ""; o.DataDirectory = o.DataDirectory ?? "";
                o.ToolboxPort = 8000; o.AiPort = 8500; o.WebPort = 5173;
                return o;
            }
            catch (FileNotFoundException) { return null; }
            catch (DirectoryNotFoundException) { return null; }
            catch (Exception error) { throw new PreferencesException("read", path, error); }
        }
        public LauncherOptions Load()
        {
            try { return Read(Target) ?? new LauncherOptions(); }
            catch (Exception error) { Record("read", "failed", error); throw; }
        }
        public LauncherOptions LoadFormal(string appDirectory, string packageRoot, Func<List<PreferencesSource>, PreferencesSource> select)
        { return LoadFormal(appDirectory, packageRoot, select, Environment.GetEnvironmentVariable); }
        // The environment is injectable so migration tests never read real user settings.
        public LauncherOptions LoadFormal(string appDirectory, string packageRoot, Func<List<PreferencesSource>, PreferencesSource> select, Func<string, string> environment)
        {
            try { return LoadFormalCore(appDirectory, packageRoot, select, environment); }
            catch (Exception error) { Record("read", "failed", error); throw; }
        }
        private LauncherOptions LoadFormalCore(string appDirectory, string packageRoot, Func<List<PreferencesSource>, PreferencesSource> select, Func<string, string> environment)
        {
            var current = Read(Target);
            if (current != null)
            {
                try
                {
                    var metadata = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(Target, Encoding.UTF8));
                    string[] known = new[] { DirectoryPath, Path.Combine(appDirectory, "desktop-v040-full"), Path.Combine(appDirectory, "desktop-v3"), Path.Combine(appDirectory, "launcher") };
                    if (metadata.ContainsKey("BrowserStateDirectory"))
                    {
                        string browser = metadata["BrowserStateDirectory"] as string;
                        if (browser == null || !known.Any(p => String.Equals(p, browser, StringComparison.OrdinalIgnoreCase))) throw new FormatException("Unknown browser state source");
                        BrowserStateDirectory = browser;
                    }
                    Source = metadata.ContainsKey("MigrationSource") ? metadata["MigrationSource"] as string : Target;
                    if (Source == null || !known.Any(p => String.Equals(Path.Combine(p, "preferences.json"), Source, StringComparison.OrdinalIgnoreCase))) throw new FormatException("Unknown migration source");
                }
                catch (Exception error) { throw new PreferencesException("read", Target, error); }
                Record("read", "ok", null); return current;
            }
            var sources = new List<PreferencesSource>();
            foreach (string oldName in new[] { "desktop-v040-full", "desktop-v3", "launcher" })
            {
                string directory = Path.Combine(appDirectory, oldName), file = Path.Combine(directory, "preferences.json");
                var old = Read(file);
                if (old == null) continue;
                // Full mode previously supplied these defaults outside preferences.json.
                if (oldName == "desktop-v040-full")
                {
                    if (String.IsNullOrWhiteSpace(old.VaspAiHome)) old.VaspAiHome = Path.Combine(directory, "home");
                    if (String.IsNullOrWhiteSpace(old.DataDirectory)) old.DataDirectory = Path.Combine(directory, "data");
                }
                PreserveLegacyDirectories(old, file, environment);
                sources.Add(new PreferencesSource { Path = file, Options = old });
            }
            if (sources.Count == 0)
                return new LauncherOptions { RootDirectory = packageRoot, EnableAi = true,
                    VaspAiHome = Path.Combine(appDirectory, "desktop-v040-full", "home"), DataDirectory = Path.Combine(appDirectory, "desktop-v040-full", "data") };
            var chosen = sources[0];
            if (sources.Any(s => Content(s.Options) != Content(chosen.Options)))
            {
                chosen = select(sources);
                if (chosen == null || !sources.Contains(chosen)) throw new LauncherException("已取消旧启动设置迁移。原配置和业务数据未改动。");
            }
            Source = chosen.Path;
            BrowserStateDirectory = Path.GetDirectoryName(chosen.Path);
            Save(chosen.Options); Record("migration", "ok", null);
            return Copy(chosen.Options);
        }
        private static LauncherException LegacyPathError(string source, string reason)
        {
            return new LauncherException("无法可靠确认旧启动设置的数据路径：" + source + "\n" + reason
                + "\n迁移已停止，原配置和业务数据未改动。请先在旧环境确认实际绝对路径，并在旧 preferences.json 明确填写 VaspAiHome / DataDirectory 后重试；不要删除旧目录。");
        }
        private static Dictionary<string, string> LegacyEnvironment(string backend, string source)
        {
            string file = Path.Combine(backend, ".env"), text;
            try
            {
                using (var stream = new FileStream(file, FileMode.Open, FileAccess.Read, FileShare.Read))
                {
                    if (stream.Length > 1024 * 1024) throw LegacyPathError(source, "旧 backend/.env 过大，未读取其内容。");
                    using (var reader = new StreamReader(stream, new UTF8Encoding(false, true))) text = reader.ReadToEnd();
                }
            }
            catch (FileNotFoundException) { return new Dictionary<string, string>(); }
            catch (LauncherException) { throw; }
            catch (Exception error) { throw new PreferencesException("read", file, error); }
            var result = new Dictionary<string, string>();
            var spellings = new Dictionary<string, string>();
            // runtime.py reads this exact file, not a parent/repository .env. Only
            // path fields are retained; credentials are never imported or logged.
            foreach (string row in Regex.Split(text, "\\r?\\n"))
            {
                var match = Regex.Match(row, @"^\s*(?:export\s+)?(?<key>'[^']+'|[A-Za-z_][A-Za-z0-9_]*)\s*(?:=\s*(?<value>.*))?$");
                if (!match.Success || !match.Groups["value"].Success) continue;
                string spelling = match.Groups["key"].Value.Trim('\''), key = spelling.ToUpperInvariant(), value = match.Groups["value"].Value.Trim();
                bool relevant = new[] { "VASP_AI_HOME", "DATA_DIR", "USERPROFILE", "HOMEDRIVE", "HOMEPATH" }.Contains(key);
                if (value.Length > 0 && (value[0] == '\'' || value[0] == '"'))
                {
                    char quote = value[0]; int end = 1;
                    for (; end < value.Length; end++) { if (value[end] == '\\') { end++; continue; } if (value[end] == quote) break; }
                    // Do not interpret path assignments inside an unrelated multiline value.
                    if (end >= value.Length) throw LegacyPathError(source, "旧 backend/.env 含多行或未闭合引号，不能安全解释路径。");
                    string tail = value.Substring(end + 1).TrimStart();
                    if (tail.Length > 0 && tail[0] != '#') { if (relevant) throw LegacyPathError(source, "旧 backend/.env 的路径赋值格式不明确。"); continue; }
                    value = value.Substring(1, end - 1);
                    if (relevant)
                        value = Regex.Replace(value, quote == '\'' ? @"\\[\\']" : @"\\[\\'""abfnrtv]", m => {
                            char escaped = m.Value[1];
                            const string codes = "abfnrtv", decoded = "\a\b\f\n\r\t\v";
                            int index = codes.IndexOf(escaped); return index < 0 ? escaped.ToString() : decoded[index].ToString();
                        });
                }
                else value = Regex.Replace(value, @"\s+#.*$", "").TrimEnd();
                if (relevant)
                {
                    if (spellings.ContainsKey(key) && spellings[key] != spelling)
                        throw LegacyPathError(source, "旧 backend/.env 同时包含大小写不同的路径键，优先级不明确。");
                    spellings[key] = spelling; result[key] = value;
                }
            }
            return result;
        }
        private static string LegacyValue(string key, Dictionary<string, string> values, Func<string, string> environment, string source)
        {
            string inherited = environment(key);
            if (inherited != null) return inherited; // runtime.py uses os.environ.setdefault.
            string value;
            if (!values.TryGetValue(key, out value)) return null;
            if (value.Contains("${")) throw LegacyPathError(source, "旧 backend/.env 的 " + key + " 含动态插值；未猜测展开结果。");
            return value;
        }
        private static string LegacyAbsolutePath(string value, string backend, string source)
        {
            try
            {
                if (value.Length >= 2 && value[1] == ':' && (value.Length == 2 || (value[2] != '\\' && value[2] != '/')))
                    throw LegacyPathError(source, "路径为依赖驱动器当前目录的相对形式，无法可靠保留。");
                if (Path.IsPathRooted(value) && Path.GetPathRoot(value).Length == 1)
                    value = Path.Combine(Path.GetPathRoot(backend), value.TrimStart('\\', '/'));
                return Path.GetFullPath(Path.IsPathRooted(value) ? value : Path.Combine(backend, value));
            }
            catch (LauncherException) { throw; }
            catch { throw LegacyPathError(source, "旧数据路径无效，无法转换为明确的绝对路径。"); }
        }
        private static void PreserveLegacyDirectories(LauncherOptions options, string source, Func<string, string> environment)
        {
            bool homeDefault = String.IsNullOrWhiteSpace(options.VaspAiHome), dataDefault = String.IsNullOrWhiteSpace(options.DataDirectory);
            bool expandHome = !homeDefault && options.VaspAiHome.StartsWith("~", StringComparison.Ordinal);
            bool needsBackend = homeDefault || dataDefault || expandHome
                || !Path.IsPathRooted(options.VaspAiHome) || !Path.IsPathRooted(options.DataDirectory)
                || Path.GetPathRoot(options.VaspAiHome).Length < 3 || Path.GetPathRoot(options.DataDirectory).Length < 3;
            string backend = null;
            if (needsBackend)
            {
                if (String.IsNullOrWhiteSpace(options.RootDirectory) || !Path.IsPathRooted(options.RootDirectory) || Path.GetPathRoot(options.RootDirectory).Length < 3)
                    throw LegacyPathError(source, "旧安装目录不是可确认的绝对路径。");
                backend = Path.GetFullPath(Path.Combine(options.RootDirectory, "backend"));
                if (!Directory.Exists(backend)) throw LegacyPathError(source, "旧安装目录的 backend 不存在或不可访问，无法确认旧默认路径及 .env。");
            }
            var values = homeDefault || dataDefault || expandHome ? LegacyEnvironment(backend, source) : new Dictionary<string, string>();
            string home = homeDefault ? LegacyValue("VASP_AI_HOME", values, environment, source) : options.VaspAiHome;
            if (String.IsNullOrEmpty(home) || home.StartsWith("~", StringComparison.Ordinal))
            {
                string userHome = LegacyValue("USERPROFILE", values, environment, source);
                if (userHome == null)
                {
                    string homePath = LegacyValue("HOMEPATH", values, environment, source);
                    if (homePath == null) throw LegacyPathError(source, "旧 Windows 用户 home 无法确认。");
                    userHome = (LegacyValue("HOMEDRIVE", values, environment, source) ?? "") + homePath;
                }
                if (String.IsNullOrEmpty(home)) home = Path.Combine(userHome, ".vasp-ai");
                else if (home == "~" || home.StartsWith("~/", StringComparison.Ordinal) || home.StartsWith("~\\", StringComparison.Ordinal)) home = Path.Combine(userHome, home.Length == 1 ? "" : home.Substring(2));
                else throw LegacyPathError(source, "旧 home 使用了无法可靠解释的 ~用户 路径。");
            }
            string data = dataDefault ? LegacyValue("DATA_DIR", values, environment, source) ?? "data" : options.DataDirectory;
            options.VaspAiHome = LegacyAbsolutePath(home, backend, source);
            options.DataDirectory = LegacyAbsolutePath(data, backend, source);
        }
        private void Record(string stage, string result, Exception error)
        {
            try
            {
                Directory.CreateDirectory(DirectoryPath);
                string record = new JavaScriptSerializer().Serialize(new { utc = DateTime.UtcNow.ToString("o"), source = Source, target = Target,
                    stage, result, category = error == null ? null : error is PreferencesException ? ((PreferencesException)error).Category : error.GetType().Name });
                File.WriteAllText(Path.Combine(DirectoryPath, "last-preferences-operation.json"), record, Encoding.UTF8);
                File.AppendAllText(Path.Combine(DirectoryPath, "preferences-events.jsonl"), record + Environment.NewLine, Encoding.UTF8);
            }
            catch { /* A diagnostic write must never convert failure into success. */ }
        }
        public void Save(LauncherOptions options)
        {
            string stage = "lock", temp = Target + "." + Guid.NewGuid().ToString("N") + ".tmp", backup = temp + ".previous";
            bool acquired = false, replaced = false; string mutexName;
            using (var hash = SHA256.Create()) mutexName = "Local\\VaspCopilotPreferences-" + BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(Target.ToUpperInvariant()))).Replace("-", "");
            using (var mutex = new Mutex(false, mutexName))
            {
                try
                {
                    try { acquired = mutex.WaitOne(5000); } catch (AbandonedMutexException) { acquired = true; }
                    if (!acquired) throw new IOException("Preferences busy");
                    stage = "read"; var previous = Read(Target); // Never overwrite unreadable/corrupt existing settings.
                    stage = "write"; Directory.CreateDirectory(DirectoryPath);
                    var fields = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(Content(options));
                    fields["MigrationSource"] = Source; fields["BrowserStateDirectory"] = BrowserStateDirectory;
                    byte[] bytes = Encoding.UTF8.GetBytes(new JavaScriptSerializer().Serialize(fields));
                    using (var stream = new FileStream(temp, FileMode.CreateNew, FileAccess.Write, FileShare.None)) { stream.Write(bytes, 0, bytes.Length); stream.Flush(true); }
                    stage = "replace";
                    if (previous != null) File.Replace(temp, Target, backup); else File.Move(temp, Target);
                    replaced = true; stage = "verify";
                    var saved = Read(Target);
                    if (saved == null || Content(saved) != Content(options)) throw new IOException("Read-back mismatch");
                    Record("verify", "ok", null);
                }
                catch (Exception error)
                {
                    if (replaced)
                    {
                        try
                        {
                            if (File.Exists(backup)) File.Replace(backup, Target, null);
                            else File.Move(Target, Target + ".unverified-" + Guid.NewGuid().ToString("N"));
                        }
                        catch { stage += "-restore-failed"; /* Preserve the backup for explicit recovery. */ }
                    }
                    Record(stage, "failed", error); throw new PreferencesException(stage, Target, error);
                }
                finally
                {
                    try { if (File.Exists(temp)) File.Delete(temp); } catch { }
                    // Keep recovery evidence if restoring the old file failed.
                    if (!stage.EndsWith("restore-failed")) try { if (File.Exists(backup)) File.Delete(backup); } catch { }
                    if (acquired) mutex.ReleaseMutex();
                }
            }
        }
    }
    public static class LauncherPreferences
    {
        private static LauncherPreferencesStore store;
        public static void Initialize(LauncherPreferencesStore context) { if (context == null) throw new ArgumentNullException("context"); store = context; }
        public static LauncherPreferencesStore Store
        {
            get
            {
                if (store == null) store = new LauncherPreferencesStore(Environment.GetEnvironmentVariable("VASP_LAUNCHER_STATE_DIR")
                    ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VASP-Copilot", "launcher"));
                return store;
            }
        }
        internal static string DirectoryPath { get { return Store.DirectoryPath; } }
        public static string StateDirectory { get { return DirectoryPath; } }
        public static LauncherOptions Load() { return Store.Load(); }
        public static void Save(LauncherOptions options) { Store.Save(options); }
    }

    public sealed class LauncherController : IDisposable
    {
        private sealed class Service
        {
            public string Key, Label, State = "stopped", Detail = "未启动", StopFile, ErrorFile, Token;
            public int Port, RuntimePid; public OwnedProcess Process;
            public int HealthFailures; public DateTime FirstHealthFailure;
        }
        private readonly object sync = new object();
        private readonly List<Service> services = new List<Service>();
        private int busy;
        private string message = "核对安装目录和 Python 环境；智能模式按已保存选择启动。", version = "", webUrl = "", root = "", fingerprint = "";
        private string startupStage = "";
        private bool disposed;
        private readonly Timer healthTimer;
        private int polling;
        private int closing;
        private int cancellationRequested;
        public void CancelStartup() { lock (sync) Interlocked.Exchange(ref cancellationRequested, 1); }
        private void FinishStartup()
        {
            // Consume cancellation only after the operation observes it. A cancellation
            // before Start remains effective, while a subsequent explicit retry is fresh.
            // Dispose sets closing before acquiring this same lock to request cancellation.
            lock (sync) if (closing == 0) Interlocked.Exchange(ref cancellationRequested, 0);
            Interlocked.Exchange(ref busy, 0);
        }
        private sealed class StartupCancelledException : LauncherException { public StartupCancelledException(string detail = null) : base(detail ?? "启动已取消，正在退出并清理所属服务。") {} }
        private void ThrowIfCancelled() { if (cancellationRequested != 0) throw new StartupCancelledException(); }
        public string LogDirectory { get; private set; }
        public bool IsRunning { get { lock (sync) return services.Any(s => s.Process != null); } }
        private readonly string runtimeScript;
        public string SelectedPython { get; private set; }
        public string SelectedPythonDescription { get; private set; }
        private PythonRuntimePolicy pythonPolicy;
        private readonly List<string> pythonProbeFailures = new List<string>();
        private string ProbeFailed(string executable, string reason)
        {
            pythonProbeFailures.Add(executable + "：" + reason);
            return null;
        }
        public string EnvironmentFailureLogPath { get; private set; }
        public int StartupAttempts { get; private set; }
        public LauncherController(string runtimeScriptPath = null)
        {
            runtimeScript = runtimeScriptPath;
            LogDirectory = Path.Combine(LauncherPreferences.DirectoryPath, "logs");
            Directory.CreateDirectory(LogDirectory);
            services.Add(new Service { Key = "toolbox", Label = "Toolbox 主服务", Port = 8000 });
            services.Add(new Service { Key = "web", Label = "页面服务", Port = 5173 });
            services.Add(new Service { Key = "ai", Label = "智能模式（可选）", Port = 8500, State = "disabled", Detail = "未选择启动" });
            healthTimer = new Timer(PollHealth, null, 1500, 1500);
        }
        private void PollHealth(object state)
        {
            if (busy != 0 || disposed || Interlocked.CompareExchange(ref polling, 1, 0) != 0) return;
            try
            {
                // Recheck after acquiring polling: Begin may have accepted an operation
                // between the first busy read and the polling compare-exchange.
                if (busy != 0 || disposed) return;
                List<Service> owned;
                lock (sync) owned = services.Where(s => s.Process != null).ToList();
                foreach (Service service in owned)
                {
                    OwnedProcess process = service.Process;
                    if (process != null && (process.HasExited || process.RuntimeExited))
                    {
                        try
                        {
                            process.Dispose();
                            lock (sync) { service.Process = null; service.RuntimePid = 0; service.State = "error"; service.Detail = "所属服务已退出，已清理其后代进程。不会自动重启。"; }
                        }
                        catch { SetState(service, "error", "所属服务已退出，但后代清理未核验；请再次停止。"); }
                    }
                    else
                    {
                        HealthResult health = CheckHealth(service);
                        lock (sync)
                        {
                            if (health == HealthResult.Healthy)
                            {
                                service.HealthFailures = 0; service.FirstHealthFailure = DateTime.MinValue;
                                service.State = "ready"; service.Detail = "HTTP 健康及进程归属已确认";
                            }
                            else if (health == HealthResult.Unavailable)
                            {
                                if (service.HealthFailures++ == 0) service.FirstHealthFailure = DateTime.UtcNow;
                                bool persistent = service.HealthFailures >= 3 && DateTime.UtcNow - service.FirstHealthFailure >= TimeSpan.FromSeconds(8);
                                service.State = persistent ? "error" : "degraded";
                                service.Detail = persistent ? "所属进程仍在，但服务持续未响应；恢复后将自动显示原页面，也可重试启动。" : "所属进程仍在，服务响应暂缓，正在复核；当前页面保留。";
                            }
                            else
                            {
                                service.State = "error";
                                service.Detail = health == HealthResult.Exited ? "所属服务已退出；不会自动重启。" : "服务进程归属或身份校验失败；未将此响应视为就绪，可停止或重启。";
                            }
                        }
                    }
                }
            }
            catch { /* A shutdown race cannot take down the window. */ }
            finally { Interlocked.Exchange(ref polling, 0); }
        }
        private void Begin()
        {
            if (disposed || closing != 0) throw new LauncherException("启动器已退出。");
            if (Interlocked.CompareExchange(ref busy, 1, 0) != 0) throw new LauncherException("正在处理上一次操作，请稍候。");
            if (closing != 0) { Interlocked.Exchange(ref busy, 0); throw new LauncherException("启动器正在退出。"); }
            while (polling != 0) Thread.Sleep(10);
        }
        public void Start(LauncherOptions options)
        {
            options = CloneOptions(options);
            Begin();
            try { StartInternal(options); }
            finally { FinishStartup(); }
        }
        public void Stop()
        {
            Begin();
            try { StopInternal(); }
            finally { Interlocked.Exchange(ref busy, 0); }
        }
        public void Restart(LauncherOptions options)
        {
            options = CloneOptions(options);
            Begin();
            try { SetStage("environment", "正在停止旧服务并重新检查运行环境…"); StopInternal(); StartInternal(options); }
            finally { FinishStartup(); }
        }
        private static LauncherOptions CloneOptions(LauncherOptions options)
        {
            return options == null ? null : new JavaScriptSerializer().Deserialize<LauncherOptions>(new JavaScriptSerializer().Serialize(options));
        }
        private static IEnumerable<string> PythonCandidates(LauncherOptions options)
        {
            if (!String.IsNullOrWhiteSpace(options.PythonExecutable))
            {
                string selected = Path.GetFullPath(options.PythonExecutable);
                if (!File.Exists(selected)) throw new LauncherException("已保存的 Python 路径不存在。请在启动设置点击“自动检测”，或重新选择解释器：" + selected);
                yield return selected;
                yield break;
            }
            var candidates = new List<string>();
            foreach (string directory in new [] { Path.Combine(options.RootDirectory, ".venv"), Path.Combine(options.RootDirectory, "backend", ".venv"), Environment.GetEnvironmentVariable("VIRTUAL_ENV"), Environment.GetEnvironmentVariable("CONDA_PREFIX") })
                if (!String.IsNullOrWhiteSpace(directory)) { candidates.Add(Path.Combine(directory, "Scripts", "python.exe")); candidates.Add(Path.Combine(directory, "python.exe")); }
            foreach (string path in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
            {
                try { candidates.Add(Path.Combine(path.Trim('"'), "python.exe")); } catch { }
            }
            if (options.FullFeatures && options.AutoPrepareEnvironment)
            {
                candidates.AddRange(RegisteredPythons());
                foreach (string parent in new [] { Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python"), Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles), Environment.GetFolderPath(Environment.SpecialFolder.ProgramFilesX86), @"C:\" })
                    try { foreach (string directory in Directory.EnumerateDirectories(parent, "Python*", SearchOption.TopDirectoryOnly).Take(32)) candidates.Add(Path.Combine(directory, "python.exe")); } catch (IOException) { } catch (UnauthorizedAccessException) { }
                foreach (string name in new [] { "anaconda3", "miniconda3" })
                    candidates.Add(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), name, "python.exe"));
                candidates.AddRange(CondaPythons(Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.UserProfile), ".conda", "environments.txt")));
            }
            foreach (string candidate in ExistingPythonPaths(candidates).Take(options.FullFeatures && options.AutoPrepareEnvironment ? 64 : 12)) yield return candidate;
        }
        private static IEnumerable<string> ExistingPythonPaths(IEnumerable<string> candidates)
        {
            var seen = new HashSet<string>(StringComparer.OrdinalIgnoreCase);
            string aliases = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Microsoft", "WindowsApps");
            foreach (string candidate in candidates)
            {
                string path;
                try
                {
                    if (String.IsNullOrWhiteSpace(candidate) || !Path.IsPathRooted(candidate)) continue;
                    path = Path.GetFullPath(candidate);
                    // Store execution aliases are not interpreters; the real registered
                    // Store installation and py launcher remain eligible.
                    if (String.Equals(Path.GetDirectoryName(path), aliases, StringComparison.OrdinalIgnoreCase) || !File.Exists(path)) continue;
                }
                catch { continue; }
                if (seen.Add(path)) yield return path;
            }
        }
        private static IEnumerable<string> CondaPythons(string file)
        {
            try
            {
                if (!File.Exists(file) || new FileInfo(file).Length > 128 * 1024) return new string[0];
                return File.ReadAllLines(file, Encoding.UTF8).Take(256).Where(x => !String.IsNullOrWhiteSpace(x))
                    .Select(x => Path.Combine(x.Trim(), "python.exe")).ToArray();
            }
            catch { return new string[0]; }
        }
        private static IEnumerable<string> RegistryPythons(RegistryKey python)
        {
            var paths = new List<string>();
            if (python == null) return paths;
            foreach (string company in python.GetSubKeyNames().Take(64))
                try
                {
                    using (var provider = python.OpenSubKey(company))
                        if (provider != null) foreach (string tag in provider.GetSubKeyNames().Take(64))
                            using (var install = provider.OpenSubKey(tag + "\\InstallPath"))
                                if (install != null)
                                {
                                    string executable = install.GetValue("ExecutablePath") as string;
                                    string directory = install.GetValue("") as string;
                                    if (!String.IsNullOrWhiteSpace(executable)) paths.Add(executable);
                                    if (!String.IsNullOrWhiteSpace(directory)) paths.Add(Path.Combine(directory, "python.exe"));
                                }
                }
                catch (System.Security.SecurityException) { } catch (UnauthorizedAccessException) { } catch (IOException) { } catch (ArgumentException) { }
            return paths;
        }
        private static IEnumerable<string> RegisteredPythons()
        {
            var paths = new List<string>();
            foreach (var hive in new [] { RegistryHive.CurrentUser, RegistryHive.LocalMachine })
                foreach (var view in new [] { RegistryView.Registry64, RegistryView.Registry32 })
                    try
                    {
                        using (var root = RegistryKey.OpenBaseKey(hive, view))
                        using (var python = root.OpenSubKey(@"Software\Python")) paths.AddRange(RegistryPythons(python));
                    }
                    catch (System.Security.SecurityException) { } catch (UnauthorizedAccessException) { } catch (IOException) { }
            return paths;
        }
        private static IDictionary<string, string> PreparationEnvironment()
        {
            // Pass only OS plumbing. Preparation must never inherit model, SSH,
            // materials, reviewer, .env, Python path or pip configuration secrets.
            var allowed = new HashSet<string>(new [] { "SYSTEMROOT", "SYSTEMDRIVE", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP", "COMSPEC", "PROCESSOR_ARCHITECTURE", "NUMBER_OF_PROCESSORS" }, StringComparer.OrdinalIgnoreCase);
            var overrides = new Dictionary<string, string>(StringComparer.OrdinalIgnoreCase);
            foreach (System.Collections.DictionaryEntry entry in Environment.GetEnvironmentVariables())
                if (!allowed.Contains((string)entry.Key)) overrides[(string)entry.Key] = null;
            overrides["PYTHONUTF8"] = "1";
            overrides["PYTHON_MANAGER_AUTOMATIC_INSTALL"] = "false";
            return overrides;
        }
        private string ProbeBasePython(string executable, string prefix, string state)
        {
            // The request directory is already unique; probes run sequentially.
            // Avoid adding another GUID to .NET Framework's legacy path budget.
            string result = Path.Combine(state, "probe.json");
            string code = "import json,sys,struct,platform,sysconfig;json.dump(dict(python=sys.executable,major=sys.version_info[0],minor=sys.version_info[1],releaselevel=sys.version_info.releaselevel,free_threaded=bool(sysconfig.get_config_var('Py_GIL_DISABLED')),bits=struct.calcsize('P')*8,machine=platform.machine().lower(),implementation=platform.python_implementation(),platform=sys.platform),open(sys.argv[1],'w',encoding='utf-8'))";
            try
            {
                using (var check = OwnedProcess.Start(executable, prefix + "-I -X utf8 -c " + OwnedProcess.Quote(code) + " " + OwnedProcess.Quote(result), state, PreparationEnvironment()))
                {
                    DateTime deadline = DateTime.UtcNow.AddSeconds(10);
                    while (!check.Wait(100)) { ThrowIfCancelled(); if (DateTime.UtcNow >= deadline) return ProbeFailed(executable, "检测超时（10 秒）"); }
                    ThrowIfCancelled();
                }
                if (!File.Exists(result)) return ProbeFailed(executable, "未返回解释器信息，可能未安装该版本或无法启动");
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(result));
                string python = Convert.ToString(data["python"]);
                string version = Convert.ToString(data["major"]) + "." + Convert.ToString(data["minor"]);
                var entry = pythonPolicy.versions.FirstOrDefault(x => x.version == version);
                if (entry == null || entry.status != "supported")
                    return ProbeFailed(executable, "发现 Python " + version + "，" + (entry == null ? "此版本尚未验证；当前支持 " + pythonPolicy.Supported : entry.reason));
                if (Convert.ToInt32(data["bits"]) != 64 || Convert.ToString(data["implementation"]) != "CPython"
                    || !new [] { "amd64", "x86_64" }.Contains(Convert.ToString(data["machine"])) || Convert.ToString(data["platform"]) != "win32")
                    return ProbeFailed(executable, "发现 Python " + version + "，需要 Windows x64 CPython 标准版本");
                if (Convert.ToBoolean(data["free_threaded"]) || Convert.ToString(data["releaselevel"]) != "final")
                    return ProbeFailed(executable, "发现 Python " + version + "，自由线程或预发布版本尚未验证");
                if (!Path.IsPathRooted(python) || !File.Exists(python)) return ProbeFailed(executable, "解释器返回的路径无效");
                SelectedPythonDescription = "Python " + version + " x64 · " + Path.GetFullPath(python);
                return Path.GetFullPath(python);
            }
            catch (StartupCancelledException) { throw; }
            catch { return ProbeFailed(executable, "无法启动或解释器信息不完整"); }
            finally { try { File.Delete(result); } catch { } }
        }
        private IEnumerable<string> PythonLaunchers()
        {
            var candidates = new List<string> { Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.Windows), "py.exe"), Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python", "Launcher", "py.exe") };
            foreach (string directory in (Environment.GetEnvironmentVariable("PATH") ?? "").Split(';'))
                try { candidates.Add(Path.Combine(directory.Trim('"'), "py.exe")); } catch { }
            return candidates.Where(File.Exists).Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase).Take(8);
        }
        private string PrepareEnvironment(LauncherOptions options)
        {
            EnvironmentFailureLogPath = null;
            SelectedPythonDescription = null;
            pythonProbeFailures.Clear();
            pythonPolicy = PythonRuntimePolicy.Load(options.RootDirectory);
            string state = Path.GetFullPath(Path.Combine(LauncherPreferences.Store.RuntimeStateDirectory, "runtime", "full"));
            string request = Path.Combine(state, "requests", Guid.NewGuid().ToString("N"));
            Directory.CreateDirectory(request);
            string result = Path.Combine(request, "result.json"), progress = Path.Combine(request, "progress.json");
            SetStage("environment_validate", "正在自动检测 Windows x64 CPython " + pythonPolicy.Supported + "；环境准备日志目录：" + Path.Combine(state, "logs"));
            string python = null;
            DateTime discoveryDeadline = DateTime.UtcNow.AddSeconds(60);
            foreach (string candidate in PythonCandidates(options))
            {
                ThrowIfCancelled();
                if (DateTime.UtcNow >= discoveryDeadline) break;
                python = ProbeBasePython(candidate, "", request);
                if (python != null) break;
            }
            if (python == null && String.IsNullOrWhiteSpace(options.PythonExecutable))
                foreach (string launcher in PythonLaunchers())
                {
                    foreach (string selector in pythonPolicy.versions.OrderBy(x => x.status == "supported" ? 0 : 1).Select(x => "-" + x.version + " "))
                    {
                        if (DateTime.UtcNow >= discoveryDeadline) break;
                        ThrowIfCancelled(); python = ProbeBasePython(launcher, selector, request); if (python != null) break;
                    }
                    if (python != null || DateTime.UtcNow >= discoveryDeadline) break;
                }
            if (python == null)
            {
                Directory.CreateDirectory(Path.Combine(state, "logs"));
                if (DateTime.UtcNow >= discoveryDeadline) pythonProbeFailures.Add("自动检测达到时间上限，可手动指定解释器后重试");
                string diagnostic = Path.Combine(state, "logs", "python-discovery-" + Guid.NewGuid().ToString("N") + ".local.log");
                File.WriteAllLines(diagnostic, pythonProbeFailures, Encoding.UTF8);
                EnvironmentFailureLogPath = diagnostic;
                string meaningful = String.Join("；", pythonProbeFailures.Where(x => x.Contains("发现 Python")).Take(4));
                if (meaningful.Length == 0) meaningful = String.Join("；", pythonProbeFailures.Take(2));
                throw new LauncherException("未找到可用的 Windows x64 CPython " + pythonPolicy.Supported + "。" + meaningful
                    + "。可在启动设置点击“自动检测”清除手动路径，或选择已有解释器；未安装依赖。检测记录：" + diagnostic);
            }
            string helper = Path.Combine(options.RootDirectory, "launcher", "environment.py");
            if (!File.Exists(helper)) throw new LauncherException("安装目录缺少 launcher/environment.py，请恢复完整桌面目录。");
            // Proxy URLs may contain passwords: convey only their presence. The helper
            // can reuse an offline ready environment, or explicitly reject installation.
            var preparationEnvironment = PreparationEnvironment();
            if (new [] { "HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY" }.Any(name => !String.IsNullOrWhiteSpace(Environment.GetEnvironmentVariable(name))))
                preparationEnvironment["VASP_INSTALLER_PROXY_CONFIGURED"] = "1";
            SetStage("environment_create", "已选择 " + SelectedPythonDescription + "；正在准备独立运行环境；日志目录：" + Path.Combine(state, "logs"));
            string preparationLog = Path.Combine(state, "logs");
            try
            {
                using (var preparation = OwnedProcess.Start(python, "-I -X utf8 " + OwnedProcess.Quote(helper) + " --root " + OwnedProcess.Quote(options.RootDirectory) + " --state " + OwnedProcess.Quote(state) + " --result-file " + OwnedProcess.Quote(result) + " --progress-file " + OwnedProcess.Quote(progress), request, preparationEnvironment))
                {
                    DateTime deadline = DateTime.UtcNow.AddMinutes(20);
                    string previous = "";
                    while (!preparation.Wait(150))
                    {
                        ThrowIfCancelled();
                        ReadPreparationLog(result, state, ref preparationLog);
                        ReadPreparationLog(progress, state, ref preparationLog);
                        ReadPreparationProgress(progress, ref previous);
                        if (DateTime.UtcNow >= deadline) throw new LauncherException("独立环境准备超过 20 分钟，已停止安装进程；可重试。日志目录：" + Path.Combine(state, "logs"));
                    }
                    ThrowIfCancelled(); ReadPreparationLog(result, state, ref preparationLog); ReadPreparationLog(progress, state, ref preparationLog); ReadPreparationProgress(progress, ref previous);
                }
                if (!File.Exists(result)) throw new LauncherException("独立环境准备未返回结果；可重试。日志目录：" + Path.Combine(state, "logs"));
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(result));
                if (!data.ContainsKey("ok") || !Convert.ToBoolean(data["ok"]))
                {
                    string failure = data.ContainsKey("message") ? Convert.ToString(data["message"]) : "准备失败";
                    string code = data.ContainsKey("code") ? Convert.ToString(data["code"]) : "PREPARE_FAILED";
                    throw new LauncherException("独立环境准备失败（" + code + "）：" + failure + " 可重试。日志：" + preparationLog);
                }
                string prepared = data.ContainsKey("python") ? Convert.ToString(data["python"]) : "";
                string environments = Path.Combine(state, "environments") + Path.DirectorySeparatorChar;
                if (!Path.IsPathRooted(prepared) || !Path.GetFullPath(prepared).StartsWith(environments, StringComparison.OrdinalIgnoreCase) || !File.Exists(prepared))
                    throw new LauncherException("环境准备返回了无效的独立 Python 路径；未启动服务。日志目录：" + Path.Combine(state, "logs"));
                return Path.GetFullPath(prepared);
            }
            catch (StartupCancelledException)
            {
                ReadPreparationLog(result, state, ref preparationLog);
                EnvironmentFailureLogPath = File.Exists(preparationLog) ? preparationLog : null;
                SetMessage("启动已取消，已清理所属环境准备及安装进程。日志：" + preparationLog);
                throw new StartupCancelledException(message);
            }
            catch (LauncherException) { EnvironmentFailureLogPath = File.Exists(preparationLog) ? preparationLog : null; throw; }
            catch { EnvironmentFailureLogPath = File.Exists(preparationLog) ? preparationLog : null; throw new LauncherException("独立环境准备失败；已清理所属安装进程，可重试。日志目录：" + Path.Combine(state, "logs")); }
        }
        private static StreamReader OpenPreparationReader(string file)
        {
            // Helpers update metadata while we poll. Windows readers must allow both
            // writes and atomic replacement; File.ReadAllText's FileShare.Read can
            // otherwise make the helper fail with a sharing violation.
            return new StreamReader(new FileStream(file, FileMode.Open, FileAccess.Read,
                FileShare.ReadWrite | FileShare.Delete), Encoding.UTF8, true);
        }
        private void ReadPreparationLog(string file, string state, ref string previous)
        {
            try
            {
                if (!File.Exists(file)) return;
                string text;
                using (var reader = OpenPreparationReader(file)) text = reader.ReadToEnd();
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(text);
                if (!data.ContainsKey("log_path")) return;
                string log = Convert.ToString(data["log_path"]);
                if (!Path.IsPathRooted(log)) return;
                log = Path.GetFullPath(log);
                if (log != previous && log.StartsWith(Path.Combine(state, "logs") + Path.DirectorySeparatorChar, StringComparison.OrdinalIgnoreCase)
                    && File.Exists(log) && (File.GetAttributes(log) & FileAttributes.ReparsePoint) == 0)
                { previous = log; SetMessage("独立环境准备日志：" + log); }
            }
            catch { /* A partial metadata read must not interrupt preparation. */ }
        }
        private void ReadPreparationProgress(string file, ref string previous)
        {
            try
            {
                if (!File.Exists(file)) return;
                string current;
                using (var reader = OpenPreparationReader(file)) current = reader.ReadToEnd();
                if (current == previous) return;
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(current);
                string stage = Convert.ToString(data["stage"]), detail = Convert.ToString(data["message"]);
                if (!new [] { "validate", "create", "install", "verify", "ready" }.Contains(stage)) return;
                previous = current; SetStage("environment_" + stage, detail);
            }
            catch { /* Atomic helper writes are expected; tolerate transient file access. */ }
        }
        private static void Validate(LauncherOptions options)
        {
            if (options == null || String.IsNullOrWhiteSpace(options.RootDirectory)) throw new LauncherException("请先选择 VASP-Copilot 安装目录。");
            if (options.FullFeatures && options.IsolatedProfile) throw new LauncherException("完整功能与隔离测试模式不能同时启用。");
            options.RootDirectory = Path.GetFullPath(options.RootDirectory);
            foreach (string file in new [] { "backend/app/main.py", "backend/ai_mode/server.py", "frontend/dist/index.html" })
                if (!File.Exists(Path.Combine(options.RootDirectory, file.Replace('/', Path.DirectorySeparatorChar))))
                    throw new LauncherException(file.StartsWith("frontend") ? "缺少 frontend/dist 构建产物。请先按安装指南构建前端，再启动。" : "选择的安装目录缺少 " + file + "。");
            foreach (string directory in new [] { options.VaspAiHome, options.DataDirectory })
                if (!String.IsNullOrWhiteSpace(directory) && !Path.IsPathRooted(directory)) throw new LauncherException("可选数据目录必须使用绝对路径；留空沿用既有配置。");
        }
        private void CheckDependencies(string python, string runtime, LauncherOptions options)
        {
            string resultFile = Path.Combine(LogDirectory, "check-" + Guid.NewGuid().ToString("N") + ".json");
            try
            {
                using (OwnedProcess check = OwnedProcess.Start(python, "-X utf8 " + OwnedProcess.Quote(runtime) + " --kind check --root " + OwnedProcess.Quote(options.RootDirectory) + " --result-file " + OwnedProcess.Quote(resultFile), options.RootDirectory))
                {
                    DateTime deadline = DateTime.UtcNow.AddSeconds(15);
                    while (!check.Wait(100)) { ThrowIfCancelled(); if (DateTime.UtcNow >= deadline) throw new LauncherException("Python 环境检测超时，请检查所选解释器。"); }
                    ThrowIfCancelled();
                }
                if (!File.Exists(resultFile)) throw new LauncherException("无法执行所选 Python，请在启动设置自动检测或重新选择已有解释器。");
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(resultFile));
                if (!data.ContainsKey("ok") || !(bool)data["ok"])
                {
                    string missing = data.ContainsKey("missing") ? String.Join(", ", ((System.Collections.ArrayList)data["missing"]).Cast<object>()) : "";
                    string reason = data.ContainsKey("python_reason") ? Convert.ToString(data["python_reason"]) : "请检查解释器及运行依赖";
                    throw new LauncherException("Python 环境未就绪：" + reason + (missing.Length > 0 ? "，缺少或无法导入：" + missing : "") + "。请按 Windows源码安装与基础使用.md 检查运行环境。");
                }
            }
            catch (LauncherException) { throw; }
            catch { throw new LauncherException("Python 环境检测失败，请检查 Python 路径和安装指南。"); }
            finally { try { File.Delete(resultFile); } catch {} }
        }
        private void StartInternal(LauncherOptions options)
        {
            if (IsRunning) { SetMessage("本启动器已有服务运行；修改参数请停止或重启。"); return; }
            ThrowIfCancelled();
            SetStage("environment", "正在核对安装目录与已有 Python 环境…");
            Validate(options);
            string runtime = runtimeScript ?? Path.Combine(options.RootDirectory, "launcher", "runtime.py");
            if (!File.Exists(runtime)) throw new LauncherException("桌面程序缺少 launcher/runtime.py，请恢复完整桌面目录。");
            string python = null, lastFailure = "未找到已有可用 Python 环境，请在启动设置自动检测或选择解释器。";
            int count = 0;
            if (options.FullFeatures && options.AutoPrepareEnvironment)
            {
                python = PrepareEnvironment(options);
                SetStage("environment_verify", "独立环境已准备，正在核验业务依赖…");
                CheckDependencies(python, runtime, options);
            }
            else
            {
                foreach (string candidate in PythonCandidates(options))
                {
                    ThrowIfCancelled();
                    count++;
                    SetMessage("正在检查已有 Python 环境（候选 " + count + "）…");
                    try { CheckDependencies(candidate, runtime, options); python = candidate; break; }
                    catch (StartupCancelledException) { throw; }
                    catch (LauncherException e) { lastFailure = e.Message; }
                }
            }
            if (python == null) throw new LauncherException("已检查 " + count + " 个 Python 候选，均未就绪。" + lastFailure + " 请通过“启动设置”选择已有环境。");
            SelectedPython = python;
            SetStage("services", "已有 Python 环境已确认，正在准备本地服务…");
            for (int attempt = 1; attempt <= 3; attempt++)
            {
                StartupAttempts = attempt;
                ThrowIfCancelled();
                ChoosePorts(options);
                try { StartAttempt(options, python, runtime); SetStage("ready", "本地服务健康与进程归属已确认。"); return; }
                catch (PortConflictException)
                {
                    if (attempt == 3) throw new LauncherException("本地端口连续 3 次被占用，已停止本次服务。请稍后重试。");
                    SetMessage("检测到明确端口占用，正在重新选择本地端口（" + (attempt + 1) + "/3）…");
                }
            }
        }
        private static void ChoosePorts(LauncherOptions options)
        {
            var reservations = new List<TcpListener>();
            try
            {
                for (int i = 0; i < (options.EnableAi ? 3 : 2); i++)
                {
                    var listener = new TcpListener(IPAddress.Loopback, 0);
                    listener.Server.ExclusiveAddressUse = true; listener.Start(); reservations.Add(listener);
                }
                options.ToolboxPort = ((IPEndPoint)reservations[0].LocalEndpoint).Port;
                options.WebPort = ((IPEndPoint)reservations[1].LocalEndpoint).Port;
                if (options.EnableAi) options.AiPort = ((IPEndPoint)reservations[2].LocalEndpoint).Port;
            }
            finally { foreach (var listener in reservations) listener.Stop(); }
        }
        private sealed class PortConflictException : LauncherException { public PortConflictException(string message) : base(message) {} }
        private void StartAttempt(LauncherOptions options, string python, string runtime)
        {
            lock (sync)
            {
                root = options.RootDirectory; fingerprint = Fingerprint(root); version = "0.5.0 / 安装指纹 " + fingerprint;
                webUrl = "http://127.0.0.1:" + options.WebPort;
                foreach (Service s in services)
                {
                    if (s.Process != null) { s.Process.Dispose(); s.Process = null; }
                    s.Port = s.Key == "toolbox" ? options.ToolboxPort : s.Key == "ai" ? options.AiPort : options.WebPort;
                    s.State = s.Key == "ai" && !options.EnableAi ? "disabled" : "stopped";
                    s.HealthFailures = 0; s.FirstHealthFailure = DateTime.MinValue;
                    s.Detail = s.State == "disabled" ? "未选择启动" : "未启动";
                }
            }
            // Preflight ALL enabled ports: never create a partial stack for known conflicts.
            foreach (Service s in services.Where(s => s.State != "disabled"))
            {
                int pid = Native.ListenerPid(s.Port);
                if (pid != 0)
                {
                    SetState(s, "conflict", "端口被非本启动器进程占用（PID " + pid + "）；将自动重新选择端口。");
                    SetMessage(s.Label + "端口 " + s.Port + " 冲突；未启动或接管占用者。");
                    throw new PortConflictException(message);
                }
            }
            LauncherPreferences.Save(options);
            // A new secret belongs only to this start attempt and its two service children.
            string reviewerSecret = null;
            if (options.FullFeatures && options.EnableAi)
            {
                byte[] bytes = new byte[32];
                using (var random = RandomNumberGenerator.Create()) random.GetBytes(bytes);
                reviewerSecret = Convert.ToBase64String(bytes);
                Array.Clear(bytes, 0, bytes.Length);
            }
            try
            {
                foreach (Service s in services.Where(s => s.State != "disabled"))
                {
                    ThrowIfCancelled();
                    s.Token = Guid.NewGuid().ToString("N");
                    s.StopFile = Path.Combine(LogDirectory, s.Key + "-" + s.Token + ".stop");
                    s.ErrorFile = Path.Combine(LogDirectory, s.Key + "-" + s.Token + ".error.json");
                    string arguments = "-X utf8 " + OwnedProcess.Quote(runtime) + " --kind " + s.Key + " --root " + OwnedProcess.Quote(root)
                        + " --port " + s.Port + " --toolbox-port " + options.ToolboxPort + " --ai-port " + options.AiPort
                        + " --token " + s.Token + " --stop-file " + OwnedProcess.Quote(s.StopFile) + " --result-file " + OwnedProcess.Quote(s.ErrorFile)
                        + (options.EnableAi ? " --enable-ai" : "")
                        + (options.FullFeatures ? " --full-features" : "")
                        + (String.IsNullOrWhiteSpace(options.VaspAiHome) ? "" : " --ai-home " + OwnedProcess.Quote(options.VaspAiHome))
                        + (String.IsNullOrWhiteSpace(options.DataDirectory) ? "" : " --data-dir " + OwnedProcess.Quote(options.DataDirectory));
                    if (options.IsolatedProfile) arguments += " --isolated";
                    SetState(s, "starting", "正在启动并核对 HTTP 健康、安装指纹和进程归属…");
                    var environment = new Dictionary<string, string> {
                        { "VASP_REVIEWER_SHARED_SECRET", s.Key != "web" ? reviewerSecret : null },
                        { "VASP_REVIEWER_ENABLED", "false" }, { "VASP_REVIEWER_URL", null }
                    };
                    lock (sync) s.Process = OwnedProcess.Start(python, arguments, Path.Combine(root, "backend"), environment);
                    DateTime deadline = DateTime.UtcNow.AddSeconds(45);
                    while (DateTime.UtcNow < deadline)
                    {
                        ThrowIfCancelled();
                        if (s.Process.HasExited) break;
                        if (IsHealthy(s))
                        {
                            int runtimePid = Native.ListenerPid(s.Port);
                            s.Process.TrackRuntime(runtimePid);
                            lock (sync) s.RuntimePid = runtimePid;
                            SetState(s, "ready", "HTTP 健康及进程归属已确认"); break;
                        }
                        Thread.Sleep(180);
                    }
                    if (s.State != "ready")
                    {
                        string category = "启动失败或健康检查超时";
                        bool portConflict = false;
                        if (File.Exists(s.ErrorFile))
                        {
                            try
                            {
                                var error = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(s.ErrorFile));
                                if (error.ContainsKey("error_type")) category += "（" + Convert.ToString(error["error_type"]) + "）";
                                portConflict = error.ContainsKey("code") && Convert.ToString(error["code"]) == "PORT_IN_USE";
                            } catch { }
                        }
                        SetState(s, "error", category + "；只记录失败类别，不保存配置或响应正文。");
                        if (portConflict) throw new PortConflictException(s.Label + " 端口绑定被其他进程抢占。");
                        throw new LauncherException(s.Label + " " + category + "。请检查依赖、目录权限及已有数据实例。");
                    }
                }
                SetMessage("本地服务已就绪。就绪仅表示服务可达，不代表模型调用或超算连接成功。");
            }
            catch (Exception exc)
            {
                StopInternal(true);
                var known = exc as LauncherException;
                SetMessage(known == null ? "本地服务启动失败；已清理本次所属进程。" : known.Message);
                if (known != null) throw;
                throw new LauncherException(message);
            }
        }
        private enum HealthResult { Healthy, Unavailable, IdentityFailure, Exited }
        private bool IsHealthy(Service s) { return CheckHealth(s) == HealthResult.Healthy; }
        private HealthResult CheckHealth(Service s)
        {
            OwnedProcess owned = s.Process;
            if (owned == null || owned.HasExited || owned.RuntimeExited) return HealthResult.Exited;
            bool verifyingIdentity = true;
            try
            {
                int listenerPid = Native.ListenerPid(s.Port);
                if (listenerPid == 0) return HealthResult.Unavailable;
                if (!owned.ContainsPid(listenerPid)) return HealthResult.IdentityFailure;
                var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + s.Port + "/__launcher__/health");
                request.Proxy = null; request.Timeout = 650; request.ReadWriteTimeout = 650; request.AllowAutoRedirect = false;
                request.Headers["X-Launcher-Token"] = s.Token;
                using (var response = request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream()))
                {
                    var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                    if (Convert.ToString(data["token"]) != s.Token || Convert.ToString(data["kind"]) != s.Key
                        || Convert.ToInt32(data["pid"]) != listenerPid || Convert.ToString(data["fingerprint"]) != fingerprint
                        || !String.Equals(Path.GetFullPath(Convert.ToString(data["root"])).TrimEnd('\\', '/'), root.TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase)) return HealthResult.IdentityFailure;
                }
                verifyingIdentity = false;
                if (s.Key != "web")
                {
                    var health = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + s.Port + (s.Key == "toolbox" ? "/health" : "/ai/v1/ping"));
                    health.Proxy = null; health.Timeout = 650; health.ReadWriteTimeout = 650; health.AllowAutoRedirect = false;
                    using (var response = health.GetResponse())
                    using (var reader = new StreamReader(response.GetResponseStream()))
                    {
                        var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                        bool ready = s.Key == "toolbox" ? Convert.ToString(data["status"]) == "ok"
                            : Convert.ToString(data["mode"]) == "ai" && (Convert.ToString(data["version"]) == "0.3.0" || Convert.ToString(data["version"]) == "0.4.0" || Convert.ToString(data["version"]) == "0.4.1" || Convert.ToString(data["version"]) == "0.5.0") && Convert.ToBoolean(data["enabled"]);
                        if (!ready) return HealthResult.Unavailable;
                    }
                }
                // Preserve ownership even if a listener changed during the HTTP requests.
                if (owned.HasExited || owned.RuntimeExited) return HealthResult.Exited;
                return Native.ListenerPid(s.Port) == listenerPid && owned.ContainsPid(listenerPid) ? HealthResult.Healthy : HealthResult.IdentityFailure;
            }
            catch (WebException exc)
            {
                using (var response = exc.Response as HttpWebResponse)
                {
                    if (verifyingIdentity && response != null && (response.StatusCode == HttpStatusCode.Unauthorized || response.StatusCode == HttpStatusCode.Forbidden))
                        return HealthResult.IdentityFailure;
                }
                return HealthResult.Unavailable;
            }
            catch { return HealthResult.IdentityFailure; }
        }
        private void StopInternal(bool preserveErrors = false)
        {
            SetMessage("正在停止本启动器所属服务；已提交超算作业不会因此取消。");
            // Request all services to stop first; uvicorn runs its existing lifespan cleanup.
            List<Service> owned;
            lock (sync) owned = services.Where(s => s.Process != null).ToList();
            foreach (Service s in owned) try { File.WriteAllText(s.StopFile, "stop"); } catch { }
            DateTime deadline = DateTime.UtcNow.AddSeconds(8);
            bool cleanupFailed = false;
            foreach (Service s in owned)
            {
                try
                {
                    int remaining = Math.Max(0, (int)(deadline - DateTime.UtcNow).TotalMilliseconds);
                    bool graceful = s.Process.Wait(remaining);
                    s.Process.Dispose();
                    lock (sync) { s.Process = null; s.RuntimePid = 0; }
                    int listener = Native.ListenerPid(s.Port);
                    bool error = preserveErrors && s.State == "error";
                    if (!error) SetState(s, "stopped", listener != 0 ? "所属进程已停止；端口现由其他进程占用，未清理它。" : graceful ? "已正常停止，端口已释放" : "已在超时后清理所属进程树，端口已释放");
                    try { File.Delete(s.StopFile); File.Delete(s.ErrorFile); } catch { }
                }
                catch
                {
                    cleanupFailed = true;
                    SetState(s, "error", "已请求停止，但无法核验所属进程完整退出。其他所属服务继续清理；可再次停止。");
                }
            }
            if (cleanupFailed) { SetMessage("停止存在未核验项，请查看服务状态并再次停止。"); throw new LauncherException(message); }
            if (!preserveErrors) SetMessage("所属本地服务已停止。超算作业未取消；本地监控与辅助已停止。");
        }
        public LauncherSnapshot GetSnapshot()
        {
            lock (sync)
            {
                // Pure cached snapshot: network/OS process cleanup always runs off the UI.
                return new LauncherSnapshot { Busy = busy != 0, Message = message, StartupStage = startupStage, Version = version, WebUrl = webUrl,
                    Services = services.Select(s => new ServiceSnapshot { Key = s.Key, Label = s.Label, State = s.State, Detail = s.Detail, Port = s.Port, Pid = s.RuntimePid }).ToList() };
            }
        }
        private static string Fingerprint(string directory)
        {
            using (var hash = SHA256.Create())
                return String.Join(".", new [] { "backend/app/main.py", "backend/ai_mode/server.py", "frontend/dist/index.html" }
                    .Select(file => BitConverter.ToString(hash.ComputeHash(File.ReadAllBytes(Path.Combine(directory, file.Replace('/', Path.DirectorySeparatorChar))))).Replace("-", "").ToLowerInvariant().Substring(0, 12)));
        }
        private void SetState(Service s, string state, string detail) { lock (sync) { s.State = state; s.Detail = detail; } }
        private void SetStage(string stage, string detail) { lock (sync) startupStage = stage; SetMessage(detail); }
        private void SetMessage(string value)
        {
            lock (sync) message = value;
            try { File.AppendAllText(Path.Combine(LogDirectory, "launcher.log"), DateTime.Now.ToString("s") + " " + value + Environment.NewLine, Encoding.UTF8); } catch { }
        }
        public void Dispose()
        {
            if (Interlocked.Exchange(ref closing, 1) != 0) return;
            CancelStartup();
            // Pending operations complete before shutdown; no new operation can begin.
            while (Interlocked.CompareExchange(ref busy, 1, 0) != 0) Thread.Sleep(50);
            while (polling != 0) Thread.Sleep(10);
            try { StopInternal(); disposed = true; healthTimer.Dispose(); }
            finally { Interlocked.Exchange(ref busy, 0); if (!disposed) Interlocked.Exchange(ref closing, 0); }
        }
    }
}
