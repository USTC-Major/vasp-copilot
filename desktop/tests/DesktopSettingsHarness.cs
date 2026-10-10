using System;
using System.Drawing;
using System.IO;
using System.Windows.Forms;
using System.Web.Script.Serialization;
using VaspCopilot.DesktopV3;
using VaspCopilot.Launcher;

class DesktopSettingsHarness
{
    [STAThread] static int Main(string[] args)
    {
        Console.OutputEncoding = new System.Text.UTF8Encoding(false);
        Application.EnableVisualStyles(); Application.SetCompatibleTextRenderingDefault(false);
        LauncherPreferences.Initialize(new LauncherPreferencesStore(args[1]));
        if (!Program.FullFeatures(new string[0]) || !Program.FullFeatures(new[] { "--full-features" }) || Program.FullFeatures(new[] { "--test-profile", args[1] })) throw new Exception("Entry modes inconsistent");
        if (Program.InstanceKey("test-user", "desktop") == Program.InstanceKey("test-user", args[1])) throw new Exception("Isolation scope not distinct");
        var options = LauncherPreferences.Load(); options.FullFeatures = true; options.AutoPrepareEnvironment = true;
        using (var form = new SettingsForm(options, "synthetic-selected-runtime"))
        {
            form.StartPosition = FormStartPosition.Manual; form.Location = new Point(-20000, -20000); form.ShowInTaskbar = false;
            form.Shown += delegate
            {
                var root = (TextBox)form.Controls.Find("installationDirectory", true)[0];
                var python = (TextBox)form.Controls.Find("pythonExecutable", true)[0];
                if (args[0] == "save")
                {
                    root.Text = args[2]; python.Text = args[3]; ((CheckBox)form.Controls.Find("enableAi", true)[0]).Checked = false;
                    ((Button)form.Controls.Find("savePreferences", true)[0]).PerformClick();
                }
                else
                {
                    if (root.Text != args[2] || python.Text != args[3] || options.EnableAi) throw new Exception("New process UI did not restore saved fields");
                    using (var bitmap = new Bitmap(form.Width, form.Height)) { form.DrawToBitmap(bitmap, new Rectangle(0, 0, form.Width, form.Height)); bitmap.Save(Path.Combine(args[1], "settings-restored.png")); }
                    form.Close();
                }
            };
            form.ShowDialog();
            if (args[0] == "save" && form.DialogResult != DialogResult.OK) throw new Exception("Actual form save did not succeed");
        }
        Console.WriteLine(new JavaScriptSerializer().Serialize(new { passed = true, action = args[0], actualSettingsForm = true, target = LauncherPreferences.Store.Target })); return 0;
    }
}
