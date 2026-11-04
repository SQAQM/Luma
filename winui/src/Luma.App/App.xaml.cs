using Microsoft.UI.Xaml;

namespace Luma.Preview;

public partial class App : Application
{
    private Window? _window;
    private readonly PreviewOptions _options;

    public App()
    {
        try { _options = PreviewOptions.Parse(Environment.GetCommandLineArgs().Skip(1).ToArray()); }
        catch (ArgumentException) { Environment.Exit(2); throw; }
        catch (Exception error) when(error is IOException or UnauthorizedAccessException)
        {
            ShowStartupError("无法打开程序旁的 Data 数据目录。请将软件放在可写目录，检查磁盘空间和权限。软件不会改用 AppData。\n错误类型："+error.GetType().Name);
            Environment.Exit(3);throw;
        }
        UnhandledException += (_, args) =>
        {
            if (!_options.Diagnostics) return;
            string file = Path.Combine(_options.DataDirectory, "unhandled-error.json");
            if (File.Exists(file)) return;
            using var output = new FileStream(file, FileMode.CreateNew);
            var methods = new System.Diagnostics.StackTrace(args.Exception, false).GetFrames().Select(f => f.GetMethod()).Where(m => m?.DeclaringType?.Namespace?.StartsWith("Luma.Preview", StringComparison.Ordinal) == true).Select(m => m!.DeclaringType!.Name + "." + m.Name).Take(8).ToArray();
            System.Text.Json.JsonSerializer.Serialize(output, new { type = args.Exception.GetType().Name, hresult = args.Exception.HResult.ToString("X8"), component = Views.ShellView.InitializationPhase, methods });
        };
        try { InitializeComponent(); }
        catch (Exception error)
        {
            if (_options.Diagnostics)
            {
                using var output = new FileStream(Path.Combine(_options.DataDirectory, "startup-error.json"), FileMode.CreateNew);
                System.Text.Json.JsonSerializer.Serialize(output, new { type = error.GetType().Name, hresult = error.HResult.ToString("X8"), component = "app_resources" });
            }
            Environment.Exit(3); throw;
        }
    }

    private static void ShowStartupError(string text)=>MessageBoxW(0,text,"Luma · 数据目录不可用",0x10);
    [System.Runtime.InteropServices.DllImport("user32.dll",CharSet=System.Runtime.InteropServices.CharSet.Unicode)]
    private static extern int MessageBoxW(nint window,string text,string caption,uint flags);

    protected override void OnLaunched(LaunchActivatedEventArgs args)
    {
        try
        {
            _window = new MainWindow(_options);
            _window.Activate();
        }
        catch (ArgumentException)
        {
            // Invalid diagnostic paths never create or open the legacy data root.
            Environment.Exit(2);
        }
        catch (Exception error)
        {
            if (_options.Diagnostics)
            {
                using var output = new FileStream(Path.Combine(_options.DataDirectory, "startup-error.json"), FileMode.CreateNew);
                System.Text.Json.JsonSerializer.Serialize(output, new { type = error.GetType().Name, hresult = error.HResult.ToString("X8"), component = Views.ShellView.InitializationPhase, materialStep=Services.MaterialService.InitializationStep, methods=new System.Diagnostics.StackTrace(error,false).GetFrames().Select(f=>f.GetMethod()).Where(m=>m?.DeclaringType?.Namespace?.StartsWith("Luma.Preview",StringComparison.Ordinal)==true).Select(m=>m!.DeclaringType!.Name+"."+m.Name).Take(8).ToArray() });
            }
            Environment.Exit(3);
        }
    }
}
