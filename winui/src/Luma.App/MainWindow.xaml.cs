using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using System.Runtime.InteropServices;
using System.Runtime.InteropServices.WindowsRuntime;
using System.Text.Json;
using Windows.Graphics;
using Windows.Graphics.Imaging;
using Windows.Storage;
using Windows.UI.ViewManagement;

namespace Luma.Preview;

public sealed partial class MainWindow : Window
{
    private readonly PreviewOptions _options;
    private MainWindow? _fileWindow;
    private bool _pickerBusy;
    private readonly Views.ShellView Shell;
    private readonly Services.EngineSession _engine;
    private readonly Services.MaterialService _materials;
    private readonly Services.WindowChrome _chrome;
    private bool _closePending, _allowClose;
    private readonly SubclassCallback _settingsCallback;
    private readonly bool _settingsHook;
    private readonly nint _hwnd;
    private readonly List<string> _captures = [];
    private string _theme = "System";
    private bool _ready, _closed, _captureBusy, _captureAgain;
    private int _revision, _captureRevision;
    private string? _captureError;
    private readonly Luma.Core.Management.UiPreferences _preferences;
    private int _normalWidth=1000,_normalHeight=650;private bool _restoreMaximized;

    internal MainWindow(PreviewOptions options)
    {
        _options = options;
        _preferences=new(options.DataDirectory);
        InitializeComponent();
        Shell = new Views.ShellView();
        Shell.FileWindowMode=options.OpenFile is not null;
        _engine = new Services.EngineSession(options.DataDirectory,options.Diagnostics);
        Shell.AttachEngine(_engine);
        Shell.ConfigureStorage(options.DataDirectory,options.StorageRoot,_engine);
        _chrome=new Services.WindowChrome(this,Chrome,ChromeArea,ChromeTint,Shell.ResolveThemeBrush);
        Shell.ThemeColors=Surface.Resources.MergedDictionaries[0];
        _materials=new Services.MaterialService(DispatcherQueue,this,Surface,plan=>{WindowTint.Background=Shell.ResolveThemeBrush(plan.UseAcrylic?"LumaMaterialTint":"LumaCanvas");Shell.ApplyMaterialPlan(plan);_chrome.ApplyMaterial(plan);});
        _materials.Changed+=UpdateState;
        Shell.ActualThemeChanged+=(_,_)=>UpdateState();
        Shell.ConfigureDiagnostics(options.Diagnostics,options.ThumbnailPressure,options.MetadataFailureOnce);
        ContentHost.Children.Add(Shell);
        Title = "Luma · 图片管理";
        _hwnd = WinRT.Interop.WindowNative.GetWindowHandle(this);
        Shell.WindowHandle=_hwnd;
        _settingsCallback = OnWindowMessage;
        _settingsHook = SetWindowSubclass(_hwnd, _settingsCallback, 1, 0);
        Activated += (_, _) => { if (_ready && !_closed) Shell.RefreshSystemPreferences(); };
        string icon = Path.Combine(AppContext.BaseDirectory, "Assets", "Luma.ico");
        if (File.Exists(icon)) AppWindow.SetIcon(icon);
        var saved=_preferences.Load();_normalWidth=saved.Width;_normalHeight=saved.Height;_restoreMaximized=saved.Maximized;
        AppWindow.Resize(new SizeInt32(_normalWidth,_normalHeight));
        Shell.RestorePreferences(saved.Theme,saved.ReduceMotion);Shell.RestoreMaterialPreference(saved.UseMaterials);
        AppWindow.Changed+=(_,_)=>{if(AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter p&&p.State==Microsoft.UI.Windowing.OverlappedPresenterState.Restored){_normalWidth=AppWindow.Size.Width;_normalHeight=AppWindow.Size.Height;}};
        Shell.Changed += () => { _theme = Shell.Theme; UpdateState(); };
        if(_options.Capture){var snapshot=new Microsoft.UI.Xaml.Input.KeyboardAccelerator{Key=Windows.System.VirtualKey.F12};snapshot.Invoked+=(_,e)=>{e.Handled=true;UpdateState();};Surface.KeyboardAccelerators.Add(snapshot);}
        Shell.CloseRequested += RequestClose;
        Shell.OpenFileRequested+=PickImage;
        Shell.FullscreenRequested += value => {
            try{AppWindow.SetPresenter(value?Microsoft.UI.Windowing.AppWindowPresenterKind.FullScreen:Microsoft.UI.Windowing.AppWindowPresenterKind.Default);Shell.SetFullscreen(AppWindow.Presenter.Kind==Microsoft.UI.Windowing.AppWindowPresenterKind.FullScreen);}
            catch(Exception){Shell.SetFullscreen(AppWindow.Presenter.Kind==Microsoft.UI.Windowing.AppWindowPresenterKind.FullScreen);}
        };
        AppWindow.Closing += (_, args) =>
        {
            if (_allowClose) return;
            args.Cancel = true;
            // Cancel synchronously; return from native callback before async shutdown.
            DispatcherQueue.TryEnqueue(RequestClose);
        };
        Closed += (_, _) => { _closed = true; if (_settingsHook) RemoveWindowSubclass(_hwnd, _settingsCallback, 1); _materials.Dispose(); Shell.Dispose(); _revision++; WriteState(); };
    }

    private async void RequestClose()
    {
        if (_closed || _closePending) return;
        _closePending = true;
        await Shell.PrepareStorageCloseAsync();
        try{_preferences.Save(new(_normalWidth,_normalHeight,AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter p&&p.State==Microsoft.UI.Windowing.OverlappedPresenterState.Maximized,Shell.Theme,Shell.ReduceMotionPreference,Shell.MaterialsRequested));}catch(Exception error) when(error is IOException or UnauthorizedAccessException or Luma.Core.Engine.EngineRpcException){}
        await _engine.StopAsync(closing: true);
        if (_engine.IsAlive) { _closePending = false; return; }
        _allowClose = true;
        DispatcherQueue.TryEnqueue(Close);
    }

    private void SurfaceLoaded(object sender, RoutedEventArgs args)
    {
        Shell.Configure(_options.TextScale, _options.Layout);
        if(_restoreMaximized&&AppWindow.Presenter is Microsoft.UI.Windowing.OverlappedPresenter presenter)presenter.Maximize();
        _ready = true;
        UpdateState();
        if(_options.OpenFile is{} file)_=_engine.OpenFileAsync(file);
        if(_options.Diagnostics&&Environment.GetEnvironmentVariable("LUMA_RELEASE_UI_TEST")=="1")_=Shell.VerifyReleaseUi(_options.DataDirectory,CaptureVerificationFrame);
        if(_options.Diagnostics&&Environment.GetEnvironmentVariable("LUMA_FEATURE_UI_TEST")=="1")_=Shell.VerifyFeatureUi(_options.DataDirectory,CaptureVerificationFrame);
        if(_options.Diagnostics&&Environment.GetEnvironmentVariable("LUMA_MEDIA_UI_TEST")=="1")_=Shell.VerifyMediaUi(_options.DataDirectory,CaptureVerificationFrame);
    }
    private async void PickImage()
    {
        if(_closed||_closePending||_pickerBusy||_engine.StorageBusy||_engine.RestartRequired)return;
        _pickerBusy=true;
        try
        {
            var picker=new Microsoft.Windows.Storage.Pickers.FileOpenPicker(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(_hwnd));
            foreach(string extension in new[]{".png",".jpg",".jpeg",".webp",".gif",".bmp",".tif",".tiff",".ico",".avif"})picker.FileTypeFilter.Add(extension);
            var result=await picker.PickSingleFileAsync();
            if(result is not null&&!_closed&&!_closePending)
            {
                if(_options.OpenFile is null)
                {
                    if(_fileWindow is null)
                    {
                        string profile=Path.Combine(_options.StorageRoot,"file-window");Luma.Core.Management.PreviewProfile.Open(profile,false);
                        _fileWindow=new MainWindow(_options with{DataDirectory=profile,OpenFile=result.Path,Diagnostics=false,Capture=false});
                        _fileWindow.Closed+=(_,_)=>_fileWindow=null;
                    }
                    else await _fileWindow._engine.OpenFileAsync(result.Path);
                    if(!_closed&&!_closePending)_fileWindow?.Activate();
                }
                else await _engine.OpenFileAsync(result.Path);
            }
        }
        catch(Exception)
        {
            if(_closed||_closePending)return;
            var dialog=new ContentDialog{Title="无法打开图片",Content="文件选择或独立查看窗口未能完成。请确认文件可读取后重试。",CloseButtonText="知道了",XamlRoot=Surface.XamlRoot,RequestedTheme=Shell.ActualTheme};
            await dialog.ShowAsync();
        }
        finally{_pickerBusy=false;}
    }

    private void SurfaceSizeChanged(object sender, SizeChangedEventArgs args)
    {
        if (_ready && !_closed) UpdateState();
    }

    private void UpdateState()
    {
        if (!_ready || _closed) return;
        // Backdrop configuration follows the XamlRoot theme, not a nested page's override.
        Surface.RequestedTheme=Shell.RequestedTheme;
        _materials.Apply(Shell.MaterialsRequested,Shell.ActualTheme,Shell.ReduceMotionPreference);
        _chrome.Refresh();
        GetWindowRect(_hwnd, out var rectangle);
        int dark = Shell.ActualTheme == ElementTheme.Dark ? 1 : 0;
        _ = DwmSetWindowAttribute(_hwnd, 20, ref dark, sizeof(int));
        _revision++;
        WriteState();
        if (_options.Capture) _ = CaptureAsync();
    }

    private async Task CaptureVerificationFrame()
    {
        if(!_options.Diagnostics||!_options.Capture)return;
        var deadline=DateTime.UtcNow.AddSeconds(10);
        while(_captureBusy&&DateTime.UtcNow<deadline)await Task.Delay(20);
        if(_captureBusy)throw new InvalidOperationException("Own capture did not settle");
        await CaptureAsync();
        if(_captureError is not null)throw new InvalidOperationException("Own frame capture failed");
    }
    private async Task CaptureAsync()
    {
        if (_captureBusy) { _captureAgain = true; return; }
        _captureBusy = true;
        try
        {
            do
            {
                _captureAgain = false;
                int target = _revision;
                await Task.Delay(200); // Diagnostic capture settles layout without blocking input.
                if (_closed) return;
                var bitmap = new RenderTargetBitmap();
                await bitmap.RenderAsync(Surface);
                if (_closed) return;
                if (_revision != target) { _captureAgain = true; continue; }
                byte[] pixels = (await bitmap.GetPixelsAsync()).ToArray();
                string name = $"capture-{Shell.Layout}-{Shell.Page}-{Shell.Mode}-{_theme}-{bitmap.PixelWidth}x{bitmap.PixelHeight}-{_options.TextScale}-r{target}.png";
                if(_captureRevision==target&&_captures.Contains(name))continue;
                string pending = ".capture-" + Guid.NewGuid().ToString("N") + ".tmp";
                var folder = await StorageFolder.GetFolderFromPathAsync(_options.DataDirectory);
                var file = await folder.CreateFileAsync(pending, CreationCollisionOption.FailIfExists);
                using (var stream = await file.OpenAsync(FileAccessMode.ReadWrite))
                {
                    var encoder = await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId, stream);
                    encoder.SetPixelData(BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied,
                        (uint)bitmap.PixelWidth, (uint)bitmap.PixelHeight, 96, 96, pixels);
                    await encoder.FlushAsync();
                }
                PreviewOptions.RejectReparsePoints(Path.Combine(_options.DataDirectory, name));
                CommitDiagnostic(Path.Combine(_options.DataDirectory, pending), Path.Combine(_options.DataDirectory, name));
                if (!_captures.Contains(name)) _captures.Add(name);
                if(Surface.XamlRoot is{} xamlRoot)
                {
                    int index=0;
                    foreach(var popup in Microsoft.UI.Xaml.Media.VisualTreeHelper.GetOpenPopupsForXamlRoot(xamlRoot).Take(3))
                    {
                        if(!popup.IsOpen||popup.Child is not FrameworkElement child||child.ActualWidth<1||child.ActualHeight<1)continue;
                        var preview=new RenderTargetBitmap();await preview.RenderAsync(child);
                        if(preview.PixelWidth*preview.PixelHeight>4_000_000)continue;
                        string popupName=$"popup-{Shell.Page}-r{target}-{index++}.png";
                        var popupFile=await folder.CreateFileAsync(popupName,CreationCollisionOption.ReplaceExisting);
                        using(var popupStream=await popupFile.OpenAsync(FileAccessMode.ReadWrite))
                        {
                            var popupEncoder=await BitmapEncoder.CreateAsync(BitmapEncoder.PngEncoderId,popupStream);
                            popupEncoder.SetPixelData(BitmapPixelFormat.Bgra8,BitmapAlphaMode.Premultiplied,(uint)preview.PixelWidth,(uint)preview.PixelHeight,96,96,(await preview.GetPixelsAsync()).ToArray());await popupEncoder.FlushAsync();
                        }
                        if(!_captures.Contains(popupName))_captures.Add(popupName);
                    }
                }
                _captureRevision = target;
                _captureError = null;
                WriteState();
            } while (_captureAgain && !_closed);
        }
        catch (Exception exception)
        {
            if (!_closed) { _captureError = "capture_" + exception.HResult.ToString("X8"); WriteState(); }
        }
        finally { _captureBusy = false; }
    }

    private void WriteState()
    {
        if (!_options.Diagnostics) return;
        GetWindowRect(_hwnd, out var rectangle);
        var access = new AccessibilitySettings();
        var settings = new UISettings();
        var state = new
        {
            schemaVersion = 8, stage = 8, page = Shell.Page, mode = Shell.Mode, workspace = Shell.Snapshot, material = _materials.Snapshot, titleBar=_chrome.Snapshot, nativeSettingsHook = _settingsHook, uiFramework = "WinUI 3", processId = Environment.ProcessId,
            hwnd = _hwnd.ToInt64(), ready = _ready, closed = _closed,
            revision = _revision, captureRevision = _captureRevision, captureError = _captureError,
            theme = _theme, actualTheme = Shell.ActualTheme.ToString(),
            windowWidth = rectangle.Right - rectangle.Left, windowHeight = rectangle.Bottom - rectangle.Top,
            contentWidth = Surface.ActualWidth, contentHeight = Surface.ActualHeight,
            dpi = GetDpiForWindow(_hwnd), rasterizationScale = Surface.XamlRoot?.RasterizationScale,
            applicationTextScale = _options.TextScale, systemHighContrast = access.HighContrast,
            systemAnimationsEnabled = settings.AnimationsEnabled,
            engineStarted = _engine.Started, catalogOpened = false, engine = _engine.Snapshot, captures = _captures.ToArray(),
            framework = RuntimeInformation.FrameworkDescription,
            architecture = RuntimeInformation.ProcessArchitecture.ToString()
        };
        string final = Path.Combine(_options.DataDirectory, "window-state.json");
        PreviewOptions.RejectReparsePoints(final);
        string temporary = Path.Combine(_options.DataDirectory, ".state-" + Guid.NewGuid().ToString("N") + ".tmp");
        using (var stream = new FileStream(temporary, FileMode.CreateNew, FileAccess.Write, FileShare.None))
            JsonSerializer.Serialize(stream, state, new JsonSerializerOptions { WriteIndented = true });
        CommitDiagnostic(temporary, final);
    }

    private static void CommitDiagnostic(string temporary, string final)
    {
        // ReplaceFile supports an existing observer with FILE_SHARE_DELETE;
        // overwrite MoveFileEx returned ACCESS_DENIED in the native regression.
        if (File.Exists(final)) File.Replace(temporary, final, null);
        else File.Move(temporary, final);
    }

    [StructLayout(LayoutKind.Sequential)] private struct Rectangle { public int Left, Top, Right, Bottom; }
    private nint OnWindowMessage(nint handle, uint message, nuint wparam, nint lparam, nuint id, nuint data)
    {
        // Cover Win10's 1px DWM strip without changing native frame styles or hit testing.
        if(message==0x0083&&wparam!=0&&_chrome is{Supported:true,HighContrast:false}&&!OperatingSystem.IsWindowsVersionAtLeast(10,0,22000)&&!IsZoomed(handle)){
            var before=Marshal.PtrToStructure<Rectangle>(lparam);
            var result=DefSubclassProc(handle,message,wparam,lparam);
            var after=Marshal.PtrToStructure<Rectangle>(lparam);
            if(after.Top-before.Top is 0 or 1){after.Top=before.Top-1;Marshal.StructureToPtr(after,lparam,false);}
            return result;
        }
        if ((message == 0x001A || message == 0x031A) && !_closed)
            DispatcherQueue.TryEnqueue(() => { if (!_closed) Shell.RefreshSystemPreferences(true); });
        return DefSubclassProc(handle, message, wparam, lparam);
    }
    [UnmanagedFunctionPointer(CallingConvention.Winapi)] private delegate nint SubclassCallback(nint handle, uint message, nuint wparam, nint lparam, nuint id, nuint data);
    [DllImport("comctl32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool SetWindowSubclass(nint handle, SubclassCallback callback, nuint id, nuint data);
    [DllImport("comctl32.dll")] [return: MarshalAs(UnmanagedType.Bool)] private static extern bool RemoveWindowSubclass(nint handle, SubclassCallback callback, nuint id);
    [DllImport("comctl32.dll")] private static extern nint DefSubclassProc(nint handle, uint message, nuint wparam, nint lparam);
    [DllImport("user32.dll")] private static extern bool GetWindowRect(nint handle, out Rectangle rectangle);
    [DllImport("user32.dll")] private static extern uint GetDpiForWindow(nint handle);
    [DllImport("user32.dll")] [return:MarshalAs(UnmanagedType.Bool)] private static extern bool IsZoomed(nint handle);
    [DllImport("dwmapi.dll")] private static extern int DwmSetWindowAttribute(nint handle, int attribute, ref int value, int size);
}
