using System;
using System.Drawing;
using System.IO;
using System.Runtime.InteropServices;
using System.Runtime.Versioning;
using System.Security.Cryptography;
using System.Security.Principal;
using System.Text;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;
using VaspCopilot.Launcher;

[assembly: TargetFramework(".NETFramework,Version=v4.8", FrameworkDisplayName = ".NET Framework 4.8")]
[assembly: System.Reflection.AssemblyVersion("0.4.0.0")]
[assembly: System.Reflection.AssemblyFileVersion("0.4.0.0")]
[assembly: System.Reflection.AssemblyInformationalVersion("0.4.0")]
namespace VaspCopilot.DesktopV3
{
    internal static class Program
    {
        [DllImport("user32.dll")] internal static extern bool SetForegroundWindow(IntPtr window);
        [DllImport("user32.dll")] private static extern bool AllowSetForegroundWindow(int pid);
        private static string Arg(string[] args, string key)
        {
            int i = Array.IndexOf(args, key); return i < 0 || i + 1 >= args.Length ? "" : args[i + 1];
        }
        [STAThread] private static void Main(string[] args)
        {
            Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
            bool fullFeatures = Array.IndexOf(args, "--full-features") >= 0;
            bool testProfileSpecified = Array.IndexOf(args, "--test-profile") >= 0;
            if (fullFeatures && testProfileSpecified)
            {
                MessageBox.Show("--full-features 与 --test-profile 不能同时使用。请删除其中一个启动参数。", "启动参数冲突", MessageBoxButtons.OK, MessageBoxIcon.Warning);
                return;
            }
            string profile = Arg(args, "--test-profile");
            if (profile.Length > 0 && !Path.IsPathRooted(profile)) { MessageBox.Show("测试 profile 必须是绝对路径。"); return; }
            bool isolated = profile.Length > 0;
            string state = isolated ? Path.GetFullPath(profile) : Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "VASP-Copilot", fullFeatures ? "desktop-v040-full" : "desktop-v3");
            Directory.CreateDirectory(state);
            Environment.SetEnvironmentVariable("VASP_LAUNCHER_STATE_DIR", state);
            string instance;
            string instanceScope = isolated ? state.ToUpperInvariant() : fullFeatures ? "full-features" : "normal";
            using (var hash = SHA256.Create()) instance = BitConverter.ToString(hash.ComputeHash(Encoding.UTF8.GetBytes(WindowsIdentity.GetCurrent().User.Value + "|" + instanceScope))).Replace("-", "");
            // Event exists before mutex ownership is decided, so an early second launch is retained.
            using (var activation = new EventWaitHandle(false, EventResetMode.AutoReset, "Local\\VaspCopilotV3-Activate-" + instance))
            {
                bool first;
                using (var mutex = new Mutex(true, "Local\\VaspCopilotV3-" + instance, out first))
                {
                    if (!first) { AllowSetForegroundWindow(-1); activation.Set(); return; }
                    try
                    {
                        string packageRoot = Path.GetFullPath(Path.Combine(AppDomain.CurrentDomain.BaseDirectory, "..", ".."));
                        var options = LauncherPreferences.Load();
                        options.IsolatedProfile = isolated;
                        options.FullFeatures = fullFeatures;
                        options.AutoPrepareEnvironment = fullFeatures;
                        if (isolated)
                        {
                            options.VaspAiHome = Path.Combine(state, "home"); options.DataDirectory = Path.Combine(state, "data");
                            if (Arg(args, "--test-root").Length > 0) options.RootDirectory = Path.GetFullPath(Arg(args, "--test-root"));
                            if (Arg(args, "--test-python").Length > 0) options.PythonExecutable = Path.GetFullPath(Arg(args, "--test-python"));
                            options.EnableAi = false;
                            Environment.SetEnvironmentVariable("PYTHONPYCACHEPREFIX", Path.Combine(state, "pycache"));
                        }
                        else if (fullFeatures)
                        {
                            options.EnableAi = true;
                            if (String.IsNullOrWhiteSpace(options.VaspAiHome)) options.VaspAiHome = Path.Combine(state, "home");
                            if (String.IsNullOrWhiteSpace(options.DataDirectory)) options.DataDirectory = Path.Combine(state, "data");
                        }
                        using (var controller = new LauncherController(Path.Combine(packageRoot, "launcher", "runtime.py")))
                        using (var form = new DesktopForm(controller, options, state, isolated, activation)) Application.Run(form);
                    }
                    finally { mutex.ReleaseMutex(); }
                }
            }
        }
    }
    internal static class ShellTheme
    {
        internal static readonly Color Background = Color.FromArgb(20, 24, 30), Surface = Color.FromArgb(27, 32, 40), Border = Color.FromArgb(51, 60, 73), Text = Color.FromArgb(225, 231, 239), Muted = Color.FromArgb(161, 173, 189), Blue = Color.FromArgb(126, 177, 246);
        internal const string Institution = "中国科学技术大学 · 宋礼课题组", Group = "USTC · Song Group", Authors = "Developed by 夏梓童、黄思远、王奕轩";
        internal static void Apply(Form form)
        { form.BackColor = Background; form.ForeColor = Text; form.Font = new Font("Microsoft YaHei UI", 10F); form.Icon = Icon.ExtractAssociatedIcon(Application.ExecutablePath); }
        internal static Button Button(string text, bool primary = false)
        {
            var button = new Button { Text = text, AutoSize = true, AutoSizeMode = AutoSizeMode.GrowAndShrink, MinimumSize = new Size(80, 34), Padding = new Padding(12, 4, 12, 4), Margin = new Padding(4), FlatStyle = FlatStyle.Flat, BackColor = primary ? Color.FromArgb(48, 82, 126) : Surface, ForeColor = Text, UseVisualStyleBackColor = false };
            button.FlatAppearance.BorderColor = primary ? Blue : Border; button.FlatAppearance.MouseOverBackColor = Color.FromArgb(44, 54, 69); return button;
        }
        internal static Label Label(string text, float size = 10F, bool muted = false)
        { return new Label { Text = text, AutoSize = true, ForeColor = muted ? Muted : Text, Font = new Font("Microsoft YaHei UI", size), Margin = new Padding(0, 6, 0, 6) }; }
        internal static PictureBox Logo(int size)
        { return new PictureBox { Width = size, Height = size, SizeMode = PictureBoxSizeMode.Zoom, Image = BrandImage(), Margin = new Padding(0, 0, 0, 8) }; }
        private static Image BrandImage()
        { using (var stream = typeof(ShellTheme).Assembly.GetManifestResourceStream("VaspCopilot.Brand")) using (var image = Image.FromStream(stream)) return new Bitmap(image); }
        internal static void Input(TextBox box)
        { box.BackColor = Surface; box.ForeColor = Text; box.BorderStyle = BorderStyle.FixedSingle; box.Dock = DockStyle.Fill; box.Margin = new Padding(4, 8, 4, 8); }
    }
    internal sealed class SettingsForm : Form
    {
        private readonly TextBox root = new TextBox(), python = new TextBox();
        private readonly CheckBox ai = new CheckBox();
        internal SettingsForm(LauncherOptions options, string selectedPython)
        {
            SuspendLayout(); AutoScaleMode = AutoScaleMode.Dpi; ShellTheme.Apply(this);
            Text = "启动设置 · VASP-Copilot"; ClientSize = new Size(760, 340); AutoSize = true; AutoSizeMode = AutoSizeMode.GrowAndShrink; MinimumSize = new Size(780, 380); FormBorderStyle = FormBorderStyle.FixedDialog;
            StartPosition = FormStartPosition.CenterParent; MaximizeBox = false; MinimizeBox = false;
            var layout = new TableLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(24), ColumnCount = 3, RowCount = 7 };
            layout.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize)); layout.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 100)); layout.ColumnStyles.Add(new ColumnStyle(SizeType.AutoSize));
            for (int row = 0; row < layout.RowCount; row++) layout.RowStyles.Add(new RowStyle(SizeType.AutoSize));
            var title = ShellTheme.Label("安装目录与运行环境", 16F); layout.Controls.Add(title, 0, 0); layout.SetColumnSpan(title, 3);
            root.Text = options.RootDirectory; python.Text = options.PythonExecutable; ShellTheme.Input(root); ShellTheme.Input(python);
            var folder = ShellTheme.Button("选择目录");
            folder.Click += delegate { using (var dialog = new FolderBrowserDialog { Description = "选择已有 VASP-Copilot 安装目录（含 backend 与 frontend/dist）", SelectedPath = root.Text }) if (dialog.ShowDialog(this) == DialogResult.OK) root.Text = dialog.SelectedPath; };
            var executable = ShellTheme.Button("选择文件");
            executable.Click += delegate { using (var dialog = new OpenFileDialog { Title = "手动选择 Windows x64 CPython 解释器", Filter = "Python|python*.exe|可执行文件|*.exe", CheckFileExists = true }) if (dialog.ShowDialog(this) == DialogResult.OK) python.Text = dialog.FileName; };
            var automatic = ShellTheme.Button("自动检测");
            automatic.Click += delegate { python.Clear(); };
            var pythonButtons = new FlowLayoutPanel { AutoSize = true, WrapContents = false };
            pythonButtons.Controls.Add(automatic); pythonButtons.Controls.Add(executable);
            layout.Controls.Add(ShellTheme.Label("安装目录"), 0, 1); layout.Controls.Add(root, 1, 1); layout.Controls.Add(folder, 2, 1);
            layout.Controls.Add(ShellTheme.Label("Python（留空自动）"), 0, 2); layout.Controls.Add(python, 1, 2); layout.Controls.Add(pythonButtons, 2, 2);
            ai.Text = "启用智能模式服务"; ai.AutoSize = true; ai.Checked = options.EnableAi && !options.IsolatedProfile; ai.Enabled = !options.IsolatedProfile; ai.Margin = new Padding(4, 12, 4, 8); layout.Controls.Add(ai, 1, 3);
            string pythonHint = options.FullFeatures && options.AutoPrepareEnvironment
                ? "默认自动检测已有 Python，无需预装项目依赖。首次启动会创建应用专用环境并安装运行依赖；首次需要联网，本地端口自动分配。"
                : "Python 留空时自动检查已有环境；本地端口自动分配。";
            try { pythonHint += " 支持 Windows x64 CPython " + PythonRuntimePolicy.Load(options.RootDirectory).Supported + "。"; } catch (LauncherException) { }
            if (!String.IsNullOrWhiteSpace(selectedPython)) pythonHint += "\n上次启动采用：" + selectedPython;
            var hint = ShellTheme.Label(pythonHint, 10F, true); hint.MaximumSize = new Size(650, 0); layout.Controls.Add(hint, 1, 4); layout.SetColumnSpan(hint, 2);
            if (options.IsolatedProfile) { var isolation = ShellTheme.Label("隔离候选：智能模式关闭，数据保存在独立测试目录。", 10F, true); layout.Controls.Add(isolation, 1, 5); layout.SetColumnSpan(isolation, 2); }
            var buttons = new FlowLayoutPanel { AutoSize = true, FlowDirection = FlowDirection.RightToLeft, Dock = DockStyle.Fill, Margin = new Padding(0, 16, 0, 0) };
            var save = ShellTheme.Button("保存并启动", true); save.DialogResult = DialogResult.OK; var cancel = ShellTheme.Button("取消"); cancel.DialogResult = DialogResult.Cancel;
            save.Click += delegate { options.RootDirectory = root.Text.Trim(); options.PythonExecutable = python.Text.Trim(); options.EnableAi = !options.IsolatedProfile && ai.Checked; };
            buttons.Controls.Add(save); buttons.Controls.Add(cancel); layout.Controls.Add(buttons, 0, 6); layout.SetColumnSpan(buttons, 3);
            Controls.Add(layout); AcceptButton = save; CancelButton = cancel;
            AutoScaleDimensions = new SizeF(96, 96); ResumeLayout(true);
        }
    }
    internal sealed class AboutForm : Form
    {
        internal AboutForm(bool isolated, bool fullFeatures)
        {
            SuspendLayout(); AutoScaleMode = AutoScaleMode.Dpi; ShellTheme.Apply(this); Text = "关于 VASP-Copilot";
            ClientSize = new Size(590, 390); AutoSize = true; AutoSizeMode = AutoSizeMode.GrowAndShrink; MinimumSize = new Size(590, 390); FormBorderStyle = FormBorderStyle.FixedDialog; StartPosition = FormStartPosition.CenterParent; MaximizeBox = MinimizeBox = false;
            var layout = new FlowLayoutPanel { Dock = DockStyle.Top, AutoSize = true, Padding = new Padding(32), FlowDirection = FlowDirection.TopDown, WrapContents = false };
            layout.Controls.Add(ShellTheme.Logo(64)); layout.Controls.Add(ShellTheme.Label("VASP-Copilot", 23F)); layout.Controls.Add(ShellTheme.Label("科研计算工作空间 · v0.4.0" + (isolated ? " · 隔离候选" : fullFeatures ? " · 完整功能候选" : ""), 10F, true));
            layout.Controls.Add(ShellTheme.Label(ShellTheme.Institution, 12F)); layout.Controls.Add(ShellTheme.Label(ShellTheme.Group, 10F, true)); layout.Controls.Add(ShellTheme.Label(ShellTheme.Authors, 10F, true));
            var close = ShellTheme.Button("关闭"); close.DialogResult = DialogResult.OK; layout.Controls.Add(close); Controls.Add(layout); AcceptButton = CancelButton = close;
            AutoScaleDimensions = new SizeF(96, 96); ResumeLayout(true);
        }
    }
    internal sealed class DesktopForm : Form
    {
        private readonly LauncherController controller;
        private readonly LauncherOptions options;
        private readonly string state;
        private readonly bool isolated;
        private readonly WebView2 web = new WebView2();
        private readonly Label status = new Label(), launchTitle = new Label(), launchMessage = new Label();
        private readonly Label[] steps = new Label[4];
        private readonly Button retry = ShellTheme.Button("重试启动", true), advanced = ShellTheme.Button("启动设置"), evidence = ShellTheme.Button("记录当前页面"), workflow = ShellTheme.Button("工作流");
        private readonly Button launchRetry = ShellTheme.Button("重试启动", true), launchSettings = ShellTheme.Button("启动设置"), launchLog = ShellTheme.Button("查看安装日志");
        private readonly Panel launch = new Panel();
        private readonly System.Windows.Forms.Timer timer = new System.Windows.Forms.Timer();
        private bool closing, finished, initialized, browserReady, servicesReady, pageReady, startupFailed, serviceInterrupted, serviceDegraded;
        private Task startup;
        private string origin = "", lastStage = "";
        private ulong navigationId;
        private int evidenceNumber;
        internal DesktopForm(LauncherController controller, LauncherOptions options, string state, bool isolated, EventWaitHandle activation)
        {
            SuspendLayout(); this.controller = controller; this.options = options; this.state = state; this.isolated = isolated;
            AutoScaleMode = AutoScaleMode.Dpi; ShellTheme.Apply(this); Text = "VASP-Copilot" + (isolated ? " · V3 隔离候选" : options.FullFeatures ? " · 完整功能复测" : ""); Width = 1280; Height = 900; MinimumSize = new Size(960, 700); StartPosition = FormStartPosition.CenterScreen;
            var bar = new FlowLayoutPanel { Dock = DockStyle.Top, Height = 54, Padding = new Padding(14, 5, 8, 5), BackColor = ShellTheme.Surface, WrapContents = false };
            var brand = ShellTheme.Label("VASP-Copilot", 12F); brand.Margin = new Padding(0, 8, 24, 0); bar.Controls.Add(brand);
            workflow.Enabled = false; workflow.Click += delegate { if (initialized && servicesReady && origin.Length > 0) web.CoreWebView2.Navigate(origin + "/workflow"); };
            retry.Click += delegate { RunStartup(); }; advanced.Click += async delegate { await Configure(); };
            var about = ShellTheme.Button("关于"); about.Click += delegate { using (var dialog = new AboutForm(isolated, options.FullFeatures)) dialog.ShowDialog(this); };
            var exit = ShellTheme.Button("退出"); exit.Click += delegate { Close(); };
            evidence.Visible = isolated; evidence.Enabled = false; evidence.Click += async delegate { await CaptureEvidence(); };
            bar.Controls.Add(workflow); bar.Controls.Add(advanced); bar.Controls.Add(about); bar.Controls.Add(retry); bar.Controls.Add(exit); if (isolated) bar.Controls.Add(evidence);
            status.Dock = DockStyle.Bottom; status.Height = 38; status.Padding = new Padding(16, 8, 8, 8); status.ForeColor = ShellTheme.Muted; status.BackColor = ShellTheme.Surface; status.Text = "准备桌面工作空间";
            web.Dock = DockStyle.Fill; web.DefaultBackgroundColor = ShellTheme.Background; web.Visible = false;
            BuildLaunch(); Controls.Add(web); Controls.Add(launch); Controls.Add(bar); Controls.Add(status);
            Shown += async delegate
            {
                if (String.IsNullOrWhiteSpace(options.RootDirectory))
                {
                    ShowLaunch("选择安装目录", "首次使用，请在启动设置选择已有安装目录。后续启动会自动准备本地服务。", false);
                    await Configure(); return;
                }
                RunStartup();
            };
            FormClosing += OnClosing; DpiChanged += async delegate { await RecordDisplay("dpi-changed"); };
            timer.Interval = 400; timer.Tick += delegate
            {
                if (closing) return;
                if (activation.WaitOne(0)) { if (WindowState == FormWindowState.Minimized) WindowState = FormWindowState.Normal; Show(); Activate(); Program.SetForegroundWindow(Handle); Log("activated", new { pid = System.Diagnostics.Process.GetCurrentProcess().Id }); }
                var snapshot = controller.GetSnapshot();
                launchLog.Visible = !String.IsNullOrEmpty(controller.EnvironmentFailureLogPath);
                if (startup != null && !startup.IsCompleted && !closing && !startupFailed)
                {
                    if (browserReady) { launchMessage.Text = snapshot.Message; UpdateSteps(snapshot.StartupStage); }
                }
                UpdateServiceHealth(snapshot);
            }; timer.Start();
            AutoScaleDimensions = new SizeF(96, 96); ResumeLayout(true);
            Log("session", new { pid = System.Diagnostics.Process.GetCurrentProcess().Id, isolated, shell = "V3" });
        }
        private void UpdateServiceHealth(LauncherSnapshot snapshot)
        {
            if (closing || snapshot.Busy || startupFailed || (startup != null && !startup.IsCompleted)) return;
            // A confirmed interruption hides the page without destroying its document.
            if (servicesReady && snapshot.Services.Exists(s => s.State == "error"))
                {
                    servicesReady = false; serviceInterrupted = true; serviceDegraded = false; workflow.Enabled = evidence.Enabled = false;
                    ShowLaunch("本地服务中断", "本地服务暂不可用。正在等待恢复，当前页面已保留；也可重试启动。", true);
                    steps[2].Text = "3. 本地服务  ·  已中断"; steps[3].Text = "4. 真实工作流界面  ·  等待恢复";
                    status.Text = "本地服务状态异常"; Log("service-failed", new { snapshot });
                }
            else if (serviceInterrupted && snapshot.Services.TrueForAll(s => s.State == "ready" || s.State == "disabled") && pageReady && initialized)
            {
                Uri current;
                if (!Uri.TryCreate(web.CoreWebView2.Source, UriKind.Absolute, out current) || !IsLocal(current)) return;
                serviceInterrupted = serviceDegraded = false; servicesReady = true; launch.Visible = false; web.Visible = true;
                workflow.Enabled = evidence.Enabled = true; status.Text = "工作空间已恢复，当前页面已保留";
                UpdateSteps("ready"); Log("service-recovered", new { snapshot, documentPreserved = true });
            }
            else if (servicesReady && pageReady)
            {
                bool degraded = snapshot.Services.Exists(s => s.State == "degraded");
                if (degraded != serviceDegraded)
                {
                    serviceDegraded = degraded;
                    status.Text = degraded ? "本地服务响应暂缓，正在复核；当前页面保留" : "工作空间已就绪" + (isolated ? " · 隔离候选" : "");
                }
            }
        }
        private void BuildLaunch()
        {
            launch.Dock = DockStyle.Fill; launch.AutoScroll = true; launch.BackColor = ShellTheme.Background;
            var center = new TableLayoutPanel { Dock = DockStyle.Fill, AutoSize = true, MinimumSize = new Size(800, 620), ColumnCount = 3, RowCount = 3 };
            center.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50)); center.ColumnStyles.Add(new ColumnStyle(SizeType.Absolute, 650)); center.ColumnStyles.Add(new ColumnStyle(SizeType.Percent, 50));
            center.RowStyles.Add(new RowStyle(SizeType.Percent, 50)); center.RowStyles.Add(new RowStyle(SizeType.AutoSize)); center.RowStyles.Add(new RowStyle(SizeType.Percent, 50));
            var card = new FlowLayoutPanel { AutoSize = true, Dock = DockStyle.Fill, FlowDirection = FlowDirection.TopDown, WrapContents = false, Padding = new Padding(32, 18, 32, 18) };
            card.Controls.Add(ShellTheme.Logo(72)); card.Controls.Add(ShellTheme.Label("DESKTOP WORKSPACE" + (options.FullFeatures ? " · 完整功能复测" : ""), 10F, true)); card.Controls.Add(ShellTheme.Label("VASP-Copilot", 28F));
            card.Controls.Add(ShellTheme.Label("把研究意图，连接到可检查的计算计划。", 11F, true));
            if (options.FullFeatures) { var fullHint = ShellTheme.Label("完整功能复测：可配置真实模型、MP与超算；操作仍按当前流程确认", 9F, true); fullHint.MaximumSize = new Size(570, 0); card.Controls.Add(fullHint); }
            launchTitle.AutoSize = true; launchTitle.Font = new Font(Font.FontFamily, 13F); launchTitle.ForeColor = ShellTheme.Blue; launchTitle.Margin = new Padding(0, 18, 0, 6); card.Controls.Add(launchTitle);
            launchMessage.AutoSize = true; launchMessage.MaximumSize = new Size(570, 0); launchMessage.ForeColor = ShellTheme.Muted; launchMessage.Margin = new Padding(0, 2, 0, 12); card.Controls.Add(launchMessage);
            for (int i = 0; i < steps.Length; i++) { steps[i] = ShellTheme.Label("", 10F, true); card.Controls.Add(steps[i]); }
            var buttons = new FlowLayoutPanel { AutoSize = true, Margin = new Padding(0, 12, 0, 12) };
            launchRetry.Click += delegate { RunStartup(); }; launchSettings.Click += async delegate { await Configure(); }; buttons.Controls.Add(launchRetry); buttons.Controls.Add(launchSettings);
            launchLog.Visible = false; launchLog.Click += delegate { OpenPreparationLog(); }; buttons.Controls.Add(launchLog); card.Controls.Add(buttons);
            card.Controls.Add(ShellTheme.Label(ShellTheme.Institution + " / " + ShellTheme.Group, 10F, true)); card.Controls.Add(ShellTheme.Label(ShellTheme.Authors, 10F, true));
            center.Controls.Add(card, 1, 1); launch.Controls.Add(center); ShowLaunch("准备桌面工作空间", "正在检查启动信息。", false); UpdateSteps("");
        }
        private void OpenPreparationLog()
        {
            string path = controller.EnvironmentFailureLogPath;
            if (String.IsNullOrEmpty(path) || !File.Exists(path)) return;
            try
            {
                System.Diagnostics.Process.Start(new System.Diagnostics.ProcessStartInfo {
                    FileName = Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.System), "notepad.exe"),
                    Arguments = OwnedProcess.Quote(path), UseShellExecute = false });
            }
            catch { MessageBox.Show(this, "无法打开安装日志，可手动查看：\n" + path, "安装日志", MessageBoxButtons.OK, MessageBoxIcon.Information); }
        }
        private void ShowLaunch(string title, string detail, bool failed)
        { launchTitle.Text = title; launchMessage.Text = detail; launchTitle.ForeColor = failed ? Color.FromArgb(234, 160, 155) : ShellTheme.Blue; launch.Visible = true; launch.BringToFront(); web.Visible = false; if (failed) foreach (var step in steps) if (step != null && step.Text.Contains("正在准备")) step.Text = step.Text.Replace("正在准备", "未完成"); }
        private void SetActions(bool available)
        { retry.Enabled = advanced.Enabled = launchRetry.Enabled = launchSettings.Enabled = available; }
        private void UpdateSteps(string stage)
        {
            bool autoPrepare = options.FullFeatures && options.AutoPrepareEnvironment;
            string[] names = autoPrepare
                ? new[] { "桌面运行时", "检测 Python", "启动本地服务", "打开工作流页面" }
                : new[] { "桌面运行时", "已有 Python 环境", "本地服务", "真实工作流界面" };
            if (autoPrepare)
            {
                switch (stage)
                {
                    case "environment_create": names[1] = "创建应用专用环境"; break;
                    case "environment_install": names[1] = "安装运行依赖"; break;
                    case "environment_verify": names[1] = "验证运行依赖"; break;
                    case "environment_ready": names[1] = "Python 与依赖已就绪"; break;
                }
            }
            int active = !browserReady ? 0 : stage == "services" || stage == "health" || stage == "environment_ready" ? 2 : stage == "ready" ? 3 : 1;
            for (int i = 0; i < steps.Length; i++) { string suffix = i < active || (i == 3 && pageReady) ? "已确认" : i == active ? "正在准备" : "等待"; steps[i].Text = (i + 1) + ". " + names[i] + "  ·  " + suffix; steps[i].ForeColor = i == active ? ShellTheme.Blue : ShellTheme.Muted; }
            if (stage != lastStage) { lastStage = stage; Log("startup-progress", new { stage, browserReady }); }
        }
        private void Log(string kind, object data)
        { try { File.AppendAllText(Path.Combine(state, "desktop-events.jsonl"), new JavaScriptSerializer().Serialize(new { utc = DateTime.UtcNow.ToString("o"), kind, data }) + Environment.NewLine); } catch { } }
        private void RunStartup()
        {
            if (closing || (startup != null && !startup.IsCompleted)) return;
            servicesReady = pageReady = startupFailed = serviceInterrupted = serviceDegraded = false; workflow.Enabled = evidence.Enabled = false;
            ShowLaunch("正在准备工作空间", "正在检查桌面运行时与启动信息…", false); SetActions(false); UpdateSteps(""); startup = StartAsync();
        }
        private async Task Configure()
        {
            if (closing || (startup != null && !startup.IsCompleted)) return;
            using (var dialog = new SettingsForm(options, controller.SelectedPythonDescription))
            {
                if (dialog.ShowDialog(this) != DialogResult.OK) return;
                LauncherPreferences.Save(options); RunStartup();
            }
            await Task.FromResult(0);
        }
        private async Task InitializeWeb()
        {
            if (initialized) return;
            string browserFolder = Environment.GetEnvironmentVariable("VASP_D2_BROWSER_FOLDER");
            string runtime = CoreWebView2Environment.GetAvailableBrowserVersionString(browserFolder);
            var environment = await CoreWebView2Environment.CreateAsync(browserFolder, Path.Combine(state, "webview-data"));
            await web.EnsureCoreWebView2Async(environment); web.ZoomFactor = 1.0;
            if (isolated)
            {
                web.CoreWebView2.AddWebResourceRequestedFilter("*", CoreWebView2WebResourceContext.All);
                web.CoreWebView2.WebResourceRequested += delegate(object sender, CoreWebView2WebResourceRequestedEventArgs e)
                {
                    Uri uri;
                    if (Uri.TryCreate(e.Request.Uri, UriKind.Absolute, out uri) && (uri.Scheme == "http" || uri.Scheme == "https") && !IsLocal(uri))
                        e.Response = environment.CreateWebResourceResponse(new MemoryStream(new byte[0]), 403, "Isolated profile: local resources only", "Content-Type: text/plain");
                };
            }
            web.CoreWebView2.NavigationStarting += delegate(object sender, CoreWebView2NavigationStartingEventArgs e) { Uri uri; if (!Uri.TryCreate(e.Uri, UriKind.Absolute, out uri) || !IsLocal(uri)) e.Cancel = true; else navigationId = e.NavigationId; };
            web.CoreWebView2.NewWindowRequested += delegate(object sender, CoreWebView2NewWindowRequestedEventArgs e) { e.Handled = true; };
            web.CoreWebView2.NavigationCompleted += async delegate(object sender, CoreWebView2NavigationCompletedEventArgs e)
            {
                if (closing || e.NavigationId != navigationId) return;
                Uri current; bool local = Uri.TryCreate(web.CoreWebView2.Source, UriKind.Absolute, out current) && IsLocal(current);
                Log("navigation", new { success = e.IsSuccess, error = e.WebErrorStatus.ToString(), local });
                pageReady = e.IsSuccess && local;
                if (e.IsSuccess && local && servicesReady)
                {
                    pageReady = true; launch.Visible = false; web.Visible = true; workflow.Enabled = evidence.Enabled = true;
                    status.Text = "工作空间已就绪" + (isolated ? " · 隔离候选" : ""); UpdateSteps("ready"); Log("workspace-ready", new { page = "/workflow" }); await RecordDisplay("navigation");
                }
                else if (!e.IsSuccess && servicesReady)
                { pageReady = false; evidence.Enabled = false; ShowLaunch("界面加载失败", "真实工作流界面未能加载（" + e.WebErrorStatus + "）。请重试启动。", true); status.Text = "界面加载失败"; SetActions(true); }
            };
            web.CoreWebView2.DownloadStarting += OnDownload; initialized = browserReady = true; Log("browser-ready", new { runtime, sdk = "1.0.3650.58" });
        }
        private async Task StartAsync()
        {
            bool cleanup = false;
            try
            {
                status.Text = "正在准备工作空间"; await InitializeWeb();
                if (closing) return;
                UpdateSteps("environment");
                await Task.Run(delegate { if (closing) return; if (controller.IsRunning) controller.Restart(options); else controller.Start(options); });
                if (closing) return;
                origin = controller.GetSnapshot().WebUrl; servicesReady = true;
                launchTitle.Text = "正在打开工作流"; launchMessage.Text = "本地服务已核验，正在加载真实应用界面。"; UpdateSteps("ready"); status.Text = "正在加载工作流界面";
                Log("ready", new { root = options.RootDirectory, python = controller.SelectedPython, attempts = controller.StartupAttempts, snapshot = controller.GetSnapshot() });
                if (!closing) web.CoreWebView2.Navigate(origin + "/workflow");
            }
            catch (WebView2RuntimeNotFoundException) { startupFailed = true; ShowLaunch("桌面运行时未就绪", "未找到 WebView2 Runtime。请准备已有桌面运行时后重试。", true); status.Text = "桌面运行时未就绪"; Log("startup-failed", new { category = "WEBVIEW2_RUNTIME_NOT_FOUND" }); }
            catch (Exception e)
            {
                startupFailed = true; servicesReady = false;
                string detail = e is LauncherException ? e.Message : "桌面启动失败（" + e.GetType().Name + "），可重试或打开启动设置。";
                ShowLaunch("启动未完成", detail, true); status.Text = "启动未完成"; Log("startup-failed", new { category = e.GetType().Name }); cleanup = true;
            }
            if (cleanup) try { await Task.Run(delegate { controller.Stop(); }); } catch { launchMessage.Text += " 所属服务清理未核验，请再次退出。"; }
            if (!closing) SetActions(true);
        }
        private bool IsLocal(Uri uri) { Uri allowed; return Uri.TryCreate(origin, UriKind.Absolute, out allowed) && uri.Scheme == allowed.Scheme && uri.Host == allowed.Host && uri.Port == allowed.Port; }
        private async Task RecordDisplay(string trigger)
        {
            if (closing || !initialized) return;
            try { string page = await web.CoreWebView2.ExecuteScriptAsync("JSON.stringify({devicePixelRatio:window.devicePixelRatio,viewportScale:window.visualViewport?window.visualViewport.scale:null,innerWidth:innerWidth,innerHeight:innerHeight,rootZoom:getComputedStyle(document.documentElement).zoom})"); Log("display", new { trigger, formDpi = DeviceDpi, webDpi = web.DeviceDpi, zoomFactor = web.ZoomFactor, clientWidth = ClientSize.Width, clientHeight = ClientSize.Height, webWidth = web.Width, webHeight = web.Height, pageMetricsJson = page }); } catch (Exception e) { Log("display-read-failed", new { category = e.GetType().Name }); }
        }
        private async Task CaptureEvidence()
        { if (!initialized) return; string file = Path.Combine(state, "page-" + (++evidenceNumber).ToString("D2") + ".png"); using (var stream = File.Create(file)) await web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png, stream); await RecordDisplay("capture"); }
        private void OnDownload(object sender, CoreWebView2DownloadStartingEventArgs e)
        {
            e.Handled = true; var deferral = e.GetDeferral();
            BeginInvoke((Action)delegate
            {
                try
                {
                    using (var dialog = new SaveFileDialog { Title = "保存下载文件（请选择新文件名）", InitialDirectory = Environment.GetFolderPath(Environment.SpecialFolder.MyDocuments), FileName = Path.GetFileName(e.ResultFilePath), OverwritePrompt = false, AddExtension = true })
                    {
                        if (dialog.ShowDialog(this) != DialogResult.OK) { e.Cancel = true; return; }
                        string target = Path.GetFullPath(dialog.FileName);
                        if (File.Exists(target)) { e.Cancel = true; status.Text = "文件已存在，请重新下载并选择新文件名。"; return; }
                        // Same directory/volume as target: final File.Move remains atomic and never overwrites.
                        string staging = Path.Combine(Path.GetDirectoryName(target), ".vasp-copilot-" + Guid.NewGuid().ToString("N") + ".download"); e.ResultFilePath = staging;
                        var operation = e.DownloadOperation; bool completed = false;
                        operation.StateChanged += delegate
                        {
                            try
                            {
                                if (operation.State == CoreWebView2DownloadState.Completed && !completed)
                                {
                                    completed = true; File.Move(staging, target);
                                    status.Text = "下载完成：" + target; Log("download-completed", new { bytes = new FileInfo(target).Length });
                                }
                                else if (operation.State == CoreWebView2DownloadState.Interrupted) status.Text = "下载未完成（" + operation.InterruptReason + "）。";
                            }
                            catch (Exception exception) { status.Text = "保存未完成（" + exception.GetType().Name + "）；临时文件已保留。"; Log("download-finalize-failed", new { category = exception.GetType().Name }); }
                        };
                    }
                }
                catch (Exception exception) { e.Cancel = true; status.Text = "保存失败（" + exception.GetType().Name + "）。"; }
                finally { deferral.Complete(); }
            });
        }
        private async void OnClosing(object sender, FormClosingEventArgs e)
        {
            if (finished) return; e.Cancel = true; if (closing) return; closing = true; controller.CancelStartup(); Enabled = false; ShowLaunch("正在退出", "正在停止本次所属服务…", false); status.Text = "正在停止本次所属服务…";
            try { if (startup != null) await startup; timer.Stop(); await Task.Run(delegate { controller.Dispose(); }); web.Dispose(); Log("closed", new { ownedServicesStopped = !controller.IsRunning }); finished = true; Close(); }
            catch (Exception exception) { status.Text = "退出清理未核验（" + exception.GetType().Name + "），请再次退出。"; Log("cleanup-failed", new { category = exception.GetType().Name }); closing = false; Enabled = true; timer.Start(); }
        }
    }
}
