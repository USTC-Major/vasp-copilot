using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.WinForms;
using VaspCopilot.DesktopV3;
using VaspCopilot.Launcher;

class DesktopHealthHarness
{
    static object Field(object target, string name) { return target.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(target); }
    static bool Flag(object target, string name) { return (bool)Field(target, name); }
    static void Require(bool ok, string message) { if (!ok) throw new Exception(message); }
    static async Task Wait(Func<bool> condition, int milliseconds, string message)
    {
        DateTime end = DateTime.UtcNow.AddMilliseconds(milliseconds);
        while (!condition() && DateTime.UtcNow < end) await Task.Delay(60);
        Require(condition(), message);
    }
    [STAThread] static int Main(string[] args)
    {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        var json = new JavaScriptSerializer(); string evidence = args[3]; Directory.CreateDirectory(evidence);
        var options = new LauncherOptions { RootDirectory = args[0], PythonExecutable = args[1], EnableAi = true, IsolatedProfile = true,
            VaspAiHome = Path.Combine(evidence, "home"), DataDirectory = Path.Combine(evidence, "data") };
        var controller = new LauncherController(args[2]);
        using (var activation = new EventWaitHandle(false, EventResetMode.AutoReset))
        using (var form = new DesktopForm(controller, options, evidence, true, activation))
        {
            form.StartPosition = FormStartPosition.Manual; form.Location = new Point(-20000, -20000); form.ShowInTaskbar = false;
            bool ok = false; string error = "";
            form.Shown += async delegate
            {
                try
                {
                    await Wait(() => Flag(form, "pageReady"), 60000, "test WebView never loaded");
                    var web = (WebView2)Field(form, "web"); var launch = (Panel)Field(form, "launch");
                    string before = await web.CoreWebView2.ExecuteScriptAsync("document.getElementById('draft').value='synthetic-unsaved-draft';window.__healthDocument='preserve-this-document';JSON.stringify({draft:document.getElementById('draft').value,marker:window.__healthDocument})");
                    ulong navigation = (ulong)Field(form, "navigationId");
                    File.WriteAllText(Path.Combine(args[0], "toolbox-mode"), "slow");
                    await Wait(() => controller.GetSnapshot().Services.Exists(s => s.Key == "toolbox" && s.State == "degraded"), 6000, "brief degradation not seen");
                    await Task.Delay(1800); Require(!launch.Visible && web.Visible && Flag(form, "pageReady"), "brief degradation hid workspace");
                    File.WriteAllText(Path.Combine(args[0], "toolbox-mode"), "normal");
                    await Wait(() => controller.GetSnapshot().Services.TrueForAll(s => s.State == "ready"), 6000, "brief recovery not seen");
                    File.WriteAllText(Path.Combine(args[0], "toolbox-mode"), "slow");
                    await Wait(() => Flag(form, "serviceInterrupted"), 17000, "persistent failure did not show interruption");
                    Require(launch.Visible && !web.Visible && Flag(form, "pageReady"), "interruption discarded loaded-page state");
                    File.WriteAllText(Path.Combine(args[0], "toolbox-mode"), "normal");
                    await Wait(() => !Flag(form, "serviceInterrupted") && Flag(form, "servicesReady"), 6000, "workspace did not automatically restore");
                    string after = await web.CoreWebView2.ExecuteScriptAsync("JSON.stringify({draft:document.getElementById('draft').value,marker:window.__healthDocument})");
                    Require(before == after && navigation == (ulong)Field(form, "navigationId"), "recovery navigated or discarded draft/document");
                    Require(!launch.Visible && web.Visible && Flag(form, "pageReady"), "restored page not visible");
                    var status = (Label)Field(form, "status"); status.Text = "synthetic-download-success";
                    await Task.Delay(1300); Require(status.Text == "synthetic-download-success", "stable health overwrote unrelated status message");
                    ok = true;
                    File.WriteAllText(Path.Combine(evidence, "ui-result.json"), json.Serialize(new { ok, actualWebView = true, briefTimeoutDidNotHidePage = true,
                        persistentFailureShowedOverlay = true, recoveredAutomatically = true, sameDocumentAndUnsavedDraft = before == after,
                        sameNavigationId = true, unrelatedStatusPreserved = true, snapshot = controller.GetSnapshot() }));
                }
                catch (Exception exc) { error = exc.ToString(); File.WriteAllText(Path.Combine(evidence, "ui-result.json"), json.Serialize(new { ok, error, snapshot = controller.GetSnapshot() })); }
                finally { form.Close(); }
            };
            Application.Run(form); return ok ? 0 : 1;
        }
    }
}
