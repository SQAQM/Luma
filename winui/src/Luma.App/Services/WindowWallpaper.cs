using Microsoft.UI.Xaml.Media;
using Microsoft.UI.Xaml.Media.Imaging;
using Windows.Graphics.Imaging;
using Windows.Storage;

namespace Luma.Preview.Services;

internal static class WindowWallpaper
{
    private const uint MaxDisplaySide = 2400;

    public static async Task<ImageBrush> LoadAsync(string path)
    {
        var file = await StorageFile.GetFileFromPathAsync(path);
        using var stream = await file.OpenAsync(FileAccessMode.Read);
        var decoder = await BitmapDecoder.CreateAsync(stream);
        uint longSide = Math.Max(decoder.PixelWidth, decoder.PixelHeight);
        double scale = Math.Min(1, (double)MaxDisplaySide / Math.Max(1, longSide));
        var transform = new BitmapTransform
        {
            ScaledWidth = Math.Max(1, (uint)Math.Round(decoder.PixelWidth * scale)),
            ScaledHeight = Math.Max(1, (uint)Math.Round(decoder.PixelHeight * scale))
        };
        using var pixels = await decoder.GetSoftwareBitmapAsync(BitmapPixelFormat.Bgra8,
            BitmapAlphaMode.Ignore, transform, ExifOrientationMode.RespectExifOrientation, ColorManagementMode.ColorManageToSRgb);
        using var prepared = SoftwareBitmap.Convert(pixels, BitmapPixelFormat.Bgra8, BitmapAlphaMode.Premultiplied);
        var source = new SoftwareBitmapSource();
        await source.SetBitmapAsync(prepared);
        return new ImageBrush { ImageSource = source, Stretch = Stretch.UniformToFill };
    }
}
