using System.Text.Json;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;

namespace Luma.Preview.Views;

public sealed partial class ManagementView
{
    internal async Task VerifyTagsUi(Action<bool, string> check, Func<Task> capture, long imageId)
    {
        async Task<bool> Until(Func<bool> ready)
        {
            for (int i = 0; i < 120; i++)
            {
                if (ready()) return true;
                await Task.Delay(50);
            }
            return false;
        }

        Button? Correction(string id) => Fields.Children.OfType<StackPanel>()
            .SelectMany(panel => panel.Children.OfType<Controls.TagChipPanel>())
            .SelectMany(panel => panel.Children.OfType<Button>())
            .FirstOrDefault(button => AutomationProperties.GetAutomationId(button) == id);

        long Count(string key)
        {
            if (_tagId is null || _labelState is not { } labels) return -1;
            var value = labels.GetProperty("tags").EnumerateArray().FirstOrDefault(item => N(item, "id") == _tagId);
            return value.ValueKind == JsonValueKind.Object ? N(value, key) : -1;
        }

        static void Invoke(Button button) =>
            ((IInvokeProvider)new ButtonAutomationPeer(button).GetPattern(PatternInterface.Invoke)).Invoke();

        bool ready = await Until(() => IsShowing("Tags") && !_formLoading &&
            _labelState.HasValue && Correction("ManageConfirmTag") is { IsEnabled: true });
        check(ready && _selection.SequenceEqual(new[] { imageId }) && Count("manual_count") == 0,
            "sidebar navigation carries the selected image and loads correction state automatically");
        if (!ready) return;
        UpdateLayout();
        var save = Fields.Children.OfType<Controls.TagChipPanel>().SelectMany(panel => panel.Children.OfType<Button>())
            .First(button => AutomationProperties.GetAutomationId(button) == "ManageTagSave");
        var saveBounds = save.TransformToVisual(FormScroll).TransformBounds(new(0, 0, save.ActualWidth, save.ActualHeight));
        check(saveBounds.Top >= 0 && saveBounds.Bottom <= FormScroll.ActualHeight + 1,
            "the tag editor keeps Save visible alongside its options at large text scale");
        await capture();
        Invoke(Correction("ManageConfirmTag")!);
        bool included = await Until(() => !_correctingTag && Count("manual_count") == 1);
        check(included, "one tag action applies the chosen label with its selection snapshot");
        if (!included) return;
        Invoke(Correction("ManageAutoTag")!);
        check(await Until(() => !_correctingTag && Count("manual_count") == 0),
            "restore automatic removes the manual override for the same selection");
        ShowPage("Tags", []);
        ready = await Until(() => !_formLoading && _tagName?.IsLoaded == true);
        check(ready && _selection.Length == 0 && Correction("ManageConfirmTag") is null,
            "tag definitions have no image correction controls without a selection");
    }
}
