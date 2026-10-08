using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Linq;
using System.Net;
using System.Net.Sockets;
using System.Security.Cryptography;
using System.Text;
using System.Threading;
using System.Web.Script.Serialization;

namespace VaspCopilot.Launcher
{
    public class LauncherException : Exception { public LauncherException(string message) : base(message) {} }
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
    public static class LauncherPreferences
    {
        // D1 candidate only: allow the desktop harness to isolate its preferences/logs.
        internal static readonly string DirectoryPath = Environment.GetEnvironmentVariable("VASP_LAUNCHER_STATE_DIR")
            ?? Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VASP-Copilot", "launcher");
        public static LauncherOptions Load()
        {
            try { var options = new JavaScriptSerializer().Deserialize<LauncherOptions>(File.ReadAllText(Path.Combine(DirectoryPath, "preferences.json"), Encoding.UTF8)) ?? new LauncherOptions(); options.ToolboxPort = 8000; options.WebPort = 5173; options.AiPort = 8500; return options; }
            catch { return new LauncherOptions(); }
        }
        public static void Save(LauncherOptions options)
        {
            Directory.CreateDirectory(DirectoryPath);
            string target = Path.Combine(DirectoryPath, "preferences.json");
            string temp = target + ".tmp";
            File.WriteAllText(temp, new JavaScriptSerializer().Serialize(new { options.RootDirectory, options.PythonExecutable, options.VaspAiHome, options.DataDirectory, options.EnableAi }), Encoding.UTF8);
            if (File.Exists(target)) File.Replace(temp, target, null); else File.Move(temp, target);
        }
    }

    public sealed class LauncherController : IDisposable
    {
        private sealed class Service
        {
            public string Key, Label, State = "stopped", Detail = "未启动", StopFile, ErrorFile, Token;
            public int Port, RuntimePid; public OwnedProcess Process;
        }
        private readonly object sync = new object();
        private readonly List<Service> services = new List<Service>();
        private int busy;
        private string message = "选择已有安装目录和 Python 环境；智能模式默认关闭。", version = "", webUrl = "", root = "", fingerprint = "";
        private string startupStage = "";
        private bool disposed;
        private readonly Timer healthTimer;
        private int polling;
        private int closing;
        private int cancellationRequested;
        public void CancelStartup() { Interlocked.Exchange(ref cancellationRequested, 1); }
        private sealed class StartupCancelledException : LauncherException { public StartupCancelledException() : base("启动已取消，正在退出并清理所属服务。") {} }
        private void ThrowIfCancelled() { if (cancellationRequested != 0) throw new StartupCancelledException(); }
        public string LogDirectory { get; private set; }
        public bool IsRunning { get { lock (sync) return services.Any(s => s.Process != null); } }
        private readonly string runtimeScript;
        public string SelectedPython { get; private set; }
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
                    bool healthy = IsHealthy(service);
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
                        lock (sync)
                        {
                            service.State = healthy ? "ready" : "error";
                            service.Detail = healthy ? "HTTP 健康及进程归属已确认" : "进程仍在，但 HTTP 健康或归属检查未通过；可停止或重启。";
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
            finally { Interlocked.Exchange(ref busy, 0); }
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
            finally { Interlocked.Exchange(ref busy, 0); }
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
                if (!File.Exists(selected)) throw new LauncherException("找不到选择的 Python 可执行文件，请选择 python.exe。");
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
            foreach (string candidate in candidates.Where(File.Exists).Select(Path.GetFullPath).Distinct(StringComparer.OrdinalIgnoreCase).Take(12)) yield return candidate;
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
                if (!File.Exists(resultFile)) throw new LauncherException("无法执行所选 Python，需已有 Python 3.10+ 环境。");
                var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(File.ReadAllText(resultFile));
                if (!data.ContainsKey("ok") || !(bool)data["ok"])
                {
                    string missing = data.ContainsKey("missing") ? String.Join(", ", ((System.Collections.ArrayList)data["missing"]).Cast<object>()) : "";
                    throw new LauncherException("Python 环境未就绪" + (missing.Length > 0 ? "，缺少：" + missing : "，需 Python 3.10+ 或有效依赖") + "。请按 Windows源码安装与基础使用.md 安装已有项目依赖。");
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
            string python = null, lastFailure = "未找到已有 Python 3.10+ 环境。";
            int count = 0;
            foreach (string candidate in PythonCandidates(options))
            {
                ThrowIfCancelled();
                count++;
                SetMessage("正在检查已有 Python 环境（候选 " + count + "）…");
                try { CheckDependencies(candidate, runtime, options); python = candidate; break; }
                catch (StartupCancelledException) { throw; }
                catch (LauncherException e) { lastFailure = e.Message; }
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
                root = options.RootDirectory; fingerprint = Fingerprint(root); version = "0.4.0 / 安装指纹 " + fingerprint;
                webUrl = "http://127.0.0.1:" + options.WebPort;
                foreach (Service s in services)
                {
                    if (s.Process != null) { s.Process.Dispose(); s.Process = null; }
                    s.Port = s.Key == "toolbox" ? options.ToolboxPort : s.Key == "ai" ? options.AiPort : options.WebPort;
                    s.State = s.Key == "ai" && !options.EnableAi ? "disabled" : "stopped";
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
        private bool IsHealthy(Service s)
        {
            OwnedProcess owned = s.Process;
            if (owned == null || owned.HasExited) return false;
            try
            {
                int listenerPid = Native.ListenerPid(s.Port);
                if (!owned.ContainsPid(listenerPid)) return false;
                var request = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + s.Port + "/__launcher__/health");
                request.Proxy = null; request.Timeout = 650; request.ReadWriteTimeout = 650;
                request.Headers["X-Launcher-Token"] = s.Token;
                using (var response = request.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream()))
                {
                    var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                    if (Convert.ToString(data["token"]) != s.Token || Convert.ToString(data["kind"]) != s.Key
                        || Convert.ToInt32(data["pid"]) != listenerPid || Convert.ToString(data["fingerprint"]) != fingerprint
                        || !String.Equals(Path.GetFullPath(Convert.ToString(data["root"])).TrimEnd('\\', '/'), root.TrimEnd('\\', '/'), StringComparison.OrdinalIgnoreCase)) return false;
                }
                if (s.Key == "web") return true;
                var health = (HttpWebRequest)WebRequest.Create("http://127.0.0.1:" + s.Port + (s.Key == "toolbox" ? "/health" : "/ai/v1/ping"));
                health.Proxy = null; health.Timeout = 650; health.ReadWriteTimeout = 650;
                using (var response = health.GetResponse())
                using (var reader = new StreamReader(response.GetResponseStream()))
                {
                    var data = new JavaScriptSerializer().Deserialize<Dictionary<string, object>>(reader.ReadToEnd());
                    return s.Key == "toolbox" ? Convert.ToString(data["status"]) == "ok"
                        : Convert.ToString(data["mode"]) == "ai" && (Convert.ToString(data["version"]) == "0.3.0" || Convert.ToString(data["version"]) == "0.4.0") && Convert.ToBoolean(data["enabled"]);
                }
            }
            catch { return false; }
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
            // Pending operations complete before shutdown; no new operation can begin.
            while (Interlocked.CompareExchange(ref busy, 1, 0) != 0) Thread.Sleep(50);
            while (polling != 0) Thread.Sleep(10);
            try { StopInternal(); disposed = true; healthTimer.Dispose(); }
            finally { Interlocked.Exchange(ref busy, 0); if (!disposed) Interlocked.Exchange(ref closing, 0); }
        }
    }
}
