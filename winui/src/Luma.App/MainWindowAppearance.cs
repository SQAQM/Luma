using Microsoft.UI.Xaml.Media;

namespace Luma.Preview;

public sealed partial class MainWindow
{
    private Brush? CustomWindowBackground()
    {
        var appearance = Shell.CustomAppearance;
        if (appearance.Background != "Image" || _visualAccessibility.HighContrast)
        {
            if (_wallpaperPath is not null)
            {
                _wallpaperRequest++;
                _wallpaper = null;
                _wallpaperPath = null;
            }
            if (appearance.Background != "Color" || _visualAccessibility.HighContrast) return null;
            var color = Services.AppearanceColors.Parse(appearance.BackgroundColor)
                ?? ((SolidColorBrush)Shell.ResolveThemeBrush("LumaCanvas")).Color;
            if (_customCanvas.Color != color) _customCanvas.Color = color;
            return _customCanvas;
        }

        if (appearance.ImagePath != _wallpaperPath)
        {
            _wallpaperPath = appearance.ImagePath;
            _wallpaper = null;
            int generation = ++_wallpaperRequest;
            if (_wallpaperPath is { } path) _ = RestoreWallpaper(path, generation);
        }
        return _wallpaper;
    }

    private async Task RestoreWallpaper(string path, int generation)
    {
        try
        {
            var brush = await Services.WindowWallpaper.LoadAsync(path);
            if (!_closed && generation == _wallpaperRequest)
            {
                _wallpaper = brush;
                UpdateState();
            }
        }
        catch (Exception error) when (IsWallpaperError(error))
        {
            if (!_closed && generation == _wallpaperRequest) Shell.ShowWallpaperError();
        }
    }

    private async void PickWallpaper()
    {
        if (_closed || _closePending || _pickerBusy) return;
        _pickerBusy = true;
        try
        {
            var picker = new Microsoft.Windows.Storage.Pickers.FileOpenPicker(Microsoft.UI.Win32Interop.GetWindowIdFromWindow(_hwnd));
            foreach (string extension in new[] { ".png", ".jpg", ".jpeg", ".bmp", ".webp", ".gif", ".tif", ".tiff", ".avif" })
                picker.FileTypeFilter.Add(extension);
            var result = await picker.PickSingleFileAsync();
            if (result is not null && !_closed && !_closePending && Shell.CustomAppearance.Background == "Image")
                await SetWindowWallpaper(result.Path);
        }
        catch (Exception error) when (IsWallpaperError(error))
        {
            if (!_closed && !_closePending) Shell.ShowWallpaperError();
        }
        finally { _pickerBusy = false; }
    }

    private async Task SetWindowWallpaper(string path)
    {
        var brush = await Services.WindowWallpaper.LoadAsync(path);
        if (_closed || _closePending || Shell.CustomAppearance.Background != "Image") return;
        _wallpaperPath = path;
        _wallpaper = brush;
        _wallpaperRequest++;
        Shell.SetWallpaper(path);
    }

    private static bool IsWallpaperError(Exception error) =>
        error is IOException or UnauthorizedAccessException or ArgumentException or NotSupportedException or System.Runtime.InteropServices.COMException;
}
