using System.Text.Json;
using Luma.Core.Engine;
using Luma.Preview.Views.Controls;
using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Controls;

namespace Luma.Preview.Views;

public sealed partial class ManagementView
{
    private JsonElement? _vision, _labelState;
    private long? _tagId;
    private bool _newTag, _correctingTag;
    private TextBlock? _tagCounts;
    private TextBox? _tagName, _tagDescription;
    private NumberBox? _tagThreshold;
    private CheckBox? _tagEnabled;

    private async Task<bool> ReadTags(long generation)
    {
        var definitions = await Read("tags");
        var vision = await Read("vision");
        if (!TagScopeIsCurrent(generation)) return false;
        _definitions = Rows(definitions);
        _vision = vision;
        _labelState = null;
        if (_selection.Length != 0)
        {
            try
            {
                var labels = await ReadTagLabels(generation, _selection);
                if (TagScopeIsCurrent(generation)) _labelState = labels;
            }
            catch (EngineRpcException) { }
        }
        return TagScopeIsCurrent(generation);
    }

    private bool TagScopeIsCurrent(long generation) => _visible && _page == "Tags" && generation == _generation;

    private async Task<JsonElement?> ReadTagLabels(long generation, long[] images)
    {
        var data = await Read("labels", new { ids = images });
        return TagScopeIsCurrent(generation) && ReferenceEquals(images, _selection) ? data : null;
    }

    private void BuildTagActions()
    {
        Actions.Children.Add(Button("新建标签", "ManageTagNew", NewTag, when: () => !_correctingTag));
        if (_vision is not { } vision) return;
        if (B(vision, "ready"))
        {
            if (_definitions.Any(definition => B(definition, "enabled")))
                Actions.Children.Add(Button("自动识别", "ManageClassify", RecognizeTags, true, () => !_correctingTag));
        }
        else if (B(vision, "runtime_available"))
        {
            Actions.Children.Add(Button("下载识别模型", "ManageInstallVision", InstallTagRecognizer,
                true, () => !_correctingTag));
        }
    }

    private Task NewTag()
    {
        Invalidate();
        Records.SelectedItem = null;
        _newTag = true;
        RenderTagForm(null);
        _tagName?.Focus(FocusState.Programmatic);
        Notify();
        return Task.CompletedTask;
    }

    private void RenderTagForm(JsonElement? item)
    {
        ClearForm();
        _tagId = item is { } value ? N(value, "id") : null;
        _tagCounts = null;
        if (_selection.Length > 0 && _tagId.HasValue)
        {
            var correction = new StackPanel { Spacing = 6, Margin = new Thickness(0, 0, 0, 6) };
            var row = new TagChipPanel();
            if (item is { } definition && B(definition, "enabled"))
                row.Children.Add(Button("添加标签", "ManageConfirmTag", () => ApplyTag("include"), true, CanCorrectTag));
            row.Children.Add(Button("排除标签", "ManageExcludeTag", () => ApplyTag("exclude"), true, CanCorrectTag));
            row.Children.Add(Button("恢复自动", "ManageAutoTag", () => ApplyTag("auto"), true, CanCorrectTag));
            _tagCounts = new TextBlock
            {
                FontSize = 12 * _scale,
                TextWrapping = TextWrapping.Wrap,
                Style = (Style)Application.Current.Resources["LumaCaption"]
            };
            AutomationProperties.SetAutomationId(_tagCounts, "ManageTagCounts");
            AutomationProperties.SetLiveSetting(_tagCounts, AutomationLiveSetting.Polite);
            correction.Children.Add(_tagCounts);
            correction.Children.Add(row);
            Fields.Children.Add(correction);
            UpdateTagCounts();
        }

        _tagName = Input("标签名称", "ManageTagName", item is { } name ? S(name, "name") : "", 100);
        _tagDescription = Input("判定描述", "ManageTagDescription",
            item is { } description ? S(description, "description") : "", 500, true);
        _tagDescription.PlaceholderText = "英文描述";
        _tagDescription.MinHeight = 60;
        _tagThreshold = new NumberBox
        {
            Value = item is { } threshold && threshold.TryGetProperty("threshold", out var raw) ? raw.GetDouble() : .28,
            Minimum = .2,
            Maximum = .9,
            SmallChange = .01,
            FontSize = 14 * _scale,
            Header = "相似度阈值",
            MinWidth = 124
        };
        AutomationProperties.SetAutomationId(_tagThreshold, "ManageTagThreshold");
        _tagEnabled = new CheckBox
        {
            Content = "启用",
            IsChecked = item is not { } enabled || B(enabled, "enabled"),
            FontSize = 14 * _scale,
            VerticalAlignment = VerticalAlignment.Bottom
        };
        AutomationProperties.SetAutomationId(_tagEnabled, "ManageTagEnabled");
        var footer = new TagChipPanel();
        footer.Children.Add(_tagThreshold);
        footer.Children.Add(_tagEnabled);
        var save = Button("保存标签", "ManageTagSave", SaveTag, true, () => !_correctingTag);
        save.Style = (Style)Application.Current.Resources["LumaPrimaryButton"];
        save.VerticalAlignment = VerticalAlignment.Bottom;
        footer.Children.Add(save);
        Fields.Children.Add(footer);
    }

    private bool CanCorrectTag() => _selection.Length > 0 && _tagId.HasValue && !_correctingTag;

    private void UpdateTagCounts()
    {
        if (_tagCounts is null) return;
        JsonElement tag = default;
        if (_labelState is { } state && state.TryGetProperty("tags", out var tags))
            tag = tags.EnumerateArray().FirstOrDefault(value => N(value, "id") == _tagId);
        _tagCounts.Text = $"所选图片 {_selection.Length} 张";
        if (tag.ValueKind == JsonValueKind.Object)
            _tagCounts.Text += $" · 已标记 {N(tag, "effective_count")} · 手动添加 {N(tag, "manual_count")} · 排除 {N(tag, "excluded_count")}";
    }

    private async Task ApplyTag(string action)
    {
        if (!CanCorrectTag() || _tagId is not long tagId) return;
        var images = _selection;
        long generation = _generation;
        _correctingTag = true;
        Notify();
        try
        {
            var scope = _labelState ?? await ReadTagLabels(generation, images);
            if (!TagScopeIsCurrent(generation) || _tagId != tagId || scope is not { } loaded) return;
            string snapshot = S(loaded, "snapshot_token");
            if (snapshot.Length == 0) return;
            await Write("labels.apply", new { ids = images, changes = new[] { new { tag_id = tagId, action } }, snapshot }, false);
            if (!TagScopeIsCurrent(generation)) return;
            _labelState = null;
            var labels = await ReadTagLabels(generation, images);
            if (TagScopeIsCurrent(generation))
            {
                _labelState = labels;
                UpdateTagCounts();
            }
        }
        catch (EngineRpcException)
        {
            if (TagScopeIsCurrent(generation))
            {
                UpdateTagCounts();
                Status.Text = "标签状态读取失败";
            }
        }
        finally
        {
            _correctingTag = false;
            Notify();
        }
    }

    private async Task SaveTag()
    {
        if (string.IsNullOrWhiteSpace(_tagName?.Text)) { await Missing("请输入标签名称"); return; }
        string description = _tagDescription?.Text.Trim() ?? "";
        if (description.Length < 5 || description.Any(c => c > 127) || !description.Any(char.IsAsciiLetter))
        {
            await Missing("判定描述需要至少 5 个英文字符");
            return;
        }
        if (_tagThreshold is null || !double.IsFinite(_tagThreshold.Value) || _tagThreshold.Value is < .2 or > .9)
        {
            await Missing("相似度阈值范围为 0.20–0.90");
            return;
        }
        long generation = _generation;
        string name = _tagName.Text.Trim();
        var definitions = _definitions.Where(d => N(d, "id") != _tagId)
            .Select(d => JsonSerializer.Deserialize<Dictionary<string, object?>>(d.GetRawText())!).ToList();
        definitions.Add(new()
        {
            ["id"] = _tagId, ["name"] = name, ["description"] = description,
            ["threshold"] = _tagThreshold.Value, ["enabled"] = _tagEnabled?.IsChecked == true
        });
        if (!await Write("tags.save", new { definitions }) || !TagScopeIsCurrent(generation)) return;
        _newTag = false;
        Records.SelectedItem = Records.Items.OfType<ManagementRow>().FirstOrDefault(r => S(r.Value, "name") == name);
    }

    private async Task InstallTagRecognizer()
    {
        long generation = _generation;
        long bytes = _vision is { } vision ? N(vision, "download_bytes") : 0;
        string size = bytes > 0 ? $"{bytes / 1048576d:F0} MiB" : "";
        if (await Confirm("下载识别模型", size) && TagScopeIsCurrent(generation))
            await Write("vision.install", new { });
    }

    private async Task RecognizeTags()
    {
        long generation = _generation;
        var images = _selection;
        var dialog = Confirmation("自动识别", "");
        dialog.PrimaryButtonText = images.Length > 0 ? $"识别所选 {images.Length} 张" : "识别整个图库";
        if (images.Length > 0) dialog.SecondaryButtonText = "识别整个图库";
        var result = await dialog.ShowAsync();
        if (!TagScopeIsCurrent(generation)) return;
        if (result == ContentDialogResult.Primary && images.Length > 0)
            await Write("vision.classify", new { ids = images }, false);
        else if (result is ContentDialogResult.Primary or ContentDialogResult.Secondary)
            await Write("vision.classify", new { scope = "all" }, false);
    }
}
