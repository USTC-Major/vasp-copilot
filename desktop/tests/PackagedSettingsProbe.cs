using System;
using System.Drawing;
using System.IO;
using System.Reflection;
using System.Windows.Forms;
using System.Web.Script.Serialization;

// Load the shipped EXE's actual form and storage. No copied persistence logic,
// business services or normal Windows-user preferences are exercised here.
class PackagedSettingsProbe
{
    [STAThread] static int Main(string[] args)
    {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        var assembly = Assembly.LoadFrom(args[0]);
        var facade = assembly.GetType("VaspCopilot.Launcher.LauncherPreferences");
        var storeType = assembly.GetType("VaspCopilot.Launcher.LauncherPreferencesStore");
        var store = Activator.CreateInstance(storeType, new object[] { args[2], null });
        facade.GetMethod("Initialize").Invoke(null, new[] { store });
        var options = facade.GetMethod("Load").Invoke(null, null);
        var optionType = options.GetType(); optionType.GetProperty("FullFeatures").SetValue(options, true, null); optionType.GetProperty("AutoPrepareEnvironment").SetValue(options, true, null);
        var program = assembly.GetType("VaspCopilot.DesktopV3.Program"); var mode = program.GetMethod("FullFeatures", BindingFlags.Static | BindingFlags.NonPublic);
        if (!(bool)mode.Invoke(null, new object[] { new string[0] }) || !(bool)mode.Invoke(null, new object[] { new[] { "--full-features" } }) || (bool)mode.Invoke(null, new object[] { new[] { "--test-profile", args[2] } })) throw new Exception("Published entry parser mismatch");
        var formType = assembly.GetType("VaspCopilot.DesktopV3.SettingsForm");
        using (var form = (Form)Activator.CreateInstance(formType, BindingFlags.NonPublic | BindingFlags.Instance, null, new[] { options, "synthetic-last-runtime", null }, null))
        {
            form.StartPosition = FormStartPosition.Manual; form.Location = new Point(-20000, -20000); form.ShowInTaskbar = false;
            form.Shown += delegate
            {
                var root = (TextBox)form.Controls.Find("installationDirectory", true)[0]; var python = (TextBox)form.Controls.Find("pythonExecutable", true)[0];
                if (args[1] == "save")
                {
                    root.Text = args[3]; python.Text = args[4]; ((CheckBox)form.Controls.Find("enableAi", true)[0]).Checked = false;
                    ((Button)form.Controls.Find("savePreferences", true)[0]).PerformClick();
                }
                else
                {
                    if (root.Text != args[3] || python.Text != args[4] || (bool)optionType.GetProperty("EnableAi").GetValue(options, null)) throw new Exception("Published form restore mismatch");
                    using (var bitmap = new Bitmap(form.Width, form.Height)) { form.DrawToBitmap(bitmap, new Rectangle(0, 0, form.Width, form.Height)); bitmap.Save(Path.Combine(args[2], "packaged-settings-restored.png")); }
                    form.Close();
                }
            };
            form.ShowDialog();
            if (args[1] == "save" && form.DialogResult != DialogResult.OK) throw new Exception("Published actual save failed");
        }
        Console.WriteLine(new JavaScriptSerializer().Serialize(new { passed = true, action = args[1], packagedExecutable = args[0], actualSettingsForm = true })); return 0;
    }
}
