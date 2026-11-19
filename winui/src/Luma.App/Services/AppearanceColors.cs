using System.Globalization;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Media;
using Windows.UI;

namespace Luma.Preview.Services;

internal sealed class AppearanceColors
{
    private static readonly string[] InkKeys =
        ["LumaText", "LumaSecondary", "LumaSelectionItemInteractiveText", "LumaSelectionItemSelectedText"];
    private readonly Dictionary<SolidColorBrush, Color> _original = [];
    private string? _current;

    public AppearanceColors(params ResourceDictionary[] resources)
    {
        foreach (var dictionary in resources)
            Add(dictionary);
    }

    private void Add(ResourceDictionary dictionary)
    {
        foreach (var variant in new[] { "Light", "Default" })
            if (dictionary.ThemeDictionaries.TryGetValue(variant, out var entry) && entry is ResourceDictionary palette)
                foreach (var key in InkKeys)
                    if (palette.TryGetValue(key, out var brush) && brush is SolidColorBrush ink)
                        _original.TryAdd(ink, ink.Color);
        foreach (var merged in dictionary.MergedDictionaries)
            Add(merged);
    }

    public bool Apply(string? value)
    {
        if (_current == value) return false;
        _current = value;
        Color? selected = Parse(value);
        foreach (var (brush, original) in _original)
            brush.Color = selected ?? original;
        return true;
    }

    public static Color? Parse(string? value) =>
        value is { Length: 7 } && uint.TryParse(value.AsSpan(1), NumberStyles.HexNumber, CultureInfo.InvariantCulture, out uint rgb)
            ? Color.FromArgb(255, (byte)(rgb >> 16), (byte)(rgb >> 8), (byte)rgb)
            : null;

    public static string Hex(Color color) => $"#{color.R:X2}{color.G:X2}{color.B:X2}";
}
