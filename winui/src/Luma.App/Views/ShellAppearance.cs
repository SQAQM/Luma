using Luma.Core.Management;
using Luma.Preview.Services;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace Luma.Preview.Views;

public sealed partial class ShellView
{
    internal WindowAppearance CustomAppearance { get; private set; } = new();
    internal event Action? WallpaperRequested;

    private void SyncAppearanceColors()
    {
        if (TextColorPicker is null || BackgroundColorPicker is null) return;
        bool restoring = _restoringAppearance;
        _restoringAppearance = true;
        try
        {
            var text = AppearanceColors.Parse(CustomAppearance.TextColor) ?? ((SolidColorBrush)Brush("LumaText")).Color;
            var background = AppearanceColors.Parse(CustomAppearance.BackgroundColor) ?? ((SolidColorBrush)Brush("LumaCanvas")).Color;
            TextColorPicker.Color = text;
            BackgroundColorPicker.Color = background;
            SetSwatch(TextSwatch, text);
            SetSwatch(BackgroundSwatch, background);
            TextColorLabel.Text = CustomAppearance.TextColor ?? "主题默认";
            BackgroundColorLabel.Text = CustomAppearance.BackgroundColor ?? "选择颜色";
            ResetTextColor.IsEnabled = CustomAppearance.TextColor is not null;
            WallpaperButton.Content = CustomAppearance.ImagePath is null ? "选择图片" : "更换图片";
        }
        finally { _restoringAppearance = restoring; }
    }

    private void TextColorChanged(ColorPicker sender, ColorChangedEventArgs args)
    {
        if (_restoringAppearance) return;
        CustomAppearance = CustomAppearance with { TextColor = AppearanceColors.Hex(args.NewColor) };
        Changed?.Invoke();
    }

    private void BackgroundColorChanged(ColorPicker sender, ColorChangedEventArgs args)
    {
        if (_restoringAppearance) return;
        CustomAppearance = CustomAppearance with { BackgroundColor = AppearanceColors.Hex(args.NewColor) };
        SyncAppearanceColors();
        Changed?.Invoke();
    }

    private void ResetTextColorClicked(object sender, RoutedEventArgs args)
    {
        CustomAppearance = CustomAppearance with { TextColor = null };
        Changed?.Invoke();
    }

    private static void SetSwatch(Border swatch, Color color)
    {
        if (swatch.Background is SolidColorBrush fill)
            fill.Color = color;
        else
            swatch.Background = new SolidColorBrush(color);
    }

    private void PickWallpaperClicked(object sender, RoutedEventArgs args) => WallpaperRequested?.Invoke();

    internal void SetWallpaper(string path)
    {
        CustomAppearance = CustomAppearance with { ImagePath = path };
        SyncAppearanceColors();
        Changed?.Invoke();
    }

    internal void ShowWallpaperError()
    {
        Feedback.Text = "无法读取背景图片";
        Changed?.Invoke();
    }
}
