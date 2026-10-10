using System;
using System.Drawing;
using System.IO;
using System.Linq;
using System.Reflection;
using System.Threading;
using System.Threading.Tasks;
using System.Web.Script.Serialization;
using System.Windows.Forms;
using Microsoft.Web.WebView2.Core;
using Microsoft.Web.WebView2.WinForms;

// Executes the actual shipped DesktopForm/SettingsForm and production frontend.
// The host supplies only synthetic profile and prepared Python, never real data.
class PackagedCenterProbe
{
    static object Field(object o, string name) { return o.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).GetValue(o); }
    static void Set(object o, string name, object value) { o.GetType().GetField(name, BindingFlags.Instance | BindingFlags.NonPublic).SetValue(o, value); }
    static bool Flag(object o, string name) { return (bool)Field(o, name); }
    static void Require(bool ok, string message) { if (!ok) throw new Exception(message); }
    static async Task Wait(Func<bool> condition, int timeout, string message)
    { var end = DateTime.UtcNow.AddMilliseconds(timeout); while (!condition() && DateTime.UtcNow < end) await Task.Delay(50); Require(condition(), message); }
    static async Task WaitJs(WebView2 web, string expression, string message)
    { var end = DateTime.UtcNow.AddSeconds(20); while (DateTime.UtcNow < end) { if (await web.CoreWebView2.ExecuteScriptAsync("Boolean(" + expression + ")") == "true") return; await Task.Delay(80); } throw new Exception(message); }
    static void Property(object o, string key, object value) { o.GetType().GetProperty(key).SetValue(o, value, null); }
    [STAThread] static int Main(string[] args)
    {
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        string package = args[0], state = args[2]; Directory.CreateDirectory(state);
        var assembly = Assembly.LoadFrom(Path.Combine(package, "VASP-Copilot.exe"));
        var store = Activator.CreateInstance(assembly.GetType("VaspCopilot.Launcher.LauncherPreferencesStore"), new object[] { state, null });
        assembly.GetType("VaspCopilot.Launcher.LauncherPreferences").GetMethod("Initialize").Invoke(null, new[] { store });
        var options = Activator.CreateInstance(assembly.GetType("VaspCopilot.Launcher.LauncherOptions"));
        Property(options, "RootDirectory", package); Property(options, "PythonExecutable", args[3] == "failure" ? Path.Combine(state,"missing-python.exe") : args[1]);
        Property(options, "EnableAi", false); Property(options, "IsolatedProfile", true);
        Property(options, "VaspAiHome", Path.Combine(state,"home")); Property(options,"DataDirectory",Path.Combine(state,"data"));
        var controller = Activator.CreateInstance(assembly.GetType("VaspCopilot.Launcher.LauncherController"), new object[] { Path.Combine(package,"launcher/runtime.py") });
        using (var activation = new EventWaitHandle(false, EventResetMode.AutoReset))
        using (var form = (Form)Activator.CreateInstance(assembly.GetType("VaspCopilot.DesktopV3.DesktopForm"), BindingFlags.NonPublic | BindingFlags.Instance, null, new[] { controller, options, state, true, activation, package }, null))
        {
            form.StartPosition = FormStartPosition.Manual; form.Location = new Point(-20000,-20000); form.ShowInTaskbar = false;
            bool passed = false, missing = false; string error = ""; var json = new JavaScriptSerializer();
            form.Shown += async delegate
            {
                try
                {
                    var settingsButton = (Button)Field(form,"advanced");
                    var buttons = settingsButton.Parent.Controls.OfType<Button>().Select(b=>b.Text).ToArray();
                    Require(!buttons.Contains("工作流") && new[]{"设置","关于","重试启动","退出"}.All(buttons.Contains),"Top buttons incorrect");
                    if (args[3] == "failure" || args[3] == "webview-missing")
                    {
                        await Wait(()=>Flag(form,"startupFailed"),60000,"missing Python did not fail");
                        int opened=0;
                        using (var ui = new System.Windows.Forms.Timer { Interval=50 })
                        {
                            ui.Tick += delegate {
                                var dialog=Application.OpenForms.Cast<Form>().FirstOrDefault(f=>f.GetType().Name=="SettingsForm");
                                if (dialog==null) return; opened++; ui.Stop();
                                ((TextBox)dialog.Controls.Find("pythonExecutable",true)[0]).Text=args[1];
                                ((Button)dialog.Controls.Find("savePreferences",true)[0]).PerformClick();
                            };
                            ui.Start(); settingsButton.PerformClick();
                        }
                        Require(opened==1,"native failure settings unavailable");
                        var saved=json.DeserializeObject(File.ReadAllText(Path.Combine(state,"preferences.json"))) as System.Collections.Generic.Dictionary<string,object>;
                        Require((string)saved["PythonExecutable"]==args[1],"failure recovery did not save");
                        if (args[3] == "webview-missing") { await Wait(()=>Flag(form,"startupFailed"),10000,"unavailable owned WebView path did not fail"); passed=true; return; }
                    }
                    await Wait(()=>Flag(form,"pageReady")&&Flag(form,"settingsBridgeReady")||Flag(form,"startupFailed"),60000,"actual frontend/bridge did not load");
                    if (!Flag(form,"pageReady"))
                    {
                        missing=File.ReadAllText(Path.Combine(state,"desktop-events.jsonl")).Contains("WEBVIEW2_RUNTIME_NOT_FOUND");
                        Require(missing,"actual package startup failed"); return;
                    }
                    var web=(WebView2)Field(form,"web");
                    if(args[3]=="browser")
                    {
                        File.WriteAllText(Path.Combine(state,"browser-origin.json"),json.Serialize(new{origin=(string)Field(form,"origin")}));
                        await Wait(()=>File.Exists(Path.Combine(state,"browser-done")),120000,"independent browser did not finish"); passed=true; return;
                    }
                    await WaitJs(web,"document.querySelector('input[type=file]')","workflow upload missing");
                    ulong navigation=(ulong)Field(form,"navigationId");
                    await web.CoreWebView2.ExecuteScriptAsync("window.__dt02Document='same-document';const d=new DataTransfer();d.items.add(new File(['Si synthetic\\n1.0\\n5 0 0\\n0 5 0\\n0 0 5\\nSi\\n1\\nDirect\\n0 0 0\\n'],'Si.POSCAR'));const f=document.querySelector('input[type=file]');f.files=d.files;f.dispatchEvent(new Event('change',{bubbles:true}));");
                    await WaitJs(web,"document.querySelector('input[aria-label=\"样品名称\"]')","synthetic structure not uploaded");
                    await web.CoreWebView2.ExecuteScriptAsync("const e=document.querySelector('input[aria-label=\"样品名称\"]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(e,'DT02 retained draft');e.dispatchEvent(new Event('input',{bubbles:true}));");
                    await WaitJs(web,"document.querySelector('input[aria-label=\"样品名称\"]').value==='DT02 retained draft'","draft input failed");
                    settingsButton.PerformClick();
                    await WaitJs(web,"location.pathname==='/settings' && document.getElementById('settings-execution') && document.querySelector('button[type=submit]')","native settings soft navigation failed");
                    Require(navigation==(ulong)Field(form,"navigationId"),"settings caused document navigation");
                    await WaitJs(web,"document.querySelectorAll('a[aria-label=\"设置\"]').length===0 && !Array.from(document.querySelectorAll('nav a')).some(a=>a.textContent==='执行设置')","duplicate desktop settings entry");
                    await web.CoreWebView2.ExecuteScriptAsync("const j=document.querySelector('#max_jobs');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(j,'7');j.dispatchEvent(new Event('input',{bubbles:true}));document.querySelector('button[type=submit]').click();");
                    await WaitJs(web,"document.body.textContent.includes('Toolbox 执行设置已保存')","actual execution save failed");
                    await web.CoreWebView2.ExecuteScriptAsync("fetch('/api/v1/toolbox/settings').then(r=>r.json()).then(r=>window.__dt02Saved=r.settings.max_jobs===7 && r.settings.ssh.host==='')");
                    await WaitJs(web,"window.__dt02Saved===true","execution values or synthetic SSH guard incorrect");
                    await web.CoreWebView2.ExecuteScriptAsync("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.replace(/\\s/g,'')==='测试SSH连接').click()");
                    await WaitJs(web,"document.body.textContent.includes('未配置') || document.body.textContent.includes('填写')","unconfigured saved SSH feedback missing");
                    using(var image=File.Create(Path.Combine(state,"desktop-center.png"))) await web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                    var validate=form.GetType().GetMethod("ValidSettingsMessage",BindingFlags.NonPublic|BindingFlags.Instance);
                    string source=web.CoreWebView2.Source, nonce=(string)Field(form,"settingsNonce");
                    Func<string,string,bool> valid=(url,payload)=>{var values=new object[]{url,payload,""};return (bool)validate.Invoke(form,values);};
                    string query=json.Serialize(new{type="vasp-desktop-settings",version=1,action="query-capabilities"});
                    string request=json.Serialize(new{type="vasp-desktop-settings",version=1,action="open-launch-settings",nonce});
                    Require(valid(source,query)&&valid(source,request),"valid fixed actions rejected");
                    Require(!valid("https://example.invalid/settings",request)&&!valid(source,request.Replace(nonce,new string('b',32)))
                        &&!valid(source,"{}")&&!valid(source,query.TrimEnd('}')+",\"url\":\"file:///C:/\"}")
                        &&!valid(source,request.Replace("open-launch-settings","execute-command")),"invalid settings message accepted");
                    Set(form,"pageReady",false); Require(!valid(source,request),"stale document accepted"); Set(form,"pageReady",true);
                    int localOpened=0;
                    using(var ui=new System.Windows.Forms.Timer{Interval=50})
                    {
                        ui.Tick+=delegate {var dialog=Application.OpenForms.Cast<Form>().FirstOrDefault(f=>f.GetType().Name=="SettingsForm");if(dialog==null)return;localOpened++;ui.Stop();((Button)dialog.Controls.Find("savePreferences",true)[0]).PerformClick();};ui.Start();
                        await web.CoreWebView2.ExecuteScriptAsync("Array.from(document.querySelectorAll('button')).find(b=>b.textContent.replace(/\\s/g,'')==='打开本地启动配置').click()");
                        await Wait(()=>localOpened==1,10000,"center could not open native settings");
                    }
                    await web.CoreWebView2.ExecuteScriptAsync("document.querySelector('a[aria-label=\"生成工作流\"]').click()");
                    await WaitJs(web,"document.querySelector('input[aria-label=\"样品名称\"]')?.value==='DT02 retained draft' && window.__dt02Document==='same-document'","workflow draft lost");
                    Require(navigation==(ulong)Field(form,"navigationId"),"return reloaded document");
                    form.Size=new Size(960,700); await Task.Delay(250);
                    using(var image=File.Create(Path.Combine(state,"desktop-minimum.png"))) await web.CoreWebView2.CapturePreviewAsync(CoreWebView2CapturePreviewImageFormat.Png,image);
                    Require(settingsButton.Parent.Controls.OfType<Button>().All(b=>b.Right<=b.Parent.ClientSize.Width),"top button clipped at minimum size");
                    passed=true;
                }
                catch(Exception e){error=e.ToString();}
                finally{File.WriteAllText(Path.Combine(state,"results.json"),json.Serialize(new{passed,missingRuntime=missing,error,actualPackagedDesktop=true,scenario=args[3]}));form.Close();}
            };
            Application.Run(form); return passed||missing?0:1;
        }
    }
}
