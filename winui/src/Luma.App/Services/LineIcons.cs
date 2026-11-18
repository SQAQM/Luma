using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Controls;

namespace Luma.Preview.Services;

internal enum LineGlyph
{
    Gallery, GalleryStack, Model, Tag, Folder, Trash, Jobs, Storage, Settings,
    Grid, List, Refresh, Cancel, Left, Right, Play, Pause, Heart, HeartSelected, Star, StarSelected
}

internal static class LineIcons
{
    internal static ContentPresenter Create(LineGlyph kind) => new()
    {
        Width = 18,
        Height = 18,
        HorizontalAlignment = HorizontalAlignment.Center,
        VerticalAlignment = VerticalAlignment.Center,
        HorizontalContentAlignment = HorizontalAlignment.Center,
        VerticalContentAlignment = VerticalAlignment.Center,
        IsHitTestVisible = false,
        Content = true,
        ContentTemplate = Template(kind),
    };

    internal static void SetKind(ContentPresenter icon, LineGlyph kind) => icon.ContentTemplate = Template(kind);

    private static DataTemplate Template(LineGlyph kind) =>
        (DataTemplate)Application.Current.Resources["LumaIcon" + kind];
}
