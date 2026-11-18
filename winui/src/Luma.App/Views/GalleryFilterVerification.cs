using Microsoft.UI.Xaml;
using Microsoft.UI.Xaml.Automation;
using Microsoft.UI.Xaml.Automation.Peers;
using Microsoft.UI.Xaml.Automation.Provider;
using Microsoft.UI.Xaml.Controls;
using Microsoft.UI.Xaml.Media;
using Windows.Foundation;

namespace Luma.Preview.Views;

public sealed partial class GalleryView
{
    internal async Task VerifyFilterPanelUi(Action<bool, string> check, Func<Task> capture)
    {
        Rect Bounds(FrameworkElement element) => element.TransformToVisual(XamlRoot.Content)
            .TransformBounds(new Rect(0, 0, element.ActualWidth, element.ActualHeight));

        static void Invoke(Button button)
        {
            var peer = new ButtonAutomationPeer(button);
            ((IInvokeProvider)peer.GetPattern(PatternInterface.Invoke)).Invoke();
        }

        UpdateLayout();
        var galleryBefore = Bounds(ImagesPanel);
        var toolbarBefore = Bounds(ToolbarActions);
        int previousKind = _filterKind;
        string previousFormat = _wanted["format"];
        try
        {
            Invoke(FilterButton);
            for (int i = 0; i < 50 && (!FilterFlyout.IsOpen || FilterPanel.ActualHeight == 0); i++)
                await Task.Delay(50);
            await Task.Delay(180);

            check(FilterFlyout.IsOpen && FilterPanel.ActualHeight > 0, "gallery filter opens from its native button");
            if (!FilterFlyout.IsOpen || FilterPanel.ActualHeight == 0) return;
            var anchor = Bounds(FilterButton);
            var panel = Bounds(FilterPanel);
            if (XamlRoot.Size.Height - anchor.Bottom >= panel.Height + 18)
                check(panel.Top >= anchor.Bottom - 1, "gallery filter opens below its button when there is room");
            check(Math.Abs(Bounds(ImagesPanel).Height - galleryBefore.Height) < 1 &&
                Math.Abs(Bounds(ImagesPanel).Top - galleryBefore.Top) < 1 &&
                Math.Abs(Bounds(ToolbarActions).Height - toolbarBefore.Height) < 1,
                "open gallery filter keeps the gallery viewport and toolbar fixed");
            check(FilterKinds.Children.Count == 5 && FilterKinds.Children.All(element => element is Button),
                "all five gallery filter categories are in the same panel");
            check(AutomationProperties.GetAutomationId(FilterValues.Children[0]) == "GalleryClearFilter" &&
                VisualTreeHelper.GetOpenPopupsForXamlRoot(XamlRoot).Count(popup => popup.IsOpen) == 1,
                "gallery filter exposes one popup with a direct category reset");

            Invoke((Button)FilterKinds.Children[1]);
            for (int i = 0; i < 50 && _filterKind != 1; i++) await Task.Delay(50);
            check(_filterKind == 1 && FilterFlyout.IsOpen && FilterInput.IsLoaded,
                "changing gallery filter category keeps its input and candidates in place");
            Button? png = null;
            for (int i = 0; i < 50 && png is null; i++)
            {
                png = FilterValues.Children.OfType<Button>().FirstOrDefault(button =>
                    AutomationProperties.GetName(button).Equals("png", StringComparison.OrdinalIgnoreCase));
                if (png is null) await Task.Delay(50);
            }
            await capture();
            check(png is not null, "gallery filter offers the indexed PNG format");
            if (png is null) return;
            Invoke(png);
            for (int i = 0; i < 50 && FilterFlyout.IsOpen; i++) await Task.Delay(50);
            check(!FilterFlyout.IsOpen && _wanted["format"].Equals("png", StringComparison.OrdinalIgnoreCase),
                "choosing a candidate applies it and closes the gallery filter");
            check(FilterAppliedSummary.Visibility == Visibility.Visible &&
                FilterAppliedSummary.Text.Contains("png", StringComparison.OrdinalIgnoreCase),
                "applied gallery values remain visible when reopening the panel");
        }
        finally
        {
            FilterFlyout.Hide();
            _filterKind = previousKind;
            if (_wanted["format"] != previousFormat)
            {
                _wanted["format"] = previousFormat;
                ChangeScope();
            }
            else UpdateFilterSummary();
        }
    }
}
