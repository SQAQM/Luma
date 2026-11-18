using System.Text.Json;
using Microsoft.UI.Text;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Input;
using Windows.System;

namespace Luma.Preview.Views;

public sealed partial class GalleryView
{
    private const int ContentFilterIndex = 3;
    private static readonly (string Key, string Name, int MaxLength)[] FilterKindsInfo =
    [
        ("model", "模型", 1024),
        ("format", "格式", 30),
        ("artist", "画师", 1000),
        ("content", "内容标签", 1000),
        ("prompt", "提示词词项", 2048),
    ];

    private sealed record FilterChoice(string Name, long? TagId);

    private readonly Dictionary<string, string> _wanted = new()
    {
        ["model"] = "", ["format"] = "", ["artist"] = "", ["prompt"] = "",
    };
    private JsonElement? _facets;
    private int _facetRequestVersion;
    private int _filterKind;
    private int _displayedFilterKind = -1;
    private FilterChoice[] _displayedFilterChoices = [];
    private string? _tagName;
    private string? _filterError;

    private void InitializeFilters()
    {
        for (int i = 0; i < FilterKindsInfo.Length; i++)
        {
            int index = i;
            var button = FilterRowButton(FilterKindsInfo[index].Name, $"GalleryFilterKind{index}");
            button.Click += (_, _) => SelectFilterKind(index);
            FilterKinds.Children.Add(button);
        }
        UpdateFilterSummary();
    }

    private Button FilterRowButton(string label, string id)
    {
        var button = new Button
        {
            Style = (Style)Application.Current.Resources["LumaNav"],
            MinHeight = 32,
            Padding = new Thickness(8, 4, 8, 4),
            FontSize = 14 * _scale,
            Content = new TextBlock { Text = label, FontSize = 14 * _scale, TextTrimming = TextTrimming.CharacterEllipsis, MaxLines = 1 },
        };
        AutomationProperties.SetAutomationId(button, id);
        AutomationProperties.SetName(button, label);
        return button;
    }

    private void FilterOpening(object sender, object e)
    {
        ScaleFilters();
        FilterInput.Text = ActiveFilterValue(_filterKind);
        _filterError = null;
        UpdateFilterSummary();
        RenderFilterChoices();
        FilterPanel.ChangeView(null, 0, null, true);
        FilterValuesScroll.ChangeView(null, 0, null, true);
    }

    private void FilterOpened(object sender, object e) => Changed?.Invoke();

    private void FilterClosed(object sender, object e)
    {
        if (!_disposed) Changed?.Invoke();
    }

    private void ScaleFilters()
    {
        FilterTitle.FontSize = 14 * _scale;
        FilterAppliedSummary.FontSize = 12 * _scale;
        FilterStatus.FontSize = 12 * _scale;
        FilterInput.FontSize = FilterApplyButton.FontSize = ClearFiltersButton.FontSize = 14 * _scale;
        foreach (var button in FilterKinds.Children.Concat(FilterValues.Children).OfType<Button>())
        {
            button.FontSize = 14 * _scale;
            if (button.Content is TextBlock text) text.FontSize = button.FontSize;
        }

        if (XamlRoot is not { } root || !FilterButton.IsLoaded) return;
        var anchor = FilterButton.TransformToVisual(root.Content).TransformBounds(
            new Windows.Foundation.Rect(0, 0, FilterButton.ActualWidth, FilterButton.ActualHeight));
        double below = root.Size.Height - anchor.Bottom - 16;
        double above = anchor.Top - 16;
        FilterPanel.Width = Math.Max(1, Math.Min(root.Size.Width - 32, 414 + 54 * (_scale - 1)));
        FilterKinds.Width = Math.Min(FilterPanel.Width * .36, 36 + 76 * _scale);
        FilterPanel.MaxHeight = Math.Max(72, Math.Min(370 * Math.Sqrt(_scale), below >= 220 ? below : Math.Max(below, above)));
        FilterValuesScroll.MaxHeight = Math.Max(68, Math.Min(176 * Math.Sqrt(_scale), FilterPanel.MaxHeight - 148 * Math.Sqrt(_scale)));
    }

    private void FilterTitleSwiped(object sender, ManipulationCompletedRoutedEventArgs e)
    {
        if (e.PointerDeviceType == Microsoft.UI.Input.PointerDeviceType.Touch && e.Cumulative.Translation.Y < -42)
            FilterFlyout.Hide();
    }

    private void SelectFilterKind(int index)
    {
        if (_filterKind != index)
        {
            _filterKind = index;
            FilterInput.Text = ActiveFilterValue(index);
            _filterError = null;
            UpdateFilterSummary();
            RenderFilterChoices();
            FilterValuesScroll.ChangeView(null, 0, null, true);
        }
        Changed?.Invoke();
    }

    private void SetFilterFacets(JsonElement facets)
    {
        _facets = facets;
        if (_tag is long id)
            _tagName = ReadFilterChoices(ContentFilterIndex).FirstOrDefault(choice => choice.TagId == id)?.Name ?? _tagName;
        UpdateFilterSummary();
        if (FilterFlyout.IsOpen) RenderFilterChoices();
    }

    private FilterChoice[] ReadFilterChoices(int index)
    {
        if (_facets is not { ValueKind: JsonValueKind.Object } facets ||
            !facets.TryGetProperty(FilterKindsInfo[index].Key, out var values) || values.ValueKind != JsonValueKind.Array)
            return [];

        var choices = new List<FilterChoice>();
        foreach (var entry in values.EnumerateArray().Take(128))
        {
            if (entry.ValueKind != JsonValueKind.Object || !entry.TryGetProperty("name", out var nameProperty) ||
                nameProperty.ValueKind != JsonValueKind.String || nameProperty.GetString() is not { Length: > 0 } name)
                continue;
            long? id = null;
            if (index == ContentFilterIndex)
            {
                if (!entry.TryGetProperty("id", out var idProperty) || idProperty.ValueKind != JsonValueKind.Number ||
                    !idProperty.TryGetInt64(out long tagId) || tagId <= 0)
                    continue;
                id = tagId;
            }
            choices.Add(new(name, id));
        }
        return choices.ToArray();
    }

    private void RenderFilterChoices()
    {
        var choices = ReadFilterChoices(_filterKind);
        if (_displayedFilterKind != _filterKind || !_displayedFilterChoices.SequenceEqual(choices))
        {
            int index = _filterKind;
            int n = 0;
            // Replacing the facet list must not steal focus from a draft in the text field.
            if (XamlRoot is not null && FocusManager.GetFocusedElement(XamlRoot) is Button focused &&
                FilterValues.Children.Contains(focused))
                FilterInput.Focus(FocusState.Programmatic);
            FilterValues.Children.Clear();

            var any = FilterRowButton("不限", "GalleryClearFilter");
            any.Click += (_, _) => CommitFilter(index, "", null);
            FilterValues.Children.Add(any);
            foreach (var choice in choices)
            {
                var button = FilterRowButton(choice.Name, $"GalleryFilterValue{n++}");
                button.Click += (_, _) => CommitFilter(index, choice.Name, choice.TagId);
                ToolTipService.SetToolTip(button, choice.Name);
                FilterValues.Children.Add(button);
            }
            _displayedFilterKind = index;
            _displayedFilterChoices = choices;
        }

        string activeValue = ActiveFilterValue(_filterKind);
        foreach (var button in FilterValues.Children.OfType<Button>())
        {
            bool current = AutomationProperties.GetAutomationId(button) == "GalleryClearFilter"
                ? !HasFilter(_filterKind)
                : AutomationProperties.GetName(button) == activeValue;
            SetFilterRowSelected(button, current);
        }
        UpdateFilterStatus();
    }

    private void SetFilterRowSelected(Button button, bool selected)
    {
        button.Style = selected ? (Style)Resources["GalleryFilterSelected"] : (Style)Application.Current.Resources["LumaNav"];
        button.FontWeight = selected ? FontWeights.SemiBold : FontWeights.Normal;
        if (button.Content is TextBlock text) text.FontWeight = button.FontWeight;
        AutomationProperties.SetItemStatus(button, selected ? "已选中" : "");
    }

    private void FilterDraftChanged(object sender, TextChangedEventArgs e)
    {
        _filterError = null;
        if (FilterStatus is not null) UpdateFilterStatus();
    }

    private void UpdateFilterStatus()
    {
        FilterStatus.Text = _filterError ?? (_displayedFilterChoices.Length > 0 ? "" :
            _facets is null ? "候选暂不可用" : "没有候选名称");
        FilterStatus.Visibility = FilterStatus.Text.Length == 0 ? Visibility.Collapsed : Visibility.Visible;
    }

    private void FilterInputKeyDown(object sender, KeyRoutedEventArgs e)
    {
        if (e.Key != VirtualKey.Enter) return;
        e.Handled = true;
        ApplyFilterDraft();
    }

    private void ApplyFilterClicked(object sender, RoutedEventArgs e) => ApplyFilterDraft();

    private void ApplyFilterDraft()
    {
        string value = FilterInput.Text.Trim();
        // Preserve the previous manual shortcut for clearing a category.
        if (value == "不限") value = "";
        long? tag = null;
        if (_filterKind == ContentFilterIndex && value.Length > 0)
        {
            tag = ReadFilterChoices(ContentFilterIndex).FirstOrDefault(choice => choice.Name == value)?.TagId;
            if (tag is null)
            {
                ShowFilterError("请选择已有的内容标签。");
                return;
            }
        }
        CommitFilter(_filterKind, value, tag);
    }

    private void ShowFilterError(string message)
    {
        _filterError = message;
        UpdateFilterStatus();
        Changed?.Invoke();
    }

    private void CommitFilter(int index, string value, long? tag)
    {
        var kind = FilterKindsInfo[index];
        if (value.Length > kind.MaxLength)
        {
            ShowFilterError("筛选文字过长。");
            return;
        }
        FilterFlyout.Hide();
        if (index == ContentFilterIndex)
        {
            _tag = tag;
            _tagName = tag is null ? null : value;
        }
        else _wanted[kind.Key] = value;
        ChangeScope();
    }

    internal void FilterContent(long tag, string? name = null)
    {
        _tag = tag;
        _tagName = name ?? ReadFilterChoices(ContentFilterIndex).FirstOrDefault(choice => choice.TagId == tag)?.Name;
        ChangeScope();
    }

    private string ActiveFilterValue(int index) => index == ContentFilterIndex
        ? _tagName ?? ""
        : _wanted[FilterKindsInfo[index].Key];

    private bool HasFilter(int index) => index == ContentFilterIndex ? _tag is not null : ActiveFilterValue(index).Length > 0;

    private void UpdateFilterSummary()
    {
        var active = new List<string>();
        for (int i = 0; i < FilterKindsInfo.Length; i++)
        {
            string value = ActiveFilterValue(i);
            string label = HasFilter(i) ? value.Length == 0 ? "已选内容标签" : value : "";
            var button = (Button)FilterKinds.Children[i];
            SetFilterRowSelected(button, i == _filterKind);
            AutomationProperties.SetHelpText(button, label);
            ToolTipService.SetToolTip(button, label.Length > 0 ? label : FilterKindsInfo[i].Name);
            if (label.Length > 0)
                active.Add($"{FilterKindsInfo[i].Name} · {ShortFilterLabel(label)}");
        }
        string content = active.Count > 0 ? $"筛选 ({active.Count})" : "筛选";
        string summary = active.Count == 0 ? content : $"{content}，{string.Join("；", active)}";
        FilterButton.Content = content;
        FilterAppliedSummary.Text = string.Join("；", active);
        FilterAppliedSummary.Visibility = active.Count > 0 ? Visibility.Visible : Visibility.Collapsed;
        AutomationProperties.SetName(FilterButton, summary);
        ToolTipService.SetToolTip(FilterButton, summary);
        ClearFiltersButton.IsEnabled = active.Count > 0 || SearchBox.Text.Length > 0 ||
            _rootIds.ElementAtOrDefault(RootsBox.SelectedIndex) is not null;
    }

    private static string ShortFilterLabel(string label)
    {
        if (label.Length <= 32) return label;
        var elements = System.Globalization.StringInfo.ParseCombiningCharacters(label);
        return elements.Length <= 32 ? label : label[..elements[32]] + "…";
    }

    private void ClearClicked(object sender, RoutedEventArgs e)
    {
        FilterFlyout.Hide();
        _updating = true;
        try
        {
            SearchBox.Text = "";
            RootsBox.SelectedIndex = _rootIds.IndexOf(null);
            ClearFilterValues();
            _inspector = false;
        }
        finally { _updating = false; }
        ChangeScope();
    }

    private void ClearFilterValues()
    {
        _tag = null;
        _tagName = null;
        foreach (var key in _wanted.Keys.ToArray()) _wanted[key] = "";
    }

    private void ResetFilters()
    {
        FilterFlyout.Hide();
        ++_facetRequestVersion;
        _facets = null;
        _filterKind = 0;
        _displayedFilterKind = -1;
        _displayedFilterChoices = [];
        FilterValues.Children.Clear();
        FilterInput.Text = "";
        ClearFilterValues();
        UpdateFilterSummary();
    }
}
